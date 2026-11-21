#!/usr/bin/env python3
"""Der Store-Editor im Rahmen der Plattform (RFC-0118) -- ohne Netz.

Rahmen und Name, Reiter, Listenbericht (Suche, Filter, Sortierung,
Blättern), Hilfe aus docs/ und die MCP-Tür mit den Rollen der Seite.

    py -3 test_frame.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


LIST_URL = "https://example.invalid/oaap-store.json"
DOC = {"store": "0.2", "id": "oaap.gross", "name": "Große Liste",
       "apps": [{"id": f"app-{i:02d}", "name": f"App {i:02d}", "type": "native",
                 "version": "1.0.0", "app_class": "frontend",
                 "package": {"git": "https://example.invalid/r", "path": f"apps/app-{i:02d}"}}
                for i in range(60)]}


def fake_fetch(url, headers=None):
    if url == LIST_URL:
        return json.dumps(DOC)
    raise OSError(f"im Test nicht erreichbar: {url}")


class Fake(app.Handler):
    def __init__(self, roles="keyuser"):
        self.body, self.status, self.location = "", 200, ""
        self.headers = {"X-OAAP-User": "anna", "X-OAAP-Roles": roles}

    def send_html(self, body, status=200):
        self.body, self.status = body, status

    def redirect(self, target):
        self.location = target


TMP = tempfile.mkdtemp(prefix="store-editor-frame-")
app.fetch = fake_fetch
app.DATA_DIR = TMP
app.TOKENS = ["ghp_geheim", "", ""]

try:
    app.save_sources([{"url": LIST_URL, "name": "Groß", "token": 1}])
    h = Fake()

    print("=== Rahmen und Name ===")
    body = h.lists_page("anna", "keyuser")
    ok("Kit der Plattform", '<link rel="stylesheet" href="/platform/theme.css">' in body)
    ok("Rahmenskript der Plattform", '<script src="/platform/app.js" defer></script>' in body)
    ok("App-Name ohne OAAP in Kopf und Titel",
       '<span class="oaap-appname">Store-Editor</span>' in body and "OAAP Store" not in body)
    ok("Hilfeprodukt am Kopf", 'data-oaap-product="store-editor"' in body)
    ok("keine Rollen und kein Benutzername im Kopf", "keyuser" not in body and "anna" not in body)
    ok("keine eigene Palette", "--oaap-blue-950" not in body and "#1e3a8a" not in body)
    ok("Reiter: Übersicht zuerst, Einrichtung zuletzt",
       body.find(">Übersicht<") < body.find(">Liste einfügen<") < body.find(">Listen und Zugang<"))
    ok("Übersicht ist der aktive Reiter", '<a href="/" aria-current="page">Übersicht</a>' in body)
    ok("der Schlüssel steht nirgends", "ghp_geheim" not in body)

    print("\n=== Seite einer Liste: Reiter und Listenbericht ===")
    body = h.list_page("0", "anna", "keyuser", params={})
    ok("Zurück zur Liste über dem Titel", 'class="oaap-back" href="/"' in body)
    ok("drei Reiter der Liste", "/liste/0/pruefung" in body and "/liste/0/entwurf" in body)
    ok("Filterleiste mit Suche", 'class="oaap-filter"' in body and 'name="q"' in body)
    ok("Zählzeile 1–50 von 60", "1–50 von 60 Einträge" in body, body[body.find("oaap-countline"):][:120])
    ok("Blättern: Weitere 50", "Weitere 50" in body and "offset=50" in body)
    ok("keine Formulare in Tabellenzeilen",
       "<form" not in body[body.find("<tbody>"):body.find("</tbody>")])
    body = h.list_page("0", "anna", "keyuser", params={"offset": ["50"]})
    ok("zweite Seite 51–60", "51–60 von 60 Einträge" in body)
    body = h.list_page("0", "anna", "keyuser", params={"q": ["App 07"]})
    ok("Suche findet genau eine App", "App 07" in body and "App 08" not in body
       and "1 Eintrag<" in body, body[body.find("oaap-countline"):][:120])
    body = h.list_page("0", "anna", "keyuser", params={"sort": ["-app"]})
    ok("absteigend sortiert: App 59 vor App 00",
       0 < body.find("App 59") < body.find("App 58") and 'aria-sort="descending"' in body)
    body = h.list_page("0", "anna", "keyuser", params={"pruefung": ["ok"]})
    ok("Filter Prüfung=in Ordnung: ohne Manifest nichts in Ordnung",
       "Keine Treffer für diese Auswahl." in body)
    body = h.list_page("0", "anna", "keyuser", params={}, tab="pruefung")
    ok("Reiter Prüfung zeigt Befunde als Listenbericht",
       "Befunde" in body and 'name="art"' in body and "Hinweis" in body)
    body = h.list_page("0", "anna", "keyuser", params={}, tab="entwurf")
    ok("Entfernen steht im Reiter Entwurf (keyuser)", "Liste aus dem Editor nehmen" in body)
    body = Fake("user").list_page("0", "anna", "user", params={}, tab="entwurf")
    ok("ein user sieht Entfernen nicht", "Liste aus dem Editor nehmen" not in body)
    body = h.sources_page("anna", "keyuser")
    ok("Listen und Zugang ohne Zeilenformular",
       "<form" not in body[body.find("<tbody>"):body.find("</tbody>")])

    print("\n=== Seite eines Eintrags ===")
    body = h.entry_page("0", "app-03", "anna", "keyuser")
    ok("Abgleichen auf der Seite der App", 'action="/liste/0/abgleich"' in body
       and 'value="app-03"' in body)
    ok("keine RFC-Nummern in der Oberfläche", "RFC-" not in body and "§1." not in body)

    print("\n=== Liste einfügen (geprüft, nicht gespeichert) ===")

    class Paste(Fake):
        def form(self):
            return {"doc": [json.dumps(DOC)]}
    p = Paste()
    p.paste_result("anna", "keyuser")
    ok("eingefügte Liste wird geprüft", "Große Liste" in p.body and p.status == 200, p.body[-300:])

    print("\n=== Hilfe aus docs/ ===")
    h = Fake()
    h.help_page("", "anna", "keyuser")
    ok("/hilfe zeigt die Übersicht aus docs/index.md", "Store-Editor – Überblick" in h.body)
    ok("Verweise bleiben in /hilfe", 'href="/hilfe/faq"' in h.body)
    h = Fake()
    h.help_page("faq", "anna", "keyuser")
    ok("/hilfe/faq zeigt die FAQ", "Häufige Fragen" in h.body)
    h = Fake()
    h.help_page("../app", "anna", "keyuser")
    ok("kein Weg aus docs/ hinaus", h.status == 404)

    print("\n=== MCP-Tür ===")
    door = app.DOOR
    keyuser = {"user": "anna", "roles": ["keyuser"]}
    fremd = {"user": "bob", "roles": ["server_admin"]}

    def call(name, args, who):
        r = door.answer({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": args}}, who)["result"]
        return r["isError"], r["content"][0]["text"]

    tools = door.answer({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, keyuser)["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    ok("tools/list nennt die Lesewerkzeuge",
       names == ["store_editor_entries", "store_editor_findings", "store_editor_lists"], names)
    ok("alle nur lesend", all(t["annotations"]["readOnlyHint"] for t in tools))
    prompts = door.answer({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"}, keyuser)["result"]["prompts"]
    ok("zwei Vorlagen", len(prompts) == 2)
    err, text = call("store_editor_lists", {}, keyuser)
    ok("Listen lesen als keyuser", not err and "Groß" in text and "Platz 1" in text, text)
    ok("ohne den Schlüssel", "ghp_geheim" not in text)
    err, text = call("store_editor_lists", {}, fremd)
    ok("ohne Rolle der Seite abgelehnt", err and "nur für" in text, text)
    err, text = call("store_editor_entries", {"liste": "0", "q": "app-1"}, keyuser)
    data = json.loads(text)
    ok("Einträge mit Suche", not err and len(data["eintraege"]) == 10, text[:200])
    err, text = call("store_editor_entries", {"liste": "0"}, fremd)
    ok("Einträge ohne Rolle abgelehnt", err)
    err, text = call("store_editor_findings", {"liste": "Groß", "app": "app-05"}, keyuser)
    data = json.loads(text)
    ok("Befunde zu einer App", not err and data["befunde"]
       and all(b["app"] in ("app-05", "") for b in data["befunde"]), text[:200])
    err, text = call("store_editor_findings", {"liste": "9"}, keyuser)
    ok("unbekannte Liste: ein Satz statt Absturz", err and "nicht eingetragen" in text)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
