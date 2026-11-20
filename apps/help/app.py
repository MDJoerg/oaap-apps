"""help -- die Dokumentation eines Mandanten für Menschen und jede KI
(RFC-0078 Stufen 1 und 2, RFC-0094 Stufe 1: die Doku der installierten Apps
als Hilfsprodukte, mit Kacheln und Filtern).

Drei Türen auf denselben Kern (library.py):
  /mcp              MCP (Streamable HTTP, JSON): help_search, help_read,
                    help_topics, Resources help://quelle/id, Prompts guide/cli
  /api/v1/...       dieselben Antworten als JSON
  /                 Such- und Leseseite
Zwei Pfade (O3): ohne Präfix angemeldet, mit Zielgruppenfilter nach den
Rollen der Person; unter /public/ dieselben Türen öffentlich und
ungefiltert -- nur mit HELP_PUBLIC=on (zentrale Instanz, D8).
Dazu /admin (Route nur für admin): Quellen, Abruf „jetzt holen“, eigene
Notizen ablegen (Quelle `local`). Und /oaap/setup (RFC-0104 §3.1, Route nur
für admin): die Einrichtungstür für den Aufbau aus einem Profil, geprüft nur
mit OAAP_SETUP_KEY (setup_door.py).

Keine eigene KI (D2). Kein Zustand eines Knotens (D1). Nach außen nur über
die Destination `docs-origin` (RFC-0068). Nur Standardbibliothek.
"""
import html
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import guide_requests
import maint_door
import mcp_door
import render
import setup_door
import sources as src
import index as ix
import apps_source
from library import APPS_SOURCE, ARCHIVE_SOURCE, LOCAL_SOURCE, Library

APP_ID = "help"
VERSION = "0.8.0"
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
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:14px;margin:16px 0 24px}
.tile{display:block;border:1px solid var(--line);border-radius:10px;background:var(--card);padding:14px 16px;color:var(--fg);text-decoration:none;border-top:4px solid var(--tc,var(--acc))}
.tile:hover{border-color:var(--tc,var(--acc))}.tile b{display:block;font-size:1.05em;margin-bottom:4px}
.tile .meta{display:block}.chips{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 14px}
.chip{display:inline-block;padding:2px 10px;border:1px solid var(--line);border-radius:14px;font-size:.86em;color:var(--fg);text-decoration:none}
.chip.on{background:var(--acc);border-color:var(--acc);color:#fff}
"""
LAYOUTS = ("kacheln", "nüchtern")


def esc(s):
    return html.escape(str(s or ""), quote=True)


def site_title():
    return (os.environ.get("HELP_TITLE") or "Hilfe").strip()[:60] or "Hilfe"


def page(title, body, q="", base=""):
    t = site_title()
    return f"""<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · {esc(t)}</title><style>{STYLE}</style></head><body>
<header><div class="wrap"><a class="brand" href="{base}/">{esc(t)}</a>
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

    def is_admin(self):
        """Wer den Weg zur Abhilfe lesen soll (L9) -- nie auf /public/."""
        return not self.public and bool({"admin", "tenant_admin"} & self.roles())

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
        if u.path == setup_door_path():
            return self.setup_call(q=q)
        p = self.route(u.path)
        if p is None:
            return self.send(404, self.page("Nicht gefunden", "<p>Diese Seite gibt es nicht.</p>"))
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if p == "/mcp":
            return self.send(405, "", "text/plain", {"Allow": "POST"})
        if p == "/api/v1/search":
            tags = [t for t in one("tags").split(",") if t] or None
            hits = self.lib.search(one("q"), limit=one("limit", "8"), source=one("source") or None,
                                   tags=tags, version=one("version") or None, roles=self.view_roles(),
                                   product=one("product") or None, kind=one("kind") or None)
            return self.json(200, {"count": len(hits), "hits": hits})
        if p == "/api/v1/products":
            return self.json(200, {"products": self.lib.products(roles=self.view_roles())})
        if p.startswith("/api/v1/docs/"):
            doc = self.lib.read(unquote(p[len("/api/v1/docs/"):]), section=one("section") or None,
                                roles=self.view_roles(), version=one("version") or None)
            return self.json(200, doc) if doc else self.json(404, {"error": "not found"})
        if p == "/api/v1/topics":
            return self.json(200, self.lib.topics(prefix=one("prefix"), roles=self.view_roles(),
                                                  product=one("product") or None))
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
        if p.startswith("/produkt/"):
            return self.product_page(unquote(p[len("/produkt/"):]).strip("/"))
        if p == "/":
            return self.home(one("q"), one("product"), one("kind"))
        return self.send(404, self.page("Nicht gefunden", "<p>Diese Seite gibt es nicht.</p>"))

    def do_POST(self):
        body = self.read_body()
        if body is None:
            return self.json(413, {"error": "Rumpf zu groß"})
        raw = urlparse(self.path).path
        if raw == "/maint" or raw.startswith("/maint/"):
            return self.maint_post(raw, body)
        if raw == setup_door_path():
            return self.setup_call(body=body)
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

    # --- die Einrichtungstür (RFC-0104 §3.1): nur OAAP_SETUP_KEY, nie Identität
    def setup_call(self, q=None, body=None):
        if not setup_door.authorized(self.headers, self.lib.env):
            return self.json(403, {"v": 1, "error": "Nur mit dem Einrichtungsschlüssel der Instanz."})
        actor = setup_door.actor_of(self.headers)
        if body is None:
            one = lambda k: (q.get(k) or [""])[0]  # noqa: E731
            code, doc = setup_door.get(self.lib, one("kind"), one("id"))
            return self.json(code, doc)
        try:
            code, doc = setup_door.post(self.lib, body, actor)
        except setup_door.Malformed as e:
            return self.json(400, {"v": 1, "error": str(e)})
        print("setup actor=%s %s" % (actor, json.dumps(
            [[r["kind"], r["id"], r["result"]] for r in doc["results"]], ensure_ascii=False)), flush=True)
        return self.json(code, doc)

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
                "oaap_docs": self.lib.oaap_docs,
                "notice": self.lib.platform_notice(admin=True),
                "sources": {k: st.get(k) for k in (ARCHIVE_SOURCE, APPS_SOURCE, LOCAL_SOURCE)
                            if st.get(k)},
                "last_good": st.get("last_good")}

    def product_titles(self):
        return {p["product"]: p for p in self.lib.products(roles=self.view_roles())}

    def chips(self, q, product, kind, counts_p, counts_k, titles):
        """Filter als Links (RFC-0094 §5): Produkt und Typ, mit Zahlen,
        nur Werte, die es in den Treffern gibt."""
        def link(**kw):
            args = {"q": q, "product": product, "kind": kind}
            args.update(kw)
            return self.base + "/?" + "&".join(f"{k}={quote(v)}" for k, v in args.items() if v)
        out = []
        if counts_p:
            out.append("<div class=chips><span class=meta>Produkt:</span>"
                       + (f"<a class=chip href=\"{esc(link(product=''))}\">alle</a>" if product else "")
                       + "".join(f"<a class=\"chip{' on' if v == product else ''}\" href=\"{esc(link(product=v))}\">"
                                 f"{esc((titles.get(v) or {}).get('title') or v)} ({n})</a>"
                                 for v, n in sorted(counts_p.items(), key=lambda x: -x[1]))
                       + "</div>")
        if counts_k:
            out.append("<div class=chips><span class=meta>Typ:</span>"
                       + (f"<a class=chip href=\"{esc(link(kind=''))}\">alle</a>" if kind else "")
                       + "".join(f"<a class=\"chip{' on' if v == kind else ''}\" href=\"{esc(link(kind=v))}\">"
                                 f"{esc(ix.KIND_WORDS.get(v, v))} ({n})</a>"
                                 for v, n in sorted(counts_k.items(), key=lambda x: -x[1]))
                       + "</div>")
        return "".join(out)

    def hit_row(self, h, titles):
        pt = (titles.get(h.get("product")) or {}).get("title") or h.get("product") or h["source"]
        kind = ix.KIND_WORDS.get(h.get("kind") or "", "")
        return (f"<div class=hit><h3><a href=\"{esc(self.href(h['source'], h['id'], h['anchor']))}\">{esc(h['title'])}"
                + (f" — {esc(h['section'])}" if h["section"] and h["section"] != h["title"] else "")
                + f"</a></h3><div class=meta><span class=badge>{esc(pt)}</span>"
                + (f" <span class=badge>{esc(kind)}</span>" if kind and h.get("kind") != "unspecified" else "")
                + (" <span class=\"badge warn\">Vorschau (Test)</span>" if h.get("channel") == "test" else "")
                + (f" · Version {esc(h['app_version'])}" if h.get("app_version") else "")
                + (f" · {stamp(h)}" if stamp(h) else "") + "</div>"
                + f"<p class=snip>{render.inline(h['snippet'])}</p>"
                # L20 (0.6.0): weitere passende Abschnitte desselben Dokuments, als Links
                + ("<p class=meta>Auch: " + " · ".join(
                    f"<a href=\"{esc(self.href(h['source'], h['id'], m['anchor']))}\">{esc(m['section'] or h['title'])}</a>"
                    for m in h["more"]) + "</p>" if h.get("more") else "")
                + "</div>")

    def home(self, q, product="", kind=""):
        titles = self.product_titles()
        if q.strip():
            roles = self.view_roles()
            # Erst ungefiltert für die Zahlen der Filter, dann gefiltert für die Liste.
            pool = self.lib.search(q, limit=20, roles=roles)
            counts_p, counts_k = {}, {}
            for h in pool:
                if not kind or h.get("kind") == kind:
                    counts_p[h.get("product") or ""] = counts_p.get(h.get("product") or "", 0) + 1
                if not product or h.get("product") == product:
                    counts_k[h.get("kind") or ""] = counts_k.get(h.get("kind") or "", 0) + 1
            counts_p.pop("", None)
            counts_k.pop("", None)
            hits = (self.lib.search(q, limit=20, roles=roles, product=product or None,
                                    kind=kind or None) if (product or kind) else pool)
            body = self.chips(q, product, kind, counts_p, counts_k, titles)
            note = self.lib.platform_notice(admin=self.is_admin())
            if not hits:
                body += (f"<h1>Keine Treffer</h1><p>Für „{esc(q)}“ gibt es nichts. Andere oder weniger Wörter versuchen.</p>"
                         + (f"<div class=box><p class=warn>{esc(note)}</p></div>" if note else ""))
            else:
                body += (f"<p class=meta>{len(hits)} Treffer für „{esc(q)}“</p>"
                         + "".join(self.hit_row(h, titles) for h in hits))
            return self.send(200, self.page(q, body, q))
        st = self.status()
        layout = (self.lib.env.get("HELP_LAYOUT") or "kacheln").strip().lower()
        products = list(titles.values())
        parts = [f"<h1>{esc(site_title())}</h1><p>Anleitungen, Funktionsbeschreibungen und FAQ "
                 "zu den Apps dieses Mandanten. Oben suchen oder ein Thema wählen. "
                 "Eine KI erreicht dasselbe über MCP unter <code>/mcp</code>.</p>"]
        note = self.lib.platform_notice(admin=self.is_admin())
        if note:
            parts.append(f"<div class=box><p class=warn>{esc(note)}</p></div>")
        if products and layout != "nüchtern":
            tiles = []
            for pr in products:
                color = pr.get("color") or ""
                style = f" style=\"--tc:{esc(color)}\"" if re.fullmatch(r"#[0-9a-fA-F]{6}", color) else ""
                kinds = ", ".join(f"{ix.KIND_WORDS.get(k, k)} {n}" for k, n in list(pr["kinds"].items())[:3]
                                  if k != "unspecified")
                tiles.append(
                    f"<a class=tile{style} href=\"{esc(self.base + '/produkt/' + quote(pr['product']))}\">"
                    f"<b>{esc(pr['title'])}</b>"
                    f"<span class=meta>{pr['documents']} Dokumente"
                    + (f" · Version {esc(pr['version'])}" if pr.get("version") else "")
                    + (" · Vorschau (Test)" if pr.get("channel") == "test" else "") + "</span>"
                    + (f"<span class=meta>{esc(kinds)}</span>" if kinds else "") + "</a>")
            parts.append("<div class=tiles>" + "".join(tiles) + "</div>")
        elif products:
            parts.append("<ul class=plain>" + "".join(
                f"<li><a href=\"{esc(self.base + '/produkt/' + quote(pr['product']))}\">{esc(pr['title'])}</a>"
                f" <span class=meta>{pr['documents']} Dokumente</span></li>" for pr in products) + "</ul>")
        if not products and not st["documents"]:
            parts.append("<div class=box><p class=warn>Noch keine Dokumente geladen.</p>"
                         + (f"<p class=meta>{esc((st['sources'].get(ARCHIVE_SOURCE) or {}).get('error'))}</p>" if st["sources"].get(ARCHIVE_SOURCE) else "")
                         + "</div>")
        good = st.get("last_good") or {}
        parts.append(f"<p class=meta>{st['documents']} Dokumente"
                     + (f" · Plattform-Doku vom {esc(good.get('at'))}" if good.get("at") and st.get("oaap_docs") else "")
                     + ("" if self.public else " · <a href=\"/admin\">Verwaltung</a>") + "</p>")
        return self.send(200, self.page("Übersicht", "".join(parts)))

    def product_page(self, pid):
        titles = self.product_titles()
        pr = titles.get(pid)
        if not pr:
            return self.send(404, self.page("Nicht gefunden", "<p>Dieses Thema gibt es hier nicht.</p>"))
        tree = self.lib.topics(roles=self.view_roles(), product=pid)
        docs = [d for items in tree.values() for d in items]
        by_kind = {}
        for d in docs:
            by_kind.setdefault(d.get("kind") or "unspecified", []).append(d)
        src_of = {d["id"]: s for s, items in tree.items() for d in items}
        parts = [f"<h1>{esc(pr['title'])}</h1><p class=meta>{pr['documents']} Dokumente"
                 + (f" · Version {esc(pr['version'])}" if pr.get("version") else "")
                 + (f" · aus {esc(pr['app'])}" if pr.get("app") and pr.get("app") != "OAAP" else "")
                 + (" · <span class=warn>Vorschau aus einer Test-Instanz</span>" if pr.get("channel") == "test" else "")
                 + "</p>"]
        if pr.get("entry") and pr["entry"] in src_of:
            parts.append(f"<p><a href=\"{esc(self.href(src_of[pr['entry']], pr['entry']))}\">Einstieg lesen</a></p>")
        parts.append(f"<form action=\"{self.base}/\" method=get><input type=hidden name=product value=\"{esc(pid)}\">"
                     f"<p><input type=search name=q placeholder=\"In {esc(pr['title'])} suchen …\"></p></form>")
        for kind in [k for k in ix.KINDS if k in by_kind]:
            items = sorted(by_kind[kind], key=lambda d: d["title"].lower())
            gen = [d for d in items if "cli" in d.get("tags", [])]
            if len(gen) > 12:
                items = [d for d in items if d not in gen and d["id"] != "cli/generated"]
                items.append({"id": "cli/generated", "title": f"Erzeugte CLI-Referenz ({len(gen)} Seiten)"})
            li = "".join(f"<li><a href=\"{esc(self.href(src_of.get(d['id'], ARCHIVE_SOURCE), d['id']))}\">{esc(d['title'])}</a></li>"
                         for d in items)
            parts.append(f"<h2>{esc(ix.KIND_WORDS.get(kind, kind))}</h2><ul class=plain>{li}</ul>")
        return self.send(200, self.page(pr["title"], "".join(parts)))

    def doc_page(self, ref):
        source, _, doc_id = ref.partition("/")
        doc = self.lib.read(f"{source}:{doc_id}", roles=self.view_roles())
        if not doc:
            return self.send(404, self.page("Nicht gefunden", "<p>Dieses Dokument gibt es hier nicht.</p>"))
        def link_for(rel_path):
            return self.base + "/p/" + quote(source) + "/" + quote(rel_path)

        titles = self.product_titles()
        pt = (titles.get(doc.get("product")) or {}).get("title") or doc.get("product") or doc["source"]
        info = [f"<a class=badge href=\"{esc(self.base + '/produkt/' + quote(doc.get('product') or ''))}\">{esc(pt)}</a>"
                + (f" <span class=badge>{esc(ix.KIND_WORDS.get(doc.get('kind'), ''))}</span>"
                   if doc.get("kind") and doc["kind"] != "unspecified" else "")
                + (f" Version {esc(doc['app_version'])}" if doc.get("app_version") else "")
                + (" <span class=\"badge warn\">Vorschau (Test)</span>" if doc.get("channel") == "test" else "")]
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
            f"({'zugeordnet' if st['destination'] else '<span class=err>nicht zugeordnet</span>'}), Pfad <code>{esc(self.lib.archive_path)}</code></td>"
            f"<td>{esc(a.get('at'))} {'ok' if a.get('ok') else ('<span class=err>' + esc(a.get('error')) + '</span>' if a else '–')}"
            + (f"<br><span class=meta>antwortet: Stand {esc(good.get('at'))}, {good.get('files')} Dateien, {esc((good.get('sha256') or '')[:12])}</span>" if good else "")
            + "</td></tr>"]
        ap = st["sources"].get(APPS_SOURCE) or {}
        rows.append(f"<tr><td>{APPS_SOURCE}</td><td>Doku der installierten Apps des Mandanten "
                    f"(<code>{esc(self.lib.docs_root)}</code>, vom Knoten eingehängt)</td>"
                    f"<td>{ap.get('products', 0)} Produkte, {ap.get('files', 0)} Dateien"
                    + (f"<br><span class=meta>{esc(ap.get('error'))}</span>" if ap.get("error") else "")
                    + "</td></tr>")
        if not self.lib.oaap_docs:
            rows[0] = (f"<tr><td>{ARCHIVE_SOURCE}</td><td colspan=2>ausgeschaltet "
                       "(<code>HELP_OAAP_DOCS=off</code>) -- die Destination docs-origin wird nicht gebraucht</td></tr>")
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


def setup_door_path():
    """Wie im Manifest (`setup.path`); nur ohne Präfix, nie unter /public/."""
    return "/oaap/setup"


def watch_loop(lib, seconds=30):
    """Ändert der Knoten die App-Doku (Installation, Update, Entfernen),
    baut die Hilfe neu -- ohne die Plattform-Doku neu zu holen."""
    while True:
        time.sleep(seconds)
        try:
            if lib.apps_changed():
                lib.rebuild()
                st = lib.state().get(APPS_SOURCE) or {}
                print(f"App-Doku neu gelesen: {st.get('products', 0)} Produkte, "
                      f"{st.get('files', 0)} Dateien", flush=True)
        except Exception as e:                  # noqa: BLE001
            print(f"App-Doku nicht gelesen: {e}", flush=True)


def fetch_loop(lib, hours):
    """Beim Start holen, danach alle `hours` Stunden (§3.1)."""
    time.sleep(2)
    while True:
        st = lib.refresh()
        e = st.get(ARCHIVE_SOURCE, {})
        print(f"Abruf {ARCHIVE_SOURCE}: {'ok, ' + str(e.get('files')) + ' Dateien' if e.get('ok') else e.get('error')}",
              flush=True)
        note = lib.platform_notice(admin=True)
        if note:
            print(f"Hinweis: {note}", flush=True)
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
    threading.Thread(target=watch_loop, args=(LIB,), daemon=True).start()
    print(f"help {VERSION} on :{PORT} (FTS5: {LIB.index.fts5()})", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
