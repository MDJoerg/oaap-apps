"""Vorgaenge 0.21: Lieferungen durch ein Tor (RFC-0135 Stufe 1).

    python3 apps/vorgaenge/test_gate.py

Was das Gateway tut -- den Zugang pruefen (identity), den Prinzipal nennen,
das Tor beschreiben -- steht hier als Kopfzeilen, so wie sie beim Empfaenger
ankommen. Gehalten wird:

1. Nur mit der Beschreibung eines Tors und dem Zugang als Prinzipal: keine
   Sitzung, keine Herkunft einer App, kein fremder Prinzipal.
2. JSON und Formularfelder, gegen dieselbe Feldliste wie die Vorlagen
   (gates.py): unbekannte Felder und fehlende Pflichtfelder mit Namen (422).
3. Dieselbe `ref` zweimal ist EIN Vorgang -- auch nach dem Schliessen; je
   Zugang getrennt.
4. Antwort als JSON oder als Zeilen `number=`/`ref=`.
5. `GET .../<ref>` liest Status und letzte Antwort; eine fremde ref fehlt.
6. Der Vorgang steht im Bereich des Prozesses, mit den technischen Feldern.
7. gates.py ist die Kopie aus oaap-reference (wenn daneben ausgecheckt).
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="vorgaenge-gate-")
os.environ["VORGAENGE_DATA_DIR"] = DATA

import app  # noqa: E402
import gates  # noqa: E402
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
ST.add_queue("sap-support", "SAP-Support")
SRV = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
SRV.store, SRV.authz, SRV.kick = ST, FakeAuthz(), threading.Event()
threading.Thread(target=SRV.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{SRV.server_port}"


def call(method, path, headers=None, body=None, ctype="application/json"):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = (json.dumps(body).encode() if ctype == "application/json"
                else (body if isinstance(body, str) else urlencode(body)).encode())
        h["Content-Type"] = ctype
    req = urllib.request.Request(URL + path, data=data, method=method, headers=h)
    try:
        with urllib.request.build_opener().open(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


FIELDS = gates.check_fields(gates.DEFAULT_FIELDS)
PROC = {"process": "sap-support", "queue": "sap-support", "kind": "question", "label": "SAP-Support"}


def gate_headers(principal="gate-sap-support-1", name="sap-support", fields=FIELDS, proc=PROC):
    """Was der Block des Tors dem Ziel schickt (appctl.gate_site_lines)."""
    return {"X-OAAP-User": principal, "X-OAAP-User-Id": "22222222-2222-4222-8222-222222222222",
            "X-OAAP-Roles": "user", "X-OAAP-Gate-Access": principal,
            "X-OAAP-Gate-Label": "SAP%20A4H%20001",
            "X-OAAP-Gate-Name": name, "X-OAAP-Gate-Tenant": "t-1",
            "X-OAAP-Gate-Process": gates.pack(proc), "X-OAAP-Gate-Fields": gates.pack(fields),
            "X-OAAP-Source-Instance": f"gate:{name}", "X-OAAP-Source-App": "gate",
            "X-OAAP-Source-Version": "-", "X-OAAP-Source-Tenant": "t-1"}


G = gate_headers()
GOOD = {"ref": "ZSUP-000815", "title": "ME21N: keine Berechtigung",
        "text": "Benutzer MMUSTER kann keine Bestellung anlegen.\nZweite Zeile.",
        "system": "A4H/001", "occurred": "2026-11-26T09:14:03Z"}

print("1. nur durch ein Tor")
st, _ = call("POST", "/platform/cases/gate", {}, GOOD)
ok("ohne Beschreibung des Tors: 403", st == 403, st)
st, _ = call("POST", "/platform/cases/gate", dict(G, **{"X-OAAP-User": "jemand"}), GOOD)
ok("Prinzipal ist nicht der Zugang: 403", st == 403, st)
st, _ = call("POST", "/platform/cases/gate", dict(G, **{"X-OAAP-Source-Instance": "chat"}), GOOD)
ok("Herkunft ist kein Tor: 403", st == 403, st)
st, _ = call("POST", "/platform/cases/gate", dict(G, **{"X-OAAP-Gate-Fields": "kaputt"}), GOOD)
ok("Feldliste unlesbar: 403", st == 403, st)

print("2. Felder")
st, body = call("POST", "/platform/cases/gate", G, dict(GOOD, prio="1", extra="x"))
ok("unbekannte Felder: 422 mit Namen", st == 422 and "extra, prio" in body, (st, body))
st, body = call("POST", "/platform/cases/gate", G, {"ref": "ZSUP-1"})
ok("Pflichtfelder fehlen: 422 mit Namen", st == 422 and "title, text" in body, (st, body))
st, body = call("POST", "/platform/cases/gate", G, dict(GOOD, occurred="26.11.2026"))
ok("falsches Format: 422 mit Feld", st == 422 and "occurred" in body, (st, body))
st, body = call("POST", "/platform/cases/gate", G, dict(GOOD, ref="a b"))
ok("ref mit Leerzeichen: 422", st == 422 and "ref" in body, (st, body))
st, body = call("POST", "/platform/cases/gate", G, "kein json")
ok("kein JSON-Objekt: 422", st == 422, (st, body))
st, body = call("POST", "/platform/cases/gate", G, "x", ctype="text/xml")
ok("anderer Inhaltstyp: 415", st == 415, (st, body))

print("3. anlegen, einmal je ref")
st, body = call("POST", "/platform/cases/gate", G, GOOD)
doc = json.loads(body) if st == 201 else {}
ok("JSON: 201 mit Nummer und ref", st == 201 and doc.get("number") and doc.get("ref") == "ZSUP-000815",
   (st, body))
num = doc.get("number")
st, body = call("POST", "/platform/cases/gate", G, GOOD)
ok("dieselbe ref noch einmal: 200, derselbe Vorgang",
   st == 200 and json.loads(body).get("number") == num and json.loads(body).get("existing"), (st, body))
st, body = call("POST", "/platform/cases/gate", gate_headers("gate-sap-support-2"), GOOD)
ok("dieselbe ref von einem anderen Zugang: ein eigener Vorgang",
   st == 201 and json.loads(body).get("number") != num, (st, body))
c = ST.get(num)
tech = json.loads(c["tech"]) if isinstance(c.get("tech"), str) else c.get("tech")
ok("im Bereich des Prozesses, Art aus dem Prozess, Absender mit Bezeichnung",
   c["queue"] == "sap-support" and c["kind"] == "question"
   and c["reporter_name"] == "SAP A4H 001 (Tor sap-support)", c)
ok("Betreff und Text aus den Feldern", c["title"] == GOOD["title"] and c["body"] == GOOD["text"], c)
ok("technische Felder und Beglaubigung",
   tech.get("delivered") == {"system": "A4H/001", "occurred": "2026-11-26T09:14:03Z"}
   and tech["attested"].get("gate") == "sap-support"
   and tech["attested"].get("access") == "gate-sap-support-1", tech)

print("4. Formularfelder und Zeilen")
form = dict(GOOD, ref="ZSUP-000816", text="aus ABAP: set_form_field")
st, body = call("POST", "/platform/cases/gate?answer=text", G, form,
                ctype="application/x-www-form-urlencoded")
lines = dict(x.split("=", 1) for x in body.strip().splitlines() if "=" in x)
ok("Formularfelder, Antwort als Zeilen", st == 201 and lines.get("ref") == "ZSUP-000816"
   and lines.get("number", "").startswith(num.split("-")[0]), (st, body))
st, body = call("POST", "/platform/cases/gate", G, "ref=U-1&title=Pr%C3%BCfung&text=x",
                ctype="application/x-www-form-urlencoded")
ok("Umlaut UTF-8 kodiert: gespeichert als ü", st == 201
   and ST.get(json.loads(body)["number"])["title"] == "Prüfung", (st, body))
st, body = call("POST", "/platform/cases/gate", G, "ref=U-2&title=Pr%FCfung&text=x",
                ctype="application/x-www-form-urlencoded")
ok("Latin-1 ohne Angabe: 400 mit Hinweis statt Ersatzzeichen (gemessen 26.11.)",
   st == 400 and "charset=iso-8859-1" in body, (st, body))
st, body = call("POST", "/platform/cases/gate", G, "ref=U-3&title=Pr%FCfung&text=x",
                ctype="application/x-www-form-urlencoded; charset=ISO-8859-1")
ok("Latin-1 mit charset=ISO-8859-1: gespeichert als ü", st == 201
   and ST.get(json.loads(body)["number"])["title"] == "Prüfung", (st, body))
st, body = call("POST", "/platform/cases/gate", G, "ref=U-4&title=x&text=x",
                ctype="application/x-www-form-urlencoded; charset=windows-1252")
ok("anderer Zeichensatz: 415", st == 415, (st, body))
st, body = call("POST", "/platform/cases/gate", dict(G, Accept="text/plain"),
                dict(GOOD, ref="ZSUP-9", extra="x"))
ok("Ablehnung als Zeile error=", st == 422 and body.startswith("error=abgelehnt"), (st, body))

print("5. lesen nach ref")
st, body = call("GET", "/platform/cases/gate/ZSUP-000815", G)
d = json.loads(body) if st == 200 else {}
ok("eigene ref: Nummer und Status", st == 200 and d.get("number") == num and d.get("status") == "new",
   (st, body))
ST.set_status(ST.get(num), "u1", "Anna Admin", "answered", "Rolle Z_ME21N zugewiesen.\nBitte neu anmelden.")
st, body = call("GET", "/platform/cases/gate/ZSUP-000815?answer=text", G)
lines = dict(x.split("=", 1) for x in body.strip().splitlines() if "=" in x)
ok("Antwort als Zeilen, Umbruch als \\n", st == 200 and lines.get("status") == "answered"
   and lines.get("answer") == "Rolle Z_ME21N zugewiesen.\\nBitte neu anmelden.", (st, body))
st, _ = call("GET", "/platform/cases/gate/ZSUP-000815", gate_headers("gate-sap-support-3"))
ok("ref eines anderen Zugangs: 404", st == 404, st)
st, _ = call("GET", "/platform/cases/gate/ZSUP-000815", gate_headers(name="anderes-tor"))
ok("dieselbe ref an einem anderen Tor: 404", st == 404, st)
st, _ = call("GET", "/platform/cases/gate/ZSUP-000815", {})
ok("ohne Tor: 403", st == 403, st)
ST.set_status(ST.get(num), "u1", "Anna Admin", "closed", "erledigt")
st, body = call("POST", "/platform/cases/gate", G, GOOD)
ok("geschlossen, dieselbe ref: kein neuer Vorgang", st == 200 and json.loads(body)["number"] == num,
   (st, body))

print("6. Feldliste eines Tors")
own = gates.check_fields([
    {"name": "ticket", "type": "text", "max": 20, "required": True, "to": "ref"},
    {"name": "kurz", "type": "text", "required": True, "to": "title"},
    {"name": "dringend", "type": "yesno", "to": "tech"},
    {"name": "bereich", "type": "choice", "choices": ["MM", "FI"], "to": "tech"}])
H = gate_headers(name="formular", fields=own)
st, body = call("POST", "/platform/cases/gate", H, {"ticket": "T-1", "kurz": "Test", "dringend": "X",
                                                    "bereich": "MM"})
ok("eigene Felder: 201, ohne Textfeld", st == 201, (st, body))
st, body = call("POST", "/platform/cases/gate", H, {"ticket": "T-2", "kurz": "Test", "bereich": "SD"})
ok("Auswahl ausserhalb: 422", st == 422 and "bereich" in body, (st, body))
try:
    gates.check_fields([{"name": "a", "to": "title", "required": True}])
    ok("ohne ref-Feld abgelehnt", False)
except ValueError as e:
    ok("ohne ref-Feld abgelehnt", "ref" in str(e), e)

print("7. die Kopie")
ref_copy = os.path.join(HERE, "..", "..", "..", "oaap-reference", "platform", "services", "gates.py")
if os.path.exists(ref_copy):
    a = open(ref_copy, encoding="utf-8").read().replace("\r\n", "\n")
    b = open(os.path.join(HERE, "gates.py"), encoding="utf-8").read().replace("\r\n", "\n")
    ok("gates.py ist die Kopie aus oaap-reference", a == b)
else:
    print("SKIP  gates.py gegen oaap-reference (nicht daneben ausgecheckt)")

SRV.shutdown()
print("")
print(f"{'OK' if not fails else 'FAILED'} -- {fails} failure(s)")
sys.exit(1 if fails else 0)
