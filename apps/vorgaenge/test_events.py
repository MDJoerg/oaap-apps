"""Vorgaenge 0.20: der Status eines Vorgangs aus dem Backend einer App geht als
adressiertes Ereignis an diese App zurueck (RFC-0127 Stufe 1.5, D12).

    python3 apps/vorgaenge/test_events.py

Geprueft ohne Plattform: wann der Ereignis-Ausgang sich fuellt (nur Backend-
Vorgaenge, nur mit Schluessel, in derselben Transaktion wie der Wechsel), was
im Ereignis steht (duenn, adressiert) und wie der Ausgang auf die Antworten
der Tuer reagiert. Der echte Weg durch die Tuer ist auf oaap-test gemessen
(RFC-0127 §1.11).
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["VORGAENGE_DATA_DIR"] = tempfile.mkdtemp(prefix="vorgaenge-ev-")

import app  # noqa: E402
import store  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


def new_store(events=True):
    return store.Store(os.path.join(tempfile.mkdtemp(prefix="vg-"), "v.db"), events=events)


BACKEND = {"instance": "bestand-cds", "app": "bestand", "submitted_by": "app:bestand-cds"}


def backend_case(s, obj=None):
    made = s.create(kind="internal", title="Auftrag 17", body="", reporter="app:bestand-cds",
                    reporter_name="App bestand", obj=obj, attested=BACKEND)
    return s.get(made["number"])


def rows(s):
    return s.event_outbox_due(now=10 ** 12)


print("\n=== Wann sich der Ausgang fuellt ===")
s = new_store()
c = backend_case(s, obj={"kind": "bundle", "id": "B-17"})
ok("das Anlegen allein meldet nichts", rows(s) == [])
s.set_status(c, "u1", "Bea", "in_progress")
r = rows(s)
ok("ein Statuswechsel -> ein Ereignis im Ausgang", len(r) == 1, r)
doc = json.loads(r[0]["doc"])
ok("adressiert an die eroeffnende Instanz, Typ und Subjekt",
   doc["type"] == "app.vorgaenge.case.statuschanged.v1" and doc["oaapto"] == "instance:bestand-cds"
   and doc["subject"] == c["number"] and r[0]["addressee"] == "bestand-cds")
ok("duenn: Nummer, von, nach, das Objekt der App -- kein Titel, keine Person",
   doc["data"] == {"case": c["number"], "from": "new", "to": "in_progress",
                   "object": {"kind": "bundle", "id": "B-17"}}, doc["data"])
ok("kein source, id, time -- die setzt die Plattform", not {"source", "id", "time"} & set(doc))

s2 = new_store()
made = s2.create(kind="question", title="Frage", body="", reporter="u9", reporter_name="Ute",
                 attested={"instance": "chat", "app": "chat"}, allowed_kinds=store.KINDS)
c2 = s2.get(made["number"])
s2.set_status(c2, "u1", "Bea", "in_progress")
ok("ein Vorgang des Feedback-Knopfs (nicht aus dem Backend) meldet nichts", rows(s2) == [])
made = s2.create(kind="internal", title="Falsch", body="", reporter="x", reporter_name="x",
                 attested={"instance": "a", "app": "a", "submitted_by": "app:b"})
s2.set_status(s2.get(made["number"]), "u1", "Bea", "in_progress")
ok("submitted_by passt nicht zur Quelle -> nichts", rows(s2) == [])

s3 = new_store(events=False)
c3 = backend_case(s3)
s3.set_status(c3, "u1", "Bea", "in_progress")
ok("ohne Ereignisschluessel (events=False) fuellt sich nichts", rows(s3) == [])

c = s.get(c["number"])
try:
    s.set_status(c, "u1", "Bea", "closed")      # nicht erlaubt von in_progress
except store.Refused:
    pass
ok("ein abgelehnter Wechsel meldet nichts (dieselbe Transaktion)", len(rows(s)) == 1)
s.set_status(s.get(c["number"]), "u1", "Bea", "answered")
ok("jeder weitere Wechsel ein weiteres Ereignis, in Reihenfolge",
   [json.loads(x["doc"])["data"]["to"] for x in rows(s)] == ["in_progress", "answered"])

print("\n=== Der Ausgang und die Tuer ===")
answers = iter([("sent", ""), ("retry", "HTTP 503")])
app.flush_events(s, send=lambda raw: next(answers))
due = s.event_outbox_due(now=10 ** 12)
ok("202 -> erledigt; 503 -> bleibt, mit Wartezeit und Grund",
   len(due) == 1 and due[0]["attempts"] == 1 and due[0]["last_error"] == "HTTP 503"
   and s.event_outbox_due() == [], due)
with s.db:
    s.db.execute("UPDATE event_outbox SET next_try = 0")
app.flush_events(s, send=lambda raw: ("refused", "HTTP 403 gave nothing to address"))
ok("eine endgueltige Absage (4xx) wird nicht wiederholt, der Grund bleibt",
   s.event_outbox_due(now=10 ** 12) == [] and
   s.db.execute("SELECT done, last_error FROM event_outbox ORDER BY seq DESC LIMIT 1").fetchone()["last_error"]
   .startswith("HTTP 403"))

print("\n=== Paket ===")
import yaml  # noqa: E402
m = yaml.safe_load(open(os.path.join(HERE, "oaap-app.yaml"), encoding="utf-8"))
pub = m["events"]["publishes"][0]
ok("Manifest 0.21 erklaert den Typ adressiert, Schema liegt im Paket",
   m["oaap_manifest"] == "0.21" and pub["audience"] == "addressed"
   and pub["type"] == store.Store.EVENT_TYPE and os.path.isfile(os.path.join(HERE, pub["schema"])))
sch = json.load(open(os.path.join(HERE, pub["schema"]), encoding="utf-8"))
ok("das Schema verlangt Nummer und Ziel, sonst nichts", sch["required"] == ["case", "to"]
   and sch["additionalProperties"] is False)

print()
print("all event checks passed" if not fails else f"{fails} check(s) FAILED")
sys.exit(1 if fails else 0)
