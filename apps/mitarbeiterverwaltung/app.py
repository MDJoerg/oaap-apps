"""OAAP Mitarbeiterverwaltung 0.1 -- Owner of Mitarbeiter (RFC-0031
Schritt 4, zweite Welle; program/zielbild-datenplattform.md Runde 1,
Punkt 5: "eine einfache Mitarbeiterverwaltung").

Diese App weiss NICHTS von Partnerverwaltung und liest/schreibt auch
nichts von ihr (kein consumes, kein contributes auf Firma oder
Kontaktperson) -- das ist der Punkt, nicht ein Versehen. Der Bauplan
nennt sie "zweiter Owner von Person -> erste echte Dublette, erster
Merge": zwei unabhaengige Apps legen dieselbe echte Person zweimal an,
jede unter ihrem eigenen Typ (Kontaktperson hier, Mitarbeiter dort),
weil keine von der anderen weiss -- exakt das Problem, das RFC-0031
selbst als Motivation nennt ("vier Apps, vier Meinungen, was ein Kunde
ist"). Das Aufloesen (Merge/Unmerge) ist eine Aufgabe des Zwillings-
Browsers (Schritt 5) und wird hier bewusst NICHT gebaut -- diese App
erzeugt den Fall, sie loest ihn nicht.

Sonst wie jede andere Referenz-App: kein eigener Login, ein HTTP-Port,
Persistenz nur als eigener Index (id + type, kein Titel, kein
Attribut -- der Zwilling ist die einzige Quelle), Konfiguration ueber
deklarierte Variablen, Logs nach stdout, Gesundheitspfad, nur
Standardbibliothek (plus twin.py).

RFC-0035 (App-Design-Kontrakt Teil A): bindet /platform/theme.css ein
und benutzt dessen Variablen mit lokalen Fallback-Werten; die
Kopfzeile folgt der Mini-Konvention aus D4 (App-Name links, Link
zurueck zum Portal rechts) statt einer eigenen Marke.
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

import twin

VERSION = "0.1.0"
PORT = 8000

DATA_DIR = os.environ.get("MITARBEITERVERWALTUNG_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

MITARBEITER_TYPE = "Mitarbeiter"
HR_GROUP = "hr.core"


# --------------------------------------------------------- der eigene Index

def remember(obj_id, type_key):
    """id + type, NICHTS sonst -- wie Partnerverwaltung's eigener Index."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INDEX_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": obj_id, "type": type_key}) + "\n")
    except OSError as exc:
        sys.stdout.write(f"Index nicht schreibbar: {exc}\n")


def known(type_key):
    seen = {}
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") == type_key and row.get("id"):
                    seen[row["id"]] = True
    except OSError:
        return []
    return list(seen.keys())


# --------------------------------------------------------------- Optik

STYLE = """<link rel="stylesheet" href="/platform/theme.css">
<style>
  *{box-sizing:border-box}
  body{font-family:var(--oaap-font-family, system-ui, -apple-system, "Segoe UI", sans-serif);
       font-size:var(--oaap-font-size-base, 15px);margin:0;
       background:var(--oaap-color-bg, #fff);color:var(--oaap-color-text, #1a1d21)}
  main{max-width:52rem;margin:1.6rem auto;padding:0 var(--oaap-space-3, 16px)}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-color-surface, #f4f5f7);
       border:1px solid var(--oaap-color-border, #d8dbe0);
       border-radius:var(--oaap-radius, 6px);padding:1.4rem;margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:#fff;color:var(--oaap-color-primary, #2f6fed);white-space:nowrap;
       border:1px solid var(--oaap-color-border, #d8dbe0)}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;
       border-radius:var(--oaap-radius, 6px);
       background:var(--oaap-color-primary, #2f6fed);
       color:var(--oaap-color-primary-text, #fff);text-decoration:none;
       font-size:.95rem;cursor:pointer;min-height:44px}
  .hint{font-size:.8rem;color:var(--oaap-color-text-muted, #5b616b);margin:0 0 .6rem}
  .muted{color:var(--oaap-color-text-muted, #5b616b);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;
       border-bottom:1px solid var(--oaap-color-border, #d8dbe0);font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;
       color:var(--oaap-color-text-muted, #5b616b)}
  code{background:var(--oaap-color-surface, #f4f5f7);border-radius:.25rem;
       padding:.1rem .35rem;font-size:.86rem;word-break:break-all}
  form.stack{display:flex;flex-direction:column;gap:.6rem;max-width:26rem}
  form.stack label{font-size:.82rem;color:var(--oaap-color-text-muted, #5b616b)}
  input{padding:.5rem .6rem;border-radius:var(--oaap-radius, 6px);
       border:1px solid var(--oaap-color-border, #d8dbe0);font-size:.95rem;min-height:40px}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
  @media (max-width:720px){ .cols{grid-template-columns:1fr} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Ccircle cx='50' cy='36' r='18' fill='%232f6fed'/%3E%3Cpath "
           "d='M18 90c0-22 14-36 32-36s32 14 32 36' fill='%232f6fed'/%3E%3C/svg%3E")

BACK_TO_PORTAL = ('href="/" onclick="location.href=location.protocol+\'//\'+'
                  "location.hostname+'/';return false;\"")


def page(body, user, roles, title="Mitarbeiterverwaltung"):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} -- OAAP Mitarbeiterverwaltung</title>
{STYLE}
<header class="oaap-header">
  <span class="oaap-header-title">Mitarbeiterverwaltung</span>
  <a class="oaap-header-back" {BACK_TO_PORTAL}>&larr; Portal</a>
</header>
<main>{body}</main>
<footer style="max-width:52rem;margin:2rem auto 1.2rem;padding:0 var(--oaap-space-3, 16px);
  color:var(--oaap-color-text-muted, #5b616b);font-size:.8rem">
  OAAP Mitarbeiterverwaltung {VERSION} -- Owner von Mitarbeiter (RFC-0031 Schritt 4, zweite Welle)
</footer>
</html>"""


def not_configured_card():
    return """<div class="card attention">
  <h2>Kein Zwilling-Zugang</h2>
  <p class="hint">Diese Instanz hat weder <code>OAAP_TWIN_URL</code> noch
    <code>OAAP_PLATFORM_KEY</code> -- entweder traegt dieser Knoten das
    Profil <code>store</code> nicht, oder diese Instanz ist eine
    Generalprobe, die RFC-0031 SS2.2 bewusst keinen Zwilling-Schluessel
    gibt.</p>
</div>"""


def group_html(group_key, group):
    rows = []
    for k, v in (group.get("attributes") or {}).items():
        rows.append(f"<tr><td>{esc(k)}</td><td>{esc(str(v.get('value')))}</td></tr>")
    attr_table = (f"<table><tr><th>Attribut</th><th>Wert</th></tr>{''.join(rows)}</table>"
                  if rows else "<p class='muted' style='margin:0'>keine Attribute</p>")
    return f"""<div class="card">
  <h2><code>{esc(group_key)}</code> <span class="badge">Herkunft: {esc(group.get('origin', '?'))}</span></h2>
  {attr_table}
</div>"""


def render_object(obj, notice=""):
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')
    parts.append(f"""<div class="card">
  <h2>{esc(obj['title'])} <span class="badge">{esc(obj['type'])}</span></h2>
  <p class="hint">ID:</p>
  <p><code>{esc(obj['id'])}</code></p>
  <p class="muted">Owner: {esc(obj['owner'])}</p>
</div>""")
    for gk, g in sorted((obj.get("groups") or {}).items()):
        parts.append(group_html(gk, g))
    return "".join(parts)


def render_home(notice=""):
    if not twin.configured():
        return not_configured_card()
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')

    rows = []
    for obj_id in known(MITARBEITER_TYPE):
        obj = twin.get_object(obj_id)
        if obj is None:
            continue
        rows.append(f"<tr><td><a href='/object?id={esc(obj_id)}'>{esc(obj['title'])}</a></td>"
                    f"<td class='muted'><code>{esc(obj_id)}</code></td></tr>")
    table = (f"<table><tr><th>Name</th><th>ID</th></tr>{''.join(rows)}</table>"
             if rows else "<p class='muted' style='margin:0'>noch keine angelegt</p>")

    parts.append(f"""<div class="cols">
  <div class="card">
    <h2>Mitarbeiter</h2>
    {table}
  </div>
  <div class="card">
    <h2>Mitarbeiter anlegen</h2>
    <form class="stack" method="post" action="/create">
      <label>Name<input type="text" name="title" required></label>
      <label>E-Mail<input type="text" name="Email"></label>
      <label>Telefon<input type="text" name="Phone"></label>
      <label>Abteilung<input type="text" name="Department"></label>
      <button type="submit">Anlegen</button>
    </form>
  </div>
</div>""")
    return "".join(parts)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-mitarbeiterverwaltung/" + VERSION

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def identity(self):
        return (self.headers.get("X-OAAP-User", ""), self.headers.get("X-OAAP-Roles", ""))

    def send_html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, doc):
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_form(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(max(0, min(length, 64 * 1024))).decode("utf-8", "replace")
        form = {}
        for pair in raw.split("&"):
            k, _, v = pair.partition("=")
            if k:
                form[unquote_plus(k)] = unquote_plus(v)
        return form

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION,
                                  "twin": twin.configured()})
            return
        user, roles = self.identity()
        if not roles:
            self.send_html(403, page('<div class="card"><p>Diese Seite braucht eine '
                                     'angemeldete Sitzung.</p></div>', "", ""))
            return
        if path == "/object":
            qs = parse_qs(parsed.query)
            obj_id = (qs.get("id") or [""])[0]
            try:
                obj = twin.get_object(obj_id) if obj_id else None
            except twin.TwinError as exc:
                self.send_html(200, page(
                    f'<div class="card attention"><p>{esc(exc.detail or str(exc))}</p></div>',
                    user, roles))
                return
            if obj is None:
                self.send_html(404, page(
                    '<div class="card attention"><p>Kein Objekt mit dieser ID.</p></div>',
                    user, roles))
                return
            self.send_html(200, page(render_object(obj), user, roles, obj["title"]))
            return
        if path == "/":
            self.send_html(200, page(render_home(), user, roles))
            return
        self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>', user, roles))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        user, roles = self.identity()
        if not roles:
            self.send_html(403, page('<div class="card"><p>Diese Seite braucht eine '
                                     'angemeldete Sitzung.</p></div>', "", ""))
            return
        if path != "/create":
            self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>', user, roles))
            return
        form = self.read_form()
        notice = ""
        redirect = "/"
        title = (form.get("title") or "").strip()
        if not title:
            notice = "Ohne Namen kann ich nichts anlegen."
        else:
            attrs = {k: form[k] for k in ("Email", "Phone", "Department") if form.get(k)}
            try:
                result = twin.create_object(MITARBEITER_TYPE, title, HR_GROUP,
                                             attributes=attrs or None)
                remember(result["id"], MITARBEITER_TYPE)
                redirect = f"/object?id={result['id']}"
            except twin.TwinError as exc:
                notice = exc.detail or str(exc)

        if notice:
            self.send_html(200, page(render_home(notice), user, roles))
            return
        self.send_response(303)
        self.send_header("Location", redirect)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    sys.stdout.write(f"OAAP Mitarbeiterverwaltung {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
