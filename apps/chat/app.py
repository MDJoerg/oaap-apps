"""Chat -- der Plattform-Chat (RFC-0074 Stufen 2 und 3, I-41 Schritt 4).

Assistenten (oaap.ai.assistants 0.1) mit den Werkzeugen je einer Sicht des
MCP-Gateways, ein Modell über das KI-Gateway, der Verlauf als Datei.

    Browser --Sitzung--> Chat               (die Plattform gibt die Zusicherung an DIESE Instanz)
    Chat --via-Tür auf der eigenen Seite--> MCP-Gateway --> App      (als die Person, Verfahren A)
    Chat --Destination ki--> KI-Gateway --> Modell                   (der Schlüssel bleibt auf der Plattform)

- Ein Assistent hat keine Rechte (D1): jeder Werkzeugaufruf läuft als die
  Person, die gerade schreibt; die Sicht begrenzt die Werkzeuge, die App die Daten.
- Wer welchen Assistenten sieht und pflegt, sagt store.py (D4).
- Der Browser treibt die Schleife (D7): jede Runde ist eine eigene Anfrage
  mit frischer Zusicherung. Die App speichert keine Zusicherung (§5.3) und
  hat keinen Rückfall auf einen eigenen Zugang (§5.2).
- Ein Werkzeug, das nicht liest, läuft erst nach Bestätigung, jedes Mal (D6);
  dem Gateway sagt der Chat das mit `X-OAAP-Confirmed: yes` (§5.6).
- `/name …` holt einen Prompt der Sicht (MCP prompts/get) als Nachricht.
- Antworten werden im Browser als Markdown dargestellt, ```mermaid als Diagramm
  (static/markdown.js, Mermaid mitgeliefert, nichts von außen geladen).
- „Einrichtung prüfen“ (0.3.0) nennt, welches Teil fehlt und was der nächste Schritt ist.
- Ansichten von MCP Apps (0.4.0, RFC-0075): bei Assistenten mit `views: true` zeigt
  der Chat die Ansicht eines Tools über den Sandbox-Proxy auf seiner zweiten
  Herkunft (`/ui-sandbox`, Manifest 0.13); Aufrufe aus der Ansicht laufen über
  dieselben Türen, schreibende nur nach Zustimmung im Chat (D4).
- Der Verlauf liegt unter /data, je Person ein Ordner; nur sie liest ihn.

Nur Standardbibliothek.
"""
import json
import os
import re
import secrets
import shlex
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import authz_client
import loop
import store

VERSION = "0.5.1"
PORT = int(os.environ.get("PORT", "8000"))
DATA = os.environ.get("CHAT_DATA_DIR", "/data")
CLIENT_KEY = (os.environ.get("OAAP_MCP_CLIENT_KEY") or "").strip()
CLIENT_URL = (os.environ.get("OAAP_MCP_CLIENT_URL") or "").strip()
KI_URL = (os.environ.get("OAAP_DESTINATION_KI_URL") or "").strip()
SANDBOX_ORIGINS = [o.strip() for o in (os.environ.get("OAAP_UI_SANDBOX_ORIGINS") or "").split(",")
                   if o.strip()]
OWN_ORIGINS = [o.strip() for o in (os.environ.get("OAAP_INSTANCE_NAMES") or "").split(",") if o.strip()]
# Hinter einem Edge nennt die Plattform die Namen mit http (so bedient sie der Knoten);
# der Browser sieht sie mit https. Erlaubte Eltern der Sandbox sind beide Formen.
OWN_ORIGINS = list(dict.fromkeys(OWN_ORIGINS + ["https://" + o[7:] for o in OWN_ORIGINS
                                                if o.startswith("http://")]))
UI_EXTENSION = "io.modelcontextprotocol/ui"
UI_MIME = "text/html;profile=mcp-app"
MODEL_TIMEOUT = int(os.environ.get("CHAT_MODEL_TIMEOUT", "240"))
ASSERTION = "X-OAAP-User-Assertion"
ACT_ASSERTION = "X-OAAP-Act-Assertion"
MCP_VERSION = "2025-11-25"
MAX_BODY = 64_000
MAX_TEXT = 4_000
PERSON = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
CONV = re.compile(r"^[a-z0-9]{12}$")
COMMAND = re.compile(r"^/([A-Za-z0-9_.-]{1,64})(?:\s+(.*))?$", re.S)
WEEKDAYS = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

# Was ein Assistent aus der Konfiguration erbt, wenn CHAT_ASSISTANT es nicht nennt.
DEFAULTS = {
    "id": "assistent", "title": "Assistent",
    "greeting": "Hallo! Ich kann für dich in den angebundenen Anwendungen nachsehen.",
    "system": ("Du bist der Assistent eines kleinen Betriebs. Antworte kurz und auf Deutsch. "
               "Daten kennst du nur aus den Werkzeugen -- erfinde nichts. Fehlt für ein "
               "Werkzeug eine Angabe, frag nach, statt sie zu raten."),
    "model": "standard", "visibility": {"scope": "tenant"}, "owner": "tenant", "commands": False,
}


def configured_assistant():
    """(Assistent, Fehler) aus CHAT_ASSISTANT -- oder None, wenn nichts gesetzt ist."""
    raw = (os.environ.get("CHAT_ASSISTANT") or "").strip()
    if not raw:
        return None
    try:
        a = dict(DEFAULTS, **json.loads(raw))
    except (ValueError, TypeError):
        return dict(DEFAULTS), ["CHAT_ASSISTANT ist kein JSON-Objekt."]
    return a, store.problems(a)


CONFIGURED = configured_assistant()
SETUP_ERRORS = list(CONFIGURED[1]) if CONFIGURED else []
try:
    MODEL_EXTRA = json.loads(os.environ.get("CHAT_MODEL_EXTRA") or "{}")
    assert isinstance(MODEL_EXTRA, dict)
except (ValueError, AssertionError):
    MODEL_EXTRA = {}
    SETUP_ERRORS.append("CHAT_MODEL_EXTRA ist kein JSON-Objekt.")
STORE = store.Store(os.path.join(DATA, "assistants.json"), CONFIGURED)
AUTHZ = authz_client.Authz(app=None)


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
                            "assistant": c.get("assistant"), "updated": c.get("updated", "")})
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------- MCP über die via-Tür

_MCP, _MCP_GUARD = {}, threading.Lock()     # (Person, Gateway, Sicht) -> Sitzungs-ID, nie eine Zusicherung


def gateway_for(name):
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
    gws = doc["gateways"]
    hit = [g for g in gws if g.get("name") == name] if name else (gws if len(gws) == 1 else [])
    if not hit:
        raise Stop(f"Gateway {name or '(keins genannt)'} ist nicht in der Liste des Mandanten: "
                   + (", ".join(g.get("name", "?") for g in gws) or "die Liste ist leer") + ".")
    return hit[0]


def mcp_post(url, msg, session, assertion, assistant_id, confirmed=False):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion,
               "X-OAAP-Assistant": assistant_id}
    if confirmed:
        headers["X-OAAP-Confirmed"] = "yes"
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
    """Die Sicht eines Assistenten, als die Person dieser Anfrage."""

    def __init__(self, person, assertion, assistant):
        self.views = bool(assistant.get("views"))
        gw = gateway_for(assistant.get("gateway") or "")
        self.url = f'{gw["via"]}/{assistant["view"]}'
        self.key = (person, gw["name"], assistant["view"])
        self.assertion = assertion
        self.aid = assistant["id"]

    def _open(self):
        status, sess, msgs = mcp_post(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION,
                       "capabilities": ({"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME]}}}
                                        if self.views else {}),
                       "clientInfo": {"name": "oaap-chat", "version": VERSION}}},
            "", self.assertion, self.aid)
        if status == 401:
            raise Stop("Die Plattform hat dich am MCP-Gateway nicht erkannt (401). Lade die "
                       "Seite neu und versuche es noch einmal.", 401)
        if status != 200 or not sess:
            # Ab Gateway 0.7.3 nennt die 403 den Grund (Profil, Zugang, Ampel) -- weitergeben.
            said = next((str((m.get("error") or {}).get("message") or "") for m in msgs
                         if isinstance(m, dict) and m.get("error")), "")
            raise Stop(f"Das MCP-Gateway hat keine Sitzung eröffnet ({status})"
                       + (f": {said}" if said else "."))
        mcp_post(self.url, {"jsonrpc": "2.0", "method": "notifications/initialized"}, sess,
                 self.assertion, self.aid)
        with _MCP_GUARD:
            _MCP[self.key] = sess
        return sess

    def rpc(self, method, params, confirmed=False):
        with _MCP_GUARD:
            sess = _MCP.get(self.key)
        for attempt in (0, 1):
            sess = sess or self._open()
            status, _, msgs = mcp_post(self.url, {"jsonrpc": "2.0", "id": 2, "method": method,
                                                  "params": params}, sess, self.assertion,
                                       self.aid, confirmed)
            if status in (400, 404) and attempt == 0:     # Sitzung unbekannt oder abgelaufen
                sess = None
                continue
            if status == 401:
                raise Stop("Die Plattform hat dich am MCP-Gateway nicht erkannt (401).", 401)
            if status != 200:
                raise Stop(f"Das MCP-Gateway antwortete {status} auf {method}.")
            return msgs
        raise Stop("Das MCP-Gateway hat die Sitzung zweimal nicht angenommen.")

    def result(self, method, field):
        for m in self.rpc(method, {}):
            if isinstance(m.get("result"), dict):
                return m["result"].get(field) or []
            if "error" in m:
                raise Stop(f"Das MCP-Gateway lehnte {method} ab: "
                           + str((m["error"] or {}).get("message", "")))
        raise Stop(f"Das MCP-Gateway hat auf {method} nicht geantwortet.")

    def tools(self):
        return self.result("tools/list", "tools")

    def prompts(self):
        return self.result("prompts/list", "prompts")

    def call(self, name, args, confirmed=False):
        return loop.result_text(self.rpc("tools/call", {"name": name, "arguments": args},
                                         confirmed))

    def call_raw(self, name, args, confirmed=False):
        """(Text für das Modell, CallToolResult für eine Ansicht)."""
        msgs = self.rpc("tools/call", {"name": name, "arguments": args}, confirmed)
        return loop.result_text(msgs), loop.raw_result(msgs)

    def read(self, uri):
        """resources/read als die Person -- das Gateway liefert nur Freigegebenes."""
        for m in self.rpc("resources/read", {"uri": uri}):
            if isinstance(m.get("result"), dict):
                return m["result"]
            if "error" in m:
                raise Stop(f"{uri}: " + str((m["error"] or {}).get("message", "abgelehnt")), 403)
        raise Stop(f"{uri}: keine Antwort.")

    def prompt(self, name, args):
        for m in self.rpc("prompts/get", {"name": name, "arguments": args}):
            if isinstance(m.get("result"), dict):
                return m["result"].get("messages") or []
            if "error" in m:
                raise Stop(f"/{name}: " + str((m["error"] or {}).get("message", "abgelehnt")))
        raise Stop(f"/{name}: keine Antwort.")


# --------------------------------------------------------------- das Modell

def ask_model(assistant, person_name, msgs, tools):
    if not KI_URL:
        raise Stop("Keine Destination ki gebunden -- der Chat erreicht kein Modell.")
    d = datetime.now()
    today = WEEKDAYS[d.weekday()] + d.strftime(", %d.%m.%Y")
    system = (assistant["system"] + f"\n\nHeute ist {today}. Du sprichst mit {person_name}. "
              "Werkzeuge, die etwas ändern, laufen erst, wenn die Person zustimmt.")
    body = dict(MODEL_EXTRA, model=assistant["model"], stream=False,
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

def command_args(prompt, rest):
    """`/name …` -> Argumente des Prompts: `a=1 b="zwei Worte"`, sonst der Rest für das eine."""
    names = [a.get("name") for a in prompt.get("arguments") or [] if isinstance(a, dict)]
    rest = (rest or "").strip()
    if not rest:
        return {}
    try:
        words = shlex.split(rest)
    except ValueError:
        words = rest.split()
    if all("=" in w for w in words):
        return dict(w.split("=", 1) for w in words)
    if len(names) == 1:
        return {names[0]: rest}
    return dict(zip(names, words))


def run_command(way, assistant, msgs, text):
    """Ein Prompt der Sicht wird zu Nachrichten im Gespräch (MCP prompts/get, lesend)."""
    m = COMMAND.match(text)
    if not assistant.get("commands"):
        raise Stop("Dieser Assistent hat keine /Befehle.", 400)
    prompts = {p.get("name"): p for p in way.prompts() if isinstance(p, dict)}
    if m.group(1) not in prompts:
        raise Stop(f"/{m.group(1)} gibt es in dieser Sicht nicht. Vorhanden: "
                   + (", ".join("/" + n for n in sorted(prompts)) or "keine") + ".", 400)
    p = prompts[m.group(1)]
    args = command_args(p, m.group(2))
    missing = [a["name"] for a in p.get("arguments") or []
               if isinstance(a, dict) and a.get("required") and a.get("name") not in args]
    if missing:
        raise Stop(f"/{p['name']} braucht noch: " + ", ".join(missing) + ".", 400)
    added = 0
    for pm in way.prompt(p["name"], args):
        content = pm.get("content") if isinstance(pm, dict) else None
        if isinstance(content, dict) and content.get("type") == "text" \
                and pm.get("role") in ("user", "assistant"):
            msgs.append({"role": pm["role"], "content": content.get("text", "")[:MAX_TEXT * 4],
                         "command": p["name"], "at": now()})
            added += 1
    if not added:
        raise Stop(f"/{p['name']} lieferte keinen Text.")


def step(person, person_name, assertion, assistant, conv, decision):
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
            way = way or McpWay(person, assertion, assistant)
            if assistant.get("views") and name in conv.get("ui", {}):
                result, raw = way.call_raw(name, args, confirmed=eff != "read")
                note = {"view": conv["ui"][name]["resourceUri"], "raw": raw, "arguments": args}
            else:
                result, note = way.call(name, args, confirmed=eff != "read"), {}
            if eff != "read":
                note["confirmed"] = True
        msgs.append(dict({"role": "tool", "tool_call_id": c["id"], "name": name,
                          "content": result, "at": now()}, **note))
        conv.pop("confirm", None)
    if loop.model_turns_since_user(msgs) >= loop.MAX_MODEL_TURNS:
        msgs.append({"role": "assistant", "at": now(), "content":
                     f"Ich habe nach {loop.MAX_MODEL_TURNS} Runden aufgehört, ohne fertig zu "
                     "werden. Formuliere die Frage bitte enger."})
        return "done"
    way = way or McpWay(person, assertion, assistant)
    listed = way.tools()
    tools, effects_now = loop.tools_for_model(listed)
    effects.clear()
    effects.update(effects_now)
    conv["ui"] = loop.ui_map(listed) if assistant.get("views") else {}
    reply = ask_model(assistant, person_name, msgs, tools)
    reply["at"] = now()
    msgs.append(reply)
    return "more" if reply.get("tool_calls") else "done"


# -------------------------------------------------------------------- Seite

PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Nur diese Dateien, nie ein Pfad aus der Anfrage (kein Weg aus dem Ordner hinaus).
STATIC = {"markdown.js": "application/javascript; charset=utf-8",
          "mermaid.min.js": "application/javascript; charset=utf-8"}


def own_instance_guess():
    """Der Name dieser Instanz, wie ihn ein Zugangsprofil als @<name> braucht -- geraten.

    Die Plattform nennt der App ihre Adressen (OAAP_INSTANCE_NAMES), nicht ihren
    Namen. Die automatische Adresse ist <app>.<mandant>.<knoten> (im Mandanten
    default <app>.<knoten>); daraus <mandant>-<app>. Nur für einen Hinweis."""
    names = [n.strip() for n in (os.environ.get("OAAP_INSTANCE_NAMES") or "").split(",") if n.strip()]
    for origin in reversed(names):            # die automatischen Adressen stehen hinten
        host = origin.split("://", 1)[-1].split("/")[0].split(":")[0]
        labels = host.split(".")
        if len(labels) >= 4:
            return f"{labels[1]}-{labels[0]}"
    return ""


def setup_report(person, assertion, a):
    """Jedes Teil, das dieser Assistent braucht, der Reihe nach -- mit dem nächsten Schritt.

    Prüft echt (eine Runde als die Person, eine Frage an das KI-Gateway), damit der
    Bericht sagt, was IST, nicht was eingestellt sein sollte."""
    items = []

    def add(key, ok, title, detail="", next_=""):
        items.append({"key": key, "ok": ok, "title": title, "detail": detail, "next": next_})
        return ok

    if not add("client", bool(CLIENT_KEY and CLIENT_URL), "Der Chat darf als MCP-Client handeln",
               "" if CLIENT_KEY and CLIENT_URL else "Die Plattform hat dieser Instanz keinen Client-Schlüssel gegeben.",
               "" if CLIENT_KEY and CLIENT_URL else "Das Manifest erklärt mcp_client; eine Generalprobe bekommt keinen. "
               "Instanz neu ausrollen oder die Plattform aktualisieren (ab 0.1.243)."):
        return items
    try:
        gw = gateway_for(a.get("gateway") or "")
        add("gateway", True, f"Gateway {gw['name']} ist im Mandanten erreichbar")
    except Stop as exc:
        add("gateway", False, "Ein MCP-Gateway im Mandanten", exc.text,
            "Ein MCP-Gateway aus dem Store in diesem Mandanten installieren; nennt der Mandant "
            "mehrere, im Assistenten das Feld gateway setzen.")
        gw = None
    if gw and assertion:
        group = own_instance_guess()
        try:
            n = len(McpWay(person, assertion, a).tools())
            add("view", True, f"Sicht {a['view']} antwortet dir: {n} Werkzeug(e)")
            if not n:
                add("tools", False, "Werkzeuge in der Sicht",
                    "Die Sicht ist leer, oder ihre Werkzeuge liegen über Ampel oder Wirkungsklasse des Profils.",
                    "Auf der Betreiberseite des Gateways: Quelle → „Katalog lesen“, Werkzeuge in die Sicht "
                    "nehmen; die Ampel der Quelle darf nicht über der des Zugangsprofils liegen.")
        except Stop as exc:
            if "Ampel" in exc.text:
                hint = ("Auf der Betreiberseite des Gateways: die Ampel der Quelle senken (Server → "
                        "Ampel) oder beim Zugangsprofil „Ändern“ → Ampel höher.")
            elif "Zugang" in exc.text and "kein Zugangsprofil" not in exc.text:
                hint = (f"Auf der Betreiberseite des Gateways bei der Sicht {a['view']} den Zugang "
                        "„Plattform“ einschalten.")
            elif "403" in exc.text:
                hint = ("Auf der Betreiberseite des Gateways ein Zugangsprofil anlegen oder ändern: Gruppe "
                        + (f"@{group}" if group else "@<Name dieser Instanz>")
                        + (" (vermutet aus der Adresse)" if group else "") + f", Sicht {a['view']}. "
                        f"Die Sicht {a['view']} braucht den Zugang „Plattform“.")
            elif "401" in exc.text:
                hint = "Seite neu laden (die Anmeldung ist älter als fünf Minuten)."
            else:
                hint = f"Gibt es die Sicht {a['view']} im Gateway {gw['name']}?"
            add("view", False, f"Sicht {a['view']} als du", exc.text, hint)
    elif gw:
        add("view", False, f"Sicht {a['view']} als du", "Keine Zusicherung der Plattform in dieser Anfrage.",
            "Über die Plattform angemeldet öffnen, nicht mit einem Schlüssel.")
    if not add("destination", bool(KI_URL), "Ein Modell ist angebunden (Destination ki)",
               "" if KI_URL else "Dieser Instanz ist keine Destination ki zugeordnet.",
               "" if KI_URL else "Im Portal: Instanz → Destinationen → beim Bedarf „ki“ eine Destination des "
               "Mandanten zuordnen. Hat der Mandant keine, legt der Betreiber des Knotens eine an (Ziel: ein "
               "KI-Gateway, mit einem Schlüssel nur für den Modellnamen -- Portal → Destinationen, ab Plattform 0.1.244; sonst oaap destination add)."):
        return items
    req = urllib.request.Request(KI_URL.rstrip("/") + "/models", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ids = [m.get("id") for m in (json.loads(r.read(1_000_000)).get("data") or []) if isinstance(m, dict)]
        add("model", a["model"] in ids, f"Modellname {a['model']} ist am KI-Gateway freigegeben",
            "" if a["model"] in ids else "Der Schlüssel der Destination erreicht: " + (", ".join(ids) or "nichts") + ".",
            "" if a["model"] in ids else f"Auf der Seite des KI-Gateways den Modellnamen {a['model']} anlegen "
            "und dem Schlüssel der Destination erlauben -- oder im Assistenten einen vorhandenen Namen wählen.")
    except urllib.error.HTTPError as exc:
        add("model", False, "Das KI-Gateway antwortet", f"Antwort {exc.code}.",
            "Der Schlüssel der Destination ist ungültig oder widerrufen: neuen Schlüssel ausstellen und "
            "bei der Destination hinterlegen." if exc.code in (401, 403) else "Läuft das KI-Gateway?")
    except (OSError, ValueError) as exc:
        add("model", False, "Das KI-Gateway antwortet", f"Nicht erreicht ({type(exc).__name__}).",
            "Läuft das KI-Gateway, und stimmt das Ziel der Destination?")
    return items
PUBLIC = ("id", "title", "greeting", "model", "view", "gateway", "visibility", "commands")


def public(a, origin, who):
    return dict({k: a.get(k) for k in PUBLIC + ("views",)}, origin=origin,
                manage=origin == "chat" and store.can_manage(a, who))


def view_of(conv):
    """Was der Browser zeigt -- ohne eigene Vermerke, die er nicht braucht."""
    shown = []
    for m in conv["messages"]:
        x = {k: m[k] for k in ("role", "content", "name", "confirmed", "seconds", "at", "command",
                               "tool_call_id") if k in m}
        if m.get("view"):
            x["view"] = m["view"]
        if m.get("tool_calls"):
            x["calls"] = [{"name": c["function"]["name"],
                           "arguments": loop.call_args(c)[0]} for c in m["tool_calls"]]
        shown.append(x)
    return {"id": conv["id"], "title": conv.get("title") or "Neues Gespräch",
            "assistant": conv.get("assistant"), "messages": shown, "confirm": conv.get("confirm")}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Eine Zeile je Anfrage, nie Kopfzeilen (darin stünde die Zusicherung).
        sys.stderr.write("%s %s %s\n" % (self.command, self.path.split("?")[0], args[1]
                                         if len(args) > 1 else ""))

    def send(self, status, ctype, body, extra=(), cache="no-store"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def json(self, status, doc, extra=()):
        self.send(status, "application/json; charset=utf-8", json.dumps(doc, ensure_ascii=False),
                  extra)

    def person(self):
        pid = self.headers.get("X-OAAP-User-Id", "").strip()
        return pid if PERSON.match(pid) else ""

    def who(self):
        uid = self.person()
        roles = {r.strip() for r in self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()}
        return store.Who(uid, roles, AUTHZ.grants(uid) if uid else None)

    def person_name(self):
        return (self.headers.get("X-OAAP-Display-Name") or self.headers.get("X-OAAP-User")
                or "der angemeldeten Person").strip()[:80]

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if 0 < n <= MAX_BODY else {}
        if not isinstance(body, dict):
            raise ValueError
        return body

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/healthz":
            return self.send(200, "text/plain", "ok")
        if path == "/":
            with open(PAGE_FILE, encoding="utf-8") as f:
                return self.send(200, "text/html; charset=utf-8", f.read())
        if path == "/ui-sandbox":
            # Der Sandbox-Proxy (RFC-0075 D6) -- auf der zweiten Herkunft ohne Person.
            # Er spricht nur mit den eigenen Adressen dieser Instanz.
            with open(os.path.join(STATIC_DIR, "sandbox.html"), encoding="utf-8") as f:
                page = f.read().replace("__HOSTS__", json.dumps(OWN_ORIGINS))
            return self.send(200, "text/html; charset=utf-8", page)
        if path.startswith("/static/") and path[8:] in STATIC:
            with open(os.path.join(STATIC_DIR, path[8:]), "rb") as f:
                return self.send(200, STATIC[path[8:]], f.read(), cache="private, max-age=3600")
        person = self.person()
        if not person:
            return self.json(401, {"error": "Keine angemeldete Person."})
        who = self.who()
        if path == "/api/state":
            return self.json(200, {
                "user": self.headers.get("X-OAAP-User", ""), "version": VERSION,
                "assistants": [public(a, o, who) for a, o in STORE.visible(who)],
                "errors": SETUP_ERRORS, "tenant_admin": "tenant_admin" in who.roles,
                "teams_known": who.grants is not None,
                "client": bool(CLIENT_KEY and CLIENT_URL), "model": bool(KI_URL),
                "sandbox_origins": SANDBOX_ORIGINS,
                "conversations": list_convs(person)})
        if path == "/api/setup":
            aid = (self.path.split("assistant=", 1)[1].split("&")[0] if "assistant=" in self.path else "")
            a, _ = STORE.get(aid)
            if a is None or not store.can_see(a, who):
                return self.json(404, {"error": "Kein solcher Assistent."})
            errs = store.problems(a)
            if errs:
                return self.json(200, {"assistant": aid, "items": [
                    {"key": "assistant", "ok": False, "title": "Der Assistent ist gültig",
                     "detail": " ".join(errs), "next": "Den Assistenten unter „Assistenten …“ korrigieren."}]})
            return self.json(200, {"assistant": aid, "items": setup_report(
                person, self.headers.get(ASSERTION, "").strip(), a)})
        if path == "/api/assistants/export":
            return self.json(200, STORE.export(who),
                             [("Content-Disposition", 'attachment; filename="assistenten.json"')])
        m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})$", path)
        if m:
            a, origin = STORE.get(m.group(1))
            if a is None or not store.can_see(a, who):
                return self.json(404, {"error": "Kein solcher Assistent."})
            return self.json(200, dict(a, origin=origin, manage=origin == "chat"
                                       and store.can_manage(a, who)))
        m = re.match(r"^/api/conv/([a-z0-9]{12})(/commands|/view)?$", path)
        if m:
            conv = load_conv(person, m.group(1))
            if not conv:
                return self.json(404, {"error": "Kein solches Gespräch."})
            if not m.group(2):
                return self.json(200, view_of(conv))
            if m.group(2) == "/view":
                return self.view_doc(person, who, conv)
            return self.commands(person, who, conv)
        return self.send(404, "text/plain", "nicht hier")

    def commands(self, person, who, conv):
        a = self.assistant_for(conv, who)
        if isinstance(a, str):
            return self.json(403, {"error": a})
        if not a.get("commands"):
            return self.json(200, {"commands": []})
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": "Keine Zusicherung der Plattform für diese Anfrage."})
        try:
            ps = McpWay(person, assertion, a).prompts()
        except Stop as exc:
            return self.json(200, {"commands": [], "said": exc.text})
        return self.json(200, {"commands": [
            {"name": p.get("name"), "description": p.get("description", ""),
             "arguments": [{"name": x.get("name"), "required": bool(x.get("required"))}
                           for x in p.get("arguments") or [] if isinstance(x, dict)]}
            for p in ps if isinstance(p, dict)]})

    # ---- Ansichten (MCP Apps, RFC-0075)
    def view_doc(self, person, who, conv):
        """Die Ansicht zu einem Werkzeugergebnis: HTML (vom Gateway, festgehalten), Aufruf, Ergebnis."""
        a = self.assistant_for(conv, who)
        if isinstance(a, str):
            return self.json(403, {"error": a})
        if not a.get("views"):
            return self.json(403, {"error": "Dieser Assistent zeigt keine Ansichten."})
        call = (self.path.split("call=", 1)[1].split("&")[0] if "call=" in self.path else "")
        msg = next((x for x in conv["messages"] if x.get("role") == "tool"
                    and x.get("tool_call_id") == call and x.get("view")), None)
        if msg is None:
            return self.json(404, {"error": "Zu diesem Aufruf gibt es keine Ansicht."})
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": "Keine Zusicherung der Plattform für diese Anfrage."})
        try:
            res = McpWay(person, assertion, a).read(msg["view"])
        except Stop as exc:
            return self.json(exc.status if exc.status in (400, 401, 403) else 502, {"error": exc.text})
        c = next((x for x in res.get("contents") or [] if isinstance(x, dict)), {})
        if (c.get("mimeType") or "").replace(" ", "") != UI_MIME or not isinstance(c.get("text"), str):
            return self.json(502, {"error": "Die Ansicht hat keine Form, die der Chat zeigen kann."})
        meta = (c.get("_meta") or {}).get("ui") if isinstance(c.get("_meta"), dict) else {}
        return self.json(200, {
            "html": c["text"], "uri": msg["view"], "tool": {"name": msg.get("name")},
            "arguments": msg.get("arguments") or {}, "result": msg.get("raw") or {},
            # Stufe 2: keine fremden Domains, egal was die Ansicht erklärt (RFC-0075 §2.3).
            "csp": {}, "permissions": {}, "prefersBorder": bool((meta or {}).get("prefersBorder")),
            "sandbox": next((o for o in SANDBOX_ORIGINS), "")})

    def view_call(self, person, who, conv, body):
        """tools/call oder resources/read aus einer Ansicht -- als die Person, durch dieselbe Tür.

        Nur Tools der Sicht, deren visibility "app" enthält; was nicht liest, nur mit
        `confirmed: true` -- die Karte im Chat fragt, nicht die Ansicht (D4)."""
        a = self.assistant_for(conv, who)
        if isinstance(a, str):
            return self.json(403, {"error": a})
        if not a.get("views"):
            return self.json(403, {"error": "Dieser Assistent zeigt keine Ansichten."})
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": "Keine Zusicherung der Plattform für diese Anfrage."})
        try:
            way = McpWay(person, assertion, a)
            if body.get("method") == "resources/read":
                return self.json(200, {"result": way.read(str(body.get("uri") or ""))})
            name = str(body.get("name") or "")
            args = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
            listed = {t.get("name"): t for t in way.tools() if isinstance(t, dict)}
            if name not in listed:
                return self.json(403, {"error": f"Ein Werkzeug {name} gibt es in dieser Sicht nicht."})
            ui = loop.tool_ui(listed[name])
            if ui is not None and "app" not in ui["visibility"]:
                return self.json(403, {"error": f"{name} ist nicht für Ansichten freigegeben."})
            eff = loop.effect_of(listed[name])
            if eff != "read" and body.get("confirmed") is not True:
                return self.json(409, {"error": "Zustimmung fehlt.", "confirm": {
                    "name": name, "effect": eff, "arguments": args}})
            _text, raw = way.call_raw(name, args, confirmed=eff != "read")
            return self.json(200, {"result": raw})
        except Stop as exc:
            return self.json(exc.status if exc.status in (400, 401, 403) else 502, {"error": exc.text})

    def assistant_for(self, conv, who):
        """Der Assistent des Gesprächs -- oder ein Satz, warum nicht (mehr)."""
        a, _ = STORE.get(conv.get("assistant") or "")
        if a is None:
            return "Den Assistenten dieses Gesprächs gibt es nicht mehr."
        if not store.can_see(a, who):
            return "Diesen Assistenten darfst du nicht (mehr) benutzen."
        errs = store.problems(a)
        return ("Der Assistent ist ungültig: " + " ".join(errs)) if errs else a

    def do_POST(self):
        path = self.path.split("?")[0]
        person = self.person()
        if not person:
            return self.json(401, {"error": "Keine angemeldete Person."})
        try:
            body = self.body()
        except ValueError:
            return self.json(400, {"error": "Kein JSON-Objekt."})
        who = self.who()
        if path == "/api/assistants":
            errs = STORE.save(body, who)
            return self.json(400 if errs else 200, {"errors": errs} if errs else {"saved": body.get("id")})
        if path == "/api/assistants/import":
            errs = STORE.import_document(body, who)
            return self.json(400 if errs else 200, {"errors": errs} if errs else
                             {"imported": len(body.get("assistants") or [])})
        m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})/delete$", path)
        if m:
            errs = STORE.delete(m.group(1), who)
            return self.json(400 if errs else 200, {"errors": errs} if errs else {"deleted": m.group(1)})
        if path == "/api/conv":
            a, _ = STORE.get(str(body.get("assistant") or ""))
            if a is None or not store.can_see(a, who):
                return self.json(404, {"error": "Diesen Assistenten gibt es für dich nicht."})
            conv = {"id": secrets.token_hex(6), "assistant": a["id"], "created": now(),
                    "messages": []}
            save_conv(person, conv)
            return self.json(200, view_of(conv))
        m = re.match(r"^/api/conv/([a-z0-9]{12})/view-call$", path)
        if m:
            conv = load_conv(person, m.group(1))
            if conv is None:
                return self.json(404, {"error": "Kein solches Gespräch."})
            return self.view_call(person, who, conv, body)
        m = re.match(r"^/api/conv/([a-z0-9]{12})/(send|step|delete|rename)$", path)
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
            if action == "rename":
                title = " ".join(str(body.get("title") or "").split())[:80]
                if not title:
                    return self.json(400, {"error": "Ein Titel hat 1 bis 80 Zeichen."})
                conv["title"] = title
                save_conv(person, conv)
                return self.json(200, view_of(conv))
            return self.round(person, who, conv, action, body)
        finally:
            lock.release()

    def round(self, person, who, conv, action, body):
        # §5.2: ohne Zusicherung keine Runde -- nie ein Rückfall auf die App selbst.
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": "Keine Zusicherung der Plattform für diese Anfrage "
                                            "(keine Person, oder ein Schlüssel statt einer Sitzung)."})
        a = self.assistant_for(conv, who)
        if isinstance(a, str):
            return self.json(403, {"error": a})
        if "CHAT_MODEL_EXTRA ist kein JSON-Objekt." in SETUP_ERRORS:
            return self.json(400, {"error": "CHAT_MODEL_EXTRA ist kein JSON-Objekt."})
        try:
            if action == "send":
                text = str(body.get("text") or "").strip()
                if not text or len(text) > MAX_TEXT:
                    return self.json(400, {"error": f"Eine Nachricht hat 1 bis {MAX_TEXT} Zeichen."})
                if loop.pending_calls(conv["messages"]):
                    return self.json(409, {"error": "Erst den offenen Werkzeugaufruf bestätigen "
                                                    "oder ablehnen."})
                if COMMAND.match(text):
                    run_command(McpWay(person, assertion, a), a, conv["messages"], text)
                else:
                    conv["messages"].append({"role": "user", "content": text, "at": now()})
                conv.setdefault("title", text[:60])
                save_conv(person, conv)
            decision = body.get("decision") if isinstance(body.get("decision"), dict) else None
            state = step(person, self.person_name(), assertion, a, conv, decision)
        except Stop as exc:
            save_conv(person, conv)
            if exc.status == 400:
                return self.json(400, {"error": exc.text})
            return self.json(200, dict(view_of(conv), state="error", said=exc.text))
        save_conv(person, conv)
        return self.json(200, dict(view_of(conv), state=state))


def main():
    os.makedirs(DATA, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"oaap-chat {VERSION} auf {PORT}; MCP-Client: {'ja' if CLIENT_KEY and CLIENT_URL else 'nein'}; "
          f"Modell: {'ja' if KI_URL else 'nein'}; Berechtigungen: "
          f"{'ja' if AUTHZ.url else 'nein (Team-Assistenten unsichtbar)'}; Assistenten "
          f"{len(STORE.all())}" + (f"; FEHLER: {' '.join(SETUP_ERRORS)}" if SETUP_ERRORS else ""),
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
