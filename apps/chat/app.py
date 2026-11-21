"""Chat -- der Plattform-Chat (RFC-0074 Stufen 2 und 3, I-41 Schritt 4).

Assistenten (oaap.ai.assistants 0.1) mit den Werkzeugen je einer Sicht des
MCP-Gateways, ein Modell über das KI-Gateway, der Verlauf als Datei.

    Browser --Sitzung--> Chat               (die Plattform gibt die Zusicherung an DIESE Instanz)
    Chat --via-Tür auf der eigenen Seite--> MCP-Gateway --> App      (als die Person, Verfahren A)
    Chat --Destination ki--> KI-Gateway --> Modell                   (der Schlüssel bleibt auf der Plattform)
    Chat --via-Tür /platform/ai/via/<gw>/--> KI-Gateway --> Modell  (0.16.0, RFC-0104 §4: ohne Destination,
                                                                     Client-Schlüssel + Zusicherung der Person)

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
from urllib.parse import unquote
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import authz_client
import loop
import store

VERSION = "0.20.0"
PORT = int(os.environ.get("PORT", "8000"))
DATA = os.environ.get("CHAT_DATA_DIR", "/data")
CLIENT_KEY = (os.environ.get("OAAP_MCP_CLIENT_KEY") or "").strip()
CLIENT_URL = (os.environ.get("OAAP_MCP_CLIENT_URL") or "").strip()
KI_URL = (os.environ.get("OAAP_DESTINATION_KI_URL") or "").strip()
# 0.16.0 (RFC-0104 §4, Manifest 0.18 `ai_client`): ohne Destination ki erreicht der Chat
# das KI-Gateway seines Mandanten über eine via-Tür auf der eigenen Seite -- wie das
# MCP-Gateway (Verfahren A): Client-Schlüssel und Zusicherung der Person, beide nötig.
AI_CLIENT_KEY = (os.environ.get("OAAP_AI_CLIENT_KEY") or "").strip()
AI_CLIENT_URL = (os.environ.get("OAAP_AI_CLIENT_URL") or "").strip()
AI_GATEWAY = (os.environ.get("CHAT_AI_GATEWAY") or "").strip()
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
NO_ASSERTION = ("Keine Zusicherung der Plattform für diese Anfrage. Entweder kommt sie "
                "nicht aus einer Anmeldung (ein Schlüssel statt einer Sitzung), oder dem "
                "Knoten fehlt sein Zusicherungsschlüssel -- das sieht der Betreiber mit "
                "'oaap status' und behebt es mit 'oaap update'.")
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
# 0.8.0 (RFC-0101 §2, W1): die Ampel des Gesprächs. Die Person sagt, was sie eingeben
# wird; der Chat nimmt nur Modelle und Sichten bis zu dieser Farbe und schickt sie als
# X-OAAP-Light an beide Gateways, die dann ebenfalls ablehnen. Sie verschärft die
# Decke des Schlüssels, lockert sie nie -- ein Schutz vor Versehen, keine Kontrolle.
LIGHTS = ("green", "yellow", "red")
LIGHT_RANK = {x: i for i, x in enumerate(LIGHTS)}
LIGHT_HEADER = "X-OAAP-Light"
_DL = (os.environ.get("CHAT_DEFAULT_LIGHT") or "").strip().lower()
# D5: grün ist die Vorgabe; der Mandant darf gelb vorgeben (Demo, Schulung), rot nie.
DEFAULT_LIGHT = _DL if _DL in ("green", "yellow") else "green"


def templates_url():
    """0.19.0 (RFC-0117 §3.5): die Seite „Assistenten“ des Portals (aus OAAP_PORTAL_URL,
    runtime 2.38) -- dort legt der tenant_admin Assistenten aus Vorlagen an. '' ohne."""
    from urllib.parse import urlsplit
    u = urlsplit((os.environ.get("OAAP_PORTAL_URL") or "").strip())
    return f"{u.scheme}://{u.netloc}/assistenten" if u.scheme in ("https", "http") and u.netloc else ""
if _DL and _DL not in ("green", "yellow"):
    SETUP_ERRORS.append(f"CHAT_DEFAULT_LIGHT kennt nur green oder yellow, nicht „{_DL[:20]}“ "
                        "(rot ist nie die Vorgabe) -- es gilt grün.")
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
                            "assistant": c.get("assistant"), "updated": c.get("updated", ""),
                            "created": c.get("created", "")})
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


def mcp_post(url, msg, session, assertion, assistant_id, confirmed=False, light=""):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream",
               "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion,
               "X-OAAP-Assistant": assistant_id}
    if light:
        headers[LIGHT_HEADER] = light
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

    def __init__(self, person, assertion, assistant, light=""):
        if not assistant.get("view"):
            # 0.18.0 (RFC-0116 D9): ein Satz statt eines KeyError, an jeder Tür
            raise Stop("Dieser Assistent hat keine Sicht und darum keine Werkzeuge, "
                       "Befehle oder Ansichten.", 400)
        self.light = light if light in LIGHT_RANK else ""
        self.views = bool(assistant.get("views"))
        gw = gateway_for(assistant.get("gateway") or "")
        self.url = f'{gw["via"]}/{assistant["view"]}'
        # 0.7.0: ob die Sitzung Ansichten (MCP Apps) angesagt hat, gehört zum Schlüssel --
        # sonst erbte ein Assistent mit Ansichten die Sitzung eines ohne auf derselben Sicht.
        self.key = (person, gw["name"], assistant["view"], self.views)
        self.assertion = assertion
        # 0.9.0 (RFC-0097): die Revision, die spricht, steht in der Messzeile des Gateways.
        self.aid = assistant["id"] + (f'@{assistant["rev"]}' if assistant.get("rev") else "")

    def _open(self):
        status, sess, msgs = mcp_post(self.url, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": MCP_VERSION,
                       "capabilities": ({"extensions": {UI_EXTENSION: {"mimeTypes": [UI_MIME]}}}
                                        if self.views else {}),
                       "clientInfo": {"name": "oaap-chat", "version": VERSION}}},
            "", self.assertion, self.aid, light=self.light)
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
                 self.assertion, self.aid, light=self.light)
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
                                       self.aid, confirmed, light=self.light)
            if status in (400, 404) and attempt == 0:     # Sitzung unbekannt oder abgelaufen
                sess = None
                continue
            if status == 401:
                raise Stop("Die Plattform hat dich am MCP-Gateway nicht erkannt (401).", 401)
            if status != 200:
                # 0.8.0: eine 403 nennt den Grund (z. B. die Ampel des Gesprächs) -- weitergeben.
                said = next((str((m.get("error") or {}).get("message") or "") for m in msgs
                             if isinstance(m, dict) and m.get("error")), "")
                raise Stop(f"Das MCP-Gateway antwortete {status} auf {method}"
                           + (f": {said}" if said else "."))
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

NO_MODEL_WAY = ("Keine Destination ki zugeordnet und kein KI-Gateway über die Plattform erreichbar -- "
                "der Chat erreicht kein Modell. (Portal: Seite der Instanz, Reiter Netz & Adressen, "
                "Destinationen, „Zuordnen“; oder ein KI-Gateway im Mandanten installieren, ab Plattform "
                "mit Manifest 0.18)")
NO_GRANT = "im KI-Gateway fehlt die Freigabe für diesen Chat"


class ModelWay:
    """Wohin die Fragen an das Modell gehen (RFC-0104 §4): `destination` (die Destination ki,
    wie bis 0.15.0) oder `gateway` (die via-Tür eines KI-Gateways des Mandanten)."""

    def __init__(self, kind, base, name, headers=None):
        self.kind, self.base, self.name, self.headers = kind, base.rstrip("/"), name, dict(headers or {})

    @property
    def label(self):
        return ("Destination ki" if self.kind == "destination"
                else f"KI-Gateway {self.name} (via-Tür, ohne Schlüssel)")

    def request(self, path, data=None, headers=None, method=None):
        h = dict(self.headers)
        h.update(headers or {})
        return urllib.request.Request(self.base + path, data=data, headers=h, method=method)

    def refused(self, code):
        """Der Satz zu einer Ablehnung (401/403) -- je nach Weg ein anderer Grund."""
        if self.kind == "destination":
            return "der Schlüssel der Destination ki ist ungültig oder widerrufen"
        if code == 403:
            return NO_GRANT
        return ("die Plattform ließ den Ruf nicht durch: die Anmeldung ist älter als fünf Minuten "
                "(Seite neu laden) oder der Client-Schlüssel der Instanz fehlt")


def ai_gateways():
    """Die KI-Gateways des Mandanten (GET OAAP_AI_CLIENT_URL), oder Stop."""
    req = urllib.request.Request(AI_CLIENT_URL, headers={"Authorization": "Bearer " + AI_CLIENT_KEY,
                                                         "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            doc = json.loads(r.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise Stop(f"Die Plattform antwortete {exc.code} auf die Liste der KI-Gateways.") from None
    except (OSError, ValueError) as exc:
        raise Stop(f"Liste der KI-Gateways nicht gelesen: {type(exc).__name__}") from None
    if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("gateways"), list):
        raise Stop("Die Liste der KI-Gateways hat eine unbekannte Form.")
    return [g for g in doc["gateways"] if isinstance(g, dict) and isinstance(g.get("name"), str)]


def model_way(assertion):
    """Welchen Weg der Chat zum Modell nimmt (RFC-0104 §4), oder Stop mit dem Grund:
    1. eine Destination ki, die der Instanz zugeordnet ist -- wie bisher (eine Wahl von Hand gewinnt);
    2. sonst genau ein KI-Gateway in der Liste des Mandanten -- seine via-Tür;
    3. mehrere -- das nennt CHAT_AI_GATEWAY; ohne den Wert sagt der Chat es."""
    if KI_URL:
        return ModelWay("destination", KI_URL, "ki")
    if not (AI_CLIENT_KEY and AI_CLIENT_URL):
        raise Stop(NO_MODEL_WAY)
    gws = ai_gateways()
    names = ", ".join(g["name"] for g in gws)
    if AI_GATEWAY:
        hit = [g for g in gws if g["name"] == AI_GATEWAY]
        if not hit:
            raise Stop(f"CHAT_AI_GATEWAY nennt das KI-Gateway {AI_GATEWAY}, aber im Mandanten gibt es "
                       + (f"nur: {names}." if gws else "keins."))
    elif len(gws) == 1:
        hit = gws
    elif not gws:
        raise Stop("Keine Destination ki zugeordnet, und im Mandanten ist kein KI-Gateway installiert "
                   "-- der Chat erreicht kein Modell.")
    else:
        raise Stop(f"Im Mandanten gibt es mehrere KI-Gateways ({names}); die Einstellung "
                   "CHAT_AI_GATEWAY der Instanz nennt keins.")
    via = str(hit[0].get("via") or "")
    if not via:
        raise Stop(f"Die Plattform nennt für das KI-Gateway {hit[0]['name']} keine via-Tür.")
    if not assertion:
        raise Stop(NO_ASSERTION, 401)
    return ModelWay("gateway", via, hit[0]["name"],
                    {"Authorization": "Bearer " + AI_CLIENT_KEY, ACT_ASSERTION: assertion})


def model_ready():
    """Hat der Chat überhaupt einen Weg zum Modell (ohne die Liste zu fragen)?"""
    return bool(KI_URL or (AI_CLIENT_KEY and AI_CLIENT_URL))


def ask_model(assistant, person_name, msgs, tools, light="", assertion=""):
    way = model_way(assertion)
    d = datetime.now()
    today = WEEKDAYS[d.weekday()] + d.strftime(", %d.%m.%Y")
    system = (assistant["system"] + f"\n\nHeute ist {today}. Du sprichst mit {person_name}. "
              "Werkzeuge, die etwas ändern, laufen erst, wenn die Person zustimmt.")
    body = dict(MODEL_EXTRA, model=assistant["model"], stream=False,
                messages=[{"role": "system", "content": system}] + loop.for_model(msgs))
    body.setdefault("temperature", 0.2)
    if tools:
        body["tools"] = tools
    headers = {"Content-Type": "application/json"}
    if light in LIGHT_RANK:
        headers[LIGHT_HEADER] = light
    req = way.request("/chat/completions", data=json.dumps(body).encode(), headers=headers, method="POST")
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
        why = f" -- {way.refused(exc.code)}" if way.kind == "gateway" and exc.code in (401, 403) else ""
        raise Stop(f"Das KI-Gateway antwortete {exc.code}: {str(said)[:200]}{why}") from None
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
            way = way or McpWay(person, assertion, assistant, conv_light(conv))
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
    if assistant.get("view"):
        way = way or McpWay(person, assertion, assistant, conv_light(conv))
        listed = way.tools()
    else:
        listed = []          # 0.18.0 (RFC-0116 D9): ohne Sicht keine Werkzeuge, kein Gateway
    tools, effects_now = loop.tools_for_model(listed)
    effects.clear()
    effects.update(effects_now)
    conv["ui"] = loop.ui_map(listed) if assistant.get("views") else {}
    reply = ask_model(assistant, person_name, msgs, tools, conv_light(conv), assertion)
    reply["at"] = now()
    if assistant.get("rev"):
        reply["rev"] = assistant["rev"]       # für Bewertungen (RFC-0101 Stufe 2)
        # 0.10.0: die Umstände dieser Antwort, wie sie damals waren -- ein später geänderter
        # Assistent darf eine alte Bewertung nicht einem anderen Modell zuschreiben.
        reply["model"] = assistant.get("model") or ""
        reply["light"] = conv_light(conv)
    msgs.append(reply)
    return "more" if reply.get("tool_calls") else "done"


# -------------------------------------------------------------------- Seite

PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
# Nur diese Dateien, nie ein Pfad aus der Anfrage (kein Weg aus dem Ordner hinaus).
# 0.13.0: die Darstellung liegt in static/md, Kopie aus lib/mdview (check-shared.py).
STATIC = {"md/markdown.js": "application/javascript; charset=utf-8",
          "md/view.js": "application/javascript; charset=utf-8",
          "md/mermaid.min.js": "application/javascript; charset=utf-8",
          # 0.17.0: Symbol im Reiter und auf dem Homescreen (iOS braucht PNG ohne Transparenz)
          "favicon.svg": "image/svg+xml", "icon-180.png": "image/png",
          "icon-192.png": "image/png", "icon-512.png": "image/png"}
# 0.17.0: der Name dieses Chats -- Titel des Reiters, Kopf der Leiste und Name auf dem
# Homescreen (Web-App-Manifest, apple-mobile-web-app-title). Vorgabe „Chat“.
TITLE = " ".join((os.environ.get("CHAT_TITLE") or "").split())[:40] or "Chat"


def page_html():
    with open(PAGE_FILE, encoding="utf-8") as f:
        return f.read().replace("__TITLE__", html_escape(TITLE))


def html_escape(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def web_manifest():
    return {"name": TITLE, "short_name": TITLE, "start_url": "./", "scope": "./",
            "display": "standalone", "background_color": "#f7f7f5", "theme_color": "#1d4ed8",
            "icons": [{"src": "static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "static/favicon.svg", "sizes": "any", "type": "image/svg+xml"}]}


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


def _tools_report(person, assertion, a, add):
    """Der MCP-Teil des Einrichtungsberichts; False, wenn danach nichts mehr zu prüfen ist."""
    if not add("client", bool(CLIENT_KEY and CLIENT_URL), "Der Chat darf als MCP-Client handeln",
               "" if CLIENT_KEY and CLIENT_URL else "Die Plattform hat dieser Instanz keinen Client-Schlüssel gegeben.",
               "" if CLIENT_KEY and CLIENT_URL else "Das Manifest erklärt mcp_client; eine Generalprobe bekommt keinen. "
               "Instanz neu ausrollen oder die Plattform aktualisieren (ab 0.1.243)."):
        return False
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
    if gw and assertion:
        doc = gateway_readiness(a.get("gateway") or "", assertion, a["view"])
        if doc and doc.get("state") != "not_for_you":
            for s in doc["servers"]:
                if s.get("state") == "ready":
                    continue
                where = (f"auf „Meine Werkzeuge“ des Gateways ({doc['me']})" if doc.get("me")
                         else "auf „Meine Werkzeuge“ des Gateways (Kachel im Portal)")
                what = ("Der Server hat deinen Zugang abgelehnt." if s["state"] == "credential_rejected"
                        else "Dieser Server braucht deinen eigenen Zugang.")
                add("personal:" + s["server"], False,
                    f"{s['tools']} Werkzeug(e) von {s['server']} noch nicht eingerichtet",
                    what + (" " + s["hint"] if s.get("hint") else ""),
                    ("Den Zugang erneuern " if s["state"] == "credential_rejected" else "Deinen Zugang hinterlegen ")
                    + where + ".")
    elif gw:
        add("view", False, f"Sicht {a['view']} als du", "Keine Zusicherung der Plattform in dieser Anfrage.",
            "Über die Plattform angemeldet öffnen, nicht mit einem Schlüssel.")
    return True


def setup_report(person, assertion, a):
    """Jedes Teil, das dieser Assistent braucht, der Reihe nach -- mit dem nächsten Schritt.

    Prüft echt (eine Runde als die Person, eine Frage an das KI-Gateway), damit der
    Bericht sagt, was IST, nicht was eingestellt sein sollte."""
    items = []

    def add(key, ok, title, detail="", next_=""):
        items.append({"key": key, "ok": ok, "title": title, "detail": detail, "next": next_})
        return ok

    if not a.get("view"):
        # 0.18.0 (RFC-0116 D9): ein Assistent ohne Sicht spricht ohne Werkzeuge
        add("view", True, "Ohne Sicht: dieser Assistent spricht ohne Werkzeuge",
            "Er braucht kein MCP-Gateway und keinen Zugang dort (oaap.ai.assistants 0.7).")
    elif not _tools_report(person, assertion, a, add):
        return items
    # 0.16.0 (RFC-0104 §4): welcher Weg zum Modell, und was ihm fehlt.
    if not model_ready():
        add("destination", False, "Ein Modell ist angebunden",
            "Dieser Instanz ist keine Destination ki zugeordnet, und die Plattform hat ihr keinen "
            "Zugang zu den KI-Gateways des Mandanten gegeben.",
            "Im Portal: Instanz → Destinationen → beim Bedarf „ki“ eine Destination des Mandanten "
            "zuordnen. Hat der Mandant keine, legt der Betreiber des Knotens eine an (Ziel: ein KI-Gateway, "
            "mit einem Schlüssel nur für den Modellnamen -- Portal → Destinationen, ab Plattform 0.1.244; "
            "sonst oaap destination add). Ohne Destination: ein KI-Gateway im Mandanten installieren und "
            "den Chat neu ausrollen (Manifest 0.18, ai_client).")
        return items
    try:
        way = model_way(assertion)
    except Stop as exc:
        if exc.status == 401:
            add("ai_gateway", False, "Modell über das KI-Gateway des Mandanten", exc.text,
                "Über die Plattform angemeldet öffnen, nicht mit einem Schlüssel.")
        else:
            add("ai_gateway", False, "Modell über das KI-Gateway des Mandanten", exc.text,
                "Mehrere KI-Gateways: in der Konfiguration der Instanz CHAT_AI_GATEWAY auf den Namen "
                "eines davon setzen. Keins: ein KI-Gateway aus dem Store in diesem Mandanten installieren. "
                "Oder im Portal eine Destination ki zuordnen -- die geht immer vor.")
        return items
    if way.kind == "destination":
        add("destination", True, "Ein Modell ist angebunden (Destination ki)",
            "Die Destination ki geht vor; KI-Gateways des Mandanten fragt der Chat dann nicht."
            if AI_CLIENT_KEY and AI_CLIENT_URL else "")
    else:
        add("ai_gateway", True, f"Ein Modell ist angebunden: {way.label}",
            "Der Chat ruft das KI-Gateway über die Plattform, mit seinem Client-Schlüssel und deiner "
            "Zusicherung; einen Schlüssel des KI-Gateways braucht er nicht.")
    req = way.request("/models", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            ids = [m.get("id") for m in (json.loads(r.read(1_000_000)).get("data") or []) if isinstance(m, dict)]
        whose = "der Schlüssel der Destination" if way.kind == "destination" else "dieser Chat"
        add("model", a["model"] in ids, f"Modellname {a['model']} ist am KI-Gateway freigegeben",
            "" if a["model"] in ids else f"Am KI-Gateway erreicht {whose}: " + (", ".join(ids) or "nichts") + ".",
            "" if a["model"] in ids else f"Auf der Seite des KI-Gateways den Modellnamen {a['model']} anlegen "
            + ("und dem Schlüssel der Destination erlauben" if way.kind == "destination"
               else "und der Freigabe dieses Chats (Art client) erlauben")
            + " -- oder im Assistenten einen vorhandenen Namen wählen.")
    except urllib.error.HTTPError as exc:
        if way.kind == "destination":
            add("model", False, "Das KI-Gateway antwortet", f"Antwort {exc.code}.",
                "Der Schlüssel der Destination ist ungültig oder widerrufen: neuen Schlüssel ausstellen und "
                "bei der Destination hinterlegen." if exc.code in (401, 403) else "Läuft das KI-Gateway?")
        elif exc.code == 403:
            group = own_instance_guess()
            add("model", False, f"Das KI-Gateway {way.name} antwortet",
                f"Antwort 403: {NO_GRANT}.",
                "Auf der Seite des KI-Gateways unter Schlüssel eine Freigabe der Art „client“ für diese "
                "Instanz anlegen (" + (f"Name {group}, vermutet aus der Adresse" if group
                                       else "Name dieser Instanz") + ") und ihr die Modellnamen erlauben "
                "-- oder den Aufbau aus dem Profil laufen lassen, der sie anlegt.")
        elif exc.code == 401:
            add("model", False, f"Das KI-Gateway {way.name} antwortet", f"Antwort 401: {way.refused(401)}.",
                "Seite neu laden; hilft das nicht, die Instanz neu ausrollen (Client-Schlüssel).")
        else:
            add("model", False, f"Das KI-Gateway {way.name} antwortet", f"Antwort {exc.code}.",
                "Läuft das KI-Gateway, und kennt es Aufrufe über die via-Tür (RFC-0104)?")
    except (OSError, ValueError) as exc:
        add("model", False, "Das KI-Gateway antwortet", f"Nicht erreicht ({type(exc).__name__}).",
            "Läuft das KI-Gateway, und stimmt das Ziel der Destination?" if way.kind == "destination"
            else f"Läuft das KI-Gateway {way.name}?")
    return items
def ki_models(assertion=""):
    """Die Modellnamen, die der Schlüssel der Destination ki erreicht (0.6.0, L27) --
    oder, ohne Destination, dieser Chat über die via-Tür (0.16.0, RFC-0104 §4).

    Das Formular „Assistent“ bietet nur diese an: ein frei getippter Name
    scheiterte erst beim ersten Gespräch (bec, 18.11.: „chat-default“ war
    angelegt, aber dem Schlüssel nicht erlaubt). Gibt (Liste, Fehlersatz).
    """
    if not model_ready():
        return [], "Dieser Instanz ist keine Destination ki zugeordnet (und kein KI-Gateway über die Plattform)."
    try:
        way = model_way(assertion)
    except Stop as exc:
        return [], exc.text
    req = way.request("/models", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read(1_000_000)).get("data") or []
    except urllib.error.HTTPError as exc:
        return [], (f"Das KI-Gateway lehnt ab (HTTP {exc.code}): {way.refused(exc.code)}."
                    if exc.code in (401, 403) else f"Das KI-Gateway antwortet {exc.code}.")
    except (OSError, ValueError) as exc:
        return [], f"Das KI-Gateway ist nicht erreichbar ({type(exc).__name__})."
    out = [{"id": m["id"], "light": m.get("oaap_light", ""), "meaning": m.get("oaap_light_meaning", "")}
           for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)]
    return out, ""


def gateway_views(gateway, assertion):
    """Die Sichten, die die Person aus diesem Chat öffnen kann (0.6.1) -- die
    Suchhilfe im Formular „Assistent“. Fragt das Gateway über die via-Tür
    (GET …/_views, MCP-Gateway ab 0.14.0); die Verwaltung des Mandanten
    bekommt dort auch die übrigen Sichten mit Grund. Gibt (Gateway, Liste, alle?, Fehlersatz)."""
    try:
        gw = gateway_for(gateway or "")
    except Stop as exc:
        return "", [], False, exc.text
    if not assertion:
        return gw["name"], [], False, "Keine Zusicherung der Plattform in dieser Anfrage -- über die Plattform angemeldet öffnen."
    req = urllib.request.Request(gw["via"] + "/_views", headers={
        "Accept": "application/json", "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            doc = json.loads(r.read(1_000_000))
    except urllib.error.HTTPError as exc:
        return gw["name"], [], False, (f"Das Gateway {gw['name']} kennt die Sichtenliste nicht "
                                       "(MCP-Gateway vor 0.14.0?)." if exc.code in (401, 404, 405)
                                       else f"Das Gateway {gw['name']} antwortet {exc.code}.")
    except (OSError, ValueError) as exc:
        return gw["name"], [], False, f"Das Gateway {gw['name']} ist nicht erreichbar ({type(exc).__name__})."
    rows = [v for v in (doc.get("views") or []) if isinstance(v, dict) and isinstance(v.get("name"), str)]
    return gw["name"], rows, bool(doc.get("all")), ""


def gateway_readiness(gateway, assertion, view):
    """Ist die Sicht dieses Assistenten für die Person eingerichtet? (0.7.0, RFC-0095 §5)

    Fragt das Gateway über die via-Tür (GET …/_readiness, MCP-Gateway ab 0.17.0). Das Gateway
    kennt keine Assistenten; die Zuordnung Assistent → Sicht macht der Chat (D9). Gibt das
    Dokument des Gateways oder None, wenn es die Frage nicht kennt (älteres Gateway)."""
    try:
        gw = gateway_for(gateway or "")
    except Stop:
        return None
    if not assertion:
        return None
    from urllib.parse import quote
    req = urllib.request.Request(gw["via"] + "/_readiness?view=" + quote(view or ""), headers={
        "Accept": "application/json", "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            doc = json.loads(r.read(1_000_000))
    except (urllib.error.HTTPError, OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) and isinstance(doc.get("servers"), list) else None


def conv_light(conv):
    """Die Ampel eines Gesprächs. Ein Gespräch von vor 0.8.0 hat keine: es gilt die Vorgabe."""
    x = (conv or {}).get("light")
    return x if x in LIGHT_RANK else DEFAULT_LIGHT


def ki_ceiling(assertion=""):
    """Die Decke des Schlüssels der Destination ki (GET /v1/usage des KI-Gateways), oder ''.
    Ohne Destination dieselbe Frage über die via-Tür (die Decke der Freigabe des Chats)."""
    try:
        way = model_way(assertion) if model_ready() else None
    except Stop:
        way = None
    if way is None:
        return ""
    req = way.request("/usage", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            c = json.loads(r.read(1_000_000)).get("ceiling")
    except (urllib.error.HTTPError, OSError, ValueError, AttributeError):
        return ""
    return c if c in LIGHT_RANK else ""


def light_report(a, assertion):
    """Wie grün ist dieser Assistent? (RFC-0101 §2.2) Modell und Sicht je mit ihrer Farbe,
    die Farbe, die er braucht (die schlechtere), und die Decke des Schlüssels.

    Die Farben sagen die Gateways selbst: das KI-Gateway je Modellname für den Schlüssel des
    Chats (/v1/models), das MCP-Gateway je Sicht für die Person (/_views). Was dort fehlt,
    bleibt leer -- dann sagt `said`, warum, und die Gateways entscheiden beim Aufruf."""
    models, merr = ki_models(assertion)
    m = next((x for x in models if x["id"] == a.get("model")), None)
    mlight = m["light"] if m and m.get("light") in LIGHT_RANK else ""
    if a.get("view"):
        _gw, rows, _all, verr = gateway_views(a.get("gateway") or "", assertion)
    else:
        rows, verr = [], ""     # ohne Sicht zählt nur das Modell (RFC-0116 D9)
    v = next((x for x in rows if x.get("name") == a.get("view")), None)
    vlight = v.get("light") if v and v.get("light") in LIGHT_RANK else ""
    known = [x for x in (mlight, vlight) if x]
    needs = max(known, key=LIGHT_RANK.get) if known else ""
    ceiling = ki_ceiling(assertion) or max((x["light"] for x in models if x.get("light") in LIGHT_RANK),
                                  key=LIGHT_RANK.get, default="")
    said = merr or verr or ("" if m else f"Der Modellname {a.get('model')} ist für den Schlüssel "
                            "des Chats nicht erreichbar.") or ("" if v else
                            f"Die Sicht {a.get('view')} kennt das Gateway für dich nicht."
                            if a.get("view") else "")
    return {"model": {"id": a.get("model"), "light": mlight},
            "view": {"name": a.get("view"), "light": vlight},
            "needs": needs, "ceiling": ceiling, "default": DEFAULT_LIGHT, "said": said}


def light_change_refused(old, new, sure):
    """None, wenn der Wechsel gilt -- sonst (Status, Dokument)."""
    if new not in LIGHT_RANK:
        return 400, {"error": "Eine Ampel ist green, yellow oder red."}
    if LIGHT_RANK[new] > LIGHT_RANK[old] and sure is not True:
        # §2.1: ins Rote nur bewusst -- ein Satz, ein Klick.
        return 409, {"error": "Ein Wechsel zu einer schlechteren Farbe braucht deine Bestätigung.",
                     "confirm": CONFIRM_SAYS[new]}
    return None


CONFIRM_SAYS = {"yellow": "Ich gebe in diesem Gespräch nichts Sensibles ein.",
                "red": "Ich gebe in diesem Gespräch nur Öffentliches ein.", "green": ""}


PUBLIC = ("id", "title", "greeting", "model", "view", "gateway", "visibility", "commands")


def public(a, origin, who):
    manage = (origin == "chat" and store.can_manage(a, who)) or (origin == "gateway" and a.get("manage") is True)
    return dict({k: a.get(k) for k in PUBLIC + ("views",)}, origin=origin, manage=manage,
                rev=a.get("rev"), state=a.get("state") or "active")


# ---------------------------------------------- Assistenten im MCP-Gateway (0.9.0)
# RFC-0097 Stufe 1: Die Beschreibung eines Assistenten liegt im MCP-Gateway, neben
# der Sicht, die er nennt -- mit Revisionen. Der Chat bleibt die Laufzeit. Heimat
# ist das Standard-Gateway des Mandanten (gateway_for("")). Solange die alte Datei
# des Chats noch Assistenten hat, bleiben sie hier, bis ein tenant_admin sie
# übernimmt ("Übernehmen"); ein Gateway vor 0.22 kennt keine Assistenten, dann
# bleibt alles wie bisher. Ein Assistent aus CHAT_ASSISTANT bleibt immer (D7).
ASSISTANTS_ONLY = [x.strip() for x in (os.environ.get("CHAT_ASSISTANTS_ONLY") or "").split(",") if x.strip()]
# Hat der Chat einmal gesehen, dass seine Assistenten im Gateway liegen, merkt er es
# sich -- danach fällt er nie mehr still auf die Datei zurück (ein kurz nicht
# erreichbares Gateway darf nicht dazu führen, dass Speichern in der Datei landet).
HOME_MARK = os.path.join(DATA, "assistants.home")
RECORD_FIELDS = ("rev", "state", "manage", "changed_at", "current", "created", "changed_by",
                 "origin")


def clean(a):
    """Ein Assistent im Austauschformat -- ohne die Felder, die das Gateway führt."""
    return {k: v for k, v in a.items() if k not in RECORD_FIELDS}


def gw_assist(method, path, assertion, body=None):
    """(Status, Dokument) von …/_assistants<path> des Standard-Gateways, als die Person.
    Status 0: kein Gateway, keine Zusicherung oder nicht erreichbar."""
    try:
        gw = gateway_for("")
    except Stop:
        return 0, {}
    if not assertion:
        return 0, {}
    req = urllib.request.Request(gw["via"] + "/_assistants" + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Accept": "application/json", "Content-Type": "application/json",
                                          "Authorization": "Bearer " + CLIENT_KEY, ACT_ASSERTION: assertion})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read(4_000_000) or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read(200_000) or b"{}")
        except ValueError:
            return exc.code, {}
    except (OSError, ValueError):
        return 0, {}


def gw_errors(st, doc):
    if st == 200:
        return []
    errs = doc.get("errors") if isinstance(doc.get("errors"), list) else []
    if not errs and doc.get("error"):
        errs = [str(doc["error"])]
    return errs or [f"Das MCP-Gateway antwortete {st or 'nicht'}."]


def mark_home():
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(HOME_MARK, "w", encoding="utf-8") as f:
            f.write("gateway\n")
    except OSError:
        pass


class Home:
    """Wo die Assistenten dieser Anfrage liegen: im Gateway oder (noch) im Chat."""

    def __init__(self, assertion):
        self.assertion = assertion
        self.local = STORE._read()
        self.mode, self.items, self.grants_known, self.error = "chat", [], None, ""
        marked = os.path.exists(HOME_MARK)
        if marked or (CLIENT_KEY and CLIENT_URL and assertion and not self.local):
            st, doc = gw_assist("GET", "", assertion)
            if st == 200 and isinstance(doc.get("assistants"), list):
                self.mode = "gateway"
                self.items = [a for a in doc["assistants"] if isinstance(a, dict) and a.get("id")]
                self.grants_known = doc.get("grants_known")
                if not marked:
                    mark_home()
            elif marked:
                self.mode = "gateway"
                self.error = ("Die Assistenten liegen im MCP-Gateway, das gerade nicht antwortet"
                              f" ({st or 'nicht erreichbar'}). Es bleibt nur der aus der Konfiguration.")

    def all(self, who):
        """(Assistent, Herkunft config|chat|gateway), die diese Person sieht."""
        out = []
        if CONFIGURED and not CONFIGURED[1]:
            out.append((CONFIGURED[0], "config"))
        ids = {a["id"] for a, _ in out}
        if self.mode == "gateway":
            out += [(a, "gateway") for a in self.items if a["id"] not in ids]
        else:
            out += [(a, o) for a, o in STORE.all() if o == "chat" and a["id"] not in ids
                    and store.can_see(a, who)]
        if ASSISTANTS_ONLY:
            out = [(a, o) for a, o in out if a["id"] in ASSISTANTS_ONLY]
        return out

    def find(self, aid, who):
        return next(((a, o) for a, o in self.all(who) if a["id"] == aid), (None, None))

    def migration_pending(self):
        """Wie viele Assistenten noch in der Datei liegen, wenn das Gateway sie nehmen kann."""
        if not self.local or not (CLIENT_KEY and CLIENT_URL):
            return 0
        st, doc = gw_assist("GET", "", self.assertion)
        return len(self.local) if st == 200 and isinstance(doc.get("assistants"), list) else 0


def rateable(m):
    """Eine Antwort, die man bewerten kann: der Schlusstext einer Runde, von einem Assistenten
    mit Revision (RFC-0101 D8 -- ohne Revision misst ein Stern nichts)."""
    return (m.get("role") == "assistant" and bool(m.get("content")) and not m.get("tool_calls")
            and bool(m.get("rev")))


def stamp_ms(a, b):
    try:
        t = [datetime.strptime(x, "%Y-%m-%dT%H:%M:%SZ") for x in (a, b)]
    except (TypeError, ValueError):
        return 0
    return max(0, int((t[1] - t[0]).total_seconds() * 1000))


def tool_lines(conv, msgs):
    """Je Werkzeugaufruf Name, Wirkung, Ausgang -- nie Argumente oder Ergebnisse (§3.2)."""
    effects = conv.get("effects") or {}
    out = []
    for m in msgs:
        if m.get("role") != "tool":
            continue
        content = str(m.get("content") or "")
        outcome = ("refused" if m.get("confirmed") is False else
                   "error" if content.startswith("Fehler") else "ok")
        eff = effects.get(m.get("name")) or ("read" if "confirmed" not in m else "write")
        out.append({"name": str(m.get("name") or "")[:100], "effect": eff, "outcome": outcome})
    return out[:100]


def rating_body(conv, index, stars, share):
    """(Assistent, Körper für POST …/ratings) oder (None, Satz). `index` None = das Gespräch."""
    msgs = conv["messages"]
    if index is None:
        answers = [i for i, m in enumerate(msgs) if rateable(m)]
        if not answers:
            return None, "In diesem Gespräch gibt es noch keine Antwort, die man bewerten kann."
        i, u = answers[-1], 0
        body = {"scope": "conversation", "round": sum(1 for m in msgs if m.get("role") == "user"),
                "tools": tool_lines(conv, msgs),
                "ms": stamp_ms(msgs[0].get("at"), msgs[-1].get("at"))}
    else:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(msgs):
            return None, "Diese Antwort gibt es in diesem Gespräch nicht."
        i = index
        if not rateable(msgs[i]):
            return None, ("Sterne gibt es nur für Antworten eines Assistenten mit Revisionen im "
                          "MCP-Gateway (RFC-0101 D8).")
        u = max((k for k in range(i) if msgs[k].get("role") == "user"), default=0)
        body = {"scope": "answer",
                "round": sum(1 for m in msgs[:i] if m.get("role") == "user"),
                "tools": tool_lines(conv, msgs[u:i]),
                "ms": stamp_ms(msgs[u].get("at"), msgs[i].get("at"))}
    ans = msgs[i]
    body.update(rev=ans["rev"], stars=stars, conversation=conv["id"],
                model=ans.get("model") or "", light=ans.get("light") or "")
    if share and stars is not None:
        q = next((msgs[k].get("content") for k in range(i, -1, -1) if msgs[k].get("role") == "user"), "")
        body["text"] = {"question": str(q or "")[:8000], "answer": str(ans.get("content") or "")[:8000]}
    return conv.get("assistant") or "", body


# ------------------------------------------------ 0.11.0: Nicht zufrieden (RFC-0101 Stufe 3)
# Der Chat baut den Vorgang, der Browser schickt ihn an /platform/feedback seiner eigenen
# Herkunft -- dort bezeugt die Plattform die Person (§4.3). Der Chat hält keinen Schlüssel
# für Vorgänge und legt nie selbst einen an.
CASE_NUMBER = re.compile(r"^V-\d{4}-\d{4,6}$")
CASE_OUTCOMES = ("accepted", "reopened")      # 0.14.0: was die Person zur Antwort sagt
LINK = re.compile(r"\[([^\]\n]{1,200})\]\((https?://[^)\s]{1,500})\)")
UNHAPPY_Q, UNHAPPY_A, UNHAPPY_E = 4000, 4000, 1500
NO_CALL_IDS = ("Aufruf-Kennungen gehen nicht mit: dieser Mandant führt sie noch nicht (RFC-0098 "
               "Stufe 3). Zeit, Assistent und Revision grenzen die Protokollzeilen ein.")


def final_answer(m):
    return m.get("role") == "assistant" and bool(m.get("content")) and not m.get("tool_calls")


def unhappy_round(conv, index):
    """(i, u) = Antwort und Frage ihrer Runde -- oder (None, Satz). `index` None = die letzte."""
    msgs = conv["messages"]
    if index is None:
        answers = [k for k, m in enumerate(msgs) if final_answer(m)]
        if not answers:
            return None, "In diesem Gespräch gibt es noch keine Antwort."
        i = answers[-1]
    else:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(msgs) \
                or not final_answer(msgs[index]):
            return None, "Diese Antwort gibt es in diesem Gespräch nicht."
        i = index
    u = max((k for k in range(i) if msgs[k].get("role") == "user"), default=0)
    return i, u


def round_tools(conv, msgs):
    """Je Werkzeugaufruf der Runde: Name, Wirkung, Ausgang -- und, nur für den Dialog der
    Person, Argumente und Ergebnis (D10: sie gehen nur mit Haken je Karte mit)."""
    args = {}
    for m in msgs:
        for c in m.get("tool_calls") or []:
            args[c.get("id")] = loop.call_args(c)[0]
    lines = tool_lines(conv, msgs)
    out, k = [], 0
    for m in msgs:
        if m.get("role") != "tool":
            continue
        if k >= len(lines):
            break
        out.append(dict(lines[k], id=str(m.get("tool_call_id") or k),
                        arguments=args.get(m.get("tool_call_id")) or {},
                        result=str(m.get("content") or "")[:3000]))
        k += 1
    return out


def doc_pages(answer, msgs):
    """Hilfeseiten, die die Antwort verlinkt und die ein Hilfe-Werkzeug der Runde geliefert
    hat (help 0.5 `link`) -- was gelesen wurde und nicht gereicht hat."""
    found = "\n".join(str(m.get("content") or "") for m in msgs
                      if m.get("role") == "tool" and "help" in str(m.get("name") or ""))
    out, seen = [], set()
    for title, url in LINK.findall(answer or ""):
        base = url.split("#")[0]
        if base in found and url not in seen:
            seen.add(url)
            out.append({"title": title.strip(), "url": url})
    return out[:20]


def unhappy_prefill(conv, index):
    """Was der Dialog vorbelegt: Frage, Antwort, Werkzeuge, Hilfeseiten, Umstände."""
    got = unhappy_round(conv, index)
    if got[0] is None:
        return None, got[1]
    i, u = got
    msgs = conv["messages"]
    ans = msgs[i]
    rnd = msgs[u:i + 1]
    return {"index": i, "round": sum(1 for m in msgs[:i] if m.get("role") == "user"),
            "question": str(msgs[u].get("content") or "") if msgs[u].get("role") == "user" else "",
            "answer": str(ans.get("content") or ""),
            "tools": round_tools(conv, rnd), "doc_pages": doc_pages(ans.get("content"), rnd),
            "rev": ans.get("rev") or None, "model": ans.get("model") or "",
            "light": ans.get("light") or conv_light(conv),
            "from": msgs[u].get("at") or "", "to": ans.get("at") or "",
            "ms": stamp_ms(msgs[u].get("at"), ans.get("at")), "call_ids": None,
            "call_ids_note": NO_CALL_IDS}, ""


def unhappy_case(conv, a, gw_name, body):
    """(Dokument für POST /platform/feedback, None) -- oder (None, Satz)."""
    pre, err = unhappy_prefill(conv, body.get("index"))
    if pre is None:
        return None, err
    q = str(body.get("question") or "").strip()
    ans = str(body.get("answer") or "").strip()
    exp = str(body.get("expected") or "").strip()
    if not exp:
        return None, "Bitte schreib, was du erwartet hättest."
    if not q and not ans:
        return None, "Frage oder Antwort braucht der Support, mindestens eins."
    if len(q) > UNHAPPY_Q or len(ans) > UNHAPPY_A or len(exp) > UNHAPPY_E:
        return None, (f"Frage und Antwort höchstens je {UNHAPPY_Q} Zeichen, die Erwartung "
                      f"höchstens {UNHAPPY_E}. Bitte kürzen.")
    share = body.get("share_tools") if isinstance(body.get("share_tools"), list) else []
    share = {str(x) for x in share}
    tools = []
    for t in pre["tools"]:
        x = {"name": t["name"], "effect": t["effect"], "outcome": t["outcome"]}
        if t["id"] in share:
            x["arguments"] = t["arguments"]
            x["result"] = t["result"]
        tools.append(x)
    gap = body.get("doc_gap") is True
    fields = {"assistant": a["id"], "rev": pre["rev"], "model": pre["model"], "light": pre["light"],
              "view": a.get("view") or "", "gateway": gw_name, "scope": "answer",
              "round": pre["round"], "from": pre["from"], "to": pre["to"], "ms": pre["ms"],
              "tools": tools, "expected": exp, "doc_gap": gap,
              "doc_pages": pre["doc_pages"] if gap else [],
              "call_ids_note": NO_CALL_IDS, "chat": VERSION}
    # Obergrenze der App Vorgänge (40000 Bytes): zuerst die Ergebnisse kürzen, nie die Namen
    for t in reversed(tools):
        if len(json.dumps(fields, ensure_ascii=False).encode()) <= 36000:
            break
        if "result" in t:
            t["result"] = "(für den Vorgang zu lang, weggelassen)"
    if len(json.dumps(fields, ensure_ascii=False).encode()) > 36000:
        return None, "Die mitgegebenen Werkzeugdaten sind zu groß. Bitte weniger Karten ankreuzen."
    title = f"Nicht zufrieden: {a.get('title') or a['id']} (Runde {pre['round']})"[:200]
    text = (f"Frage:\n{q or '(weggelassen)'}\n\nAntwort:\n{ans or '(weggelassen)'}\n\n"
            f"Erwartet:\n{exp}")
    doc = {"kind": "question", "subkind": "chat-answer", "title": title, "body": text,
           "object": {"kind": "conversation", "id": conv["id"]}, "fields": fields}
    if a.get("support_queue"):
        doc["queue"] = a["support_queue"]
    return doc, None


def view_of(conv):
    """Was der Browser zeigt -- ohne eigene Vermerke, die er nicht braucht."""
    shown = []
    for m in conv["messages"]:
        x = {k: m[k] for k in ("role", "content", "name", "confirmed", "seconds", "at", "command",
                               "tool_call_id", "stars") if k in m}
        if rateable(m):
            x["rate"] = True
        if m.get("view"):
            x["view"] = m["view"]
        if m.get("tool_calls"):
            x["calls"] = [{"name": c["function"]["name"],
                           "arguments": loop.call_args(c)[0]} for c in m["tool_calls"]]
        shown.append(x)
    return {"id": conv["id"], "title": conv.get("title") or "Neues Gespräch",
            "assistant": conv.get("assistant"), "messages": shown, "confirm": conv.get("confirm"),
            "light": conv_light(conv), "stars": conv.get("stars"),
            "rateable": any(rateable(m) for m in conv["messages"]),
            "cases": conv.get("cases") or [],
            "answered": any(final_answer(m) for m in conv["messages"])}


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
        # 0.13.0: Der Anzeigename kommt prozentkodiert, sobald er mehr als ASCII enthält
        # (oaap.core.identity, RFC-0040 D4: immer dekodieren) -- sonst stand
        # „J%C3%B6rg“ im Prompt.
        return unquote(self.headers.get("X-OAAP-Display-Name") or self.headers.get("X-OAAP-User")
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
            return self.send(200, "text/html; charset=utf-8", page_html())
        if path == "/manifest.webmanifest":
            return self.send(200, "application/manifest+json; charset=utf-8",
                             json.dumps(web_manifest(), ensure_ascii=False))
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
        home = Home(self.headers.get(ASSERTION, "").strip())
        if path == "/api/state":
            return self.json(200, {
                "user": self.headers.get("X-OAAP-User", ""), "version": VERSION,
                "assistants": [public(a, o, who) for a, o in home.all(who)],
                "home": home.mode,
                "migration": home.migration_pending() if "tenant_admin" in who.roles else 0,
                "errors": SETUP_ERRORS + ([home.error] if home.error else []),
                "tenant_admin": "tenant_admin" in who.roles,
                "templates_url": templates_url() if "tenant_admin" in who.roles else "",
                "teams_known": (home.grants_known is not False) if home.mode == "gateway"
                else who.grants is not None,
                "client": bool(CLIENT_KEY and CLIENT_URL), "model": model_ready(),
                "sandbox_origins": SANDBOX_ORIGINS, "default_light": DEFAULT_LIGHT,
                "conversations": list_convs(person)})
        if path == "/api/models":
            models, err = ki_models(self.headers.get(ASSERTION, "").strip())
            return self.json(200, {"models": models, "error": err})
        if path == "/api/views":
            from urllib.parse import parse_qs, urlparse
            gname = (parse_qs(urlparse(self.path).query).get("gateway") or [""])[0]
            gw, rows, all_, err = gateway_views(gname, self.headers.get(ASSERTION, "").strip())
            return self.json(200, {"gateway": gw, "views": rows, "all": all_, "error": err})
        if path == "/api/light":
            # 0.8.0 (RFC-0101 §2): Ampel des Gesprächs (oder die Vorgabe für einen Entwurf)
            # und wie grün der Assistent ist.
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            conv = None
            cid = (q.get("conv") or [""])[0]
            if cid:
                conv = load_conv(person, cid) if CONV.match(cid) else None
                if conv is None:
                    return self.json(404, {"error": "Kein solches Gespräch."})
            aid = conv["assistant"] if conv else (q.get("assistant") or [""])[0]
            a, _ = home.find(aid, who)
            if a is None:
                return self.json(404, {"error": "Kein solcher Assistent."})
            rep_ = light_report(a, self.headers.get(ASSERTION, "").strip())
            light = conv_light(conv) if conv else DEFAULT_LIGHT
            rep_["light"] = light
            rep_["ok"] = not rep_["needs"] or LIGHT_RANK[rep_["needs"]] <= LIGHT_RANK[light]
            return self.json(200, rep_)
        if path == "/api/readiness":
            # 0.7.0: je sichtbarem Assistenten, ob seine Werkzeuge für dich eingerichtet sind.
            assertion = self.headers.get(ASSERTION, "").strip()
            seen, out = {}, {}
            for a, _o in home.all(who):
                if not a.get("view"):
                    continue        # ohne Sicht fehlt nie ein Werkzeug (RFC-0116 D9)
                k = (a.get("gateway") or "", a.get("view") or "")
                if k not in seen:
                    seen[k] = gateway_readiness(k[0], assertion, k[1])
                doc = seen[k]
                if doc and doc.get("state") != "not_for_you":
                    out[a["id"]] = {"missing_tools": doc.get("missing_tools", 0),
                                    "ready": bool(doc.get("ready")), "me": doc.get("me", "")}
            return self.json(200, {"assistants": out})
        if path == "/api/setup":
            aid = (self.path.split("assistant=", 1)[1].split("&")[0] if "assistant=" in self.path else "")
            a, _ = home.find(aid, who)
            if a is None:
                return self.json(404, {"error": "Kein solcher Assistent."})
            errs = store.problems(clean(a))
            if errs:
                return self.json(200, {"assistant": aid, "items": [
                    {"key": "assistant", "ok": False, "title": "Der Assistent ist gültig",
                     "detail": " ".join(errs), "next": "Den Assistenten unter „Assistenten …“ korrigieren."}]})
            return self.json(200, {"assistant": aid, "items": setup_report(
                person, self.headers.get(ASSERTION, "").strip(), a)})
        if path == "/api/assistants/export":
            doc = (STORE.export(who) if home.mode == "chat" else
                   {"v": 1, "assistants": [clean(a) for a, o in home.all(who) if o == "gateway" and a.get("manage")]})
            return self.json(200, doc, [("Content-Disposition", 'attachment; filename="assistenten.json"')])
        m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})/(evaluation|diff)$", path)
        if m:
            # 0.12.0 (RFC-0097 Stufe 2, RFC-0101 Stufe 4): die Auswertung je Revision und der
            # Vergleich zweier Revisionen -- vom MCP-Gateway, als die Person; es entscheidet.
            if home.mode != "gateway":
                return self.json(404, {"error": "Auswertungen gibt es für Assistenten im MCP-Gateway."})
            q = ""
            if m.group(2) == "diff":
                from urllib.parse import parse_qs, urlparse
                qs = parse_qs(urlparse(self.path).query)
                a_, b_ = (qs.get("a") or [""])[0], (qs.get("b") or [""])[0]
                if not (a_.isdigit() and b_.isdigit()):
                    return self.json(400, {"error": "a und b sind Revisionsnummern."})
                q = f"?a={a_}&b={b_}"
            st, doc = gw_assist("GET", f"/{m.group(1)}/{m.group(2)}{q}", home.assertion)
            if st == 200:
                return self.json(200, doc)
            return self.json(st if st in (400, 403, 404) else 502, {"error": " ".join(gw_errors(st, doc))})
        m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})$", path)
        if m:
            a, origin = home.find(m.group(1), who)
            if a is None:
                return self.json(404, {"error": "Kein solcher Assistent."})
            return self.json(200, dict(a, origin=origin, manage=public(a, origin, who)["manage"]))
        m = re.match(r"^/api/conv/([a-z0-9]{12})(/commands|/view|/unhappy)?$", path)
        if m:
            conv = load_conv(person, m.group(1))
            if not conv:
                return self.json(404, {"error": "Kein solches Gespräch."})
            if not m.group(2):
                return self.json(200, view_of(conv))
            if m.group(2) == "/unhappy":
                raw = (self.path.split("index=", 1)[1].split("&")[0] if "index=" in self.path else "")
                pre, err = unhappy_prefill(conv, int(raw) if raw.isdigit() else None)
                if pre is None:
                    return self.json(400, {"error": err})
                a, _ = home.find(conv.get("assistant") or "", who)
                pre["assistant"] = {"id": conv.get("assistant"), "title": (a or {}).get("title") or conv.get("assistant"),
                                    "queue": (a or {}).get("support_queue") or ""}
                return self.json(200, pre)
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
            ps = McpWay(person, assertion, a, conv_light(conv)).prompts()
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
            res = McpWay(person, assertion, a, conv_light(conv)).read(msg["view"])
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
            way = McpWay(person, assertion, a, conv_light(conv))
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
        """Der Assistent des Gesprächs (geltende Revision) -- oder ein Satz, warum nicht (mehr)."""
        a, _ = Home(self.headers.get(ASSERTION, "").strip()).find(conv.get("assistant") or "", who)
        if a is None:
            return "Den Assistenten dieses Gesprächs gibt es für dich nicht (mehr)."
        if a.get("state") == "inactive":
            return "Dieser Assistent ist gerade deaktiviert."
        errs = store.problems(clean(a))
        return ("Der Assistent ist ungültig: " + " ".join(errs)) if errs else a

    def assistants_post(self, path, body, who, home):
        """Speichern, Importieren, Löschen, (de)aktivieren, Übernehmen -- im Gateway oder hier."""
        def out(errs, ok):
            return self.json(400 if errs else 200, {"errors": errs} if errs else ok)
        conf = CONFIGURED[0]["id"] if CONFIGURED and not CONFIGURED[1] else None
        if path == "/api/assistants/migrate":
            # RFC-0097 §2.2: die alte Datei ins Gateway, Besitzer bleiben; die Datei wird
            # umbenannt, nicht gelöscht.
            if "tenant_admin" not in who.roles:
                return self.json(403, {"error": "Die Assistenten dieses Chats übernimmt der tenant_admin."})
            if not home.local:
                return self.json(400, {"error": "In diesem Chat liegen keine Assistenten mehr."})
            st, doc = gw_assist("POST", "/_migrate", home.assertion,
                                {"v": 1, "assistants": home.local})
            errs = gw_errors(st, doc)
            if not errs:
                mark_home()
                os.replace(STORE.path, STORE.path + ".migrated-" + datetime.now(timezone.utc)
                           .strftime("%Y%m%dT%H%M%SZ"))
            return out(errs, {"migrated": len(home.local)})
        if home.mode == "chat":
            if path == "/api/assistants":
                errs = STORE.save(body, who)
                return out(errs, {"saved": body.get("id")})
            if path == "/api/assistants/import":
                errs = STORE.import_document(body, who)
                return out(errs, {"imported": len(body.get("assistants") or [])})
            m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})/delete$", path)
            if m:
                errs = STORE.delete(m.group(1), who)
                return out(errs, {"deleted": m.group(1)})
            return self.json(404, {"error": "Das geht erst, wenn die Assistenten im MCP-Gateway liegen."})
        if path == "/api/assistants":
            if body.get("id") == conf:
                return out(["Diesen Assistenten pflegt der Betreiber in der Konfiguration (CHAT_ASSISTANT)."], {})
            st, doc = gw_assist("POST", "", home.assertion, clean(body))
            return out(gw_errors(st, doc), {"saved": body.get("id"), "rev": doc.get("rev")})
        if path == "/api/assistants/import":
            st, doc = gw_assist("POST", "/_import", home.assertion, body)
            return out(gw_errors(st, doc), {"imported": doc.get("imported")})
        m = re.match(r"^/api/assistants/([a-z0-9-]{1,48})/(delete|state)$", path)
        if m:
            if m.group(1) == conf:
                return out(["Diesen Assistenten pflegt der Betreiber in der Konfiguration."], {})
            state = "deleted" if m.group(2) == "delete" else str(body.get("state") or "")
            st, doc = gw_assist("POST", f"/{m.group(1)}/state", home.assertion, {"state": state})
            return out(gw_errors(st, doc), {"id": m.group(1), "state": state})
        return self.json(404, {"error": "nicht hier"})

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
        assertion = self.headers.get(ASSERTION, "").strip()
        if path.startswith("/api/assistants"):
            return self.assistants_post(path, body, who, Home(assertion))
        if path == "/api/conv":
            a, _ = Home(assertion).find(str(body.get("assistant") or ""), who)
            if a is None:
                return self.json(404, {"error": "Diesen Assistenten gibt es für dich nicht."})
            if a.get("state") == "inactive":
                return self.json(409, {"error": "Dieser Assistent ist gerade deaktiviert."})
            light = body.get("light") or DEFAULT_LIGHT
            refused = light_change_refused(DEFAULT_LIGHT, light, body.get("sure"))
            if refused:
                return self.json(*refused)
            conv = {"id": secrets.token_hex(6), "assistant": a["id"], "created": now(),
                    "messages": [], "light": light, "lights": [{"at": now(), "light": light}]}
            save_conv(person, conv)
            return self.json(200, view_of(conv))
        m = re.match(r"^/api/conv/([a-z0-9]{12})/view-call$", path)
        if m:
            conv = load_conv(person, m.group(1))
            if conv is None:
                return self.json(404, {"error": "Kein solches Gespräch."})
            return self.view_call(person, who, conv, body)
        m = re.match(r"^/api/conv/([a-z0-9]{12})/unhappy$", path)
        if m:
            conv = load_conv(person, m.group(1))
            if conv is None:
                return self.json(404, {"error": "Kein solches Gespräch."})
            a, _ = Home(assertion).find(conv.get("assistant") or "", who)
            if a is None:
                return self.json(404, {"error": "Den Assistenten dieses Gesprächs gibt es für dich nicht (mehr)."})
            try:
                gw_name = gateway_for(a.get("gateway") or "")["name"]
            except Stop:
                gw_name = ""
            doc, err = unhappy_case(conv, a, gw_name, body)
            return self.json(400, {"error": err}) if doc is None else self.json(200, {"case": doc})
        m = re.match(r"^/api/conv/([a-z0-9]{12})/(send|step|delete|rename|light|rate|case)$", path)
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
            if action == "light":
                new = str(body.get("light") or "")
                refused = light_change_refused(conv_light(conv), new, body.get("sure"))
                if refused:
                    return self.json(*refused)
                if new != conv_light(conv):
                    conv["light"] = new
                    # Im eigenen Verlauf der Person: wann sie welche Farbe gewählt hat.
                    conv.setdefault("lights", []).append({"at": now(), "light": new})
                    save_conv(person, conv)
                return self.json(200, view_of(conv))
            if action == "rate":
                return self.rate(person, who, conv, body)
            if action == "case":
                # 0.11.0: die Nummer, die /platform/feedback der Person genannt hat -- nur ein
                # Zeiger im eigenen Verlauf; den Status liest die Seite als die Person (D11).
                number = str(body.get("number") or "")
                index = body.get("index")
                if body.get("outcome") is not None:
                    return self.case_outcome(person, conv, assertion, number, body.get("outcome"))
                if not CASE_NUMBER.match(number) or unhappy_round(conv, index)[0] is None:
                    return self.json(400, {"error": "Vorgangsnummer und Antwort passen nicht."})
                cases = conv.setdefault("cases", [])
                counted = False
                if not any(c["number"] == number for c in cases):
                    cases.append({"number": number, "index": index, "at": now()})
                    del cases[:-50]
                    # 0.12.0 (RFC-0101 Stufe 4): dem Gateway sagen, dass zu assistant@rev ein
                    # Vorgang ging -- gezählt, ohne Inhalt. Scheitert das, bleibt der Vorgang.
                    rev = conv["messages"][unhappy_round(conv, index)[0]].get("rev")
                    if rev:
                        st, said = gw_assist("POST", f"/{conv.get('assistant')}/cases", assertion,
                                             {"rev": rev, "number": number})
                        counted = st == 200
                save_conv(person, conv)
                return self.json(200, dict(view_of(conv), counted=counted))
            return self.round(person, who, conv, action, body)
        finally:
            lock.release()

    def case_outcome(self, person, conv, assertion, number, outcome):
        """0.14.0 (RFC-0103 D10): die Person hat bei den Vorgängen „Lösung passt“ oder „Passt
        nicht“ gesagt (als sie, über /platform/feedback/mine/…). Hier nur: im eigenen Verlauf
        vermerken und dem Gateway das Wort sagen, damit die Auswertung der Revision es zählt.
        Entschieden hat der Chat damit nichts -- er hält keinen Schlüssel der Vorgänge."""
        if outcome not in CASE_OUTCOMES:
            return self.json(400, {"error": "outcome ist accepted oder reopened."})
        k = next((c for c in conv.get("cases") or [] if c.get("number") == number), None)
        if k is None:
            return self.json(400, {"error": "Diesen Vorgang kennt dieses Gespräch nicht."})
        k["outcome"], k["outcome_at"] = outcome, now()
        counted = False
        i = unhappy_round(conv, k.get("index"))[0]
        rev = conv["messages"][i].get("rev") if i is not None else None
        if rev:
            st, _said = gw_assist("POST", f"/{conv.get('assistant')}/cases", assertion,
                                  {"rev": rev, "number": number, "outcome": outcome})
            counted = st == 200
        save_conv(person, conv)
        return self.json(200, dict(view_of(conv), counted=counted))

    def rate(self, person, who, conv, body):
        """0.10.0 (RFC-0101 Stufe 2): Sterne an eine Antwort (`index`) oder das Gespräch.
        Gespeichert wird im MCP-Gateway, als die Person; hier nur, was sie gegeben hat --
        damit ihr Verlauf es zeigt. `stars: null` zieht zurück."""
        stars = body.get("stars")
        if stars is not None and (not isinstance(stars, int) or isinstance(stars, bool) or not 0 <= stars <= 5):
            return self.json(400, {"error": "0 bis 5 Sterne."})
        index = body.get("index")
        aid, doc = rating_body(conv, index, stars, body.get("share") is True)
        if aid is None:
            return self.json(400, {"error": doc})
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            return self.json(401, {"error": NO_ASSERTION})
        a, origin = Home(assertion).find(aid, who)
        if a is None or origin != "gateway":
            return self.json(404, {"error": "Diesen Assistenten gibt es für dich nicht (mehr) im MCP-Gateway."})
        doc["view"] = a.get("view") or ""
        try:
            doc["gateway"] = gateway_for(a.get("gateway") or "")["name"]
        except Stop:
            doc["gateway"] = ""
        models, _err = ki_models(assertion)
        ml = next((x.get("light") for x in models if x["id"] == doc["model"]), "")
        doc["model_light"] = ml if ml in LIGHT_RANK else ""
        st, said = gw_assist("POST", f"/{aid}/ratings", assertion, doc)
        errs = gw_errors(st, said)
        if errs:
            return self.json(502 if st in (0, 500, 502) else 400, {"error": " ".join(errs)})
        target = conv if index is None else conv["messages"][index]
        if stars is None:
            target.pop("stars", None)
        else:
            target["stars"] = stars
        save_conv(person, conv)
        return self.json(200, dict(view_of(conv), rated={"index": index, "stars": stars,
                                                        "text": "text" in doc}))

    def round(self, person, who, conv, action, body):
        # §5.2: ohne Zusicherung keine Runde -- nie ein Rückfall auf die App selbst.
        assertion = self.headers.get(ASSERTION, "").strip()
        if not assertion:
            # 0.5.2 (L18): the app cannot tell the two causes apart, so it
            # names both -- and the second one is the operator's to fix.
            return self.json(401, {"error": NO_ASSERTION})
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
                    run_command(McpWay(person, assertion, a, conv_light(conv)), a, conv["messages"], text)
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
          f"Modell: {'Destination ki' if KI_URL else ('KI-Gateway über die Plattform' + (' ' + AI_GATEWAY if AI_GATEWAY else '') if model_ready() else 'nein')}; Berechtigungen: "
          f"{'ja' if AUTHZ.url else 'nein (Team-Assistenten unsichtbar)'}; Assistenten "
          f"in der Datei {len(STORE.all())}" + (f"; FEHLER: {' '.join(SETUP_ERRORS)}" if SETUP_ERRORS else ""),
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
