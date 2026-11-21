#!/usr/bin/env python3
"""vpn-client -- ein Tunnel als App (RFC-0060 Stufe 1, D3).

Haelt einen OpenVPN-Tunnel in seinem eigenen Container (die Plattform gibt
dafuer genau /dev/net/tun und NET_ADMIN, oaap.apps.runtime 2.23) und tut
drei Dinge, sonst nichts:

1. **Haelt den Tunnel** und zeigt seinen Zustand: auf der Seite `/` (nur
   ueber die Plattform-Anmeldung) und in `/healthz` -- 200 nur, solange
   der Tunnel steht.
2. **Traut der Gegenseite nichts.** Geschobene Routen, DNS und Standard-
   Gateway werden verworfen, Skripte aus der fremden Datei auch
   (vpnconf.py). Ein Paketfilter laesst aus dem Tunnel nur Antworten auf
   eigene Verbindungen herein und leitet nichts weiter. Steht der Filter
   nicht, startet der Tunnel nicht.
3. **Prueft seine Ziele** (0.2.0, RFC-0087 §8.1): alle 30 s ein TCP-
   Verbindungsaufbau je Ziel, solange der Tunnel steht, und sagt es an
   seiner Tuer `/oaap/links` (Manifest `link_state`) -- dort fragt der
   Knoten fuer sein Statusdokument, die Zentrale sieht so "VPN aus" und
   "Ziel antwortet nicht".
4. **Bedient benannte Ziele** unter `/t/<name>/...` als schlichter
   Reverse-Proxy: Pfad und Abfrage werden angehaengt, der Host ist der
   des Ziels, Antworten werden ungepuffert durchgereicht (Streaming).
   Keine eigene Anmeldung: den Schluessel ergaenzt das Gateway der
   Plattform, das die Anfrage ueber eine Destination `through` schickt.

Nur Standardbibliothek. Laeuft als root im Container, weil OpenVPN das
Tunnelgeraet einrichtet; der Container selbst hat keine weiteren Rechte.
"""
import collections
import html
import http.client
import http.server
import json
import os
import socket
import socketserver
import ssl
import subprocess
import sys
import threading
import time

import appframe as af
import mcpdoor
import vpnconf

PORT = 8080
RUN = "/run/vpn"
TUN_DEVICE = "/dev/net/tun"
IDENTITY_PREFIX = "x-oaap-"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "trailers", "transfer-encoding", "upgrade", "host",
       "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded"}
VERSION = "0.3.0"
APP_NAME = "VPN-Client"

try:
    TIMEOUT = max(10, min(3600, int(os.environ.get("VPN_TIMEOUT_SECONDS") or 600)))
except ValueError:
    TIMEOUT = 600

CONNECT_TIMEOUT = 10
PROBE_EVERY = 30       # Sekunden zwischen zwei Pruefungen der Ziele
PROBE_TIMEOUT = 5

LOCK = threading.Lock()
STATE = {"state": "startet", "since": "", "remote": "", "tunnel_ip": "",
         "error": "", "attempts": 0, "dropped": [], "filter": "",
         "forward": "", "kind": (os.environ.get("VPN_KIND") or "openvpn").strip()}
LOG = collections.deque(maxlen=40)
TARGETS = {}
TARGET_ERROR = ""
REACH = {}             # name -> {"state": ok|down, "since", "checked", "why"}


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def say(**kw):
    with LOCK:
        STATE.update(kw)


def up():
    with LOCK:
        return STATE["state"] == "verbunden"


# ---------------------------------------------------------------- Filter
def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as e:
        # ein fehlendes Werkzeug ist ein Fehler mit Satz, kein Absturz
        return 127, f"{cmd[0]}: {e}"
    return r.returncode, (r.stderr or r.stdout or "").strip()


def apply_filter():
    """'' oder warum der Filter nicht steht. Aus dem Tunnel herein nur
    Antworten auf eigene Verbindungen; weitergeleitet wird nichts."""
    for tool, required in (("iptables", True), ("ip6tables", False)):
        _run([tool, "-N", "VPNGUARD"])
        steps = [[tool, "-F", "VPNGUARD"],
                 [tool, "-A", "VPNGUARD", "-m", "conntrack", "--ctstate",
                  "ESTABLISHED,RELATED", "-j", "ACCEPT"],
                 [tool, "-A", "VPNGUARD", "-j", "DROP"],
                 [tool, "-P", "FORWARD", "DROP"]]
        for cmd in steps:
            rc, err = _run(cmd)
            if rc != 0:
                if required:
                    return f"{' '.join(cmd)}: {err}"
                break
        else:
            if _run([tool, "-C", "INPUT", "-i", "tun+", "-j", "VPNGUARD"])[0] != 0:
                rc, err = _run([tool, "-I", "INPUT", "1", "-i", "tun+", "-j", "VPNGUARD"])
                if rc != 0 and required:
                    return f"{tool} -I INPUT: {err}"
    return ""


def read_forward():
    try:
        with open("/proc/sys/net/ipv4/ip_forward", encoding="ascii") as f:
            return f.read().strip()
    except OSError:
        return "?"


# ---------------------------------------------------------------- Tunnel
def tunnel_ip():
    rc, out = _run(["ip", "-o", "-4", "addr", "show", "dev", "tun0"])
    if rc != 0:
        return ""
    parts = out.split()
    return parts[parts.index("inet") + 1] if "inet" in parts else ""


def prepare():
    """(Pfad der Datei, '') oder ('', Satz). Schreibt Datei und Zugang."""
    global TARGETS, TARGET_ERROR
    try:
        TARGETS = vpnconf.parse_targets(os.environ.get("VPN_TARGETS", ""))
        TARGET_ERROR = ""
    except vpnconf.ConfigError as e:
        TARGETS, TARGET_ERROR = {}, str(e)
        return "", f"VPN_TARGETS: {e}"
    if STATE["kind"] != "openvpn":
        return "", f"VPN_KIND '{STATE['kind']}' kennt diese Version nicht (nur openvpn)"
    try:
        text = vpnconf.decode_config(os.environ.get("VPN_CONFIG", ""))
    except vpnconf.ConfigError as e:
        return "", str(e)
    if not text:
        return "", "keine VPN_CONFIG gesetzt -- die .ovpn-Datei als base64 eintragen"
    os.makedirs(RUN, mode=0o700, exist_ok=True)
    auth = (os.environ.get("VPN_AUTH") or "").strip()
    auth_file = ""
    if auth:
        if ":" not in auth:
            return "", "VPN_AUTH ist benutzer:passwort"
        user, pw = auth.split(":", 1)
        auth_file = os.path.join(RUN, "auth")
        fd = os.open(auth_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"{user}\n{pw}\n")
    try:
        cfg, dropped = vpnconf.sanitize_config(text, TARGETS, auth_file)
    except vpnconf.ConfigError as e:
        return "", str(e)
    path = os.path.join(RUN, "client.ovpn")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(cfg)
    say(dropped=dropped)
    return path, ""


def on_line(line):
    """Eine Zeile von OpenVPN -> Zustand. True, wenn der Tunnel (wieder) steht.

    OpenVPN startet sich bei einem toten Tunnel INNERHALB des Prozesses neu
    (`SIGUSR1[soft,ping-restart] ... restarting`) -- der Prozess endet nicht.
    Am Prozessende allein erkannt (0.1.0), blieb der Zustand `verbunden`
    stehen und der Proxy schickte Anfragen in einen toten Tunnel (gemessen
    an oaap-test, 2026-10-16). Der Zustand haengt deshalb an diesen Zeilen.
    """
    LOG.append(line)
    if "Peer Connection Initiated with" in line:
        say(remote=line.split("with", 1)[1].strip())
    if "Initialization Sequence Completed" in line:
        say(state="verbunden", since=now(), tunnel_ip=tunnel_ip(), error="")
        return True
    if "process restarting" in line or "Inactivity timeout" in line:
        with LOCK:
            was = STATE["state"]
        say(state="verbindet", tunnel_ip="", error=line,
            **({"since": now()} if was == "verbunden" else {}))
    if "AUTH_FAILED" in line or "Exiting due to fatal error" in line:
        say(error=line)
    return False


def supervise():
    """Haelt OpenVPN am Leben. Ohne Tunnelgeraet wird nicht wiederholt
    (RFC-0060 D6): das ist eine Generalprobe oder eine Installation ohne
    tun, und daran aendert ein neuer Versuch nichts."""
    if not os.path.exists(TUN_DEVICE):
        say(state="kein Tunnelgeraet", since=now(),
            error="Dieser Container hat kein /dev/net/tun. Eine Generalprobe "
                  "bekommt keinen Tunnel; sonst wurde die App ohne Tunnelrecht "
                  "installiert (--confirm-tun). Es wird nicht erneut versucht.")
        return
    say(forward=read_forward())
    why = apply_filter()
    if why:
        say(state="Filter fehlt", since=now(), filter="nicht gesetzt",
            error="Der Paketfilter steht nicht, also startet kein Tunnel: " + why)
        return
    say(filter="gesetzt: aus dem Tunnel nur Antworten, FORWARD verworfen")
    pause = 5
    while True:
        path, why = prepare()
        if why:
            say(state="nicht konfiguriert", since=now(), error=why)
            time.sleep(30)
            continue
        with LOCK:
            STATE["attempts"] += 1
        say(state="verbindet", since=now(), error="")
        proc = subprocess.Popen(["openvpn", "--config", path, "--verb", "3"],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1)
        for line in proc.stdout:
            if on_line(line.rstrip()):
                pause = 5
        rc = proc.wait()
        say(state="getrennt", since=now(), tunnel_ip="",
            error=STATE["error"] or f"openvpn endete mit {rc}")
        time.sleep(pause)
        pause = min(120, pause * 2)


# ---------------------------------------------------------------- Ziele
def probe_target(t, timeout=PROBE_TIMEOUT):
    """('', ok) oder (Grund, False): ein TCP-Verbindungsaufbau zum Ziel,
    durch den Tunnel (die Route je Ziel-IP legt prepare an). Nichts wird
    gesendet -- ein Ziel, das die Verbindung annimmt, antwortet."""
    try:
        socket.create_connection((t["host"], t["port"]), timeout=timeout).close()
        return "", True
    except OSError as e:
        return type(e).__name__, False


def check_targets(probe=probe_target):
    """Eine Runde: jedes Ziel einmal, solange der Tunnel steht. Ohne Tunnel
    sagt die App nichts ueber Ziele -- dann ist der Tunnel das Problem, und
    alte Ergebnisse wuerden luegen."""
    if not up():
        with LOCK:
            REACH.clear()
        return
    stamp = now()
    for name, t in sorted(TARGETS.items()):
        why, alive = probe(t)
        st = "ok" if alive else "down"
        with LOCK:
            old = REACH.get(name) or {}
            REACH[name] = {"state": st, "checked": stamp, "why": why,
                           "since": old["since"] if old.get("state") == st else stamp}
    with LOCK:
        for gone in set(REACH) - set(TARGETS):
            del REACH[gone]


def watch_targets():
    while True:
        try:
            check_targets()
        except Exception as e:  # noqa: BLE001 -- die naechste Runde versucht es wieder
            LOG.append(f"Pruefung der Ziele: {type(e).__name__}")
        time.sleep(PROBE_EVERY)


def link_answer():
    """Die Antwort an der Tuer `link_state` (RFC-0087 §8.1): der eigene Tunnel
    und je Ziel ok/down -- Namen und zwei Woerter, nie eine Adresse."""
    with LOCK:
        tunnel = "ok" if STATE["state"] == "verbunden" else "down"
        targets = {n: {"state": r["state"], "since": r["since"]}
                   for n, r in sorted(REACH.items()) if n in TARGETS}
        since = STATE["since"]
    return {"v": 1, "tunnel": {"state": tunnel, "since": since}, "targets": targets}


# ---------------------------------------------------------------- HTTP
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-vpn-client/" + VERSION

    def log_message(self, fmt, *args):
        # Methode, Pfad bis zum Ziel, Status -- nie Kopfzeilen, nie Inhalt
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def _plain(self, code, text, ctype="text/plain; charset=utf-8"):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _any(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            with LOCK:
                s = dict(STATE)
            body = json.dumps({"ok": s["state"] == "verbunden", "state": s["state"],
                               "since": s["since"]})
            return self._plain(200 if s["state"] == "verbunden" else 503, body,
                               "application/json")
        if path == "/mcp":
            # vor allem anderen: das MCP-Gateway hat die Person angemeldet
            # und reicht sie in den X-OAAP-Kopfzeilen durch.
            return DOOR.serve(self)
        if path == "/oaap/links" and self.command in ("GET", "HEAD"):
            return self._plain(200, json.dumps(link_answer()), "application/json")
        if path.startswith("/t/"):
            return self._proxy()
        if path == "/" and self.command in ("GET", "HEAD"):
            return self._plain(200, page(), "text/html; charset=utf-8")
        return self._plain(404, "Nicht gefunden.\n")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = _any

    def _body(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            data = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(data)
                data += self.rfile.read(size)
                self.rfile.readline()
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def _proxy(self):
        # Nur ueber eine Destination: dort entfernt das Gateway jede
        # Identitaets-Kopfzeile. Wer ueber die Anmelde-Route der App kommt,
        # traegt sie -- und bekommt das Ziel nicht ohne den Schluessel der
        # Destination zu sehen.
        if any(k.lower().startswith(IDENTITY_PREFIX) for k in self.headers.keys()):
            return self._plain(403, "Ziele dieser App sind nur ueber eine Destination "
                                    "der Plattform erreichbar (RFC-0060).\n")
        rest = self.path[len("/t/"):]
        name, _, tail = rest.partition("/")
        name, q, query = name.partition("?")
        t = TARGETS.get(name)
        if t is None:
            return self._plain(404, f"Kein Ziel '{name}' in VPN_TARGETS.\n")
        if not up():
            with LOCK:
                st = STATE["state"]
            return self._plain(502, f"Der Tunnel ist nicht verbunden ({st}) -- "
                                    f"'{name}' ist gerade nicht erreichbar.\n")
        upstream = t["base"] + "/" + tail + (("?" + query) if q else "")
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        default = 443 if t["scheme"] == "https" else 80
        headers["Host"] = t["host"] if t["port"] == default else f"{t['host']}:{t['port']}"
        try:
            body = self._body()
            # Verbinden darf kurz dauern, Antworten lange (ein grosses Modell
            # denkt Minuten): ein Ziel, das gar nicht antwortet, soll nicht
            # TIMEOUT Sekunden lang wie ein langsames aussehen.
            if t["scheme"] == "https":
                conn = http.client.HTTPSConnection(t["host"], t["port"], timeout=CONNECT_TIMEOUT,
                                                   context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(t["host"], t["port"], timeout=CONNECT_TIMEOUT)
            conn.connect()
            conn.sock.settimeout(TIMEOUT)
            conn.request(self.command, upstream, body=body, headers=headers)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as e:
            return self._plain(502, f"'{name}' antwortet nicht durch den Tunnel: "
                                    f"{e.__class__.__name__}\n")
        try:
            self.send_response(resp.status, resp.reason)
            length = resp.getheader("Content-Length")
            for k, v in resp.getheaders():
                if k.lower() not in HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            chunked = length is None and self.command != "HEAD"
            if length is not None:
                self.send_header("Content-Length", length)
            elif chunked:
                self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Connection", "close")
            self.end_headers()
            while self.command != "HEAD":
                chunk = resp.read1(65536)
                if not chunk:
                    break
                if chunked:
                    self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                else:
                    self.wfile.write(chunk)
                self.wfile.flush()
            if chunked:
                self.wfile.write(b"0\r\n\r\n")
        except (OSError, http.client.HTTPException):
            pass
        finally:
            conn.close()
            self.close_connection = True


STATE_WORDS = {"verbunden": ("verbunden", "ok"), "verbindet": ("verbindet …", "warn"),
               "startet": ("startet …", "info"), "getrennt": ("getrennt", "err"),
               "nicht konfiguriert": ("nicht eingerichtet", "warn"),
               "kein Tunnelgeraet": ("kein Tunnelgerät", "err"),
               "Filter fehlt": ("Paketfilter fehlt", "err")}

PAGE_STYLE = (".vpn-log{background:var(--oaap-color-bg);border:1px solid var(--oaap-color-border);"
              "border-radius:6px;padding:.6rem;overflow-x:auto;font-size:.8rem;max-height:24rem}")


def state_badge(state):
    word, kind = STATE_WORDS.get(state, (state, ""))
    return af.badge(word, kind)


def page():
    with LOCK:
        s = dict(STATE)
        reach = {n: dict(r) for n, r in REACH.items()}
    e = af.esc

    def seen(n):
        r = reach.get(n)
        if not r:
            return '<span class="oaap-muted">noch nicht geprüft</span>'
        if r["state"] == "ok":
            b = af.badge("antwortet", "ok")
        else:
            b = af.badge("antwortet nicht", "err") + f' <span class="oaap-muted">({e(r["why"])})</span>'
        return f'{b} <span class="oaap-muted">seit {e(r["since"])}, geprüft {e(r["checked"])}</span>'
    rows = [
        f"<tr><td>{e(n)}</td><td><code>{e(t['url'])}</code></td>"
        f"<td class=\"oaap-minor\">{'eigene Route in den Tunnel' if t['ip'] else 'Name – keine eigene Route, nur wenn der Tunnel ihn ohnehin erreicht'}</td>"
        f"<td>{seen(n)}</td></tr>"
        for n, t in sorted(TARGETS.items())]
    head = "<th>Name</th><th>Adresse hinter dem Tunnel</th><th class=\"oaap-minor\">Weg</th><th>Erreichbar</th>"
    log = e("\n".join(LOG)) or "(noch nichts)"
    tunnel = af.card("Tunnel", (
        f"<p>{state_badge(s['state'])}</p>"
        + af.facts([("seit", e(s['since'] or '–')), ("Gegenstelle", e(s['remote'] or '–')),
                    ("Tunnel-Adresse", e(s['tunnel_ip'] or '–')), ("Versuche", str(s['attempts']))])
        + (af.msg("Letzter Fehler: " + s['error'], error=True) if s['error'] else "")))
    targets = af.card("Ziele", (
        (af.msg(TARGET_ERROR, error=True) if TARGET_ERROR else "")
        + af.table(head, rows, "Noch keine Ziele. In der Konfiguration der Instanz unter "
                   "„Ziele hinter dem Tunnel“ eines je Zeile eintragen, z. B. "
                   "<code>gaming = http://10.8.0.5:11434</code>.")
        + "<p class=\"oaap-muted\">Erreichbar nur über eine Destination der Plattform: "
          "<code>through:&lt;diese Instanz&gt;/&lt;name&gt;</code>. Den Schlüssel des Ziels "
          f"trägt die Destination, nicht diese App. Die Ziele werden alle {PROBE_EVERY} s geprüft.</p>"))
    guard = af.card("Was die Gegenseite nicht darf", af.facts([
        ("Paketfilter", e(s['filter'] or '–')),
        ("Weiterleitung im Container (ip_forward)", e(s['forward'] or '–')),
        ("Aus der Datei verworfen", e(', '.join(s['dropped']) or 'nichts')),
        ("Geschobene Routen und DNS", "werden ignoriert")]))
    proto = af.card("Protokoll (OpenVPN, letzte Zeilen)", f'<pre class="vpn-log">{log}</pre>')
    body = af.pagehead("Zustand des Tunnels") + tunnel + targets + guard + proto
    return af.page("", body, app=APP_NAME, version=VERSION, product="vpn-client",
                   style=PAGE_STYLE)


# ---------------------------------------------------------------- MCP
# Nur lesen, nur für admin (wie die Seite) -- und weniger als die Seite:
# keine Adressen, keine Zeilen aus der Konfigurationsdatei, kein Protokoll.
DOOR = mcpdoor.Door("oaap-vpn-client", VERSION,
                    "Zustand des VPN-Tunnels und seiner Ziele lesen (nur Namen und ok/down, "
                    "keine Adressen, keine Zugangsdaten).")


def _admin(who):
    if "admin" not in (who.get("roles") or []):
        raise mcpdoor.Refused("Den Zustand des Tunnels sehen nur Administratoren.")


@DOOR.tool("vpn_state", "Zustand des Tunnels",
           "Liefert den Zustand des VPN-Tunnels (verbunden oder nicht, seit wann, Versuche, "
           "ob der Paketfilter steht) und je Ziel, ob es antwortet. Enthält keine Adressen "
           "und keine Zugangsdaten.")
def _vpn_state(args, who):
    _admin(who)
    with LOCK:
        s = dict(STATE)
        reach = {n: dict(r) for n, r in REACH.items()}
    word, _kind = STATE_WORDS.get(s["state"], (s["state"], ""))
    return {"tunnel": {"state": "ok" if s["state"] == "verbunden" else "down",
                       "text": word, "since": s["since"], "attempts": s["attempts"],
                       "filter": bool(s["filter"]) and s["filter"].startswith("gesetzt"),
                       "configured": s["state"] != "nicht konfiguriert"},
            "targets": [{"name": n,
                         "state": (reach.get(n) or {}).get("state", "unknown"),
                         "since": (reach.get(n) or {}).get("since", ""),
                         "checked": (reach.get(n) or {}).get("checked", "")}
                        for n in sorted(TARGETS)],
            "targets_error": bool(TARGET_ERROR)}


DOOR.prompt("vpn_check", "Steht der Tunnel?",
            "Prüft, ob der VPN-Tunnel steht und welche Ziele antworten.",
            "Prüfe mit vpn_state, ob der VPN-Tunnel steht und welche Ziele antworten. "
            "Sag in zwei, drei Sätzen, was nicht in Ordnung ist und was ein Administrator "
            "als Nächstes prüfen sollte (Konfiguration der Instanz, Gegenstelle, Ziel).")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    address_family = socket.AF_INET


def main():
    threading.Thread(target=supervise, daemon=True).start()
    threading.Thread(target=watch_targets, daemon=True).start()
    sys.stdout.write(f"vpn-client {VERSION} auf Port {PORT}\n")
    Server(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
