"""Vorgaenge 0.3: die Tuer fuer den Knoten (RFC-0069 Stufe 2).

    python3 apps/vorgaenge/test_node.py

Ein Server, ein Mandant. Was das Gateway sonst tut -- den Schluessel pruefen,
den Prinzipal nennen, die Herkunft setzen -- steht hier als Kopfzeile, so
wie es beim Empfaenger ankommt. Gehalten wird:

1. Nur `platform:<diese Instanz>` mit Herkunft kommt durch: keine Sitzung,
   kein Backend-Schluessel, kein fremder Knotenprinzipal.
2. `open` legt EINEN Vorgang je Bezug an; dieselbe `ref` noch einmal gibt
   den vorhandenen zurueck (ein Wiederholungsversuch verdoppelt nichts).
3. `note` schreibt fort, `close` schliesst aus `new` -- ohne NEXT -- und
   laesst einen beendeten Vorgang in Ruhe; beides nur am eigenen Vorgang.
4. Die Admins des Mandanten sehen den Vorgang im Eingang, mit dem Knoten
   als Absender.
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
DATA = tempfile.mkdtemp(prefix="vorgaenge-node-")
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


def call(method, path, headers=None, body=None, ctype="application/json"):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode() if ctype == "application/json" else body.encode()
        h["Content-Type"] = ctype
    req = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener().open(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


# Die Herkunft, wie das Gateway sie auf der Site DIESER Instanz setzt
SRC = {"X-OAAP-Source-Instance": "vorgaenge", "X-OAAP-Source-App": "vorgaenge",
       "X-OAAP-Source-Version": "0.3.0", "X-OAAP-Source-Tenant": "t-1"}
NODE = {**SRC, "X-OAAP-User": "platform:vorgaenge", "X-OAAP-Roles": "user"}
ADMIN = {"X-OAAP-User-Id": "11111111-1111-4111-8111-111111111111", "X-OAAP-Roles": "admin",
         "X-OAAP-User": "max", "X-OAAP-Display-Name": "Max Muster"}
OPEN = {"op": "open", "ref": "kernel:6.12.115:2026-11-06T10:00:00Z", "node": "oaap-test",
        "title": "Neustart des Knotens oaap-test: 2026-11-08 04:15 CET (Pflege)",
        "body": "Der Knoten oaap-test startet am 2026-11-08 04:15 CET neu."}

print("")
print("1. Wer durch darf")
st, b = call("POST", "/platform/cases/node", SRC, OPEN)
ok("Herkunft ohne Prinzipal: 403", st == 403, (st, b))
st, b = call("POST", "/platform/cases/node", {**SRC, "X-OAAP-User": "instance:vorgaenge"}, OPEN)
ok("der Backend-Schluessel der Instanz (instance:) kommt nicht durch: 403", st == 403, (st, b))
st, b = call("POST", "/platform/cases/node", {**SRC, "X-OAAP-User": "platform:andere"}, OPEN)
ok("ein Knotenprinzipal einer ANDEREN Instanz: 403", st == 403, (st, b))
st, b = call("POST", "/platform/cases/node", {**ADMIN, "X-OAAP-User": "platform:vorgaenge"}, OPEN)
ok("ohne Herkunft (nicht ueber die Site gekommen): 403, auch mit passendem Namen", st == 403, (st, b))
st, b = call("POST", "/platform/cases/node", {**SRC, **ADMIN}, OPEN)
ok("eine Admin-Sitzung: 403", st == 403, (st, b))
st, b = call("POST", "/platform/cases/node", NODE, "op=open", "application/x-www-form-urlencoded")
ok("kein JSON: 415", st == 415, (st, b))

print("")
print("2. open: ein Vorgang je Bezug")
st, b = call("POST", "/platform/cases/node", NODE, dict(OPEN, ref=""))
ok("ohne ref: 400", st == 400 and "ref" in b, (st, b))
st, b = call("POST", "/platform/cases/node", NODE, dict(OPEN, title=""))
ok("ohne Betreff: 400", st == 400, (st, b))
st, b = call("POST", "/platform/cases/node", NODE, OPEN)
ok("angelegt: 201 mit Nummer", st == 201 and json.loads(b)["number"].startswith("V-"), (st, b))
num = json.loads(b)["number"]
st, b = call("POST", "/platform/cases/node", NODE, OPEN)
ok("dieselbe ref noch einmal: 200, derselbe Vorgang, existing",
   st == 200 and json.loads(b)["number"] == num and json.loads(b).get("existing") is True, (st, b))
c = ST.get(num)
ok("Art intern, Bereich eingang, Status neu, Absender der Knoten",
   c["kind"] == "internal" and c["queue"] == "eingang" and c["status"] == "new"
   and c["reporter"] == "platform:vorgaenge" and c["reporter_name"] == "Knoten oaap-test", c)
ok("der Bezug ist der Plan", c["object_kind"] == "reboot" and c["object_id"] == OPEN["ref"], c)
ok("beglaubigt: Herkunft dieser Site, submitted_by der Knotenprinzipal, der Knoten",
   c["tech"]["attested"]["instance"] == "vorgaenge"
   and c["tech"]["attested"]["submitted_by"] == "platform:vorgaenge"
   and c["tech"]["attested"]["node"] == "oaap-test", c["tech"])
st, b = call("POST", "/platform/cases/node", NODE, dict(OPEN, ref="manual:2026-11-06T11:00:00Z",
                                                        title="Neustart von Hand"))
ok("ein anderer Bezug: ein zweiter Vorgang", st == 201 and json.loads(b)["number"] != num, (st, b))
num2 = json.loads(b)["number"]

print("")
print("3. note und close")
st, b = call("POST", "/platform/cases/node", NODE,
             {"op": "note", "number": num, "node": "oaap-test",
              "text": "Verschoben auf 2026-11-14 04:15 CET: gehalten durch joerg-desk bis 2026-11-13"})
ok("note: 200", st == 200, (st, b))
c = ST.get(num)
ok("… als Kommentar des Knotens", c["entries"][-1]["type"] == "comment"
   and c["entries"][-1]["actor"] == "platform:vorgaenge"
   and c["entries"][-1]["actor_name"] == "Knoten oaap-test"
   and "joerg-desk" in c["entries"][-1]["text"], c["entries"][-1])
st, b = call("POST", "/platform/cases/node", NODE, {"op": "note", "number": num, "text": ""})
ok("ein leerer Kommentar: 400", st == 400, (st, b))
st, b = call("POST", "/platform/cases/node", NODE, {"op": "note", "number": "V-0000-0000", "text": "x"})
ok("unbekannte Nummer: 404", st == 404, (st, b))
# ein Vorgang, den eine Person angelegt hat
made = ST.create(kind="internal", title="Von Max", body="", reporter=ADMIN["X-OAAP-User-Id"],
                 reporter_name="Max Muster")
st, b = call("POST", "/platform/cases/node", NODE, {"op": "note", "number": made["number"], "text": "x"})
ok("der Vorgang einer Person: 404 -- der Knoten schreibt nur seine eigenen fort", st == 404, (st, b))
st, b = call("POST", "/platform/cases/node", NODE, {"op": "close", "number": made["number"], "text": "x"})
ok("… und schliesst nur seine eigenen", st == 404 and ST.get(made["number"])["status"] == "new", (st, b))
st, b = call("POST", "/platform/cases/node", NODE, {"op": "weird", "number": num, "text": "x"})
ok("unbekannte op: 400", st == 400, (st, b))
st, b = call("POST", "/platform/cases/node", NODE,
             {"op": "close", "number": num, "node": "oaap-test",
              "text": "Neustart erfolgt nach 2026-11-08 04:15 CET; der Knoten läuft wieder."})
ok("close aus `neu`: 200, closed", st == 200 and json.loads(b)["closed"] is True, (st, b))
c = ST.get(num)
ok("Status geschlossen, Frist laeuft (ended gesetzt), Statuseintrag vom Knoten",
   c["status"] == "closed" and c["ended"] and c["entries"][-1]["type"] == "status"
   and c["entries"][-1]["data"] == {"from": "new", "to": "closed", "by": "node"}
   and "läuft wieder" in c["entries"][-1]["text"], c)
st, b = call("POST", "/platform/cases/node", NODE, {"op": "close", "number": num, "text": "noch einmal"})
ok("close noch einmal: 200, closed false, nichts geaendert",
   st == 200 and json.loads(b)["closed"] is False and len(ST.get(num)["entries"]) == len(c["entries"]), (st, b))
st, b = call("POST", "/platform/cases/node", NODE, OPEN)
ok("nach dem Schliessen legt dieselbe ref einen NEUEN Vorgang an (der naechste Plan)",
   st == 201 and json.loads(b)["number"] not in (num, num2), (st, b))
# ein Vorgang in Klaerung schliesst ebenso
c2 = ST.get(num2)
ST.set_status(c2, ADMIN["X-OAAP-User-Id"], "Max Muster", "in_progress")
st, b = call("POST", "/platform/cases/node", NODE, {"op": "close", "number": num2, "text": "Abgesagt."})
ok("close aus `in Arbeit`: ebenso geschlossen -- ein Tatbestand, kein Bearbeitungsschritt",
   st == 200 and ST.get(num2)["status"] == "closed", (st, b))

print("")
print("4. Wer ihn sieht")
st, b = call("GET", "/", ADMIN, None)
ok("der Admin sieht den Vorgang im Eingang", st == 200 and OPEN["title"] in b, (st, b[:300]))
st, b = call("GET", f"/v/{num}", ADMIN, None)
ok("die Seite nennt den Knoten als Absender",
   st == 200 and "Gemeldet vom Knoten <b>oaap-test</b>" in b and "Knoten oaap-test" in b, (st, b[:600]))
other = {"X-OAAP-User-Id": "22222222-2222-4222-8222-222222222222", "X-OAAP-Roles": "user",
         "X-OAAP-User": "anna", "X-OAAP-Display-Name": "Anna"}
st, b = call("GET", f"/v/{num}", other, None)
ok("eine Person ohne Bereich sieht ihn nicht", st == 404, (st, b[:200]))

print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
