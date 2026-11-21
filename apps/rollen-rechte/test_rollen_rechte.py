#!/usr/bin/env python3
"""Rollen & Rechte ohne Identity-Dienst: Rahmen, Listen, Objektseiten, MCP-Tür.

Der Weg gegen den ECHTEN Identity-Dienst (wer verwalten darf, Herkunft der
Formulare, der volle Ablauf) liegt in oaap-reference/test/
test_rollen_rechte_app.py. Hier steht ein kleiner Schein-Dienst an der Stelle
von door.call: er antwortet nur der Person "u-admin" und lehnt jede andere
mit 403 ab -- so wie der echte Dienst einen Nicht-Admin ablehnt.

Geprüft wird:
  * jede Seite trägt Kit und Rahmen der Plattform und den Namen ohne OAAP;
  * Reiter sind Links, Personen zuerst, Protokoll zuletzt;
  * Listen als Listenbericht: Suche, Filter, Sortierung, Blättern ab 50;
  * keine Formulare in Tabellenzeilen -- Beenden, Ausblenden, Entfernen
    stehen auf der Seite des Objekts;
  * echte Umlaute statt „gehoeren“;
  * die MCP-Tür: tools/list, tools/call, und ein Nicht-Admin bekommt die
    Ablehnung, keine Namen -- über HTTP vor der Sitzungsprüfung.

Aufruf: py -3 test_rollen_rechte.py
"""
import http.client
import json
import os
import re
import sys
import threading
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app   # noqa: E402
import door  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:600]}")


USERS = [{"id": f"u-{n:03d}", "username": f"person{n:03d}", "display_name": f"Person {n}",
          "active": n % 10 != 0} for n in range(1, 121)]
USERS.append({"id": "u-admin", "username": "chef", "display_name": "<img src=x>"})
AGENTS = [{"id": "u-agent", "username": "agent-a", "display_name": "Agent"}]
DECLS = [{"app": "vereinsportal", "declaration": {
    "objects": [{"key": "news", "fields": [{"key": "area", "values": ["news", "sport"]},
                                           {"key": "team", "context": "Mannschaft"}]}],
    "role_templates": [{"key": "bereich", "title": "Bereich",
                        "grants": [{"object": "news", "activities": ["publish"],
                                    "area": "$value"}]}]}}]
ROLES = [{"id": "r1", "name": "Redaktion", "app": "vereinsportal", "template": "bereich",
          "values": {"area": ["news"]}},
         {"id": "r2", "name": "Alt", "app": "vereinsportal", "template": "bereich",
          "values": {}, "retired": True}]
COLLS = [{"id": "c1", "name": "Paket Redaktion", "roles": ["r1"]},
         {"id": "c2", "name": "Paket Alt", "roles": ["r2"], "retired": True}]
ASSIGN = [{"id": "a1", "subject": "u-001", "collection": "c1", "context": {},
           "valid_from": None, "valid_to": None, "source": "delegated", "live": True,
           "orphaned": True, "granted_by": "carla", "granted_at": "2026-11-01"},
          {"id": "a2", "subject": "u-002", "collection": "c1", "context": {},
           "valid_from": None, "valid_to": "2026-12-31", "source": "manual", "live": True,
           "orphaned": False, "granted_by": "chef", "granted_at": "2026-11-02"}]
MAPS = [{"id": "m1", "group": "Verein/Redaktion", "collection": "c1",
         "collection_name": "Paket Redaktion"}]
LOG = [{"when": f"2026-11-{d:02d}T10:00:00", "who": "chef", "action": "authz.assign",
        "subject": "u-001", "detail": ""} for d in range(1, 21)]
CALLS = []


def fake_call(method, path, person, body=None, params=None):
    CALLS.append((method, path, person))
    if person != "u-admin":
        raise door.DoorError(403, "not a tenant admin")
    if method != "GET":
        return {}
    p = params or {}
    if path == "users":
        return {"users": AGENTS if p.get("kind") == "machine" else USERS}
    if path == "effective":
        uid = p.get("user")
        u = next((x for x in USERS + AGENTS if x["id"] == uid), None)
        if not u:
            raise door.DoorError(404, "unknown user")
        return {"user": uid, "username": u["username"],
                "kind": "machine" if u in AGENTS else "human",
                "apps": [{"app": "vereinsportal", "grants": [
                    {"object": "news", "activities": ["publish"], "fields": {"area": ["news"]}}]}],
                "assignments": [dict(a, collection_name="Paket Redaktion", roles=["Redaktion"])
                                for a in ASSIGN if a["subject"] == uid]}
    return {"declarations": {"declarations": DECLS}, "roles": {"roles": ROLES},
            "collections": {"collections": COLLS}, "assignments": {"assignments": ASSIGN},
            "mappings": {"mappings": MAPS}, "log": {"log": LOG}}[path]


door.call = fake_call
app.door.call = fake_call

srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
PORT = srv.server_address[1]


def req(method, path, who="u-admin", body=None, roles="user", ctype=None):
    h = {"X-OAAP-User-Id": who, "X-OAAP-User": who}
    if roles:
        h["X-OAAP-Roles"] = roles
    if ctype:
        h["Content-Type"] = ctype
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    data = r.read().decode("utf-8", "replace")
    c.close()
    return r.status, data


def rows_with_forms(html_text):
    """Tabellenzeilen, in denen ein Formular oder Knopf steht."""
    return [tr for tr in re.findall(r"<tr>.*?</tr>", html_text, re.S)
            if "<form" in tr or "<button" in tr]


print("Rahmen und Namen")
s, b = req("GET", "/")
ok("die Seite trägt Kit und Rahmen der Plattform",
   s == 200 and "/platform/theme.css" in b and "/platform/app.js" in b, b[:300])
ok("der Name ohne OAAP, im Kopf und im Titel",
   'class="oaap-appname">Rollen &amp; Rechte<' in b and "OAAP Rollen" not in b
   and "<title>Rollen &amp; Rechte</title>" in b)
ok("keine eigene Palette, kein eigener Kopf, keine Rollen-Kopfzeile",
   "#2f6fed" not in b and "oaap-header-back" not in b and "X-OAAP" not in b)
ok("Reiter sind Links: Personen zuerst, Protokoll zuletzt",
   b.index('aria-current="page">Personen<') < b.index('href="/log">Protokoll<')
   and 'aria-current="page"' in b and "<button" not in b.split("<main")[0])
ok("ein Anzeigename wird maskiert", "&lt;img src=x&gt;" in b and "<img src=x>" not in b)
pages = ["/", "/agents", "/roles", "/roles/new", "/role?id=r1", "/collections",
         "/collections/new", "/collection?id=c1", "/delegated", "/assignment?id=a1",
         "/person?id=u-001", "/assign?user=u-001&collection=c1", "/mappings",
         "/mappings/new", "/mapping?id=m1", "/log"]
folded = re.compile(r"\b(fuer|gehoer\w*|aender\w*|loesch\w*|gueltig\w*|fuenf|ueber|zurueck|"
                    r"Schluessel|buendel\w*)\b", re.I)
bad = {}
for p in pages:
    s, b = req("GET", p)
    text = re.sub(r"<[^>]+>", " ", b.split("<main", 1)[-1])
    hit = folded.findall(text)
    if s != 200 or hit or rows_with_forms(b):
        bad[p] = (s, hit[:3], len(rows_with_forms(b)))
ok("alle Seiten: 200, echte Umlaute, kein Formular in einer Tabellenzeile", not bad, bad)

print("Listen")
s, b = req("GET", "/")
ok("Personen: Zählzeile und Blättern ab 50",
   "1–50 von 121 Personen" in b and "offset=50" in b and "Weitere 50" in b, b[-1500:])
s, b = req("GET", "/?q=person011")
ok("Suche findet genau eine Person", "1 Personen" in b and "person011" in b
   and "person012" not in b, b[-1200:])
s, b = req("GET", "/?status=gesperrt")
ok("Filter gesperrt: nur gesperrte Konten (12)", "12 Personen" in b
   and "person010" in b and "person011" not in b)
s, b = req("GET", "/?sort=-name")
ok("Sortierung absteigend mit aria-sort", 'aria-sort="descending"' in b
   and b.index("person120") < b.index("person119"))
s, b = req("GET", "/roles")
ok("Rollen: ausgeblendete nicht ohne alle=1", ">Redaktion<" in b and ">Alt<" not in b
   and "alle=1" in b)
s, b = req("GET", "/roles?alle=1")
ok("... mit alle=1 als ausgeblendet", ">Alt<" in b and "ausgeblendet" in b)
s, b = req("GET", "/log?sort=wann")
ok("Protokoll: Sortierung nach Zeit, das Wort statt des Codes",
   b.index("2026-11-01") < b.index("2026-11-20") and ">zugeordnet<" in b)

print("Objektseiten statt Formulare in Zeilen")
s, b = req("GET", "/delegated")
ok("Weitergaben: Zeile öffnet die Zuordnung, Verwaistes markiert",
   "/assignment?id=a1" in b and "Geber hält es nicht mehr" in b and "fünf Minuten" in b)
s, b = req("GET", "/assignment?id=a1")
ok("Zuordnung: zurück, Beenden als Formular mit Rücksprung zur Person",
   'class="oaap-back"' in b and 'action="/do/revoke"' in b
   and 'name="back" value="/person?id=u-001"' in b)
s, b = req("GET", "/role?id=r1")
ok("Rolle: Ausblenden auf der Objektseite", 'action="/do/role-retire"' in b)
s, b = req("GET", "/collection?id=c1")
ok("Paket: Ausblenden auf der Objektseite", 'action="/do/collection-retire"' in b)
s, b = req("GET", "/mapping?id=m1")
ok("Abbildung: Entfernen auf der Objektseite", 'action="/do/map-remove"' in b)
s, b = req("GET", "/person?id=u-001")
ok("Person: Pakete zum Zuordnen als Links, kein Formular",
   "/assign?user=u-001&amp;collection=c1" in b and 'action="/do/assign"' not in b)
s, b = req("GET", "/assign?user=u-001&collection=c1")
ok("Zuordnen: eine Dialogseite mit dem Formular", 'action="/do/assign"' in b)
s, b = req("GET", "/assign?user=u-001&collection=c2")
ok("ein ausgeblendetes Paket wird nicht angeboten", 'action="/do/assign"' not in b)

print("Wer nicht Admin ist")
s, b = req("GET", "/", who="u-001")
ok("ein normaler Benutzer sieht Kein Zugriff und keine Namen",
   s == 403 and "Kein Zugriff" in b and "person002" not in b)


def mcp(msg, who="u-admin"):
    s, b = req("POST", "/mcp", who=who, body=json.dumps(msg).encode(), roles="user",
               ctype="application/json")
    return s, json.loads(b) if b else {}


def tool(name, args, who="u-admin"):
    s, d = mcp({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                "params": {"name": name, "arguments": args}}, who)
    res = d.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "")
    return res.get("isError"), text


print("MCP-Tür")
s, d = mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
names = sorted(t["name"] for t in d["result"]["tools"])
ok("tools/list: vier Lesewerkzeuge", s == 200 and names == [
    "rollen_rechte_assignments", "rollen_rechte_people", "rollen_rechte_person_read",
    "rollen_rechte_roles"], names)
ok("alle nur lesend", all(t["annotations"]["readOnlyHint"] for t in d["result"]["tools"]))
s, d = mcp({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"})
ok("drei Vorlagen", len(d["result"]["prompts"]) == 3)
err, text = tool("rollen_rechte_people", {"q": "person011"})
ok("rollen_rechte_people sucht", not err and json.loads(text)["people"][0]["name"] == "person011",
   text)
err, text = tool("rollen_rechte_person_read", {"person": "person001"})
doc = json.loads(text) if not err else {}
ok("rollen_rechte_person_read nach Name: darf und Zuordnung",
   doc.get("id") == "u-001" and doc["may"][0]["object"] == "news"
   and doc["assignments"][0]["orphaned"] is True, text)
err, text = tool("rollen_rechte_roles", {})
ok("rollen_rechte_roles: Rollen und Pakete",
   not err and {r["name"] for r in json.loads(text)["roles"]} == {"Redaktion", "Alt"})
err, text = tool("rollen_rechte_assignments", {"package": "Paket Redaktion"})
ok("rollen_rechte_assignments: gültige Zuordnungen mit Namen",
   not err and json.loads(text)["total"] == 2
   and {a["person"] for a in json.loads(text)["assignments"]} == {"person001", "person002"})
ok("kein Schlüssel im Ergebnis", "Bearer" not in text and "KEY" not in text)
CALLS.clear()
for name, args in (("rollen_rechte_people", {}), ("rollen_rechte_person_read", {"person": "chef"}),
                   ("rollen_rechte_roles", {}), ("rollen_rechte_assignments", {})):
    err, text = tool(name, args, who="u-001")
    ok(f"{name} als Nicht-Admin: abgelehnt wie die Seite, keine Namen",
       err is True and "Mandanten-Administrator" in text and "person" not in text, text)
ok("... und der Dienst wurde als diese Person gefragt",
   CALLS and all(c[2] == "u-001" for c in CALLS), CALLS[:3])
s, _b = req("GET", "/mcp")
ok("GET /mcp: 405 (nur POST), auch ohne Sitzung erreichbar", s == 405)

print("")
print("OK" if not fails else f"{fails} FEHLER")
srv.shutdown()
sys.exit(1 if fails else 0)
