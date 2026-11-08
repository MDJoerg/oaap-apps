"""Chat -- die erste Fassung des Plattform-Chats (RFC-0074 Stufe 2, I-41 Schritt 4).

Ein Assistent (oaap.ai.assistants 0.1), die Werkzeuge EINER Sicht des
MCP-Gateways, ein Modell über das KI-Gateway, der Verlauf als Datei.

    Browser --Sitzung--> Chat               (die Plattform gibt die Zusicherung an DIESE Instanz)
    Chat --via-Tür auf der eigenen Seite--> MCP-Gateway --> App      (als die Person, Verfahren A)
    Chat --Destination ki--> KI-Gateway --> Modell                   (der Schlüssel bleibt auf der Plattform)

- Der Assistent hat keine Rechte (D1): jeder Werkzeugaufruf läuft als die
  Person, die gerade schreibt; die Sicht begrenzt die Werkzeuge, die App die Daten.
- Der Browser treibt die Schleife (D7): jede Runde ist eine eigene Anfrage
  mit frischer Zusicherung. Die App speichert keine Zusicherung (§5.3) und
  hat keinen Rückfall auf einen eigenen Zugang (§5.2).
- Ein Werkzeug, das schreibt, läuft erst nach Bestätigung, jedes Mal (D6).
- Der Verlauf liegt unter /data, je Person ein Ordner; nur sie liest ihn.

Nur Standardbibliothek.
"""
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import assistants
import loop

VERSION = "0.1.0"
PORT = int(os.environ.get("PORT", "8000"))
DATA = os.environ.get("CHAT_DATA_DIR", "/data")
CLIENT_KEY = (os.environ.get("OAAP_MCP_CLIENT_KEY") or "").strip()
CLIENT_URL = (os.environ.get("OAAP_MCP_CLIENT_URL") or "").strip()
KI_URL = (os.environ.get("OAAP_DESTINATION_KI_URL") or "").strip()
MODEL_TIMEOUT = int(os.environ.get("CHAT_MODEL_TIMEOUT", "240"))
ASSERTION = "X-OAAP-User-Assertion"
ACT_ASSERTION = "X-OAAP-Act-Assertion"
MCP_VERSION = "2025-11-25"
MAX_BODY = 64_000
MAX_TEXT = 4_000
PERSON = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CONV = re.compile(r"^[a-z0-9]{12}$")
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

DEFAULT_ASSISTANT = {
    "id": "assistent", "title": "Assistent",
    "greeting": "Hallo! Ich kann für dich in den angebundenen Anwendungen nachsehen.",
    "system": ("Du bist der Assistent eines kleinen Betriebs. Antworte kurz und auf Deutsch. "
               "Daten kennst du nur aus den Werkzeugen -- erfinde nichts. Fehlt für ein "
               "Werkzeug eine Angabe, frag nach, statt sie zu raten."),
    "model": "standard", "view": "chat",
    "visibility": {"scope": "tenant"}, "owner": "tenant", "commands": False,
}


def load_assistant():
    raw = (os.environ.get("CHAT_ASSISTANT") or "").strip()
    try:
        a = dict(DEFAULT_ASSISTANT, **json.loads(raw)) if raw else dict(DEFAULT_ASSISTANT)
    except (ValueError, TypeError):
        return dict(DEFAULT_ASSISTANT), ["CHAT_ASSISTANT ist kein JSON-Objekt."]
    return a, assistants.check(a)


ASSISTANT, ASSISTANT_ERRORS = load_assistant()
try:
    MODEL_EXTRA = json.loads(os.environ.get("CHAT_MODEL_EXTRA") or "{}")
    assert isinstance(MODEL_EXTRA, dict)
except (ValueError, AssertionError):
    MODEL_EXTRA = {}
    ASSISTANT_ERRORS.append("CHAT_MODEL_EXTRA ist kein JSON-Objekt.")


class Stop(Exception):
    """Eine Runde endet mit einem Satz für die Person; das Gespräch bleibt, wie es war."""

    def __init__(self, text, status=502):
        super().__init__(text)
        self.text, self.status = text, status


# ------------------------------------------------------------------ Verlauf

_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def conv_lock(person, cid):
    with _LOCKS_GUARD:
        return _LOCKS.setdefault((person, cid), threading.Lock())


def person_dir(person):
    return os.path.join(DATA, "people", person)


def conv_path(person, cid):
    return os.path.join(person_dir(person), cid + ".json")


def load_conv(person, cid):
    try:
        with open(conv_path(person, cid), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


def save_conv(person, conv):
    os.makedirs(person_dir(person), exist_ok=True)
    conv["updated"] = now()
    path = conv_path(person, conv["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(conv, f, ensure_ascii=False)
    os.replace(tmp, path)


def list_convs(person):
    out = []
    try:
        names = os.listdir(person_dir(person))
    except FileNotFoundError:
        return out
    for n in names:
        if n.endswith(".json") and CONV.match(n[:-5]):
            c = load_conv(person, n[:-5])
            if c:
                out.append({"id": c["id"], "title": c.get("title") or "Neues Gespräch",
                            "updated": c.get("updated", "")})
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- MCP über die via-Tür

_MCP, _MCP_GUARD = {}, threading.Lock()     # (Person, Gateway, Sicht) -> Sitzungs-ID, nie eine Zusicherung


def gateways():
    if not (CLIENT_KEY and CLIENT_URL):
        raise Stop("Diese Instanz ist kein MCP-Client (Manifest ohne mcp_client, oder eine "
                   "Generalprobe).")
    req = urllib.request.Request(CLIENT_URL, headers={"Authorization": "Bearer " + CLIENT_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            doc = json.loads(r.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise Stop(f"Die Plattform antwortete {exc.code} auf die Gateway-Liste.") from None
    except (OSError, ValueError) as exc:
        raise Stop(f"Gateway-Liste nicht gelesen: {type(exc).__name__}") from None
    if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("gateways"), list):
        raise Stop("Die Gateway-Liste hat eine unbekannte Form.")
    name = ASSISTANT.get("gateway") or ""
    gws = doc["gateways"]
    hit = [g for g in gws if g.get("name") == name] if name else (gws if len(gws) == 1 else [])
    if not hit:
        raise Stop(f"Gateway {name or '(keins genannt)'} ist nicht in der Liste des Mandanten: "
                   + (", ".join(g.get("name", "?") for g in gws) or "die Liste ist leer") + ".")
    return hit[0]


def mcp_post(url, msg, session, assertion):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion,
               "X-OAAP-Assistant": ASSISTANT["id"]}
    if session:
        headers["Mcp-Session-Id"] = session
        headers["MCP-Protocol-Version"] = MCP_VERSION
    req = urllib.request.Request(url, data=json.dumps(msg).encode(), headers=headers,
                                 method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=120)
    except urllib.error.HTTPError as exc:
        resp = exc
    except OSError as exc:
        raise Stop(f"Die Tür zum MCP-Gateway ist nicht erreichbar ({type(exc).__name__}).") from None
    raw = resp.read(4_000_000)
    msgs = []
    if resp.headers.get("Content-Type", "").startswith("text/event-stream"):
        for block in raw.decode("utf-8", "replace").split("\n\n"):
            data = [ln[6:] for ln in block.split("\n") if ln.startswith("data: ")]
            if data:
                try:
                    msgs.append(json.loads("\n".join(data)))
                except ValueError:
                    pass
    elif raw.strip():
        try:
            msgs.append(json.loads(raw))
        except ValueError:
            pass
    return resp.status, resp.headers.get("Mcp-Session-Id") or session, msgs


class McpWay:
    """Die Werkzeuge der Sicht des Assistenten, als die Person dieser Anfrage."""

    def __init__(self, person, assertion):
        gw = gateways()
        self.url = f'{gw["via"]}/{ASSISTANT["view"]}'
        self.key = (person, gw["name"], ASSISTANT["view"])
        self.assertion = assertion

    def _open(self):
        status, sess, msgs = mcp_post(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION, "capabilities": {},
                       "clientInfo": {"name": "oaap-chat", "version": VERSION}}},
            "", self.assertion)
        if status == 401:
            raise Stop("Die Plattform hat dich am MCP-Gateway nicht erkannt (401). Lade die "
                       "Seite neu und versuche es noch einmal.", 401)
        if status != 200 or not sess:
            raise Stop(f"Das MCP-Gateway hat keine Sitzung eröffnet ({status}).")
        mcp_post(self.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sess,
                 self.assertion)
        with _MCP_GUARD:
            _MCP[self.key] = sess
        return sess

    def rpc(self, method, params):
        with _MCP_GUARD:
            sess = _MCP.get(self.key)
        for attempt in (0, 1):
            sess = sess or self._open()
            status, _, msgs = mcp_post(self.url, {"jsonrpc": "2.0", "id": 2, "method": method,
                                                  "params": params}, sess, self.assertion)
            if status in (400, 404) and attempt == 0:     # Sitzung unbekannt oder abgelaufen
                sess = None
                continue
            if status == 401:
                raise Stop("Die Plattform hat dich am MCP-Gateway nicht erkannt (401).", 401)
            if status != 200:
                raise Stop(f"Das MCP-Gateway antwortete {status} auf {method}.")
            return msgs
        raise Stop("Das MCP-Gateway hat die Sitzung zweimal nicht angenommen.")

    def tools(self):
        for m in self.rpc("tools/list", {}):
            if isinstance(m.get("result"), dict):
                return m["result"].get("tools") or []
            if "error" in m:
                raise Stop("Das MCP-Gateway lehnte tools/list ab: "
                           + str((m["error"] or {}).get("message", "")))
        raise Stop("Das MCP-Gateway hat keine Werkzeugliste geschickt.")

    def call(self, name, args):
        return loop.result_text(self.rpc("tools/call", {"name": name, "arguments": args}))


# --------------------------------------------------------------- das Modell

def ask_model(person_name, msgs, tools):
    if not KI_URL:
        raise Stop("Keine Destination ki gebunden -- der Chat erreicht kein Modell.")
    d = datetime.now()
    today = WEEKDAYS[d.weekday()] + d.strftime(", %d.%m.%Y")
    system = (ASSISTANT["system"] + f"\n\nHeute ist {today}. Du sprichst mit {person_name}. "
              "Werkzeuge, die etwas ändern, laufen erst, wenn die Person zustimmt.")
    body = dict(MODEL_EXTRA, model=ASSISTANT["model"], stream=False,
                messages=[{"role": "system", "content": system}] + loop.for_model(msgs))
    body.setdefault("temperature", 0.2)
    if tools:
        body["tools"] = tools
    req = urllib.request.Request(KI_URL.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=MODEL_TIMEOUT) as r:
            reply = json.loads(r.read(4_000_000))
    except urllib.error.HTTPError as exc:
        said = exc.read(2000).decode("utf-8", "replace")
        try:
            said = json.loads(said).get("error", {}).get("message") or said
        except (ValueError, AttributeError):
            pass
        raise Stop(f"Das KI-Gateway antwortete {exc.code}: {str(said)[:200]}") from None
    except TimeoutError:
        raise Stop(f"Das Modell hat in {MODEL_TIMEOUT} s nicht geantwortet.") from None
    except (OSError, ValueError) as exc:
        raise Stop(f"Das KI-Gateway ist nicht erreichbar ({type(exc).__name__}).") from None
    try:
        msg = loop.model_message(reply)
    except ValueError as exc:
        raise Stop(str(exc)) from None
    msg["seconds"] = round(time.monotonic() - t0, 1)
    return msg


# ---------------------------------------------------------------- eine Runde

def step(person, person_name, assertion, conv, decision):
    """Eine Runde: erst offene Werkzeugaufrufe (frische Zusicherung), dann ein Modellzug.

    Gibt den Zustand zurück: `confirm` (eine Bestätigung fehlt), `more` (das
    Modell will Werkzeuge -- der Browser fragt die nächste Runde an) oder `done`.
    """
    msgs = conv["messages"]
    effects = conv.setdefault("effects", {})
    way = None
    for c in loop.pending_calls(msgs):
        name = c["function"]["name"]
        args, bad = loop.call_args(c)
        eff = effects.get(name)
        if eff is None:
            result, note = f"Fehler: Ein Werkzeug {name} gibt es in dieser Sicht nicht.", {}
        elif bad:
            result, note = "Fehler: " + bad, {}
        elif eff != "read" and not (decision and decision.get("call") == c["id"]):
            conv["confirm"] = {"call": c["id"], "name": name, "effect": eff, "arguments": args}
            return "confirm"
        elif eff != "read" and not decision.get("ok"):
            result, note = loop.REFUSED, {"confirmed": False}
        else:
            way = way or McpWay(person, assertion)
            result = way.call(name, args)
            note = {"confirmed": True} if eff != "read" else {}
        msgs.append(dict({"role": "tool", "tool_call_id": c["id"], "name": name,
                          "content": result, "at": now()}, **note))
        conv.pop("confirm", None)
    if loop.model_turns_since_user(msgs) >= loop.MAX_MODEL_TURNS:
        msgs.append({"role": "assistant", "at": now(), "content":
                     f"Ich habe nach {loop.MAX_MODEL_TURNS} Runden aufgehört, ohne fertig zu "
                     "werden. Formuliere die Frage bitte enger."})
        return "done"
    way = way or McpWay(person, assertion)
    tools, effects_now = loop.tools_for_model(way.tools())
    effects.clear()
    effects.update(effects_now)
    reply = ask_model(person_name, msgs, tools)
    reply["at"] = now()
    msgs.append(reply)
    return "more" if reply.get("tool_calls") else "done"


# -------------------------------------------------------------------- Seite

PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")


def public_assistant():
    return {k: ASSISTANT.get(k) for k in ("id", "title", "greeting", "model", "view")}


def view_of(conv):
    """Was der Browser zeigt -- ohne eigene Vermerke, die er nicht braucht."""
    shown = []
    for m in conv["messages"]:
        x = {k: m[k] for k in ("role", "content", "name", "confirmed", "seconds", "at") if k in m}
        if m.get("tool_calls"):
            x["calls"] = [{"name": c["function"]["name"],
                           "arguments": loop.call_args(c)[0]} for c in m["tool_calls"]]
        shown.append(x)
    return {"id": conv["id"], "title": conv.get("title") or "Neues Gespräch",
            "messages": shown, "confirm": conv.get("confirm")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Eine Zeile je Anfrage, nie Kopfzeilen (darin stünde die Zusicherung).
        sys.stderr.write("%s %s %s\n" % (self.command, self.path.split("?")[0], args[1]
                                         if len(args) > 1 else ""))

    def send(self, status, ctype, body):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, doc):
        self.send(status, "application/json; charset=utf-8", json.dumps(doc, ensure_ascii=False))

    def person(self):
        pid = self.headers.get("X-OAAP-User-Id", "").strip()
        return pid if PERSON.match(pid) else ""

    def person_name(self):
        return (self.headers.get("X-OAAP-Display-Name") or self.headers.get("X-OAAP-User")
                or "der angemeldeten Person").strip()[:80]

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.send(200, "text/plain", "ok")
        person = self.person()
        if path == "/":
            with open(PAGE_FILE, encoding="utf-8") as f:
                return self.send(200, "text/html; charset=utf-8", f.read())
        if not person:
            return self.json(401, {"error": "Keine angemeldete Person."})
        if path == "/api/state":
            return self.json(200, {
                "user": self.headers.get("X-OAAP-User", ""), "version": VERSION,
                "assistant": public_assistant(), "errors": ASSISTANT_ERRORS,
                "client": bool(CLIENT_KEY and CLIENT_URL), "model": bool(KI_URL),
                "conversations": list_convs(person)})
        m = re.match(r"^/api/conv/([a-z0-9]{12})$", path)
        if m:
            conv = load_conv(person, m.group(1))
            return self.json(200, view_of(conv)) if conv else self.json(404, {"error": "Kein solches Gespräch."})
        return self.send(404, "text/plain", "nicht hier")

    def do_POST(self):
        path = self.path.split("?")[0]
        person = self.person()
        if not person:
            return self.json(401, {"error": "Keine angemeldete Person."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n)) if 0 < n <= MAX_BODY else {}
            assert isinstance(body, dict)
        except (ValueError, AssertionError):
            return self.json(400, {"error": "Kein JSON-Objekt."})
        if path == "/api/conv":
            conv = {"id": secrets.token_hex(6), "assistant": ASSISTANT["id"], "created": now(),
                    "messages": []}
            save_conv(person, conv)
            return self.json(200, view_of(conv))
        m = re.match(r"^/api/conv/([a-z0-9]{12})/(send|step|delete)$", path)
        if not m:
            return self.send(404, "text/plain", "nicht hier")
        cid, action = m.groups()
        lock = conv_lock(person, cid)
        if not lock.acquire(blocking=False):
            return self.json(409, {"error": "In diesem Gespräch läuft schon eine Runde."})
        try:
            conv = load_conv(person, cid)
            if conv is None:
                return self.json(404, {"error": "Kein solches Gespräch."})
            if action == "delete":
                os.remove(conv_path(person, cid))
                return self.json(200, {"deleted": cid})
            return self.round(person, conv, action, body)
        finally:
            lock.release()

    def round(self, person, conv, action, body):
        # §5.2: ohne Zusicherung keine Runde -- nie ein Rückfall auf die App selbst.
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": "Keine Zusicherung der Plattform für diese Anfrage "
                                            "(keine Person, oder ein Schlüssel statt einer Sitzung)."})
        if ASSISTANT_ERRORS:
            return self.json(400, {"error": "Der Assistent ist ungültig: "
                                            + " ".join(ASSISTANT_ERRORS)})
        if action == "send":
            text = str(body.get("text") or "").strip()
            if not text or len(text) > MAX_TEXT:
                return self.json(400, {"error": f"Eine Nachricht hat 1 bis {MAX_TEXT} Zeichen."})
            if loop.pending_calls(conv["messages"]):
                return self.json(409, {"error": "Erst den offenen Werkzeugaufruf bestätigen "
                                                "oder ablehnen."})
            conv["messages"].append({"role": "user", "content": text, "at": now()})
            conv.setdefault("title", text[:60])
            save_conv(person, conv)
        decision = body.get("decision") if isinstance(body.get("decision"), dict) else None
        try:
            state = step(person, self.person_name(), assertion, conv, decision)
        except Stop as exc:
            save_conv(person, conv)
            return self.json(200, dict(view_of(conv), state="error", said=exc.text))
        save_conv(person, conv)
        return self.json(200, dict(view_of(conv), state=state))


def main():
    os.makedirs(DATA, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"oaap-chat {VERSION} auf {PORT}; MCP-Client: {'ja' if CLIENT_KEY and CLIENT_URL else 'nein'}; "
          f"Modell: {'ja' if KI_URL else 'nein'}; Assistent "
          f"{ASSISTANT['id']} {'gültig' if not ASSISTANT_ERRORS else 'UNGÜLTIG: ' + ' '.join(ASSISTANT_ERRORS)}",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
