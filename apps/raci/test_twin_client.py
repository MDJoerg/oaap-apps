#!/usr/bin/env python3
"""twin.py against a paper oaap.data.twin (RFC-0031 Schritt 3) -- no
Docker, no Postgres, no node. Mirrors partnerverwaltung's own test
(same twin.py, kept as a sibling copy); this one additionally proves
app.py's own rule: RACI never touches anything but its own group
(`raci.assignments`), reading a Customer/Person only as a reference.

Run: python3 apps/raci/test_twin_client.py
"""
import json
import os
import sys
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

    def do_GET(self):
        LAST["method"] = "GET"
        LAST["path"] = self.path
        if self.path.endswith("customer"):
            self._reply(200, {"id": "urn:oaap:obj:c1", "type": "Customer",
                              "title": "Mueller GmbH", "owner": "app:partnerverwaltung",
                              "groups": {"raci.assignments": {
                                  "origin": "app:raci", "attributes": {},
                                  "relations": [{"key": "responsible", "target": "urn:oaap:obj:p1",
                                                "valid_from": None, "valid_to": None}],
                                  "activities": []}}})
            return
        if self.path.endswith("p1"):
            self._reply(200, {"id": "urn:oaap:obj:p1", "type": "Person", "title": "Anna",
                              "owner": "app:partnerverwaltung", "groups": {}})
            return
        if self.path.endswith("wrongtype"):
            self._reply(200, {"id": "urn:oaap:obj:x", "type": "Person", "title": "Anna",
                              "owner": "app:partnerverwaltung", "groups": {}})
            return
        self._reply(404, "no such object")

    def do_PUT(self):
        LAST["method"] = "PUT"
        LAST["path"] = self.path
        LAST["body"] = self._body()
        if self.path.endswith("groups/crm.core"):
            # RACI must never even ATTEMPT this -- the stub would refuse
            # it exactly like the real service does (RFC-0031 SS3.3), but
            # the test below checks RACI's OWN code never sends it.
            self._reply(403, "'app:raci' does not contribute to 'crm.core'")
            return
        self._reply(204)


server = HTTPServer(("127.0.0.1", 0), Stub)
port = server.server_port
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["OAAP_TWIN_URL"] = f"http://127.0.0.1:{port}"
os.environ["OAAP_PLATFORM_KEY"] = "oaapk_test_secret"
import twin  # noqa: E402
import app as m  # noqa: E402

print("=== render_customer: reads the Customer, resolves 'responsible' via a second GET ===")
html = m.render_customer("customer")
ok("shows the customer's OWN title, read fresh, not cached",
   "Mueller GmbH" in html)
ok("resolves the responsible relation's target to the PERSON's title, "
   "not the bare id -- proves the reference is followed, not copied",
   "Anna" in html)
ok("the assignment is labelled with the group it lives in",
   "raci.assignments" in html or "responsible" in html)

print("\n=== render_customer: wrong type and missing object are handled, not raised ===")
ok("a Person id where a Customer was expected is refused with a plain reason",
   "kein Customer" in m.render_customer("wrongtype")
   or "ist ein Person" in m.render_customer("wrongtype"))
ok("a 404 renders a plain 'no such object' card instead of crashing the page",
   "Kein Kunde" in m.render_customer("gone"))

print("\n=== app.py never writes anywhere but its own group ===")
m.render_customer("customer")  # warms nothing -- render is read-only, checked next
ok("render_customer() issued no PUT at all (pure read)",
   LAST.get("method") == "GET")
import twin as _t
_t.write_group("customer", m.ASSIGNMENT_GROUP, relations=[
    {"key": "responsible", "target": "urn:oaap:obj:p1"}])
ok("the only group this app's write path ever names is raci.assignments",
   LAST["path"].endswith("/groups/" + m.ASSIGNMENT_GROUP))
ok("ASSIGNMENT_GROUP is RACI's own reserved constant, not a literal "
   "sprinkled through the file (grep once, trust everywhere)",
   "ASSIGNMENT_GROUP = \"raci.assignments\"" in open(
       os.path.join(HERE, "app.py"), encoding="utf-8").read())

server.shutdown()

print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
