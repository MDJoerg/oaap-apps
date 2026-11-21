#!/usr/bin/env python3
"""Die Seiten und die MCP-Tür der App „Projekte“ gegen einen Papier-Zwilling
(RFC-0118): Rahmen, Liste ALLER Projekte aus GET /twin/objects?type=Projekt
(auch die der Partnerverwaltung), Kunde als Auswahl statt URN, Filter,
Sortierung, Blättern, MCP mit Rollenprüfung.

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
OBJECTS[urn("f1")] = {"id": urn("f1"), "type": "Firma", "title": "Müller GmbH", "owner": "app:partnerverwaltung",
                      "groups": {}}
OBJECTS[urn("f2")] = {"id": urn("f2"), "type": "Firma", "title": "Schulz KG", "owner": "app:partnerverwaltung",
                      "groups": {}}
for i in range(52):
    owner = "app:partnerverwaltung" if i % 4 == 0 else "app:projekt"
    OBJECTS[urn(f"pr{i:02d}")] = {
        "id": urn(f"pr{i:02d}"), "type": "Projekt", "title": f"Projekt {i:02d}", "owner": owner,
        "groups": {"project.core": {"origin": owner,
                                    "attributes": {"Status": {"value": "laufend" if i % 2 else "geplant"}},
                                    "relations": [{"key": "forCustomer", "target": urn("f1" if i % 3 else "f2")}],
                                    "activities": []}}}
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
os.environ["PROJEKT_DATA_DIR"] = tempfile.mkdtemp(prefix="oaap-test-pages-")
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
ok("Liste antwortet 200", st == 200, st)
ok("lädt das Kit /platform/theme.css", "/platform/theme.css" in html)
ok("lädt das Rahmenskript /platform/app.js", "/platform/app.js" in html)
ok("App-Name „Projekte“ ohne OAAP im Kopf", '<span class="oaap-appname">Projekte</span>' in html
   and "OAAP Projekt" not in html and "Projekt-App" not in html)
ok("Hilfeprodukt am Kopf", 'data-oaap-product="projekt"' in html)
ok("keine rohen Rollen, keine eigene Palette", "X-OAAP" not in html and "#2f6fed" not in html)
ok("kein relativer Link auf ../partnerverwaltung/", "../partnerverwaltung" not in html)
st, _ = get("/", headers={})
ok("ohne Sitzung 403", st == 403)

print("\n=== Listenbericht aus dem Zwilling ===")
ok("Liste aus /twin/objects?type=Projekt", any(c.startswith("/twin/objects?type=Projekt") for c in CALLS))
ok("Kundennamen aus /twin/objects?type=Firma, kein N+1",
   any(c.startswith("/twin/objects?type=Firma") for c in CALLS)
   and not any(c.startswith("/twin/objects/") for c in CALLS))
ok("alle 52 Projekte, auch die der Partnerverwaltung", "1–50 von 52 Projekte" in html
   and "Partnerverwaltung" in html)
ok("Kunde beim Namen, keine Kennung in der Liste", "Müller GmbH" in html and "urn:oaap:obj:f1<" not in html)
ok("Status als Badge", 'class="oaap-badge info">laufend' in html)
st, h2 = get("/?von=" + urllib.parse.quote("app:partnerverwaltung"))
ok("Filter „Angelegt von“", "13 Projekte" in h2, h2[h2.find("countline"):][:60])
st, h3 = get("/?kunde=" + urllib.parse.quote(urn("f2")) + "&status=geplant")
ok("Filter Kunde + Status", "9 Projekte" in h3, h3[h3.find("countline"):][:60])
st, h4 = get("/?sort=-titel&offset=50")
ok("Sortierung + Seite 2", 'aria-sort="descending"' in h4 and "51–52 von 52" in h4 and "Projekt 00" in h4)

print("\n=== Neu und Objektseite ===")
st, neu = get("/neu")
ok("Kunde als Auswahl statt URN-Feld", '<select name="kunde_id">' in neu and ">Müller GmbH</option>" in neu
   and "urn:oaap:obj:..." not in neu and "Kunde (Firma)" in neu)
st, _b, h = post("/create", {"title": "Neu", "kunde_id": urn("f1"), "Status": "geplant"})
ok("Anlegen leitet weiter", st == 303 and "/object?id=" in h.get("Location", ""))
ok("...mit forCustomer auf die gewählte Firma", LAST["post"][1]["group"]["relations"][0]["target"] == urn("f1"))
st, b, _h = post("/create", {"title": ""})
ok("ohne Titel: Hinweis, Auswahl bleibt", st == 200 and "Ohne Titel" in b and "<select" in b)
st, obj = get("/object?id=" + urllib.parse.quote(urn("pr00")))
ok("Objektseite: Kunde beim Namen, angelegt von", st == 200 and "Schulz KG" in obj and "Partnerverwaltung" in obj)
ok("Kennung einmal, kopierbar", obj.count(urn("pr00") + "<") == 1 and "Kopieren" in obj)
ok("keine Gruppenschlüssel, kein forCustomer sichtbar", ">project.core" not in obj and "forCustomer" not in obj
   and "Herkunft" not in obj)

print("\n=== MCP ===")
r = mcp("tools/list")
names = [t["name"] for t in r["result"]["tools"]]
ok("tools/list", names == ["projekt_search", "projekt_read"], names)
r = mcp("tools/call", {"name": "projekt_search", "arguments": {"q": "Schulz", "status": "geplant", "limit": 50}})
res = json.loads(r["result"]["content"][0]["text"])
ok("search nach Kunde und Status", res["anzahl"] == 9 and all(t["kunde"] == "Schulz KG" for t in res["treffer"]))
r = mcp("tools/call", {"name": "projekt_read", "arguments": {"kennung": urn("pr01")}})
res = json.loads(r["result"]["content"][0]["text"])
ok("read mit Kunde beim Namen", res["kunde"][0]["name"] == "Müller GmbH" and res["angelegt_von"] == "Projekte")
r = mcp("tools/call", {"name": "projekt_read", "arguments": {"kennung": urn("f1")}})
ok("read lehnt eine Firma ab", r["result"]["isError"] is True)
r = mcp("tools/call", {"name": "projekt_search", "arguments": {}}, headers={"X-OAAP-User": "x"})
ok("ohne Rollen abgelehnt", r["result"]["isError"] is True)
ok("Vorlagen vorhanden", len(mcp("prompts/list")["result"]["prompts"]) == 2)
st, _ = get("/mcp")
ok("GET /mcp → 405", st == 405, st)

srv.shutdown()
stub.shutdown()
print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
