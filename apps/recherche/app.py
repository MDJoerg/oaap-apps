"""recherche -- Websuche und Webseiten als MCP-Quelle (RFC-0116 Stufe 1).

  /mcp          MCP (Streamable HTTP, JSON): web_search, web_fetch
  /             eine Seite: was die App tut, wohin sie fragt, was sie gezählt hat
  /api/status   dasselbe als JSON
  /healthz      lebt sie?

Keine eigene KI, kein Speicher außer einem Zwischenspeicher im Arbeitsspeicher (eine
Stunde, nach dem Hash der Adresse). Kein Suchbegriff, keine Adresse und kein Seiteninhalt
landet in einem Protokoll (§2.4): gezählt wird je Tag und Werkzeug, und Ablehnungen nach
ihrem Grund.
"""
import html
import json
import os
import sys
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import appframe as af
import fetch as fx
import mcp_door
import search as sx

VERSION = "0.3.0"
APP_NAME = "Recherche"
PORT = int(os.environ.get("PORT", "8000"))
BODY_LIMIT = 256 * 1024
# Kit und Rahmen der Plattform (RFC-0118): Stil, Skript und Abfragen nur vom eigenen Ursprung
CSP = ("default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'")


def on(v, default="on"):
    return (v if v is not None else default).strip().lower() in ("on", "1", "true", "yes", "ja")


class State:
    """Einstellungen aus der Umgebung, der Abrufer und die Zählung."""

    def __init__(self, env=os.environ, fetcher=None):
        self.env = env
        self.fetcher = fetcher or fx.Fetcher(deny=env.get("RECHERCHE_DENY", ""),
                                             allow=env.get("RECHERCHE_ALLOW", ""),
                                             names_env=env.get("OAAP_INSTANCE_NAMES", ""))
        self.lock = threading.Lock()
        self.counts = {}      # (tag, werkzeug) -> {"ok": n, "refused": {grund: n}}

    def closed(self):
        return (self.env.get("OAAP_EGRESS") or "").strip().lower() == "closed"

    def fetch_on(self):
        # §4.7: in einem geschlossenen Mandanten kann web_fetch nicht gehen -- nicht angeboten
        return on(self.env.get("RECHERCHE_FETCH")) and not self.closed()

    def fetch_off_sentence(self):
        if self.closed():
            return ("web_fetch gibt es hier nicht: diese Instanz gehört einem geschlossenen "
                    "Mandanten und erreicht das Internet nur über ihre Destinationen.")
        return "web_fetch ist in dieser Instanz ausgeschaltet (RECHERCHE_FETCH=off); nur die Suche geht."

    def count(self, tool, refused=""):
        key = (date.today().isoformat(), tool)
        with self.lock:
            row = self.counts.setdefault(key, {"ok": 0, "refused": {}})
            if refused:
                row["refused"][refused] = row["refused"].get(refused, 0) + 1
            else:
                row["ok"] += 1
            if len(self.counts) > 120:              # etwa zwei Monate, dann vergessen
                for k in sorted(self.counts)[:len(self.counts) - 120]:
                    self.counts.pop(k, None)

    def status(self):
        with self.lock:
            rows = [{"day": d, "tool": t, "ok": v["ok"], "refused": dict(v["refused"])}
                    for (d, t), v in sorted(self.counts.items(), reverse=True)]
        return {"version": VERSION, "backend": (self.env.get("RECHERCHE_BACKEND") or "searxng"),
                "search_destination": bool(sx.destination(self.env)),
                "search_backend": sx.backend(self.env)[1],
                "fetch": self.fetch_on(), "egress": "closed" if self.closed() else "open",
                "allow": fx.domains(self.env.get("RECHERCHE_ALLOW", "")),
                "deny": fx.domains(self.env.get("RECHERCHE_DENY", "")),
                "counts": rows}


STATE = None


class Handler(BaseHTTPRequestHandler):
    server_version = f"oaap-recherche/{VERSION}"

    @property
    def state(self):
        return getattr(self.server, "state", None) or STATE

    def log_message(self, fmt, *args):
        # §2.4: nie die Abfrage, nie den Rumpf -- nur Methode und Pfad
        sys.stdout.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, code, doc):
        self.send(code, json.dumps(doc, ensure_ascii=False), "application/json; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/healthz":
            return self.json(200, {"ok": True, "version": VERSION})
        if p == "/mcp":
            return self.send(405, "", "text/plain", {"Allow": "POST"})
        if p == "/api/status":
            return self.json(200, self.state.status())
        if p == "/":
            return self.send(200, self.home())
        return self.send(404, af.page("Nicht gefunden", af.empty("Diese Seite gibt es nicht. <a href=\"./\">Zur Übersicht</a>"), app=APP_NAME, version=VERSION, product="recherche"))

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > BODY_LIMIT:
            self.close_connection = True
            return self.json(413, {"error": "Rumpf zu groß"})
        body = self.rfile.read(n) if n else b""
        if urlparse(self.path).path != "/mcp":
            return self.send(404, "")
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.json(415, {"error": "MCP spricht JSON."})
        try:
            msg = json.loads(body or b"{}")
        except ValueError:
            return self.json(400, {"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "Parse error"}})
        out = mcp_door.answer(self.state, VERSION, msg)
        if out is None:
            return self.send(202, "", "text/plain")
        return self.json(200, out)

    def home(self):
        s = self.state.status()
        e = html.escape
        dest = {"destination": "Destination <code>search</code> (vom Betreiber zugeordnet)",
                "eigen": "eigener Suchdienst der Instanz (SearXNG)"}.get(
            s["search_backend"],
            "<b>keiner</b> – <code>oaap destination bind &lt;instanz&gt; &lt;destination&gt; --as search</code>")
        fetch = ("an" if s["fetch"] else "aus") + (" (geschlossener Mandant)" if s["egress"] == "closed" else "")
        settings = af.card("Einstellungen", (
            '<dl class="oaap-kv">'
            f"<dt>Suchdienst</dt><dd>{dest}</dd>"
            f"<dt>Webseiten lesen</dt><dd>{e(fetch)}</dd>"
            f"<dt>Erlaubte Seiten</dt><dd>{e(', '.join(s['allow'])) or 'alle öffentlichen'}</dd>"
            f"<dt>Gesperrte Seiten</dt><dd>{e(', '.join(s['deny'])) or 'keine'}</dd></dl>"))
        q = af.Query(parse_qs(urlparse(self.path).query), filters=("tool",), default_sort="-day")
        rows = [r for r in s["counts"] if not q.filters["tool"] or r["tool"] == q.filters["tool"]]
        cols = [("Tag", "day", lambda r: r["day"], ""),
                ("Werkzeug", "tool", lambda r: r["tool"], ""),
                ("beantwortet", "ok", lambda r: r["ok"], "oaap-num"),
                ("abgelehnt (Grund: Zahl)", "", None, "")]

        def row(r):
            ref = ", ".join(f"{k}: {v}" for k, v in sorted(r["refused"].items())) or "–"
            return (f"<tr><td>{e(r['day'])}</td><td>{e(r['tool'])}</td>"
                    f"<td class=\"oaap-num\">{r['ok']}</td><td>{e(ref)}</td></tr>")
        counts = af.list_report(
            q, rows, cols, row, noun="Zeilen", placeholder="Tag oder Werkzeug",
            choices=[("tool", "Werkzeug", [("web_search", "web_search"), ("web_fetch", "web_fetch")])],
            empty_text="Noch nichts gezählt (seit dem letzten Start).")
        body = (af.pagehead("Recherche")
                + "<p>Eine Quelle für die Assistenten dieses Mandanten: <code>web_search</code> fragt "
                "einen Suchdienst, <code>web_fetch</code> liest eine öffentliche Seite als Text. Ein "
                "Gespräch muss dafür auf <b>rot</b> stehen, denn Suchwörter und Adressen verlassen "
                "den Mandanten.</p>"
                + settings
                + af.card("Gezählt", '<p class="oaap-muted">Je Tag und Werkzeug, seit dem letzten Start. '
                          "Suchwörter, Adressen und Inhalte schreibt die App nirgends hin.</p>" + counts))
        return af.page("Recherche", body, app=APP_NAME, version=VERSION, product="recherche")


def main():
    global STATE
    STATE = State()
    print(f"recherche {VERSION} auf :{PORT} -- Suchdienst "
          f"{ {'destination': 'Destination search', 'eigen': 'eigener Dienst'}.get(sx.backend(os.environ)[1], 'KEINER')}, "
          f"web_fetch {'an' if STATE.fetch_on() else 'aus'}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
