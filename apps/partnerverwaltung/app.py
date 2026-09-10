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

VERSION = "0.1.0"
PORT = 8000

DATA_DIR = os.environ.get("PARTNERVERWALTUNG_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

FIRMA_GROUP = "crm.core"
KONTAKT_GROUP = "crm.contact"


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

STYLE = """<style>
  :root{
    --oaap-blue-950:#172554; --oaap-blue-900:#1e3a8a; --oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb; --oaap-blue-100:#dbeafe;
    --oaap-bg:#f4f6fa; --oaap-surface:#fff; --oaap-text:#1f2937;
    --oaap-muted:#6b7280; --oaap-border:#e5e7eb;
    --ok:#15803d; --err:#b91c1c; --warn:#b45309;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       margin:0;background:var(--oaap-bg);color:var(--oaap-text)}
  header.oaap{background:linear-gradient(135deg,var(--oaap-blue-900),var(--oaap-blue-950));
       color:#fff;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;padding:.6rem 1.2rem}
  .brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;color:#fff}
  .brand b{font-size:1.15rem;letter-spacing:.08em}
  .brand small{display:block;font-size:.62rem;opacity:.75;letter-spacing:.02em}
  .userbox{display:flex;align-items:center;gap:.7rem;font-size:.9rem;margin-left:auto}
  .userbox .who{text-align:right;line-height:1.2}
  .userbox .who small{opacity:.75}
  main{max-width:62rem;margin:1.6rem auto;padding:0 1.2rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  code{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
  form.stack{display:flex;flex-direction:column;gap:.6rem;max-width:28rem}
  form.stack label{font-size:.82rem;color:var(--oaap-muted)}
  input,select{padding:.5rem .6rem;border-radius:.4rem;border:1px solid var(--oaap-border);
       font-size:.95rem;min-height:40px}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
  @media (max-width:720px){ .cols{grid-template-columns:1fr} .userbox .who{display:none} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")


def page(body, user, roles, title="Partnerverwaltung"):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} -- OAAP Partnerverwaltung</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">
    <span><b>PARTNERVERWALTUNG</b><small>Kunden, Ansprechpartner, Zwilling</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer style="max-width:62rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
  color:var(--oaap-muted);font-size:.8rem">
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
