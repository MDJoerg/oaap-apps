#!/usr/bin/env python3
"""twin.py against a paper oaap.data.twin (RFC-0031 Schritt 3) -- no
Docker, no Postgres, no node. A local stub HTTP server plays the twin
service's own response shapes (services/twin/app.py) closely enough to
prove this client builds the right request and reads the right
response; the real thing (Owner creates, Person links, RACI reads and
writes) belongs on `oaap-test`, like every other oaap.data.* capability
this program has built.

Run: python3 apps/partnerverwaltung/test_twin_client.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

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


# --------------------------------------------------------- Papier-Zwilling

LAST = {}  # the stub's memory of the last request, for assertions


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw) if raw else {}

    def _reply(self, status, doc=None):
        body = json.dumps(doc).encode("utf-8") if doc is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        LAST["method"] = "POST"
        LAST["path"] = self.path
        LAST["auth"] = self.headers.get("Authorization")
        LAST["body"] = self._body()
        if self.path == "/twin/objects":
            self._reply(201, {"id": "urn:oaap:obj:11111111-1111-1111-1111-111111111111"})
            return
        self._reply(404)

    def do_GET(self):
        LAST["method"] = "GET"
        LAST["path"] = self.path
        LAST["auth"] = self.headers.get("Authorization")
        if self.path.endswith("nope"):
            self._reply(404, "no such object")
            return
        if self.path.endswith("verboten"):
            self._reply(403, "'x' neither contributes nor consumes 'Customer'")
            return
        self._reply(200, {"id": "urn:oaap:obj:22222222-2222-2222-2222-222222222222",
                          "type": "Customer", "title": "Mueller GmbH", "owner": "app:partnerverwaltung",
                          "groups": {"crm.core": {"origin": "app:partnerverwaltung",
                                                  "attributes": {}, "relations": [], "activities": []}}})

    def do_PUT(self):
        LAST["method"] = "PUT"
        LAST["path"] = self.path
        LAST["auth"] = self.headers.get("Authorization")
        LAST["body"] = self._body()
        if self.path.endswith("besetzt"):
            self._reply(403, "group 'crm.core' belongs to 'app:other' -- 'app:me' may not write it")
            return
        self._reply(204)


server = HTTPServer(("127.0.0.1", 0), Stub)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["OAAP_TWIN_URL"] = f"http://127.0.0.1:{port}/twin"  # prod shape, see twin.py
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"
import twin  # noqa: E402  -- reads the env vars above at import time

print("=== configured(): both env vars present ===")
ok("configured() is true with both set", twin.configured())

print("\n=== create_object: request shape ===")
result = twin.create_object("Customer", "Mueller GmbH", "crm.core",
                            attributes={"VatId": "DE123"}, attr_valid_from="2019-01-01")
ok("POSTs to /twin/objects", LAST["path"] == "/twin/objects")
ok("carries the bearer key, not the raw secret string alone",
   LAST["auth"] == "Bearer oaapk_test_secret")
ok("body carries type/title/group.key", LAST["body"]["type"] == "Customer"
   and LAST["body"]["title"] == "Mueller GmbH" and LAST["body"]["group"]["key"] == "crm.core")
ok("attribute valid_from rides on the GROUP payload, not per-attribute "
   "(matches services/twin/app.py's _write_group_content attribute loop)",
   LAST["body"]["group"]["valid_from"] == "2019-01-01")
ok("returns the parsed {'id': ...} the service sent back",
   result["id"] == "urn:oaap:obj:11111111-1111-1111-1111-111111111111")

print("\n=== create_object: relations carry their OWN valid_from, not the group's ===")
twin.create_object("Person", "Anna", "crm.contact",
                   relations=[{"key": "isContactOf", "target": "urn:oaap:obj:2",
                               "valid_from": "2019-01-01"}])
ok("the relation entry itself carries valid_from",
   LAST["body"]["group"]["relations"][0]["valid_from"] == "2019-01-01")
ok("no group-level valid_from leaks in when only relations were given",
   "valid_from" not in LAST["body"]["group"])

print("\n=== get_object: 200, 404 (None, not raised), and another status (raised) ===")
obj = twin.get_object("urn:oaap:obj:22222222-2222-2222-2222-222222222222")
ok("GET returns the parsed object header+groups",
   obj["title"] == "Mueller GmbH" and "crm.core" in obj["groups"])
ok("a 404 becomes None -- a stale/mistyped id pasted into a form is routine",
   twin.get_object("nope") is None)
try:
    twin.get_object("verboten")
    ok("a 403 is raised, not swallowed like a 404", False)
except twin.TwinError as exc:
    ok("a 403 is raised, not swallowed like a 404", exc.status == 403)
    ok("...and carries the service's own reason",
       "neither contributes nor consumes" in exc.detail)

print("\n=== write_group: PUT, and a 'nobody else writes there' refusal surfaces ===")
twin.write_group("urn:oaap:obj:2", "raci.assignments",
                 relations=[{"key": "responsible", "target": "urn:oaap:obj:3"}])
ok("PUTs to the group path", LAST["path"] == "/twin/objects/urn:oaap:obj:2/groups/raci.assignments")
try:
    twin.write_group("urn:oaap:obj:2", "besetzt", attributes={"x": 1})
    ok("a foreign-origin group write is raised as TwinError(403)", False)
except twin.TwinError as exc:
    ok("a foreign-origin group write is raised as TwinError(403)", exc.status == 403)

print("\n=== configured(): missing key or url ===")
os.environ["OAAP_PLATFORM_KEY"] = ""
import importlib
importlib.reload(twin)
ok("configured() is false without a key", not twin.configured())
try:
    twin.get_object("anything")
    ok("an unconfigured client raises TwinError(0, ...) instead of guessing", False)
except twin.TwinError as exc:
    ok("an unconfigured client raises TwinError(0, ...) instead of guessing", exc.status == 0)
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"
importlib.reload(twin)

# ------------------------------------------------------- der eigene Index

print("\n=== app.py's own index: id+type only, deduplicated, filtered by type ===")
DATA_DIR = tempfile.mkdtemp(prefix="oaap-partnerverwaltung-test-")
os.environ["PARTNERVERWALTUNG_DATA_DIR"] = DATA_DIR
import app as m  # noqa: E402

m.remember("urn:oaap:obj:c1", "Customer")
m.remember("urn:oaap:obj:c1", "Customer")  # doppelt -- muss harmlos sein
m.remember("urn:oaap:obj:p1", "Person")
ok("Customer index has exactly one entry despite the duplicate remember()",
   m.known("Customer") == ["urn:oaap:obj:c1"])
ok("Person index is separate from Customer's",
   m.known("Person") == ["urn:oaap:obj:p1"])
ok("an unknown type has an empty index, not an error", m.known("Nonexistent") == [])

with open(m.INDEX_PATH, encoding="utf-8") as fh:
    saved = [json.loads(line) for line in fh]
ok("the index file stores id+type ONLY -- no title, no attribute "
   "(the twin stays the only source, per this app's own module comment)",
   all(set(row.keys()) == {"id", "type"} for row in saved))

print("\n=== oaap-app.yaml: a second, independent owner of Projekt (zweite Welle) ===")
manifest_src = open(os.path.join(HERE, "oaap-app.yaml"), encoding="utf-8").read()
ok("contributes to Projekt as owner, WITHOUT defining it "
   "(no 'object_types' entry for Projekt -- that stays the "
   "Projekt-App's own, RFC-0031 D1)",
   "type: Projekt\n    role: owner" in manifest_src
   and "key: Projekt" not in manifest_src.split("data_model:")[1].split("contributes:")[0])

server.shutdown()

print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
