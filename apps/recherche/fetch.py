"""web_fetch: eine Seite als Text -- ohne eine Tür ins eigene Netz zu sein (RFC-0116 §2.3).

Die Regeln, die das Design tragen:

* Nur http und https, nur die Ports 80 und 443 (D6), keine Zugangsdaten in der Adresse.
* Der Name wird HIER aufgelöst, und jede Adresse muss global sein: keine Loopback-,
  privaten, Link-local-, Multicast- oder reservierten Bereiche. Die Verbindung geht dann
  an GENAU die geprüfte Adresse (Host-Kopfzeile und SNI tragen den Namen) -- ein zweites
  Auflösen zwischen Prüfung und Verbindung (DNS-Rebinding) gibt es nicht.
* Die Adressen des eigenen Knotens sind gesperrt, auch wenn sie öffentlich sind: wer sie
  liest, liest die Apps des Knotens unter ihren öffentlichen Namen. Welche das sind, sagen
  die eigenen Namen der Instanz (OAAP_INSTANCE_NAMES) -- aufgelöst, nicht geraten.
* Jede Umleitung (höchstens fünf) wird genauso neu geprüft.
* Höchstens 2 MB, nur Text, HTML und JSON (PDF später, D7).
* Jede Ablehnung ist ein Satz, nie eine leere Antwort.

Der Text: der Hauptteil der Seite (main/article, sonst body) ohne Skripte, Stile,
Navigation; höchstens 12.000 Zeichen -- so viel nimmt der Chat von einem Ergebnis.
"""
import hashlib
import html.parser
import http.client
import ipaddress
import re
import socket
import ssl
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote, urljoin, urlsplit

MAX_BYTES = 2 * 1024 * 1024
MAX_TEXT = 12_000
MAX_REDIRECTS = 5
TIMEOUT = 15
PORTS = {"http": 80, "https": 443}
TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/json")
CACHE_SECONDS = 3600          # D8: eine Stunde, nach dem Hash der Adresse
CACHE_MAX = 200
UA = "oaap-recherche/0.1 (+https://github.com/MDJoerg/oaap-apps)"


class Refused(Exception):
    """Ein Satz für die Person und das Modell; `reason` ist ein kurzes Wort für die Zählung."""

    def __init__(self, text, reason):
        super().__init__(text)
        self.text, self.reason = text, reason


def domains(raw):
    """'a.de, b.com' → ['a.de', 'b.com'] (klein, ohne Punkt vorn)."""
    return [d.strip().lower().lstrip(".") for d in (raw or "").replace(";", ",").split(",")
            if d.strip()]


def under(host, dom):
    return host == dom or host.endswith("." + dom)


def is_public(ip):
    """Global erreichbar und kein Sonderbereich. IPv4 in IPv6 wird als IPv4 beurteilt."""
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback
                                or a.is_link_local or a.is_private)


def v4_first(ips):
    """IPv4 vor IPv6: ein Container hat oft keine IPv6-Route (gemessen auf oaap-test)."""
    return sorted(set(ips), key=lambda ip: (":" in ip, ip))


def system_resolve(host, port):
    return v4_first(ai[4][0] for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))


def node_hosts(names_env):
    """Die Namen aus OAAP_INSTANCE_NAMES ('https://a.b.c,https://x.y') ohne Schema."""
    out = []
    for n in (names_env or "").split(","):
        n = n.strip()
        if not n:
            continue
        host = urlsplit(n if "://" in n else "http://" + n).hostname
        if host:
            out.append(host.lower())
    return out


def _direct(ip, port):
    return ip, port


class _Pinned(http.client.HTTPConnection):
    def __init__(self, host, ip, port, timeout, dial=_direct):
        super().__init__(host, port, timeout=timeout)
        self._ip, self._dial = ip, dial

    def connect(self):
        self.sock = socket.create_connection(self._dial(self._ip, self.port), self.timeout)


class _PinnedTLS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout, context, dial=_direct):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip, self._dial = ip, dial

    def connect(self):
        raw = socket.create_connection(self._dial(self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class Text(html.parser.HTMLParser):
    """HTML → Absätze. Verwirft, was keine Lesestelle ist; merkt sich main/article."""

    SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header",
            "aside", "form", "iframe", "button", "select", "canvas", "head"}
    BLOCK = {"p", "div", "section", "article", "main", "br", "li", "ul", "ol", "tr", "table",
             "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr",
             "figcaption", "td", "th"}
    VOID = {"br", "hr", "img", "meta", "link", "input", "source", "wbr", "area", "base",
            "col", "embed", "param", "track"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.stack = []
        self.parts = []
        self.main = []          # Teile innerhalb von main/article
        self.in_main = 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        if tag in self.VOID:
            if tag in ("br", "hr") and not self.skip:
                self._add("\n")
            return
        self.stack.append(tag)
        if tag in self.SKIP and tag != "head":
            self.skip += 1
        if tag in ("main", "article"):
            self.in_main += 1
        if not self.skip and tag in self.BLOCK:
            self._add("\n")
        if not self.skip and tag in ("h1", "h2", "h3"):
            self._add("# " if tag == "h1" else "## " if tag == "h2" else "### ")
        if not self.skip and tag == "li":
            self._add("- ")

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag not in self.stack:
            return
        while self.stack:
            t = self.stack.pop()
            if t in self.SKIP and t != "head":
                self.skip = max(0, self.skip - 1)
            if t in ("main", "article"):
                self.in_main = max(0, self.in_main - 1)
            if not self.skip and t in self.BLOCK:
                self._add("\n")
            if t == tag:
                break

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self.skip or "head" in self.stack:
            return
        self._add(data)

    def _add(self, s):
        self.parts.append(s)
        if self.in_main:
            self.main.append(s)

    @staticmethod
    def _clean(parts):
        text = "".join(parts)
        lines = [re.sub(r"[ \t\r\f\v ]+", " ", ln).strip() for ln in text.split("\n")]
        out, blank = [], 0
        for ln in lines:
            if ln in ("", "-", "#", "##", "###"):
                blank += 1
                continue
            if blank and out:
                out.append("")
            blank = 0
            out.append(ln)
        return "\n".join(out).strip()

    def result(self):
        main = self._clean(self.main)
        body = self._clean(self.parts)
        # main/article nur, wenn er die Seite trägt -- ein Teaser-<article> ist es nicht
        return main if len(main) >= 400 or (main and len(main) >= 0.5 * len(body)) else body


def charset_of(ctype, head):
    m = re.search(r"charset=([\w.-]+)", ctype or "", re.I)
    if not m:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", head[:4096], re.I)
        name = m.group(1).decode("ascii", "replace") if m else "utf-8"
    else:
        name = m.group(1)
    try:
        "".encode(name)
        return name
    except LookupError:
        return "utf-8"


class Fetcher:
    """`resolve`, `public` und `dial` sind nur für die Tests austauschbar, nie über die
    Umgebung: `dial` verbindet die geprüfte Adresse mit einem Testport."""

    def __init__(self, deny="", allow="", names_env="", resolve=None, public=None,
                 context=None, clock=time.time, dial=None):
        self.deny, self.allow = domains(deny), domains(allow)
        self.resolve = resolve or system_resolve
        self.public = public or is_public
        self.context = context or ssl.create_default_context()
        self.clock = clock
        self.dial = dial or _direct
        self.cache, self.lock = {}, threading.Lock()
        self._names = node_hosts(names_env)
        self._node_ips = None

    def node_ips(self):
        """Die Adressen des eigenen Knotens, einmal aufgelöst (beim ersten Abruf)."""
        if self._node_ips is None:
            ips = set()
            for h in self._names:
                try:
                    ips.update(self.resolve(h, 443))
                except OSError:
                    pass
            self._node_ips = ips
        return self._node_ips

    def check(self, url):
        """(scheme, host, port, ips, path) einer erlaubten Adresse -- oder Refused. `ips`
        sind ALLE Adressen des Namens, jede geprüft, IPv4 zuerst."""
        if not isinstance(url, str) or not url.strip() or len(url) > 2000:
            raise Refused("url fehlt oder ist länger als 2000 Zeichen.", "url")
        try:
            u = urlsplit(url.strip())
            port = u.port
        except ValueError:
            raise Refused("Diese Adresse ist keine gültige URL.", "url") from None
        scheme = (u.scheme or "").lower()
        if scheme not in PORTS:
            raise Refused(f"Nur http- und https-Adressen werden gelesen, nicht „{scheme or '?'}:“.",
                          "scheme")
        if u.username or u.password:
            raise Refused("Eine Adresse mit Zugangsdaten (name@…) wird nicht gelesen.", "userinfo")
        host = (u.hostname or "").lower().rstrip(".")
        if not host:
            raise Refused("Die Adresse nennt keinen Rechner.", "url")
        try:
            # ein Name mit Umlauten (IDN) wird so aufgelöst und gesendet, wie DNS ihn kennt
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise Refused("Dieser Rechnername ist kein gültiger Domainname.", "url") from None
        port = port or PORTS[scheme]
        if port not in (80, 443):
            raise Refused(f"Nur die Ports 80 und 443 werden gelesen, nicht {port} (RFC-0116 D6).",
                          "port")
        if any(under(host, d) for d in self.deny):
            raise Refused(f"{host} steht auf der Sperrliste dieser Instanz (RECHERCHE_DENY).", "deny")
        if self.allow and not any(under(host, d) for d in self.allow):
            raise Refused(f"{host} steht nicht auf der Liste erlaubter Seiten dieser Instanz "
                          "(RECHERCHE_ALLOW).", "allow")
        if host in self._names or host == "localhost" or host.endswith(".localhost") \
                or host.endswith(".internal") or host.endswith(".local") or "." not in host:
            raise Refused(f"{host} ist ein Name im eigenen Netz oder des eigenen Knotens; "
                          "den liest die Recherche nicht.", "inside")
        try:
            ips = self.resolve(host, port)
        except (OSError, UnicodeError):
            raise Refused(f"Den Namen {host} kennt das DNS nicht (oder diese Instanz erreicht "
                          "kein DNS).", "dns") from None
        if not ips:
            raise Refused(f"Den Namen {host} kennt das DNS nicht.", "dns")
        for ip in ips:
            try:
                ok = self.public(ip)
            except ValueError:
                ok = False
            if not ok:
                raise Refused(f"{host} zeigt auf eine Adresse im eigenen oder einem privaten Netz "
                              f"({ip}); die liest die Recherche nicht.", "inside")
        if set(ips) & self.node_ips():
            raise Refused(f"{host} zeigt auf diesen Knoten; die Apps des eigenen Knotens liest "
                          "die Recherche nicht.", "inside")
        path = quote((u.path or "/") + (("?" + u.query) if u.query else ""),
                     safe="/?&=%:@!$'()*+,;~-._")
        return scheme, host, port, v4_first(ips), path

    def _get(self, scheme, host, port, ips, path):
        """Der Reihe nach an jede geprüfte Adresse, bis eine annimmt."""
        last = None
        for ip in ips:
            try:
                return self._get_one(scheme, host, port, ip, path)
            except ssl.SSLError:
                raise
            except OSError as e:
                last = e
        raise last or OSError("keine Adresse")

    def _get_one(self, scheme, host, port, ip, path):
        if scheme == "https":
            c = _PinnedTLS(host, ip, port, TIMEOUT, self.context, self.dial)
        else:
            c = _Pinned(host, ip, port, TIMEOUT, self.dial)
        c.request("GET", path, headers={
            "Host": host if port == PORTS[scheme] else f"{host}:{port}",
            "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,"
                                        "application/json;q=0.8",
            "Accept-Language": "de,en;q=0.8", "Accept-Encoding": "identity"})
        return c, c.getresponse()

    def fetch(self, url):
        """Das Ergebnis als Abbildung -- oder Refused."""
        key = hashlib.sha256(url.strip().encode("utf-8", "replace")).hexdigest()
        now = self.clock()
        with self.lock:
            hit = self.cache.get(key)
            if hit and now - hit[0] < CACHE_SECONDS:
                return dict(hit[1], cached=True)
        current = url.strip()
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, ips, path = self.check(current)
            try:
                conn, r = self._get(scheme, host, port, ips, path)
            except ssl.SSLError as e:
                raise Refused(f"Die TLS-Verbindung zu {host} ist gescheitert ({e.reason or e}).",
                              "tls") from None
            except (OSError, http.client.HTTPException) as e:
                raise Refused(f"{host} hat nicht geantwortet ({type(e).__name__}). Ist die Seite "
                              "erreichbar? In einem geschlossenen Mandanten erreicht diese "
                              "Instanz das Internet nicht.", "connect") from None
            try:
                if r.status in (301, 302, 303, 307, 308):
                    loc = r.getheader("Location") or ""
                    if not loc:
                        raise Refused(f"{host} leitet um, nennt aber kein Ziel.", "redirect")
                    current = urljoin(f"{scheme}://{host}{path}", loc)
                    continue
                if r.status >= 400:
                    raise Refused(f"{host} antwortet mit {r.status} {r.reason}.", "status")
                ctype = (r.getheader("Content-Type") or "").lower()
                base = ctype.split(";")[0].strip()
                if base and base not in TYPES:
                    raise Refused(f"Die Seite ist {base}; gelesen werden nur Text, HTML und JSON "
                                  "(PDF kommt später, RFC-0116 D7).", "type")
                n = r.getheader("Content-Length")
                if n and n.isdigit() and int(n) > MAX_BYTES:
                    raise Refused(f"Die Seite ist größer als 2 MB ({int(n) // 1024} KB); sie wird "
                                  "nicht gelesen.", "size")
                body = r.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    raise Refused("Die Seite ist größer als 2 MB; sie wird nicht gelesen.", "size")
            finally:
                conn.close()
            raw = body.decode(charset_of(ctype, body), "replace")
            if base in ("text/html", "application/xhtml+xml") or (not base and "<html" in raw[:2000].lower()):
                p = Text()
                try:
                    p.feed(raw)
                    p.close()
                except Exception:   # noqa: BLE001 -- ein kaputtes HTML ist kein Absturz
                    pass
                text, title = p.result(), re.sub(r"\s+", " ", p.title).strip()
            else:
                text, title = raw.strip(), ""
            cut = len(text) > MAX_TEXT
            res = {"url": url.strip(), "final_url": f"{scheme}://{host}{'' if port == PORTS[scheme] else ':' + str(port)}{path}",
                   "title": title[:300], "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "text": text[:MAX_TEXT], "truncated": cut}
            with self.lock:
                if len(self.cache) >= CACHE_MAX:
                    for k in sorted(self.cache, key=lambda k: self.cache[k][0])[:CACHE_MAX // 4]:
                        self.cache.pop(k, None)
                self.cache[key] = (now, res)
            return res
        raise Refused(f"Mehr als {MAX_REDIRECTS} Umleitungen; die Seite wird nicht gelesen.", "redirect")
