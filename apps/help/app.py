"""help -- statisches Hilfewissen für jede KI (RFC-0078 Stufen 1 und 2).

Drei Türen auf denselben Kern (library.py):
  /mcp              MCP (Streamable HTTP, JSON): help_search, help_read,
                    help_topics, Resources help://quelle/id, Prompts guide/cli
  /api/v1/...       dieselben Antworten als JSON
  /                 Such- und Leseseite
Zwei Pfade (O3): ohne Präfix angemeldet, mit Zielgruppenfilter nach den
Rollen der Person; unter /public/ dieselben Türen öffentlich und
ungefiltert -- nur mit HELP_PUBLIC=on (zentrale Instanz, D8).
Dazu /admin (Route nur für admin): Quellen, Abruf „jetzt holen“, eigene
Notizen ablegen (Quelle `local`).

Keine eigene KI (D2). Kein Zustand eines Knotens (D1). Nach außen nur über
die Destination `docs-origin` (RFC-0068). Nur Standardbibliothek.
"""
import html
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import guide_requests
import maint_door
import mcp_door
import render
import sources as src
import index as ix
from library import ARCHIVE_SOURCE, LOCAL_SOURCE, Library

APP_ID = "help"
VERSION = "0.3.1"
PORT = int(os.environ.get("PORT", "8000"))
DATA_DIR = os.environ.get("HELP_DATA_DIR", "/data")
LIB = None

CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
       "form-action 'self'; base-uri 'none'; frame-ancestors 'self'")

STYLE = """
:root{--bg:#fbfbfa;--fg:#1d1d1b;--mute:#6b6b66;--line:#e2e1dc;--card:#fff;--acc:#2b5f8a;--code:#f2f1ec;--warn:#8a5a00}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#e8e7e2;--mute:#9a9992;--line:#33322f;--card:#1f1f1d;--acc:#7fb0dc;--code:#262624;--warn:#e0b050}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
header{border-bottom:1px solid var(--line);background:var(--card)}
.wrap{max-width:860px;margin:0 auto;padding:0 16px}
header .wrap{display:flex;gap:16px;align-items:center;min-height:56px;flex-wrap:wrap}
header a.brand{font-weight:650;color:var(--fg);text-decoration:none}
header form{flex:1;display:flex;gap:8px;min-width:220px}
input[type=search],input[type=text],textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg);font:inherit}
button{padding:8px 14px;border:1px solid var(--acc);background:var(--acc);color:#fff;border-radius:6px;font:inherit;cursor:pointer}
button.ghost{background:transparent;color:var(--acc)}
a{color:var(--acc)}main{padding:24px 0 48px}
.hit{padding:14px 0;border-bottom:1px solid var(--line)}.hit h3{margin:0 0 4px;font-size:1.05em}
.meta{color:var(--mute);font-size:.86em}.snip{margin:6px 0 0}
.badge{display:inline-block;padding:0 6px;border:1px solid var(--line);border-radius:10px;font-size:.8em;color:var(--mute)}
.warn{color:var(--warn)}.err{color:#b3261e}
pre{background:var(--code);padding:12px;border-radius:6px;overflow-x:auto;font-size:.9em}
code{background:var(--code);padding:1px 4px;border-radius:4px;font-size:.92em}pre code{padding:0;background:none}
table{border-collapse:collapse;display:block;overflow-x:auto;font-size:.92em}td,th{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}
blockquote{margin:0;padding:4px 14px;border-left:3px solid var(--line);color:var(--mute)}
.box{border:1px solid var(--line);background:var(--card);border-radius:8px;padding:12px 16px;margin:0 0 20px}
.toc{columns:2;font-size:.9em}@media(max-width:640px){.toc{columns:1}}
ul.plain{list-style:none;padding:0}ul.plain li{padding:3px 0}
"""


def esc(s):
    return html.escape(str(s or ""), quote=True)


def page(title, body, q="", base=""):
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · OAAP-Hilfe</title><style>{STYLE}</style></head><body>
<header><div class="wrap"><a class="brand" href="{base}/">OAAP-Hilfe</a>
<form action="{base}/" method="get" role="search"><input type="search" name="q" value="{esc(q)}" placeholder="Wie richte ich …?" aria-label="Suchen"><button>Suchen</button></form>
</div></header><main><div class="wrap">{body}</div></main></body></html>"""


def doc_href(source, doc_id, anchor="", base=""):
    return f"{base}/doc/{quote(source)}/{quote(doc_id)}" + (f"#{quote(anchor)}" if anchor else "")


def stamp(m):
    bits = []
    if m.get("checked_against"):
        bits.append(f"geprüft gegen {esc(m['checked_against'])}"
                    + (f" am {esc(m['checked_on'])}" if m.get("checked_on") else ""))
    if m.get("applies_from"):
        bits.append(f"gilt ab {esc(m['applies_from'])}")
    if m.get("applies_until"):
        bits.append(f"bis {esc(m['applies_until'])}")
    return " · ".join(bits)


class Handler(BaseHTTPRequestHandler):
    server_version = f"oaap-help/{VERSION}"
    BODY_LIMIT = 2 * 1024 * 1024 + 4096

    @property
    def lib(self):
        return getattr(self.server, "lib", None) or LIB

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    # --- Antworten
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

    def roles(self):
        return {r.strip() for r in self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()}

    # --- zwei Pfade, ein Paket (RFC-0078 O3, Jörg 11.11.)
    # `/…` ist angemeldet: die Plattform setzt X-OAAP-Roles, und der
    # Zielgruppenfilter gilt immer. `/public/…` ist öffentlich und
    # ungefiltert (D8) -- aber nur, wenn HELP_PUBLIC=on gesetzt ist; sonst
    # antwortet er 404, als gäbe es ihn nicht. Auf dem öffentlichen Pfad
    # entfernt das Gateway gefälschte Identitäts-Kopfzeilen; gelesen werden
    # sie dort ohnehin nicht.
    def route(self, path):
        self.public, self.base = False, ""
        if path == "/public" or path.startswith("/public/"):
            if (self.lib.env.get("HELP_PUBLIC") or "").strip().lower() != "on":
                return None
            self.public, self.base = True, "/public"
            path = path[len("/public"):] or "/"
            if path.startswith("/admin"):
                return None
        return path

    def view_roles(self):
        return ix.UNFILTERED if self.public else self.roles()

    def href(self, source, doc_id, anchor=""):
        return doc_href(source, doc_id, anchor, self.base)

    def page(self, title, body, q=""):
        return page(title, body, q, self.base)

    def same_origin(self):
        origin = self.headers.get("Origin", "")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        return bool(origin) and urlparse(origin).netloc == host

    def read_body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > self.BODY_LIMIT:
            self.close_connection = True
            return None
        return self.rfile.read(n) if n else b""

    # --- Routen
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/healthz":
            return self.json(200, {"ok": True, "version": VERSION,
                                   "documents": self.lib.index.count()})
        if u.path == "/maint" or u.path.startswith("/maint/"):
            return self.maint_get(u.path, q)
        p = self.route(u.path)
        if p is None:
            return self.send(404, self.page("Nicht gefunden", "<p>Diese Seite gibt es nicht.</p>"))
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if p == "/mcp":
            return self.send(405, "", "text/plain", {"Allow": "POST"})
        if p == "/api/v1/search":
            tags = [t for t in one("tags").split(",") if t] or None
            hits = self.lib.search(one("q"), limit=one("limit", "8"), source=one("source") or None,
                                   tags=tags, version=one("version") or None, roles=self.view_roles())
            return self.json(200, {"count": len(hits), "hits": hits})
        if p.startswith("/api/v1/docs/"):
            doc = self.lib.read(unquote(p[len("/api/v1/docs/"):]), section=one("section") or None,
                                roles=self.view_roles(), version=one("version") or None)
            return self.json(200, doc) if doc else self.json(404, {"error": "not found"})
        if p == "/api/v1/topics":
            return self.json(200, self.lib.topics(prefix=one("prefix"), roles=self.view_roles()))
        if p == "/api/v1/status":
            return self.json(200, self.status())
        if p == "/admin" or p == "/admin/":
            return self.admin_page(one("m"))
        if p.startswith("/doc/"):
            return self.doc_page(unquote(p[len("/doc/"):]))
        if p.startswith("/p/"):
            # relativer Link zwischen Dokumenten: Pfad → id → Leseseite
            source, _, path = unquote(p[3:]).partition("/")
            doc_id = self.lib.index.id_by_path(source, path)
            if not doc_id:
                return self.send(404, self.page("Nicht gefunden", "<p>Dieses Dokument gibt es hier nicht.</p>"))
            return self.send(303, "", headers={"Location": self.href(source, doc_id)})
        if p == "/":
            return self.home(one("q"))
        return self.send(404, self.page("Nicht gefunden", "<p>Diese Seite gibt es nicht.</p>"))

    def do_POST(self):
        body = self.read_body()
        if body is None:
            return self.json(413, {"error": "Rumpf zu groß"})
        raw = urlparse(self.path).path
        if raw == "/maint" or raw.startswith("/maint/"):
            return self.maint_post(raw, body)
        p = self.route(raw)
        if p is None:
            return self.send(404, "")
        if p == "/mcp":
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self.json(415, {"error": "MCP spricht JSON."})
            try:
                msg = json.loads(body or b"{}")
            except ValueError:
                return self.json(400, {"jsonrpc": "2.0", "id": None,
                                       "error": {"code": -32700, "message": "Parse error"}})
            out = mcp_door.answer(self.lib, self.view_roles(), VERSION, msg)
            if out is None:
                return self.send(202, "", "text/plain")
            return self.json(200, out)
        if p.startswith("/admin/"):
            # Die Route /admin verlangt `admin` am Gateway; hier dieselbe Prüfung
            # noch einmal, und ein Formular nur von dieser Seite.
            if "admin" not in self.roles():
                return self.send(403, self.page("Nur für admin", "<p>Diese Verwaltung ist nur für admin.</p>"))
            if not self.same_origin():
                return self.send(403, self.page("Abgelehnt", "<p>Dieses Formular kam nicht von dieser Seite.</p>"))
            f = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True).items()}
            try:
                if p == "/admin/fetch":
                    st = self.lib.refresh()
                    m = "geholt" if st[ARCHIVE_SOURCE]["ok"] else "fehler"
                elif p == "/admin/local":
                    name = src.save_local(self.lib.data_dir, f.get("name"), f.get("text", ""))
                    self.lib.rebuild_local()
                    m = "gespeichert:" + name
                elif p == "/admin/local/delete":
                    src.delete_local(self.lib.data_dir, f.get("name"))
                    self.lib.rebuild_local()
                    m = "entfernt"
                else:
                    return self.send(404, "")
            except src.Refused as e:
                return self.send(400, self.page("Nicht möglich", f"<p class=err>{esc(e)}</p><p><a href='/admin'>Zurück</a></p>"))
            return self.send(303, "", headers={"Location": "/admin?m=" + quote(m)})
        return self.send(404, "")

    # --- die Bearbeitungstür (RFC-0078 §15.3): Schlüssel, nie die Plattform-Anmeldung
    def maint_ok(self):
        if maint_door.authorized(self.headers, self.lib.env):
            return True
        self.json(401, {"error": "Schlüssel fehlt oder stimmt nicht (Authorization: Bearer …)."})
        return False

    def maint_get(self, path, q):
        if not self.maint_ok():
            return None
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        rq = self.lib.requests
        try:
            if path == "/maint/api/v1/requests":
                return self.json(200, maint_door.call(self.lib, "requests_list",
                                                      {"status": one("status", "open"), "limit": one("limit", "50")}))
            if path.startswith("/maint/api/v1/requests/"):
                r = rq.read(unquote(path[len("/maint/api/v1/requests/"):]))
                return self.json(200, r) if r else self.json(404, {"error": "not found"})
            if path == "/maint/api/v1/gaps":
                return self.json(200, {"gaps": rq.gaps(one("days", "30"), one("limit", "50"))})
        except guide_requests.Refused as e:
            return self.json(400, {"error": str(e)})
        if path == "/maint/mcp":
            return self.send(405, "", "text/plain", {"Allow": "POST"})
        return self.json(404, {"error": "not found"})

    def maint_post(self, path, body):
        if not self.maint_ok():
            return None
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        try:
            msg = json.loads(body or b"{}")
        except ValueError:
            return self.json(400, {"error": "kein JSON"})
        if path == "/maint/mcp":
            out = maint_door.answer(self.lib, VERSION, msg)
            return self.send(202, "", "text/plain") if out is None else self.json(200, out)
        if path.startswith("/maint/api/v1/requests/") and path.endswith("/close") and isinstance(msg, dict):
            rid = unquote(path[len("/maint/api/v1/requests/"):-len("/close")])
            try:
                return self.json(200, maint_door.call(self.lib, "request_close", dict(msg, id=rid)))
            except guide_requests.Refused as e:
                return self.json(400, {"error": str(e)})
        return self.json(404, {"error": "not found"})

    # --- Inhalte
    def status(self):
        st = self.lib.state()
        return {"version": VERSION, "documents": self.lib.index.count(),
                "fts5": self.lib.index.fts5(), "destination": bool(src.destination_url(self.lib.env)),
                "public_path": (self.lib.env.get("HELP_PUBLIC") or "").strip().lower() == "on",
                "answering": st.get("answering", ""),
                "requests": {"enabled": self.lib.requests.enabled,
                             "maintainer_key": bool((self.lib.env.get("HELP_MAINT_KEY_SHA256") or "").strip())},
                "sources": {k: st.get(k) for k in (ARCHIVE_SOURCE, LOCAL_SOURCE) if st.get(k)},
                "last_good": st.get("last_good")}

    def home(self, q):
        if q.strip():
            hits = self.lib.search(q, limit=20, roles=self.view_roles())
            if not hits:
                body = f"<h1>Keine Treffer</h1><p>Für „{esc(q)}“ gibt es nichts. Andere oder weniger Wörter versuchen.</p>"
            else:
                rows = []
                for h in hits:
                    rows.append(
                        f"<div class=hit><h3><a href=\"{esc(self.href(h['source'], h['id'], h['anchor']))}\">{esc(h['title'])}"
                        + (f" — {esc(h['section'])}" if h["section"] and h["section"] != h["title"] else "")
                        + f"</a></h3><div class=meta><span class=badge>{esc(h['source'])}</span> {esc(h['id'])}"
                        + (f" · {stamp(h)}" if stamp(h) else "") + "</div>"
                        + f"<p class=snip>{render.inline(h['snippet'])}</p></div>")
                body = f"<p class=meta>{len(hits)} Treffer für „{esc(q)}“</p>" + "".join(rows)
            return self.send(200, self.page(q, body, q))
        tree = self.lib.topics(roles=self.view_roles())
        st = self.status()
        parts = ["<h1>OAAP-Hilfe</h1><p>Anleitungen, CLI-Referenz und Betriebswissen der Plattform. "
                 "Eine KI erreicht dasselbe über MCP unter <code>/mcp</code>.</p>"]
        if not st["documents"]:
            parts.append("<div class=box><p class=warn>Noch keine Dokumente geladen.</p>"
                         + (f"<p class=meta>{esc((st['sources'].get(ARCHIVE_SOURCE) or {}).get('error'))}</p>" if st["sources"].get(ARCHIVE_SOURCE) else "")
                         + "</div>")
        for source, docs in tree.items():
            groups = {}
            for d in docs:
                top = d["id"].split("/")[0] if "/" in d["id"] else "Allgemein"
                groups.setdefault(top, []).append(d)
            parts.append(f"<h2>{esc(source)}</h2>")
            for top in sorted(groups):
                items = groups[top]
                # Die erzeugte CLI-Referenz (Tag `cli`) hat ~80 Seiten: eine Zeile, die zum Index führt.
                gen = [d for d in items if "cli" in d.get("tags", [])]
                if len(gen) > 12:
                    items = [d for d in items if d not in gen and d["id"] != "cli/generated"]
                    items.append({"id": "cli/generated", "title": f"Erzeugte CLI-Referenz ({len(gen)} Seiten)"})
                li = "".join(f"<li><a href=\"{esc(self.href(source, d['id']))}\">{esc(d['title'])}</a></li>" for d in items)
                parts.append(f"<h3>{esc(top)}</h3><ul class=plain>{li}</ul>")
        good = st.get("last_good") or {}
        parts.append(f"<p class=meta>{st['documents']} Dokumente"
                     + (f" · Stand vom {esc(good.get('at'))}" if good.get("at") else "")
                     + ("" if self.public else " · <a href=\"/admin\">Verwaltung</a>") + "</p>")
        return self.send(200, self.page("Übersicht", "".join(parts)))

    def doc_page(self, ref):
        source, _, doc_id = ref.partition("/")
        doc = self.lib.read(f"{source}:{doc_id}", roles=self.view_roles())
        if not doc:
            return self.send(404, self.page("Nicht gefunden", "<p>Dieses Dokument gibt es hier nicht.</p>"))
        def link_for(rel_path):
            return self.base + "/p/" + quote(source) + "/" + quote(rel_path)

        info = [f"<span class=badge>{esc(doc['source'])}</span> <code>{esc(doc['id'])}</code>"]
        if stamp(doc):
            info.append(stamp(doc))
        if doc["url"]:
            info.append(f"<a href=\"{esc(doc['url'])}\" rel=\"noopener noreferrer\" target=\"_blank\">Original öffnen</a>")
        if doc["shadowed_by"]:
            info.append(f"<span class=warn>überdeckt von <a href=\"{esc(self.href(doc['shadowed_by'], doc['id']))}\">{esc(doc['shadowed_by'])}</a></span>")
        elif doc["other_versions"]:
            info.append("weitere Fassungen: " + ", ".join(
                f"<a href=\"{esc(self.href(v.split(':', 1)[0], doc['id']))}\">{esc(v.split(':', 1)[0])}</a>"
                for v in doc["other_versions"]))
        toc = ""
        if len(doc["sections"]) > 2:
            toc = "<div class=box><div class=toc>" + "".join(
                f"<div><a href=\"#{esc(s['anchor'])}\">{esc(s['heading'])}</a></div>" for s in doc["sections"]) + "</div></div>"
        body = (f"<div class=meta>{' · '.join(info)}</div>" + toc
                + render.to_html(doc["markdown"], doc["path"], link_for))
        return self.send(200, self.page(doc["title"], body))

    def admin_page(self, msg):
        if "admin" not in self.roles():
            return self.send(403, self.page("Nur für admin", "<p>Diese Verwaltung ist nur für admin.</p>"))
        st = self.status()
        a = st["sources"].get(ARCHIVE_SOURCE) or {}
        good = st.get("last_good") or {}
        note = f"<p class=meta>Erledigt: {esc(msg)}</p>" if msg else ""
        rows = [
            f"<tr><td>{ARCHIVE_SOURCE}</td><td>archive über Destination <code>docs-origin</code> "
            f"({'gebunden' if st['destination'] else '<span class=err>nicht gebunden</span>'}), Pfad <code>{esc(self.lib.archive_path)}</code></td>"
            f"<td>{esc(a.get('at'))} {'ok' if a.get('ok') else ('<span class=err>' + esc(a.get('error')) + '</span>' if a else '–')}"
            + (f"<br><span class=meta>antwortet: Stand {esc(good.get('at'))}, {good.get('files')} Dateien, {esc((good.get('sha256') or '')[:12])}</span>" if good else "")
            + "</td></tr>"]
        local = src.read_local(self.lib.data_dir)
        rows.append(f"<tr><td>{LOCAL_SOURCE}</td><td>eigene Notizen, Priorität über der Plattform-Doku</td><td>{len(local)} Dateien</td></tr>")
        files = "".join(
            f"<li><code>{esc(n)}</code> <form method=post action=\"/admin/local/delete\" style=\"display:inline\">"
            f"<input type=hidden name=name value=\"{esc(n)}\"><button class=ghost>entfernen</button></form></li>"
            for n, _t in local) or "<li class=meta>keine</li>"
        body = f"""<h1>Verwaltung</h1>{note}
<table><tr><th>Quelle</th><th>Art</th><th>Letzter Abruf</th></tr>{''.join(rows)}</table>
<form method=post action="/admin/fetch" style="margin:12px 0"><button>Jetzt holen</button></form>
<p class=meta>{st['documents']} Dokumente · Suche: {'FTS5' if st['fts5'] else 'einfach (ohne FTS5)'} · Öffentlicher Pfad <code>/public/</code>: {'an' if st['public_path'] else 'aus'} · angemeldet gilt der Zielgruppenfilter</p>
<h2>Anfragen nach Anleitungen</h2>
<p>{'An' if self.lib.requests.enabled else 'Aus'} (<code>HELP_REQUESTS</code>) · offen: {self.lib.requests.counts()['open']} · Bearbeitungstür <code>/maint/mcp</code>: {'Schlüssel gesetzt' if st['requests']['maintainer_key'] else 'kein Schlüssel (HELP_MAINT_KEY_SHA256)'}</p>
<h2>Eigene Notizen (local)</h2>
<p>Eine Notiz mit derselben <code>id</code> wie ein Plattformtext überdeckt ihn auf diesem Knoten; das Original bleibt lesbar.</p>
<ul class=plain>{files}</ul>
<form method=post action="/admin/local"><p><input type=text name=name placeholder="dateiname.md (a-z, 0-9, / _ . -)" required></p>
<p><textarea name=text rows=12 placeholder="---&#10;id: szenarien/server-aufsetzen&#10;---&#10;# …"></textarea></p><button>Speichern</button></form>"""
        return self.send(200, self.page("Verwaltung", body))


def fetch_loop(lib, hours):
    """Beim Start holen, danach alle `hours` Stunden (§3.1)."""
    time.sleep(2)
    while True:
        st = lib.refresh()
        e = st.get(ARCHIVE_SOURCE, {})
        print(f"Abruf {ARCHIVE_SOURCE}: {'ok, ' + str(e.get('files')) + ' Dateien' if e.get('ok') else e.get('error')}",
              flush=True)
        time.sleep(max(0.1, hours) * 3600)


def main():
    global LIB
    os.makedirs(DATA_DIR, exist_ok=True)
    LIB = Library(DATA_DIR)
    if not LIB.index.count():
        LIB.rebuild_local()            # lokale Notizen sofort, auch ohne Archiv
    try:
        hours = float(os.environ.get("HELP_FETCH_HOURS") or 6)
    except ValueError:
        hours = 6.0
    threading.Thread(target=fetch_loop, args=(LIB, hours), daemon=True).start()
    print(f"help {VERSION} on :{PORT} (FTS5: {LIB.index.fts5()})", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
