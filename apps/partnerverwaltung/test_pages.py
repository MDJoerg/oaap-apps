#!/usr/bin/env python3
"""Die Seiten und die MCP-Tür der Partnerverwaltung gegen einen Papier-
Zwilling (RFC-0118): Rahmen (Kit, Rahmenskript, Name ohne OAAP), Listen
aus GET /twin/objects?type= (alle Objekte, auch fremder Apps), Filter,
Sortierung, Blättern, Auswahl statt URN, MCP mit Rollenprüfung.

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
for i in range(60):
    OBJECTS[urn(f"f{i:02d}")] = {"id": urn(f"f{i:02d}"), "type": "Firma", "title": f"Firma {i:02d}",
                                 "owner": "app:partnerverwaltung",
                                 "groups": {"crm.core": {"origin": "app:partnerverwaltung",
                                                         "attributes": {"Location": {"value": "Hamburg" if i % 2 else "Köln"}},
                                                         "relations": [], "activities": []}}}
OBJECTS[urn("f00")]["title"] = "Müller GmbH"
OBJECTS[urn("f00")]["groups"]["raci.assignments"] = {
    "origin": "app:raci", "attributes": {},
    "relations": [{"key": "responsible", "target": urn("p1"), "valid_from": None, "valid_to": None}],
    "activities": []}
OBJECTS[urn("p1")] = {"id": urn("p1"), "type": "Kontaktperson", "title": "Anna Berg",
                      "owner": "app:partnerverwaltung",
                      "groups": {"crm.contact": {"origin": "app:partnerverwaltung",
                                                 "attributes": {"Email": {"value": "anna@example.com"}},
                                                 "relations": [{"key": "isContactOf", "target": urn("f00"),
                                                                "valid_from": "2019-01-01", "valid_to": None}],
                                                 "activities": [{"key": "PhoneCall", "status": "open"}]}}}
OBJECTS[urn("p2")] = {"id": urn("p2"), "type": "Kontaktperson", "title": "Bernd Kurz",
                      "owner": "app:partnerverwaltung", "groups": {"crm.contact": {
                          "origin": "app:partnerverwaltung", "attributes": {}, "relations": [], "activities": []}}}
OBJECTS[urn("pr1")] = {"id": urn("pr1"), "type": "Projekt", "title": "Website-Relaunch",
                       "owner": "app:projekt", "groups": {"project.core": {
                           "origin": "app:projekt", "attributes": {"Status": {"value": "laufend"}},
                           "relations": [{"key": "forCustomer", "target": urn("f00")}], "activities": []}}}
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
os.environ["PARTNERVERWALTUNG_DATA_DIR"] = tempfile.mkdtemp(prefix="oaap-pv-pages-")
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
ok("App-Name ohne OAAP im Kopf", '<span class="oaap-appname">Partnerverwaltung</span>' in html
   and "OAAP Partnerverwaltung" not in html)
ok("Hilfeprodukt am Kopf", 'data-oaap-product="partnerverwaltung"' in html)
ok("eine Reiterleiste mit Firmen zuerst", html.index("Firmen</a>") < html.index("Kontaktpersonen</a>")
   and 'class="oaap-tabs"' in html)
ok("keine rohen Rollen in der Seite", "X-OAAP" not in html and ">user<" not in html)
ok("keine eigene Palette", "#2f6fed" not in html and "--oaap-blue" not in html)
st, _ = get("/", headers={})
ok("ohne Sitzung 403", st == 403)

print("\n=== Listenbericht aus dem Zwilling ===")
ok("die Liste kommt aus /twin/objects?type=Firma, nicht aus dem Index",
   any(c.startswith("/twin/objects?type=Firma") for c in CALLS))
ok("keine Einzelabfrage je Zeile (kein N+1)",
   not any(c.startswith("/twin/objects/") for c in CALLS))
ok("Zählzeile und Blättern ab 50", "1–50 von 60 Firmen" in html and "Weitere 50" in html)
ok("Kennungen stehen nicht in der Liste", "urn:oaap:obj:" not in html.split("<table")[1].split("href=")[0])
st, html2 = get("/?offset=50")
ok("Seite 2 zeigt den Rest", "51–60 von 60" in html2)
st, html3 = get("/?q=Hamburg")
ok("Suche filtert auf dem Server", "30 Firmen" in html3, html3[html3.find("countline"):][:80])
st, html4 = get("/?sort=-name")
ok("Sortierung absteigend markiert", 'aria-sort="descending"' in html4)
ok("absteigend: Müller GmbH zuerst", html4.index("Müller GmbH") < html4.index("Firma 59"))
st, html5 = get("/kontaktpersonen?firma=" + urllib.parse.quote(urn("f00")))
ok("Kontaktpersonen nach Firma filtern", "Anna Berg" in html5 and "Bernd Kurz" not in html5)
ok("Firma als Titel in der Liste", "Müller GmbH" in html5)

print("\n=== Objektseite ===")
st, obj = get("/object?id=" + urllib.parse.quote(urn("f00")))
ok("Firma-Seite 200", st == 200, st)
ok("Kennung einmal im Kopf, kopierbar", obj.count(urn("f00")) >= 1 and "Kopieren" in obj and 'id="kennung"' in obj)
ok("Projekt der App „Projekte“ erscheint (geteilter Zwilling)", "Website-Relaunch" in obj and "angelegt von Projekte" in obj
   or ("Website-Relaunch" in obj and "Projekte" in obj))
ok("Ansprechpartner beim Namen", "Anna Berg" in obj)
ok("Zuständigkeiten aus RACI in Worten", "Zuständigkeiten" in obj and "Verantwortlich" in obj
   and "angelegt von Zuständigkeiten (RACI)" in obj)
ok("keine Gruppenschlüssel und keine Herkunft app: (sichtbar)", ">crm.core" not in obj and "crm.core<" not in obj and "Herkunft" not in obj
   and "app:" not in obj.replace('data-oaap', ''))
ok("Zurück-Link über dem Titel", 'class="oaap-back"' in obj)
st, person = get("/object?id=" + urllib.parse.quote(urn("p1")))
ok("Verknüpfen mit Auswahl statt URN-Feld", '<select name="customer_id"' in person
   and "urn:oaap:obj:..." not in person and ">Müller GmbH</option>" in person)
ok("Aktivität deutsch", "Anruf" in person and "offen" in person and "PhoneCall</" not in person)

print("\n=== Schreiben ===")
st, _b, h = post("/link", {"person_id": urn("p2"), "customer_id": urn("f01"), "valid_from": "2026-01-01"})
ok("Verknüpfen leitet weiter", st == 303, st)
ok("...und schreibt isContactOf in crm.contact", LAST["put"][0].endswith("/groups/crm.contact")
   and LAST["put"][1]["relations"][0]["target"] == urn("f01"))
st, _b, h = post("/create/firma", {"title": "Neue AG", "Location": "Bremen"})
ok("Firma anlegen leitet auf die Objektseite", st == 303 and "/object?id=" in h.get("Location", ""))
st, b, _h = post("/create/firma", {"title": ""})
ok("ohne Namen: Hinweis im Formular", st == 200 and "Ohne Namen" in b)

print("\n=== MCP ===")
r = mcp("tools/list")
names = [t["name"] for t in r["result"]["tools"]]
ok("tools/list nennt search und read", names == ["partnerverwaltung_search", "partnerverwaltung_read"], names)
r = mcp("tools/call", {"name": "partnerverwaltung_search", "arguments": {"q": "Anna"}})
res = json.loads(r["result"]["content"][0]["text"])
ok("search findet Anna mit Firma beim Namen", res["treffer"][0]["name"] == "Anna Berg"
   and res["treffer"][0]["firma"] == ["Müller GmbH"])
r = mcp("tools/call", {"name": "partnerverwaltung_read", "arguments": {"kennung": urn("f00")}})
res = json.loads(r["result"]["content"][0]["text"])
ok("read liefert Ansprechpartner und Projekte", res["ansprechpartner"][0]["name"] == "Anna Berg"
   and res["projekte"][0]["angelegt_von"] == "Projekte")
r = mcp("tools/call", {"name": "partnerverwaltung_read", "arguments": {"kennung": urn("pr1")}})
ok("read lehnt fremde Typen ab", r["result"]["isError"] is True)
r = mcp("tools/call", {"name": "partnerverwaltung_search", "arguments": {}},
        headers={"X-OAAP-User": "x"})
ok("ohne Rollen: abgelehnt wie die Seite", r["result"]["isError"] is True
   and "angemeldete Sitzung" in r["result"]["content"][0]["text"])
r = mcp("prompts/list")
ok("Vorlagen vorhanden", len(r["result"]["prompts"]) == 2)
st, _ = get("/mcp")
ok("GET /mcp → 405, nicht 403", st == 405, st)

srv.shutdown()
stub.shutdown()
print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
