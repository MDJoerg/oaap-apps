"""Multi-System-Sichten, Stufe 2 (RFC-0114 §8, mcp-gateway 0.38.0).

    py -3 apps/mcp-gateway/test_landscape2.py      (auf dem Knoten: python3 …)

Das Gateway läuft echt, per HTTP; dahinter zwei Papier-Server -- zwei „SAP-Systeme“
mit demselben MCP-Dienst, diesmal mit Prompts, Ressourcen und einer Ressourcenvorlage.
Gehalten wird:

  * ein Prompt steht EINMAL, mit dem Pflichtargument `system`; `prompts/get` geht an den
    Server des gewählten Systems, ohne das Argument; ohne/fremdes System abgelehnt, bevor
    etwas hinausgeht; ein abweichender Prompt erst nach der Freigabe für das System;
  * eine Ressource steht je System einmal, die URI mit dem System davor
    (`oaap-system://<system>/<uri>`); gelesen wird beim Server dieses Systems mit der
    URI ohne Präfix; eine Vorlage ebenso; eine URI ohne System, ein fremdes System:
    abgelehnt; die Ampel je System gilt auch hier;
  * `systeme` nennt Prompts und Ressourcen je System;
  * Umwandeln: aus einer Sicht mit Präfix-Namen wird eine NEUE Multi-System-Sicht; eine
    gehaltene Freigabe des zweiten Servers geht mit, die alte Sicht bleibt; ein Eintrag
    ohne Gegenstück oder mit anderer Wirkungsklasse verhindert das Umwandeln; Plan auf
    der Seite und in der Kommandozeile.
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
TMP = tempfile.mkdtemp(prefix="mcpgw-land2-")
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


def prompt(name, desc):
    return {"name": name, "description": desc,
            "arguments": [{"name": "thema", "description": "Worum es geht", "required": True}]}


TOOLS = [tool("find_users", "Sucht Benutzer.", {"pattern": {"type": "string"}}),
         tool("get_role", "Liest eine Rolle.", {"role": {"type": "string"}})]
RES = [{"uri": "sap://doku/readme", "name": "Liesmich", "mimeType": "text/plain"}]
TPL = [{"uriTemplate": "sap://table/{name}", "name": "Tabelle", "mimeType": "text/plain"}]


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
        m, mid, p = body["method"], body["id"], body.get("params") or {}
        res = None
        if m == "initialize":
            self.reply({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}, "prompts": {}, "resources": {}},
                "serverInfo": {"name": S["name"], "version": "1"}}},
                [("Mcp-Session-Id", "s-" + S["name"])])
            return
        if m == "tools/list":
            res = {"tools": S["tools"]}
        elif m == "prompts/list":
            res = {"prompts": S["prompts"]}
        elif m == "resources/list":
            res = {"resources": RES}
        elif m == "resources/templates/list":
            res = {"resourceTemplates": TPL}
        elif m == "tools/call":
            S["calls"].append(("tool", p.get("name"), p.get("arguments")))
            res = {"content": [{"type": "text", "text": f"{S['name']} antwortet {p.get('name')}"}]}
        elif m == "prompts/get":
            S["calls"].append(("prompt", p.get("name"), p.get("arguments")))
            res = {"messages": [{"role": "user", "content": {"type": "text",
                                                             "text": f"{S['name']}: {p.get('name')}"}}]}
        elif m == "resources/read":
            S["calls"].append(("resource", p.get("uri"), None))
            res = {"contents": [{"uri": p.get("uri"), "mimeType": "text/plain",
                                 "text": f"{S['name']} liest {p.get('uri')}"}]}
        self.reply({"jsonrpc": "2.0", "id": mid, "result": res or {}})


def paper(name, tools, prompts):
    srv = ThreadingHTTPServer(("0.0.0.0", 0), Paper)
    srv.state = {"name": name, "tools": tools, "prompts": prompts, "calls": []}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


IP = lan_ip()
A = paper("A4H", TOOLS + [tool("changed", "Text der Referenz.")],
          [prompt("erklaere", "Erklärt etwas."), prompt("anders", "Prompt der Referenz.")])
B = paper("ABAP2025", TOOLS + [tool("changed", "Ein ANDERER Text auf dem zweiten System.")],
          [prompt("erklaere", "Erklärt etwas."), prompt("anders", "Ein ANDERER Prompt.")])

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
    return msgs[-1] if msgs else {}, resp.headers


def session(view, key):
    _m, h = rpc(view, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"}}}, key)
    sid = h.get("Mcp-Session-Id")
    rpc(view, {"jsonrpc": "2.0", "method": "notifications/initialized"}, key, sid)
    return sid


def lst(view, key, sid, method, field):
    m, _h = rpc(view, {"jsonrpc": "2.0", "id": 2, "method": method}, key, sid)
    return (m.get("result") or {}).get(field, [])


def req(view, key, sid, method, params):
    m, _h = rpc(view, {"jsonrpc": "2.0", "id": 3, "method": method, "params": params}, key, sid)
    return m


def secret_from(page):
    return "mcp-oaap-" + page.split("mcp-oaap-")[1].split("<")[0].strip()


def out_calls():
    return len(A.state["calls"]) + len(B.state["calls"])


print("--- Server, Kataloge, Zielsysteme")
for name, srv in (("sys-a", A), ("sys-b", B)):
    admin("/server-save", name=name, address=f"http://{IP}:{srv.server_address[1]}/mcp",
          auth="none", light="green")
    admin("/server-read", name=name)
ok("Kataloge mit Prompts, Ressource und Vorlage",
   len(books.catalog(app.DB, "sys-b", "prompt")) == 2
   and len(books.catalog(app.DB, "sys-b", "resource")) == 1
   and len(books.catalog(app.DB, "sys-b", "template")) == 1)
admin("/system-save", id="a4h", title="A4H Entwicklung", role="development", active="1",
      kind="sap", facts="sid=A4H, client=001", servers="sys-a", servers_seen="1")
admin("/system-save", id="abap2025", title="ABAP 2025", role="production", active="1",
      kind="sap", facts="sid=S25, client=100", servers="sys-b", servers_seen="1")

print("--- Multi-System-Sicht mit Prompts und Ressourcen")
admin("/view-save", name="sap", max_effect="read")
admin("/item-add", view="sap", server="sys-a", kind="tool", source="find_users", effect="read")
for p in ("erklaere", "anders"):
    admin("/item-add", view="sap", server="sys-a", kind="prompt", source=p, effect="read")
admin("/item-add", view="sap", server="sys-a", kind="resource", source="sap://doku/readme",
      effect="read")
admin("/item-add", view="sap", server="sys-a", kind="template", source="sap://table/{name}",
      effect="read")
admin("/view-multi", view="sap", system="a4h")
admin("/view-system-add", view="sap", system="abap2025")
st = {(i["kind"], i["name"]): landscape.availability(app.DB, "sap", i, "abap2025")[0]
      for i in books.items(app.DB, "sap")}
ok("Abgleich auch für Prompts und Ressourcen: erklaere gleich, anders abweichend, "
   "Ressource und Vorlage gleich",
   st[("prompt", "erklaere")] == "gleich" and st[("prompt", "anders")] == "abweichend"
   and st[("resource", "sap://doku/readme")] == "gleich"
   and st[("template", "sap://table/{name}")] == "gleich", st)
vp = admin_get("/view?name=sap")
ok("die Seite zeigt Prompts und Ressourcen in der Matrix, mit „Für abap2025 freigeben“",
   "Prompt" in vp and "Ressourcenvorlage" in vp and 'name="kind" value="prompt"' in vp)

KEY = secret_from(admin("/issue", label="chat-gelb", views="sap", max_effect="read", ceiling="yellow"))
sid = session("sap", KEY)

print("--- Prompts")
pl = {p["name"]: p for p in lst("sap", KEY, sid, "prompts/list", "prompts")}
ok("jeder Prompt einmal", set(pl) == {"erklaere", "anders"}, list(pl))
a0 = (pl["erklaere"].get("arguments") or [{}])[0]
ok("…mit dem Pflichtargument system vorn, das beide Systeme nennt",
   a0.get("name") == "system" and a0.get("required") is True
   and "a4h = A4H Entwicklung" in a0.get("description", "")
   and "abap2025 = ABAP 2025 (Produktion, SID S25, Mandant 100)" in a0.get("description", ""), a0)
ok("…ein abweichender Prompt nennt nur a4h",
   "abap2025" not in pl["anders"]["arguments"][0]["description"], pl["anders"])
r = req("sap", KEY, sid, "prompts/get", {"name": "erklaere",
                                         "arguments": {"system": "abap2025", "thema": "Rollen"}})
ok("prompts/get auf abap2025 geht an dessen Server, ohne system",
   "ABAP2025: erklaere" in json.dumps(r) and B.state["calls"][-1] == ("prompt", "erklaere", {"thema": "Rollen"}),
   (r, B.state["calls"][-1:]))
n = out_calls()
r1 = req("sap", KEY, sid, "prompts/get", {"name": "erklaere", "arguments": {"thema": "x"}})
r2 = req("sap", KEY, sid, "prompts/get", {"name": "erklaere",
                                          "arguments": {"system": "gibts", "thema": "x"}})
r3 = req("sap", KEY, sid, "prompts/get", {"name": "anders",
                                          "arguments": {"system": "abap2025", "thema": "x"}})
ok("ohne System, fremdes System, abweichender Prompt: abgelehnt, nichts ging hinaus",
   "fehlt" in json.dumps(r1, ensure_ascii=False) and "gehört nicht" in json.dumps(r2, ensure_ascii=False)
   and "abweichend" in json.dumps(r3, ensure_ascii=False) and out_calls() == n, (r1, r2, r3))
admin("/view-system-approve", view="sap", system="abap2025", kind="prompt", name="anders")
r = req("sap", KEY, sid, "prompts/get", {"name": "anders",
                                         "arguments": {"system": "abap2025", "thema": "x"}})
ok("nach der Freigabe für abap2025 (Art prompt): angeboten",
   "ABAP2025: anders" in json.dumps(r), r)

print("--- Ressourcen und Vorlagen")
rl = lst("sap", KEY, sid, "resources/list", "resources")
uris = sorted(x["uri"] for x in rl)
ok("die Ressource je System einmal, die URI mit dem System davor",
   uris == ["oaap-system://a4h/sap://doku/readme", "oaap-system://abap2025/sap://doku/readme"], uris)
ok("…der Name nennt das System", any(x["name"] == "Liesmich (ABAP 2025)" for x in rl), rl)
tl = sorted(x["uriTemplate"] for x in lst("sap", KEY, sid, "resources/templates/list",
                                           "resourceTemplates"))
ok("die Vorlage ebenso", tl == ["oaap-system://a4h/sap://table/{name}",
                                "oaap-system://abap2025/sap://table/{name}"], tl)
r = req("sap", KEY, sid, "resources/read", {"uri": "oaap-system://abap2025/sap://doku/readme"})
ok("gelesen beim Server von abap2025, mit der URI ohne Präfix",
   "ABAP2025 liest sap://doku/readme" in json.dumps(r)
   and B.state["calls"][-1] == ("resource", "sap://doku/readme", None), (r, B.state["calls"][-1:]))
r = req("sap", KEY, sid, "resources/read", {"uri": "oaap-system://a4h/sap://table/T000"})
ok("eine Vorlage: beim Server von a4h, sap://table/T000",
   A.state["calls"][-1] == ("resource", "sap://table/T000", None), (r, A.state["calls"][-1:]))
n = out_calls()
r1 = req("sap", KEY, sid, "resources/read", {"uri": "sap://doku/readme"})
r2 = req("sap", KEY, sid, "resources/read", {"uri": "oaap-system://gibts/sap://doku/readme"})
ok("eine URI ohne System, ein fremdes System: abgelehnt, nichts ging hinaus",
   "oaap-system://" in json.dumps(r1) and "gehört nicht" in json.dumps(r2, ensure_ascii=False)
   and out_calls() == n, (r1, r2))
time.sleep(0.2)
row = app.DB.execute("SELECT system, server FROM usage WHERE kind IN ('resource','template') AND outcome='ok' "
                     "ORDER BY id DESC LIMIT 1").fetchone()
ok("die Messzeile nennt System und Server", row and row["system"] == "a4h"
   and row["server"] == "sys-a", dict(row) if row else None)

print("--- Ampel")
GRUEN = secret_from(admin("/issue", label="chat-gruen", views="sap", max_effect="read",
                          ceiling="green"))
sg = session("sap", GRUEN)
gu = sorted(x["uri"] for x in lst("sap", GRUEN, sg, "resources/list", "resources"))
ok("bis grün: die Ressource der Produktion fehlt", gu == ["oaap-system://a4h/sap://doku/readme"], gu)
r = req("sap", GRUEN, sg, "resources/read", {"uri": "oaap-system://abap2025/sap://doku/readme"})
ok("…und Lesen dort wird abgelehnt", "yellow" in json.dumps(r), r)
gp = lst("sap", GRUEN, sg, "prompts/list", "prompts")
ok("…ein Prompt nennt nur a4h", all("abap2025" not in p["arguments"][0]["description"] for p in gp), gp)

print("--- systeme")
r = req("sap", KEY, sid, "tools/call", {"name": "systeme", "arguments": {}})
land = (r.get("result") or {}).get("structuredContent") or {}
s2 = land["systems"][1] if len(land.get("systems", [])) > 1 else {}
ok("nennt je System Prompts und Ressourcen",
   s2.get("prompts") == ["anders", "erklaere"] or sorted(s2.get("prompts", [])) == ["anders", "erklaere"])
ok("…die Ressourcen mit dem Präfix, ohne Adresse",
   "oaap-system://abap2025/sap://doku/readme" in s2.get("resources", [])
   and IP not in json.dumps(r) and "sys-b" not in json.dumps(r), s2)

print("--- Umwandeln")
admin("/view-save", name="alt", max_effect="read")
admin("/item-add", view="alt", server="sys-a", kind="tool", source="find_users", effect="read")
admin("/item-add", view="alt", server="sys-a", kind="tool", source="changed", effect="read")
admin("/item-add", view="alt", server="sys-a", kind="tool", source="get_role", effect="read")
admin("/item-add", view="alt", server="sys-b", kind="tool", source="find_users", effect="read",
      **{"as": "sys-b_find_users"})
admin("/item-add", view="alt", server="sys-b", kind="tool", source="changed", effect="read",
      **{"as": "sys-b_changed"})
before = sorted(i["name"] for i in books.items(app.DB, "alt"))
plan = landscape.convert_plan(app.DB, "alt", "a4h")
rows = {(r["system"], r["name"]): r for r in plan["rows"]}
ok("Plan: keine Hindernisse, abap2025 als weiteres System", plan["problems"] == []
   and plan["systems"] == ["abap2025"], plan["problems"])
ok("…find_users gleich, changed mit übernommener Freigabe, get_role kommt hinzu",
   rows[("abap2025", "find_users")]["old"] == "sys-b_find_users"
   and not rows[("abap2025", "find_users")]["carry"]
   and rows[("abap2025", "changed")]["carry"] and rows[("abap2025", "get_role")]["old"] == "", rows)
vp = admin_get("/view?name=alt&convert=a4h")
ok("die Seite zeigt den Plan und „Neue Multi-System-Sicht anlegen“",
   "Umwandeln" in vp and "Freigabe übernommen" in vp and "Neue Multi-System-Sicht anlegen" in vp)
page = admin("/view-convert", view="alt", system="a4h", new="alt-multi")
ok("umgewandelt: neue Multi-System-Sicht mit Referenz a4h und abap2025",
   landscape.reference_of(app.DB, "alt-multi") == "a4h"
   and landscape.view_system_ids(app.DB, "alt-multi") == ["a4h", "abap2025"], page[-600:])
ok("…nur die Einträge der Referenz, ohne Präfix-Namen",
   sorted(i["name"] for i in books.items(app.DB, "alt-multi")) == ["changed", "find_users", "get_role"])
ok("…changed ist für abap2025 freigegeben, ohne neue Freigabe",
   landscape.availability(app.DB, "alt-multi",
                          books.item_row(app.DB, "alt-multi", "tool", "changed"), "abap2025")[0]
   == "freigegeben")
ok("…die alte Sicht bleibt, wie sie war",
   sorted(i["name"] for i in books.items(app.DB, "alt")) == before
   and not landscape.is_multi(app.DB, "alt"))
KM = secret_from(admin("/issue", label="multi", views="alt-multi", max_effect="read", ceiling="yellow"))
sm = session("alt-multi", KM)
r = req("alt-multi", KM, sm, "tools/call", {"name": "changed", "arguments": {"system": "abap2025"}})
ok("…und im Chat: changed auf abap2025 aufrufbar", "ABAP2025 antwortet changed" in json.dumps(r), r)

admin("/view-save", name="alt2", max_effect="read")
admin("/item-add", view="alt2", server="sys-a", kind="tool", source="find_users", effect="read")
admin("/item-add", view="alt2", server="sys-b", kind="tool", source="get_role", effect="read")
p2 = landscape.convert_plan(app.DB, "alt2", "a4h")
ok("ein Eintrag ohne Gegenstück an der Referenz: kein Umwandeln, mit Satz",
   any("kein Gegenstück" in x for x in p2["problems"]), p2["problems"])
admin("/view-save", name="alt3", max_effect="write")
admin("/item-add", view="alt3", server="sys-a", kind="tool", source="find_users", effect="read")
admin("/item-add", view="alt3", server="sys-b", kind="tool", source="find_users", effect="write",
      **{"as": "b_find"})
p3 = landscape.convert_plan(app.DB, "alt3", "a4h")
ok("eine andere Wirkungsklasse: kein Umwandeln", any("Wirkungsklasse" in x for x in p3["problems"]),
   p3["problems"])
page = admin("/view-convert", view="alt2", system="a4h", new="alt2-multi")
ok("…die Seite sagt es und legt nichts an", books.view_row(app.DB, "alt2-multi") is None
   and "kein Gegenstück" in page)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    rc = app.cli(["view", "convert", "alt", "a4h"])
ok("Kommandozeile: view convert ohne neuen Namen zeigt nur den Plan",
   rc == 0 and "Freigabe übernommen" in buf.getvalue() and books.view_row(app.DB, "alt-multi2") is None,
   buf.getvalue())

print("--- eine gewöhnliche Sicht bleibt")
admin("/view-save", name="einfach", max_effect="read")
admin("/item-add", view="einfach", server="sys-b", kind="resource", source="sap://doku/readme",
      effect="read")
admin("/item-add", view="einfach", server="sys-b", kind="prompt", source="erklaere", effect="read")
KE = secret_from(admin("/issue", label="einfach", views="einfach", max_effect="read", ceiling="yellow"))
se = session("einfach", KE)
eu = [x["uri"] for x in lst("einfach", KE, se, "resources/list", "resources")]
ep = lst("einfach", KE, se, "prompts/list", "prompts")
ok("URIs ohne Präfix, Prompts ohne system",
   eu == ["sap://doku/readme"] and [a["name"] for a in ep[0]["arguments"]] == ["thema"], (eu, ep))
r = req("einfach", KE, se, "resources/read", {"uri": "sap://doku/readme"})
ok("…und lesbar wie bisher", "ABAP2025 liest sap://doku/readme" in json.dumps(r), r)

gw.shutdown()
print("")
print(f"{passes} passed, {fails} failed")
sys.exit(1 if fails else 0)
