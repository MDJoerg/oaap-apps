#!/usr/bin/env python3
"""Die Seiten und die MCP-Tür von „Zuständigkeiten (RACI)“ gegen einen
Papier-Zwilling (RFC-0118): Rahmen statt eigener Palette, keine rohen
Rollen, Übersicht aller Firmen aus GET /twin/objects?type=Firma,
Kontaktperson als Auswahl statt URN, Filter, Sortierung, Blättern, MCP mit
Rollenprüfung.

Run: py -3 test_pages.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ok_n = fail_n = 0


def ok(label, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"PASS  {label}")
    else:
        fail_n += 1
        print(f"FAIL  {label} {detail}")


def urn(n):
    return f"urn:oaap:obj:{n}"


OBJECTS = {}
for i in range(53):
    groups = {"crm.core": {"origin": "app:partnerverwaltung", "attributes": {"VatId": {"value": "DE-GEHEIM"}},
                           "relations": [], "activities": []}}
    if i % 2 == 0:
        groups["raci.assignments"] = {"origin": "app:raci", "attributes": {}, "activities": [],
                                      "relations": [{"key": "responsible", "target": urn("p1" if i % 4 == 0 else "p2"),
                                                     "valid_from": None, "valid_to": None}]}
    OBJECTS[urn(f"f{i:02d}")] = {"id": urn(f"f{i:02d}"), "type": "Firma", "title": f"Firma {i:02d}",
                                 "owner": "app:partnerverwaltung", "groups": groups}
OBJECTS[urn("f00")]["title"] = "Müller GmbH"
OBJECTS[urn("p1")] = {"id": urn("p1"), "type": "Kontaktperson", "title": "Anna Berg", "owner": "app:partnerverwaltung",
                      "groups": {"crm.contact": {"origin": "app:partnerverwaltung",
                                                 "attributes": {"Email": {"value": "anna@example.com"}},
                                                 "relations": [], "activities": []}}}
OBJECTS[urn("p2")] = {"id": urn("p2"), "type": "Kontaktperson", "title": "Bernd Kurz", "owner": "app:partnerverwaltung",
                      "groups": {}}
OBJECTS[urn("p3")] = {"id": urn("p3"), "type": "Kontaktperson", "title": "Clara Wolf", "owner": "app:partnerverwaltung",
                      "groups": {}}
CALLS = []
LAST = {}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, status, doc=None):
        body = json.dumps(doc).encode("utf-8") if doc is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        CALLS.append(self.path)
        u = urllib.parse.urlparse(self.path)
        if u.path == "/twin/objects":
            t = urllib.parse.parse_qs(u.query).get("type", [""])[0]
            objs = sorted((o for o in OBJECTS.values() if o["type"] == t), key=lambda o: o["title"])
            return self._reply(200, {"objects": objs, "truncated": False})
        oid = urllib.parse.unquote(u.path.rsplit("/", 1)[1])
        if oid in OBJECTS:
            return self._reply(200, OBJECTS[oid])
        self._reply(404, "no such object")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        LAST["post"] = (self.path, json.loads(self.rfile.read(n) or b"{}"))
        self._reply(201, {"id": urn("neu1")})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        LAST["put"] = (self.path, json.loads(self.rfile.read(n) or b"{}"))
        self._reply(204)


stub = HTTPServer(("127.0.0.1", 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
os.environ["OAAP_TWIN_URL"] = f"http://127.0.0.1:{stub.server_port}/twin"
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"

import app as m  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), m.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
USER = {"X-OAAP-User": "anna", "X-OAAP-Roles": "user"}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def get(path, headers=USER):
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with OPENER.open(req) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def post(path, data, headers=USER, ctype="application/x-www-form-urlencoded"):
    body = data if isinstance(data, bytes) else urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST",
                                 headers={**headers, "Content-Type": ctype})
    try:
        with OPENER.open(req) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def mcp(method, params=None, headers=USER):
    st, body, _h = post("/mcp", json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                            "params": params or {}}).encode(), headers, "application/json")
    return json.loads(body)


print("=== Rahmen ===")
st, html = get("/")
ok("Übersicht antwortet 200", st == 200, st)
ok("lädt das Kit /platform/theme.css", "/platform/theme.css" in html)
ok("lädt das Rahmenskript /platform/app.js", "/platform/app.js" in html)
ok("App-Name „Zuständigkeiten (RACI)“ ohne OAAP im Kopf",
   '<span class="oaap-appname">Zuständigkeiten (RACI)</span>' in html and "OAAP RACI" not in html)
ok("Hilfeprodukt am Kopf", 'data-oaap-product="raci"' in html)
ok("keine eigene Palette mehr", "--oaap-blue" not in html and "#1e3a8a" not in html and "userbox" not in html)
st, html_u = get("/", headers={"X-OAAP-User": "anna.geheim", "X-OAAP-Roles": "user,keyuser"})
ok("weder Benutzer noch Rollen roh in der Seite", "anna.geheim" not in html_u and "keyuser" not in html_u)
ok("kein relativer Link auf ../partnerverwaltung/", "../partnerverwaltung" not in html)
st, _ = get("/", headers={})
ok("ohne Sitzung 403", st == 403)

print("\n=== Übersicht aus dem Zwilling ===")
ok("Firmen aus /twin/objects?type=Firma, Personen aus ?type=Kontaktperson, kein N+1",
   any(c.startswith("/twin/objects?type=Firma") for c in CALLS)
   and any(c.startswith("/twin/objects?type=Kontaktperson") for c in CALLS)
   and not any(c.startswith("/twin/objects/") for c in CALLS))
ok("Zählzeile und Blättern", "1–50 von 53 Firmen" in html and "Weitere 50" in html)
ok("Verantwortliche beim Namen", "Anna Berg" in html and "Bernd Kurz" in html)
ok("Lücken als Badge", 'oaap-badge warn">niemand zugewiesen' in html)
ok("keine Stammdaten der Partnerverwaltung (nur Referenz)", "DE-GEHEIM" not in html)
st, h2 = get("/?offen=ohne")
ok("Filter: niemand zugewiesen", "26 Firmen" in h2, h2[h2.find("countline"):][:60])
st, h3 = get("/?person=" + urllib.parse.quote(urn("p1")))
ok("Filter: Verantwortlich", "14 Firmen" in h3, h3[h3.find("countline"):][:60])
st, h4 = get("/?sort=-anzahl")
ok("Sortierung nach Anzahl", 'aria-sort="descending"' in h4)

print("\n=== Seite einer Firma ===")
st, f = get("/?kunde=" + urllib.parse.quote(urn("f00")))
ok("Firmen-Seite 200", st == 200, st)
ok("Verantwortlich mit E-Mail", "Anna Berg" in f and "anna@example.com" in f)
ok("Kontaktperson als Auswahl statt URN-Feld", '<select name="person_id"' in f and "urn:oaap:obj:..." not in f
   and "Mitarbeiter-ID" not in f and "Kontaktperson" in f)
ok("schon Zugewiesene stehen nicht zur Auswahl", '>Anna Berg (anna@example.com)</option>' not in f
   and ">Clara Wolf</option>" in f)
ok("Kennung einmal, kopierbar", f.count(urn("f00") + "<") == 1 and "Kopieren" in f)
ok("Zurück zur Übersicht", 'class="oaap-back"' in f)
ok("keine Stammdaten der Partnerverwaltung", "DE-GEHEIM" not in f)
st, _b, h = post("/assign", {"customer_id": urn("f01"), "person_id": urn("p3")})
ok("Zuweisen leitet zurück auf die Firma", st == 303 and "kunde=" in h.get("Location", ""))
ok("...und schreibt nur raci.assignments", LAST["put"][0].endswith("/groups/raci.assignments")
   and LAST["put"][1]["relations"][0]["target"] == urn("p3"))
st, b, _h = post("/assign", {"customer_id": urn("f01"), "person_id": ""})
ok("ohne Person: Hinweis auf der Firmen-Seite", st == 200 and "Bitte eine Kontaktperson wählen" in b)

print("\n=== MCP ===")
r = mcp("tools/list")
names = [t["name"] for t in r["result"]["tools"]]
ok("tools/list", names == ["raci_search", "raci_read"], names)
r = mcp("tools/call", {"name": "raci_search", "arguments": {"ohne_verantwortliche": True, "limit": 50}})
res = json.loads(r["result"]["content"][0]["text"])
ok("search: Firmen ohne Verantwortliche", res["anzahl"] == 26 and not res["treffer"][0]["verantwortlich"])
r = mcp("tools/call", {"name": "raci_search", "arguments": {"q": "Anna"}})
res = json.loads(r["result"]["content"][0]["text"])
ok("search nach Person", res["anzahl"] == 14)
r = mcp("tools/call", {"name": "raci_read", "arguments": {"kennung": urn("f00")}})
res = json.loads(r["result"]["content"][0]["text"])
ok("read mit Name und E-Mail, ohne Stammdaten", res["verantwortlich"][0]["name"] == "Anna Berg"
   and res["verantwortlich"][0]["email"] == "anna@example.com" and "DE-GEHEIM" not in json.dumps(res))
r = mcp("tools/call", {"name": "raci_read", "arguments": {"kennung": urn("p1")}})
ok("read lehnt eine Person ab", r["result"]["isError"] is True)
r = mcp("tools/call", {"name": "raci_read", "arguments": {"kennung": urn("f00")}}, headers={"X-OAAP-User": "x"})
ok("ohne Rollen abgelehnt", r["result"]["isError"] is True)
ok("Vorlagen vorhanden", len(mcp("prompts/list")["result"]["prompts"]) == 2)
st, _ = get("/mcp")
ok("GET /mcp → 405", st == 405, st)

srv.shutdown()
stub.shutdown()
print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
