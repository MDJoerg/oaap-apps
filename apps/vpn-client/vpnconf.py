"""Was der VPN-Client aus seiner Konfiguration macht (RFC-0060 §2.2).

Reine Funktionen ohne Netz und ohne Prozesse, damit sie ohne Container
getestet werden koennen:

  * `parse_targets`   -- VPN_TARGETS, eine Zeile `name = http://adresse`
  * `decode_config`   -- VPN_CONFIG, die .ovpn-Datei als base64 (eine Zeile:
                         die Plattform traegt Konfigwerte einzeilig, und eine
                         .ovpn-Datei hat Zeilen, `;`-Kommentare und Zertifikate)
  * `sanitize_config` -- die fremde Datei, aus der alles entfernt ist, womit
                         die Gegenseite in unseren Container hineinregieren
                         koennte, plus unsere eigenen Regeln

Eine fremde VPN-Gegenstelle ist eine fremde Autoritaet: was sie schiebt
(Routen, DNS, Standard-Gateway) und was in ihrer Datei an Skripten steht,
ist Datenmaterial, kein Befehl.
"""
import base64
import binascii
import ipaddress
import re
import urllib.parse

NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,38}[a-z0-9]")

# Direktiven einer fremden .ovpn-Datei, die wir NICHT uebernehmen:
#   - alles, was Routen, Gateway oder DNS setzt (die Gegenseite bestimmt
#     nicht, wohin unser Container sonst noch spricht);
#   - alles, was Programme startet (up/down/... laufen als root im Container);
#   - alles, was Verwaltung, Protokoll oder Dienstbetrieb umlenkt, weil
#     das unsere eigene Steuerung ist.
DROPPED = {
    "route", "route-ipv6", "redirect-gateway", "redirect-private",
    "dhcp-option", "block-outside-dns", "route-gateway", "route-metric",
    "route-delay", "route-up", "route-pre-down", "ifconfig-ipv6",
    "up", "down", "ipchange", "client-connect", "client-disconnect",
    "learn-address", "tls-verify", "auth-user-pass-verify", "script-security",
    "plugin", "management", "management-client", "management-hold",
    "management-query-passwords", "log", "log-append", "status", "daemon",
    "writepid", "cd", "chroot", "setenv", "setenv-safe", "pull-filter",
    "route-nopull", "auth-user-pass", "askpass", "config", "keepalive",
    "ping", "ping-restart", "ping-exit",
}

# Inline-Bloecke (<ca> ... </ca>) werden unveraendert uebernommen.
_BLOCK_OPEN = re.compile(r"^<([a-z0-9-]+)>$")


class ConfigError(ValueError):
    """Ein Satz, der sagt, was an der Konfiguration nicht stimmt."""


def parse_targets(text):
    """{name: {url, scheme, host, port, base, ip}} aus VPN_TARGETS.

    Eine Zeile je Ziel: `gaming = http://10.8.0.5:11434[/basis]`. Getrennt
    wird an Zeilenumbruechen UND an `;` -- die Plattform liefert eine
    mehrzeilige Liste als `;`-getrennte Zeile (oaap.apps.runtime 2.8).
    Ein Fehler wirft ConfigError mit der Zeile, nie ein stilles Weglassen.
    """
    out = {}
    for raw in (text or "").replace(";", "\n").split("\n"):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"Ziel ohne '=': {line!r} (erwartet: name = http://adresse)")
        name, url = (x.strip() for x in line.split("=", 1))
        if not NAME_RE.fullmatch(name):
            raise ConfigError(f"Zielname {name!r}: [a-z0-9-], 2-40 Zeichen")
        if name in out:
            raise ConfigError(f"Ziel {name!r} steht zweimal da")
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise ConfigError(f"Ziel {name!r}: eine http- oder https-Adresse mit Host")
        if u.username or u.password or u.query or u.fragment:
            raise ConfigError(f"Ziel {name!r}: keine Anmeldedaten, keine Abfrage, "
                              "kein Fragment in der Adresse -- den Schluessel traegt "
                              "die Destination, nicht die App (RFC-0060 D5)")
        try:
            ip = ipaddress.ip_address(u.hostname)
        except ValueError:
            ip = None
        out[name] = {"url": url, "scheme": u.scheme, "host": u.hostname,
                     "port": u.port or (443 if u.scheme == "https" else 80),
                     "base": u.path.rstrip("/"), "ip": ip}
    return out


def decode_config(value):
    """Der Text der .ovpn-Datei aus VPN_CONFIG (base64, eine Zeile)."""
    v = (value or "").strip()
    if not v:
        return ""
    try:
        raw = base64.b64decode(v, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError("VPN_CONFIG ist kein base64 -- erwartet wird die "
                          ".ovpn-Datei als eine Zeile: base64 -w0 client.ovpn") from None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ConfigError("VPN_CONFIG ist base64, aber kein Text") from None
    if not re.search(r"^\s*(remote|client|dev)\b", text, re.M):
        raise ConfigError("VPN_CONFIG sieht nicht wie eine OpenVPN-Datei aus "
                          "(keine Zeile remote/client/dev)")
    return text


def sanitize_config(text, targets, auth_file=""):
    """(neue Datei, [verworfene Direktiven]).

    Kommentare und Leerzeilen fallen weg, Inline-Bloecke bleiben wortgleich,
    jede Direktive aus DROPPED wird verworfen und benannt. Angehaengt
    werden unsere Regeln: keine geschobenen Routen und kein DNS, keine
    Skripte, und genau eine Route je Ziel-Adresse in den Tunnel.
    """
    keep, dropped = [], []
    block = None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if block:
            keep.append(raw.rstrip())
            if line == f"</{block}>":
                block = None
            continue
        m = _BLOCK_OPEN.match(line)
        if m:
            block = m.group(1)
            keep.append(line)
            continue
        if not line or line[0] in "#;":
            continue
        word = line.split()[0].lower().lstrip("-")
        if word in DROPPED:
            dropped.append(word)
            continue
        keep.append(line)
    if block:
        raise ConfigError(f"Block <{block}> wird nicht geschlossen")
    keep += [
        "# --- vpn-client (RFC-0060): die Gegenseite regiert hier nicht",
        "route-nopull",
        'pull-filter ignore "redirect-gateway"',
        'pull-filter ignore "dhcp-option"',
        'pull-filter ignore "block-outside-dns"',
        "script-security 1",
        # einen toten Tunnel selbst erkennen, nicht auf ein geschobenes
        # ping-restart der Gegenseite warten (das kann fehlen)
        "keepalive 10 60",
    ]
    if auth_file:
        keep.append(f"auth-user-pass {auth_file}")
    for t in targets.values():
        ip = t.get("ip")
        if ip is not None and ip.version == 4:
            keep.append(f"route {ip} 255.255.255.255")
        elif ip is not None:
            keep.append(f"route-ipv6 {ip}/128")
    return "\n".join(keep) + "\n", sorted(set(dropped))
