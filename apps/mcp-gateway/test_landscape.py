"""Systemlandschaft und Multi-System-Sichten (RFC-0114 Stufe 1, mcp-gateway 0.32.0).

    py -3 apps/mcp-gateway/test_landscape.py      (auf dem Knoten: python3 …)

Das Gateway läuft echt, per HTTP; dahinter zwei Papier-Server -- zwei „SAP-Systeme“
mit demselben MCP-Dienst. Auf dem zweiten hat ein Werkzeug einen anderen Text, ein
anderes fehlt. Gehalten wird:

  * Zielsysteme: Titel, Rolle, aktiv, Angaben (nie ein Geheimnis), Server (einer reicht
    an genau einen Ort), Ampel aus der Rolle (Produktion gelb), nie lockerer als ein Server;
  * eine Multi-System-Sicht wird am Referenzsystem ausgeprägt; ein Eintrag eines anderen
    Systems wird abgewiesen; je Werkzeug und System gleich / abweichend / fehlt;
  * im Chat steht jedes Werkzeug EINMAL, mit dem Pflichtargument `system` (nur aktive
    Systeme, deren Ampel reicht, auf denen es angeboten ist), dazu `systeme`;
  * der Aufruf geht an den Server des gewählten Systems, ohne das Argument `system`;
    fehlendes, fremdes, abgeschaltetes System oder ein abweichendes Werkzeug: abgelehnt,
    bevor etwas hinausgeht (S2); eine Freigabe für das System öffnet es;
  * `systeme` nennt nie Adresse, Server oder Zugang (S1); die Messzeile nennt das System;
  * Einrichtungstür (Art `system`), Kommandozeile, Betreiberseite.
"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="mcpgw-land-")
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


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def tool(name, desc, props=None):
    return {"name": name, "description": desc, "annotations": {"readOnlyHint": True},
            "inputSchema": {"type": "object", "properties": props or {},
                            "required": list(props or {})}}


SAME = [tool("find_users", "Sucht Benutzer.", {"pattern": {"type": "string"}}),
        tool("get_role", "Liest eine Rolle.", {"role": {"type": "string"}})]


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
        S = self.server.state
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if "method" not in body or body.get("id") is None:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        m, mid = body["method"], body["id"]
        if m == "initialize":
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
                "serverInfo": {"name": S["name"], "version": "1"}}},
                [("Mcp-Session-Id", "s-" + S["name"])])
            return
        if m == "tools/list":
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {"tools": S["tools"]}})
            return
        if m == "tools/call":
            p = body.get("params") or {}
            S["calls"].append((p.get("name"), p.get("arguments")))
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {"content": [
                {"type": "text", "text": f"{S['name']} antwortet {p.get('name')}"}]}})
            return
        self.reply({"jsonrpc": "2.0", "id": mid, "result": {}})


def paper(name, tools):
    srv = ThreadingHTTPServer(("0.0.0.0", 0), Paper)
    srv.state = {"name": name, "tools": tools, "calls": []}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


IP = lan_ip()
A = paper("A4H", SAME + [tool("changed", "Text der Referenz."),
                         tool("only_a", "Nur auf A4H.")])
B = paper("ABAP2025", SAME + [tool("changed", "Ein ANDERER Text auf dem zweiten System.")])

import app  # noqa: E402
import books  # noqa: E402
import landscape  # noqa: E402

gw = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=gw.serve_forever, daemon=True).start()
GW = f"http://127.0.0.1:{gw.server_address[1]}"


def admin(path, **form):
    data = urlencode([(k, x) for k, v in form.items()
                      for x in (v if isinstance(v, list) else [v])]).encode()
    req = urllib.request.Request(GW + path, data=data, method="POST",
                                 headers={"X-OAAP-User": "betreiber", "X-OAAP-Roles": "admin",
                                          "Host": "gw.example"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def admin_get(path):
    req = urllib.request.Request(GW + path, headers={"X-OAAP-User": "betreiber",
                                                     "X-OAAP-Roles": "admin"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode()


def rpc(view, msg, key, sid=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + key}
    if sid:
        headers["Mcp-Session-Id"] = sid
    req = urllib.request.Request(f"{GW}/mcp/{view}", data=json.dumps(msg).encode(),
                                 headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        resp = exc
    raw = resp.read().decode()
    msgs = []
    for block in raw.split("\n\n"):
        data = [ln[6:] for ln in block.split("\n") if ln.startswith("data: ")]
        if data:
            msgs.append(json.loads("\n".join(data)))
    if not msgs and raw.strip().startswith("{"):
        msgs.append(json.loads(raw))
    return resp.status, resp.headers, msgs


def session(view, key):
    _st, h, _m = rpc(view, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}}}, key)
    sid = h.get("Mcp-Session-Id")
    rpc(view, {"jsonrpc": "2.0", "method": "notifications/initialized"}, key, sid)
    return sid


def listed(view, key, sid):
    _s, _h, msgs = rpc(view, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, key, sid)
    return {t["name"]: t for t in (msgs[-1].get("result") or {}).get("tools", [])} if msgs else {}


def call(view, key, sid, name, args):
    _s, _h, msgs = rpc(view, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                              "params": {"name": name, "arguments": args}}, key, sid)
    return msgs[-1] if msgs else {}


def enum(t):
    return ((t.get("inputSchema") or {}).get("properties") or {}).get("system", {}).get("enum")


def secret_from(page):
    return "mcp-oaap-" + page.split("mcp-oaap-")[1].split("<")[0].strip()


print("--- Server und Kataloge")
for name, srv in (("sys-a", A), ("sys-b", B)):
    admin("/server-save", name=name, address=f"http://{IP}:{srv.server_address[1]}/mcp",
          auth="none", light="green")
    admin("/server-read", name=name)
ok("zwei Server mit Katalog", len(books.catalog(app.DB, "sys-a", "tool")) == 4
   and len(books.catalog(app.DB, "sys-b", "tool")) == 3)

print("--- Zielsysteme")
admin("/system-save", id="a4h", title="A4H Entwicklung", role="development", active="1",
      kind="sap", facts="sid=A4H, client=001", light="", description="Demo", servers="sys-a",
      servers_seen="1")
admin("/system-save", id="abap2025", title="ABAP 2025", role="production", active="1",
      kind="sap", facts="sid=A4H, client=001", light="", description="", servers="sys-b",
      servers_seen="1")
ok("angelegt, mit Servern", landscape.servers_of(app.DB, "a4h") == ["sys-a"]
   and landscape.servers_of(app.DB, "abap2025") == ["sys-b"])
ok("die Ampel kommt aus der Rolle: Entwicklung grün (wie der Server), Produktion gelb",
   landscape.system_light(app.DB, landscape.system_row(app.DB, "a4h")) == "green"
   and landscape.system_light(app.DB, landscape.system_row(app.DB, "abap2025")) == "yellow")
page = admin("/system-save", id="boese", title="X", role="other", facts="password=geheim",
             servers_seen="1")
ok("eine Angabe, die nach Geheimnis klingt: abgewiesen", landscape.system_row(app.DB, "boese") is None
   and "Geheimnis" in page)
page = admin("/system-save", id="doppelt", title="X", role="other", servers="sys-a", servers_seen="1")
ok("ein Server gehört zu genau einem System", landscape.system_row(app.DB, "doppelt") is None
   and "schon zum Zielsystem" in page)
srow = books.server_row(app.DB, "sys-a")
books.save_server(app.DB, "t", "sys-a", srow["address"], "none", "red", {})
ok("nie lockerer als ein Server (S5): Server rot -> System rot",
   landscape.system_light(app.DB, landscape.system_row(app.DB, "a4h")) == "red")
books.save_server(app.DB, "t", "sys-a", srow["address"], "none", "green", {})

print("--- Multi-System-Sicht")
admin("/view-save", name="sap", max_effect="read")
for t in ("find_users", "get_role", "changed", "only_a"):
    admin("/item-add", view="sap", server="sys-a", kind="tool", source=t, effect="read")
admin("/view-multi", view="sap", system="a4h")
ok("Referenz gesetzt", landscape.reference_of(app.DB, "sap") == "a4h")
page = admin("/item-add", view="sap", server="sys-b", kind="tool", source="find_users",
             effect="read", **{"as": "find_users_b"})
ok("ein Eintrag eines anderen Systems wird abgewiesen",
   books.item_row(app.DB, "sap", "tool", "find_users_b") is None and "Referenzsystem" in page)
admin("/view-system-add", view="sap", system="abap2025")
st = {i["name"]: landscape.availability(app.DB, "sap", i, "abap2025")[0]
      for i in books.items(app.DB, "sap", "tool")}
ok("Abgleich: find_users/get_role gleich, changed abweichend, only_a fehlt",
   st == {"find_users": "gleich", "get_role": "gleich", "changed": "abweichend", "only_a": "fehlt"}, st)
vp = admin_get("/view?name=sap")
ok("die Seite der Sicht zeigt den Abgleich und „Für abap2025 freigeben“",
   "Multi-System-Sicht" in vp and "abweichend" in vp and "Für abap2025 freigeben" in vp)

print("--- im Chat")
KEY = secret_from(admin("/issue", label="chat-gelb", views="sap", max_effect="read", ceiling="yellow"))
sid = session("sap", KEY)
tl = listed("sap", KEY, sid)
ok("jedes Werkzeug einmal, keine Präfix-Kopien; dazu „systeme“",
   set(tl) == {"find_users", "get_role", "changed", "only_a", "systeme"}, list(tl))
ok("find_users: system ist Pflicht, Auswahl beide Systeme",
   enum(tl["find_users"]) == ["a4h", "abap2025"]
   and tl["find_users"]["inputSchema"]["required"][0] == "system", tl["find_users"])
ok("…die Beschreibung nennt Titel und Rolle",
   "ABAP 2025 (Produktion, SID A4H, Mandant 001)" in
   tl["find_users"]["inputSchema"]["properties"]["system"]["description"])
ok("changed (abweichend) und only_a (fehlt): nur a4h",
   enum(tl["changed"]) == ["a4h"] and enum(tl["only_a"]) == ["a4h"])
r = call("sap", KEY, sid, "find_users", {"system": "abap2025", "pattern": "M*"})
ok("Aufruf auf abap2025 geht an dessen Server, ohne das Argument system",
   "ABAP2025 antwortet" in json.dumps(r) and B.state["calls"][-1] == ("find_users", {"pattern": "M*"}),
   (r, B.state["calls"]))
nb = len(B.state["calls"]) + len(A.state["calls"])
for args, word in (({"pattern": "M*"}, "fehlt"), ({"system": "gibts", "pattern": "x"}, "gehört nicht"),):
    r = call("sap", KEY, sid, "find_users", args)
    ok(f"ohne/fremdes System: abgelehnt ({word}), nichts ging hinaus",
       "error" in r and word in r["error"]["message"]
       and len(B.state["calls"]) + len(A.state["calls"]) == nb, r)
r = call("sap", KEY, sid, "changed", {"system": "abap2025"})
ok("ein abweichendes Werkzeug auf abap2025: abgelehnt", "error" in r and "abweichend" in r["error"]["message"], r)
admin("/view-system-approve", view="sap", system="abap2025", name="changed")
ok("nach der Freigabe für abap2025: angeboten",
   enum(listed("sap", KEY, sid)["changed"]) == ["a4h", "abap2025"])
r = call("sap", KEY, sid, "changed", {"system": "abap2025"})
ok("…und aufrufbar", "ABAP2025 antwortet changed" in json.dumps(r), r)
row = app.DB.execute("SELECT system, server FROM usage WHERE tool='changed' ORDER BY id DESC "
                     "LIMIT 1").fetchone()
time.sleep(0.2)
row = app.DB.execute("SELECT system, server FROM usage WHERE tool='changed' ORDER BY id DESC "
                     "LIMIT 1").fetchone()
ok("die Messzeile nennt System und Server", row and row["system"] == "abap2025"
   and row["server"] == "sys-b", dict(row) if row else None)

print("--- systeme (S1)")
r = call("sap", KEY, sid, "systeme", {})
text = json.dumps(r)
land = (r.get("result") or {}).get("structuredContent") or {}
ok("nennt beide Systeme mit Titel, Rolle, aktiv, Ampel, Werkzeugen",
   [s["id"] for s in land.get("systems", [])] == ["a4h", "abap2025"]
   and land["systems"][1]["role_label"] == "Produktion" and land["systems"][1]["light"] == "yellow"
   and "only_a" not in land["systems"][1]["tools"], land)
ok("…nie Adresse, Server oder Zugang", IP not in text and "sys-b" not in text
   and "http" not in text, text[:400])

print("--- Ampel und abgeschaltet")
GRUEN = secret_from(admin("/issue", label="chat-gruen", views="sap", max_effect="read",
                          ceiling="green"))
sg = session("sap", GRUEN)
ok("ein Zugang bis grün: die Produktion (gelb) fehlt in der Auswahl",
   enum(listed("sap", GRUEN, sg)["find_users"]) == ["a4h"])
r = call("sap", GRUEN, sg, "find_users", {"system": "abap2025", "pattern": "x"})
ok("…und ein Aufruf dorthin wird abgelehnt", "error" in r and "yellow" in r["error"]["message"], r)
admin("/system-save", id="abap2025", title="ABAP 2025", role="production", active="0", kind="sap",
      facts="sid=A4H, client=001", servers="sys-b", servers_seen="1")
ok("abgeschaltet: sofort nicht mehr in der Auswahl",
   enum(listed("sap", KEY, sid)["find_users"]) == ["a4h"])
r = call("sap", KEY, sid, "find_users", {"system": "abap2025", "pattern": "x"})
ok("…und abgelehnt", "error" in r and "abgeschaltet" in r["error"]["message"], r)
page = admin("/system-delete", id="abap2025")
ok("ein System, das eine Sicht nennt, wird nicht gelöscht",
   landscape.system_row(app.DB, "abap2025") is not None and "steht in Sichten" in page)

print("--- Einrichtungstür, Kommandozeile, Betreiberseite")
planned = set()
res = app.setup_item({"kind": "system", "id": "qas", "title": "QAS", "role": "quality",
                      "type": "sap", "servers": []}, True, "rezept", planned)
ok("Einrichtungstür, Art system: dry would_make", res[0] == "would_make", res)
res = app.setup_item({"kind": "system", "id": "qas", "title": "QAS", "role": "quality",
                      "type": "sap", "servers": []}, False, "rezept", planned)
res2 = app.setup_item({"kind": "system", "id": "qas", "title": "QAS", "role": "quality",
                       "type": "sap", "servers": []}, False, "rezept", planned)
ok("…angelegt, dann present", res[0] == "made" and res2[0] == "present", (res, res2))
res = app.setup_item({"kind": "system", "id": "qas2", "title": "Q", "facts": {"token": "x"}},
                     True, "rezept", planned)
ok("…ein Geheimnis in den Angaben: refused", res[0] == "refused", res)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = app.cli(["view", "systems", "sap"])
ok("Kommandozeile: view systems zeigt den Abgleich",
   rc == 0 and "find_users" in buf.getvalue() and "gleich" in buf.getvalue(), buf.getvalue())
tab = admin_get("/?tab=systeme")
ok("Betreiberseite: Reiter Systemlandschaft mit den Systemen",
   "Systemlandschaft" in tab and "A4H Entwicklung" in tab and "Produktion" in tab)
admin("/view-save", name="einfach", max_effect="read")
admin("/item-add", view="einfach", server="sys-b", kind="tool", source="find_users", effect="read")
EINFACH = secret_from(admin("/issue", label="einfach", views="einfach", max_effect="read",
                            ceiling="yellow"))
se = session("einfach", EINFACH)
te = listed("einfach", EINFACH, se)
ok("eine gewöhnliche Sicht bleibt, wie sie war: kein system-Argument, kein „systeme“",
   set(te) == {"find_users"} and enum(te["find_users"]) is None, te)

gw.shutdown()
print("")
print(f"{passes} passed, {fails} failed")
sys.exit(1 if fails else 0)
