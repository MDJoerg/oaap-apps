"""0.22.0 (RFC-0134 Stufe 2, §5): Aufträge des Leiters an Spezialisten.

    py -3 apps/vorgaenge/test_tasks.py

Geprüft: nur der verantwortliche Spezialist gibt Aufträge, nur an andere Spezialisten; einer zur
Zeit; höchstens VORGAENGE_TASKS_MAX je Vorgang; der Beauftragte liest den Vorgang, schreibt genau
ein Ergebnis und wird nie verantwortlich (Tiefe eins); Aufträge zählen nicht als Rückfragen; die
meldende Person sieht nichts davon.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="vorgaenge-tasks-")
os.environ["VORGAENGE_DATA_DIR"] = DATA
os.environ["VORGAENGE_TASKS_MAX"] = "3"

import app  # noqa: E402
import store  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


# Der Leiter hält „Bearbeiter/in“ in sap-support; die Mitglieder seines Teams haben keinen Bereich --
# sie lesen den Vorgang nur, weil sie einen Auftrag bekommen.
GRANTS = {"lead": [{"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
                    "fields": {"queue": ["sap-support"]}}]}


class FakeAuthz:
    url = "http://fake"

    def grants(self, uid):
        return GRANTS.get(uid, [])


app.STORE = store.Store(os.path.join(DATA, "t.db"))
app.AUTHZ = FakeAuthz()
srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
S = app.STORE
S.add_queue("sap-support", "SAP-Support")


def person(uid, roles="user", name=None):
    return {"X-OAAP-User-Id": uid, "X-OAAP-Roles": roles, "X-OAAP-Display-Name": name or uid.title(),
            "X-OAAP-User": uid}


def call(method, path, who, body=None, ctype="application/json", extra=None):
    h = dict(person(*who) if isinstance(who, tuple) else person(who))
    h.update(extra or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if ctype == "application/json"
                                                     else urllib.parse.urlencode(body).encode())
        h["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)

    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedir).open(req, timeout=10) as r:
            b = r.read().decode()
            return r.status, (json.loads(b) if b.startswith("{") else b)
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        return e.code, (json.loads(b) if b.startswith("{") else b)


def form(path, who, fields):
    return call("POST", path, who, fields, ctype="application/x-www-form-urlencoded",
                extra={"Origin": BASE, "Host": BASE.split("//")[1]})


ADMIN = ("chef", "user,tenant_admin")
LEAD = ("lead", "user", "Leitung SAP (KI)")
ANA = ("ana", "user", "Analyse (KI)")
WEB = ("web", "user", "Recherche (KI)")
for w in (LEAD, ANA, WEB):
    call("GET", "/api/spez/turn", w)          # meldet sich als Spezialist an
call("GET", "/", ("bea", "user"))
S.set_member("sap-support", "bea", "handler", "chef")
st, _ = form("/bereiche/spezialist", ADMIN, {"queue": "sap-support", "uid": "lead"})
ok("der Leiter ist Erstbearbeitung von sap-support", st in (302, 303), st)

c = S.create(kind="question", title="SU53 nach ME21N", body="Benutzer KLAUS bekommt keine Berechtigung.",
             reporter="sap", reporter_name="SAP A4H", queue="sap-support")
N = c["number"]
st, d = call("GET", f"/api/spez/case/{N}", LEAD)
ok("der Leiter sieht, wem er Aufträge geben kann: andere Spezialisten, keine Menschen, nicht sich",
   st == 200 and sorted((p["name"], p["uid"]) for p in d["taskable"]) == [("Analyse (KI)", "ana"),
                                                                           ("Recherche (KI)", "web")]
   and d["tasks_used"] == 0 and d["tasks_max"] == 3, d)
st, d = call("GET", f"/api/spez/case/{N}", ANA)
ok("KÖDER: ein Spezialist ohne Bereich und ohne Auftrag liest den Vorgang nicht", st == 404, st)

st, d = call("POST", f"/api/spez/case/{N}/task", LEAD, {"to": "Bea", "text": "Schau mal"})
ok("KÖDER: ein Mensch bekommt keinen Auftrag (dafür gibt es die Rückfrage)", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{N}/task", ("bea", "user"), {"to": "Analyse (KI)", "text": "x"})
ok("KÖDER: wer nicht verantwortlich ist, gibt keinen Auftrag", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{N}/task", LEAD,
             {"to": "Analyse (KI)", "text": "Prüfe die Rollen von KLAUS und SU53.", "run": "L1",
              "assistant": "leitung@1"})
T1 = d.get("task")
c = S.get(N)
ok("Auftrag gegeben: Status „wartet auf Auftrag“, Eintrag intern mit Nummer, Beauftragter beteiligt",
   st == 200 and c["status"] == "waiting_task" and "ana" in c["involved"]
   and any(e["type"] == "task" and e["internal"] and e["seq"] == T1 and e["data"].get("n") == 1
           and e["data"].get("run") == "L1" for e in c["entries"]), (st, d, c["status"]))
st, d = call("POST", f"/api/spez/case/{N}/task", LEAD, {"to": "Recherche (KI)", "text": "Suche im Netz."})
ok("KÖDER: ein zweiter Auftrag, solange einer offen ist (D4: einer zur Zeit)", st == 409, (st, d))

st, d = call("GET", "/api/spez/turn", ANA)
ok("der Beauftragte ist an der Reihe, als Auftrag (nicht als Rückfrage)",
   any(x["number"] == N and x.get("task") == T1 and "consult" not in x for x in d["cases"]), d)
st, d = call("GET", f"/api/spez/case/{N}", ANA)
ok("der Beauftragte liest den Vorgang: der Auftrag an ihn, nicht verantwortlich, niemandem Aufträge geben",
   st == 200 and not d["mine"] and d["tasks_open"] == [{"seq": T1, "asked_by": "Leitung SAP (KI)",
                                                        "task": "Prüfe die Rollen von KLAUS und SU53."}]
   and d["consults_open"] == [] and d["taskable"] == [] and d["consultable"] == [], d)
st, d = call("POST", f"/api/spez/case/{N}/task", ANA, {"to": "Recherche (KI)", "text": "mach du"})
ok("KÖDER: der Beauftragte gibt keine Aufträge weiter (Tiefe eins)", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{N}/entry", ANA, {"kind": "note", "text": "ich schreibe Schritte"})
ok("KÖDER: der Beauftragte schreibt keine Schritte in den Vorgang", st == 409, (st, d))

st, d = call("POST", f"/api/spez/case/{N}/answer", ANA,
             {"consult": T1, "text": "Ursache: M_BEST_EKO fehlt (EKORG 1000).", "run": "A1", "assistant": "analyse@2"})
c = S.get(N)
res = [e for e in c["entries"] if e["type"] == "task_result"]
ok("Ergebnis: genau ein interner Eintrag task_result, Status zurück auf in Arbeit",
   st == 200 and len(res) == 1 and res[0]["internal"] and res[0]["data"].get("consult") == T1
   and c["status"] == "in_progress" and c["responsible"] == "lead", (st, d, c["status"]))
st, d = call("POST", f"/api/spez/case/{N}/answer", ANA, {"consult": T1, "text": "noch was"})
ok("KÖDER: ein zweites Ergebnis wird abgelehnt", st == 409, (st, d))
st, d = call("GET", "/api/spez/turn", LEAD)
ok("der Leiter ist wieder dran (der letzte Eintrag ist nicht seiner)",
   any(x["number"] == N and (x["last"] or {}).get("actor") != "lead" for x in d["cases"]), d)
st, d = call("GET", f"/api/spez/case/{N}", LEAD)
kinds = [(e["type"], (e.get("consult") or {}).get("n")) for e in d["entries"] if e["type"] in ("task", "task_result")]
ok("der Leiter liest Auftrag und Ergebnis im Verlauf", kinds == [("task", 1), ("task_result", None)], kinds)

# Rückfragen und Aufträge zählen getrennt: eine Rückfrage an einen Spezialisten geht trotz Auftrag
st, d = call("POST", f"/api/spez/case/{N}/consult", LEAD, {"to": "Recherche (KI)", "text": "Kennst du das?"})
ok("eine Rückfrage geht weiterhin (zählt nicht als Auftrag)", st == 200 and S.get(N)["status"] == "waiting_consult",
   (st, d))
K = d.get("consult")
call("POST", f"/api/spez/case/{N}/answer", WEB, {"consult": K, "text": "Ja, Rolle Z_EINKAUF."})
ok("Antwort auf die Rückfrage heißt consult_answer, nicht task_result",
   [e["type"] for e in S.get(N)["entries"] if e["type"] in ("consult_answer", "task_result")]
   == ["task_result", "consult_answer"], "")

# Grenze: VORGAENGE_TASKS_MAX=3
for i, who in ((2, "Recherche (KI)"), (3, "Analyse (KI)")):
    st, d = call("POST", f"/api/spez/case/{N}/task", LEAD, {"to": who, "text": f"Auftrag {i}"})
    ok(f"Auftrag {i} gegeben", st == 200, (st, d))
    tgt = WEB if who.startswith("Recherche") else ANA
    st, _ = call("POST", f"/api/spez/case/{N}/answer", tgt, {"consult": d.get("task"), "text": f"Ergebnis {i}"})
st, d = call("POST", f"/api/spez/case/{N}/task", LEAD, {"to": "Recherche (KI)", "text": "Auftrag 4"})
ok("Grenze: der vierte Auftrag wird abgelehnt, mit dem Weg (abschließen, markieren, zurückgeben)",
   st == 409 and "Grenze 3" in d.get("error", "") and "zurückgeben" in d.get("error", ""), (st, d))

# Stopp
c2 = S.create(kind="question", title="Zweiter Fall", body="", reporter="sap", reporter_name="SAP A4H",
              queue="sap-support")
form(f"/v/{c2['number']}/spezstop", ("bea", "user"), {"on": "1"})
st, d = call("POST", f"/api/spez/case/{c2['number']}/task", LEAD, {"to": "Analyse (KI)", "text": "x"})
ok("KÖDER: nach dem Stopp gibt der Leiter keinen Auftrag", st == 409, (st, d))

# Was Menschen sehen
st, page = call("GET", f"/v/{N}", ("bea", "user"))
ok("die Bearbeiterin sieht Auftrag und Ergebnis intern", "Auftrag 1 an Analyse (KI)" in page
   and "Ergebnis des Auftrags" in page and "M_BEST_EKO" in page, "")
ok("KÖDER: „wartet auf Auftrag“ steht nicht im Statusformular",
   "value=waiting_task" not in page and "value='waiting_task'" not in page, "")
st, page = call("GET", f"/v/{N}", ("sap", "user"))
ok("KÖDER: die meldende Seite sieht keinen Auftrag", "M_BEST_EKO" not in page and "Auftrag 1" not in page, "")

ok("tasks_max: unsinnige Einstellung fällt auf 8 zurück", app.tasks_max("99") == 8 and app.tasks_max("x") == 8
   and app.tasks_max("2") == 2, "")

print(f"\n{'OK' if not fails else f'{fails} FEHLER'}")
sys.exit(1 if fails else 0)
