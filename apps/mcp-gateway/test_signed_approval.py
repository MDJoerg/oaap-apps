"""Unterschriebene Freigaben (RFC-0105 §7.1, mcp-gateway 0.39.0).

    py -3 apps/mcp-gateway/test_signed_approval.py      (auf dem Knoten: python3 …)

Das Gateway läuft echt, per HTTP; dahinter ein Papier-Server mit einem lesenden und einem
schreibenden Werkzeug. In einer Sicht mit `approval = signed` gilt:

  * ohne Freigabeschlüssel lässt sich `signed` nicht einschalten; der Schlüssel wird nie
    wieder gezeigt;
  * ein schreibender Aufruf eines Schlüssels ohne unterschriebene Freigabe, mit einer
    falsch unterschriebenen, für ein anderes Werkzeug, andere Argumente, abgelaufen --
    abgelehnt, nichts geht hinaus; auch die Kopfzeilen von Stufe 2 allein reichen nicht;
  * eine gültige Freigabe trägt genau EINEN Aufruf; die Messzeile nennt die Person aus
    der Freigabe, nicht aus einer Kopfzeile;
  * lesende Werkzeuge brauchen keine Freigabe.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="mcpgw-sig-")
os.environ.update({"MCPGW_DATA_DIR": TMP, "MCPGW_MASTER_KEY": "x" * 64,
                   "MCPGW_CATALOG_MINUTES": "0"})
sys.path.insert(0, HERE)

fails = passes = 0


def ok(label, cond, detail=""):
    global fails, passes
    fails += not cond
    passes += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:700]}")


TOOLS = [{"name": "get_case", "description": "Liest.", "annotations": {"readOnlyHint": True},
          "inputSchema": {"type": "object", "properties": {"n": {"type": "string"}}}},
         {"name": "comment_case", "description": "Schreibt einen Kommentar.",
          "inputSchema": {"type": "object", "properties": {"n": {"type": "string"},
                                                           "text": {"type": "string"}}}},
         {"name": "close_case", "description": "Schließt.",
          "inputSchema": {"type": "object", "properties": {"n": {"type": "string"}}}}]
CALLS = []


class Paper(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def reply(self, doc, headers=()):
        body = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for h in headers:
            self.send_header(*h)
        self.end_headers()
        self.wfile.write(body)

    def do_DELETE(self):
        self.reply({})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if "method" not in body or body.get("id") is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        m, mid, p = body["method"], body["id"], body.get("params") or {}
        if m == "initialize":
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                "serverInfo": {"name": "p", "version": "1"}}}, [("Mcp-Session-Id", "s")])
            return
        if m == "tools/list":
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}})
            return
        if m == "tools/call":
            CALLS.append((p.get("name"), p.get("arguments")))
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": "erledigt " + str(p.get("name"))}]}})
            return
        self.reply({"jsonrpc": "2.0", "id": mid, "result": {}})


paper = ThreadingHTTPServer(("0.0.0.0", 0), Paper)
threading.Thread(target=paper.serve_forever, daemon=True).start()

import app  # noqa: E402
import books  # noqa: E402
import signed_approval as sa  # noqa: E402

gw = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=gw.serve_forever, daemon=True).start()
GW = f"http://127.0.0.1:{gw.server_address[1]}"


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def admin(path, **form):
    data = urlencode(form).encode()
    rq = urllib.request.Request(GW + path, data=data, method="POST",
                                headers={"X-OAAP-User": "betreiber", "X-OAAP-Roles": "admin",
                                         "Host": "gw.example"})
    with urllib.request.urlopen(rq, timeout=30) as r:
        return r.read().decode()


def admin_get(path):
    rq = urllib.request.Request(GW + path, headers={"X-OAAP-User": "betreiber", "X-OAAP-Roles": "admin"})
    with urllib.request.urlopen(rq, timeout=30) as r:
        return r.read().decode()


def rpc(msg, key, sid=None, extra=None):
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
         "Authorization": "Bearer " + key}
    if sid:
        h["Mcp-Session-Id"] = sid
    h.update(extra or {})
    rq = urllib.request.Request(f"{GW}/mcp/spez", data=json.dumps(msg).encode(), headers=h, method="POST")
    try:
        r = urllib.request.urlopen(rq, timeout=30)
    except urllib.error.HTTPError as e:
        r = e
    raw = r.read().decode()
    msgs = []
    for block in raw.split("\n\n"):
        d = [ln[6:] for ln in block.split("\n") if ln.startswith("data: ")]
        if d:
            msgs.append(json.loads("\n".join(d)))
    if not msgs and raw.strip().startswith("{"):
        msgs.append(json.loads(raw))
    return (msgs[-1] if msgs else {}), r.headers


def call(name, args, extra=None):
    m, _h = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": name, "arguments": args}}, KEY, SID, extra)
    return m


FK = "oaap-fk-" + "ab" * 32
OTHER = "oaap-fk-" + "cd" * 32


def token(key=FK, tool="comment_case", args=None, by="Jörg Prüf", jti="v1:case:7", exp=3600, iat=0):
    t = time.time()
    return sa.sign(key, {"v": 1, "iss": "vorgaenge", "case": "V-1", "plan": 7, "tool": tool,
                         "args": sa.args_hash(args if args is not None else ARGS), "by": by,
                         "iat": int(t + iat), "exp": int(t + exp), "jti": jti})


print("--- Einrichtung")
admin("/server-save", name="vg", address=f"http://{lan_ip()}:{paper.server_address[1]}/mcp",
      auth="none", light="green")
admin("/server-read", name="vg")
admin("/view-save", name="spez", max_effect="write")
for t, eff in (("get_case", "read"), ("comment_case", "write"), ("close_case", "write")):
    admin("/item-add", view="spez", server="vg", kind="tool", source=t, effect=eff)
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    rc = app.cli(["view", "set", "spez", "--approval", "signed"])
ok("signed ohne Freigabeschlüssel: abgelehnt", rc != 0 and books.view_row(app.DB, "spez")["approval"] == "",
   buf.getvalue())
old_stdin = sys.stdin
sys.stdin = io.StringIO("oaap-fk-zu-kurz\n")
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rc = app.cli(["view", "set", "spez", "--approval", "signed", "--approval-key-stdin", "ja"])
ok("ein Schlüssel in falscher Form: abgelehnt", rc != 0 and books.view_row(app.DB, "spez")["approval"] == "")
sys.stdin = io.StringIO(FK + "\n")
with contextlib.redirect_stdout(io.StringIO()):
    rc = app.cli(["view", "set", "spez", "--approval", "signed", "--approval-key-stdin", "ja"])
sys.stdin = old_stdin
v = books.view_row(app.DB, "spez")
ok("mit Schlüssel: signed, der Schlüssel versiegelt (nicht im Klartext gespeichert)",
   rc == 0 and v["approval"] == "signed" and v["approval_key"] and FK not in v["approval_key"], dict(v))
page = admin_get("/view?name=spez")
ok("die Seite sagt „hinterlegt“ und zeigt den Schlüssel nie", "(hinterlegt)" in page and FK not in page
   and "nur mit unterschriebener Freigabe" in page)

KEY = "mcp-oaap-" + admin("/issue", label="spez", views="spez", max_effect="write",
                          ceiling="yellow").split("mcp-oaap-")[1].split("<")[0].strip()
_m, h = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}, KEY)
SID = h.get("Mcp-Session-Id")
rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, KEY, SID)
ARGS = {"n": "V-2026-0009", "text": "Geprüft: Rolle fehlt."}

print("--- Ablehnungen, bevor etwas hinausgeht")
r = call("get_case", {"n": "V-1"})
ok("ein lesendes Werkzeug braucht keine Freigabe", "erledigt get_case" in json.dumps(r), r)
n = len(CALLS)
cases = [
    ("ohne Freigabe", {}),
    ("nur die Kopfzeilen von Stufe 2", {"X-OAAP-Confirmed": "yes", "X-OAAP-Approved-By": "Jörg"}),
    ("mit einem anderen Schlüssel unterschrieben", {sa.HEADER: token(key=OTHER)}),
    ("für ein anderes Werkzeug", {sa.HEADER: token(tool="close_case")}),
    ("für andere Argumente", {sa.HEADER: token(args={"n": "V-2026-0009", "text": "Alles löschen."})}),
    ("abgelaufen", {sa.HEADER: token(exp=-10, iat=-100)}),
    ("verändert (ein Zeichen der Angaben)", {sa.HEADER: token()[:-50] + ("A" if token()[-50] != "A" else "B")
                                            + token()[-49:]}),
]
for label, extra in cases:
    r = call("comment_case", ARGS, {k: v.encode("utf-8").decode("latin-1") for k, v in extra.items()})
    ok(f"{label}: abgelehnt (-32003)", (r.get("error") or {}).get("code") == -32003, r)
ok("…und nichts ging an den Server", len(CALLS) == n, CALLS[n:])
time.sleep(0.2)
row = app.DB.execute("SELECT outcome, reason, door FROM usage WHERE tool='comment_case' ORDER BY id DESC "
                     "LIMIT 1").fetchone()
ok("die Messzeile: refused, needs_approval, Tür gateway",
   row and (row["outcome"], row["reason"], row["door"]) == ("refused", "needs_approval", "gateway"),
   dict(row) if row else None)

print("--- eine gültige Freigabe")
good = token(jti="vorgaenge:case-9:plan-12")
r = call("comment_case", ARGS, {sa.HEADER: good, "X-OAAP-Approved-By": "Jemand Anderes"})
ok("trägt den Aufruf, genau mit den freigegebenen Argumenten",
   "erledigt comment_case" in json.dumps(r) and CALLS[-1] == ("comment_case", ARGS), (r, CALLS[-1:]))
time.sleep(0.2)
row = app.DB.execute("SELECT outcome, approved_by FROM usage WHERE tool='comment_case' ORDER BY id DESC "
                     "LIMIT 1").fetchone()
ok("die Messzeile nennt die Person aus der Freigabe, nicht aus der Kopfzeile",
   row and row["outcome"] == "ok" and row["approved_by"] == "Jörg Prüf", dict(row) if row else None)
n = len(CALLS)
r = call("comment_case", ARGS, {sa.HEADER: good})
ok("dieselbe Freigabe ein zweites Mal: abgelehnt, nichts ging hinaus",
   (r.get("error") or {}).get("code") == -32003 and "schon benutzt" in r["error"]["message"]
   and len(CALLS) == n, r)
ok("…verbraucht steht sie in approvals_used",
   app.DB.execute("SELECT by FROM approvals_used WHERE jti=?", (good,)).fetchone() is None
   and app.DB.execute("SELECT by FROM approvals_used WHERE jti='vorgaenge:case-9:plan-12'").fetchone()[0]
   == "Jörg Prüf")

print("--- zurück auf required")
admin("/view-save", name="spez", max_effect="write", approval="required", approval_key="")
r = call("comment_case", ARGS, {"X-OAAP-Confirmed": "yes", "X-OAAP-Approved-By": "Jorg"})
ok("required wie in 0.34: die Kopfzeilen reichen wieder (Seite, Schlüssel bleibt hinterlegt)",
   "erledigt comment_case" in json.dumps(r) and books.view_row(app.DB, "spez")["approval_key"], r)
page = admin("/view-save", name="spez", max_effect="write", approval="signed", approval_key="falsch")
ok("auf der Seite: ein Schlüssel in falscher Form wird abgewiesen",
   "oaap-fk-" in page and books.view_row(app.DB, "spez")["approval"] == "required")

gw.shutdown()
print("")
print(f"{passes} passed, {fails} failed")
sys.exit(1 if fails else 0)
