#!/usr/bin/env python3
"""twin.py against a paper oaap.data.twin (RFC-0031 Schritt 3) -- same
stub pattern as Partnerverwaltung's own test (twin.py is the same small
copy, see its module comment). The real thing -- and the duplicate
Anna creates against Partnerverwaltung's own twin objects -- belongs
on `oaap-test`.

Run: python3 apps/mitarbeiterverwaltung/test_twin_client.py
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


LAST = {}


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
        self._reply(201, {"id": "urn:oaap:obj:33333333-3333-3333-3333-333333333333"})

    def do_GET(self):
        LAST["method"] = "GET"
        LAST["path"] = self.path
        self._reply(200, {"id": "urn:oaap:obj:33333333-3333-3333-3333-333333333333",
                          "type": "Mitarbeiter", "title": "Anna", "owner": "app:mitarbeiterverwaltung",
                          "groups": {"hr.core": {"origin": "app:mitarbeiterverwaltung",
                                                 "attributes": {"Email": {"value": "anna@example.com"}},
                                                 "relations": [], "activities": []}}})


server = HTTPServer(("127.0.0.1", 0), Stub)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["OAAP_TWIN_URL"] = f"http://127.0.0.1:{port}/twin"
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"
import twin  # noqa: E402

print("=== create_object: a Mitarbeiter, owned by THIS app, unrelated to Partnerverwaltung ===")
result = twin.create_object("Mitarbeiter", "Anna", "hr.core",
                            attributes={"Email": "anna@example.com", "Department": "Vertrieb"})
ok("POSTs to /twin/objects", LAST["path"] == "/twin/objects")
ok("body names the Mitarbeiter type and hr.core group",
   LAST["body"]["type"] == "Mitarbeiter" and LAST["body"]["group"]["key"] == "hr.core")
ok("returns the id the (stub) twin assigned",
   result["id"] == "urn:oaap:obj:33333333-3333-3333-3333-333333333333")

print("\n=== get_object: the created Mitarbeiter reads back with its own group ===")
obj = twin.get_object(result["id"])
ok("title and type round-trip", obj["title"] == "Anna" and obj["type"] == "Mitarbeiter")
ok("owner is THIS app, not Partnerverwaltung",
   obj["owner"] == "app:mitarbeiterverwaltung")
ok("hr.core carries the attributes this app wrote",
   obj["groups"]["hr.core"]["attributes"]["Email"]["value"] == "anna@example.com")

print("\n=== app.py's own index: id+type only, deduplicated, filtered by type ===")
DATA_DIR = tempfile.mkdtemp(prefix="oaap-mitarbeiterverwaltung-test-")
os.environ["MITARBEITERVERWALTUNG_DATA_DIR"] = DATA_DIR
import app as m  # noqa: E402

m.remember("urn:oaap:obj:m1", "Mitarbeiter")
m.remember("urn:oaap:obj:m1", "Mitarbeiter")  # doppelt -- muss harmlos sein
ok("Mitarbeiter index has exactly one entry despite the duplicate remember()",
   m.known("Mitarbeiter") == ["urn:oaap:obj:m1"])
ok("an unknown type has an empty index, not an error", m.known("Nonexistent") == [])

with open(m.INDEX_PATH, encoding="utf-8") as fh:
    saved = [json.loads(line) for line in fh]
ok("the index file stores id+type ONLY -- no title, no attribute",
   all(set(row.keys()) == {"id", "type"} for row in saved))

print("\n=== oaap-app.yaml: no consumes at all -- this app cannot read "
      "Partnerverwaltung's types even if its code wanted to ===")
manifest_src = open(os.path.join(HERE, "oaap-app.yaml"), encoding="utf-8").read()
ok("no 'consumes:' section -- the duplicate is structural, not just "
   "a missing feature", "consumes:" not in manifest_src)
ok("contributes only 'Mitarbeiter', as owner",
   "type: Mitarbeiter" in manifest_src and "role: owner" in manifest_src)

server.shutdown()

print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
