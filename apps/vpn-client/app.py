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
3. **Bedient benannte Ziele** unter `/t/<name>/...` als schlichter
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

import vpnconf

PORT = 8080
RUN = "/run/vpn"
TUN_DEVICE = "/dev/net/tun"
IDENTITY_PREFIX = "x-oaap-"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailer", "trailers", "transfer-encoding", "upgrade", "host",
       "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "forwarded"}
VERSION = "0.1.0"

try:
    TIMEOUT = max(10, min(3600, int(os.environ.get("VPN_TIMEOUT_SECONDS") or 600)))
except ValueError:
    TIMEOUT = 600

LOCK = threading.Lock()
STATE = {"state": "startet", "since": "", "remote": "", "tunnel_ip": "",
         "error": "", "attempts": 0, "dropped": [], "filter": "",
         "forward": "", "kind": (os.environ.get("VPN_KIND") or "openvpn").strip()}
LOG = collections.deque(maxlen=40)
TARGETS = {}
TARGET_ERROR = ""


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
    r = subprocess.run(cmd, capture_output=True, text=True)
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


def supervise():
    """Haelt OpenVPN am Leben. Ohne Tunnelgeraet wird nicht wiederholt
    (RFC-0060 D6): das ist eine Generalprobe oder eine Installation ohne
    tun, und daran aendert ein neuer Versuch nichts."""
    if not os.path.exists(TUN_DEVICE):
        say(state="kein Tunnelgeraet", since=now(),
            error="Dieser Container hat kein /dev/net/tun. Eine Generalprobe "
                  "bekommt keinen Tunnel (RFC-0060 D6); sonst wurde die App ohne "
                  "--confirm-tun installiert. Es wird nicht erneut versucht.")
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
            line = line.rstrip()
            LOG.append(line)
            if "Peer Connection Initiated with" in line:
                say(remote=line.split("with", 1)[1].strip())
            if "Initialization Sequence Completed" in line:
                say(state="verbunden", since=now(), tunnel_ip=tunnel_ip(), error="")
                pause = 5
            if "AUTH_FAILED" in line or "Exiting due to fatal error" in line:
                say(error=line)
        rc = proc.wait()
        say(state="getrennt", since=now(), tunnel_ip="",
            error=STATE["error"] or f"openvpn endete mit {rc}")
        time.sleep(pause)
        pause = min(120, pause * 2)


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
            if t["scheme"] == "https":
                conn = http.client.HTTPSConnection(t["host"], t["port"], timeout=TIMEOUT,
                                                   context=ssl.create_default_context())
            else:
                conn = http.client.HTTPConnection(t["host"], t["port"], timeout=TIMEOUT)
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


def page():
    with LOCK:
        s = dict(STATE)
    e = html.escape
    rows = "".join(
        f"<tr><td>{e(n)}</td><td><code>{e(t['url'])}</code></td>"
        f"<td>{'eigene Route in den Tunnel' if t['ip'] else 'Name -- keine eigene Route, nur wenn der Tunnel ihn ohnehin erreicht'}</td></tr>"
        for n, t in sorted(TARGETS.items()))
    log = e("\n".join(LOG)) or "(noch nichts)"
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VPN-Client</title><style>
body{{font-family:system-ui,sans-serif;max-width:60rem;margin:1.5rem auto;padding:0 1rem;color:#1d2433;background:#fff}}
@media (prefers-color-scheme:dark){{body{{color:#e6e9ef;background:#151a23}}code,pre{{background:#232a36}}}}
code,pre{{background:#f1f3f6;padding:.1rem .3rem;border-radius:4px}} pre{{padding:.6rem;overflow-x:auto;font-size:.8rem}}
table{{border-collapse:collapse;width:100%}} td,th{{text-align:left;padding:.3rem .5rem;border-bottom:1px solid #8884}}
.s{{font-size:1.3rem;font-weight:600}}</style></head><body>
<h1>VPN-Client</h1>
<p class="s">Tunnel: {e(s['state'])}</p>
<p>seit {e(s['since'] or '–')} · Gegenstelle {e(s['remote'] or '–')} · Tunnel-Adresse {e(s['tunnel_ip'] or '–')} · Versuche {s['attempts']}</p>
<p>{('<strong>Letzter Fehler:</strong> ' + e(s['error'])) if s['error'] else ''}</p>
<h2>Ziele</h2>
{('<p><strong>' + e(TARGET_ERROR) + '</strong></p>') if TARGET_ERROR else ''}
<table><tr><th>Name</th><th>Adresse hinter dem Tunnel</th><th>Weg</th></tr>{rows or '<tr><td colspan=3>keine</td></tr>'}</table>
<p>Erreichbar nur über eine Destination der Plattform: <code>--target through:&lt;diese Instanz&gt;/&lt;name&gt;</code>.
Den Schlüssel des Ziels trägt die Destination, nicht diese App.</p>
<h2>Was die Gegenseite nicht darf</h2>
<p>Paketfilter: {e(s['filter'] or '–')} · ip_forward im Container: {e(s['forward'] or '–')}</p>
<p>Aus der Datei verworfen: {e(', '.join(s['dropped']) or 'nichts')} · geschobene Routen und DNS werden ignoriert.</p>
<h2>Protokoll (OpenVPN, letzte Zeilen)</h2><pre>{log}</pre>
<p><small>vpn-client {VERSION} · RFC-0060</small></p></body></html>"""


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    address_family = socket.AF_INET


def main():
    threading.Thread(target=supervise, daemon=True).start()
    sys.stdout.write(f"vpn-client {VERSION} auf Port {PORT}\n")
    Server(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
