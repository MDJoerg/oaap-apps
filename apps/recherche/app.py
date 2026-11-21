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
from urllib.parse import urlparse

import fetch as fx
import mcp_door
import search as sx

VERSION = "0.1.1"
PORT = int(os.environ.get("PORT", "8000"))
BODY_LIMIT = 256 * 1024
CSP = "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"


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
                    "Mandanten und erreicht das Internet nur über ihre Destinationen (RFC-0068).")
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
        return self.send(404, "<p>Diese Seite gibt es nicht.</p>")

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
        rows = "".join(
            f"<tr><td>{e(r['day'])}</td><td>{e(r['tool'])}</td><td>{r['ok']}</td>"
            f"<td>{e(', '.join(f'{k}: {v}' for k, v in sorted(r['refused'].items())) or '–')}</td></tr>"
            for r in s["counts"][:60]) or "<tr><td colspan=4>Noch nichts gezählt (seit dem letzten Start).</td></tr>"
        dest = ("zugeordnet" if s["search_destination"] else
                "<b>nicht zugeordnet</b> -- <code>oaap destination bind &lt;instanz&gt; &lt;destination&gt; --as search</code>")
        return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Recherche</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:820px;margin:2em auto;padding:0 16px;color:#222}}
code{{background:#f2f2f2;padding:0 3px}}table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:4px 6px;text-align:left}}.muted{{color:#666}}</style></head><body>
<h1>Recherche</h1>
<p>Eine Quelle für das MCP-Gateway dieses Mandanten (RFC-0116): <code>web_search</code> fragt einen
Suchdienst, <code>web_fetch</code> liest eine öffentliche Seite als Text. Ein Assistent nutzt sie über
die Sicht <code>recherche</code>; das Gespräch muss dafür auf <b>rot</b> stehen, denn Suchwörter und
Adressen verlassen den Mandanten.</p>
<table>
<tr><th>Suchdienst (Destination <code>search</code>)</th><td>{dest}</td></tr>
<tr><th>Webseiten lesen (<code>web_fetch</code>)</th><td>{'an' if s['fetch'] else 'aus'}{' (geschlossener Mandant)' if s['egress'] == 'closed' else ''}</td></tr>
<tr><th>Erlaubte Seiten</th><td>{e(', '.join(s['allow'])) or 'alle öffentlichen'}</td></tr>
<tr><th>Gesperrte Seiten</th><td>{e(', '.join(s['deny'])) or 'keine'}</td></tr>
</table>
<h2>Gezählt</h2>
<p class="muted">Je Tag und Werkzeug, seit dem letzten Start. Suchwörter, Adressen und Inhalte schreibt
die App nirgends hin.</p>
<table><tr><th>Tag</th><th>Werkzeug</th><th>beantwortet</th><th>abgelehnt (Grund: Zahl)</th></tr>{rows}</table>
<p class="muted">recherche {VERSION}</p></body></html>"""


def main():
    global STATE
    STATE = State()
    print(f"recherche {VERSION} auf :{PORT} -- Suchdienst "
          f"{'zugeordnet' if sx.destination(os.environ) else 'NICHT zugeordnet'}, "
          f"web_fetch {'an' if STATE.fetch_on() else 'aus'}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
