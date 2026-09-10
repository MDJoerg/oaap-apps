"""OAAP Partnerverwaltung 0.1 -- Owner of Firma and Kontaktperson
(RFC-0031 Schritt 4, the digital twin's reference apps; RFC-0031's own
prose calls these "Customer"/"Person" -- this app's REGISTERED type
keys are 'Firma'/'Kontaktperson' instead, because 'Customer'/'Person'
are already permanently registered on oaap-test by a throwaway probe
from Schritt 2's own live verification, and oaap.data.model 0.1 has no
way to release a type key again -- see the module comment in
oaap-app.yaml).

Diese App ist der ERSTE Beweis, kein Werkzeug: RFC-0031 SS9 nennt
"Partner management (owner) creates Muller GmbH and Anna, relates them
with isContactOf valid from 2019" als Schritt 1 des Konformitats-
Szenarios, und oaap.data.twin 0.1 baut genau das als sein eigenes
Minimum. Diese App macht daraus etwas, das ein Mensch anklicken kann:

- Firmen und Kontaktpersonen ANLEGEN -- als Owner (RFC-0031 SS3.3: "the
  owner is the origin that created it"), mit ihrer eigenen Kern-Gruppe
  (crm.core auf Firma, crm.contact auf Kontaktperson);
- eine Kontaktperson mit einer Firma VERKNUPFEN (isContactOf, gultig ab
  einem Datum -- die Gultigkeitsachse, die RFC-0031 als das eine Ding
  nennt, das der Branchenstandard nicht kann);
- eine Aktivitat (PhoneCall/Task) auf einem Objekt erfassen.

Was diese App NICHT tut: eine eigene Kopie der Daten halten. Der
Zwilling ist die einzige Quelle fur Titel, Attribute und Beziehungen;
diese App merkt sich unter dem deklarierten Mount NUR die IDs, die sie
selbst angelegt hat (id + type, kein Titel, kein Attribut) -- ohne das
gabe es keinen Weg, "meine eigenen Kunden" auf einer Seite zu zeigen,
weil oaap.data.twin 0.1 noch keine Liste/Suche kennt (SS1: '/twin/
references' kommt erst spater). Jede Seite liest den aktuellen Stand
frisch vom Zwilling -- der Fallstrick aus dem Bauplan ("wer den
Kundennamen in die eigene Tabelle schreibt, hat den Zwilling umgangen
und merkt es erst beim Umbenennen") gilt hier nicht, weil es nichts
Umbenennbares in der eigenen Ablage gibt.

Gebaut wie jede andere Referenz-App: kein eigener Login (geprufte
Identitat als Gateway-Kopfzeile), ein HTTP-Port, Persistenz nur unter
dem deklarierten Mount, Konfiguration uber deklarierte Variablen, Logs
nach stdout, Gesundheitspfad, nur Standardbibliothek (plus twin.py,
der stille Vertrag mit oaap.data.twin).
"""
import html
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

import twin

VERSION = "0.1.2"
PORT = 8000

DATA_DIR = os.environ.get("PARTNERVERWALTUNG_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

FIRMA_GROUP = "crm.core"
KONTAKT_GROUP = "crm.contact"
# RFC-0031 Zielbild Runde 1, Punkt 5 / zweite Welle von Schritt 4:
# 'Projekt' ist von der Projekt-App (../projekt/) definiert und
# registriert; diese App bindet sich nur an den schon registrierten
# Typ (siehe oaap-app.yaml), legt aber eigene Instanzen an -- der
# geteilte digitale Zwilling: ein Typ, zwei Owner.
PROJEKT_TYPE = "Projekt"
PROJECT_GROUP = "project.core"


# --------------------------------------------------------- der eigene Index

def remember(obj_id, type_key):
    """id + type, NICHTS sonst -- siehe Modulkommentar. Ein Duplikat
    (dieselbe id zweimal) ist harmlos, weil `known()` dedupliziert;
    doppelt anhaengen ist billiger als vorher nachsehen."""
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
  main{max-width:62rem;margin:1.6rem auto;padding:0 var(--oaap-space-3, 16px)}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-color-surface, #f4f5f7);
       border:1px solid var(--oaap-color-border, #d8dbe0);
       border-radius:var(--oaap-radius, 6px);padding:1.4rem;margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:#fff;color:var(--oaap-color-primary, #2f6fed);white-space:nowrap;
       border:1px solid var(--oaap-color-border, #d8dbe0)}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;
       border-radius:var(--oaap-radius, 6px);
       background:var(--oaap-color-primary, #2f6fed);
       color:var(--oaap-color-primary-text, #fff);text-decoration:none;
       font-size:.95rem;cursor:pointer;min-height:44px}
  .hint{font-size:.8rem;color:var(--oaap-color-text-muted, #5b616b);margin:0 0 .6rem}
  .muted{color:var(--oaap-color-text-muted, #5b616b);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;
       border-bottom:1px solid var(--oaap-color-border, #d8dbe0);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;
       color:var(--oaap-color-text-muted, #5b616b)}
  code{background:var(--oaap-color-surface, #f4f5f7);border-radius:.25rem;
       padding:.1rem .35rem;font-size:.86rem;word-break:break-all}
  form.stack{display:flex;flex-direction:column;gap:.6rem;max-width:28rem}
  form.stack label{font-size:.82rem;color:var(--oaap-color-text-muted, #5b616b)}
  input,select{padding:.5rem .6rem;border-radius:var(--oaap-radius, 6px);
       border:1px solid var(--oaap-color-border, #d8dbe0);font-size:.95rem;min-height:40px}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
  @media (max-width:720px){ .cols{grid-template-columns:1fr} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

BACK_TO_PORTAL = ('href="/" onclick="location.href=location.protocol+\'//\'+'
                  "location.hostname+'/';return false;\"")


def page(body, user, roles, title="Partnerverwaltung"):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} -- OAAP Partnerverwaltung</title>
{STYLE}
<header class="oaap-header">
  <span class="oaap-header-title">Partnerverwaltung</span>
  <a class="oaap-header-back" {BACK_TO_PORTAL}>&larr; Portal</a>
</header>
<main>{body}</main>
<footer style="max-width:62rem;margin:2rem auto 1.2rem;padding:0 var(--oaap-space-3, 16px);
  color:var(--oaap-color-text-muted, #5b616b);font-size:.8rem">
  OAAP Partnerverwaltung {VERSION} -- Owner von Firma und Kontaktperson (RFC-0031 Schritt 4)
</footer>
</html>"""


def not_configured_card():
    return """<div class="card attention">
  <h2>Kein Zwilling-Zugang</h2>
  <p class="hint">Diese Instanz hat weder <code>OAAP_TWIN_URL</code> noch
    <code>OAAP_PLATFORM_KEY</code> -- entweder tragt dieser Knoten das
    Profil <code>store</code> nicht (<code>oaap node add-profile store</code>),
    oder diese Instanz ist eine Generalprobe, die RFC-0031 SS2.2 bewusst
    keinen Zwilling-Schluessel gibt.</p>
</div>"""


def group_html(group_key, group):
    rows = []
    for k, v in (group.get("attributes") or {}).items():
        rows.append(f"<tr><td>{esc(k)}</td><td>{esc(str(v.get('value')))}</td>"
                    f"<td class='muted'>{esc(v.get('valid_from') or '')}"
                    f"{' – ' + esc(v['valid_to']) if v.get('valid_to') else ''}</td></tr>")
    rel_rows = "".join(
        f"<li><code>{esc(r['key'])}</code> &rarr; <code>{esc(r['target'])}</code>"
        f" <span class='muted'>{esc(r.get('valid_from') or '')}"
        f"{' – ' + esc(r['valid_to']) if r.get('valid_to') else ''}</span></li>"
        for r in group.get("relations") or [])
    act_rows = "".join(
        f"<li><code>{esc(a['key'])}</code>"
        f"{' &rarr; ' + esc(a['target']) if a.get('target') else ''}"
        f" <span class='badge'>{esc(a.get('status') or '')}</span></li>"
        for a in group.get("activities") or [])
    attr_table = (f"<table><tr><th>Attribut</th><th>Wert</th><th>Gultig</th></tr>{''.join(rows)}</table>"
                  if rows else "<p class='muted' style='margin:0'>keine Attribute</p>")
    return f"""<div class="card">
  <h2><code>{esc(group_key)}</code> <span class="badge">Herkunft: {esc(group.get('origin', '?'))}</span></h2>
  {attr_table}
  {"<p class='hint' style='margin-top:.8rem'><b>Relationen</b></p><ul>" + rel_rows + "</ul>" if rel_rows else ""}
  {"<p class='hint' style='margin-top:.8rem'><b>Aktivitaten</b></p><ul>" + act_rows + "</ul>" if act_rows else ""}
</div>"""


def render_projects(firma_id):
    """Projekte, die DIESE App fuer diese Firma angelegt hat -- aus dem
    eigenen Index gefiltert, dann frisch vom Zwilling gelesen (kein
    Titel im Index, siehe Modulkommentar). Zeigt nur, was diese App
    selbst weiss: die Projekt-App legt moeglicherweise WEITERE Projekte
    auf derselben Firma an, von denen dieser Index nichts sieht -- der
    Punkt des geteilten Zwillings, nicht ein Fehler dieser Liste."""
    rows = []
    for obj_id in known(PROJEKT_TYPE):
        proj = twin.get_object(obj_id)
        if proj is None:
            continue
        group = (proj.get("groups") or {}).get(PROJECT_GROUP, {})
        target = next((r["target"] for r in group.get("relations") or []
                       if r["key"] == "forCustomer"), None)
        if target != firma_id:
            continue
        rows.append(f"<li><a href='/object?id={esc(obj_id)}'>{esc(proj['title'])}</a></li>")
    if not rows:
        return ""
    return f"""<div class="card">
  <h2>Projekte (von dieser App angelegt)</h2>
  <ul>{''.join(rows)}</ul>
</div>"""


def render_object(obj, notice=""):
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')
    parts.append(f"""<div class="card">
  <h2>{esc(obj['title'])} <span class="badge">{esc(obj['type'])}</span></h2>
  <p class="hint">ID (an andere Apps weiterzugeben, z. B. an RACI):</p>
  <p><code>{esc(obj['id'])}</code></p>
  <p class="muted">Owner: {esc(obj['owner'])}</p>
</div>""")
    for gk, g in sorted((obj.get("groups") or {}).items()):
        parts.append(group_html(gk, g))

    if obj["type"] == "Firma":
        parts.append(render_projects(obj["id"]))
        parts.append(f"""<div class="card">
  <h2>Projekt anlegen (Projekt-App's Typ, diese App als zweiter Owner)</h2>
  <p class="hint">Bindet sich an <code>Projekt</code>, definiert von
    <a href="../projekt/">Projekt-App</a> -- der geteilte digitale
    Zwilling (Zielbild §1.5): ein Typ, zwei Owner.</p>
  <form class="stack" method="post" action="/create/projekt">
    <input type="hidden" name="kunde_id" value="{esc(obj['id'])}">
    <label>Titel<input type="text" name="title" required></label>
    <label>Status<input type="text" name="Status" placeholder="geplant"></label>
    <button type="submit">Anlegen</button>
  </form>
</div>""")

    if obj["type"] == "Kontaktperson":
        parts.append(f"""<div class="card">
  <h2>Als Ansprechpartner verknupfen (isContactOf)</h2>
  <p class="hint">Verbindet diese Kontaktperson mit einer Firma -- gultig ab
    einem Datum, wie im Konformitats-Szenario (RFC-0031 SS9 Schritt 1: "relates
    them with isContactOf valid from 2019").</p>
  <form class="stack" method="post" action="/link">
    <input type="hidden" name="person_id" value="{esc(obj['id'])}">
    <label>Firma-ID<input type="text" name="customer_id" required
      placeholder="urn:oaap:obj:..."></label>
    <label>Gultig ab<input type="date" name="valid_from" value="{date.today().isoformat()}"></label>
    <button type="submit">Verknupfen</button>
  </form>
</div>""")

    parts.append(f"""<div class="card">
  <h2>Aktivitat erfassen</h2>
  <form class="stack" method="post" action="/activity">
    <input type="hidden" name="object_id" value="{esc(obj['id'])}">
    <input type="hidden" name="group_key" value="{esc(FIRMA_GROUP if obj['type'] == 'Firma' else KONTAKT_GROUP)}">
    <label>Art<select name="activity_key">
      <option value="PhoneCall">Anruf (PhoneCall)</option>
      <option value="Task">Aufgabe (Task)</option>
    </select></label>
    <button type="submit">Erfassen</button>
  </form>
</div>""")
    return "".join(parts)


def render_home(notice=""):
    parts = []
    if not twin.configured():
        return not_configured_card()
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')

    for type_key, group_key, label, singular, fields in (
            ("Firma", FIRMA_GROUP, "Firmen", "Firma", [
                ("title", "Name", "text", True),
                ("VatId", "USt-ID", "text", False),
                ("Email", "E-Mail", "text", False),
                ("Location", "Standort", "text", False),
                ("Phone", "Telefon", "text", False)]),
            ("Kontaktperson", KONTAKT_GROUP, "Kontaktpersonen", "Kontaktperson", [
                ("title", "Name", "text", True),
                ("Email", "E-Mail", "text", False),
                ("Phone", "Telefon", "text", False)])):
        rows = []
        for obj_id in known(type_key):
            obj = twin.get_object(obj_id)
            if obj is None:
                continue
            rows.append(f"<tr><td><a href='/object?id={esc(obj_id)}'>{esc(obj['title'])}</a></td>"
                        f"<td class='muted'><code>{esc(obj_id)}</code></td></tr>")
        table = (f"<table><tr><th>Name</th><th>ID</th></tr>{''.join(rows)}</table>"
                 if rows else "<p class='muted' style='margin:0'>noch keine angelegt</p>")
        form_fields = "".join(
            f"<label>{esc(flabel)}<input type='{ftype}' name='{fkey}' {'required' if req else ''}></label>"
            for fkey, flabel, ftype, req in fields)
        parts.append(f"""<div class="cols">
  <div class="card">
    <h2>{esc(label)}</h2>
    {table}
  </div>
  <div class="card">
    <h2>{esc(singular)} anlegen</h2>
    <form class="stack" method="post" action="/create/{type_key.lower()}">
      {form_fields}
      <button type="submit">Anlegen</button>
    </form>
  </div>
</div>""")
    return "".join(parts)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-partnerverwaltung/" + VERSION

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
        form = self.read_form()
        notice = ""
        redirect = "/"

        try:
            if path in ("/create/firma", "/create/kontaktperson"):
                type_key = "Firma" if path.endswith("firma") else "Kontaktperson"
                group_key = FIRMA_GROUP if type_key == "Firma" else KONTAKT_GROUP
                title = (form.get("title") or "").strip()
                if not title:
                    notice = "Ohne Namen kann ich nichts anlegen."
                else:
                    attrs = {k: form[k] for k in ("VatId", "Email", "Location", "Phone")
                             if form.get(k)}
                    result = twin.create_object(type_key, title, group_key, attributes=attrs or None)
                    remember(result["id"], type_key)
                    redirect = f"/object?id={result['id']}"
            elif path == "/create/projekt":
                title = (form.get("title") or "").strip()
                kunde_id = (form.get("kunde_id") or "").strip()
                if not title:
                    notice = "Ohne Titel kann ich nichts anlegen."
                else:
                    attrs = {k: form[k] for k in ("Status",) if form.get(k)}
                    relations = ([{"key": "forCustomer", "target": kunde_id}]
                                 if kunde_id else None)
                    result = twin.create_object(PROJEKT_TYPE, title, PROJECT_GROUP,
                                                attributes=attrs or None, relations=relations)
                    remember(result["id"], PROJEKT_TYPE)
                    redirect = f"/object?id={kunde_id}" if kunde_id else f"/object?id={result['id']}"
            elif path == "/link":
                person_id = (form.get("person_id") or "").strip()
                customer_id = (form.get("customer_id") or "").strip()
                valid_from = (form.get("valid_from") or "").strip() or None
                if not (person_id and customer_id):
                    notice = "Kontaktperson- und Firma-ID werden beide gebraucht."
                else:
                    twin.write_group(person_id, KONTAKT_GROUP, relations=[
                        {"key": "isContactOf", "target": customer_id, "valid_from": valid_from}])
                    redirect = f"/object?id={person_id}"
            elif path == "/activity":
                object_id = (form.get("object_id") or "").strip()
                group_key = (form.get("group_key") or "").strip()
                activity_key = (form.get("activity_key") or "").strip()
                if not (object_id and group_key and activity_key):
                    notice = "Objekt, Gruppe und Art werden gebraucht."
                else:
                    twin.write_group(object_id, group_key, activities=[
                        {"key": activity_key, "status": "open"}])
                    redirect = f"/object?id={object_id}"
            else:
                self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>',
                                         user, roles))
                return
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
    sys.stdout.write(f"OAAP Partnerverwaltung {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
