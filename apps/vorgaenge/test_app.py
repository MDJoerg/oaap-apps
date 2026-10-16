"""Vorgaenge 0.1 gegen einen echten Server im Test (RFC-0061 Stufe 1).

    python3 apps/vorgaenge/test_app.py

Kein Gateway, kein Knoten: die Kopfzeilen, die das Gateway setzt, setzt
hier der Test -- und prueft dabei vor allem, was ohne sie passiert. Die
Rechte aus „Rollen & Rechte“ kommen aus einem Ersatz fuer authz_client,
der dieselbe Antwortform liefert wie /authz/effective.
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
DATA = tempfile.mkdtemp(prefix="vorgaenge-test-")
os.environ["VORGAENGE_DATA_DIR"] = DATA

import app  # noqa: E402
import store  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


GRANTS = {}   # user -> grants wie /authz/effective sie liefert


class FakeAuthz:
    url = "http://fake"

    def grants(self, uid):
        return GRANTS.get(uid, [])


app.STORE = store.Store(os.path.join(DATA, "t.db"))
app.AUTHZ = FakeAuthz()
srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
HOST = f"127.0.0.1:{srv.server_port}"

SOURCE = {"X-OAAP-Source-Instance": "hbvp-portal-test", "X-OAAP-Source-App": "hbsha",
          "X-OAAP-Source-Version": "1.2.3", "X-OAAP-Source-Tenant": "t-hbvp"}


def user(uid, roles="user", name=None):
    return {"X-OAAP-User-Id": uid, "X-OAAP-Roles": roles,
            "X-OAAP-Display-Name": name or uid}


def call(method, path, headers=None, body=None, ctype=None):
    data = None
    h = dict(headers or {})
    if body is not None:
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if ctype == "application/json" else body.encode())
        h["Content-Type"] = ctype or "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


ORIGIN = {"Origin": f"http://{HOST}"}


def report(uid="u-anna", **over):
    doc = {"kind": "bug", "title": "Tor 2 ist defekt", "body": "Seit Samstag.",
           "object": {"kind": "venue", "id": "0d6f-venue"},
           "browser": {"path": "/admin", "user_agent": "UA", "last_error": "x is undefined",
                       "cookie": "SECRET", "user_id": "u-mallory"},
           "attested": {"instance": "faked", "user_id": "u-mallory"}}
    doc.update(over)
    return call("POST", "/platform/feedback", {**SOURCE, **user(uid), **ORIGIN}, doc, "application/json")


# --- 1. Der Knopf zeigt sich nur mit Sitzung UND ueber das Gateway
st, _, _ = call("GET", "/platform/feedback/session", user("u-anna"))
ok("session: ohne Herkunftszeilen 404 (nicht über das Gateway gekommen)", st == 404, st)
st, _, _ = call("GET", "/platform/feedback/session", SOURCE)
ok("session: ohne angemeldete Person 404", st == 404, st)
st, b, _ = call("GET", "/platform/feedback/session", {**SOURCE, **user("u-anna")})
ok("session: mit beidem 200 und ok", st == 200 and json.loads(b).get("ok") is True, (st, b))
ok("session: der Knopf bietet `internal` nicht an", "internal" not in json.loads(b)["kinds"])

# --- 2. Melden
st, b, _ = call("POST", "/platform/feedback", {**user("u-anna"), **ORIGIN},
                {"kind": "bug", "title": "x"}, "application/json")
ok("feedback: ohne Herkunft abgelehnt (404)", st == 404, st)
st, b, _ = call("POST", "/platform/feedback", {**SOURCE, **user("u-anna")},
                {"kind": "bug", "title": "x"}, "application/json")
ok("feedback: ohne Origin abgelehnt (403)", st == 403, st)
st, b, _ = call("POST", "/platform/feedback", {**SOURCE, **user("u-anna"), "Origin": "https://evil.example"},
                {"kind": "bug", "title": "x"}, "application/json")
ok("feedback: fremder Origin abgelehnt (403)", st == 403, st)
st, b, _ = call("POST", "/platform/feedback", {**SOURCE, **user("u-anna"), **ORIGIN},
                "kind=bug&title=x", "application/x-www-form-urlencoded")
ok("feedback: kein JSON -> 415", st == 415, st)
st, b, _ = report(kind="internal")
ok("feedback: Art `internal` über den Knopf abgelehnt", st == 400, (st, b))
st, b, _ = report(title="  ")
ok("feedback: leerer Betreff abgelehnt", st == 400, (st, b))
st, b, _ = report()
ok("feedback: angelegt (201) mit Nummer", st == 201 and json.loads(b)["number"].startswith("V-"), (st, b))
num = json.loads(b)["number"]
c = app.STORE.get(num)
att = c["tech"]["attested"]
ok("beglaubigt: Instanz aus der Kopfzeile, nicht aus dem Dokument",
   att["instance"] == "hbvp-portal-test", att)
ok("beglaubigt: Person aus der Kopfzeile, nicht aus dem Dokument",
   att["user_id"] == "u-anna" and c["reporter"] == "u-anna", att)
ok("beglaubigt: App und Version vom Gateway", att["app"] == "hbsha" and att["version"] == "1.2.3", att)
br = c["tech"].get("browser") or {}
ok("browser: nur erlaubte Felder (kein cookie, keine user_id)",
   "cookie" not in br and "user_id" not in br and br.get("last_error") == "x is undefined", br)
ok("Objektbezug {kind, id} gespeichert", (c["object_kind"], c["object_id"]) == ("venue", "0d6f-venue"))
ok("liegt im Eingang, Status neu", c["queue"] == "eingang" and c["status"] == "new")

# --- 3. Wer sieht was
st, b, _ = call("GET", f"/v/{num}", user("u-anna"))
ok("die Meldende sieht ihren Vorgang", st == 200 and "Tor 2 ist defekt" in b, st)
ok("… aber nicht die Browser-Angaben (nur der Bereich)", "x is undefined" not in b)
st, b, _ = call("GET", f"/v/{num}", user("u-bob"))
ok("eine andere Person ohne Recht sieht ihn nicht (404)", st == 404, st)
st, b, _ = call("GET", f"/v/{num}", user("u-admin", "admin,user"))
ok("der Admin des Mandanten sieht den Eingang", st == 200 and "x is undefined" in b, st)
st, b, _ = call("GET", "/", user("u-bob"))
ok("Übersicht ohne Bereiche: kein Vorgang von Anna", st == 200 and num not in b)

# --- 4. Bereiche
st, b, _ = call("POST", "/bereiche", {**user("u-bob"), **ORIGIN}, "id=mb&title=Mannschaft+mB")
ok("Bereich anlegen: nur Admins", st == 400, st)
st, b, _ = call("POST", "/bereiche", {**user("u-admin", "admin"), **ORIGIN}, "id=mb&title=Mannschaft+mB")
ok("Bereich anlegen: Admin -> 303", st == 303, (st, b))
st, b, _ = call("POST", "/bereiche", {**user("u-admin", "admin")}, "id=x2&title=X")
ok("Formular ohne Origin abgelehnt (403)", st == 403, st)
st, b, _ = call("POST", "/bereiche", {**user("u-admin", "admin"), **ORIGIN}, "id=Böse Kennung&title=X")
ok("Bereich: ungültige Kennung abgelehnt", st == 400, st)

# --- 5. Klären: Status und Verschieben
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-admin", "admin"), **ORIGIN}, "status=rejected&text=")
ok("Ablehnung ohne Begründung abgelehnt", st == 400 and "Begründung" in b, st)
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-admin", "admin"), **ORIGIN}, "status=closed")
ok("von neu nicht direkt nach geschlossen", st == 400, st)
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-admin", "admin"), **ORIGIN}, "status=triage")
ok("neu -> Klärung", st == 303 and app.STORE.get(num)["status"] == "triage", st)
st, b, _ = call("POST", f"/v/{num}/move", {**user("u-anna"), **ORIGIN}, "queue=mb")
ok("die Meldende darf nicht verschieben", st == 400, st)
st, b, _ = call("POST", f"/v/{num}/move", {**user("u-admin", "admin"), **ORIGIN}, "queue=mb&text=Hallenwart")
c = app.STORE.get(num)
ok("verschoben nach mb, Eingang wird Beobachter", c["queue"] == "mb" and c["observers"] == ["eingang"], c["observers"])

GRANTS["u-trainer"] = [{"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
                        "fields": {"queue": ["mb"]}}]
GRANTS["u-other"] = [{"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
                      "fields": {"queue": ["mc"]}}]
st, b, _ = call("GET", f"/v/{num}", user("u-trainer"))
ok("Bearbeiter/in mit Kontext mb sieht ihn", st == 200, st)
st, b, _ = call("GET", f"/v/{num}", user("u-other"))
ok("Bearbeiter/in mit Kontext mc sieht ihn nicht", st == 404, st)
st, b, _ = call("GET", "/", user("u-trainer"))
ok("Übersicht der Bearbeiter/in listet ihn", num in b)
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-trainer"), **ORIGIN}, "status=answered&text=Ist+bestellt")
ok("Bearbeiter/in beantwortet", st == 303 and app.STORE.get(num)["status"] == "answered", (st, b))
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-other"), **ORIGIN}, "status=closed")
ok("fremder Bereich darf nicht schließen", st == 404, st)
st, b, _ = call("POST", f"/v/{num}/comment", {**user("u-anna"), **ORIGIN}, "text=Danke")
ok("die Meldende kommentiert", st == 303, st)
st, b, _ = call("POST", f"/v/{num}/status", {**user("u-anna"), **ORIGIN}, "status=closed")
ok("die Meldende schließt die Antwort", st == 303 and app.STORE.get(num)["status"] == "closed", st)
types = [e["type"] for e in app.STORE.get(num)["entries"]]
ok("Verlauf ist vollständig und nur angehängt",
   types == ["created", "status", "move", "status", "comment", "status"], types)
st, b, _ = call("GET", f"/v/{num}", user("u-admin", "admin"))
ok("der Eingang als Beobachter liest weiter mit", st == 200, st)

# --- 6. Interner Vorgang in der App
st, b, h = call("POST", "/neu", {**user("u-trainer"), **ORIGIN},
                "kind=internal&queue=mb&title=Trikots+bestellen&body=")
ok("interner Vorgang angelegt", st == 303 and h.get("Location", "").startswith("/v/V-"), (st, h))
internal = h.get("Location", "")
st, b, _ = call("GET", internal, user("u-admin", "admin"))
ok("der Admin sieht einen Bereich, der nie im Eingang lag, NICHT (Eingang ja, alles nein)",
   st == 404, st)

# --- 7. Drosselung
app.REPORTS_PER_HOUR = 3
codes = [report("u-spam")[0] for _ in range(4)]
ok("Drosselung je Person: die vierte Meldung in der Stunde 429", codes == [201, 201, 201, 429], codes)

# --- 7b. Loeschfrist (Q6, 0.1.1)
import sqlite3  # noqa: E402
import time as _t  # noqa: E402
app.REPORTS_PER_HOUR = 100
DAY = 86400
st, b, _ = report("u-ret", title="Alt und geschlossen")
old = json.loads(b)["number"]
st, b, _ = report("u-ret", title="Noch offen")
still_open = json.loads(b)["number"]
call("POST", f"/v/{old}/comment", {**user("u-ret"), **ORIGIN}, "text=Nachtrag+der+Meldenden")
call("POST", f"/v/{old}/comment", {**user("u-admin", "admin", "Hallenwart Admin"), **ORIGIN}, "text=Antwort")
st, b, _ = call("POST", f"/v/{old}/status", {**user("u-admin", "admin"), **ORIGIN}, "status=rejected&text=doppelt")
ok("abgelehnt -> Abschlusszeit gesetzt", st == 303 and app.STORE.get(old)["ended"] != "", app.STORE.get(old)["ended"])
ok("nach 11 Monaten noch nichts entfernt",
   app.STORE.remove_person_data(12, now=_t.time() + 335 * DAY) == [])
done = app.STORE.remove_person_data(12, now=_t.time() + 370 * DAY)
ok("nach 12 Monaten die abgeschlossenen Vorgaenge, kein offener",
   old in done and still_open not in done
   and all(app.STORE.get(n)["status"] in ("closed", "rejected") for n in done), done)
c = app.STORE.get(old)
ok("meldende Person ersetzt", (c["reporter"], c["reporter_name"]) == ("removed", "entfernt"), c["reporter"])
ok("Benutzer-ID, Rollen und Browser-Angaben weg",
   "user_id" not in c["tech"]["attested"] and "roles" not in c["tech"]["attested"]
   and "browser" not in c["tech"], c["tech"])
ok("die beglaubigte Herkunft bleibt", c["tech"]["attested"].get("instance") == "hbvp-portal-test")
actors = [(e["type"], e["actor"], e["actor_name"]) for e in c["entries"]]
ok("ihre eigenen Eintraege heissen jetzt 'entfernt'",
   all(a != "u-ret" for _, a, _n in actors) and ("comment", "removed", "entfernt") in actors, actors)
ok("die Eintraege der Bearbeitenden bleiben", ("comment", "u-admin", "Hallenwart Admin") in actors, actors)
ok("die Entfernung steht im Verlauf", actors[-1][0] == "retention", actors[-1])
ok("Betreff und Bezug bleiben", c["title"] == "Alt und geschlossen" and c["object_id"] == "0d6f-venue")
ok("der offene Vorgang derselben Person bleibt unberuehrt",
   app.STORE.get(still_open)["reporter"] == "u-ret")
ok("ein zweiter Lauf tut nichts", app.STORE.remove_person_data(12, now=_t.time() + 900 * DAY) == [])
st, b, _ = call("GET", f"/v/{old}", user("u-ret"))
ok("die fruehere Meldende sieht den Vorgang nicht mehr", st == 404, st)
st, b, _ = call("GET", f"/v/{old}", user("u-admin", "admin"))
ok("der Eingang zeigt 'entfernt' und den Eintrag Loeschfrist",
   st == 200 and "entfernt" in b and "Löschfrist" in b and "u-ret" not in b, st)
# Wiederoeffnen haelt die Frist an
st, b, _ = report("u-re", title="Wird wieder geoeffnet")
re_n = json.loads(b)["number"]
for s_ in ("rejected&text=nein", "reopened"):
    call("POST", f"/v/{re_n}/status", {**user("u-admin", "admin"), **ORIGIN}, "status=" + s_)
ok("wieder geoeffnet: keine Abschlusszeit, keine Entfernung",
   app.STORE.get(re_n)["ended"] == ""
   and re_n not in app.STORE.remove_person_data(12, now=_t.time() + 900 * DAY))
# eine Ablage aus 0.1.0 (ohne die neuen Spalten) bekommt ihre Frist
oldp = os.path.join(DATA, "v010.db")
d = sqlite3.connect(oldp)
d.executescript("""
CREATE TABLE cases (id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, queue TEXT NOT NULL,
  reporter TEXT NOT NULL, reporter_name TEXT NOT NULL, object_kind TEXT NOT NULL DEFAULT '',
  object_id TEXT NOT NULL DEFAULT '', source_instance TEXT NOT NULL DEFAULT '',
  source_app TEXT NOT NULL DEFAULT '', tech TEXT NOT NULL DEFAULT '{}',
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE entries (seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
  at TEXT NOT NULL, actor TEXT NOT NULL, actor_name TEXT NOT NULL, type TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '{}');
INSERT INTO cases VALUES ('c1','V-2025-0001','bug','t','b','closed','eingang','u-x','X',
  '','','','','{"attested":{"user_id":"u-x"}}','2025-01-01T00:00:00Z','2025-03-01T00:00:00Z');
INSERT INTO entries (case_id, at, actor, actor_name, type) VALUES
  ('c1','2025-02-01T00:00:00Z','u-adm','A','status');
""")
d.commit()
d.close()
s010 = store.Store(oldp)
ok("0.1.0-Ablage: Abschlusszeit aus dem letzten Statuseintrag",
   s010.get("V-2025-0001")["ended"] == "2025-02-01T00:00:00Z", s010.get("V-2025-0001")["ended"])
ok("0.1.0-Ablage: die Frist greift", s010.remove_person_data(12) == ["V-2025-0001"])
# Einstellung und Kalender
ok("Frist: '6' -> 6, '0'/'abc'/'' -> 12",
   [app.retention_months(x) for x in ("6", "0", "abc", "")] == [6, 12, 12, 12])
ok("31. Maerz minus ein Monat ist der 29. Februar (Schaltjahr)",
   store._months_before(_t.mktime((2024, 3, 31, 12, 0, 0, 0, 0, 0)) - _t.timezone, 1)
   .startswith("2024-02-29"))

# --- 8. Ohne Anmeldung nichts
st, b, _ = call("GET", "/")
ok("ohne X-OAAP-User-Id 401", st == 401, st)
st, b, _ = call("GET", "/healthz")
ok("healthz 200", st == 200 and b == "ok", st)

srv.shutdown()
print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
