"""Vorlagen für Clients (RFC-0107, Spec `oaap.ai.mcp-gateway` §2.4).

Eine Vorlage ist **Daten** (`templates/<ziel>.json`): die Datei eines Werkzeugs
(Claude Code, VS Code, opencode), in der Platzhalter stehen. Gefüllt wird mit
`gwcore.fill` -- derselben Maschine wie im KI-Gateway (D2).

Je Sicht gibt es einen **Weg**: `key` (der Schlüssel steht als Umgebungsvariable
`OAAP_MCP_KEY` in der Datei, D4) oder `signin` (Anmeldung im Realm des Mandanten:
Client-ID und Rücksprung-Port). Eine Vorlage sagt in `ways`, welche Wege sie
kann, und hat in `entry` und `notes` je Weg eine Form.

Platzhalter:
- Text: `{name}` (Sicht), `{url}`, `{gateway}`, `{client_id}`, `{redirect_uri}`
- ganzer Wert: `{servers}` (die Einträge, Liste oder Objekt), `{callback_port}`

Einen Platzhalter für den Wert eines Schlüssels gibt es nicht (D3), und eine
Vorlage, in der `mcp-oaap-` steht, wird beim Laden abgewiesen.
"""
import json
import os

from gwcore.fill import TemplateError, check_names, dump, fill, load_dir, require

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "templates")
REQUIRED = ("target", "title", "format", "checked", "file", "where", "ways", "document",
            "entry", "servers_as", "notes")
TEXT_NAMES = ("name", "url", "gateway", "client_id", "redirect_uri")
WHOLE_NAMES = ("servers", "callback_port")
WAYS = ("key", "signin")
KEY_VAR = "OAAP_MCP_KEY"
KEY_PREFIX = "mcp-oaap-"


def _check(tpl, where):
    require(tpl, REQUIRED, where)
    ways = tpl["ways"]
    if not isinstance(ways, list) or not ways or any(w not in WAYS for w in ways):
        raise TemplateError(f"{where}: ways ist eine Liste aus {', '.join(WAYS)}")
    if tpl["servers_as"] not in ("list", "map"):
        raise TemplateError(f"{where}: servers_as ist list oder map")
    for w in ways:
        if w not in tpl["entry"] or w not in tpl["notes"]:
            raise TemplateError(f"{where}: für den Weg {w} fehlt entry oder notes")
    check_names(tpl["document"], TEXT_NAMES, WHOLE_NAMES, where)
    for w in ways:
        check_names(tpl["entry"][w], TEXT_NAMES, WHOLE_NAMES, where)
        check_names(tpl["notes"][w], (), (), where)
    if json.dumps(tpl["document"]).count('"{servers}"') != 1:
        raise TemplateError(f"{where}: {{servers}} genau einmal im Dokument")
    if KEY_PREFIX in json.dumps(tpl):
        raise TemplateError(f"{where}: ein Schlüsselwert hat in einer Vorlage nichts zu suchen")


def load(directory=DIR):
    """{ziel: vorlage} -- eine fehlerhafte Datei ist ein Fehler beim Start."""
    return load_dir(directory, _check)


def render(tpl, picks, gateway, client_id="", callback_port=0, redirect_uri=""):
    """Text der Datei. `picks` = [{"name", "url", "way"}], schon geprüft."""
    built = []
    for p in picks:
        texts = {"name": p["name"], "url": p["url"], "gateway": gateway,
                 "client_id": client_id, "redirect_uri": redirect_uri}
        built.append((p["name"], fill(tpl["entry"][p["way"]], texts,
                                      {"callback_port": callback_port})))
    servers = ([b for _n, b in built] if tpl["servers_as"] == "list"
               else {n: b for n, b in built})
    return dump(fill(tpl["document"], {"gateway": gateway}, {"servers": servers}))


def notes(tpl, picks):
    """Die Hinweise der Vorlage für die Wege, die vorkommen, in der Reihenfolge von WAYS."""
    used = {p["way"] for p in picks}
    return [tpl["notes"][w] for w in WAYS if w in used]
