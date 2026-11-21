"""0.16.0 (RFC-0105 §7.1, D9): unterschriebene Freigaben und die Ampel eines Bereichs.

    py -3 apps/vorgaenge/test_spez_signed.py

Geprüft: Eine Freigabe eines geplanten Aufrufs trägt eine Unterschrift mit dem
Freigabeschlüssel dieser Instanz, die das MCP-Gateway mit seinem eigenen Modul
(`mcp-gateway/signed_approval.py`) prüft -- genau dieses Werkzeug, genau diese Argumente;
eine Ablehnung und ein Entwurf tragen keine. Den Schlüssel sieht und erneuert nur der
tenant_admin; danach trägt eine alte Freigabe nicht mehr. Die Ampel eines Bereichs stellt
nur der tenant_admin ein, der Lauf liest sie mit dem Vorgang.
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
sys.path.insert(1, os.path.join(HERE, "..", "mcp-gateway"))
DATA = tempfile.mkdtemp(prefix="vorgaenge-sig-")
os.environ["VORGAENGE_DATA_DIR"] = DATA

import app  # noqa: E402
import signed_approval as sa  # noqa: E402  (das Prüfmodul des Gateways)
import store  # noqa: E402

app.DATA_DIR = DATA
fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


GRANTS = {"spez": [{"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
                    "fields": {"queue": ["analyse"]}}],
          "bea": [{"object": "case", "activities": ["read", "comment", "triage", "move", "close"],
                   "fields": {"queue": ["analyse"]}}]}


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
S.add_queue("analyse", "Analyse")


def person(uid, roles="user", name=None):
    return {"X-OAAP-User-Id": uid, "X-OAAP-Roles": roles, "X-OAAP-Display-Name": name or uid.title(),
            "X-OAAP-User": uid}


def call(method, path, who, body=None, ctype="application/json", extra=None):
    h = dict(person(*who))
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


ADMIN = ("chef", "user,tenant_admin", "Chef")
BEA = ("bea", "user", "Bea Prüferin")
SPEZ = ("spez", "user", "Analyse (KI)")

call("GET", "/api/spez/turn", SPEZ)
form("/bereiche/spezialist", ADMIN, {"queue": "analyse", "uid": "spez"})

print("--- Ampel des Bereichs (D9)")
st, _ = form("/bereiche/ampel", BEA, {"queue": "analyse", "light": "green"})
ok("KÖDER: die Ampel stellt nur der tenant_admin ein", st == 400 and not S.queue("analyse")["light"], st)
st, _ = form("/bereiche/ampel", ADMIN, {"queue": "analyse", "light": "lila"})
ok("eine unbekannte Farbe wird abgelehnt", st == 400, st)
st, _ = form("/bereiche/ampel", ADMIN, {"queue": "analyse", "light": "green"})
ok("tenant_admin setzt grün", st in (302, 303) and S.queue("analyse")["light"] == "green", st)
st, page = call("GET", "/bereiche", ADMIN)
st, qpage = call("GET", "/bereich/analyse", ADMIN)
ok("Bereiche zeigen die Ampel und den Weg zum Freigabeschlüssel",
   "Ampel grün" in page and "Ampel für Spezialisten" in qpage and "<option value='green' selected>" in qpage
   and "/bereiche/freigabeschluessel" in page
   and "Ampel grün" in page.split("Zuletzt geändert")[-1], "")
c = S.create(kind="bug", title="Rolle fehlt", body="Bitte prüfen.", reporter="mel", reporter_name="Melanie",
             queue="analyse")
N = c["number"]
st, d = call("GET", f"/api/spez/case/{N}", SPEZ)
ok("der Lauf liest die Ampel mit dem Vorgang", st == 200 and d.get("queue_light") == "green", d)

print("--- unterschriebene Freigabe")
st, page = call("GET", "/bereiche/freigabeschluessel", BEA)
ok("KÖDER: den Freigabeschlüssel sieht nur der tenant_admin", st == 403 and "oaap-fk-" not in page, st)
st, page = call("GET", "/bereiche/freigabeschluessel", ADMIN)
FK = "oaap-fk-" + page.split("oaap-fk-")[1][:64] if "oaap-fk-" in page else ""
ok("der tenant_admin sieht ihn, mit der Anleitung fürs MCP-Gateway",
   st == 200 and sa.KEY_RE.match(FK) and "nur mit unterschriebener Freigabe" in page, st)
ok("…er liegt als Datei 0600 im Datenverzeichnis",
   os.path.exists(os.path.join(DATA, "approval.key")) and (os.name == "nt" or
   (os.stat(os.path.join(DATA, "approval.key")).st_mode & 0o077) == 0))
ARGS = {"user": "mel", "role": "Z_LIZENZ", "text": "Grün ist ä"}
call("POST", f"/api/spez/case/{N}/entry", SPEZ, {"kind": "plan", "text": "Ich trage die Rolle ein.",
                                                 "tool": "assign_role", "arguments": ARGS, "run": "r1"})
plan = [e for e in S.get(N)["entries"] if e["type"] == "spec" and e["data"].get("kind") == "plan"][-1]
st, _ = form(f"/v/{N}/freigabe", BEA, {"seq": plan["seq"], "ok": "1"})
appr = [e for e in S.get(N)["entries"] if e["type"] == "approval"][-1]
tok = appr["data"].get("token") or ""
ok("die Freigabe trägt eine Unterschrift", st in (302, 303) and tok.startswith(sa.PREFIX), appr["data"])
st, d = call("GET", f"/api/spez/case/{N}", SPEZ)
ok("…der Spezialist liest sie mit der Freigabe",
   any((e.get("approval") or {}).get("token") == tok for e in d["entries"]), d)
try:
    p = sa.verify(FK, tok, "assign_role", ARGS)
    good = True
except sa.Invalid as exc:
    p, good = {"err": str(exc)}, False
ok("das Prüfmodul des Gateways nimmt sie an: Werkzeug, Argumente, Person, eindeutige Kennung",
   good and p["by"] == "Bea Prüferin" and p["case"] == N and p["plan"] == plan["seq"]
   and p["jti"].endswith(f":{plan['seq']}") and p["exp"] - p["iat"] == 7 * 86400, p)
for label, tool, args in (("ein anderes Werkzeug", "delete_user", ARGS),
                          ("andere Argumente", "assign_role", dict(ARGS, role="SAP_ALL"))):
    try:
        sa.verify(FK, tok, tool, args)
        bad = False
    except sa.Invalid:
        bad = True
    ok(f"KÖDER: {label} trägt sie nicht", bad)

call("POST", f"/api/spez/case/{N}/entry", SPEZ, {"kind": "plan", "text": "Noch eine Rolle.",
                                                 "tool": "assign_role", "arguments": {"user": "x"}, "run": "r2"})
plan2 = [e for e in S.get(N)["entries"] if e["type"] == "spec" and e["data"].get("kind") == "plan"][-1]
form(f"/v/{N}/freigabe", BEA, {"seq": plan2["seq"], "ok": "0"})
appr2 = [e for e in S.get(N)["entries"] if e["type"] == "approval"][-1]
ok("eine Ablehnung trägt keine Unterschrift", appr2["data"]["ok"] is False and "token" not in appr2["data"])

print("--- Schlüssel erneuern")
st, _ = form("/bereiche/freigabeschluessel", BEA, {})
ok("KÖDER: erneuern darf nur der tenant_admin", st == 400, st)
st, page = form("/bereiche/freigabeschluessel", ADMIN, {})
FK2 = "oaap-fk-" + page.split("oaap-fk-")[1][:64] if "oaap-fk-" in page else ""
try:
    sa.verify(FK2, tok, "assign_role", ARGS)
    still = True
except sa.Invalid:
    still = False
ok("neu erzeugt: ein anderer Schlüssel, die alte Freigabe trägt nicht mehr",
   st == 200 and FK2 and FK2 != FK and not still, st)
st, page = call("GET", "/bereiche", ADMIN)
ok("das Protokoll der Bereiche nennt es", "Freigabeschlüssel neu erzeugt" in page)

print("")
print("ALLE PASS" if not fails else f"{fails} FEHLER")
sys.exit(1 if fails else 0)
