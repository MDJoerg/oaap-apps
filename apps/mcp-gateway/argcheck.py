"""Argumente gegen das freigegebene Schema prüfen (Spec §7, RFC-0065 §14 A1).

Bevor ein `tools/call` zum Server geht, wird geprüft, ob seine
Argumente zu dem Schema passen, das der Betreiber **freigegeben** hat —
nicht zu dem, das der Server heute meldet. Ein SAP-System soll keinen
Unsinnsaufruf bekommen, nur weil ein Modell ein Argument erfunden hat.

Bewusst eine Teilmenge von JSON Schema, von Hand und ohne fremden Code:

- geprüft: `type`, `properties`, `required`, `additionalProperties`,
  `enum`, `const`, `items`/`prefixItems`, `minItems`/`maxItems`,
  `minLength`/`maxLength`, `minimum`/`maximum` (auch die exklusiven),
  `anyOf`, `oneOf`, `allOf`, `not`, lokales `$ref`;
- **nicht** geprüft: `pattern`, `format` und alles Übrige —
  `unchecked()` nennt es, damit die Betreiber-Seite es sagen kann.
  `pattern` und `patternProperties` absichtlich nicht: Ein fremder
  regulärer Ausdruck auf fremde Eingabe ist eine Einladung, das Gateway
  lahmzulegen. Ein Name, den nur ein Muster erlaubt, gilt deshalb als
  nicht vorgesehen — strenger, nie lockerer.

Unbekannte Argumente: Auf der obersten Ebene und in jedem Objekt, das
seine Eigenschaften aufzählt, wird abgewiesen, was nicht aufgezählt ist
— außer das Schema erlaubt es ausdrücklich (`additionalProperties`).
Ein Objekt ohne aufgezählte Eigenschaften ist ein freies Objekt.

Eine Meldung nennt den **Pfad** des Arguments, nie seinen Wert (§10).
"""

CHECKED = {
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "items", "prefixItems", "minItems", "maxItems", "minLength", "maxLength",
    "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "anyOf", "oneOf", "allOf", "not", "$ref",
}
# Was nur beschreibt und nichts verlangt.
DESCRIPTIVE = {
    "$schema", "$id", "$defs", "definitions", "title", "description", "default",
    "examples", "$comment", "deprecated", "readOnly", "writeOnly",
}
MAX_DEPTH = 32


class Refused(ValueError):
    """Ein Argument passt nicht. `path` nennt es, die Nachricht sagt warum."""

    def __init__(self, path, reason):
        super().__init__(f"Argument {path}: {reason}")
        self.path = path


def remote_refs(schema):
    """Alle `$ref`, die nicht im Schema selbst bleiben — solch ein Schema ist nicht freigebbar."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and not ref.startswith("#"):
                found.append(ref)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schema)
    return found


def unchecked(schema):
    """Schlüsselwörter im Schema, die diese Prüfung nicht durchsetzt."""
    found = set()

    def walk(node, in_props=False):
        if isinstance(node, dict):
            for key, value in node.items():
                if in_props:                    # Namen von Eigenschaften, keine Wörter
                    walk(value)
                    continue
                if key not in CHECKED and key not in DESCRIPTIVE:
                    found.add(key)
                walk(value, in_props=key in ("properties", "$defs", "definitions",
                                             "patternProperties"))
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(schema)
    return sorted(found)


def _pointer(root, ref):
    node = root
    for part in ref[1:].split("/"):
        if not part:
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            raise Refused("(Schema)", f"Verweis {ref} führt ins Leere")
    return node


def _is(kind, value):
    if kind == "null":
        return value is None
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        if isinstance(value, bool):
            return False
        return isinstance(value, int) or (isinstance(value, float) and value.is_integer())
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "string":
        return isinstance(value, str)
    if kind == "array":
        return isinstance(value, list)
    if kind == "object":
        return isinstance(value, dict)
    return True                                 # unbekannter Typname: nicht unser Urteil


TYPE_WORDS = {"null": "null", "boolean": "ein Wahrheitswert", "integer": "eine ganze Zahl",
              "number": "eine Zahl", "string": "ein Text", "array": "eine Liste",
              "object": "ein Objekt"}


def _child(path, key):
    return f"{path}.{key}" if path else key


def _check(schema, value, path, root, depth, top):
    if depth > MAX_DEPTH:
        raise Refused(path or "(Argumente)", "Schema zu tief verschachtelt")
    if schema is True or schema == {} or schema is None:
        return
    if schema is False:
        raise Refused(path or "(Argumente)", "ist hier nicht erlaubt")
    if not isinstance(schema, dict):
        return
    if isinstance(schema.get("$ref"), str):
        _check(_pointer(root, schema["$ref"]), value, path, root, depth + 1, top)
    shown = path or "(Argumente)"

    kinds = schema.get("type")
    if kinds is not None:
        kinds = kinds if isinstance(kinds, list) else [kinds]
        if not any(_is(k, value) for k in kinds):
            raise Refused(shown, "muss " + " oder ".join(TYPE_WORDS.get(k, k) for k in kinds)
                          + " sein")
    if "enum" in schema and isinstance(schema["enum"], list) and value not in schema["enum"]:
        raise Refused(shown, "ist keiner der erlaubten Werte")
    if "const" in schema and value != schema["const"]:
        raise Refused(shown, "hat nicht den festen Wert")

    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        for name in schema.get("required") or ():
            if isinstance(name, str) and name not in value:
                raise Refused(_child(path, name), "fehlt, ist aber Pflicht")
        extra = schema.get("additionalProperties")
        enumerates = top or "properties" in schema
        for name, item in value.items():
            if name in props:
                _check(props[name], item, _child(path, name), root, depth + 1, False)
                continue
            if extra is False or (extra is None and enumerates):
                raise Refused(_child(path, name), "ist im freigegebenen Schema nicht vorgesehen")
            if isinstance(extra, dict):
                _check(extra, item, _child(path, name), root, depth + 1, False)

    if isinstance(value, list):
        prefix = schema.get("prefixItems")
        items = schema.get("items")
        if isinstance(items, list):             # ältere Form der Tupel
            prefix, items = items, schema.get("additionalItems")
        for i, item in enumerate(value):
            sub = prefix[i] if isinstance(prefix, list) and i < len(prefix) else items
            if sub is not None:
                _check(sub, item, f"{shown}[{i}]", root, depth + 1, False)
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            raise Refused(shown, f"braucht mindestens {schema['minItems']} Einträge")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            raise Refused(shown, f"darf höchstens {schema['maxItems']} Einträge haben")

    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            raise Refused(shown, f"muss mindestens {schema['minLength']} Zeichen haben")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            raise Refused(shown, f"darf höchstens {schema['maxLength']} Zeichen haben")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        lo, hi = schema.get("minimum"), schema.get("maximum")
        xlo, xhi = schema.get("exclusiveMinimum"), schema.get("exclusiveMaximum")
        if xlo is True:                         # Entwurf 4: Wahrheitswert an minimum
            xlo, lo = lo, None
        if xhi is True:
            xhi, hi = hi, None
        num = lambda x: isinstance(x, (int, float)) and not isinstance(x, bool)  # noqa: E731
        if num(lo) and value < lo:
            raise Refused(shown, f"darf nicht kleiner als {lo} sein")
        if num(hi) and value > hi:
            raise Refused(shown, f"darf nicht größer als {hi} sein")
        if num(xlo) and value <= xlo:
            raise Refused(shown, f"muss größer als {xlo} sein")
        if num(xhi) and value >= xhi:
            raise Refused(shown, f"muss kleiner als {xhi} sein")

    for sub in schema.get("allOf") or ():
        _check(sub, value, path, root, depth + 1, top)
    if isinstance(schema.get("anyOf"), list) and schema["anyOf"]:
        if not any(_passes(sub, value, path, root, depth, top) for sub in schema["anyOf"]):
            raise Refused(shown, "passt zu keiner der erlaubten Formen")
    if isinstance(schema.get("oneOf"), list) and schema["oneOf"]:
        hits = sum(1 for sub in schema["oneOf"] if _passes(sub, value, path, root, depth, top))
        if hits != 1:
            raise Refused(shown, "muss zu genau einer der erlaubten Formen passen")
    if "not" in schema and _passes(schema["not"], value, path, root, depth, top):
        raise Refused(shown, "hat eine ausgeschlossene Form")


def _passes(schema, value, path, root, depth, top):
    try:
        _check(schema, value, path, root, depth + 1, top)
        return True
    except Refused:
        return False


def check_property(schema, name, value):
    """Prüft einen Wert gegen eine Eigenschaft der obersten Ebene — für feste Werte und Grenzen einer Sicht."""
    root = schema if isinstance(schema, dict) else {}
    props = root.get("properties") if isinstance(root.get("properties"), dict) else {}
    _check(props.get(name, True), value, name, root, 0, False)


def check(schema, arguments):
    """Wirft Refused, wenn `arguments` nicht zum Schema passt. `None` zählt als `{}`."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise Refused("(Argumente)", "müssen ein Objekt sein")
    root = schema if isinstance(schema, dict) else {}
    _check(root or {"type": "object"}, arguments, "", root, 0, True)
