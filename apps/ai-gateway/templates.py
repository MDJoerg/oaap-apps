"""Vorlagen für Clients (RFC-0106 Stufe 2, Spec `oaap.ai.gateway` §3.2).

Eine Vorlage ist **Daten** (`templates/<ziel>.json`), kein Code: ein neues
Werkzeug ist eine neue Datei in einer Fassung der App (D6). Die Vorlage
beschreibt das Dokument als JSON, in dem Platzhalter stehen:

- `{gateway}`, `{url}`, `{id}`, `{name}` — Text, auch mitten in einem Wert
- `{tools}`, `{vision}`, `{context}`, `{output}` — nur als ganzer Wert; er
  wird zur Zahl oder zu ja/nein, oder über `values` zu etwas anderem
  (opencode will für Bilder eine Liste `["text", "image"]`)
- `{models}` — nur als ganzer Wert: die Liste (`models_as: list`) oder das
  Objekt (`models_as: map`, Schlüssel = `{id}`) der Modell-Blöcke

Eine **unbekannte** Eigenschaft fällt samt ihrem Feld weg; ein Objekt, das
dadurch leer wird, auch. Das Werkzeug nimmt dann seinen eigenen Vorgabewert,
und die Seite sagt, welche Angaben fehlten. Geraten wird nichts (D3).

Der Schlüssel steht nie im Ergebnis (D9): Die Vorlage selbst trägt den
Platzhalter des Werkzeugs (`${input:…}`, eine Umgebungsvariable), und es gibt
keinen Platzhalter, in den ein Schlüssel passen würde.
"""
import json
import os
import re

import props

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "templates")
REQUIRED = ("target", "title", "format", "checked", "file", "where", "key_hint", "document",
            "model", "models_as")
TEXT_NAMES = ("gateway", "url", "id", "name")
WHOLE = re.compile(r"^\{(tools|vision|context|output|models)\}$")
ANY = re.compile(r"\{([a-z_]+)\}")
OMIT = object()


class TemplateError(ValueError):
    pass


def _check(tpl, where):
    missing = [k for k in REQUIRED if k not in tpl]
    if missing:
        raise TemplateError(f"{where}: es fehlt {', '.join(missing)}")
    if tpl["models_as"] not in ("list", "map"):
        raise TemplateError(f"{where}: models_as ist list oder map")

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str):
            for name in ANY.findall(node):
                if name in TEXT_NAMES:
                    continue
                if not WHOLE.match(node):
                    raise TemplateError(f"{where}: {{{name}}} nur als ganzer Wert")
    walk(tpl["document"])
    walk(tpl["model"])
    if json.dumps(tpl["document"]).count('"{models}"') != 1:
        raise TemplateError(f"{where}: {{models}} genau einmal im Dokument")


def load(directory=DIR):
    """{ziel: vorlage} — eine fehlerhafte Datei ist ein Fehler beim Start, kein stilles Fehlen."""
    out = {}
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(directory, fn), encoding="utf-8") as fh:
            tpl = json.load(fh)
        _check(tpl, fn)
        out[tpl["target"]] = tpl
    return out


def _fill(node, ctx, tpl):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            r = _fill(v, ctx, tpl)
            if r is OMIT or (isinstance(r, dict) and not r and v):
                continue
            out[_text(k, ctx)] = r
        return out
    if isinstance(node, list):
        return [r for r in (_fill(v, ctx, tpl) for v in node) if r is not OMIT]
    if isinstance(node, str):
        m = WHOLE.match(node)
        if m:
            name = m.group(1)
            if name == "models":
                return ctx["models"]
            value = ctx["props"].get(name, "")
            if value == "":
                return OMIT
            mapped = tpl.get("values", {}).get(name, {})
            return mapped[value] if value in mapped else props.typed(name, value)
        return _text(node, ctx)
    return node


def _text(s, ctx):
    return ANY.sub(lambda m: str(ctx.get(m.group(1), m.group(0)))
                   if m.group(1) in TEXT_NAMES else m.group(0), s)


def render(tpl, models, url, gateway):
    """(text, fehlende) — `models` = [{"id": …, "props": {prop: wert}}].

    `fehlende` = {modellname: [unbekannte Angaben]} für den Satz auf der Seite.
    """
    blocks, missing = [], {}
    for m in models:
        ctx = {"gateway": gateway, "url": url, "id": m["id"], "name": m["id"],
               "props": m["props"]}
        blocks.append((m["id"], _fill(tpl["model"], ctx, tpl)))
        gone = [p for p in props.PROPS if m["props"].get(p, "") == ""]
        if gone:
            missing[m["id"]] = gone
    built = ([b for _i, b in blocks] if tpl["models_as"] == "list"
             else {i: b for i, b in blocks})
    doc = _fill(tpl["document"], {"gateway": gateway, "url": url, "models": built, "props": {}},
                tpl)
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n", missing
