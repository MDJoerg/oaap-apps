"""Vorgaenge 0.12: Anfragen eines Mandanten an den Betreiber (RFC-0109 §13).

    python3 apps/vorgaenge/test_request.py

Der Knoten schreibt die Anfrage durch seine eigene Tuer (RFC-0069), mit dem
Bezug `request` und dem Namen, fuer den er schreibt. Gehalten wird:

1. `open` mit object request legt EINEN Vorgang je Anfrage an; der
   Absender ist die Person ueber den Knoten, beglaubigt bleibt der Knoten.
2. `read` gibt Status und die Antworten der Bearbeitenden -- nie interne
   Kommentare, nie die eigenen Eintraege des Knotens; nur am eigenen Vorgang.
3. Ein fremdes object wird abgelehnt; der Neustart-Plan geht wie bisher.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="vorgaenge-req-")
os.environ["VORGAENGE_DATA_DIR"] = DATA

import app  # noqa: E402
import store  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


class FakeAuthz:
    url = "http://fake"

    def grants(self, uid):
        return []


ST = store.Store(os.path.join(DATA, "t.db"))
SRV = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
SRV.store, SRV.authz, SRV.kick = ST, FakeAuthz(), threading.Event()
threading.Thread(target=SRV.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{SRV.server_port}"


def call(method, path, headers=None, body=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener().open(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}


SRC = {"X-OAAP-Source-Instance": "vorgaenge", "X-OAAP-Source-App": "vorgaenge",
       "X-OAAP-Source-Version": "0.12.0", "X-OAAP-Source-Tenant": "t-0"}
NODE = {**SRC, "X-OAAP-User": "platform:vorgaenge", "X-OAAP-Roles": "user"}
OP_UID = "11111111-1111-4111-8111-111111111111"
REQ = {"op": "open", "object": "request", "ref": "q-20261120t120000-bec",
       "node": "oaapx01", "from": "bec · Jörg M.",
       "title": "Anfrage aus bec: SAP SSO einrichten",
       "body": "Rezept sap-sso 1.0.0 ist für bec nicht freigegeben.\n\nWir brauchen SAP SSO."}
D = "/platform/cases/node"

print("1. open")
st, b = call("POST", D, NODE, REQ)
ok("201 mit Nummer", st == 201 and b.get("number", "").startswith("V-"), (st, b))
num = b.get("number")
st, b2 = call("POST", D, NODE, REQ)
ok("dieselbe Anfrage noch einmal: derselbe Vorgang", st == 200 and b2.get("number") == num
   and b2.get("existing") is True, (st, b2))
c = ST.get(num)
ok("Bezug request, Absender die Person über den Knoten, Reporter der Knoten",
   c["object_kind"] == "request" and c["object_id"] == REQ["ref"]
   and c["reporter_name"] == "bec · Jörg M. (über den Knoten oaapx01)"
   and c["reporter"] == "platform:vorgaenge", c)
ok("beglaubigt: der Knoten, für wen er schreibt steht daneben",
   c["tech"]["attested"]["submitted_by"] == "platform:vorgaenge"
   and c["tech"]["attested"]["on_behalf"] == "bec · Jörg M.", c["tech"])
st, b = call("POST", D, NODE, dict(REQ, object="anything", ref="x"))
ok("ein fremdes object: 400", st == 400, (st, b))
st, b = call("POST", D, NODE, {"op": "open", "ref": "kernel:1", "node": "oaapx01",
                               "title": "Neustart", "body": "b"})
ok("ohne object: der Neustart-Plan wie bisher", st == 201
   and ST.get(b["number"])["object_kind"] == "reboot", (st, b))

print("2. read")
st, b = call("POST", D, NODE, {"op": "read", "number": num})
ok("frisch: Status neu, keine Antworten", st == 200 and b["status"] == "new"
   and b["status_label"] == "neu" and b["replies"] == [], (st, b))
c = ST.get(num)
ST.comment(c, OP_UID, "Betreiber Max", "Welches SAP-System ist es?")
ST.comment(ST.get(num), OP_UID, "Betreiber Max", "intern: erst Lizenz prüfen", internal=True)
call("POST", D, NODE, {"op": "note", "number": num, "node": "oaapx01", "from": "bec · Jörg M.",
                       "object": "request", "text": "Das System A4H."})
ST.set_status(ST.get(num), OP_UID, "Betreiber Max", "answered", "Freigegeben für bec.")
st, b = call("POST", D, NODE, {"op": "read", "number": num})
texts = [r["text"] for r in b.get("replies") or []]
ok("die Rückfrage und die Antwort mit Status stehen da", st == 200
   and "Welches SAP-System ist es?" in texts and "Freigegeben für bec." in texts
   and b["status"] == "answered", (st, b))
ok("kein interner Kommentar", not any("intern" in t for t in texts), texts)
ok("nicht die eigene Notiz des Knotens", "Das System A4H." not in texts, texts)
ok("die Statuszeile nennt den neuen Status",
   any(r.get("status") == "answered" and r.get("status_label") == "beantwortet"
       for r in b["replies"]), b)
c = ST.get(num)
ok("die Notiz des Knotens trägt den Namen der Person",
   any(e["actor_name"].startswith("bec · Jörg M.") and e["text"] == "Das System A4H."
       for e in c["entries"]), c["entries"])
made = ST.create(kind="internal", title="Von Max", body="", reporter=OP_UID,
                 reporter_name="Max")
st, b = call("POST", D, NODE, {"op": "read", "number": made["number"]})
ok("einen fremden Vorgang liest der Knoten nicht: 404", st == 404, (st, b))
st, b = call("POST", D, {**SRC, "X-OAAP-User": "max"}, {"op": "read", "number": num})
ok("ohne Knotenprinzipal: 403", st == 403, (st, b))

print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
