"""Vorgaenge 0.2: Uebergabe zwischen zwei Mandanten und Vorgaenge aus dem
Backend einer App (RFC-0061 §5, §3.2).

    python3 apps/vorgaenge/test_stage2.py

Zwei echte Server in einem Prozess: A ist der Verein (hbvp), B der
Entwicklungsmandant (dev). Was das Gateway sonst tut -- den Schluessel
pruefen und den Prinzipal nennen --, tut hier eine Nachbildung von
send_doc(): ein Schluessel, den es nicht kennt, kommt nicht an.
"""
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="vorgaenge-s2-")
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

    def __init__(self, grants):
        self.g = grants

    def grants(self, uid):
        return self.g.get(uid, [])


def server(name, grants):
    st = store.Store(os.path.join(DATA, f"{name}.db"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
    srv.store, srv.authz, srv.kick = st, FakeAuthz(grants), threading.Event()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return st, srv, f"http://127.0.0.1:{srv.server_port}"


GA, GB = {}, {}
A, srvA, URL_A = server("hbvp", GA)
B, srvB, URL_B = server("dev", GB)

# Schluessel -> Prinzipal, wie das Gateway des EMPFAENGERS es aufloesen wuerde
KEYS = {"key-a2b": "m-hbvp-to-dev", "key-b2a": "m-dev-to-hbvp", "key-evil": "m-evil"}


def fake_send(url, key, raw):
    principal = KEYS.get(key)
    if not principal:
        return False, "HTTP 401 unknown key"
    st, b, _ = call(url, "POST", "/handover",
                    {"Authorization": "Bearer " + key, "X-OAAP-User": principal},
                    raw.encode(), "application/json")
    d = json.loads(b or "{}")
    return (st == 200 and d.get("ok") is True), f"HTTP {st} {d.get('error', '')}"


app.send_doc = fake_send


def call(base, method, path, headers=None, body=None, ctype=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else (
            json.dumps(body).encode() if ctype == "application/json" else body.encode())
        h["Content-Type"] = ctype or "application/x-www-form-urlencoded"
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


def user(uid, roles="user", name=None):
    return {"X-OAAP-User-Id": uid, "X-OAAP-Roles": roles, "X-OAAP-User": uid,
            "X-OAAP-Display-Name": name or uid}


def origin(base):
    return {"Origin": base}


SOURCE = {"X-OAAP-Source-Instance": "hbvp-portal-test", "X-OAAP-Source-App": "hbsha",
          "X-OAAP-Source-Version": "0.0.190", "X-OAAP-Source-Tenant": "t-hbvp"}
ADMIN_A = user("11111111-1111-4111-8111-111111111111", "admin", "Vereinsadmin Max Muster")
ADMIN_B = user("22222222-2222-4222-8222-222222222222", "admin", "Entwicklerin")

# --- Partner auf beiden Seiten
B.add_queue("triage", "Triage Entwicklung")
A.save_partner("dev", "HVP-Entwicklung", URL_B, "key-a2b", "m-dev-to-hbvp", "eingang")
B.save_partner("hbvp", "Handballverein", URL_A, "key-b2a", "m-hbvp-to-dev", "triage")
B.save_partner("evil", "Jemand anderes", "", "", "m-evil", "eingang")

print("")
print("1. Eine Meldung im Verein")
st, b, _ = call(URL_A, "POST", "/platform/feedback",
                {**SOURCE, **user("33333333-3333-4333-8333-333333333333", name="Anna Spielerin"),
                 **origin(URL_A)},
                {"kind": "bug", "title": "Spielstand springt zurück (Anna)",
                 "body": "Anna Spielerin meldet: nach Timeout springt der Stand.",
                 "object": {"kind": "meeting", "id": "m-0042"},
                 "browser": {"path": "/admin", "last_error": "TypeError x"}},
                "application/json")
num = json.loads(b)["number"]
c = A.get(num)
A.comment(c, ADMIN_A["X-OAAP-User-Id"], "Vereinsadmin Max Muster", "Intern: Max ruft Anna an.")
A.comment(c, ADMIN_A["X-OAAP-User-Id"], "Vereinsadmin Max Muster", "Tritt nur nach dem Timeout auf.")
c = A.get(num)
seq_keep = [e["seq"] for e in c["entries"] if e["text"].startswith("Tritt")][0]

print("")
print("2. Entwurf, Vorschau, Freigabe (Q3)")
GA["44444444-4444-4444-8444-444444444444"] = [
    {"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
     "fields": {"queue": ["eingang"]}}]
st, b, _ = call(URL_A, "GET", f"/v/{num}/uebergabe?partner=dev",
                user("44444444-4444-4444-8444-444444444444"))
ok("ohne hand_over kein Entwurf (Bearbeiter/in, nicht Clearing)", st == 403, st)
st, b, _ = call(URL_A, "GET", f"/v/{num}", ADMIN_A)
ok("das Statusformular bietet 'übergeben' nicht an",
   "<option value=handed_over>" not in b and "Entwurf öffnen" in b, st)
st, b, _ = call(URL_A, "GET", f"/v/{num}/uebergabe?partner=dev", ADMIN_A)
ok("der Entwurf zeigt Betreff und Kommentare zur Auswahl",
   st == 200 and "Spielstand springt" in b and "Tritt nur nach" in b, st)
form = {"partner": "dev", "title": "Spielstand springt nach Timeout zurück",
        "body": "Nach einem Timeout springt der Spielstand auf den alten Wert.",
        "with_tech": "1", "step": "preview"}
st, b, _ = call(URL_A, "POST", f"/v/{num}/uebergabe", {**ADMIN_A, **origin(URL_A)},
                urlencode(form) + f"&entry={seq_keep}")
ok("Vorschau 200", st == 200 and "Freigeben und senden" in b, (st, b[:300]))
hidden = dict(re.findall(r"<input type=hidden name=(\w+) value='([^']*)'>", b))
pre = re.search(r"<pre class=card>(.*?)</pre>", b, re.S).group(1)
import html as _h  # noqa: E402
shown = json.loads(_h.unescape(pre))
ok("die Vorschau zeigt das Dokument mit der ausgewaehlten Notiz, ohne die andere",
   [e["text"] for e in shown["entries"]] == ["Tritt nur nach dem Timeout auf."], shown["entries"])
rel = dict(hidden, step="release")
rel_tampered = dict(rel, body="Anderer Text, als gezeigt")
st, b, _ = call(URL_A, "POST", f"/v/{num}/uebergabe", {**ADMIN_A, **origin(URL_A)},
                urlencode(rel_tampered) + f"&entry={seq_keep}")
ok("ein anderer Text als in der Vorschau wird nicht freigegeben",
   st == 400 and "weicht von der Vorschau ab" in b, (st, b[:200]))
st, b, _ = call(URL_A, "POST", f"/v/{num}/uebergabe", {**ADMIN_A, **origin(URL_A)},
                urlencode(rel) + f"&entry={seq_keep}")
ok("freigegeben (303)", st == 303, (st, b[:200]))
c = A.get(num)
ok("Status 'übergeben'", c["status"] == "handed_over", c["status"])
ok("der Kick fuer den Ausgang wurde gesetzt", srvA.kick.is_set())
out = A.outbox_due()
ok("ein Dokument im Ausgang", len(out) == 1, out)
raw = out[0]["doc"]
ok("es ist byte-gleich mit der Vorschau", raw == store.canonical(shown))


def walk_keys(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_keys(v)


sent = json.loads(raw)
ok("keine verbotenen Schluessel im Dokument (§10.3)",
   not (set(walk_keys(sent)) & store.FORBIDDEN_KEYS), set(walk_keys(sent)))
ok("weder Benutzer-ID noch Name der Meldenden noch die nicht gewaehlte Notiz",
   "33333333" not in raw and "Anna" not in raw and "Max" not in raw and "11111111" not in raw, raw)
ok("die technischen Angaben gehen mit (App, Version, letzter Fehler)",
   sent["tech"].get("app") == "hbsha" and sent["tech"].get("last_error") == "TypeError x", sent["tech"])
ok("body_sha256 stimmt", sent["body_sha256"] == __import__("hashlib").sha256(
    sent["body"].encode()).hexdigest())
try:
    store.check_outgoing({"a": [{"tech": {"user_id": "x"}}]})
    ok("die Pruefung findet einen verbotenen Schluessel auch tief im Dokument", False)
except store.Refused as e:
    ok("die Pruefung findet einen verbotenen Schluessel auch tief im Dokument",
       "user_id" in str(e), e)

print("")
print("3. Zustellung, mindestens einmal, und Empfang")
app.flush_outbox(A)
ok("quittiert", A.outbox_due() == [] and A.outbox_for_case(c["id"])[0]["acked"] != "")
got = B.listing(queues=["triage"])
ok("bei B ein neuer Vorgang im Bereich des Partners", len(got) == 1, got)
cb = B.get(got[0]["number"])
ok("… gemeldet vom Partner, nicht von einer Person",
   cb["reporter"] == "partner:hbvp" and cb["reporter_name"] == "Handballverein", cb["reporter"])
ok("… mit Betreff, Text, Bezug und der freigegebenen Notiz",
   cb["title"] == "Spielstand springt nach Timeout zurück"
   and (cb["object_kind"], cb["object_id"]) == ("meeting", "m-0042")
   and any(e["text"] == "Tritt nur nach dem Timeout auf." for e in cb["entries"]), cb["entries"])
ok("… das Rohdokument liegt unveraendert mit Hash",
   cb["tech"]["handover"]["sha256"] == __import__("hashlib").sha256(raw.encode()).hexdigest())
with A.lock, A.db:
    A.db.execute("UPDATE outbox SET acked='', next_try=0")
app.flush_outbox(A)
ok("eine zweite Zustellung legt nichts doppelt an", len(B.listing(queues=["triage"])) == 1)
bad = dict(sent, handover_id="99999999-9999-4999-8999-999999999999", body="geändert")
st, b, _ = call(URL_B, "POST", "/handover", {"Authorization": "Bearer key-a2b",
                                              "X-OAAP-User": "m-hbvp-to-dev"},
                json.dumps(bad).encode(), "application/json")
ok("ein Text, der nicht zu body_sha256 passt, wird abgelehnt", st == 400 and "sha256" in b, (st, b))
st, b, _ = call(URL_B, "POST", "/handover", {"Authorization": "Bearer x", "X-OAAP-User": "m-unknown"},
                raw.encode(), "application/json")
ok("ein Prinzipal ohne Partnereintrag: 403", st == 403, st)
st, b, _ = call(URL_B, "POST", "/handover", {"X-OAAP-User": "m-hbvp-to-dev"},
                raw.encode(), "application/json")
ok("ohne Schluessel: 401", st == 401, st)
st, b, _ = call(URL_B, "POST", "/handover", {**ADMIN_B, **origin(URL_B)}, raw.encode(),
                "application/json")
ok("eine Sitzung im Browser ist kein Partner", st in (401, 403), st)

PARTNER_KEY = {"Authorization": "Bearer key-a2b", "X-OAAP-User": "m-hbvp-to-dev",
               "X-OAAP-User-Id": "55555555-5555-4555-8555-555555555555", "X-OAAP-Roles": "user"}
st, b, _ = call(URL_B, "GET", "/bereiche", PARTNER_KEY)
ok("der Zugang des Partners sieht keine Seite (Bereiche): 403", st == 403, st)
st, b, _ = call(URL_B, "POST", "/neu", {**PARTNER_KEY, **origin(URL_B)},
                "kind=internal&queue=eingang&title=von+aussen")
ok("… und legt ueber /neu keinen Vorgang an, auch mit passendem Origin: 403", st == 403, st)

print("")
print("4. Antwort zurueck")
st, b, _ = call(URL_B, "GET", f"/v/{cb['number']}", ADMIN_B)
ok("der Bereich 'triage' gehoert nicht automatisch den Admins von B", st == 404, st)
GB[ADMIN_B["X-OAAP-User-Id"]] = [
    {"object": "case", "activities": ["read", "comment", "triage", "move", "hand_over", "close"],
     "fields": {"queue": ["triage"]}}]
st, b, _ = call(URL_B, "GET", f"/v/{cb['number']}", ADMIN_B)
ok("B zeigt das Antwortformular an den Verein", "Antwort an Handballverein" in b, st)
st, b, _ = call(URL_B, "POST", f"/v/{cb['number']}/antwort", {**ADMIN_B, **origin(URL_B)},
                urlencode({"text": "Behoben in 0.0.191."}))
ok("Antwort freigegeben (303)", st == 303, (st, b[:200]))
app.flush_outbox(B)
c = A.get(num)
ok("beim Verein: Eintrag 'Antwort des Partners' und Status beantwortet",
   c["status"] == "answered" and any(e["type"] == "answer" and e["text"] == "Behoben in 0.0.191."
                                     for e in c["entries"]), (c["status"], c["entries"][-2:]))
ok("die Antwort traegt keine Person von B",
   all("Entwicklerin" not in e["actor_name"] for e in c["entries"]))
# ein anderer Partner darf auf A's Uebergabe nicht antworten
A.save_partner("evil", "Fremder", "", "", "m-evil-on-a", "eingang")
fake = {"handover_id": "88888888-8888-4888-8888-888888888888", "answer_to": sent["handover_id"],
        "body": "x", "body_sha256": __import__("hashlib").sha256(b"x").hexdigest()}
st, b, _ = call(URL_A, "POST", "/handover", {"Authorization": "Bearer y", "X-OAAP-User": "m-evil-on-a"},
                json.dumps(fake).encode(), "application/json")
ok("eine Antwort von einem anderen Partner wird abgelehnt", st == 400, (st, b))

print("")
print("5. Vorgaenge aus dem Backend (§3.2)")
APPKEY = {**SOURCE, "X-OAAP-User": "instance:hbvp-portal-test", "X-OAAP-User-Id": "aaaa"}
anna = "33333333-3333-4333-8333-333333333333"
doc = {"kind": "internal", "title": "Bewerbung Hallenzeit Mo 18 Uhr",
       "body": "Mannschaft mB", "on_behalf_of": {"user_id": anna, "name": "Anna Spielerin"}}
st, b, _ = call(URL_A, "POST", "/platform/cases/submit", APPKEY, doc, "application/json")
ok("angelegt (201)", st == 201, (st, b))
sub = A.get(json.loads(b)["number"])
ok("eingereicht von der App, beglaubigt",
   sub["tech"]["attested"].get("submitted_by") == "app:hbvp-portal-test"
   and sub["tech"]["attested"].get("version") == "0.0.190", sub["tech"])
ok("'im Namen von' steht als Angabe der App, nicht als beglaubigt",
   sub["tech"]["asserted"]["on_behalf_of"] == anna and "user_id" not in sub["tech"]["attested"],
   sub["tech"])
st, b, _ = call(URL_A, "GET", "/", user(anna))
ok("Anna sieht ihn unter 'Von mir gemeldet'", sub["number"] in b)
st, b, _ = call(URL_A, "POST", "/platform/cases/submit",
                {**SOURCE, "X-OAAP-User": "instance:hbvp-portal"}, doc, "application/json")
ok("der Schluessel einer anderen Instanz: 403", st == 403, st)
st, b, _ = call(URL_A, "POST", "/platform/cases/submit", {**SOURCE, **user(anna)}, doc,
                "application/json")
ok("eine Person (Sitzung) an dieser Tuer: 403", st == 403, st)
st, b, _ = call(URL_A, "POST", "/platform/cases/submit",
                {"X-OAAP-User": "instance:hbvp-portal-test"}, doc, "application/json")
ok("ohne Herkunft vom Gateway: 403", st == 403, st)
st, b, _ = call(URL_A, "POST", "/platform/cases/submit", APPKEY,
                dict(doc, on_behalf_of="anna"), "application/json")
ok("on_behalf_of ohne Benutzer-ID: 400", st == 400, (st, b))
st, b, _ = call(URL_A, "POST", "/platform/cases/submit", APPKEY,
                {"title": "ohne Person"}, "application/json")
sub2 = A.get(json.loads(b)["number"]) if st == 201 else {}
ok("ohne on_behalf_of meldet die App selbst",
   st == 201 and sub2["reporter"] == "app:hbvp-portal-test" and "asserted" not in sub2["tech"], b)
app.SUBMISSIONS_PER_HOUR = 2
st, b, _ = call(URL_A, "POST", "/platform/cases/submit", APPKEY, {"title": "zu viel"},
                "application/json")
ok("Drosselung je Instanz: 429", st == 429, (st, b))

srvA.shutdown()
srvB.shutdown()
print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
