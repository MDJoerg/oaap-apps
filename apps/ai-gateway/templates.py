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

Seit 0.11.1 füllt `gwcore.fill` (RFC-0107 D2) -- dieselbe Maschine wie im
MCP-Gateway. Hier steht nur, was die Namen im KI-Gateway bedeuten.
"""
import json
import os

import props
from gwcore.fill import OMIT, TemplateError, check_names, dump, fill, load_dir, require  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "templates")
REQUIRED = ("target", "title", "format", "checked", "file", "where", "key_hint", "document",
            "model", "models_as")
TEXT_NAMES = ("gateway", "url", "id", "name")
WHOLE_NAMES = ("tools", "vision", "context", "output", "models")


def _check(tpl, where):
    require(tpl, REQUIRED, where)
    if tpl["models_as"] not in ("list", "map"):
        raise TemplateError(f"{where}: models_as ist list oder map")
    check_names(tpl["document"], TEXT_NAMES, WHOLE_NAMES, where)
    check_names(tpl["model"], TEXT_NAMES, WHOLE_NAMES, where)
    if json.dumps(tpl["document"]).count('"{models}"') != 1:
        raise TemplateError(f"{where}: {{models}} genau einmal im Dokument")


def load(directory=DIR):
    """{ziel: vorlage} — eine fehlerhafte Datei ist ein Fehler beim Start, kein stilles Fehlen."""
    return load_dir(directory, _check)


def _whole(tpl, values):
    out = {}
    for name in props.PROPS:
        value = values.get(name, "")
        if value == "":
            out[name] = OMIT
            continue
        mapped = tpl.get("values", {}).get(name, {})
        out[name] = mapped[value] if value in mapped else props.typed(name, value)
    return out


def render(tpl, models, url, gateway):
    """(text, fehlende) — `models` = [{"id": …, "props": {prop: wert}}].

    `fehlende` = {modellname: [unbekannte Angaben]} für den Satz auf der Seite.
    """
    blocks, missing = [], {}
    for m in models:
        texts = {"gateway": gateway, "url": url, "id": m["id"], "name": m["id"]}
        blocks.append((m["id"], fill(tpl["model"], texts, _whole(tpl, m["props"]))))
        gone = [p for p in props.PROPS if m["props"].get(p, "") == ""]
        if gone:
            missing[m["id"]] = gone
    built = ([b for _i, b in blocks] if tpl["models_as"] == "list"
             else {i: b for i, b in blocks})
    doc = fill(tpl["document"], {"gateway": gateway, "url": url}, {"models": built})
    return dump(doc), missing
