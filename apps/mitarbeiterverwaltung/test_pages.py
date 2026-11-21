#!/usr/bin/env python3
"""Die Seiten und die MCP-Tür der Mitarbeiterverwaltung gegen einen Papier-
Zwilling (RFC-0118): Rahmen, Liste aus GET /twin/objects?type=Mitarbeiter,
Filter, Sortierung, Blättern, MCP mit Rollenprüfung.

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
for i in range(55):
    OBJECTS[urn(f"m{i:02d}")] = {"id": urn(f"m{i:02d}"), "type": "Mitarbeiter", "title": f"Person {i:02d}",
                                 "owner": "app:mitarbeiterverwaltung",
                                 "groups": {"hr.core": {"origin": "app:mitarbeiterverwaltung",
                                                        "attributes": {"Department": {"value": "Vertrieb" if i % 5 == 0 else "Technik"},
                                                                       "Email": {"value": f"p{i}@example.com"}},
                                                        "relations": [], "activities": []}}}
OBJECTS[urn("m00")]["title"] = "Anna Öztürk"
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
os.environ["MITARBEITERVERWALTUNG_DATA_DIR"] = tempfile.mkdtemp(prefix="oaap-mv-pages-")
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
ok("App-Name ohne OAAP im Kopf", '<span class="oaap-appname">Mitarbeiterverwaltung</span>' in html
   and "OAAP Mitarbeiterverwaltung" not in html)
ok("Hilfeprodukt am Kopf", 'data-oaap-product="mitarbeiterverwaltung"' in html)
ok("eine Fläche, keine Reiterleiste", 'class="oaap-tabs"' not in html)
ok("keine rohen Rollen, keine eigene Palette", "X-OAAP" not in html and "#2f6fed" not in html)
st, _ = get("/", headers={})
ok("ohne Sitzung 403", st == 403)

print("\n=== Listenbericht aus dem Zwilling ===")
ok("Liste aus /twin/objects?type=Mitarbeiter", any(c.startswith("/twin/objects?type=Mitarbeiter") for c in CALLS))
ok("kein N+1", not any(c.startswith("/twin/objects/") for c in CALLS))
ok("Zählzeile und Blättern", "1–50 von 55 Mitarbeiter" in html and "Weitere 50" in html)
st, h2 = get("/?abteilung=Vertrieb")
ok("Filter Abteilung", "11 Mitarbeiter" in h2, h2[h2.find("countline"):][:60])
st, h3 = get("/?q=%C3%B6zt%C3%BCrk")
ok("Suche mit Umlaut", "Anna Öztürk" in h3 and "1 Mitarbeiter" in h3)
st, h4 = get("/?sort=-name")
ok("Sortierung absteigend", 'aria-sort="descending"' in h4 and h4.index("Person 54") < h4.index("Person 10"))

print("\n=== Objektseite und Anlegen ===")
st, obj = get("/object?id=" + urllib.parse.quote(urn("m00")))
ok("Objektseite 200 mit Kennung zum Kopieren", st == 200 and 'id="kennung"' in obj and "Kopieren" in obj)
ok("Angaben deutsch, ohne Gruppenschlüssel", "Abteilung" in obj and "Personaldaten" in obj
   and "hr.core" not in obj and "Herkunft" not in obj)
st, _b, h = post("/create", {"title": "Neu", "Department": "Einkauf"})
ok("Anlegen leitet auf die Objektseite", st == 303 and "/object?id=" in h.get("Location", ""))
ok("...mit hr.core", LAST["post"][1]["group"]["key"] == "hr.core")
st, b, _h = post("/create", {"title": " "})
ok("ohne Namen: Hinweis", st == 200 and "Ohne Namen" in b)

print("\n=== MCP ===")
r = mcp("tools/list")
names = [t["name"] for t in r["result"]["tools"]]
ok("tools/list", names == ["mitarbeiterverwaltung_search", "mitarbeiterverwaltung_read"], names)
r = mcp("tools/call", {"name": "mitarbeiterverwaltung_search", "arguments": {"abteilung": "vertrieb", "limit": 3}})
res = json.loads(r["result"]["content"][0]["text"])
ok("search filtert Abteilung und kürzt", res["anzahl"] == 11 and len(res["treffer"]) == 3 and res["gekuerzt"])
r = mcp("tools/call", {"name": "mitarbeiterverwaltung_read", "arguments": {"kennung": urn("m00")}})
res = json.loads(r["result"]["content"][0]["text"])
ok("read liefert Angaben deutsch", res["name"] == "Anna Öztürk" and res["angaben"]["Abteilung"] == "Vertrieb")
r = mcp("tools/call", {"name": "mitarbeiterverwaltung_read", "arguments": {"kennung": urn("m00")}},
        headers={"X-OAAP-User": "x"})
ok("ohne Rollen abgelehnt", r["result"]["isError"] is True)
ok("Vorlagen vorhanden", len(mcp("prompts/list")["result"]["prompts"]) == 2)
st, _ = get("/mcp")
ok("GET /mcp → 405", st == 405, st)

srv.shutdown()
stub.shutdown()
print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
