"""Tests des Chats: Schleife (loop.py) und die App gegen eine nachgebaute Plattform.

Die nachgebaute Plattform hat drei Türen: die Gateway-Liste, die via-Tür (prüft
Client-Schlüssel und Zusicherung wie die echte) und ein Modell mit Drehbuch.
Aufruf: py -3 test_chat.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = []


def check(name, ok, info=None):
    RESULTS.append(bool(ok))
    print(("  PASS  " if ok else "  FAIL  ") + name + ("" if ok or info is None else f"  {info!r}"[:600]))


# ------------------------------------------------------------ loop.py allein
import loop  # noqa: E402

tools, eff = loop.tools_for_model([
    {"name": "a", "inputSchema": {"type": "object"}, "_meta": {"oaap/effect": "read"}},
    {"name": "b", "_meta": {"oaap/effect": "execute"}},
    {"name": "c", "annotations": {"readOnlyHint": True}},
    {"name": "d", "_meta": {"oaap/effect": "allmighty"}}, "kein-objekt"])
check("loop: Wirkungsklasse nur vom Gateway; ohne Angabe oder unbekannt = write",
      eff == {"a": "read", "b": "execute", "c": "write", "d": "write"}, eff)
check("loop: Werkzeuge in OpenAI-Form, leeres Schema wird ein Objekt",
      [t["function"]["name"] for t in tools] == ["a", "b", "c", "d"]
      and tools[1]["function"]["parameters"] == {"type": "object", "properties": {}}, tools)
msgs = [{"role": "user", "content": "x"},
        {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "a", "arguments": "{}"}},
                                             {"id": "2", "function": {"name": "b", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "1", "content": "ok"}]
check("loop: offen ist nur der Aufruf ohne Ergebnis", [c["id"] for c in loop.pending_calls(msgs)] == ["2"])
check("loop: nach einer neuen Frage ist nichts mehr offen",
      loop.pending_calls(msgs + [{"role": "user", "content": "y"}]) == [])
check("loop: Argumente als Text, kaputt, kein Objekt",
      loop.call_args({"function": {"arguments": '{"p": 1}'}}) == ({"p": 1}, "")
      and loop.call_args({"function": {"arguments": "{"}})[0] is None
      and loop.call_args({"function": {"arguments": "[1]"}})[0] is None)
check("loop: Ergebnis -- Text, isError, Fehler",
      loop.result_text([{"result": {"content": [{"type": "text", "text": "pA"}]}}]) == "pA"
      and loop.result_text([{"result": {"isError": True, "content": [{"type": "text", "text": "x"}]}}])
      == "Fehler: x"
      and loop.result_text([{"error": {"message": "nein"}}]) == "Fehler: nein")
fm = loop.for_model([{"role": "user", "content": "x", "at": "t"},
                     {"role": "tool", "tool_call_id": "1", "content": "r", "confirmed": True, "name": "a"}])
check("loop: eigene Vermerke gehen nicht ans Modell",
      fm == [{"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "1", "content": "r"}], fm)

# ------------------------------------------------------ nachgebaute Plattform
CK = "client-key-123"
DOOR = []          # (Werkzeug, Zusicherung, Assistent)
MODEL = []         # Rümpfe an das Modell
BAD_ASSERTION = {"on": False}
CONFIRMED = []     # X-OAAP-Confirmed je Aufruf an der Tür
# fachliche Berechtigungen (RFC-0045) wie der Dienst der Plattform sie nennt
GRANTS = {
    "id-anna": [{"object": "assistant", "activities": ["use", "manage"], "fields": {"team": ["service"]}}],
    "id-bert": [{"object": "assistant", "activities": ["use"], "fields": {"team": ["service"]}}],
    "id-carl": [],
}
AUTHZ_DOWN = {"on": False}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, status, doc, headers=()):
        data = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/authz/effective?user="):
            uid = self.path.split("=", 1)[1]
            if AUTHZ_DOWN["on"] or self.headers.get("Authorization") != "Bearer AZ":
                return self.reply(503, {})
            return self.reply(200, {"user": uid, "grants": GRANTS.get(uid, []), "fresh_for": 0})
        if self.path == "/v1/models":
            return self.reply(200, {"object": "list", "data": [{"id": "standard"}, {"id": "klein"}]})
        if self.path == "/gw" and self.headers.get("Authorization") == "Bearer " + CK:
            port = self.server.server_address[1]
            return self.reply(200, {"v": 1, "gateways": [
                {"name": "gw1", "via": f"http://127.0.0.1:{port}/via/gw1"}]})
        self.reply(401, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/via/gw1/"):
            a = self.headers.get("X-OAAP-Act-Assertion", "")
            if self.headers.get("Authorization") != "Bearer " + CK or not a.startswith("ZS-") \
                    or BAD_ASSERTION["on"]:
                return self.reply(401, {"error": "nein"})
            if self.path == "/via/gw1/vg":
                return self.vg(body, a)
            if self.path == "/via/gw1/zu-rot":
                return self.reply(403, {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32000, "message": "Angemeldet, aber die Ampel der Sicht zu-rot ist red, "
                                               "das Zugangsprofil reicht bis yellow -- die Ampel der Quelle "
                                               "senken oder die des Profils erhöhen."}})
            if self.path == "/via/gw1/ohne-profil":
                return self.reply(403, {"jsonrpc": "2.0", "id": None, "error": {
                    "code": -32000, "message": "Angemeldet, aber kein Zugangsprofil erlaubt diese Sicht."}})
            if self.path != "/via/gw1/kp-i":
                return self.reply(404, {})
            m = body.get("method")
            if m == "initialize":
                return self.reply(200, {"jsonrpc": "2.0", "id": 1, "result": {}},
                                  [("Mcp-Session-Id", "S-" + a.split("-")[1])])
            if m == "notifications/initialized":
                return self.reply(202, {})
            if self.headers.get("Mcp-Session-Id") != "S-" + a.split("-")[1]:
                return self.reply(404, {})      # Sitzung gehört einer anderen Person
            if m == "tools/list":
                return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
                    {"name": "list_projects", "inputSchema": {"type": "object"},
                     "_meta": {"oaap/effect": "read"}},
                    {"name": "add_entry", "inputSchema": {"type": "object"},
                     "_meta": {"oaap/effect": "write"}, "annotations": {"readOnlyHint": True}},
                    {"name": "legacy", "inputSchema": {"type": "object"}}]}})
            if m == "prompts/list":
                return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"prompts": [
                    {"name": "wochenbericht", "description": "Bericht für ein Projekt",
                     "arguments": [{"name": "projekt", "required": True}]}]}})
            if m == "prompts/get":
                p = body["params"]
                if p["name"] != "wochenbericht":
                    return self.reply(200, {"jsonrpc": "2.0", "id": 2, "error": {"code": -32602, "message": "unbekannt"}})
                return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"messages": [
                    {"role": "user", "content": {"type": "text",
                                                 "text": f"Fasse die Woche in Projekt {p['arguments'].get('projekt')} zusammen."}}]}})
            if m == "tools/call":
                p = body["params"]
                DOOR.append((p["name"], a, self.headers.get("X-OAAP-Assistant"), p["arguments"]))
                CONFIRMED.append(self.headers.get("X-OAAP-Confirmed", ""))
                return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {
                    "content": [{"type": "text", "text": f"Ergebnis {p['name']} für {a.split('-')[1]}"}]}})
        if self.path == "/v1/chat/completions":
            MODEL.append(body)
            last = body["messages"][-1]
            users = [m for m in body["messages"] if m["role"] == "user"]
            q = users[-1]["content"] if users else ""
            call = None
            if q == "Schleife":
                call = ("list_projects", "{}")
            elif last["role"] == "user":
                call = {"Projekte?": ("list_projects", "{}"), "Karte?": ("karte", '{"nr": "V-1"}'),
                        "Eintrag": ("add_entry", '{"project": "pA", "text": "neu"}'),
                        "Alt": ("legacy", "{}"), "Erfunden": ("delete_all", "{}"),
                        "Kaputt": ("list_projects", "{nicht json")}.get(q)
            if call:
                msg = {"role": "assistant", "content": "", "tool_calls": [
                    {"id": f"c{len(MODEL)}", "type": "function",
                     "function": {"name": call[0], "arguments": call[1]}}]}
            else:
                msg = {"role": "assistant", "content": "Fertig: " + str(last.get("content"))[:80]}
            return self.reply(200, {"choices": [{"message": msg}]})
        self.reply(404, {})


VG_INIT, VG_CALLS = [], []
VG_HTML = "<!doctype html><html><head><title>Karte</title></head><body><p>Karte</p></body></html>"


def _vg(self, body, a):
    m = body.get("method")
    if m == "initialize":
        VG_INIT.append(body["params"].get("capabilities"))
        return self.reply(200, {"jsonrpc": "2.0", "id": 1, "result": {}}, [("Mcp-Session-Id", "V-" + a.split("-")[1])])
    if m == "notifications/initialized":
        return self.reply(202, {})
    if m == "tools/list":
        return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"tools": [
            {"name": "karte", "inputSchema": {"type": "object"},
             "_meta": {"oaap/effect": "read", "ui": {"resourceUri": "ui://karte/v1"}}},
            {"name": "nur_app", "inputSchema": {"type": "object"},
             "_meta": {"oaap/effect": "read", "ui": {"resourceUri": "ui://karte/v1", "visibility": ["app"]}}},
            {"name": "nur_modell", "inputSchema": {"type": "object"},
             "_meta": {"oaap/effect": "read", "ui": {"resourceUri": "ui://karte/v1", "visibility": ["model"]}}},
            {"name": "schreiben", "inputSchema": {"type": "object"}, "_meta": {"oaap/effect": "write"}}]}})
    if m == "resources/read":
        uri = body["params"]["uri"]
        if uri != "ui://karte/v1":
            return self.reply(200, {"jsonrpc": "2.0", "id": 2, "error": {"code": -32002, "message": "nicht freigegeben"}})
        return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {"contents": [
            {"uri": uri, "mimeType": "text/html;profile=mcp-app", "text": VG_HTML,
             "_meta": {"ui": {"csp": {"connectDomains": ["https://evil.example"]}, "prefersBorder": True}}}]}})
    if m == "tools/call":
        p_ = body["params"]
        VG_CALLS.append((p_["name"], p_["arguments"], self.headers.get("X-OAAP-Confirmed", ""), a))
        return self.reply(200, {"jsonrpc": "2.0", "id": 2, "result": {
            "content": [{"type": "text", "text": json.dumps({"nr": "V-1", "titel": "Drucker"})}],
            "structuredContent": {"nr": "V-1"}}})
    return self.reply(200, {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "?"}})


Fake.vg = _vg
fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
FP = fake.server_address[1]
DATA = tempfile.mkdtemp(prefix="chat-test-")
os.environ.update({
    "CHAT_DATA_DIR": DATA, "OAAP_MCP_CLIENT_KEY": CK, "OAAP_MCP_CLIENT_URL": f"http://127.0.0.1:{FP}/gw",
    "OAAP_DESTINATION_KI_URL": f"http://127.0.0.1:{FP}/v1/",
    "CHAT_ASSISTANT": json.dumps({"id": "projekte", "title": "Projekte", "view": "kp-i",
                                  "system": "Du hilfst bei Projekten.", "commands": True}),
    "OAAP_AUTHZ_URL": f"http://127.0.0.1:{FP}/authz", "OAAP_AUTHZ_KEY": "AZ",
    "OAAP_INSTANCE_NAMES": "https://chat.example.org,https://chat.vuebung.node.example",
})
import app  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
N = {"n": 0}


def req(method, path, body=None, person="anna", assertion=True, roles="user"):
    h = {"Content-Type": "application/json"}
    if person:
        h["X-OAAP-User-Id"] = "id-" + person
        h["X-OAAP-User"] = person
        h["X-OAAP-Roles"] = roles
    if assertion:
        N["n"] += 1
        h["X-OAAP-User-Assertion"] = f"ZS-{person}-{N['n']}"
    r = urllib.request.Request(BASE + path, method=method, headers=h,
                               data=None if body is None else json.dumps(body).encode())
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


check("Assistent aus CHAT_ASSISTANT gültig", app.SETUP_ERRORS == [], app.SETUP_ERRORS)
st, _ = req("GET", "/api/state", person=None)
check("ohne Person: 401", st == 401)
st, s = req("GET", "/api/state")
check("Zustand: Assistent, Client, Modell, keine Gespräche",
      st == 200 and [a["id"] for a in s["assistants"]] == ["projekte"] and s["client"] and s["model"]
      and s["conversations"] == [], s)
_, c = req("POST", "/api/conv", {"assistant": "projekte"})
cid = c["id"]
st, r = req("POST", f"/api/conv/{cid}/send", {"text": "Projekte?"}, assertion=False)
check("ohne Zusicherung keine Runde: 401, kein Modellzug (§5.2)", st == 401 and not MODEL, r)

st, r = req("POST", f"/api/conv/{cid}/send", {"text": "Projekte?"})
check("Frage: das Modell will ein Werkzeug -> Zustand more, noch kein Aufruf an der Tür",
      st == 200 and r["state"] == "more" and not DOOR, r)
m0 = MODEL[0]
check("Modellzug: Name des Assistenten, drei Werkzeuge der Sicht, Systemprompt mit Datum und Person",
      m0["model"] == "standard" and len(m0["tools"]) == 3
      and m0["messages"][0]["role"] == "system" and "Du hilfst bei Projekten." in m0["messages"][0]["content"]
      and "Heute ist" in m0["messages"][0]["content"] and "anna" in m0["messages"][0]["content"], m0)
check("... keine Zusicherung im Rumpf an das Modell", "ZS-" not in json.dumps(m0))
n_before = N["n"]
st, r = req("POST", f"/api/conv/{cid}/step", {})
check("nächste Runde: lesendes Werkzeug läuft ohne Rückfrage, dann antwortet das Modell",
      r["state"] == "done" and DOOR[-1][0] == "list_projects"
      and r["messages"][-1]["content"].startswith("Fertig: Ergebnis list_projects für anna"), r)
check("... mit der Zusicherung DIESER Anfrage und dem Assistenten als Vermerk",
      DOOR[-1][1] == f"ZS-anna-{n_before + 1}" and DOOR[-1][2] == "projekte", DOOR[-1])

dump = ""
for root, _, files in os.walk(DATA):
    for f in files:
        dump += open(os.path.join(root, f), encoding="utf-8").read()
check("Verlauf als Datei unter people/<Person>/, ohne eine Zusicherung darin",
      os.path.exists(os.path.join(DATA, "people", "id-anna", cid + ".json")) and "ZS-" not in dump)

# D6: schreiben nur nach Zustimmung
calls = len(DOOR)
req("POST", f"/api/conv/{cid}/send", {"text": "Eintrag"})
st, r = req("POST", f"/api/conv/{cid}/step", {})
check("schreibendes Werkzeug: Zustand confirm mit Name und Argumenten, die Tür nicht gerufen",
      r["state"] == "confirm" and r["confirm"]["name"] == "add_entry"
      and r["confirm"]["arguments"] == {"project": "pA", "text": "neu"} and len(DOOR) == calls, r)
check("... der readOnlyHint der Quelle macht es nicht zum lesenden", r["confirm"]["effect"] == "write")
st, r2 = req("POST", f"/api/conv/{cid}/send", {"text": "noch was"})
check("neue Nachricht, solange eine Bestätigung offen ist: 409", st == 409, r2)
st, r2 = req("POST", f"/api/conv/{cid}/step", {})
check("Runde ohne Entscheidung bleibt bei confirm", r2.get("state") == "confirm" and len(DOOR) == calls, r2)
st, r = req("POST", f"/api/conv/{cid}/step", {"decision": {"call": r["confirm"]["call"], "ok": False}})
tool_msg = [m for m in r["messages"] if m["role"] == "tool"][-1]
check("abgelehnt: Tür nicht gerufen, Ergebnis sagt es dem Modell, Vermerk confirmed=false",
      len(DOOR) == calls and tool_msg["content"] == loop.REFUSED and tool_msg["confirmed"] is False
      and r["state"] == "done", r)
req("POST", f"/api/conv/{cid}/send", {"text": "Eintrag"})
_, r = req("POST", f"/api/conv/{cid}/step", {})
st, r = req("POST", f"/api/conv/{cid}/step", {"decision": {"call": "falsche-id", "ok": True}})
check("Zustimmung zu einem anderen Aufruf zählt nicht", r["state"] == "confirm" and len(DOOR) == calls, r)
st, r = req("POST", f"/api/conv/{cid}/step", {"decision": {"call": r["confirm"]["call"], "ok": True}})
check("zugestimmt: genau ein Aufruf an der Tür, Vermerk confirmed=true",
      len(DOOR) == calls + 1 and DOOR[-1][0] == "add_entry" and DOOR[-1][3] == {"project": "pA", "text": "neu"}
      and [m for m in r["messages"] if m["role"] == "tool"][-1]["confirmed"] is True, r)

req("POST", f"/api/conv/{cid}/send", {"text": "Alt"})
_, r = req("POST", f"/api/conv/{cid}/step", {})
check("Werkzeug ohne Angabe des Gateways: wird erfragt", r["state"] == "confirm" and r["confirm"]["name"] == "legacy", r)
req("POST", f"/api/conv/{cid}/step", {"decision": {"call": r["confirm"]["call"], "ok": False}})

calls = len(DOOR)
req("POST", f"/api/conv/{cid}/send", {"text": "Erfunden"})
_, r = req("POST", f"/api/conv/{cid}/step", {})
check("erfundenes Werkzeug: nicht gerufen, Fehler als Ergebnis",
      len(DOOR) == calls and "gibt es in dieser Sicht nicht" in
      [m for m in r["messages"] if m["role"] == "tool"][-1]["content"], r)
req("POST", f"/api/conv/{cid}/send", {"text": "Kaputt"})
_, r = req("POST", f"/api/conv/{cid}/step", {})
check("kaputte Argumente: nicht gerufen, Fehler als Ergebnis",
      len(DOOR) == calls and "kein JSON" in [m for m in r["messages"] if m["role"] == "tool"][-1]["content"], r)

# fremde Person
st, r = req("GET", f"/api/conv/{cid}", person="bert")
check("bert liest annas Gespräch nicht (404) und sieht keine Gespräche",
      st == 404 and req("GET", "/api/state", person="bert")[1]["conversations"] == [], r)
st, r = req("POST", f"/api/conv/{cid}/send", {"text": "Projekte?"}, person="bert")
check("bert schreibt nicht in annas Gespräch (404)", st == 404, r)

# Tür lehnt ab -> Satz, kein Rückfall
_, c2 = req("POST", "/api/conv", {"assistant": "projekte"})
req("POST", f"/api/conv/{c2['id']}/send", {"text": "Projekte?"})
BAD_ASSERTION["on"] = True
app._MCP.clear()
calls = len(DOOR)
st, r = req("POST", f"/api/conv/{c2['id']}/step", {})
BAD_ASSERTION["on"] = False
check("Tür lehnt die Zusicherung ab: Zustand error mit Satz, nichts aufgerufen, Aufruf bleibt offen",
      r["state"] == "error" and "401" in r["said"] and len(DOOR) == calls
      and loop.pending_calls(app.load_conv("id-anna", c2["id"])["messages"]), r)
st, r = req("POST", f"/api/conv/{c2['id']}/step", {})
check("... erneut versucht mit frischer Zusicherung: läuft", r["state"] == "done" and len(DOOR) == calls + 1, r)

# Sitzung einer anderen Person wird nicht übernommen
app._MCP[("id-anna", "gw1", "kp-i")] = "S-bert"
req("POST", f"/api/conv/{c2['id']}/send", {"text": "Projekte?"})
_, r = req("POST", f"/api/conv/{c2['id']}/step", {})
check("fremde MCP-Sitzung: die Tür kennt sie nicht, der Chat eröffnet eine eigene",
      r["state"] == "done" and app._MCP[("id-anna", "gw1", "kp-i")] == "S-anna", r)

# Schleife begrenzt
_, c3 = req("POST", "/api/conv", {"assistant": "projekte"})
st, r = req("POST", f"/api/conv/{c3['id']}/send", {"text": "Schleife"})
rounds = 1
while r.get("state") == "more" and rounds < 20:
    _, r = req("POST", f"/api/conv/{c3['id']}/step", {})
    rounds += 1
check(f"Schleife endet nach {loop.MAX_MODEL_TURNS} Modellzügen mit einem Satz",
      r["state"] == "done" and "aufgehört" in r["messages"][-1]["content"]
      and sum(1 for m in r["messages"] if m["role"] == "assistant") == loop.MAX_MODEL_TURNS + 1, rounds)

st, r = req("POST", f"/api/conv/{c3['id']}/delete", {}, person="bert")
check("bert löscht annas Gespräch nicht", st == 404)
st, r = req("POST", f"/api/conv/{c3['id']}/delete", {})
check("anna löscht ihr Gespräch", st == 200 and not os.path.exists(app.conv_path("id-anna", c3["id"])))

# ein Assistent mit einem Recht ist ungültig
import assistants  # noqa: E402
check("Assistent mit token: ungültig (D1)",
      any("keine Rechte" in e for e in assistants.check(dict(app.CONFIGURED[0], token="x"))))

# ------------------------------------------------ Stufe 3: Bestätigung am Gateway
check("§5.6: bestätigter Schreibaufruf trägt X-OAAP-Confirmed: yes, lesende nicht",
      [c for d, c in zip(DOOR, CONFIRMED) if d[0] == "add_entry"] == ["yes"]
      and all(c == "" for d, c in zip(DOOR, CONFIRMED) if d[0] == "list_projects"),
      list(zip([d[0] for d in DOOR], CONFIRMED)))

# ------------------------------------------------ Stufe 3: /Befehle
_, c4 = req("POST", "/api/conv", {"assistant": "projekte"})
st, r = req("GET", f"/api/conv/{c4['id']}/commands")
check("/Befehle: die Prompts der Sicht, mit Argumenten", st == 200 and r["commands"] == [
    {"name": "wochenbericht", "description": "Bericht für ein Projekt",
     "arguments": [{"name": "projekt", "required": True}]}], r)
st, r = req("POST", f"/api/conv/{c4['id']}/send", {"text": "/wochenbericht"})
check("/wochenbericht ohne Pflichtargument: 400 mit Satz, nichts im Gespräch",
      st == 400 and "projekt" in r["error"] and not app.load_conv("id-anna", c4["id"])["messages"], r)
st, r = req("POST", f"/api/conv/{c4['id']}/send", {"text": "/gibtsnicht"})
check("unbekannter Befehl: 400, nennt die vorhandenen", st == 400 and "/wochenbericht" in r["error"], r)
st, r = req("POST", f"/api/conv/{c4['id']}/send", {"text": "/wochenbericht pA"})
first = (r.get("messages") or [{}])[0]
check("/wochenbericht pA: der Text des Prompts wird die Nachricht, das Modell antwortet",
      st == 200 and first.get("command") == "wochenbericht"
      and first.get("content") == "Fasse die Woche in Projekt pA zusammen." and r["state"] == "done", r)
st, r = req("POST", f"/api/conv/{c4['id']}/send", {"text": "/wochenbericht projekt=pB"})
check("/wochenbericht projekt=pB: Argument mit Namen",
      [m["content"] for m in r["messages"] if m.get("command")][-1].endswith("Projekt pB zusammen."), r)


# ------------------------------------------------ Stufe 3: Assistenten und Sichtbarkeit (D4)
def asst(aid, scope, team=None, **kw):
    v = {"scope": scope}
    if team:
        v["context"] = {"team": team}
    return dict({"id": aid, "title": aid, "system": "Du hilfst.", "model": "standard",
                 "view": "kp-i", "visibility": v, "owner": "x"}, **kw)


def ids(who, **k):
    return [a["id"] for a in req("GET", "/api/state", person=who, **k)[1]["assistants"]]


st, r = req("POST", "/api/assistants", asst("annas", "private"))
check("anna legt einen privaten Assistenten an", st == 200, r)
st, r = req("GET", "/api/assistants/annas")
check("... Eigentümer ist anna, egal was sie als owner schickte", st == 200 and r["owner"] == "id-anna", r)
check("privat: nur anna sieht ihn", "annas" in ids("anna") and "annas" not in ids("bert"))
st, r = req("POST", "/api/assistants", asst("annas", "private", title="gekapert"), person="bert")
check("bert überschreibt annas privaten Assistenten nicht", st == 400
      and req("GET", "/api/assistants/annas")[1]["title"] == "annas", r)
st, r = req("POST", "/api/conv", {"assistant": "annas"}, person="bert")
check("bert beginnt kein Gespräch mit annas privatem Assistenten (404)", st == 404, r)

st, r = req("POST", "/api/assistants", asst("service", "context", "service"))
check("anna (assistant.manage in Team service) legt einen Team-Assistenten an", st == 200, r)
st, r = req("POST", "/api/assistants", asst("lager", "context", "lager"))
check("... für ein Team, in dem sie nichts verwaltet: abgelehnt", st == 400, r)
st, r = req("POST", "/api/assistants", asst("x", "context", "service"), person="bert")
check("bert (nur use) legt im Team service keinen an", st == 400, r)
check("Team service: anna und bert sehen ihn, carl nicht",
      "service" in ids("anna") and "service" in ids("bert") and "service" not in ids("carl"))
AUTHZ_DOWN["on"] = True
check("Berechtigungen nicht lesbar: niemand sieht den Team-Assistenten (geschlossen)",
      "service" not in ids("bert") and req("GET", "/api/state", person="bert")[1]["teams_known"] is False)
AUTHZ_DOWN["on"] = False
st, r = req("POST", "/api/assistants", dict(asst("x", "private"),
                                             visibility={"scope": "context", "context": {"abteilung": "a"}}))
check("anderer Kontextschlüssel als team: abgelehnt mit Satz", st == 400 and "team" in " ".join(r["errors"]), r)

st, r = req("POST", "/api/assistants", asst("alle", "tenant"))
check("Mandanten-Assistent ohne tenant_admin: abgelehnt", st == 400, r)
st, r = req("POST", "/api/assistants", asst("alle", "tenant"), roles="user,tenant_admin")
check("... als tenant_admin: angelegt, alle sehen ihn", st == 200 and "alle" in ids("carl"), r)
st, r = req("POST", "/api/assistants", asst("projekte", "tenant"), roles="user,tenant_admin")
check("den Assistenten aus der Konfiguration ändert niemand im Chat",
      st == 400 and "Konfiguration" in r["errors"][0], r)
st, r = req("POST", "/api/assistants", asst("annas2", "private", token="geheim"))
check("ein Assistent mit einem Recht wird nicht gespeichert (D1)",
      st == 400 and "keine Rechte" in " ".join(r["errors"]), r)

# Sichtbarkeit wird je Runde geprüft
_, cb = req("POST", "/api/conv", {"assistant": "service"}, person="bert")
st, r = req("POST", f"/api/conv/{cb['id']}/send", {"text": "Projekte?"}, person="bert")
check("bert spricht mit dem Team-Assistenten", st == 200 and r["state"] == "more", r)
GRANTS["id-bert"] = []
n_door = len(DOOR)
st, r = req("POST", f"/api/conv/{cb['id']}/step", {}, person="bert")
check("Recht entzogen: die nächste Runde läuft nicht (403), kein Werkzeug",
      st == 403 and "nicht (mehr)" in r["error"] and len(DOOR) == n_door, r)
GRANTS["id-bert"] = [{"object": "assistant", "activities": ["use"], "fields": {"team": ["service"]}}]
st, r = req("POST", f"/api/conv/{cb['id']}/step", {}, person="bert")
check("Recht zurück: die Runde läuft, X-OAAP-Assistant nennt den Assistenten des Gesprächs",
      r.get("state") == "done" and DOOR[-1][2] == "service", (r, DOOR[-1]))

# Export und Import
st, ex = req("GET", "/api/assistants/export")
check("Export: was anna verwaltet (privat + Team), nicht Mandant und Konfiguration",
      sorted(a["id"] for a in ex["assistants"]) == ["annas", "service"], ex)
st, r = req("POST", "/api/assistants/import", ex, person="bert")
check("bert importiert annas Datei: abgelehnt, nichts geändert (alles oder nichts)",
      st == 400 and req("GET", "/api/assistants/annas")[1]["owner"] == "id-anna", r)
mixed = {"v": 1, "assistants": [asst("ok1", "private"), asst("lager2", "context", "lager")]}
st, r = req("POST", "/api/assistants/import", mixed)
check("Import mit einem erlaubten und einem verbotenen: abgelehnt, auch der erlaubte nicht gespeichert",
      st == 400 and "ok1" not in ids("anna"), r)
doc = {"v": 1, "assistants": [asst("neu1", "private"), asst("neu2", "context", "service")]}
st, r = req("POST", "/api/assistants/import", doc)
check("anna importiert zwei: beide da", st == 200 and {"neu1", "neu2"} <= set(ids("anna")), r)
st, r = req("POST", "/api/assistants/neu2/delete", {}, person="bert")
check("bert löscht den Team-Assistenten nicht", st == 400 and "neu2" in ids("anna"), r)
st, r = req("POST", "/api/assistants/neu2/delete", {})
check("anna löscht ihn", st == 200 and "neu2" not in ids("anna"), r)
_, cg = req("POST", "/api/conv", {"assistant": "neu1"})
req("POST", "/api/assistants/neu1/delete", {})
st, r = req("POST", f"/api/conv/{cg['id']}/send", {"text": "hallo"})
check("Gespräch mit gelöschtem Assistenten: keine Runde (403)", st == 403, r)
with open(os.path.join(DATA, "assistants.json"), encoding="utf-8") as f:
    check("assistants.json ist eine Austauschdatei von oaap.ai.assistants",
          assistants.check_document(json.load(f)) == [])

# ------------------------------------------------ 0.3.0: Dateien, Umbenennen, Einrichtung
def raw_get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=10) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "", b""


st, ct, body = raw_get("/static/markdown.js")
check("0.3.0: /static/markdown.js wird ausgeliefert (JavaScript)", st == 200 and "javascript" in ct
      and b"ChatMarkdown" in body, (st, ct))
st, ct, body = raw_get("/static/mermaid.min.js")
check("... Mermaid ebenso (mitgeliefert, nichts von außen)", st == 200 and len(body) > 1_000_000, st)
check("... nur die genannten Dateien: ../app.py, %2e%2e und andere Namen nie ausgeliefert",
      all(raw_get(x)[0] != 200 for x in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/MERMAID-LICENSE",
                                         "/static/", "/static/app.py")))
_, cr = req("POST", "/api/conv", {"assistant": "projekte"})
st, r = req("POST", f"/api/conv/{cr['id']}/rename", {"title": "  Drucker   im Büro  "})
check("umbenennen: Titel gesetzt, Leerraum zusammengezogen", st == 200 and r["title"] == "Drucker im Büro", r)
st, r = req("POST", f"/api/conv/{cr['id']}/rename", {"title": "   "})
check("leerer Titel: 400", st == 400, r)
st, r = req("POST", f"/api/conv/{cr['id']}/rename", {"title": "fremd"}, person="bert")
check("bert benennt annas Gespräch nicht um (404)", st == 404, r)

items = lambda r: {i["key"]: i for i in r.get("items", [])}
st, r = req("GET", "/api/setup?assistant=projekte")
it = items(r)
check("Einrichtung projekte: alles da -- Client, Gateway, Sicht mit 3 Werkzeugen, Destination, Modellname",
      st == 200 and all(i["ok"] for i in r["items"]) and set(it) == {"client", "gateway", "view", "destination", "model"}
      and "3 Werkzeug" in it["view"]["title"], r)
req("POST", "/api/assistants", asst("ohneprofil", "private", view="ohne-profil"))
st, r = req("GET", "/api/setup?assistant=ohneprofil")
it = items(r)
check("Sicht ohne Zugangsprofil: nicht ok, nächster Schritt nennt Profil, vermutete Gruppe @vuebung-chat und Zugang platform",
      not it["view"]["ok"] and "Zugangsprofil" in it["view"]["next"] and "@vuebung-chat" in it["view"]["next"]
      and "Plattform" in it["view"]["next"], it.get("view"))
req("POST", "/api/assistants", asst("falschesmodell", "private", model="gross"))
st, r = req("GET", "/api/setup?assistant=falschesmodell")
it = items(r)
check("Modellname nicht freigegeben: nicht ok, nennt die erreichbaren (standard, klein)",
      not it["model"]["ok"] and "standard, klein" in it["model"]["detail"] and "gross" in it["next"] if False else
      (not it["model"]["ok"] and "standard, klein" in it["model"]["detail"] and "gross" in it["model"]["next"]), it.get("model"))
saved_ki = app.KI_URL
app.KI_URL = ""
st, r = req("GET", "/api/setup?assistant=projekte")
it = items(r)
app.KI_URL = saved_ki
check("ohne Destination: nicht ok, nennt Portal (zuordnen) und Betreiber (anlegen); das Modell wird dann nicht mehr gefragt",
      not it["destination"]["ok"] and "Portal" in it["destination"]["next"]
      and "Betreiber" in it["destination"]["next"] and "model" not in it, r)
st, r = req("GET", "/api/setup?assistant=projekte", assertion=False)
it = items(r)
check("ohne Zusicherung: die Sicht wird nicht als ok gemeldet", not it["view"]["ok"], it.get("view"))
st, r = req("GET", "/api/setup?assistant=gibtsnicht")
check("unbekannter Assistent: 404", st == 404, r)
st, r = req("GET", "/api/setup?assistant=ohneprofil", person="bert")
check("fremder privater Assistent: 404", st == 404, r)
req("POST", "/api/assistants", asst("zurot", "private", view="zu-rot"))
st, r = req("GET", "/api/setup?assistant=zurot")
it = items(r)
check("0.3.1: der Grund des Gateways (Ampel) steht im Bericht, der Schritt nennt die Ampel, nicht ein neues Profil",
      not it["view"]["ok"] and "Ampel der Sicht zu-rot ist red" in it["view"]["detail"]
      and "Ampel" in it["view"]["next"] and "anlegen" not in it["view"]["next"], it.get("view"))
check("vermutete Instanz aus der automatischen Adresse", app.own_instance_guess() == "vuebung-chat",
      app.own_instance_guess())

# ------------------------------------------------ 0.4.0: Ansichten (MCP Apps, RFC-0075)
req("POST", "/api/assistants", asst("mitansicht", "private", view="vg", views=True))
req("POST", "/api/assistants", asst("ohneansicht", "private", view="vg"))
st, sbx = 0, ""
try:
    with urllib.request.urlopen(BASE + "/ui-sandbox", timeout=10) as r_:
        st, sbx = r_.status, r_.read().decode()
except urllib.error.HTTPError as e:
    st = e.code
check("0.4.0: /ui-sandbox wird ohne Person ausgeliefert, mit den eigenen Adressen als erlaubten Eltern",
      st == 200 and '["https://chat.example.org", "https://chat.vuebung.node.example"]' in sbx
      and "__HOSTS__" not in sbx and "allow-same-origin" not in sbx.split("inner.setAttribute(\"sandbox\"")[1][:60], st)
_, cv = req("POST", "/api/conv", {"assistant": "mitansicht"})
n_model = len(MODEL)
st, r = req("POST", f"/api/conv/{cv['id']}/send", {"text": "Karte?"})
names = [t["function"]["name"] for t in MODEL[n_model]["tools"]]
check("0.4.0: das Modell sieht Tools nur mit visibility model -- nur_app fehlt, nur_modell ist da",
      "karte" in names and "nur_modell" in names and "nur_app" not in names, names)
check("... der Chat sagt die Erweiterung io.modelcontextprotocol/ui beim Gateway an",
      VG_INIT and VG_INIT[-1] == {"extensions": {"io.modelcontextprotocol/ui": {"mimeTypes": ["text/html;profile=mcp-app"]}}},
      VG_INIT[-1:])
st, r = req("POST", f"/api/conv/{cv['id']}/step", {})
tm = [m for m in r["messages"] if m["role"] == "tool"][-1]
check("0.4.0: das Ergebnis eines Tools mit Ansicht trägt die Ansicht und die Aufrufkennung",
      tm.get("view") == "ui://karte/v1" and tm.get("tool_call_id"), tm)
st, v = req("GET", f"/api/conv/{cv['id']}/view?call={tm['tool_call_id']}")
check("0.4.0: die Tür /view liefert HTML, Argumente und das CallToolResult",
      st == 200 and v["html"] == VG_HTML and v["arguments"] == {"nr": "V-1"}
      and v["result"]["structuredContent"] == {"nr": "V-1"}, v)
check("... und gibt keine fremden Domains weiter, egal was die Ansicht erklärt (Stufe 2)",
      v["csp"] == {} and "evil" not in json.dumps(v), v.get("csp"))
st, v = req("GET", f"/api/conv/{cv['id']}/view?call={tm['tool_call_id']}", assertion=False)
check("... ohne Zusicherung keine Ansicht (401)", st == 401, st)
st, v = req("GET", f"/api/conv/{cv['id']}/view?call={tm['tool_call_id']}", person="bert")
check("... bert bekommt annas Ansicht nicht (404)", st == 404, st)
_, co = req("POST", "/api/conv", {"assistant": "ohneansicht"})
st, v = req("POST", f"/api/conv/{co['id']}/view-call", {"name": "karte", "arguments": {}})
check("0.4.0: ein Assistent ohne views: keine Aufrufe aus Ansichten (403)", st == 403, v)

n = len(VG_CALLS)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"name": "nur_app", "arguments": {"x": 1}})
check("0.4.0: die Ansicht ruft ein Tool nur für Ansichten -- als die Person, ohne Zustimmung (lesend)",
      st == 200 and VG_CALLS[-1][0] == "nur_app" and VG_CALLS[-1][2] == "" and VG_CALLS[-1][3].startswith("ZS-anna"), v)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"name": "nur_modell", "arguments": {}})
check("... ein Tool nur für das Modell darf die Ansicht nicht rufen (403)", st == 403 and VG_CALLS[-1][0] != "nur_modell", v)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"name": "gibtsnicht", "arguments": {}})
check("... ein Tool, das die Sicht nicht hat: 403", st == 403, v)
n = len(VG_CALLS)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"name": "schreiben", "arguments": {"t": "neu"}})
check("0.4.0: ein schreibendes Tool aus der Ansicht ohne Zustimmung: 409 mit Karte, nichts gerufen (D4)",
      st == 409 and v["confirm"]["name"] == "schreiben" and v["confirm"]["effect"] == "write" and len(VG_CALLS) == n, v)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"name": "schreiben", "arguments": {"t": "neu"}, "confirmed": True})
check("... mit Zustimmung: genau ein Aufruf, X-OAAP-Confirmed: yes",
      st == 200 and len(VG_CALLS) == n + 1 and VG_CALLS[-1][2] == "yes", v)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"method": "resources/read", "uri": "ui://karte/v1"})
check("0.4.0: resources/read aus der Ansicht -- durch die Tür, als die Person", st == 200
      and v["result"]["contents"][0]["text"] == VG_HTML, v)
st, v = req("POST", f"/api/conv/{cv['id']}/view-call", {"method": "resources/read", "uri": "ui://anderes"})
check("... was das Gateway nicht freigibt, kommt nicht (403)", st == 403, v)

srv.shutdown()
fake.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print(f"\n{sum(RESULTS)} passed, {len(RESULTS) - sum(RESULTS)} failed")
sys.exit(0 if all(RESULTS) else 1)
