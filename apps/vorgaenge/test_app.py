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
ok("0.9.0: die Meldende schließt nicht mehr über das Statusformular (sonst fehlte „Lösung passt“)",
   st == 400 and app.STORE.get(num)["status"] == "answered", st)
st, b, _ = call("POST", f"/v/{num}/accept", {**user("u-anna"), **ORIGIN}, "")
ok("die Meldende nimmt die Antwort ab („Lösung passt“)",
   st == 303 and app.STORE.get(num)["status"] == "closed" and app.STORE.get(num)["mark"] == "accepted", st)
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

# --- 0.6.0 (RFC-0101 Stufe 3): Unterart, Felder, Bereich, Status der eigenen Vorgänge
app.STORE.add_queue("sap-support", "SAP-Support")
st, b, _ = report(kind="question", title="Nicht zufrieden: SAP", subkind="chat-answer",
                  fields={"assistant": "sap", "rev": 3, "tools": [{"name": "whoami", "outcome": "ok"}],
                          "doc_gap": True}, queue="sap-support")
ok("0.6.0: Unterart und Felder angenommen (201)", st == 201, (st, b))
n6 = json.loads(b)["number"]
c6 = app.STORE.get(n6)
ok("0.6.0: Felder getrennt vom Beglaubigten gespeichert",
   c6["tech"].get("subkind") == "chat-answer" and c6["tech"]["fields"]["rev"] == 3
   and "fields" not in c6["tech"]["attested"], c6["tech"])
ok("0.6.0: der Bereich, den die Seite nennt", c6["queue"] == "sap-support", c6["queue"])
st, b, _ = report(kind="question", title="x", subkind="chat-answer", fields={}, queue="gibt-es-nicht")
ok("0.6.0: ein unbekannter Bereich -> Eingang", st == 201 and app.STORE.get(json.loads(b)["number"])["queue"]
   == "eingang", (st, b))
st, b, _ = report(kind="question", title="x", fields={"a": 1})
ok("0.6.0: Felder ohne Unterart abgelehnt", st == 400 and "Unterart" in b, (st, b))
st, b, _ = report(kind="question", title="x", subkind="Chat Answer")
ok("0.6.0: Unterart, die keine Kennung ist: abgelehnt", st == 400, (st, b))
st, b, _ = report(kind="question", title="x", subkind="chat-answer", fields={"Bad-Key": 1})
ok("0.6.0: Feldname mit Großbuchstaben/Bindestrich abgelehnt", st == 400, (st, b))
st, b, _ = report(kind="question", title="x", subkind="chat-answer", fields={"t": "x" * 41000})
ok("0.6.0: Felder über 40000 Bytes abgelehnt", st == 400, (st, b))
GRANTS["u-sap"] = [{"object": "case", "activities": ["read"], "fields": {"queue": ["sap-support"]}}]
st, b, _ = call("GET", "/v/" + n6, user("u-anna"))
ok("0.6.0: die Meldende sieht die Angaben der App auf ihrem Vorgang",
   st == 200 and "Angaben der App" in b and "chat-answer" in b and "nicht beglaubigt" in b, (st, b[-600:]))
st, b, _ = call("GET", "/platform/feedback/mine?numbers=" + n6 + "," + num, {**SOURCE, **user("u-anna")})
d6 = json.loads(b) if st == 200 else {}
ok("0.6.0: /platform/feedback/mine nennt den Status eigener Vorgänge",
   st == 200 and d6["cases"][n6]["status"] == "new" and d6["cases"][n6]["answered"] is False, (st, b))
st, b, _ = call("GET", "/platform/feedback/mine?numbers=" + n6, {**SOURCE, **user("u-bert")})
ok("0.6.0: KÖDER: Bert erfährt nichts über Annas Vorgang (fehlt wie unbekannt)",
   st == 200 and json.loads(b)["cases"] == {}, (st, b))
st, b, _ = call("GET", "/platform/feedback/mine?numbers=" + n6, user("u-anna"))
ok("0.6.0: /platform/feedback/mine nicht über das Gateway: 404", st == 404, st)
c6s = app.STORE.get(n6)
app.STORE.set_status(c6s, "u-x", "X", "answered", "So geht es.")
st, b, _ = call("GET", "/platform/feedback/mine?numbers=" + n6, {**SOURCE, **user("u-anna")})
ok("0.6.0: beantwortet -> answered true", st == 200 and json.loads(b)["cases"][n6]["answered"] is True, (st, b))

# --- 0.7.0: Name, Rechte-Hinweis, Darstellung, OAAP-Kopf- und Fußzeile
st, b, _ = call("POST", "/platform/feedback", {**SOURCE, **user("u-joerg", name="J%C3%B6rg%20M%C3%BCller"), **ORIGIN},
                {"kind": "question", "title": "Umlaute", "body": "**fett** und\n```mermaid\ngraph TD\nA-->B\n```"},
                "application/json")
n7 = json.loads(b)["number"]
ok("0.7.0: der Anzeigename wird dekodiert (RFC-0040 D4)", app.STORE.get(n7)["reporter_name"] == "Jörg Müller",
   app.STORE.get(n7)["reporter_name"])
st, b, _ = call("POST", f"/v/{n7}/comment", {**user("u-joerg", name="J%C3%B6rg%20M%C3%BCller"), **ORIGIN},
                "text=Danke", "application/x-www-form-urlencoded")
ok("0.7.0: ... auch im Kommentar", app.STORE.get(n7)["entries"][-1]["actor_name"] == "Jörg Müller",
   app.STORE.get(n7)["entries"][-1]["actor_name"])
st, pg, _ = call("GET", f"/v/{n7}", user("u-joerg", name="J%C3%B6rg%20M%C3%BCller"))
ok("0.7.0: Meldende ohne Rechte: Hinweis statt fehlendem Formular -- Eingang braucht die App-Rolle admin",
   st == 200 and "Den Status kannst du hier nicht ändern" in pg and "<code>admin</code>" in pg
   and "tenant_admin" in pg and "Als Meldende kannst du im Reiter <a href='#verlauf'>Verlauf</a> kommentieren" in pg, pg[-1500:])
ok("0.7.0: OAAP-Kopfzeile mit Benutzermenü: dekodierter Name, Abmelden als POST",
   "<header class=oaap>" in pg and "<summary id=uname>Jörg Müller</summary>" in pg
   and '<form method=post action="/auth/logout">' in pg, None)
ok("0.7.0: Fußzeile mit Version und den eigenen Rechten",
   f"<footer class=oaap><b>Vorgänge {app.VERSION}</b>" in pg and "Bearbeiten in keinem Bereich" in pg, None)
ok("0.7.0: Text als Markdown zum Formatieren (roh im Element, escaped) und der Umschalter",
   "data-md" in pg and "**fett**" in pg and "A--&gt;B" in pg and 'data-md-mode=raw' in pg
   and "/static/md/view.js" in pg and "<script>x" not in pg, None)
st, pg, _ = call("GET", f"/v/{n7}", user("u-admin", roles="user,admin"))
ok("0.7.0: wer den Eingang bearbeitet, bekommt keinen Hinweis, sondern das Formular",
   st == 200 and "Den Status kannst du hier nicht ändern" not in pg and "action='/v/" in pg
   and "bearbeiten: Eingang" in pg, pg[-800:])
st, js, _ = call("GET", "/static/md/view.js", user("u-joerg"))
ok("0.7.0: /static/md/view.js wird ausgeliefert", st == 200 and "OAAPView" in js, st)
ok("0.7.0: ... nur die genannten Dateien", all(call("GET", x, user("u-joerg"))[0] != 200
                                             for x in ("/static/md/../../app.py", "/static/md/MERMAID-LICENSE",
                                                       "/static/app.py")))
import sqlite3  # noqa: E402
_mdb = os.path.join(DATA, "alt.db")
_s = store.Store(_mdb)
_s.create(kind="bug", title="alt", body="", reporter="u-x", reporter_name="J%C3%B6rg")
_c = sqlite3.connect(_mdb)
_c.execute("UPDATE cases SET reporter_name='J%C3%B6rg'")
_c.execute("PRAGMA user_version = 0")
_c.commit()
_c.close()
_s2 = store.Store(_mdb)
ok("0.7.0: schon gespeicherte, kodierte Namen werden beim Start einmal dekodiert",
   _s2.listing(reporter="u-x")[0]["reporter_name"] == "Jörg", _s2.listing(reporter="u-x"))

# --- 0.8.0 (RFC-0103 Stufe 1): tenant_admin in jedem Bereich, Zuständige je Bereich
TA = user("u-ta", roles="user,tenant_admin", name="Tenant Admin")
app.STORE.add_queue("jira", "Jira-Fragen")
app.STORE.add_queue("partner", "Partner")
st, b, _ = report(uid="u-melder", kind="question", title="Stufe 1")
n8 = json.loads(b)["number"]
st, pg, _ = call("GET", f"/v/{n8}", TA)
ok("0.8.0: der tenant_admin (ohne App-Rolle admin) sieht den Vorgang im Eingang und darf verschieben",
   st == 200 and "/move'" in pg and "Den Status kannst du hier nicht ändern" not in pg, pg[-800:])
st, b, _ = call("POST", f"/v/{n8}/move", {**TA, **ORIGIN}, "queue=jira&text=", "application/x-www-form-urlencoded")
ok("0.8.0: ... und verschiebt ihn nach jira", st == 303 and app.STORE.get(n8)["queue"] == "jira", (st, b[-300:]))
st, b, _ = call("POST", f"/v/{n8}/status", {**TA, **ORIGIN}, "status=in_progress&text=", "application/x-www-form-urlencoded")
ok("0.8.0: der tenant_admin ändert den Status auch in jira", st == 303 and app.STORE.get(n8)["status"] == "in_progress",
   (st, b[-300:]))
ta = app.Who("u-ta", "TA", {"user", "tenant_admin"}, [])
ok("0.8.0: KÖDER: der tenant_admin übergibt nicht (Daten verlassen den Mandanten)",
   not ta.may("hand_over", "jira") and ta.may("move", "partner") and ta.may("read", "irgendwo"), None)
call("GET", "/", user("u-hans", name="Hans%20M%C3%BCller"))
ok("0.8.0: wer die App öffnet, steht in der Auswahl (Name dekodiert)",
   any(x["uid"] == "u-hans" and x["name"] == "Hans Müller" for x in app.STORE.people()), app.STORE.people())
st, b, _ = call("POST", "/bereiche/mitglied", {**TA, **ORIGIN}, "op=set&queue=jira&uid=u-niemand&kind=handler",
                "application/x-www-form-urlencoded")
ok("0.8.0: eine Person, die die App nie geöffnet hat: abgelehnt mit dem Weg",
   st == 400 and "öffnet die App" in b, (st, b[-300:]))
st, b, _ = call("POST", "/bereiche/mitglied", {**TA, **ORIGIN}, "op=set&queue=jira&uid=u-hans&kind=handler",
                "application/x-www-form-urlencoded")
ok("0.8.0: der tenant_admin trägt Hans als Bearbeiter von jira ein",
   st == 303 and app.STORE.memberships("u-hans") == {"jira": "handler"}, (st, app.STORE.memberships("u-hans")))
HANS = user("u-hans", name="Hans")
st, b, _ = call("POST", f"/v/{n8}/status", {**HANS, **ORIGIN}, "status=answered&text=Erledigt", "application/x-www-form-urlencoded")
ok("0.8.0: Hans (nur Zuständiger, kein Grant) beantwortet den Vorgang in jira",
   st == 303 and app.STORE.get(n8)["status"] == "answered", (st, b[-300:]))
st, b, _ = report(uid="u-melder", kind="question", title="Im Partner-Bereich")
n9 = json.loads(b)["number"]
app.STORE.move(app.STORE.get(n9), "u-ta", "TA", "partner")
st, b, _ = call("POST", f"/v/{n9}/status", {**HANS, **ORIGIN}, "status=triage&text=", "application/x-www-form-urlencoded")
st2, pg, _ = call("GET", f"/v/{n9}", HANS)
ok("0.8.0: KÖDER: Hans (Bearbeiter von jira) handelt nicht im Bereich partner",
   st in (400, 404) and app.STORE.get(n9)["status"] == "new" and st2 == 404, (st, st2))
hans = app.Who("u-hans", "Hans", {"user"}, [], {"jira": "handler"})
clr = app.Who("u-c", "C", {"user"}, [], {"jira": "clearing"})
ok("0.8.0: Bearbeiter/in übergibt nicht, Clearing schon (nur im eigenen Bereich)",
   not hans.may("hand_over", "jira") and clr.may("hand_over", "jira") and not clr.may("hand_over", "partner"), None)
st, b, _ = call("POST", "/bereiche/mitglied", {**HANS, **ORIGIN}, "op=set&queue=jira&uid=u-melder&kind=handler",
                "application/x-www-form-urlencoded")
ok("0.8.0: KÖDER: Zuständige tragen keine weiteren ein", st == 400 and "tenant_admin" in b, (st, b[-200:]))
st, pg, _ = call("GET", "/bereiche", TA)
ok("0.8.0: Seite Bereiche: Zuständige je Bereich, pflegen, letzte Änderungen",
   st == 200 and "Hans (Bearbeiter/in)" in pg and "Zuständige pflegen" in pg and "Zuletzt geändert" in pg,
   pg[-1200:])
st, pg, _ = call("GET", "/bereiche", HANS)
ok("0.8.0: ... ohne Pflege-Formular für Hans, mit „bearbeiten“ bei jira",
   st == 200 and "Zuständige pflegen" not in pg and "bearbeiten" in pg, None)
st, b, _ = call("POST", "/bereiche/mitglied", {**TA, **ORIGIN}, "op=remove&queue=jira&uid=u-hans",
                "application/x-www-form-urlencoded")
ok("0.8.0: entfernen: danach darf Hans in jira nichts mehr",
   st == 303 and app.STORE.memberships("u-hans") == {}, app.STORE.memberships("u-hans"))
st, b, _ = call("POST", "/bereiche", {**TA, **ORIGIN}, "id=finanzen&title=Finanzen", "application/x-www-form-urlencoded")
ok("0.8.0: der tenant_admin legt Bereiche an", st == 303 and app.STORE.queue("finanzen"), (st, b[-200:]))

# --- 0.9.0 (RFC-0103 Stufe 2): Link, alle eigenen Vorgänge, abnehmen aus dem Chat, ohne Rückmeldung
os.environ["OAAP_INSTANCE_NAMES"] = "http://vorgaenge.bec.example.org,https://alias.example.org"
ANNA = {**SOURCE, **user("u-anna")}
st, b, _ = report(kind="question", title="Aus dem Chat", subkind="chat-answer", fields={"rev": 1},
                  object={"kind": "conversation", "id": "conv-42"})
n10 = json.loads(b)["number"]
st, b, _ = call("GET", "/platform/feedback/mine", ANNA)
d = json.loads(b) if st == 200 else {}
row = (d.get("cases") or {}).get(n10) or {}
ok("0.9.0: /mine ohne numbers listet alle eigenen, mit Bezug und Link ohne Schema",
   st == 200 and row.get("object") == {"kind": "conversation", "id": "conv-42"}
   and row.get("url") == f"//vorgaenge.bec.example.org/v/{n10}" and row.get("title") == "Aus dem Chat"
   and d.get("accept_days") == 14, (st, row))
ok("0.9.0: ... neueste zuerst", list(d["cases"])[0] == n10, list(d["cases"])[:3])
st, b, _ = call("GET", "/platform/feedback/mine", {**SOURCE, **user("u-bert")})
ok("0.9.0: KÖDER 5: Bert listet nichts von Anna", st == 200 and n10 not in json.loads(b)["cases"], b[:300])
POSTJ = {**ANNA, **ORIGIN}
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/accept", POSTJ, {}, "application/json")
ok("0.9.0: abnehmen geht nur bei beantwortet (409 mit Satz)", st == 409 and "beantwortet" in b, (st, b))
app.STORE.set_status(app.STORE.get(n10), "u-x", "X", "answered", "So.")
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/accept", {**SOURCE, **user("u-trainer"), **ORIGIN}, {},
                "application/json")
ok("0.9.0: KÖDER 6: eine Bearbeiterin nimmt über den Weg des Chats nicht ab (404)",
   st == 404 and app.STORE.get(n10)["status"] == "answered", (st, b))
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/accept", {**user("u-anna"), **ORIGIN}, {}, "application/json")
ok("0.9.0: ohne Gateway-Herkunft: 404", st == 404, st)
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/accept", ANNA, {}, "application/json")
ok("0.9.0: fremde Seite (ohne Origin): 403", st == 403, st)
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/reopen", POSTJ, {"text": ""}, "application/json")
ok("0.9.0: „Passt nicht“ ohne Kommentar abgelehnt", st == 409 and "was nicht passt" in b, (st, b))
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/reopen", POSTJ, {"text": "Fehlt noch X"}, "application/json")
ok("0.9.0: „Passt nicht“ öffnet wieder, mit dem Kommentar im Verlauf",
   st == 200 and json.loads(b)["case"]["status"] == "reopened"
   and app.STORE.get(n10)["entries"][-1]["text"] == "Fehlt noch X", (st, b))
app.STORE.set_status(app.STORE.get(n10), "u-x", "X", "answered", "Jetzt mit X.")
st, b, _ = call("POST", f"/platform/feedback/mine/{n10}/accept", POSTJ, {}, "application/json")
cs = json.loads(b).get("case") or {}
ok("0.9.0: „Lösung passt“ aus dem Chat: geschlossen, Markierung accepted",
   st == 200 and cs.get("status") == "closed" and cs.get("mark") == "accepted"
   and app.STORE.get(n10)["mark"] == "accepted", (st, b))
st, pg, _ = call("GET", f"/v/{n10}", user("u-anna"))
ok("0.9.0: die Seite zeigt „geschlossen (Lösung passt)“", "geschlossen (Lösung passt)" in pg, pg[-500:])
app.STORE.set_status(app.STORE.get(n10), "u-anna", "Anna", "reopened", "Doch nicht")
ok("0.9.0: Wiederöffnen leert die Markierung", app.STORE.get(n10)["mark"] == "", app.STORE.get(n10)["mark"])
# die Seite: Knöpfe nur für die Meldende bei beantwortet
st, b, _ = report(kind="question", title="Seite")
n11 = json.loads(b)["number"]
app.STORE.set_status(app.STORE.get(n11), "u-x", "X", "answered", "So.")
st, pg, _ = call("GET", f"/v/{n11}", user("u-anna"))
ok("0.9.0: die Meldende sieht „Lösung passt“ und „Passt nicht“ auf der Seite, keinen Rechte-Hinweis",
   "/accept'" in pg and "/reopen'" in pg and "Den Status kannst du hier nicht ändern" not in pg, pg[-900:])
st, pg, _ = call("GET", f"/v/{n11}", TA)
ok("0.9.0: der tenant_admin sieht die Knöpfe der Meldenden nicht", "/accept'" not in pg, None)
st, b, _ = call("POST", f"/v/{n11}/accept", {**TA, **ORIGIN}, "")
ok("0.9.0: ... und nimmt auch über die Seite nicht ab", st == 400 and app.STORE.get(n11)["status"] == "answered",
   (st, b[-200:]))
st, b, _ = call("POST", f"/v/{n11}/status", {**TA, **ORIGIN}, "status=closed&text=", "application/x-www-form-urlencoded")
ok("0.9.0: schließt ein Bearbeitender, bleibt die Markierung leer",
   st == 303 and app.STORE.get(n11)["status"] == "closed" and app.STORE.get(n11)["mark"] == "", (st, b[-200:]))
# ohne Rückmeldung (D11, Köder 7)
st, b, _ = report(kind="question", title="Still")
n12 = json.loads(b)["number"]
st, b, _ = report(kind="question", title="Frisch")
n13 = json.loads(b)["number"]
for n in (n12, n13):
    app.STORE.set_status(app.STORE.get(n), "u-x", "X", "answered", "So.")
import time as _t  # noqa: E402
with app.STORE.lock, app.STORE.db:
    old = _t.strftime("%Y-%m-%dT%H:%M:%SZ", _t.gmtime(_t.time() - 15 * 86400))
    app.STORE.db.execute("UPDATE cases SET updated=? WHERE number=?", (old, n12))
done = app.STORE.close_unanswered(14)
c12 = app.STORE.get(n12)
ok("0.9.0: KÖDER 7: 15 Tage still -> geschlossen als no_response, nie accepted",
   done == [n12] and c12["status"] == "closed" and c12["mark"] == "no_response"
   and c12["entries"][-1]["actor_name"] == "Automatisch", (done, c12["status"], c12["mark"]))
ok("0.9.0: ein frisch beantworteter bleibt offen", app.STORE.get(n13)["status"] == "answered", None)
ok("0.9.0: Einstellung VORGAENGE_ACCEPT_DAYS: Unsinn -> 14, 7 -> 7",
   app.accept_days("abc") == 14 and app.accept_days("7") == 7 and app.accept_days("0") == 14, None)
del os.environ["OAAP_INSTANCE_NAMES"]
ok("0.9.0: ohne öffentlichen Namen kein Link", app.case_url(n12) == "", app.case_url(n12))

# --- 0.10.0 (RFC-0103 Stufe 3): verantwortlich, zugewiesen, Unterstatus, interne Kommentare
MIA, OTTO = user("u-mia", name="Mia"), user("u-otto", name="Otto")
for h in (MIA, OTTO):
    call("GET", "/", h)                     # wer die App öffnet, steht in der Auswahl
call("POST", "/bereiche", {**TA, **ORIGIN}, "id=s3&title=Stufe+drei")
call("POST", "/bereiche/mitglied", {**TA, **ORIGIN}, "queue=s3&uid=u-mia&op=set&kind=handler")
st, b, _ = report(kind="question", title="Stufe drei")
n20 = json.loads(b)["number"]
call("POST", f"/v/{n20}/move", {**TA, **ORIGIN}, "queue=s3")
c20 = lambda: app.STORE.get(n20)  # noqa: E731


def post(h, action, form):
    return call("POST", f"/v/{n20}/{action}", {**h, **ORIGIN}, form)


st, b, _ = post(MIA, "rolle", "role=responsible&uid=u-mia")
ok("0.10.0: Mia (Zuständige von s3) übernimmt die Verantwortung, mit Eintrag im Verlauf",
   st == 303 and c20()["responsible"] == "u-mia" and c20()["entries"][-1]["type"] == "role", (st, b[-300:]))
st, b, _ = post(TA, "rolle", "role=responsible&uid=u-otto")
ok("0.10.0: verantwortlich wird nur ein Zuständiger des Bereichs (Otto nicht)",
   st == 400 and "Zuständiger" in b and c20()["responsible"] == "u-mia", (st, b[-300:]))
st, b, _ = post(MIA, "rolle", "role=assignee&uid=u-otto")
ok("0.10.0: Mia weist Otto zu (irgendwer im Mandanten)", st == 303 and c20()["assignee"] == "u-otto", (st, b[-300:]))
st, pg, _ = call("GET", "/", OTTO)
ok("0.10.0: Ottos Übersicht: „Mir zugewiesen“ mit dem Vorgang", "Mir zugewiesen" in pg and n20 in pg, pg[-800:])
st, pg, _ = call("GET", f"/v/{n20}", OTTO)
ok("0.10.0: Otto (außerhalb des Bereichs) sieht den Vorgang, Verantwortlich Mia, Zugewiesen Otto",
   st == 200 and "Verantwortlich: <b>Mia</b>" in pg and "Zugewiesen: <b>Otto</b>" in pg, (st, pg[-600:]))
ok("0.10.0: ... und bekommt weder Verschieben noch Rollen noch „geschlossen“ angeboten",
   "/move'" not in pg and "/rolle'" not in pg and "value=closed" not in pg and "value=rejected" not in pg, None)
st, b, _ = post(OTTO, "status", "status=waiting_test&text=")
ok("0.10.0: Otto ändert den Status (wartet auf Test)", st == 303 and c20()["status"] == "waiting_test", (st, b[-300:]))
st1, _, _ = post(OTTO, "move", "queue=jira")
st2, _, _ = post(OTTO, "rolle", "role=assignee&uid=u-anna")
st3, _, _ = post(OTTO, "status", "status=rejected&text=nein")
st4, _, _ = call("GET", f"/v/{n20}/uebergabe?partner=x", OTTO)
post(OTTO, "status", "status=answered&text=So.")
st5, _, _ = post(OTTO, "status", "status=closed&text=")
ok("0.10.0: KÖDER 3: Otto verschiebt nicht, weist nicht weiter zu, lehnt nicht ab, übergibt nicht, schließt nicht",
   (st1, st2, st3, st4, st5) == (400, 400, 400, 403, 400) and c20()["queue"] == "s3"
   and c20()["assignee"] == "u-otto" and c20()["status"] == "answered", (st1, st2, st3, st4, st5, c20()["status"]))
post(MIA, "status", "status=in_progress&text=")
# interne Kommentare (D5, Köder 4)
st, pg, _ = call("GET", f"/v/{n20}", MIA)
ok("0.10.0: für Bearbeitende ist „intern“ vorbelegt", "name=internal value=1 checked" in pg, None)
st, b, _ = post(MIA, "comment", "text=Intern%3A+Kunde+ist+schwierig&internal=1")
st, pg, _ = call("GET", f"/v/{n20}", user("u-anna"))
ok("0.10.0: KÖDER 4: die Meldende sieht den internen Kommentar nicht, und kein Häkchen „intern“",
   st == 200 and "schwierig" not in pg and "name=internal" not in pg, pg[-600:])
ok("0.10.0: ... und weder, wer verantwortlich oder zugewiesen ist, noch die Wechsel dazu",
   "Verantwortlich:" not in pg and "zugewiesen: " not in pg and "verantwortlich: " not in pg, pg[-900:])
st, pg, _ = call("GET", f"/v/{n20}", OTTO)
ok("0.10.0: die Zugewiesene liest ihn, als intern bezeichnet", "schwierig" in pg and ">intern</span>" in pg, None)
st, b, _ = post(user("u-anna"), "comment", "text=x&internal=1")
ok("0.10.0: die Meldende kommentiert nicht intern", st == 400, st)
st, b, _ = call("GET", "/platform/feedback/mine", {**SOURCE, **user("u-anna")})
ok("0.10.0: /mine trägt keinen Verlauf (nichts Internes)", "schwierig" not in b, b[:300])
# Rückfrage (D4)
st, b, _ = post(MIA, "status", "status=waiting_reporter&text=Welche+Version%3F")
st, b, _ = call("GET", "/platform/feedback/mine", {**SOURCE, **user("u-anna")})
row = json.loads(b)["cases"][n20]
ok("0.10.0: Rückfrage: /mine sagt waiting_reporter, „Rückfrage an dich“",
   row["status"] == "waiting_reporter" and row["label"] == "Rückfrage an dich", row)
st, pg, _ = call("GET", f"/v/{n20}", user("u-anna"))
ok("0.10.0: ... die Seite sagt es ihr auch, mit dem Weg (Kommentar)",
   "Rückfrage an dich" in pg and "antworte im Reiter <a href='#verlauf'>Verlauf</a> mit" in pg, pg[-900:])
post(user("u-anna"), "comment", "text=Version+3")
ok("0.10.0: antwortet die Meldende, geht der Vorgang von selbst nach „in Arbeit“",
   c20()["status"] == "in_progress" and c20()["entries"][-1]["actor_name"] == "Automatisch", c20()["status"])
post(MIA, "comment", "text=noch+intern&internal=1")
post(MIA, "status", "status=waiting_reporter&text=Und+die+Uhrzeit%3F")
post(MIA, "comment", "text=nur+intern&internal=1")
ok("0.10.0: ein interner Kommentar der Bearbeitenden beendet die Rückfrage nicht",
   c20()["status"] == "waiting_reporter", c20()["status"])
ok("0.10.0: Unterstatus haben dieselben Wege wie „in Arbeit“",
   store.NEXT["new"][:7] == ("triage", "in_progress", "clarify_internal", "clarify_external", "waiting_test",
                             "waiting_reporter", "waiting_approval") and "answered" in store.NEXT["clarify_external"]
   and store.NEXT["in_progress"][:len(store.SUBSTATUS)] == store.SUBSTATUS
   and all(x in store.OPEN for x in store.SUBSTATUS), store.NEXT["new"])
# verschieben leert „verantwortlich“, die Zuweisung bleibt
post(TA, "move", "queue=jira")
ok("0.10.0: verschieben leert „verantwortlich“, „zugewiesen“ bleibt",
   c20()["responsible"] == "" and c20()["assignee"] == "u-otto"
   and c20()["entries"][-1]["data"].get("responsible_cleared") == "u-mia", (c20()["responsible"], c20()["assignee"]))
# frühere Beteiligte lesen, ändern nicht (Köder 8)
post(TA, "rolle", "role=assignee&uid=")
st, pg, _ = call("GET", f"/v/{n20}", OTTO)
ok("0.10.0: der frühere Zugewiesene liest weiter, interne Kommentare eingeschlossen, steht unter „Beteiligt“",
   st == 200 and "schwierig" in pg and "Beteiligt: " in pg and "/status'" not in pg, (st, pg[-700:]))
st1, _, _ = post(OTTO, "status", "status=answered&text=x")
st2, _, _ = post(OTTO, "comment", "text=noch+was")
ok("0.10.0: KÖDER 8: ein früherer Zugewiesener ändert den Status nicht und kommentiert nicht",
   st1 == 400 and st2 == 400 and c20()["status"] == "waiting_reporter", (st1, st2))
st, pg, _ = call("GET", f"/v/{n20}", MIA)
ok("0.10.0: Mia (Bereich s3, jetzt Beobachterin, früher verantwortlich) liest weiter", st == 200, st)
app.STORE.set_status(app.STORE.get(n20), "u-x", "X", "rejected", "Nein.")
try:
    app.STORE.set_role(app.STORE.get(n20), "assignee", "u-otto", "u-ta", "TA")
    ended_ok = False
except store.Refused:
    ended_ok = True
ok("0.10.0: ein beendeter Vorgang bekommt niemanden mehr zugeteilt", ended_ok, None)

# --- 0.11.0 (RFC-0103 Stufe 4): Listenbericht und Reiter
import re as _re  # noqa: E402


def numbers(pg):
    return _re.findall(r"<tr><td><a href='/v/([^']+)'>", pg)


st, b, _ = report(kind="question", title="Stufe vier")
n21 = json.loads(b)["number"]
call("POST", f"/v/{n21}/move", {**TA, **ORIGIN}, "queue=s3")
call("POST", f"/v/{n21}/rolle", {**MIA, **ORIGIN}, "role=responsible&uid=u-mia")
st, b, _ = report(kind="question", title="Stufe vier, jira")
n22 = json.loads(b)["number"]
call("POST", f"/v/{n22}/move", {**TA, **ORIGIN}, "queue=jira")
call("POST", f"/v/{n20}/status", {**TA, **ORIGIN}, "status=reopened&text=")
call("POST", f"/v/{n20}/status", {**TA, **ORIGIN}, "status=in_progress&text=")
call("POST", f"/v/{n22}/comment", {**TA, **ORIGIN}, "text=neuester&internal=1")   # n22 zuletzt geändert

st, pg, _ = call("GET", "/?queue=jira&status=open", TA)
got = numbers(pg)
want = [r["number"] for r in sorted((r for r in app.STORE.listing(queues=("jira",), open_only=True)
                                     if r["queue"] == "jira"), key=lambda r: (r["updated"], r["number"]),
                                    reverse=True)]
ok("0.11.0: STUFE 4: /?queue=jira&status=open zeigt nur offene in jira, nach letzter Änderung sortiert",
   st == 200 and got == want and len(got) >= 2 and got[0] == n22, (got, want))
st, pg, _ = call("GET", "/?queue=jira&status=open&sort=updated", TA)
ok("0.11.0: sort=updated dreht die Reihenfolge um", numbers(pg) == list(reversed(want)), numbers(pg))
st, pg, _ = call("GET", "/?queue=jira&status=open", TA)
ok("0.11.0: der aktive Spaltenkopf trägt den Pfeil und den Link für die andere Richtung",
   "Geändert ▼" in pg and "sort=updated" in pg and "aria-sort=descending" in pg, pg[:200])
ok("0.11.0: die Filter stehen in einem GET-Formular (Adresse = Lesezeichen)",
   "<form method=get action='/' class=filter>" in pg and "<option value='jira' selected>" in pg, None)
st, pg, _ = call("GET", "/?status=rejected&sort=bogus&queue=nix", TA)
ok("0.11.0: unbekannte Sortierung fällt auf „letzte Änderung“ zurück, ein fremder Bereich zeigt nichts",
   st == 200 and numbers(pg) == [], numbers(pg))
st, pg, _ = call("GET", "/?status=in_progress", TA)
ok("0.11.0: ein einzelner Status als Filter", n20 in numbers(pg) and n21 not in numbers(pg)
   and all(app.STORE.get(n)["status"] == "in_progress" for n in numbers(pg)), numbers(pg))
st, pg, _ = call("GET", "/?view=assigned", MIA)
ok("0.11.0: „mir zugewiesen oder von mir verantwortet“ für Mia: V-…21", numbers(pg) == [n21], numbers(pg))
st, pg, _ = call("GET", "/?status=open&sort=-responsible", TA)
row = pg[pg.index(f"/v/{n21}'"):]
row = row[:row.index("</tr>")]
ok("0.11.0: die Spalten Verantwortlich und Zugewiesen für die Bearbeitenden", "<td>Mia</td>" in row, row)
st, pg, _ = call("GET", "/?view=mine&sort=responsible", user("u-anna"))
row = pg[pg.index(f"/v/{n21}'"):]
row = row[:row.index("</tr>")]
ok("0.11.0: die Meldende sieht ihre Meldung, aber nicht, wer sie bearbeitet (wie auf der Seite)",
   n21 in numbers(pg) and "Mia" not in row and n22 in numbers(pg), row)
ok("0.11.0: ... und nur ihre eigenen", all(app.STORE.get(n)["reporter"] == "u-anna" for n in numbers(pg)),
   numbers(pg))
st, pg, _ = call("GET", "/?status=all", OTTO)
ok("0.11.0: der frühere Zugewiesene findet den Vorgang in der Übersicht (er liest ihn ja weiter)",
   n20 in numbers(pg), numbers(pg))
st, pg, _ = call("GET", "/?status=all&sort=title", user("u-bob"))
ok("0.11.0: KÖDER 2 im Listenbericht: Bob ohne Bereich sieht keinen dieser Vorgänge",
   not ({n20, n21, n22} & set(numbers(pg))), numbers(pg))
# Reiter
st, pg, _ = call("GET", f"/v/{n22}", TA)
ok("0.11.0: Reiter Überblick, Verlauf, Intern für die Bearbeitenden",
   all(f"href='#{k}'" in pg for k in ("ueberblick", "verlauf", "intern")) and "id=intern" in pg, None)
intern = pg[pg.index("id=intern"):pg.index("id=ueberblick")]
verlauf = pg[pg.index("id=verlauf"):pg.index("id=intern")]
ok("0.11.0: der interne Kommentar steht im Reiter „Intern“, nicht im „Verlauf“",
   "neuester" in intern and "neuester" not in verlauf, None)
ok("0.11.0: der Überblick steht zuletzt im Dokument (CSS stellt ihn nach vorn) und trägt die Formulare",
   pg.index("id=ueberblick") > pg.index("id=intern") and "/status'" in pg[pg.index("id=ueberblick"):], None)
st, pg, _ = call("GET", f"/v/{n22}", user("u-anna"))
ok("0.11.0: KÖDER 4: die Meldende hat keinen Reiter „Intern“ und nichts Internes im Dokument",
   st == 200 and "#intern" not in pg and "id=intern" not in pg and "neuester" not in pg, None)
ok("0.11.0: Technik nur, wo es etwas gibt: die Meldende sieht keine Browser-Angaben",
   "#technik" not in pg, None)
st, pg, _ = call("GET", f"/v/{n22}", TA)
ok("0.11.0: der Bereich sieht den Reiter Technik mit den Browser-Angaben",
   "href='#technik'" in pg and "x is undefined" in pg[pg.index("id=technik"):], None)
ok("0.11.0: kein Reiter Übergabe ohne Partner (nur fürs Clearing)", "#uebergabe" not in pg, None)
st, _, h = call("POST", f"/v/{n22}/comment", {**TA, **ORIGIN}, "text=Noch+intern&internal=1")
st2, _, h2 = call("POST", f"/v/{n22}/comment", {**user("u-anna"), **ORIGIN}, "text=Öffentlich")
ok("0.11.0: nach dem Kommentieren zurück in den Reiter, in dem er steht",
   h.get("Location", "").endswith("#intern") and h2.get("Location", "").endswith("#verlauf"), (h, h2))
# /mine: die Zahl der zugeordneten Vorgänge, mit Link -- nichts sonst (D9)
os.environ["OAAP_INSTANCE_NAMES"] = "http://vorgaenge.bec.example.org"
st, b, _ = call("GET", "/platform/feedback/mine", {**SOURCE, **MIA})
d = json.loads(b)
ok("0.11.0: /mine nennt die Zahl der offenen zugeordneten Vorgänge und den Link dorthin",
   d["assigned"] == 1 and d["assigned_url"] == "//vorgaenge.bec.example.org/?view=assigned"
   and d["overview_url"] == "//vorgaenge.bec.example.org/?view=mine", d)
ok("0.11.0: ... aber keinen dieser Vorgänge (Mia hat keinen gemeldet)", d["cases"] == {}, d["cases"])
del os.environ["OAAP_INSTANCE_NAMES"]
st, b, _ = call("GET", "/platform/feedback/mine", {**SOURCE, **MIA})
ok("0.11.0: ohne öffentlichen Namen kein Link", json.loads(b)["assigned_url"] == "", b[:200])

srv.shutdown()
print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
