"""OAAP Projekt-App 0.1 -- Owner of Projekt, Consumer of Firma
(RFC-0031 Schritt 4, zweite Welle; program/zielbild-datenplattform.md
Runde 1, Punkt 5: "Projekte als Beispiel fuer 'gleiches Datenobjekt,
verschiedene Herkunft'").

Diese App DEFINIERT den Objekttyp 'Projekt' (Herkunft 'app:projekt',
oaap.data.model 0.1 SS2.3) -- aber sie ist nicht die einzige App, die
Projekt-Objekte ANLEGT: Partnerverwaltung bindet sich in ihrem eigenen
Manifest an denselben, schon registrierten Typ
('contributes: [{type: Projekt, role: owner}]', ohne ihn neu zu
definieren) und legt auf ihrer Firma-Seite eigene Projekt-Instanzen
an. Das ist der "geteilte digitale Zwilling" des Zielbilds: EIN Typ,
ZWEI unabhaengige Owner, jede Instanz mit ihrer eigenen Herkunft am
Objekt (RFC-0031 SS3.3) -- siehe oaap-app.yaml's Kommentar fuer den
Beweis am Code, dass das ohne RFC-0031 D1 zu verletzen geht.

'Firma' konsumiert diese App nur als Referenz (D6) -- wie RACI, keine
eigene Kopie, keine Suche (die kommt erst mit '/twin/references',
RFC-0031 SS1). Die Kunde-ID kommt aus Partnerverwaltungs eigener
Objektseite, in Klartext, genau dafuer gedacht.

Gebaut wie jede andere Referenz-App: kein eigener Login, ein
HTTP-Port, ein eigener Index NUR fuer die selbst angelegten
Projekt-IDs (id + type, kein Titel -- der Zwilling ist die einzige
Quelle), Konfiguration ueber deklarierte Variablen, Logs nach stdout,
Gesundheitspfad, nur Standardbibliothek (plus twin.py).

RFC-0035 (App-Design-Kontrakt Teil A): /platform/theme.css, Mini-
Kopfzeile (D4).
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

DATA_DIR = os.environ.get("PROJEKT_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

PROJEKT_TYPE = "Projekt"
PROJECT_GROUP = "project.core"


# --------------------------------------------------------- der eigene Index

def remember(obj_id):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INDEX_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"id": obj_id, "type": PROJEKT_TYPE}) + "\n")
    except OSError as exc:
        sys.stdout.write(f"Index nicht schreibbar: {exc}\n")


def known():
    seen = {}
    try:
        with open(INDEX_PATH, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") == PROJEKT_TYPE and row.get("id"):
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
           "%3E%3Crect x='14' y='14' width='34' height='34' fill='%232f6fed'/%3E%3Crect "
           "x='52' y='52' width='34' height='34' fill='%232f6fed' opacity='.5'/%3E%3C/svg%3E")

BACK_TO_PORTAL = ('href="/" onclick="location.href=location.protocol+\'//\'+'
                  "location.hostname+'/';return false;\"")


def page(body, user, roles, title="Projekt-App"):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} -- OAAP Projekt-App</title>
{STYLE}
<header class="oaap-header">
  <span class="oaap-header-title">Projekt-App</span>
  <a class="oaap-header-back" {BACK_TO_PORTAL}>&larr; Portal</a>
</header>
<main>{body}</main>
<footer style="max-width:52rem;margin:2rem auto 1.2rem;padding:0 var(--oaap-space-3, 16px);
  color:var(--oaap-color-text-muted, #5b616b);font-size:.8rem">
  OAAP Projekt-App {VERSION} -- Owner von Projekt, Consumer von Firma (RFC-0031 Schritt 4, zweite Welle)
</footer>
</html>"""


def not_configured_card():
    return """<div class="card attention">
  <h2>Kein Zwilling-Zugang</h2>
  <p class="hint">Diese Instanz hat weder <code>OAAP_TWIN_URL</code> noch
    <code>OAAP_PLATFORM_KEY</code>.</p>
</div>"""


def render_object(obj, notice=""):
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')
    group = (obj.get("groups") or {}).get(PROJECT_GROUP, {})
    rows = [f"<tr><td>{esc(k)}</td><td>{esc(str(v.get('value')))}</td></tr>"
            for k, v in (group.get("attributes") or {}).items()]
    attr_table = (f"<table><tr><th>Attribut</th><th>Wert</th></tr>{''.join(rows)}</table>"
                  if rows else "<p class='muted' style='margin:0'>keine Attribute</p>")
    kunde_html = "<p class='muted' style='margin:0'>keinem Kunden zugeordnet</p>"
    for rel in group.get("relations") or []:
        if rel["key"] != "forCustomer":
            continue
        try:
            kunde = twin.get_object(rel["target"])
        except twin.TwinError:
            kunde = None
        who = esc(kunde["title"]) if kunde else f"<code>{esc(rel['target'])}</code>"
        kunde_html = f"<p style='margin:0'>{who}</p>"
    parts.append(f"""<div class="card">
  <h2>{esc(obj['title'])} <span class="badge">{esc(obj['type'])}</span></h2>
  <p class="hint">ID:</p>
  <p><code>{esc(obj['id'])}</code></p>
  <p class="muted">Owner: {esc(obj['owner'])}</p>
</div>
<div class="card">
  <h2><code>{esc(PROJECT_GROUP)}</code> <span class="badge">Herkunft: {esc(group.get('origin', '?'))}</span></h2>
  {attr_table}
  <p class="hint" style="margin-top:.8rem"><b>Kunde (forCustomer)</b></p>
  {kunde_html}
</div>""")
    return "".join(parts)


def render_home(notice=""):
    if not twin.configured():
        return not_configured_card()
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')

    rows = []
    for obj_id in known():
        obj = twin.get_object(obj_id)
        if obj is None:
            continue
        rows.append(f"<tr><td><a href='/object?id={esc(obj_id)}'>{esc(obj['title'])}</a></td>"
                    f"<td class='muted'><code>{esc(obj_id)}</code></td></tr>")
    table = (f"<table><tr><th>Titel</th><th>ID</th></tr>{''.join(rows)}</table>"
             if rows else "<p class='muted' style='margin:0'>noch keine angelegt</p>")

    parts.append(f"""<div class="cols">
  <div class="card">
    <h2>Projekte (von dieser App angelegt)</h2>
    {table}
  </div>
  <div class="card">
    <h2>Projekt anlegen</h2>
    <p class="hint">Die Kunde-ID kommt aus <a href="../partnerverwaltung/">Partnerverwaltung</a>s
      Firma-Seite -- eine Referenz, keine eigene Ablage (D6).</p>
    <form class="stack" method="post" action="/create">
      <label>Titel<input type="text" name="title" required></label>
      <label>Status<input type="text" name="Status" placeholder="geplant"></label>
      <label>Beginn<input type="date" name="StartDate"></label>
      <label>Kunde-ID<input type="text" name="kunde_id" placeholder="urn:oaap:obj:..."></label>
      <button type="submit">Anlegen</button>
    </form>
  </div>
</div>""")
    return "".join(parts)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-projekt/" + VERSION

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
        kunde_id = (form.get("kunde_id") or "").strip()
        if not title:
            notice = "Ohne Titel kann ich nichts anlegen."
        else:
            attrs = {k: form[k] for k in ("Status", "StartDate") if form.get(k)}
            relations = [{"key": "forCustomer", "target": kunde_id}] if kunde_id else None
            try:
                result = twin.create_object(PROJEKT_TYPE, title, PROJECT_GROUP,
                                             attributes=attrs or None, relations=relations)
                remember(result["id"])
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
    sys.stdout.write(f"OAAP Projekt-App {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
