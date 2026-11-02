"""Der Weg zum Server: MCP über Streamable HTTP, als Client (Spec §3, §6).

Drei Regeln stecken hier in der Bauform:

- **Nur die eingetragene Adresse** (A4). Eine Weiterleitung (`3xx`) wird
  nicht verfolgt, sondern ist ein Fehler; eine Adresse aus einer Antwort
  wird nie angefahren. Deshalb `http.client` statt `urllib`: `urllib`
  folgt Weiterleitungen von selbst. Adressen, die auf Loopback,
  Link-Local oder „unbestimmt“ auflösen, werden vor jeder Verbindung
  abgewiesen — 169.254.169.254 ist der Metadatendienst mancher Hoster.
- **Feste, getestete Protokollversionen** (A3). Was ein Server sonst
  anbietet, wird nicht benutzt.
- **Die Antwortgröße wird beim Lesen gezählt** (A2), ohne den Inhalt
  auszuwerten. Wer die Grenze überschreitet, bekommt einen Fehler, nie
  eine abgeschnittene Antwort.

Zugangsdaten gehen in eine Kopfzeile der ausgehenden Anfrage und
nirgendwo sonst hin — in keine Meldung, keine Ausnahme, keine Zeile im
Protokoll. Inhalte werden durchgereicht, nicht aufgeschrieben.
"""
import base64
import http.client
import ipaddress
import json
import os
import socket
import time
from urllib.parse import urlsplit

TESTED = ("2025-03-26", "2025-06-18", "2025-11-25")
NEWEST = TESTED[-1]
CLIENT_INFO = {"name": "oaap-mcp-gateway", "version": "0.2"}
ACCEPT = "application/json, text/event-stream"
CONNECT_TIMEOUT = 10
MAX_TOOLS = 1000                    # je Art: Tools, Resources, Vorlagen, Prompts


class UpstreamError(Exception):
    """Der Server war nicht zu benutzen. `kind` für die Messung, Text für Menschen.

    Der Text nennt nie eine Adresse mit Zugangsdaten und nie einen Inhalt.
    """

    def __init__(self, kind, text):
        super().__init__(text)
        self.kind = kind


class SessionGone(UpstreamError):
    """Der Server kennt die Sitzung nicht mehr (404) — neu anmelden, einmal."""

    def __init__(self):
        super().__init__("session", "Der Server kennt die Sitzung nicht mehr.")


class TooLarge(UpstreamError):
    def __init__(self, limit):
        super().__init__("size", f"Die Antwort des Servers ist größer als {limit} Bytes.")
        self.limit = limit


# ------------------------------------------------------------------ Adresse

def resolve(address, env=None):
    """`destination:<need>` → URL aus der Umgebung; sonst die URL selbst, geprüft."""
    env = os.environ if env is None else env
    address = (address or "").strip()
    if address.startswith("destination:"):
        need = address[len("destination:"):].strip()
        var = "OAAP_DESTINATION_" + need.upper().replace("-", "_") + "_URL"
        url = env.get(var, "")
        if not need or not url:
            raise UpstreamError("address", f"Die Destination „{need}“ ist an diese Instanz "
                                           f"nicht gebunden ({var} fehlt).")
        return url
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UpstreamError("address", "Die Adresse muss mit http:// oder https:// beginnen "
                                       "oder destination:<name> lauten.")
    if parts.username or parts.password:
        raise UpstreamError("address", "Zugangsdaten gehören nicht in die Adresse, "
                                       "sondern in das Feld Zugang.")
    return address


def forbidden_ip(ip):
    ip = ipaddress.ip_address(ip)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast


def check_host(url):
    """Löst den Namen auf, weist verbotene Adressen ab und gibt die Adresse zurück, die gilt.

    Bei jeder Verbindung, nicht nur beim Speichern: Ein Name kann morgen
    woanders hinzeigen. Und verbunden wird mit **genau dieser** Adresse
    (`_connection`), nicht mit einer zweiten Auflösung — sonst könnte der
    Name zwischen Prüfung und Verbindung umspringen (DNS-Rebinding).
    """
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except OSError:
        raise UpstreamError("connect", f"Der Name {parts.hostname} lässt sich nicht "
                                       "auflösen.") from None
    for info in infos:
        if forbidden_ip(info[4][0]):
            raise UpstreamError("address", f"{parts.hostname} zeigt auf eine Adresse, die "
                                           "das Gateway nie anfährt (Loopback, Link-Local).")
    return infos[0][4][0], port


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, **kw):
        super().__init__(host, port, **kw)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        # Zertifikat und SNI gelten dem NAMEN, verbunden ist die geprüfte Adresse.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _connection(url):
    parts = urlsplit(url)
    ip, port = check_host(url)
    cls = _PinnedHTTPS if parts.scheme == "https" else _PinnedHTTP
    path = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    return cls(parts.hostname, port, ip, timeout=CONNECT_TIMEOUT), path


def auth_headers(server):
    """Verfahren T: die Kopfzeile für den technischen Zugang. Leer bei `none`."""
    kind, cred = server.get("auth_kind") or "none", server.get("credential") or ""
    if kind == "none" or not cred:
        return {}
    if kind == "bearer":
        return {"Authorization": "Bearer " + cred}
    if kind == "basic":
        return {"Authorization": "Basic " + base64.b64encode(cred.encode()).decode()}
    if kind.startswith("header:"):
        return {kind[7:]: cred}
    return {}


# ------------------------------------------------------------------ Austausch

class Exchange:
    """Eine Anfrage an den Server und das, was zurückkam.

    `messages` sind die JSON-RPC-Nachrichten in der Reihenfolge des
    Eingangs, `bytes_in` die gezählten Bytes des Körpers.
    """

    def __init__(self):
        self.status = 0
        self.session_id = None
        self.messages = []
        self.bytes_in = 0
        self.streamed = False


def post(server, url, message, session_id=None, version=None, timeout=60,
         max_bytes=1 << 20, on_message=None):
    """Schickt eine JSON-RPC-Nachricht und liest die Antwort.

    Bei einer Anfrage (mit `id`) wird gelesen, bis die Antwort mit dieser
    `id` da ist; jede andere Nachricht davor geht an `on_message`, das
    True zurückgeben kann, um sie in `messages` aufzunehmen. Wirft
    UpstreamError, nie etwas anderes.
    """
    deadline = time.monotonic() + timeout
    conn, path = _connection(url)
    body = json.dumps(message, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": ACCEPT,
               "Content-Length": str(len(body))}
    headers.update(auth_headers(server))
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    if version:
        headers["MCP-Protocol-Version"] = version
    ex = Exchange()
    want = message.get("id") if "method" in message else None
    try:
        try:
            conn.request("POST", path, body=body, headers=headers)
            conn.sock.settimeout(max(0.1, deadline - time.monotonic()))
            resp = conn.getresponse()
        except socket.timeout:
            raise UpstreamError("timeout", "Der Server hat nicht rechtzeitig geantwortet.") from None
        except (OSError, http.client.HTTPException) as exc:
            raise UpstreamError("connect", f"Keine Verbindung zum Server ({type(exc).__name__}).") from None
        ex.status = resp.status
        ex.session_id = resp.getheader("Mcp-Session-Id")
        if 300 <= resp.status < 400:
            raise UpstreamError("redirect", f"Der Server leitet weiter (HTTP {resp.status}); "
                                            "das Gateway folgt keiner Weiterleitung.")
        if resp.status == 404 and session_id:
            raise SessionGone()
        if resp.status in (202, 204):
            return ex
        if resp.status >= 400:
            supported = _supported_versions(resp, conn, deadline) if message.get(
                "method") == "initialize" else None
            if supported:
                raise UpstreamError("version", "Der Server spricht nur die Protokollversionen "
                                    + ", ".join(supported) + "; getestet sind " + ", ".join(TESTED) + ".")
            raise UpstreamError("http", f"Der Server antwortet mit HTTP {resp.status}.")
        ctype = (resp.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "text/event-stream":
            ex.streamed = True
            _read_stream(resp, conn, ex, want, deadline, max_bytes, on_message)
        else:
            raw = _read_limited(resp, conn, deadline, max_bytes)
            ex.bytes_in = len(raw)
            doc = _json(raw)
            if isinstance(doc, list):
                ex.messages.extend(d for d in doc if isinstance(d, dict))
            elif isinstance(doc, dict):
                ex.messages.append(doc)
            elif want is not None:
                raise UpstreamError("protocol", "Der Server antwortet nicht mit JSON-RPC.")
        if want is not None and not any(_is_answer(m, want) for m in ex.messages):
            raise UpstreamError("protocol", "Der Server hat auf die Anfrage nicht geantwortet.")
        return ex
    finally:
        conn.close()


def _supported_versions(resp, conn, deadline):
    """Ein Server der Ära 2026-07-28 nennt beim abgelehnten `initialize` seine Versionen."""
    try:
        doc = _json(_read_limited(resp, conn, deadline, 64 * 1024))
    except UpstreamError:
        return None
    data = ((doc or {}).get("error") or {}).get("data") if isinstance(doc, dict) else None
    found = data.get("supported") if isinstance(data, dict) else None
    if isinstance(found, list) and all(isinstance(v, str) for v in found):
        return [v[:20] for v in found[:10]]
    return None


def _is_answer(msg, want):
    return "method" not in msg and msg.get("id") == want and ("result" in msg or "error" in msg)


def _remaining(conn, deadline):
    left = deadline - time.monotonic()
    if left <= 0:
        raise UpstreamError("timeout", "Der Server hat nicht rechtzeitig geantwortet.")
    if conn.sock:
        conn.sock.settimeout(left)


def _read_limited(resp, conn, deadline, max_bytes):
    chunks, total = [], 0
    while True:
        _remaining(conn, deadline)
        try:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
        except socket.timeout:
            raise UpstreamError("timeout", "Der Server hat nicht rechtzeitig geantwortet.") from None
        except (OSError, http.client.HTTPException) as exc:
            raise UpstreamError("connect", f"Verbindung abgebrochen ({type(exc).__name__}).") from None
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise TooLarge(max_bytes)
        chunks.append(chunk)


def _read_stream(resp, conn, ex, want, deadline, max_bytes, on_message):
    """SSE lesen, Ereignis für Ereignis, bis die Antwort auf `want` da ist."""
    data = []
    while True:
        _remaining(conn, deadline)
        try:
            line = resp.readline(max_bytes + 1)
        except socket.timeout:
            raise UpstreamError("timeout", "Der Server hat nicht rechtzeitig geantwortet.") from None
        except (OSError, http.client.HTTPException) as exc:
            raise UpstreamError("connect", f"Verbindung abgebrochen ({type(exc).__name__}).") from None
        if not line:
            return
        ex.bytes_in += len(line)
        if ex.bytes_in > max_bytes:
            raise TooLarge(max_bytes)
        text = line.decode("utf-8", "replace").rstrip("\r\n")
        if text.startswith("data:"):
            data.append(text[5:].lstrip(" ") if text[5:6] == " " else text[5:])
            continue
        if text or not data:
            continue                             # event:, id:, retry:, Kommentar
        doc = _json("\n".join(data).encode("utf-8"))
        data = []
        for msg in (doc if isinstance(doc, list) else [doc]):
            if not isinstance(msg, dict):
                continue
            if want is not None and _is_answer(msg, want):
                ex.messages.append(msg)
                return
            if on_message is None or on_message(msg):
                ex.messages.append(msg)


def _json(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return None


def delete(server, url, session_id):
    """Sitzung beenden. Fehler sind hier egal: Der Server räumt sonst selbst auf."""
    if not session_id:
        return
    try:
        conn, path = _connection(url)
        headers = {"Mcp-Session-Id": session_id}
        headers.update(auth_headers(server))
        conn.request("DELETE", path, headers=headers)
        conn.getresponse().read()
        conn.close()
    except Exception:
        pass


# ------------------------------------------------------------------ Sitzung

class Session:
    """Eine Sitzung beim Server: Kennung (darf fehlen), ausgehandelte Version, Fähigkeiten des Servers."""

    def __init__(self, session_id, version, server_info, capabilities=None):
        self.id = session_id
        self.version = version
        self.server_info = server_info
        self.capabilities = capabilities or {}


def open_session(server, url, timeout=30, max_bytes=1 << 20, capabilities=None):
    """`initialize` mit der neuesten getesteten Version, dann `notifications/initialized`.

    `capabilities` sind die Client-Fähigkeiten, die das Gateway für diese
    Sitzung zusagt — nur Rückfragen, die die Sicht durchlässt UND der
    Client kann (Spec §6.1). Sonst fragt ein Server gar nicht erst.
    """
    msg = {"jsonrpc": "2.0", "id": 0, "method": "initialize",
           "params": {"protocolVersion": NEWEST, "capabilities": capabilities or {},
                      "clientInfo": CLIENT_INFO}}
    ex = post(server, url, msg, timeout=timeout, max_bytes=max_bytes)
    answer = ex.messages[-1]
    if "error" in answer:
        raise UpstreamError("protocol", "Der Server lehnt die Anmeldung ab: "
                            + str((answer["error"] or {}).get("message", ""))[:200])
    result = answer.get("result") or {}
    version = result.get("protocolVersion")
    if version not in TESTED:
        raise UpstreamError("version", f"Der Server spricht die Protokollversion {version!r}; "
                                       f"getestet sind {', '.join(TESTED)}.")
    info = result.get("serverInfo") if isinstance(result.get("serverInfo"), dict) else {}
    caps = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
    session = Session(ex.session_id, version, info, caps)
    post(server, url, {"jsonrpc": "2.0", "method": "notifications/initialized"},
         session_id=session.id, version=version, timeout=timeout, max_bytes=max_bytes)
    return session


# Was der Katalog liest: Art → (Methode, Feld der Antwort, nötige Fähigkeit des Servers).
LISTS = {"tool": ("tools/list", "tools", "tools"),
         "resource": ("resources/list", "resources", "resources"),
         "template": ("resources/templates/list", "resourceTemplates", "resources"),
         "prompt": ("prompts/list", "prompts", "prompts")}


def _list_all(server, url, session, method, field, timeout, max_bytes, start_id):
    found, cursor, n = [], None, start_id
    while True:
        params = {"cursor": cursor} if cursor else {}
        ex = post(server, url, {"jsonrpc": "2.0", "id": n, "method": method, "params": params},
                  session_id=session.id, version=session.version, timeout=timeout,
                  max_bytes=max_bytes)
        answer = ex.messages[-1]
        if "error" in answer:
            raise UpstreamError("protocol", f"{method} abgelehnt: "
                                + str((answer["error"] or {}).get("message", ""))[:200])
        result = answer.get("result") or {}
        for entry in result.get(field) or ():
            if isinstance(entry, dict):
                found.append(entry)
        if len(found) > MAX_TOOLS:
            raise UpstreamError("protocol", f"Der Server bietet mehr als {MAX_TOOLS} Einträge "
                                            f"({method}).")
        cursor = result.get("nextCursor")
        n += 1
        if not cursor:
            return found, n


def list_catalog(server, url, timeout=30, max_bytes=8 << 20):
    """Alles, was der Server anbietet: {art: [einträge]}. Gibt (offers, session) zurück.

    Eine Art, deren Fähigkeit der Server nicht nennt, ist leer: Er bietet
    sie nicht an, und was davon in einer Sicht liegt, fehlt jetzt — es
    bleibt nicht mit einem alten Stand freigegeben.
    """
    session = open_session(server, url, timeout=timeout)
    offers, n = {}, 1
    try:
        for kind, (method, field, cap) in LISTS.items():
            if cap not in session.capabilities:
                offers[kind] = []
                continue
            offers[kind], n = _list_all(server, url, session, method, field, timeout, max_bytes, n)
        return offers, session
    finally:
        delete(server, url, session.id)
