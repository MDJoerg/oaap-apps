"""Vorlagen füllen (RFC-0106 Stufe 2, RFC-0107 D2): eine Maschine für beide Gateways.

Eine Vorlage ist **Daten**: ein JSON-Baum, in dem Platzhalter stehen. Es gibt zwei
Arten:

- **Text** (`{url}`, `{name}` …) — auch mitten in einem Wert oder in einem Schlüssel;
- **ganzer Wert** (`{models}`, `{context}`, `{callback_port}` …) — der String besteht
  nur aus dem Platzhalter und wird durch einen beliebigen JSON-Wert ersetzt (Zahl,
  Liste, Objekt). Steht dort `OMIT`, fällt das Feld weg; ein Objekt, das dadurch leer
  wird, auch. Geraten wird nichts.

Welche Namen eine Vorlage benutzen darf, sagt die App. Ein unbekannter Name oder ein
ganzer-Wert-Name mitten in einem Text ist ein Fehler beim Laden (`TemplateError`),
kein stilles Fehlen.

Was hier **nicht** steht: was die Namen bedeuten. Das KI-Gateway kennt Modelle und
deren Eigenschaften, das MCP-Gateway Sichten und Wege; beide bauen ihre Werte selbst
und rufen dann `fill`. Einen Platzhalter für einen Schlüssel gibt es in keiner App
(RFC-0106 D9, RFC-0107 D3).
"""
import json
import os
import re

ANY = re.compile(r"\{([a-z_]+)\}")
OMIT = object()


class TemplateError(ValueError):
    pass


def strings(node):
    """Alle Strings eines Baums, auch die Schlüssel von Objekten."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)
    elif isinstance(node, str):
        yield node


def check_names(node, text_names, whole_names, where):
    """Jeder Platzhalter ist ein Textname oder ein ganzer Wert, der allein steht."""
    for s in strings(node):
        for name in ANY.findall(s):
            if name in text_names:
                continue
            if name not in whole_names:
                raise TemplateError(f"{where}: unbekannter Platzhalter {{{name}}}")
            if s != "{" + name + "}":
                raise TemplateError(f"{where}: {{{name}}} nur als ganzer Wert")


def require(tpl, keys, where):
    missing = [k for k in keys if k not in tpl]
    if missing:
        raise TemplateError(f"{where}: es fehlt {', '.join(missing)}")


def load_dir(directory, check, key="target"):
    """{ziel: vorlage} aus allen *.json eines Ordners; `check(tpl, dateiname)` wirft."""
    out = {}
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(directory, fn), encoding="utf-8") as fh:
            tpl = json.load(fh)
        check(tpl, fn)
        if tpl.get(key) in out:
            raise TemplateError(f"{fn}: {key} {tpl.get(key)!r} gibt es zweimal")
        out[tpl[key]] = tpl
    return out


def text(s, values):
    """Textplatzhalter ersetzen; unbekannte bleiben stehen (check_names hat sie schon)."""
    return ANY.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), s)


def fill(node, texts, whole):
    """Den Baum füllen. `texts` = {name: str}, `whole` = {name: wert oder OMIT}."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            r = fill(v, texts, whole)
            if r is OMIT or (isinstance(r, dict) and not r and v):
                continue
            out[text(k, texts)] = r
        return out
    if isinstance(node, list):
        return [r for r in (fill(v, texts, whole) for v in node) if r is not OMIT]
    if isinstance(node, str):
        m = ANY.fullmatch(node)
        if m and m.group(1) in whole:
            return whole[m.group(1)]
        return text(node, texts)
    return node


def dump(doc):
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
