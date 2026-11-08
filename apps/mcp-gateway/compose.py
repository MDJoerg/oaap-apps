"""Was eine Sicht aus dem Angebot der Server macht (Spec §5, RFC-0065 §3).

Seit 0.2 kann eine Sicht Einträge **mehrerer** Server bündeln. Ein
Eintrag ist ein Tool, eine Resource, eine Resource-Vorlage oder ein
Prompt eines Servers, unter einem Namen der Sicht. Hier stehen die
Regeln, nach denen daraus wird, was der Client sieht und was zum Server
geht — reine Funktionen, ohne Datenbank und ohne Netz:

- **Namen.** Ein Tool oder Prompt trägt in der Sicht einen eigenen
  Namen; vorgeschlagen wird der des Servers, wenn er frei ist, sonst
  `<server>_<name>`. Eine Kollision wird beim Speichern abgewiesen,
  nie still aufgelöst. Resources behalten ihre URI.
- **Feste Argumente.** Ein festes Argument verschwindet aus dem Schema,
  das der Client sieht, und wird beim Weiterreichen eingesetzt. Schickt
  der Client es trotzdem, wird abgewiesen — auch wenn sein Wert gleich
  wäre: Wer es schickt, hat nicht das Schema gelesen, das wir zeigen.
- **Grenzen.** Ein Argument lässt sich auf eine Liste von Werten oder
  auf ein **Muster mit `*` und `?`** beschränken. Bewusst kein regulärer
  Ausdruck: Ein Muster wird hier mit einem eigenen, linearen Vergleich
  geprüft, nicht mit einer Maschine, die man mit der passenden Eingabe
  lahmlegen kann (siehe argcheck.py zu `pattern`).

Eine Meldung nennt den Pfad eines Arguments, nie seinen Wert (Spec §10).
"""
import copy
import json
import re

import argcheck

KINDS = ("tool", "resource", "template", "prompt")
KIND_WORDS = {"tool": "Tool", "resource": "Resource", "template": "Resource-Vorlage",
              "prompt": "Prompt"}

# Was ein Server über einen Eintrag sagt und was der Fingerabdruck deckt (Spec §4.2).
# `size` einer Resource absichtlich nicht: Sie ändert sich mit dem Inhalt,
# und der Inhalt geht das Gateway nichts an.
FIELDS = {
    "tool": ("name", "title", "description", "inputSchema", "outputSchema", "annotations"),
    "resource": ("uri", "name", "title", "description", "mimeType", "annotations",
                 "content_sha256", "_meta"),
    "template": ("uriTemplate", "name", "title", "description", "mimeType", "annotations"),
    "prompt": ("name", "title", "description", "arguments"),
}
# Das Feld, das einen Eintrag beim Server bezeichnet.
EFFECT_META = "oaap/effect"   # §3.10, in tools/list ab Protokoll 2025-06-18
SOURCE_FIELD = {"tool": "name", "resource": "uri", "template": "uriTemplate", "prompt": "name"}

# Namen von Tools und Prompts, wie MCP 2025-11-25 sie empfiehlt.
EXPOSED = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
MAX_BOUND_VALUES = 200
MAX_GLOB = 200


UI_MIME = "text/html;profile=mcp-app"
UI_TOOL_KEYS = ("resourceUri", "visibility")
UI_RESOURCE_KEYS = ("csp", "permissions", "domain", "prefersBorder")


def tool_ui(raw):
    """`_meta.ui` eines Tools (MCP Apps, RFC-0075), nur resourceUri und visibility -- oder None.

    Die alte flache Form `_meta["ui/resourceUri"]` wird gelesen und in die neue
    übersetzt. Andere `_meta`-Angaben des Herstellers gehören nicht zum Text,
    der festgehalten wird: Sonst kippte jede Freigabe, wenn ein Server ein
    Feld ergänzt, das niemand liest."""
    meta = raw.get("_meta") if isinstance(raw.get("_meta"), dict) else {}
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    out = {k: ui[k] for k in UI_TOOL_KEYS if k in ui}
    if "resourceUri" not in out and isinstance(meta.get("ui/resourceUri"), str):
        out["resourceUri"] = meta["ui/resourceUri"]
    uri = out.get("resourceUri")
    if not (isinstance(uri, str) and uri.startswith("ui://")):
        return None
    vis = out.get("visibility")
    if vis is not None and not (isinstance(vis, list) and set(vis) <= {"model", "app"} and vis):
        out.pop("visibility")
    return out


def doc_of(kind, raw):
    doc = {f: raw[f] for f in FIELDS[kind] if f in raw}
    if kind == "tool":
        ui = tool_ui(raw)
        if ui:
            doc["_meta"] = {"ui": ui}
    return doc


def ui_view_entry(uri, result):
    """Aus der Antwort auf resources/read einer Ansicht den Katalogeintrag (RFC-0075 D3).

    Festgehalten wird der INHALT (SHA-256 über Text bzw. Bytes): bei einer
    Ansicht ist der Inhalt Code. Dazu MIME und die CSP-Angaben aus `_meta.ui`.
    Gibt (eintrag, None) oder (None, Satz) zurück."""
    import base64
    import hashlib
    contents = (result or {}).get("contents") if isinstance(result, dict) else None
    hit = next((c for c in contents or [] if isinstance(c, dict) and c.get("uri") == uri), None)
    if hit is None and contents and isinstance(contents[0], dict):
        hit = contents[0]
    if hit is None:
        return None, f"Die Ansicht {uri} lieferte keinen Inhalt."
    if (hit.get("mimeType") or "").replace(" ", "") != UI_MIME:
        return None, f"Die Ansicht {uri} ist kein {UI_MIME} (sondern {hit.get('mimeType') or 'ohne Typ'})."
    if isinstance(hit.get("text"), str):
        body = hit["text"].encode("utf-8")
    elif isinstance(hit.get("blob"), str):
        try:
            body = base64.b64decode(hit["blob"], validate=True)
        except ValueError:
            return None, f"Die Ansicht {uri} hat einen kaputten blob."
    else:
        return None, f"Die Ansicht {uri} hat weder text noch blob."
    meta = hit.get("_meta") if isinstance(hit.get("_meta"), dict) else {}
    ui = meta.get("ui") if isinstance(meta.get("ui"), dict) else {}
    entry = {"uri": uri, "name": uri, "mimeType": UI_MIME,
             "content_sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
    ui = {k: ui[k] for k in UI_RESOURCE_KEYS if k in ui}
    if ui:
        entry["_meta"] = {"ui": ui}
    return entry, None


def ui_content_matches(doc, result):
    """Stimmt der gelieferte Inhalt einer Ansicht mit dem festgehaltenen überein?"""
    if "content_sha256" not in doc:
        return True
    entry, _ = ui_view_entry(doc.get("uri", ""), result)
    return bool(entry) and entry["content_sha256"] == doc["content_sha256"]


def source_of(kind, doc):
    return doc.get(SOURCE_FIELD[kind])


# ------------------------------------------------------------------ Namen

def valid_exposed(kind, name):
    if kind in ("tool", "prompt"):
        if not EXPOSED.match(name or ""):
            raise ValueError(f"Der Name „{name}“ passt nicht: Buchstaben, Ziffern, _ . - , "
                             "höchstens 128 Zeichen.")
    elif not name:
        raise ValueError("Eine Resource braucht ihre URI.")
    return name


def propose(kind, server, source, taken):
    """Der Name, den die Seite vorschlägt. Bei Resources die URI, unverändert."""
    if kind in ("resource", "template"):
        return source
    if EXPOSED.match(source or "") and source not in taken:
        return source
    clean = re.sub(r"[^A-Za-z0-9_.-]", "_", f"{server}_{source}")[:128]
    return clean


# ------------------------------------------------------------------ Muster

def glob_match(pattern, text):
    """`*` = beliebig viele Zeichen, `?` = genau eines; sonst wörtlich. Linear-quadratisch, nie exponentiell."""
    p = t = 0
    star = mark = -1
    while t < len(text):
        if p < len(pattern) and (pattern[p] == "?" or pattern[p] == text[t]) and pattern[p] != "*":
            p += 1
            t += 1
        elif p < len(pattern) and pattern[p] == "*":
            star, mark = p, t
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            t = mark
        else:
            return False
    while p < len(pattern) and pattern[p] == "*":
        p += 1
    return p == len(pattern)


def glob_regex(pattern):
    """Nur zum ZEIGEN im Schema (`pattern`), damit das Modell die Grenze kennt. Geprüft wird mit glob_match."""
    out = []
    for ch in pattern:
        out.append(".*" if ch == "*" else "." if ch == "?" else re.escape(ch))
    return "^" + "".join(out) + "$"


# ------------------------------------------------------------------ feste Argumente, Grenzen

def _props(schema):
    if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
        return schema["properties"]
    return {}


def tailor(doc, fixed, bounds):
    """Prüft feste Argumente und Grenzen gegen das freigegebene Schema. Gibt sie bereinigt zurück.

    `fixed`: {argument: wert}. `bounds`: {argument: {"enum": [...]} oder {"glob": "Z*"}}.
    Wirft ValueError mit einem Satz.
    """
    fixed = fixed or {}
    bounds = bounds or {}
    if not isinstance(fixed, dict) or not isinstance(bounds, dict):
        raise ValueError("Feste Argumente und Grenzen sind je ein JSON-Objekt.")
    schema = doc.get("inputSchema") if isinstance(doc.get("inputSchema"), dict) else {}
    props = _props(schema)
    for name, value in fixed.items():
        if name not in props:
            raise ValueError(f"Festes Argument {name}: im freigegebenen Schema nicht vorgesehen.")
        try:
            argcheck.check_property(schema, name, value)
        except argcheck.Refused as exc:
            raise ValueError(f"Fester Wert passt nicht zum Schema — {exc}") from None
    out = {}
    for name, bound in bounds.items():
        if name not in props:
            raise ValueError(f"Grenze für {name}: im freigegebenen Schema nicht vorgesehen.")
        if name in fixed:
            raise ValueError(f"{name} ist fest vorgegeben; eine Grenze daneben wäre eine zweite Wahrheit.")
        if not isinstance(bound, dict) or len(bound) != 1 or next(iter(bound)) not in ("enum", "glob"):
            raise ValueError(f"Grenze für {name}: genau eines von "
                             '{"enum": [...]} oder {"glob": "Z*"}.')
        if "enum" in bound:
            values = bound["enum"]
            if not isinstance(values, list) or not values or len(values) > MAX_BOUND_VALUES:
                raise ValueError(f"Grenze für {name}: eine Liste mit 1 bis {MAX_BOUND_VALUES} Werten.")
            for v in values:
                try:
                    argcheck.check_property(schema, name, v)
                except argcheck.Refused as exc:
                    raise ValueError(f"Erlaubter Wert passt nicht zum Schema — {exc}") from None
            out[name] = {"enum": values}
        else:
            pat = bound["glob"]
            if not isinstance(pat, str) or not pat or len(pat) > MAX_GLOB:
                raise ValueError(f"Grenze für {name}: ein Muster mit 1 bis {MAX_GLOB} Zeichen.")
            out[name] = {"glob": pat}
    return dict(fixed), out


def shown_schema(input_schema, fixed, bounds):
    """Das Schema, das der Client sieht: ohne feste Argumente, mit den Grenzen."""
    schema = copy.deepcopy(input_schema) if isinstance(input_schema, dict) else {"type": "object"}
    if not fixed and not bounds:
        return schema
    props = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    for name in fixed or {}:
        props.pop(name, None)
    if isinstance(schema.get("required"), list):
        schema["required"] = [r for r in schema["required"] if r not in (fixed or {})]
    for name, bound in (bounds or {}).items():
        if name not in props:
            continue
        prop = dict(props[name]) if isinstance(props[name], dict) else {}
        if "enum" in bound:
            prop["enum"] = bound["enum"]
        else:
            prop["pattern"] = glob_regex(bound["glob"])
        props[name] = prop
    return schema


def prepare_call(doc, fixed, bounds, arguments):
    """Prüft die Argumente des Clients und gibt die zurück, die zum Server gehen. Wirft argcheck.Refused."""
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise argcheck.Refused("(Argumente)", "müssen ein Objekt sein")
    for name in fixed or {}:
        if name in arguments:
            raise argcheck.Refused(name, "ist in dieser Sicht fest vorgegeben")
    merged = dict(arguments)
    merged.update(fixed or {})
    argcheck.check(doc.get("inputSchema"), merged)
    for name, bound in (bounds or {}).items():
        if name not in arguments:
            continue
        value = arguments[name]
        if "enum" in bound and value not in bound["enum"]:
            raise argcheck.Refused(name, "ist in dieser Sicht keiner der erlaubten Werte")
        if "glob" in bound and not (isinstance(value, str) and glob_match(bound["glob"], value)):
            raise argcheck.Refused(name, "passt in dieser Sicht nicht zum erlaubten Muster")
    return merged


# ------------------------------------------------------------------ Resource-Vorlagen

# RFC 6570, nur Stufe 1 und `{+x}`: genug für URIs wie `sap://class/{name}`
# oder `file:///{+path}`. Eine Vorlage mit anderen Operatoren lässt sich
# nicht eindeutig prüfen und wird nicht freigegeben.
_VAR = re.compile(r"\{(\+?)([A-Za-z0-9_]+)\}")


def template_supported(template):
    if not isinstance(template, str) or not template:
        return False
    rest = _VAR.sub("", template)
    return "{" not in rest and "}" not in rest


def template_match(template, uri):
    """Passt `uri` zur Vorlage? `{x}` = ein Stück ohne `/`, `{+x}` = beliebig, je mindestens ein Zeichen."""
    parts, pos = [], 0
    for m in _VAR.finditer(template):
        parts.append(("lit", template[pos:m.start()]))
        parts.append(("any" if m.group(1) else "seg", ""))
        pos = m.end()
    parts.append(("lit", template[pos:]))
    return _match(parts, 0, uri, 0)


def _match(parts, i, uri, j):
    # Höchstens eine Handvoll Variablen je Vorlage; die Tiefe ist durch sie begrenzt.
    if i == len(parts):
        return j == len(uri)
    kind, lit = parts[i]
    if kind == "lit":
        return uri.startswith(lit, j) and _match(parts, i + 1, uri, j + len(lit))
    end = len(uri)
    if kind == "seg":
        slash = uri.find("/", j)
        end = slash if slash != -1 else len(uri)
    for k in range(end, j, -1):
        if _match(parts, i + 1, uri, k):
            return True
    return False


# ------------------------------------------------------------------ Prompts

def check_prompt_arguments(doc, arguments):
    """Prompt-Argumente sind Texte; unbekannte und fehlende Pflichtargumente werden abgewiesen."""
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise argcheck.Refused("(Argumente)", "müssen ein Objekt sein")
    declared = {a.get("name"): a for a in doc.get("arguments") or () if isinstance(a, dict)}
    for name, value in arguments.items():
        if name not in declared:
            raise argcheck.Refused(name, "ist im freigegebenen Prompt nicht vorgesehen")
        if not isinstance(value, str):
            raise argcheck.Refused(name, "muss ein Text sein")
    for name, arg in declared.items():
        if arg.get("required") and name not in arguments:
            raise argcheck.Refused(name, "fehlt, ist aber Pflicht")
    return arguments


# ------------------------------------------------------------------ was der Client sieht

def shaped(kind, item, version):
    """Der freigegebene Eintrag in der Form der Protokollversion des Clients (A3)."""
    doc = json.loads(item["approved_doc"])
    new = version >= "2025-06-18"
    desc = item["description"] or doc.get("description")
    if kind == "tool":
        out = {"name": item["name"], "description": desc or "",
               "inputSchema": shown_schema(doc.get("inputSchema") or {"type": "object"},
                                           json.loads(item["fixed"] or "{}"),
                                           json.loads(item["bounds"] or "{}"))}
        if "annotations" in doc:
            out["annotations"] = doc["annotations"]
        if new:
            for field in ("title", "outputSchema"):
                if field in doc:
                    out[field] = doc[field]
            # §3.10: die Wirkungsklasse, die der Mandant in der Sicht gesetzt hat --
            # nicht der Hinweis des Herstellers. Ein Chat fragt vor allem, was nicht
            # `read` ist (RFC-0074 D6).
            out["_meta"] = {EFFECT_META: item["effect"]}
            # 0.8.0 (RFC-0075): die freigegebene Ansicht, wie festgehalten.
            if isinstance((doc.get("_meta") or {}).get("ui"), dict):
                out["_meta"]["ui"] = doc["_meta"]["ui"]
        return out
    if kind == "prompt":
        out = {"name": item["name"]}
        if desc:
            out["description"] = desc
        if doc.get("arguments"):
            out["arguments"] = doc["arguments"]
        if new and "title" in doc:
            out["title"] = doc["title"]
        return out
    field = "uri" if kind == "resource" else "uriTemplate"
    out = {field: item["name"], "name": doc.get("name") or item["name"]}
    if kind == "resource" and new and isinstance((doc.get("_meta") or {}).get("ui"), dict):
        out["_meta"] = {"ui": doc["_meta"]["ui"]}
    if desc:
        out["description"] = desc
    for f in ("mimeType", "annotations"):
        if f in doc:
            out[f] = doc[f]
    if new and "title" in doc:
        out["title"] = doc["title"]
    return out
