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
            if m == "tools/call":
                p = body["params"]
                DOOR.append((p["name"], a, self.headers.get("X-OAAP-Assistant"), p["arguments"]))
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
                call = {"Projekte?": ("list_projects", "{}"),
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


fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
FP = fake.server_address[1]
DATA = tempfile.mkdtemp(prefix="chat-test-")
os.environ.update({
    "CHAT_DATA_DIR": DATA, "OAAP_MCP_CLIENT_KEY": CK, "OAAP_MCP_CLIENT_URL": f"http://127.0.0.1:{FP}/gw",
    "OAAP_DESTINATION_KI_URL": f"http://127.0.0.1:{FP}/v1/",
    "CHAT_ASSISTANT": json.dumps({"id": "projekte", "title": "Projekte", "view": "kp-i",
                                  "system": "Du hilfst bei Projekten."}),
})
import app  # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
N = {"n": 0}


def req(method, path, body=None, person="anna", assertion=True):
    h = {"Content-Type": "application/json"}
    if person:
        h["X-OAAP-User-Id"] = "id-" + person
        h["X-OAAP-User"] = person
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


check("Assistent aus CHAT_ASSISTANT gültig", app.ASSISTANT_ERRORS == [], app.ASSISTANT_ERRORS)
st, _ = req("GET", "/api/state", person=None)
check("ohne Person: 401", st == 401)
st, s = req("GET", "/api/state")
check("Zustand: Assistent, Client, Modell, keine Gespräche",
      st == 200 and s["assistant"]["id"] == "projekte" and s["client"] and s["model"]
      and s["conversations"] == [], s)
_, c = req("POST", "/api/conv", {})
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
_, c2 = req("POST", "/api/conv", {})
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
_, c3 = req("POST", "/api/conv", {})
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
      any("keine Rechte" in e for e in assistants.check(dict(app.ASSISTANT, token="x"))))
saved = app.ASSISTANT_ERRORS[:]
app.ASSISTANT_ERRORS[:] = ["kaputt"]
st, r = req("POST", f"/api/conv/{c2['id']}/send", {"text": "Projekte?"})
check("ungültiger Assistent: keine Runde (400)", st == 400, r)
app.ASSISTANT_ERRORS[:] = saved

srv.shutdown()
fake.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print(f"\n{sum(RESULTS)} passed, {len(RESULTS) - sum(RESULTS)} failed")
sys.exit(0 if all(RESULTS) else 1)
