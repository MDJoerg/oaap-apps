#!/usr/bin/env python3
"""twin.py against a paper oaap.data.twin -- same stub pattern as
Partnerverwaltung's own test. Real thing (Projekt-App AND
Partnerverwaltung both creating Projekt instances on the same twin)
belongs on `oaap-test`.

Run: python3 apps/projekt/test_twin_client.py
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
        LAST["path"] = self.path
        LAST["body"] = self._body()
        self._reply(201, {"id": "urn:oaap:obj:44444444-4444-4444-4444-444444444444"})

    def do_GET(self):
        LAST["path"] = self.path
        if self.path.endswith("kunde"):
            self._reply(200, {"id": "urn:oaap:obj:kunde", "type": "Firma",
                              "title": "Mueller GmbH", "owner": "app:partnerverwaltung",
                              "groups": {}})
            return
        self._reply(200, {"id": "urn:oaap:obj:44444444-4444-4444-4444-444444444444",
                          "type": "Projekt", "title": "Website-Relaunch",
                          "owner": "app:projekt",
                          "groups": {"project.core": {
                              "origin": "app:projekt",
                              "attributes": {"Status": {"value": "geplant"}},
                              "relations": [{"key": "forCustomer", "target": "urn:oaap:obj:kunde"}],
                              "activities": []}}})


server = HTTPServer(("127.0.0.1", 0), Stub)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["OAAP_TWIN_URL"] = f"http://127.0.0.1:{port}/twin"
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"
import twin  # noqa: E402

print("=== create_object: a Projekt, linked to a Firma by relation ===")
result = twin.create_object("Projekt", "Website-Relaunch", "project.core",
                            attributes={"Status": "geplant"},
                            relations=[{"key": "forCustomer", "target": "urn:oaap:obj:kunde"}])
ok("POSTs to /twin/objects", LAST["path"] == "/twin/objects")
ok("body names the Projekt type and project.core group",
   LAST["body"]["type"] == "Projekt" and LAST["body"]["group"]["key"] == "project.core")
ok("the forCustomer relation rides along in the same call",
   LAST["body"]["group"]["relations"][0]["target"] == "urn:oaap:obj:kunde")
ok("returns the id the (stub) twin assigned",
   result["id"] == "urn:oaap:obj:44444444-4444-4444-4444-444444444444")

print("\n=== get_object: reads its own Projekt AND the referenced Firma ===")
projekt = twin.get_object(result["id"])
ok("title/type/owner round-trip", projekt["title"] == "Website-Relaunch"
   and projekt["type"] == "Projekt" and projekt["owner"] == "app:projekt")
kunde = twin.get_object("urn:oaap:obj:kunde")
ok("the referenced Firma resolves to Partnerverwaltung's own object, "
   "read-only, never Projekt-App's",
   kunde["title"] == "Mueller GmbH" and kunde["owner"] == "app:partnerverwaltung")

print("\n=== app.py's own index: Projekt ids only ===")
DATA_DIR = tempfile.mkdtemp(prefix="oaap-projekt-test-")
os.environ["PROJEKT_DATA_DIR"] = DATA_DIR
import app as m  # noqa: E402

m.remember("urn:oaap:obj:pr1")
m.remember("urn:oaap:obj:pr1")  # doppelt -- muss harmlos sein
ok("exactly one entry despite the duplicate remember()",
   m.known() == ["urn:oaap:obj:pr1"])
with open(m.INDEX_PATH, encoding="utf-8") as fh:
    saved = [json.loads(line) for line in fh]
ok("the index stores id+type ONLY -- no title, no attribute",
   all(set(row.keys()) == {"id", "type"} for row in saved))

print("\n=== oaap-app.yaml: defines Projekt, consumes Firma as a reference only ===")
manifest_src = open(os.path.join(HERE, "oaap-app.yaml"), encoding="utf-8").read()
ok("defines the Projekt object type (this app is its origin)",
   "key: Projekt" in manifest_src and "object_types:" in manifest_src)
ok("consumes Firma with fields limited to 'reference' (D6)",
   "type: Firma" in manifest_src and "fields: [reference]" in manifest_src)
ok("contributes Projekt as owner, not contributor",
   "type: Projekt\n    role: owner" in manifest_src)

server.shutdown()

print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
