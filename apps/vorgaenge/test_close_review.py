"""0.23.0 (RFC-0134 Stufe 3, §7/§8): der Leiter schließt mit Beleg; das Prüfmerkmal und die Prüfung.

    py -3 apps/vorgaenge/test_close_review.py

Geprüft: der Leiter schließt nur Vorgänge, die ein Tor geliefert hat, und nur mit Beleg (ausgeführter,
freigegebener, nicht fehlgeschlagener Aufruf) oder Begründung (dann zur Prüfung); der Schließtext ist das,
was das andere System liest; die Plattform markiert bei Grenze, aufgehobener Freigabe und als Stichprobe;
Leiter und Menschen markieren; Menschen und der prüfende Spezialist prüfen, nie die eigene Arbeit;
„Nacharbeit“ öffnet wieder und weckt den Leiter; die meldende Seite sieht nichts davon.
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
DATA = tempfile.mkdtemp(prefix="vorgaenge-close-")
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
        data = json.dumps(body).encode() if ctype == "application/json" else urllib.parse.urlencode(body).encode()
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
REV = ("rev", "user", "Prüfer (KI)")
BEA = ("bea", "user")
for w in (LEAD, REV):
    call("GET", "/api/spez/turn", w)
call("GET", "/", BEA)
S.set_member("sap-support", "bea", "handler", "chef")
form("/bereiche/spezialist", ADMIN, {"queue": "sap-support", "uid": "lead"})


def gate_case(ref, title="ME21N: keine Berechtigung"):
    c = S.create(kind="internal", title=title, body="Benutzer KLAUS kann nicht bestellen.", reporter="sap-machine",
                 reporter_name="SAP A4H (Tor sap-support)", queue="sap-support",
                 obj={"kind": "gate", "id": f"sap-support/{ref}"}, attested={"app": "gate", "gate": "sap-support"})
    return c["number"]


def entry(n, kind, text, **data):
    return call("POST", f"/api/spez/case/{n}/entry", LEAD, dict(data, kind=kind, text=text))


def last_seq(n, typ="spec"):
    return [e["seq"] for e in S.get(n)["entries"] if e["type"] == typ][-1]


# --- Einstellung des Bereichs: Stichprobe aus, damit die Grundfälle bestimmt sind
st, _ = form("/bereiche/pruefung", ADMIN, {"queue": "sap-support", "share": "0", "uid": ""})
ok("der tenant_admin stellt die Stichprobe ein", st in (302, 303) and S.queue("sap-support")["review_share"] == 0, st)
st, _ = form("/bereiche/pruefung", BEA, {"queue": "sap-support", "share": "50", "uid": ""})
ok("KÖDER: eine Bearbeiterin stellt die Stichprobe nicht ein", st == 400
   and S.queue("sap-support")["review_share"] == 0, st)
st, _ = form("/bereiche/pruefung", ADMIN, {"queue": "sap-support", "share": "150", "uid": ""})
ok("KÖDER: Stichprobe über 100 % abgelehnt", st == 400, st)
st, _ = form("/bereiche/pruefung", ADMIN, {"queue": "sap-support", "share": "0", "uid": "bea"})
ok("KÖDER: ein Mensch wird nicht prüfender Spezialist", st == 400, st)
ok("neuer Bereich: Stichprobe 10 %", S.queue("eingang")["review_share"] == 10, S.queue("eingang"))

# --- kein Tor, kein Schließen
P = S.create(kind="question", title="Von einer Person", body="", reporter="bea", reporter_name="Bea",
             queue="sap-support")["number"]
st, d = call("POST", f"/api/spez/case/{P}/close", LEAD, {"text": "erledigt", "no_write": "nichts zu tun"})
ok("KÖDER: einen Vorgang einer Person schließt der Leiter nicht (RFC-0103 bleibt)", st == 409
   and "Tor" in d.get("error", ""), (st, d))
st, d = call("GET", f"/api/spez/case/{P}", LEAD)
ok("die Tür sagt dem Leiter, ob der Vorgang aus einem Tor kommt", st == 200 and d["from_gate"] is False, d)

# --- Schließen mit Beleg
G1 = gate_case("ZSUP-1")
st, d = call("GET", f"/api/spez/case/{G1}", LEAD)
ok("Vorgang aus dem Tor: from_gate, noch keine Belege, kein Merkmal",
   d["from_gate"] is True and d["proofs"] == [] and d["review_reasons"] == [], d)
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD, {"text": "Rolle zugeordnet."})
ok("KÖDER: ohne Beleg und ohne Begründung kein Schließen", st == 409 and "Begründung" in d.get("error", ""), (st, d))
entry(G1, "tool", "ERC_USER_ROLES {}\n→ Z_EINKAUF fehlt", tool="ERC_USER_ROLES")
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD, {"text": "x", "proof": last_seq(G1)})
ok("KÖDER: ein lesender Aufruf belegt nichts", st == 409 and "belegt nichts" in d.get("error", ""), (st, d))
entry(G1, "plan", "Rolle Z_EINKAUF an KLAUS", tool="ERC_ASSIGN_ROLE",
      arguments={"user": "KLAUS", "role": "Z_EINKAUF"})
PLAN = last_seq(G1)
entry(G1, "tool", "ERC_ASSIGN_ROLE ... (freigegeben von ?)", tool="ERC_ASSIGN_ROLE", plan=PLAN)
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD, {"text": "x", "proof": last_seq(G1)})
ok("KÖDER: ein Aufruf zu einem Plan ohne Freigabe belegt nichts", st == 409 and "Freigabe" in d.get("error", ""),
   (st, d))
form(f"/v/{G1}/freigabe", BEA, {"seq": PLAN, "ok": "1"})
entry(G1, "tool", "ERC_ASSIGN_ROLE ...\n→ Fehler: gesperrt", tool="ERC_ASSIGN_ROLE", plan=PLAN, failed=True)
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD, {"text": "x", "proof": last_seq(G1)})
ok("KÖDER: ein fehlgeschlagener Aufruf belegt nichts", st == 409 and "fehlgeschlagen" in d.get("error", ""), (st, d))
entry(G1, "tool", "ERC_ASSIGN_ROLE ...\n→ ok", tool="ERC_ASSIGN_ROLE", plan=PLAN, approved_by="Bea")
PROOF = last_seq(G1)
st, d = call("GET", f"/api/spez/case/{G1}", LEAD)
ok("die Tür nennt genau den gelungenen, freigegebenen Aufruf als Beleg",
   d["proofs"] == [{"seq": PROOF, "tool": "ERC_ASSIGN_ROLE"}], d["proofs"])
st, d = call("POST", f"/api/spez/case/{G1}/close", BEA, {"text": "x", "proof": PROOF})
ok("KÖDER: wer nicht verantwortlich ist, schließt nicht über die Tür", st == 409, (st, d))
CLOSE_TEXT = "Die Rolle Z_EINKAUF ist KLAUS zugeordnet. Bitte ME21N erneut versuchen."
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD,
             {"text": CLOSE_TEXT, "proof": PROOF, "run": "L9", "assistant": "leitung@3"})
c = S.get(G1)
st_e = [e for e in c["entries"] if e["type"] == "status"][-1]
ok("geschlossen: Status closed, Markierung „durch die Leitung“, öffentlicher Text mit Beleg",
   st == 200 and c["status"] == "closed" and c["mark"] == "closed_by_lead" and c["ended"]
   and not st_e["internal"] and st_e["text"] == CLOSE_TEXT and st_e["data"].get("proof") == PROOF, (st, d, c["status"]))
ok("ein interner Schritt „abgeschlossen“ mit Lauf und Assistent", any(
    e["type"] == "spec" and e["data"].get("reason") == "closed" and e["data"].get("run") == "L9" for e in c["entries"]))
ok("mit Beleg und Stichprobe 0 %: nicht zur Prüfung", c["review_mark"] == "", c["review_mark"])
r = app.node_reading(c, "sap-machine")
ok("das Tor liest den Schließtext als letzte Antwort (das liest SAP)",
   r["status"] == "closed" and r["replies"][-1]["text"] == CLOSE_TEXT, r)
st, d = call("POST", f"/api/spez/case/{G1}/close", LEAD, {"text": "nochmal", "proof": PROOF})
ok("KÖDER: ein beendeter Vorgang wird nicht noch einmal geschlossen", st == 409, (st, d))

# --- ohne Änderung: geht von selbst zur Prüfung
G2 = gate_case("ZSUP-2")
st, d = call("POST", f"/api/spez/case/{G2}/close", LEAD,
             {"text": "Die Berechtigung ist vorhanden; der Benutzer muss sich neu anmelden.",
              "no_write": "KLAUS hat Z_EINKAUF seit heute früh; nur der Puffer war alt."})
c = S.get(G2)
ok("ohne Änderung geschlossen: zur Prüfung mit Grund „geschlossen ohne Änderung“",
   st == 200 and c["status"] == "closed" and c["review_reasons"] == ["no_write"]
   and any(e["type"] == "review_mark" and e["actor"] == "system" and "Puffer" in e["text"] for e in c["entries"]),
   (st, d, c["review_mark"]))

# --- offene Rückfrage / Plan wartet
G3 = gate_case("ZSUP-3")
call("POST", f"/api/spez/case/{G3}/consult", LEAD, {"to": "bea", "text": "Darf ich das?"})
st, d = call("POST", f"/api/spez/case/{G3}/close", LEAD, {"text": "x", "no_write": "y"})
ok("KÖDER: mit offener Rückfrage kein Schließen", st == 409 and "offen" in d.get("error", ""), (st, d))
G4 = gate_case("ZSUP-4")
entry(G4, "plan", "Rolle", tool="ERC_ASSIGN_ROLE", arguments={})
st, d = call("POST", f"/api/spez/case/{G4}/close", LEAD, {"text": "x", "no_write": "y"})
ok("KÖDER: solange ein Plan auf Freigabe wartet, kein Schließen", st == 409 and "Freigabe" in d.get("error", ""),
   (st, d))

# --- die Stichprobe (D8 b)
S.set_queue_review("sap-support", 10, "", "chef")


def closed_with_proof(ref, roll):
    n = gate_case(ref)
    entry(n, "plan", "p", tool="ERC_ASSIGN_ROLE", arguments={"n": ref})
    pl = last_seq(n)
    form(f"/v/{n}/freigabe", BEA, {"seq": pl, "ok": "1"})
    entry(n, "tool", "ok", tool="ERC_ASSIGN_ROLE", plan=pl)
    S.close_by_lead(S.get(n), "lead", "Leitung SAP (KI)", "erledigt", last_seq(n), rand=lambda: roll)
    return S.get(n)


c = closed_with_proof("ZSUP-5", 0.05)
ok("Stichprobe 10 %, Wurf 5: zur Prüfung mit Grund „Stichprobe“", c["review_reasons"] == ["sample"], c["review_mark"])
c = closed_with_proof("ZSUP-6", 0.5)
ok("Stichprobe 10 %, Wurf 50: nicht zur Prüfung", c["review_reasons"] == [], c["review_mark"])

# --- die Plattform markiert ohne zu fragen
G7 = gate_case("ZSUP-7")
entry(G7, "end", "Grenze: 20 Schritte erreicht.", reason="limit")
ok("Grenze erreicht: zur Prüfung", S.get(G7)["review_reasons"] == ["limit"], S.get(G7)["review_mark"])
entry(G7, "end", "Grenze: noch einmal.", reason="limit")
ok("derselbe Grund zweimal zählt einmal", S.get(G7)["review_reasons"] == ["limit"]
   and sum(1 for e in S.get(G7)["entries"] if e["type"] == "review_mark") == 1, "")
entry(G7, "tool", "ERC_ASSIGN_ROLE ... (ohne Freigabe: aufgehoben von chef)", tool="ERC_ASSIGN_ROLE", lifted_by="chef")
ok("ein Aufruf unter aufgehobener Freigabe: ein zweiter Grund", S.get(G7)["review_reasons"] == ["limit", "lifted"],
   S.get(G7)["review_mark"])

# --- Leiter und Menschen markieren
G8 = gate_case("ZSUP-8")
st, d = call("POST", f"/api/spez/case/{G8}/mark", LEAD, {"reason": "Unsicher, ob die Rolle zu weit geht."})
ok("der Leiter markiert mit seinem Werkzeug", st == 200 and S.get(G8)["review_reasons"] == ["lead"], (st, d))
st, d = call("POST", f"/api/spez/case/{G8}/mark", LEAD, {"reason": ""})
ok("KÖDER: ohne Grund keine Markierung", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{G8}/mark", BEA, {"reason": "ich als Leiter"})
ok("KÖDER: als Leiter markiert über die Tür nur der Verantwortliche", st == 409, (st, d))
st, _ = form(f"/v/{P}/pruefmarke", BEA, {"text": "Bitte noch einmal ansehen."})
ok("eine Bearbeiterin markiert von Hand", st in (302, 303) and S.get(P)["review_reasons"] == ["person"], st)
st, _ = form(f"/v/{P}/pruefmarke", ("sap-machine", "user"), {"text": "ich auch"})
ok("KÖDER: wer den Bereich nicht bearbeitet, markiert nicht", st in (400, 404), st)

# --- der prüfende Spezialist
st, _ = form("/bereiche/pruefung", ADMIN, {"queue": "sap-support", "share": "10", "uid": "rev"})
ok("der tenant_admin trägt einen prüfenden Spezialisten ein", S.queue("sap-support")["reviewer"] == "rev", st)
st, d = call("GET", "/api/spez/turn", REV)
nums = {x["number"] for x in d["cases"] if x.get("review")}
ok("der Prüfer ist an der Reihe mit markierten Vorgängen, an denen niemand mehr arbeitet",
   G2 in nums and closed_with_proof is not None and G8 not in nums, (nums, G2, G8))
st, d = call("GET", f"/api/spez/case/{G2}", REV)
ok("der Prüfer liest den markierten Vorgang, mit „reviewing“", st == 200 and d["reviewing"] is True
   and d["review_reasons"] == ["no_write"] and not d["mine"], (st, d))
st, d = call("GET", f"/api/spez/case/{G1}", REV)
ok("KÖDER: einen nicht markierten Vorgang liest der Prüfer nicht", st == 404, st)
st, d = call("POST", f"/api/spez/case/{G2}/review", LEAD, {"result": "ok", "text": ""})
ok("KÖDER: der Leiter prüft seine eigene Arbeit nicht", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{G2}/review", REV, {"result": "rework", "text": ""})
ok("KÖDER: Nacharbeit ohne Text abgelehnt", st == 409, (st, d))
st, d = call("POST", f"/api/spez/case/{G2}/review", REV,
             {"result": "rework", "text": "Puffer ist keine Erklärung; SU53 erneut lesen."})
c = S.get(G2)
ok("Nacharbeit: Merkmal weg, Vorgang wieder offen, der Leiter bleibt verantwortlich",
   st == 200 and c["review_mark"] == "" and c["status"] == "reopened" and c["mark"] == "" and c["ended"] == ""
   and c["responsible"] == "lead"
   and any(e["type"] == "review" and e["data"].get("result") == "rework" for e in c["entries"]), (st, d, c["status"]))
st, d = call("GET", "/api/spez/turn", LEAD)
ok("der Leiter ist wieder dran", any(x["number"] == G2 and (x["last"] or {}).get("actor") != "lead"
                                     for x in d["cases"]), d)
st, d = call("GET", f"/api/spez/case/{G2}", REV)
ok("nach der Prüfung liest der Prüfer den Vorgang nicht mehr", st == 404, st)

G5 = [n for n in (x["number"] for x in S.listing(queues=["sap-support"], open_only=False))
      if S.get(n)["object_id"] == "sap-support/ZSUP-5"][0]
st, d = call("POST", f"/api/spez/case/{G5}/review", REV, {"result": "none", "text": "Ohne Zugang zu SU53 kein Urteil."})
c = S.get(G5)
ok("„kein Urteil“: das Merkmal bleibt, der Prüfer ist nicht mehr dran",
   st == 200 and c["review_reasons"] == ["sample"] and c["review_spec"] == 1, (st, d))
st, d = call("GET", "/api/spez/turn", REV)
ok("… und taucht in seiner Liste nicht mehr auf", G5 not in {x["number"] for x in d["cases"]}, d)
st, d = call("POST", f"/api/spez/case/{G5}/review", REV, {"result": "ok", "text": ""})
ok("KÖDER: zu derselben Markierung liefert der Prüfer kein zweites Mal", st in (404, 409), (st, d))
st, page = call("GET", "/?view=review", BEA)
ok("die eigene Liste „zur Prüfung“ zeigt die markierten, auch beendete", G5 in page and G7 in page
   and G1 not in page, "")
st, page = call("GET", f"/v/{G5}", BEA)
ok("die Vorgangsseite zeigt Merkmal, Gründe und das Prüfformular", "zur Prüfung" in page and "Stichprobe" in page
   and "value=rework" in page and "kein Urteil" in page, "")
st, _ = form(f"/v/{G5}/pruefung", BEA, {"result": "ok", "text": ""})
c = S.get(G5)
ok("die Bearbeiterin prüft: in Ordnung, Merkmal weg, Vorgang bleibt geschlossen",
   st in (302, 303) and c["review_mark"] == "" and c["status"] == "closed" and c["mark"] == "closed_by_lead", st)
st, _ = form(f"/v/{G5}/pruefung", BEA, {"result": "ok", "text": ""})
ok("KÖDER: ein nicht markierter Vorgang wird nicht geprüft", st == 400, st)

# --- der Prüfer ist zugleich der Leiter: nie die eigene Arbeit
S.set_queue_review("sap-support", 10, "lead", "chef")
ok("KÖDER: ist der Leiter auch Prüfer, bekommt er seine eigenen geschlossenen Vorgänge nicht",
   not [x for x in S.turn("lead") if x.get("review")], [x for x in S.turn("lead") if x.get("review")])
S.set_queue_review("sap-support", 10, "rev", "chef")

# --- was die meldende Seite sieht
c = S.get(G7)
pub = [e for e in c["entries"] if not e["internal"]]
ok("KÖDER: Merkmal und Prüfung sind intern", not [e for e in pub if e["type"] in ("review_mark", "review")], pub)
st, page = call("GET", f"/v/{G2}", ("sap-machine", "user"))
ok("KÖDER: die meldende Seite sieht weder Merkmal noch Prüfung", "zur Prüfung" not in page
   and "Puffer ist keine" not in page, "")
st, page = call("GET", "/bereich/sap-support", ADMIN)
ok("die Seite des Bereichs zeigt die Karte „Prüfung“", "Stichprobe (%)" in page and "Prüfer (KI)" in page, "")

print(f"\n{'OK' if not fails else f'{fails} FEHLER'}")
sys.exit(1 if fails else 0)
