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


SEEN_VIEWS = {"assertion": ""}
MODEL_LIGHT, DOOR_LIGHT = [], []   # X-OAAP-Light am Modell und an der Tür (0.8.0)
# 0.9.0 (RFC-0097): ein Gateway mit /_assistants -- aus, bis der Test es einschaltet
GWA = {"on": False, "down": False, "items": {}, "posts": [], "assistant_hdr": [], "gets": []}


def gwa_list():
    return [dict(d, rev=r, state=st, manage=True) for d, r, st in GWA["items"].values() if st != "deleted"]


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
            return self.reply(200, {"object": "list", "data": [{"id": "standard", "oaap_light": "yellow"},
                                                               {"id": "klein", "oaap_light": "green"}]})
        if self.path == "/v1/usage":
            return self.reply(200, {"ceiling": "yellow"})
        if self.path == "/via/gw1/_views":
            SEEN_VIEWS["assertion"] = self.headers.get("X-OAAP-Act-Assertion", "")
            if self.headers.get("Authorization") != "Bearer " + CK or not SEEN_VIEWS["assertion"].startswith("ZS-"):
                return self.reply(401, {})
            return self.reply(200, {"v": 1, "client": "vuebung-chat", "all": False, "views": [
                {"name": "vg", "ok": True, "reason": "", "light": "green", "max_effect": "read",
                 "tools": 3, "prompts": 1, "resources": 0}]})
        if self.path.startswith("/via/gw1/_assistants"):
            if not GWA["on"]:
                return self.reply(405, {})
            if GWA["down"]:
                return self.reply(503, {})
            if self.headers.get("Authorization") != "Bearer " + CK or \
                    not self.headers.get("X-OAAP-Act-Assertion", "").startswith("ZS-"):
                return self.reply(401, {})
            GWA["gets"].append(self.path[len("/via/gw1/_assistants"):])
            if "/evaluation" in self.path:
                return self.reply(200, {"id": "alt-privat", "rev": 2, "usage_since": "2026-11-01T00:00:00Z",
                                        "revisions": [{"rev": 2, "calls": 3, "refused": 1, "errors": 0, "persons": 1,
                                                       "cases": 1, "compacted": False, "changed_at": "2026-11-19T10:00:00Z",
                                                       "ratings": {"count": 2, "stars": [0, 1, 0, 0, 1, 0], "low": 0.5, "persons": 1}},
                                                      {"rev": 1, "calls": 0, "refused": 0, "errors": 0, "persons": 0,
                                                       "cases": 0, "compacted": False, "changed_at": "2026-11-18T10:00:00Z",
                                                       "ratings": {"count": 0, "stars": [0] * 6, "low": 0, "persons": 0}}]})
            if "/diff?" in self.path:
                return self.reply(200, {"diff": ["--- Revision 1", "+++ Revision 2", "-Du hilfst.", "+Neu."]})
            return self.reply(200, {"v": 1, "assistants": gwa_list(), "grants_known": True})
        if self.path.startswith("/via/gw1/_readiness?view="):
            # 0.7.0 (RFC-0095 §5): das Gateway sagt je Sicht, was der Person noch fehlt.
            if self.headers.get("Authorization") != "Bearer " + CK or                     not self.headers.get("X-OAAP-Act-Assertion", "").startswith("ZS-"):
                return self.reply(401, {})
            if self.path.endswith("=vg"):
                return self.reply(200, {"v": 1, "view": "vg", "me": "https://tools.example/me",
                                        "ready": False, "missing_tools": 2, "state": "incomplete",
                                        "servers": [{"server": "jira", "state": "needs_credential",
                                                     "tools": 2, "entries": 2,
                                                     "hint": "Jira → Profil → Token", "link": ""}]})
            return self.reply(200, {"v": 1, "view": "x", "me": "", "ready": False, "missing_tools": 0,
                                    "state": "not_for_you", "servers": [], "reason": "kein Zugangsprofil"})
        if self.path == "/gw" and self.headers.get("Authorization") == "Bearer " + CK:
            port = self.server.server_address[1]
            return self.reply(200, {"v": 1, "gateways": [
                {"name": "gw1", "via": f"http://127.0.0.1:{port}/via/gw1"}]})
        self.reply(401, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.startswith("/via/gw1/_assistants"):
            if not GWA["on"] or GWA["down"]:
                return self.reply(503 if GWA["down"] else 405, {})
            rest = self.path[len("/via/gw1/_assistants"):]
            GWA["posts"].append((rest, body))
            if rest == "":
                if any(k in body for k in ("rev", "state", "manage")):
                    return self.reply(400, {"errors": ["rev: führt das Gateway"]})
                old = GWA["items"].get(body["id"])
                rev = (old[1] if old else 0) + 1
                GWA["items"][body["id"]] = (dict(body, owner=old[0]["owner"] if old else "id-anna"), rev, "active")
                return self.reply(200, {"saved": body["id"], "rev": rev})
            if rest == "/_migrate":
                for a in body["assistants"]:
                    GWA["items"][a["id"]] = (a, 1, "active")
                return self.reply(200, {"migrated": len(body["assistants"])})
            if rest.endswith("/cases"):
                return self.reply(200, {"counted": True, "rev": body.get("rev")})
            if rest.endswith("/ratings"):
                if GWA.get("rate_refuse"):
                    return self.reply(404, {"error": "Diesen Assistenten gibt es für dich nicht."})
                return self.reply(200, {"rated": body.get("stars"), "rev": body.get("rev"),
                                        "scope": body.get("scope"), "text": "text" in body})
            if rest.endswith("/state"):
                aid = rest.split("/")[1]
                d, r, _ = GWA["items"][aid]
                GWA["items"][aid] = (d, r, body["state"])
                return self.reply(200, {"id": aid, "state": body["state"]})
            return self.reply(404, {})
        if self.path.startswith("/via/gw1/"):
            DOOR_LIGHT.append(self.headers.get("X-OAAP-Light", ""))
            GWA["assistant_hdr"].append(self.headers.get("X-OAAP-Assistant", ""))
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
            MODEL_LIGHT.append(self.headers.get("X-OAAP-Light", ""))
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
app._MCP[("id-anna", "gw1", "kp-i", False, "")] = "S-bert"
req("POST", f"/api/conv/{c2['id']}/send", {"text": "Projekte?"})
_, r = req("POST", f"/api/conv/{c2['id']}/step", {})
check("fremde MCP-Sitzung: die Tür kennt sie nicht, der Chat eröffnet eine eigene",
      r["state"] == "done" and app._MCP[("id-anna", "gw1", "kp-i", False, "")] == "S-anna", r)

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


st, ct, body = raw_get("/static/md/markdown.js")
check("0.3.0/0.13.0: /static/md/markdown.js wird ausgeliefert (JavaScript)", st == 200 and "javascript" in ct
      and b"ChatMarkdown" in body, (st, ct))
st, ct, body = raw_get("/static/md/view.js")
check("0.13.0: /static/md/view.js (formatiert/roh, Mermaid, SVG als Bild) wird ausgeliefert",
      st == 200 and b"OAAPView" in body and b"data:image/svg+xml" in body, st)
st, ct, body = raw_get("/static/md/mermaid.min.js")
check("... Mermaid ebenso (mitgeliefert, nichts von außen)", st == 200 and len(body) > 1_000_000, st)
check("... nur die genannten Dateien: ../app.py, %2e%2e und andere Namen nie ausgeliefert",
      all(raw_get(x)[0] != 200 for x in ("/static/../app.py", "/static/%2e%2e/app.py", "/static/MERMAID-LICENSE", "/static/md/MERMAID-LICENSE", "/static/markdown.js",
                                         "/static/", "/static/app.py")))
class _H:
    headers = {"X-OAAP-Display-Name": "J%C3%B6rg%20M%C3%BCller"}
check("0.13.0: der Anzeigename wird dekodiert (RFC-0040 D4), nicht „J%C3%B6rg…“ im Prompt",
      app.Handler.person_name(_H()) == "Jörg Müller", app.Handler.person_name(_H()))
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

# ------------------------------------------------ 0.18.0 (RFC-0116 D9): ein Assistent ohne Sicht
import assistants as _ar  # noqa: E402
_nov = {k: v for k, v in asst("schreibhilfe", "private").items() if k != "view"}
check("0.18.0: oaap.ai.assistants 0.7 -- ohne view gültig", _ar.check(_nov) == [], _ar.check(_nov))
check("0.18.0: KÖDER: gateway ohne view wird mit Satz abgelehnt",
      any("gateway ohne view" in e for e in _ar.check(dict(_nov, gateway="gw1"))), _ar.check(dict(_nov, gateway="gw1")))
check("0.18.0: KÖDER: views ohne view wird abgelehnt", any("views ohne view" in e for e in _ar.check(dict(_nov, views=True))))
st, r = req("POST", "/api/assistants", _nov)
check("0.18.0: der Chat speichert einen Assistenten ohne Sicht", st == 200 and not r.get("errors"), r)
st, r = req("POST", "/api/assistants", dict(_nov, id="mitgw", gateway="gw1"))
check("0.18.0: ... mit gateway, aber ohne view: abgelehnt", st != 200 or r.get("errors"), r)
st, r = req("GET", "/api/setup?assistant=schreibhilfe")
it = items(r)
check("0.18.0: Einrichtung ohne Sicht: kein Client, kein Gateway gefragt -- „ohne Werkzeuge“, dann das Modell",
      st == 200 and all(i["ok"] for i in r["items"]) and set(it) == {"view", "destination", "model"}
      and "ohne Werkzeuge" in it["view"]["title"], r)
_, cn = req("POST", "/api/conv", {"assistant": "schreibhilfe"})
_door, _models = len(DOOR), len(MODEL)
st, r = req("POST", f"/api/conv/{cn['id']}/send", {"text": "Hallo"})
check("0.18.0: ohne Sicht antwortet das Modell, ohne Werkzeuge im Rumpf",
      st == 200 and r["state"] == "done" and len(MODEL) == _models + 1 and "tools" not in MODEL[-1], (r, MODEL[-1:]))
st, r = req("POST", f"/api/conv/{cn['id']}/send", {"text": "Erfunden"})
st, r = req("POST", f"/api/conv/{cn['id']}/step", {})
check("0.18.0: KÖDER: ein erfundener Werkzeugaufruf erreicht keine Tür, der Assistent erfährt warum",
      len(DOOR) == _door and any(m.get("role") == "tool" and "gibt es in dieser Sicht nicht" in m.get("content", "")
                                 for m in r.get("messages", [])), r)
st, r = req("GET", "/api/light?assistant=schreibhilfe")
check("0.18.0: /api/light ohne Sicht: nur das Modell zählt (gelb), kein Fehlersatz",
      st == 200 and r["needs"] == "yellow" and r["view"]["light"] == "" and not r["said"], r)
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

# ------------------------------------------------ 0.6.0: Modellnamen als Auswahl (L27), W4
st, r = req("GET", "/api/models")
check("0.6.0: /api/models nennt die Modellnamen, die der Schlüssel der Destination ki erreicht",
      st == 200 and [m["id"] for m in r["models"]] == ["standard", "klein"] and r["error"] == "", r)
st, r = req("GET", "/api/models", person="", assertion=False)
check("0.6.0: /api/models nur für eine angemeldete Person", st == 401, (st, r))
_ki, app.KI_URL = app.KI_URL, ""
try:
    st, r = req("GET", "/api/models")
finally:
    app.KI_URL = _ki
check("0.6.0: ohne Destination ki keine Liste, aber ein Satz", st == 200 and r["models"] == []
      and "Destination ki" in r["error"], r)
st, _ct, html = raw_get("/")
html = html.decode("utf-8")
check("0.6.0: das Formular wählt das Modell aus einer Liste, kein freies Feld",
      '<select name="model" required>' in html and 'input name="model"' not in html, st)
check("0.6.0 W4: Werkzeugaufrufe lassen sich in den Einstellungen ausblenden",
      'data-k="calls"' in html and "body[data-calls=hide] .call" in html, st)

# ------------------------------------------------ 0.6.1: Suchhilfe für Sichten
st, r = req("GET", "/api/views")
check("0.6.1: /api/views fragt das Gateway über die via-Tür, mit Schlüssel und Zusicherung",
      st == 200 and r["gateway"] == "gw1" and [v["name"] for v in r["views"]] == ["vg"]
      and r["error"] == "" and SEEN_VIEWS["assertion"].startswith("ZS-anna"), (st, r))
st, r = req("GET", "/api/views", assertion=False)
check("0.6.1: ohne Zusicherung keine Liste, aber ein Satz", st == 200 and r["views"] == []
      and "Zusicherung" in r["error"], r)
st, r = req("GET", "/api/views?gateway=gibtsnicht")
check("0.6.1: unbekanntes Gateway: Satz mit der Liste des Mandanten", st == 200 and r["views"] == []
      and "gw1" in r["error"], r)
st, _ct, html = raw_get("/")
check("0.6.1: das Feld Sicht hat die Suchhilfe", 'list="viewlist"' in html.decode("utf-8"))

# ------------------------------------------------ 0.7.0: Bereitschaft je Assistent (RFC-0095 §5)
req("POST", "/api/assistants", asst("bereit", "private", view="vg"))
st, r = req("GET", "/api/readiness")
mark = (r.get("assistants") or {}).get("bereit", {})
check("0.7.0: /api/readiness nennt je Assistent die Werkzeuge, die dir noch fehlen, mit Link auf /me",
      st == 200 and mark.get("missing_tools") == 2 and mark.get("me") == "https://tools.example/me", (st, r))
check("0.7.0: eine Sicht, die dir nicht offensteht, bekommt keine Markierung (das sagt „Einrichtung prüfen“)",
      all(v.get("missing_tools") or v.get("ready") for v in r["assistants"].values()), r)
st, r = req("GET", "/api/setup?assistant=bereit")
it = [i for i in r.get("items", []) if i["key"] == "personal:jira"]
check("0.7.0: „Einrichtung prüfen“ nennt den Server, die Zahl, den Hinweis und wo man den Zugang hinterlegt",
      it and not it[0]["ok"] and "2 Werkzeug(e) von jira" in it[0]["title"]
      and "Jira → Profil → Token" in it[0]["detail"] and "https://tools.example/me" in it[0]["next"], r)
st, r = req("GET", "/api/readiness", assertion=False)
check("0.7.0: ohne Zusicherung keine Markierungen (kein Fehler)", st == 200 and r["assistants"] == {}, r)
st, _ct, html = raw_get("/")
check("0.7.0: die Karte zeigt „noch nicht eingerichtet – einrichten“",
      "noch nicht eingerichtet – " in html.decode("utf-8") and "loadReadiness" in html.decode("utf-8"))
req("POST", "/api/assistants/bereit/delete", {})

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

# ------------------------------------------------ 0.8.0: Ampel des Gesprächs (RFC-0101 Stufe 1)
st, s = req("GET", "/api/state")
check("0.8.0: der Zustand nennt die Vorgabe grün", s.get("default_light") == "green", s.get("default_light"))
_, c8 = req("POST", "/api/conv", {"assistant": "projekte"})
check("0.8.0: ein neues Gespräch steht auf grün", c8.get("light") == "green", c8)
MODEL_LIGHT.clear(); DOOR_LIGHT.clear()
req("POST", f"/api/conv/{c8['id']}/send", {"text": "Projekte?"})
req("POST", f"/api/conv/{c8['id']}/step", {})
check("0.8.0: der Modellzug trägt X-OAAP-Light: green", MODEL_LIGHT and set(MODEL_LIGHT) == {"green"}, MODEL_LIGHT)
check("0.8.0: jeder Aufruf an der Tür des MCP-Gateways trägt sie auch",
      DOOR_LIGHT and set(DOOR_LIGHT) == {"green"}, DOOR_LIGHT)
st, r = req("POST", f"/api/conv/{c8['id']}/light", {"light": "yellow"})
check("0.8.0: zu gelb ohne Bestätigung: 409 mit dem Satz", st == 409
      and r.get("confirm") == "Ich gebe in diesem Gespräch nichts Sensibles ein.", (st, r))
st, r = req("POST", f"/api/conv/{c8['id']}/light", {"light": "yellow", "sure": True})
check("0.8.0: mit Bestätigung gilt gelb", st == 200 and r["light"] == "yellow", (st, r))
MODEL_LIGHT.clear(); DOOR_LIGHT.clear()
req("POST", f"/api/conv/{c8['id']}/send", {"text": "Hallo"})
check("0.8.0: danach trägt der Modellzug gelb", MODEL_LIGHT == ["yellow"], MODEL_LIGHT)
st, r = req("POST", f"/api/conv/{c8['id']}/light", {"light": "green"})
check("0.8.0: zurück zu grün braucht nichts", st == 200 and r["light"] == "green", (st, r))
with open(app.conv_path("id-anna", c8["id"]), encoding="utf-8") as f:
    hist = json.load(f).get("lights") or []
check("0.8.0: der eigene Verlauf hält die Wechsel fest (grün, gelb, grün)",
      [x["light"] for x in hist] == ["green", "yellow", "green"], hist)
st, r = req("POST", f"/api/conv/{c8['id']}/light", {"light": "blau", "sure": True})
check("0.8.0: eine unbekannte Farbe: 400", st == 400, (st, r))
st, r = req("POST", f"/api/conv/{c8['id']}/light", {"light": "red", "sure": True}, person="bert")
check("0.8.0: KÖDER: eine andere Person ändert die Ampel nicht (404)", st == 404, (st, r))
st, r = req("POST", "/api/conv", {"assistant": "projekte", "light": "red"})
check("0.8.0: ein neues Gespräch gleich rot nur mit Bestätigung (409)", st == 409
      and "nur Öffentliches" in r.get("confirm", ""), (st, r))
st, r = req("POST", "/api/conv", {"assistant": "projekte", "light": "red", "sure": True})
check("0.8.0: mit Bestätigung rot", st == 200 and r["light"] == "red", (st, r))
req("POST", "/api/assistants", asst("ampel", "private", view="vg"))
st, r = req("GET", "/api/light?assistant=ampel")
check("0.8.0: /api/light: Modell gelb (KI-Gateway), Sicht grün (MCP-Gateway), braucht gelb, Decke gelb",
      st == 200 and r["model"] == {"id": "standard", "light": "yellow"}
      and r["view"] == {"name": "vg", "light": "green"} and r["needs"] == "yellow"
      and r["ceiling"] == "yellow" and r["light"] == "green" and r["ok"] is False, (st, r))
_, ca = req("POST", "/api/conv", {"assistant": "ampel", "light": "yellow", "sure": True})
st, r = req("GET", f"/api/light?conv={ca['id']}")
check("0.8.0: ein gelbes Gespräch mit diesem Assistenten: ok", st == 200 and r["ok"] is True
      and r["light"] == "yellow", (st, r))
st, r = req("GET", f"/api/light?conv={ca['id']}", person="bert")
check("0.8.0: KÖDER: das Gespräch einer anderen Person: 404", st == 404, (st, r))
req("POST", "/api/assistants/ampel/delete", {})
st, _ct, html = raw_get("/")
html = html.decode("utf-8")
check("0.8.0: die Seite hat die Ampel, den Hinweis ohne grünen Weg und die beiden Anfragen",
      'id="lightbtn"' in html and "keinen sicheren Weg" in html and "Grüne Option erbitten" in html
      and "Vorab fragen, ob das erlaubt ist" in html, st)
check("0.8.0: die Anfragen gehen über die bezeugte Route, nicht über ein Backend",
      'fetch("/platform/feedback"' in html and "cases/submit" not in html)
check("0.8.0: Senden ist gesperrt, solange kein Weg passt", "if (!lightOk()) { render(); return; }" in html)

# ------------------------------------------------ 0.9.0: Assistenten im MCP-Gateway (RFC-0097 Stufe 1)
req("POST", "/api/assistants", asst("alt-privat", "private"))
req("POST", "/api/assistants", asst("alt-alle", "tenant"), roles="user,tenant_admin")
local_n = len(app.STORE._read())
st, s9 = req("GET", "/api/state", roles="user,tenant_admin")
check("0.9.0: Gateway ohne /_assistants (vor 0.22): alles bleibt im Chat, nichts zu übernehmen",
      s9["home"] == "chat" and s9["migration"] == 0 and local_n >= 2, (s9["home"], s9["migration"], local_n))
GWA["on"] = True
st, s9 = req("GET", "/api/state", roles="user,tenant_admin")
check("0.9.0: das Gateway kennt Assistenten, die Datei hat noch welche: der tenant_admin sieht „übernehmen“",
      s9["home"] == "chat" and s9["migration"] == local_n, (s9["home"], s9["migration"]))
st, s9u = req("GET", "/api/state")
check("0.9.0: ... eine Person ohne tenant_admin sieht den Knopf nicht, die Assistenten aber wie bisher",
      s9u["migration"] == 0 and "alt-privat" in [a["id"] for a in s9u["assistants"]], s9u["migration"])
st, r = req("POST", "/api/assistants/migrate", {})
check("0.9.0: übernehmen nur als tenant_admin (403)", st == 403, (st, r))
st, r = req("POST", "/api/assistants/migrate", {}, roles="user,tenant_admin")
moved = [f for f in os.listdir(DATA) if f.startswith("assistants.json.migrated-")]
check("0.9.0: übernommen; die Datei ist umbenannt, nicht gelöscht", st == 200 and r["migrated"] == local_n
      and moved and not os.path.exists(os.path.join(DATA, "assistants.json")), (st, r, moved))
sent = [b for rest, b in GWA["posts"] if rest == "/_migrate"][-1]
check("0.9.0: ans Gateway gingen die Assistenten mit ihren Besitzern",
      {a["id"]: a["owner"] for a in sent["assistants"]}.get("alt-privat") == "id-anna", sent)
st, s9 = req("GET", "/api/state")
ga = {a["id"]: a for a in s9["assistants"]}
check("0.9.0: jetzt liegen sie im Gateway: Herkunft gateway, Fassung 1; der aus der Konfiguration bleibt",
      s9["home"] == "gateway" and ga["alt-privat"]["origin"] == "gateway" and ga["alt-privat"]["rev"] == 1
      and ga["projekte"]["origin"] == "config", s9)
st, r = req("POST", "/api/assistants", dict(asst("alt-privat", "private", system="Neu."), rev=1, manage=True))
last = GWA["posts"][-1][1]
check("0.9.0: speichern geht ans Gateway, ohne die Felder, die das Gateway führt -- Fassung 2",
      st == 200 and r["rev"] == 2 and "rev" not in last and "manage" not in last, (st, r, last))
st, r = req("POST", "/api/assistants", asst("projekte", "tenant"), roles="user,tenant_admin")
check("0.9.0: den aus der Konfiguration ändert niemand im Chat", st == 400
      and "Konfiguration" in json.dumps(r, ensure_ascii=False), (st, r))
_, c9 = req("POST", "/api/conv", {"assistant": "alt-privat", "light": "yellow", "sure": True})
GWA["assistant_hdr"].clear()
req("POST", f"/api/conv/{c9['id']}/send", {"text": "Projekte?"})
req("POST", f"/api/conv/{c9['id']}/step", {})
check("0.9.0: an der Tür steht alt-privat@2 -- die Revision, die spricht",
      GWA["assistant_hdr"] and set(GWA["assistant_hdr"]) == {"alt-privat@2"}, GWA["assistant_hdr"])
with open(app.conv_path("id-anna", c9["id"]), encoding="utf-8") as f:
    replies = [m for m in json.load(f)["messages"] if m["role"] == "assistant"]
check("0.9.0: die Antwort im Verlauf trägt die Revision (für spätere Bewertungen)",
      replies and all(m.get("rev") == 2 for m in replies), replies)

# ------------------------------------------------ 0.10.0: Sterne (RFC-0101 Stufe 2)
st, v10 = req("GET", f"/api/conv/{c9['id']}")
ix = [i for i, m in enumerate(v10["messages"]) if m.get("rate")]
check("0.10.0: die Schlussantwort ist bewertbar (rate), das Gespräch auch",
      st == 200 and len(ix) == 1 and v10["rateable"] is True, v10)
check("0.10.0: die Antwort hält Modell und Ampel von damals fest",
      replies[-1].get("model") and replies[-1].get("light") == "yellow", replies[-1])
GWA["posts"].clear()
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": ix[0], "stars": 4})
sent = [b for rest, b in GWA["posts"] if rest == "/alt-privat/ratings"]
check("0.10.0: 4 Sterne gehen ans Gateway: assistant@rev, Antwort, Runde 1, Umstände",
      st == 200 and sent and sent[-1]["rev"] == 2 and sent[-1]["scope"] == "answer"
      and sent[-1]["round"] == 1 and sent[-1]["stars"] == 4 and sent[-1]["conversation"] == c9["id"]
      and sent[-1]["light"] == "yellow" and sent[-1]["view"], (st, r, sent))
check("0.10.0: KÖDER: ohne Haken kein Text; Werkzeuge nur Name, Wirkung, Ausgang",
      sent and "text" not in sent[-1] and all(set(t) == {"name", "effect", "outcome"} for t in sent[-1]["tools"]),
      sent and sent[-1])
check("0.10.0: der Verlauf zeigt die eigenen Sterne", r["messages"][ix[0]].get("stars") == 4, r["messages"][ix[0]])
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": ix[0], "stars": 2, "share": True})
check("0.10.0: mit Haken: Frage und Antwort reisen mit",
      st == 200 and GWA["posts"][-1][1]["text"]["question"] == "Projekte?"
      and GWA["posts"][-1][1]["text"]["answer"] == v10["messages"][ix[0]]["content"], GWA["posts"][-1])
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"stars": 5})
check("0.10.0: das ganze Gespräch: scope conversation, Sterne am Gespräch",
      st == 200 and GWA["posts"][-1][1]["scope"] == "conversation" and r["stars"] == 5, (st, r.get("stars")))
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": ix[0], "stars": None})
check("0.10.0: zurückziehen: stars null ans Gateway, aus dem Verlauf weg",
      st == 200 and GWA["posts"][-1][1]["stars"] is None and "stars" not in r["messages"][ix[0]], r["messages"][ix[0]])
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": 0, "stars": 3})
check("0.10.0: die Frage der Person ist keine Antwort (400)", st == 400, (st, r))
st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": ix[0], "stars": 9})
check("0.10.0: 9 Sterne: 400", st == 400, (st, r))
GWA["rate_refuse"] = True
try:
    st, r = req("POST", f"/api/conv/{c9['id']}/rate", {"index": ix[0], "stars": 1})
finally:
    GWA["rate_refuse"] = False
with open(app.conv_path("id-anna", c9["id"]), encoding="utf-8") as f:
    kept = json.load(f)["messages"][ix[0]]
check("0.10.0: lehnt das Gateway ab, steht im Verlauf nichts (kein Stern, der nirgends gezählt ist)",
      st == 400 and "stars" not in kept, (st, r, kept.get("stars")))
_, cp = req("POST", "/api/conv", {"assistant": "projekte"})
st, r = req("POST", f"/api/conv/{cp['id']}/rate", {"stars": 3})
check("0.10.0: D8: ohne Revision (Assistent aus der Konfiguration) keine Sterne",
      st == 400 and "keine Antwort" in r.get("error", ""), (st, r))

# ------------------------------------------------ 0.11.0: Nicht zufrieden (RFC-0101 Stufe 3)
st, pre = req("GET", f"/api/conv/{c9['id']}/unhappy?index={ix[0]}")
check("0.11.0: der Dialog ist vorbelegt: Frage, Antwort, Werkzeug mit Ausgang, Revision",
      st == 200 and pre["question"] == "Projekte?" and pre["answer"] == v10["messages"][ix[0]]["content"]
      and [(t["name"], t["effect"], t["outcome"]) for t in pre["tools"]] == [("list_projects", "read", "ok")]
      and pre["rev"] == 2 and pre["index"] == ix[0] and pre["round"] == 1, (st, pre))
check("0.11.0: D9: keine Aufruf-Kennungen, und der Dialog sagt warum",
      pre.get("call_ids") is None and "RFC-0098" in pre.get("call_ids_note", ""), pre.get("call_ids_note"))
st, pre0 = req("GET", f"/api/conv/{c9['id']}/unhappy")
check("0.11.0: aus dem Menü (ohne Antwort): die letzte Antwort", st == 200 and pre0["index"] == ix[0], (st, pre0))
st, r = req("GET", f"/api/conv/{c9['id']}/unhappy?index=0")
check("0.11.0: die Frage der Person ist keine Antwort (400)", st == 400, (st, r))
base = {"index": ix[0], "question": "Projekte (ohne Namen)?", "answer": "Fertig.", "expected": ""}
st, r = req("POST", f"/api/conv/{c9['id']}/unhappy", base)
check("0.11.0: ohne Erwartung kein Vorgang (400, Pflicht)", st == 400 and "erwartet" in r.get("error", ""), (st, r))
st, r = req("POST", f"/api/conv/{c9['id']}/unhappy", dict(base, expected="Eine Liste mit Status."))
case = r.get("case") or {}
f11 = case.get("fields") or {}
check("0.11.0: der Vorgang: Frage, Unterart chat-answer, Bezug Gespräch, bearbeiteter Text, Erwartung",
      st == 200 and case["kind"] == "question" and case["subkind"] == "chat-answer"
      and case["object"] == {"kind": "conversation", "id": c9["id"]}
      and "Projekte (ohne Namen)?" in case["body"] and "Eine Liste mit Status." in case["body"]
      and f11["expected"] == "Eine Liste mit Status.", (st, r))
check("0.11.0: die Umstände: assistant@rev, Modell, Ampel, Sicht, Gateway, Runde, Zeit",
      f11.get("assistant") == "alt-privat" and f11.get("rev") == 2 and f11.get("light") == "yellow"
      and f11.get("model") and f11.get("view") and "gateway" in f11 and f11.get("round") == 1
      and f11.get("from") and f11.get("to"), f11)
check("0.11.0: KÖDER: ohne Haken je Karte keine Argumente, keine Ergebnisse, keine Kennungen",
      all(set(t) == {"name", "effect", "outcome"} for t in f11.get("tools", [])) and "call_ids" not in f11
      and "Ergebnis" not in json.dumps(case, ensure_ascii=False), f11)
check("0.11.0: ohne „Doku fehlt“ keine Hilfeseiten; ohne support_queue kein Bereich (Eingang)",
      f11.get("doc_gap") is False and f11.get("doc_pages") == [] and "queue" not in case, case)
st, r = req("POST", f"/api/conv/{c9['id']}/unhappy",
            dict(base, expected="x", share_tools=[pre["tools"][0]["id"]], doc_gap=True))
t11 = (r.get("case") or {}).get("fields", {}).get("tools") or [{}]
check("0.11.0: mit Haken an der Karte: Argumente und Ergebnis dieser Karte reisen mit",
      st == 200 and t11[0].get("arguments") == {} and "Ergebnis" in t11[0].get("result", ""), (st, r))
check("0.11.0: mit „Doku fehlt“: doc_gap true", r["case"]["fields"]["doc_gap"] is True, r["case"]["fields"])
st, r = req("POST", f"/api/conv/{c9['id']}/unhappy", dict(base, expected="x" * 1501))
check("0.11.0: zu lange Erwartung: 400 mit Satz", st == 400 and "kürzen" in r.get("error", ""), (st, r))
st, r = req("POST", "/api/assistants", asst("alt-privat", "private", system="Neu.", support_queue="sap-support"))
st2, r2 = req("POST", f"/api/conv/{c9['id']}/unhappy", dict(base, expected="x"))
check("0.11.0: D6: support_queue des Assistenten wird der Bereich des Vorgangs",
      st == 200 and st2 == 200 and r2["case"].get("queue") == "sap-support", (st, r, st2, r2))
_bad = app.store.problems(dict(asst("x", "private"), support_queue="Kein Bereich"))
check("0.11.0: support_queue, die keine Bereichskennung ist: abgelehnt (dieselbe Regel wie im Gateway)",
      any("support_queue" in e for e in _bad), _bad)
GWA["posts"].clear()
st, r = req("POST", f"/api/conv/{c9['id']}/case", {"index": ix[0], "number": "V-2026-0042"})
req("POST", f"/api/conv/{c9['id']}/case", {"index": ix[0], "number": "V-2026-0042"})
_cp = [b for rest, b in GWA["posts"] if rest == "/alt-privat/cases"]
check("0.12.0: der Vorgang wird beim Gateway gezählt -- assistant@rev und Nummer, einmal",
      r.get("counted") is True and _cp == [{"rev": 2, "number": "V-2026-0042"}], (r.get("counted"), GWA["posts"]))
check("0.11.0: die Vorgangsnummer bleibt im eigenen Verlauf, an der Antwort, einmal",
      st == 200 and [(c["number"], c["index"]) for c in r["cases"]] == [("V-2026-0042", ix[0])]
      and req("GET", f"/api/conv/{c9['id']}")[1]["cases"][0]["number"] == "V-2026-0042", (st, r))
st, r = req("POST", f"/api/conv/{c9['id']}/case", {"index": ix[0], "number": "<script>"})
check("0.11.0: keine Vorgangsnummer: 400", st == 400, (st, r))
GWA["posts"].clear()
st, r = req("POST", f"/api/conv/{c9['id']}/case", {"number": "V-2026-0042", "outcome": "accepted"})
_cp = [b for rest, b in GWA["posts"] if rest == "/alt-privat/cases"]
check("0.14.0 (RFC-0103 D10): „Lösung passt“ geht als Wort ans Gateway (rev, Nummer, outcome) und steht im Verlauf",
      st == 200 and _cp == [{"rev": 2, "number": "V-2026-0042", "outcome": "accepted"}]
      and r["cases"][0].get("outcome") == "accepted", (st, r.get("cases"), GWA["posts"]))
st, r = req("POST", f"/api/conv/{c9['id']}/case", {"number": "V-2026-0099", "outcome": "accepted"})
st2, r2 = req("POST", f"/api/conv/{c9['id']}/case", {"number": "V-2026-0042", "outcome": "closed"})
check("0.14.0: ein Vorgang, den das Gespräch nicht kennt, oder ein fremdes Wort: 400",
      st == 400 and st2 == 400 and len([1 for rest, _ in GWA["posts"] if rest == "/alt-privat/cases"]) == 1,
      (st, r, st2, r2))
_pg = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"), encoding="utf-8").read()
check("0.14.0: die Seite fragt einmal alle eigenen Vorgänge (Punkte), nimmt über /platform/feedback/mine/…/accept ab",
      'fetch("/platform/feedback/mine", ' in _pg and '"/platform/feedback/mine/" + encodeURIComponent(number)' in _pg
      and "a.target = \"_blank\"" in _pg and "waiting_reporter" in _pg, None)
GWA["gets"].clear()
st, r = req("GET", "/api/assistants/alt-privat/evaluation")
check("0.12.0: die Auswertung je Revision kommt vom Gateway, als die Person",
      st == 200 and [x["rev"] for x in r["revisions"]] == [2, 1] and [g for g in GWA["gets"] if g] == ["/alt-privat/evaluation"], (st, r, GWA["gets"]))
st, r = req("GET", "/api/assistants/alt-privat/diff?a=1&b=x")
check("0.12.0: Vergleich mit einer Nummer, die keine ist: 400, nichts ans Gateway",
      st == 400 and [g for g in GWA["gets"] if g] == ["/alt-privat/evaluation"], (st, r))
st, r = req("GET", "/api/assistants/alt-privat/diff?a=1&b=2")
check("0.12.0: der Vergleich zweier Revisionen", st == 200 and "+Neu." in r["diff"]
      and GWA["gets"][-1] == "/alt-privat/diff?a=1&b=2", (st, r, GWA["gets"]))
_msgs = [{"role": "user", "content": "Wie?"},
         {"role": "tool", "name": "help_search", "content": '{"link": "https://hilfe.example/read/a#s2"}'},
         {"role": "assistant", "content": "Siehe [Anleitung A](https://hilfe.example/read/a#s2) und "
                                          "[fremd](https://evil.example/x)."}]
check("0.11.0: Hilfeseiten: nur, was die Antwort verlinkt UND ein Hilfe-Werkzeug geliefert hat",
      app.doc_pages(_msgs[2]["content"], _msgs) == [{"title": "Anleitung A", "url": "https://hilfe.example/read/a#s2"}],
      app.doc_pages(_msgs[2]["content"], _msgs))
st, r = req("POST", "/api/assistants/alt-privat/state", {"state": "inactive"})
st2, r2 = req("POST", "/api/conv", {"assistant": "alt-privat"})
check("0.9.0: deaktiviert: kein neues Gespräch (409)", st == 200 and st2 == 409, (st, r, st2, r2))
st, r = req("POST", f"/api/conv/{c9['id']}/send", {"text": "Noch da?"})
check("0.9.0: ... und ein laufendes sagt es", st == 403 and "deaktiviert" in r.get("error", ""), (st, r))
app.ASSISTANTS_ONLY = ["alt-alle"]
try:
    st, s9 = req("GET", "/api/state")
finally:
    app.ASSISTANTS_ONLY = []
check("0.9.0 D9: CHAT_ASSISTANTS_ONLY beschränkt diesen Chat", [a["id"] for a in s9["assistants"]] == ["alt-alle"], s9["assistants"])
GWA["down"] = True
try:
    st, s9 = req("GET", "/api/state")
    st2, r2 = req("POST", "/api/assistants", asst("waehrend-ausfall", "private"))
finally:
    GWA["down"] = False
check("0.9.0: Gateway antwortet nicht: nur der aus der Konfiguration, mit einem Satz",
      [a["id"] for a in s9["assistants"]] == ["projekte"] and any("nicht antwortet" in e for e in s9["errors"]), s9)
check("0.9.0: KÖDER: Speichern während des Ausfalls landet NICHT in der Datei des Chats",
      st2 == 400 and not os.path.exists(os.path.join(DATA, "assistants.json")), (st2, r2))
st, _ct, html = raw_get("/")
html = html.decode("utf-8")
check("0.9.0: die Seite zeigt Fassung, deaktiviert, Übernehmen und Deaktivieren",
      '"Fassung " + a.rev' in html and "Ins MCP-Gateway übernehmen" in html and '"Deaktivieren"' in html)
check("0.10.0: die Seite hat Sterne an der Antwort, „Gespräch bewerten“ und den Haken",
      "function rateRow(" in html and 'id="rateconv"' in html and "Frage und Antwort mitgeben" in html)
check("0.11.0: die Seite hat den Dialog, den Haken je Karte, die Statusabfrage und das Feld support_queue",
      "function openSad(" in html and 'id="saddlg"' in html and "Argumente und Ergebnis mitgeben" in html
      and "/platform/feedback/mine" in html and 'name="support_queue"' in html
      and "Nicht zufrieden? An den Support geben" in html)
check("0.12.0: die Seite hat die Auswertung mit Sternverteilung und den Vergleich",
      "function openEval(" in html and 'id="evdlg"' in html and "Revisionen vergleichen" in html)
check("0.15.0 (RFC-0103 D9): die Ansicht „Vorgänge“: Knopf mit Zahl, Tabelle der eigenen Meldungen, "
      "Filter offen/Status/Assistent, „dir zugeordnet“ als Link",
      'id="casesbtn"' in html and 'id="caseview"' in html and "function renderCases(" in html
      and 'id="cfopen"' in html and 'id="cfstatus"' in html and 'id="cfasst"' in html
      and "d.assigned_url" in html and '"#vorgaenge"' in html and "dir zugeordnet" in html)
check("0.15.0: gezählt werden die offenen eigenen (nicht geschlossen, nicht abgelehnt)",
      'const CASE_ENDED = ["closed", "rejected"];' in html
      and "filter(st => !CASE_ENDED.includes(st.status)).length" in html)
# ---- 0.21.0 (RFC-0119 §6): die App hinter den Werkzeugen wurde entfernt
_d, _r, _st = GWA["items"]["alt-privat"]
GWA["items"]["alt-privat"] = (dict(_d, tools_gone="all"), _r, "active")
st, s21 = req("GET", "/api/state")
_a = next((a for a in s21["assistants"] if a["id"] == "alt-privat"), {})
check("0.21.0: das Gateway sagt tools_gone=all, der Chat reicht es an die Seite weiter",
      _a.get("tools_gone") == "all", _a)
st2, r2 = req("POST", "/api/conv", {"assistant": "alt-privat"})
check("0.21.0: kein neues Gespräch (409) mit „gibt es nicht mehr“",
      st2 == 409 and "gibt es nicht mehr" in r2.get("error", ""), (st2, r2))
st, r = req("POST", f"/api/conv/{c9['id']}/send", {"text": "Noch da?"})
check("0.21.0: ... und ein laufendes Gespräch sagt es, statt das Modell ohne Werkzeuge raten zu lassen",
      st == 403 and "die App dahinter wurde entfernt" in r.get("error", ""), (st, r))
GWA["items"]["alt-privat"] = (dict(_d, tools_gone="some"), _r, "active")
st2, r2 = req("POST", "/api/conv", {"assistant": "alt-privat"})
check("0.21.0: KÖDER: nur ein Teil der Werkzeuge weg -- das Gespräch geht", st2 == 200, (st2, r2))
GWA["items"]["alt-privat"] = (_d, _r, _st)
check("0.21.0: die Karte zeigt es und sperrt „Neues Gespräch“",
      '"Werkzeuge gibt es nicht mehr"' in _pg and 'a.tools_gone === "all"; acts.appendChild(go)' in _pg, None)
GWA["on"] = False

# ------------------------------------------------ 0.16.0 (RFC-0104 §4): das KI-Gateway ohne Schlüssel
# Die nachgebaute Plattform bekommt die Liste /aigw (nur mit dem KI-Client-Schlüssel) und je
# KI-Gateway eine via-Tür /aivia/<name>/, die wie die echte beide Nachweise verlangt.
AK = "ai-client-key-456"
AI = {"gws": ["aigw1"], "calls": [], "nogrant": {"aigw-ohne"}}
_get0, _post0 = Fake.do_GET, Fake.do_POST


def _ai_door(self):
    """(Name, Rest) einer via-Tür, oder None; 401/403 wie die Plattform und das Gateway."""
    if not self.path.startswith("/aivia/"):
        return None
    name, _, rest = self.path[len("/aivia/"):].partition("/")
    act = self.headers.get("X-OAAP-Act-Assertion", "")
    AI["calls"].append({"gw": name, "path": "/" + rest, "auth": self.headers.get("Authorization", ""),
                        "act": act, "light": self.headers.get("X-OAAP-Light", "")})
    if self.headers.get("Authorization") != "Bearer " + AK or not act.startswith("ZS-"):
        self.reply(401, {"error": {"message": "unauthorized"}})
        return False
    if name in AI["nogrant"]:
        self.reply(403, {"error": {"message": "unknown key"}})
        return False
    return name, "/" + rest


def _ai_get(self):
    if self.path == "/aigw":
        if self.headers.get("Authorization") != "Bearer " + AK:
            return self.reply(401, {})
        port = self.server.server_address[1]
        return self.reply(200, {"v": 1, "gateways": [
            {"name": n, "app": "ai-gateway", "via": f"http://127.0.0.1:{port}/aivia/{n}/"} for n in AI["gws"]]})
    door = _ai_door(self)
    if door is None:
        return _get0(self)
    if door is False:
        return None
    name, rest = door
    if rest == "/models":
        return self.reply(200, {"object": "list", "data": [{"id": "standard", "oaap_light": "green"},
                                                           {"id": name + "-modell", "oaap_light": "green"}]})
    if rest == "/usage":
        return self.reply(200, {"ceiling": "green"})
    return self.reply(404, {})


def _ai_post(self):
    if not self.path.startswith("/aivia/"):
        return _post0(self)
    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
    door = _ai_door(self)
    if door is False:
        return None
    name, rest = door
    if rest != "/chat/completions":
        return self.reply(404, {})
    AI["calls"][-1]["body"] = body
    last = body["messages"][-1]
    return self.reply(200, {"choices": [{"message": {"role": "assistant",
                                                     "content": f"{name}: " + str(last.get("content"))[:40]}}]})


Fake.do_GET, Fake.do_POST = _ai_get, _ai_post
saved = (app.KI_URL, app.AI_CLIENT_KEY, app.AI_CLIENT_URL, app.AI_GATEWAY)
app.AI_CLIENT_KEY, app.AI_CLIENT_URL = AK, f"http://127.0.0.1:{FP}/aigw"
try:
    # 1. eine Destination ki gewinnt (bec bleibt, wie es ist)
    n_model = len(MODEL)
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: mit Destination ki: sie ist der Weg, die KI-Gateways werden nicht gefragt",
          it["destination"]["ok"] and "geht vor" in it["destination"]["detail"] and "ai_gateway" not in it
          and not AI["calls"], (it, AI["calls"]))
    _, c = req("POST", "/api/conv", {"assistant": "projekte"})
    st, r = req("POST", f"/api/conv/{c['id']}/send", {"text": "Hallo Destination"})
    check("0.16.0: ... und ein Modellzug geht an die Destination, nicht an die via-Tür",
          r.get("state") == "done" and len(MODEL) == n_model + 1 and not AI["calls"], (r, AI["calls"]))

    # 2. ohne Destination, genau ein KI-Gateway: seine via-Tür
    app.KI_URL = ""
    st, s = req("GET", "/api/state")
    check("0.16.0: ohne Destination, mit KI-Client: der Zustand meldet ein Modell", s["model"] is True, s)
    st, r = req("GET", "/api/models")
    call = AI["calls"][-1] if AI["calls"] else {}
    check("0.16.0: /api/models über die via-Tür: <via>/models, Client-Schlüssel und die Zusicherung DIESER Anfrage",
          st == 200 and [m["id"] for m in r["models"]] == ["standard", "aigw1-modell"] and r["error"] == ""
          and call.get("path") == "/models" and call.get("auth") == "Bearer " + AK
          and call.get("act") == f"ZS-anna-{N['n']}", (r, call))
    AI["calls"].clear()
    n_model = len(MODEL)
    _, c = req("POST", "/api/conv", {"assistant": "projekte"})
    st, r = req("POST", f"/api/conv/{c['id']}/send", {"text": "Hallo Gateway"})
    sent = N["n"]
    comp = [x for x in AI["calls"] if x["path"] == "/chat/completions"]
    check("0.16.0: ein Modellzug geht an <via>/chat/completions, mit Schlüssel, Zusicherung und Ampel",
          r.get("state") == "done" and len(MODEL) == n_model and len(comp) == 1
          and comp[0]["gw"] == "aigw1" and comp[0]["auth"] == "Bearer " + AK
          and comp[0]["act"] == f"ZS-anna-{sent}" and comp[0]["light"] == "green"
          and r["messages"][-1]["content"].startswith("aigw1: Hallo Gateway"), (r, AI["calls"]))
    check("0.16.0: KÖDER: weder Client-Schlüssel noch Zusicherung im Rumpf an das Modell",
          AK not in json.dumps(comp[0].get("body")) and "ZS-" not in json.dumps(comp[0].get("body")))
    check("0.16.0: die Werkzeuge kommen weiter über die MCP-Tür (Client-Schlüssel des MCP)",
          len(comp[0]["body"].get("tools") or []) == 3, comp[0].get("body", {}).get("tools"))
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: Einrichtung prüfen nennt den Weg (KI-Gateway aigw1, via-Tür) und den Modellnamen",
          all(i["ok"] for i in r["items"]) and "aigw1" in it["ai_gateway"]["title"]
          and "via-Tür" in it["ai_gateway"]["title"] and it["model"]["ok"] and "destination" not in it, r)
    st, r = req("GET", "/api/light?assistant=projekte")
    check("0.16.0: Ampel des Assistenten: Farbe des Modells und Decke über die via-Tür",
          st == 200 and r["model"]["light"] == "green" and r["ceiling"] == "green", r)
    AI["calls"].clear()
    st, r = req("GET", "/api/models", assertion=False)
    check("0.16.0: ohne Zusicherung kein Ruf an die via-Tür, aber ein Satz",
          r["models"] == [] and "Zusicherung" in r["error"] and not [x for x in AI["calls"]], (r, AI["calls"]))

    # 3. mehrere KI-Gateways
    AI["gws"] = ["aigw1", "aigw2"]
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: mehrere KI-Gateways ohne CHAT_AI_GATEWAY: Einrichtung prüfen sagt es, nennt beide",
          not it["ai_gateway"]["ok"] and "mehrere" in it["ai_gateway"]["detail"]
          and "aigw1, aigw2" in it["ai_gateway"]["detail"] and "CHAT_AI_GATEWAY" in it["ai_gateway"]["next"]
          and "model" not in it, it)
    _, c = req("POST", "/api/conv", {"assistant": "projekte"})
    AI["calls"].clear()
    st, r = req("POST", f"/api/conv/{c['id']}/send", {"text": "Hallo mehrere"})
    check("0.16.0: ... und der Chat sagt es im Gespräch, statt eins zu raten",
          r.get("state") == "error" and "CHAT_AI_GATEWAY" in r.get("said", "")
          and not [x for x in AI["calls"] if x["path"] == "/chat/completions"], r)
    app.AI_GATEWAY = "aigw2"
    st, r = req("POST", f"/api/conv/{c['id']}/send", {"text": "Hallo zwei"})
    check("0.16.0: CHAT_AI_GATEWAY nennt eins: dorthin",
          r.get("state") == "done" and r["messages"][-1]["content"].startswith("aigw2:"), r)
    app.AI_GATEWAY = "gibtsnicht"
    st, r = req("GET", "/api/models")
    check("0.16.0: CHAT_AI_GATEWAY nennt ein unbekanntes: der Satz nennt den Wert und die Liste",
          r["models"] == [] and "gibtsnicht" in r["error"] and "aigw1, aigw2" in r["error"], r)
    app.AI_GATEWAY = ""
    AI["gws"] = []
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: kein KI-Gateway im Mandanten: nicht ok, mit dem Grund",
          not it["ai_gateway"]["ok"] and "kein KI-Gateway" in it["ai_gateway"]["detail"], it)

    # 4. keine Freigabe im KI-Gateway (403)
    AI["gws"] = ["aigw-ohne"]
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: ohne Freigabe: der Weg ist da, das Gateway antwortet 403 -- „im KI-Gateway fehlt die "
          "Freigabe für diesen Chat“, der Schritt nennt die Freigabe der Art client und die Instanz",
          it["ai_gateway"]["ok"] and not it["model"]["ok"]
          and "im KI-Gateway fehlt die Freigabe für diesen Chat" in it["model"]["detail"]
          and "client" in it["model"]["next"] and "vuebung-chat" in it["model"]["next"], it)
    st, r = req("GET", "/api/models")
    check("0.16.0: ... /api/models sagt denselben Grund", r["models"] == [] and app.NO_GRANT in r["error"], r)
    _, c = req("POST", "/api/conv", {"assistant": "projekte"})
    st, r = req("POST", f"/api/conv/{c['id']}/send", {"text": "Hallo ohne"})
    check("0.16.0: ... und das Gespräch auch", r.get("state") == "error" and app.NO_GRANT in r.get("said", ""), r)

    # 5. weder Destination noch KI-Client
    app.AI_CLIENT_KEY = ""
    st, r = req("GET", "/api/setup?assistant=projekte")
    it = items(r)
    check("0.16.0: weder Destination noch KI-Client: nicht ok, beide Wege im nächsten Schritt",
          not it["destination"]["ok"] and "Portal" in it["destination"]["next"]
          and "ai_client" in it["destination"]["next"] and "model" not in it, it)
    st, s = req("GET", "/api/state")
    check("0.16.0: ... und der Zustand meldet kein Modell", s["model"] is False, s)
finally:
    app.KI_URL, app.AI_CLIENT_KEY, app.AI_CLIENT_URL, app.AI_GATEWAY = saved
    Fake.do_GET, Fake.do_POST = _get0, _post0
st, _ct, html = raw_get("/")
check("0.16.0: die Seite fragt „Einrichtung prüfen“ auch, wenn das Modell fehlt (S.model)",
      "!S.model" in html.decode("utf-8"))

# ------------------------------------------------ 0.17.0: Leiste als Baum, Name und Symbol
_t0 = app.TITLE
app.TITLE = 'Team <Chat> & "Co"'
try:
    st, ct, body = raw_get("/")
    pg = body.decode("utf-8")
    st2, ct2, mf = raw_get("/manifest.webmanifest")
finally:
    app.TITLE = _t0
check("0.17.0: CHAT_TITLE steht maskiert im Reiter, auf dem Homescreen und im Kopf der Leiste",
      "<title>Team &lt;Chat&gt; &amp; &quot;Co&quot;</title>" in pg
      and 'name="apple-mobile-web-app-title" content="Team &lt;Chat&gt; &amp; &quot;Co&quot;"' in pg
      and "<b>Team &lt;Chat&gt; &amp; &quot;Co&quot;</b>" in pg and "__TITLE__" not in pg, None)
_m = json.loads(mf.decode("utf-8")) if st2 == 200 else {}
check("0.17.0: das Web-App-Manifest nennt denselben Namen und die Symbole",
      st2 == 200 and "manifest+json" in ct2 and _m.get("name") == 'Team <Chat> & "Co"'
      and _m.get("short_name") == _m.get("name") and _m.get("start_url") == "./"
      and [i["src"] for i in _m["icons"]] == ["static/icon-192.png", "static/icon-512.png", "static/favicon.svg"], (st2, _m))
_ok = []
for _n, _ty, _magic in (("favicon.svg", "image/svg+xml", b"<svg"), ("icon-180.png", "image/png", b"\x89PNG"),
                        ("icon-192.png", "image/png", b"\x89PNG"), ("icon-512.png", "image/png", b"\x89PNG")):
    st, ct, body = raw_get("/static/" + _n)
    _ok.append(st == 200 and ct == _ty and body.startswith(_magic))
check("0.17.0: Favicon und Homescreen-Symbole werden ausgeliefert", all(_ok), _ok)
st, ct, body = raw_get("/static/../app.py")
check("0.17.0: KÖDER: kein Weg aus static hinaus", st != 200 or b"VERSION" not in body, st)
check("0.17.0: Vorgabe-Name ist „Chat“", app.TITLE == "Chat" or bool(os.environ.get("CHAT_TITLE")), app.TITLE)
st, s17 = req("GET", "/api/state")
check("0.17.0: die Gesprächsliste nennt auch den Beginn (für den Kalender)",
      s17["conversations"] and all("created" in c for c in s17["conversations"])
      and all(c["created"] for c in s17["conversations"]), s17["conversations"][:2])
st, ct, body = raw_get("/")
pg = body.decode("utf-8")
check("0.17.0: Leiste: Obergruppen Kalender (Jahr, Monat) über Assistenten, mit Symbolen",
      "const TREES = [" in pg and pg.index('id: "calendar"') < pg.index('id: "assistants"')
      and "calendar: '<rect" in pg and "assistants: '<rect" in pg and "MONTHS[m]" in pg
      and "if (!nodes.length && !t.always) continue;" in pg, None)
check("0.17.0: kein Knopf „Neues Gespräch“ in der Leiste; Vorgänge zwischen Assistenten und Einstellungen",
      'id="new"' not in pg and '$("new")' not in pg
      and pg.index('id="manage"') < pg.index('id="casesbtn"') < pg.index('id="nset"'), None)
check("0.17.0: Gruppieren-Einstellung entfällt, /neu beginnt ohne den alten Knopf",
      'data-k="group"' not in pg and 'run: () => startDraft($("pick").value)' in pg, None)
check("0.17.0/0.26.0: schmal: Pflege (Neu, Export, Import; im Menü Ändern, Löschen, Auswertung) aus, Karte nur „Gespräch beginnen“",
      ".edit{display:none!important}" in pg and 'class="primary edit" id="anewbtn"' in pg
      and '<span class="edit" id="amore"></span>' in pg and '{label: "Exportieren", run: () => $("aexp").click()}' in pg
      and '{label: "Importieren …", run: () => $("aimp").click()}' in pg
      and '{label: "Ändern", show: () => !narrow()' in pg and '{label: "Löschen", danger: true, show: () => !narrow()' in pg
      and pg.count('{label: "Auswertung", show: () => !narrow()') == 2
      and 'el("button", "primary", "Gespräch beginnen")' in pg, None)
# 0.26.0: Launchpad -- Symbol, Farbe, Gruppe je Person
st, r = req("GET", "/api/launchpad")
check("0.26.0: Launchpad leer, mit Auswahl", st == 200 and r["assistants"] == {} and r["workspaces"] == {}
      and "folder" in r["icons"] and "blue" in r["colors"], (st, r))
st, r = req("POST", "/api/launchpad", {"kind": "assistants", "id": "annas", "icon": "code", "color": "teal", "group": "  Kunde   A "})
check("0.26.0: Eintrag gesetzt, Gruppe bereinigt", st == 200 and r["assistants"]["annas"] == {"icon": "code", "color": "teal", "group": "Kunde A"}, (st, r))
st, r2 = req("GET", "/api/launchpad", person="bert")
check("0.26.0: je Person -- bert sieht annas Anordnung nicht", st == 200 and r2["assistants"] == {}, r2)
st, r = req("POST", "/api/launchpad", {"kind": "assistants", "id": "annas", "icon": "<svg>"})
check("0.26.0: unbekanntes Symbol abgelehnt", st == 400 and "Symbol" in r["error"], (st, r))
st, r = req("POST", "/api/launchpad", {"kind": "assistants", "id": "annas", "color": "#ff0000"})
check("0.26.0: freie Farbe abgelehnt", st == 400 and "Farbe" in r["error"], (st, r))
st, r = req("POST", "/api/launchpad", {"kind": "views", "id": "x", "icon": "code"})
check("0.26.0: unbekannte Art abgelehnt", st == 400, (st, r))
st, r = req("POST", "/api/launchpad", {"kind": "workspaces", "id": "../x", "icon": "code"})
check("0.26.0: Kennung mit Pfad abgelehnt", st == 400, (st, r))
st, r = req("POST", "/api/launchpad", {"kind": "assistants", "id": "annas"})
check("0.26.0: leer nimmt den Eintrag heraus", st == 200 and "annas" not in r["assistants"], (st, r))
st, r = req("GET", "/api/state")
check("0.26.0: die Datei des Launchpads ist kein Gespräch", st == 200 and all(c["id"] != "launchpad" for c in r["conversations"]), None)
# 0.27.0: Status je Gespräch, aktive Stunden für den Kalender
st, r = req("GET", "/api/state")
cs = r["conversations"]
check("0.27.0: jedes Gespräch hat einen Status (ohne Angabe „open“) und seine Stunden",
      cs and all(c["status"] in ("open", "question", "waiting", "done") for c in cs)
      and all(isinstance(c["hours"], list) and all(len(h) == 13 for h in c["hours"]) for c in cs), cs[:1])
c0 = cs[0]
st, r = req("POST", f"/api/conv/{c0['id']}/status", {"status": "waiting"})
st2, r2 = req("GET", "/api/state")
again = [c for c in r2["conversations"] if c["id"] == c0["id"]][0]
check("0.27.0: Status setzen, „letzte Änderung“ bleibt", st == 200 and r["status"] == "waiting"
      and again["status"] == "waiting" and again["updated"] == c0["updated"], (st, r.get("status"), again.get("updated"), c0["updated"]))
st, r = req("POST", f"/api/conv/{c0['id']}/status", {"status": "erledigt"})
check("0.27.0: unbekannter Status abgelehnt", st == 400, (st, r))
st, r = req("POST", f"/api/conv/{c0['id']}/status", {"status": "done"}, person="bert")
check("0.27.0: fremdes Gespräch: 404", st == 404, (st, r))
check("0.27.0: active_hours nimmt Anlage und Nachrichten, je Stunde einmal",
      app.active_hours({"created": "2026-11-20T09:01:00Z", "messages": [{"at": "2026-11-20T09:30:00Z"}, {"at": "2026-11-23T14:00:00Z"}, {}]})
      == ["2026-11-20T09", "2026-11-23T14"])
check("0.27.0: Seite: Startseite statt letztem Gespräch, Kanban, Kalender, Status-Knopf",
      'else showView("start");' in pg and 'id="kanview"' in pg and 'id="calview"' in pg and 'id="stbtn"' in pg
      and 'id="kandone"' in pg and "S.conversations[0].id); else startDraft();" not in pg)
check("0.27.0: Status nur von dir -- kein automatisches Setzen im Server",
      app.STATUSES == ("open", "question", "waiting", "done") and 'conv["status"] = ' in open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py"), encoding="utf-8").read().split('if action == "status":')[1][:600])
import re as _re
pg_icons =set(_re.findall(r"^  ([a-z]+): '<", pg.split("const LPICON = {")[1].split("};")[0], _re.M))
pg_colors = set(_re.findall(r"([a-z]+): \"#", pg.split("const LPCOLOR = {")[1].split("};")[0]))
check("0.26.0: Seite und Server kennen dieselben Symbole und Farben",
      pg_icons == set(app.LP_ICONS) and pg_colors == set(app.LP_COLORS), (pg_icons ^ set(app.LP_ICONS), pg_colors ^ set(app.LP_COLORS)))

check("0.17.0: Kontextmenü: ein Symbol an jeder Nachricht (Kopieren) und jedem Gespräch der Leiste "
      "(Umbenennen, Löschen); erweiterbar über MSG_ACTIONS / CONV_ACTIONS",
      "function ctxButton(" in pg and 'const MSG_ACTIONS = [\n  {label: "Kopieren"'.replace("\n", "\r\n" if "\r\n" in pg else "\n") in pg
      and "const CONV_ACTIONS = [" in pg and 'label: "Umbenennen", run: c => renameConv(c.id, c.title)' in pg
      and 'label: "Löschen", danger: true, run: c => deleteConv(c.id, c.title)' in pg
      and 'row.append(b, ctxButton("Funktionen: " + c.title, CONV_ACTIONS, c));' in pg
      and pg.count("out.push(withCtx(d, ") == 2 and 'id="ctx"' in pg, None)
check("0.17.0: Kopieren: formatiert und Klartext, sonst Klartext, sonst Auswahl -- und sagt, wenn es nicht geht",
      "new ClipboardItem(" in pg and "navigator.clipboard.writeText(plain)" in pg
      and 'document.execCommand("copy")' in pg and "Kopieren ging nicht" in pg, None)
check("0.17.0: Löschen aus der Leiste trifft auch ein Gespräch, das nicht offen ist (die offene bleibt)",
      "async function deleteConv(id, title)" in pg and 'if (!wasOpen) { toast("Gespräch gelöscht"); return; }' in pg
      and '$("del").onclick = () => { if (C) deleteConv(C.id, C.title); };' in pg, None)
# 0.19.0 (RFC-0117 §3.5): der Weg zu den Vorlagen im Portal, aus OAAP_PORTAL_URL
_old = os.environ.get("OAAP_PORTAL_URL")
os.environ["OAAP_PORTAL_URL"] = "https://portal.kunde.example/instanzen/kunde-chat"
check("Vorlagen: das Portal des Mandanten + /assistenten",
      app.templates_url() == "https://portal.kunde.example/assistenten", app.templates_url())
os.environ["OAAP_PORTAL_URL"] = "javascript:alert(1)"
check("KÖDER: eine Adresse ohne http(s) ergibt keinen Link", app.templates_url() == "")
if _old is None:
    os.environ.pop("OAAP_PORTAL_URL", None)
else:
    os.environ["OAAP_PORTAL_URL"] = _old
check("die Seite zeigt den Link nur mit templates_url (tenant_admin)",
      'id="atpl"' in pg and "S.templates_url" in pg and '"templates_url": templates_url() if "tenant_admin" in who.roles' in open(
          os.path.join(HERE, "app.py"), encoding="utf-8").read())
# 0.20.0 (RFC-0118 §5 Typ A): Hilfe und Benutzermenü in der Leiste unten, aus denselben
# Quellen wie der Rahmen der Plattform -- aber ohne /platform/app.js (eigenes Layout).
_me = _pg.split("async function loadMe")[1].split("const ASK")[0]
check("0.20.0: Name ohne OAAP, Vorgabe „Chat“", "OAAP" not in app.TITLE and "<title>__TITLE__</title>" in _pg, app.TITLE)
check("0.20.0: Leiste unten: Hilfe aus /platform/context.json (Produktseite chat)",
      'id="sme"' in _pg and 'getJSONq("/platform/context.json")' in _pg
      and '"/p/" + encodeURIComponent(app.id || "chat")' in _me, None)
check("0.20.0: Benutzermenü aus /auth/whoami: Anzeigename, Portal, Passwort ändern",
      'getJSONq("/auth/whoami")' in _pg and "me.display_name || me.username" in _me and '"Portal"' in _me
      and '"Passwort ändern"' in _me, None)
check("0.20.0: Abmelden ist ein POST an /auth/logout, kein Link",
      'f.method = "post"' in _me and '"/auth/logout"' in _me and 'href="/auth/logout"' not in _pg, None)
check("0.20.0: kein Rahmen-Skript, keine Rollen in der Leiste",
      'src="/platform/app.js"' not in _pg and "roles" not in _me and 'S.user || "") + " · Chat' not in _pg, None)
check("0.20.0: der Sprung aus „Fragen“ (?frage=) bleibt", "const ASK = (() =>" in _pg and 'q.get("frage")' in _pg, None)
srv.shutdown()
fake.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print(f"\n{sum(RESULTS)} passed, {len(RESULTS) - sum(RESULTS)} failed")
sys.exit(0 if all(RESULTS) else 1)
