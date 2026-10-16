#!/usr/bin/env python3
"""vpn-client ohne Container und ohne Tunnel (RFC-0060 §2.2).

Geprueft wird, was die App verspricht, an einem echten HTTP-Ziel auf
127.0.0.1 statt hinter einem Tunnel:

  * die fremde .ovpn-Datei verliert alles, womit die Gegenseite in den
    Container hineinregieren koennte, und Inline-Zertifikate bleiben;
  * Ziele: nur http/https, keine Anmeldedaten in der Adresse;
  * /t/<name>/ reicht Pfad, Abfrage und Koerper durch, setzt den Host des
    Ziels, entfernt X-Forwarded-*, streamt ungepuffert;
  * mit Anmelde-Kopfzeilen (ueber die Route der App) -> 403;
  * Tunnel nicht verbunden -> 502 mit Satz; unbekanntes Ziel -> 404;
  * /healthz 200 nur verbunden.

Aufruf: python3 test_vpn_client.py
"""
import base64
import http.client
import http.server
import json
import os
import socketserver
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app            # noqa: E402
import vpnconf        # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:700]}")


def raises(fn, *a):
    try:
        fn(*a)
    except vpnconf.ConfigError as e:
        return str(e)
    return ""


# ------------------------------------------------------------ Konfiguration
print("")
print("Die fremde Datei")
OVPN = """client
dev tun
proto udp
remote vpn.example.net 1194
; ein Kommentar mit ; wie in vielen .ovpn-Dateien
# und einer mit #
redirect-gateway def1
dhcp-option DNS 10.8.0.1
route 192.168.0.0 255.255.0.0
up /etc/openvpn/update-resolv-conf
script-security 2
auth-user-pass
management 0.0.0.0 7505
<ca>
-----BEGIN CERTIFICATE-----
MIIBfake;notacomment
-----END CERTIFICATE-----
</ca>
cipher AES-256-GCM
"""
enc = base64.b64encode(OVPN.encode()).decode()
ok("base64 der Datei wird gelesen", vpnconf.decode_config(enc) == OVPN)
ok("kein base64 -> Satz", "base64" in raises(vpnconf.decode_config, "client\ndev tun"))
ok("base64, aber keine OpenVPN-Datei -> Satz",
   "OpenVPN" in raises(vpnconf.decode_config, base64.b64encode(b"hallo").decode()))
targets = vpnconf.parse_targets("gaming = http://10.8.0.5:11434;  # kommentar\nnas=https://nas.lan/api")
cfg, dropped = vpnconf.sanitize_config(OVPN, targets, "/run/vpn/auth")
lines = cfg.splitlines()
for word in ("redirect-gateway", "dhcp-option", "up", "script-security",
             "management", "auth-user-pass", "route"):
    ok(f"'{word}' aus der fremden Datei ist verworfen und benannt",
       word in dropped, dropped)
ok("keine Zeile der Datei setzt ein Standard-Gateway, DNS oder ein Skript",
   not any(l.startswith(("redirect-gateway", "dhcp-option", "up ", "management"))
           for l in lines), cfg)
ok("die fremde Route 192.168/16 ist weg", "192.168.0.0" not in cfg, cfg)
ok("das Zertifikat bleibt wortgleich (auch mit ';' darin)",
   "MIIBfake;notacomment" in cfg and "<ca>" in lines and "</ca>" in lines)
ok("unsere Regeln stehen am Ende",
   "route-nopull" in lines and "script-security 1" in lines
   and 'pull-filter ignore "redirect-gateway"' in lines
   and "auth-user-pass /run/vpn/auth" in lines, cfg)
ok("genau eine Route je Ziel-IP, keine fuer einen Namen",
   "route 10.8.0.5 255.255.255.255" in lines
   and sum(1 for l in lines if l.startswith("route ")) == 1, cfg)
ok("ein offener Block -> Satz", "<ca>" in raises(vpnconf.sanitize_config, "<ca>\nx", {}, ""))

print("")
print("Ziele")
ok("Ziel mit Anmeldedaten in der Adresse -> Satz",
   "Anmeldedaten" in raises(vpnconf.parse_targets, "x1 = http://u:p@10.0.0.1/"))
ok("Ziel ohne http -> Satz", "http" in raises(vpnconf.parse_targets, "x1 = ftp://10.0.0.1/"))
ok("doppelter Name -> Satz", "zweimal" in raises(vpnconf.parse_targets, "ab=http://a.b;ab=http://c.d"))
ok("Name ausserhalb [a-z0-9-] -> Satz", "Zielname" in raises(vpnconf.parse_targets, "A=http://a.b"))

# ------------------------------------------------------------ Proxy
print("")
print("Der Proxy -- gegen ein echtes Ziel auf 127.0.0.1")
SEEN = []


class Target(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append((self.command, self.path, dict(self.headers)))
        if self.path.startswith("/base/stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for i in range(3):
                piece = f"data: {i}\n\n".encode()
                self.wfile.write(b"%x\r\n" % len(piece) + piece + b"\r\n")
                self.wfile.flush()
                time.sleep(0.4)
            self.wfile.write(b"0\r\n\r\n")
            return
        body = json.dumps({"path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        SEEN.append((self.command, self.path, dict(self.headers), data))
        self.send_response(201)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


tsrv = TS(("127.0.0.1", 0), Target)
threading.Thread(target=tsrv.serve_forever, daemon=True).start()
tport = tsrv.server_address[1]
asrv = app.Server(("127.0.0.1", 0), app.Handler)
threading.Thread(target=asrv.serve_forever, daemon=True).start()
aport = asrv.server_address[1]
app.TARGETS = vpnconf.parse_targets(f"gaming = http://127.0.0.1:{tport}/base")


def call(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", aport, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, data, dict(r.getheaders())


app.say(state="getrennt")
st, data, _ = call("GET", "/t/gaming/v1/models")
ok("Tunnel nicht verbunden: 502 mit Satz, das Ziel sieht nichts",
   st == 502 and "nicht verbunden" in data.decode() and not SEEN, (st, data))
st, data, _ = call("GET", "/healthz")
ok("/healthz ohne Tunnel: 503", st == 503 and json.loads(data)["ok"] is False)

app.say(state="verbunden")
st, data, _ = call("GET", "/healthz")
ok("/healthz mit Tunnel: 200", st == 200 and json.loads(data)["ok"] is True)
st, data, _ = call("GET", "/t/gaming/v1/models?x=1&y=2",
                   {"Authorization": "Bearer k3", "X-Forwarded-For": "1.2.3.4",
                    "X-Forwarded-Host": "evil"})
seen = SEEN[-1] if SEEN else ("", "", {})
ok("Pfad und Abfrage kommen beim Ziel an, unter der Basis",
   st == 200 and seen[1] == "/base/v1/models?x=1&y=2", (st, seen))
ok("der Host ist der des Ziels", seen[2].get("Host") == f"127.0.0.1:{tport}", seen[2])
ok("der Schluessel der Destination wird durchgereicht",
   seen[2].get("Authorization") == "Bearer k3", seen[2])
ok("X-Forwarded-* kommt nicht an",
   not any(k.lower().startswith("x-forwarded") for k in seen[2]), seen[2])

st, data, _ = call("POST", "/t/gaming/v1/chat", {"Content-Type": "application/json"},
                   b'{"m":"hallo"}')
ok("ein Koerper kommt unveraendert an und zurueck",
   st == 201 and data == b'{"m":"hallo"}' and SEEN[-1][3] == b'{"m":"hallo"}', (st, data))

# Streaming: die erste Zeile muss ankommen, bevor das Ziel fertig ist.
c = http.client.HTTPConnection("127.0.0.1", aport, timeout=10)
t0 = time.monotonic()
c.request("GET", "/t/gaming/stream")
r = c.getresponse()
first = r.read1(64)
t_first = time.monotonic() - t0
rest = r.read()
t_all = time.monotonic() - t0
c.close()
ok("Streaming: das erste Stueck kommt vor dem Ende (ungepuffert)",
   first.startswith(b"data: 0") and t_first < 0.35 and t_all > 0.7,
   (first, round(t_first, 2), round(t_all, 2)))
ok("... und alles kommt an", (first + rest).count(b"data:") == 3, first + rest)

n = len(SEEN)
st, data, _ = call("GET", "/t/gaming/v1/models", {"X-OAAP-User": "joerg"})
ok("mit Anmelde-Kopfzeile (ueber die Route der App): 403, das Ziel sieht nichts",
   st == 403 and len(SEEN) == n, (st, data))
st, data, _ = call("GET", "/t/nope/x")
ok("unbekanntes Ziel: 404 mit Satz", st == 404 and "nope" in data.decode())
st, data, _ = call("GET", "/")
ok("die Seite zeigt Zustand und Ziel, keinen Schluessel",
   st == 200 and "verbunden" in data.decode() and "gaming" in data.decode()
   and "k3" not in data.decode())

print("")
print("FAIL" if fails else "OK", f"-- {fails} Fehler" if fails else "")
sys.exit(1 if fails else 0)
