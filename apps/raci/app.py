"""OAAP RACI 0.1 -- Contributor into someone else's object (RFC-0031
Schritt 4, the digital twin's reference apps).

The second half of the proof [Partnerverwaltung](../partnerverwaltung/)
started: RFC-0031 SS9 Schritt 3 -- *"RACI (contributor) reads both [the
customer and the employee] as references, writes `responsible`
relations into `raci.assignments` on Muller GmbH."* This app does
exactly that, and nothing this app owns can be written by anyone else:
`raci.assignments` is RACI's OWN group type (D1: "others attach their
own group types") on `Customer`, an object type it neither owns nor
alters -- the twin refuses any write to it from any other origin
(RFC-0031 SS3.3, "nobody else writes there"), and refuses THIS app
writing into `Customer`'s own core group even by naming it explicitly.

RACI CONSUMES `Customer` and `Person` -- it reads them only as
references (D6: platform id, type, title, origin -- "more only through
a consolidated model the tenant owns"). It never reads or writes
`crm.core`/`crm.contact`, and it holds NO storage of its own: every
Kunde/Mitarbeiter-ID a person pastes in is looked up fresh from the
twin, every time. There is deliberately no search here yet --
`/twin/references` (RFC-0031 SS1) is not built in 0.1 -- so the id
comes from [Partnerverwaltung](../partnerverwaltung/)'s own object
page, which shows it in plain text for exactly this purpose.

Gebaut wie jede andere Referenz-App: kein eigener Login, ein
HTTP-Port, keine Persistenz, Konfiguration ueber deklarierte Variablen,
Logs nach stdout, Gesundheitspfad, nur Standardbibliothek (plus
twin.py).
"""
import html
import sys
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

import twin

VERSION = "0.1.0"
PORT = 8000

ASSIGNMENT_GROUP = "raci.assignments"

esc = html.escape

STYLE = """<style>
  :root{
    --oaap-blue-950:#172554; --oaap-blue-900:#1e3a8a; --oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb; --oaap-blue-100:#dbeafe;
    --oaap-bg:#f4f6fa; --oaap-surface:#fff; --oaap-text:#1f2937;
    --oaap-muted:#6b7280; --oaap-border:#e5e7eb;
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
  main{max-width:52rem;margin:1.6rem auto;padding:0 1.2rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .muted{color:var(--oaap-muted);font-size:.9rem}
  form.stack{display:flex;flex-direction:column;gap:.6rem;max-width:32rem}
  form.stack label{font-size:.82rem;color:var(--oaap-muted)}
  input{padding:.5rem .6rem;border-radius:.4rem;border:1px solid var(--oaap-border);
       font-size:.95rem;min-height:40px}
  ul.assign{list-style:none;margin:0;padding:0}
  ul.assign li{padding:.5rem 0;border-bottom:1px solid var(--oaap-border)}
  ul.assign li:last-child{border-bottom:none}
  code{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")


def page(body, user, roles):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>RACI -- OAAP</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">
    <span><b>RACI</b><small>responsible, aus der Referenz</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer style="max-width:52rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
  color:var(--oaap-muted);font-size:.8rem">
  OAAP RACI {VERSION} -- Contributor in raci.assignments, Consumer von Customer/Person (RFC-0031 Schritt 4)
</footer>
</html>"""


def not_configured_card():
    return """<div class="card attention">
  <h2>Kein Zwilling-Zugang</h2>
  <p class="hint">Diese Instanz hat weder <code>OAAP_TWIN_URL</code> noch
    <code>OAAP_PLATFORM_KEY</code> -- entweder tragt dieser Knoten das
    Profil <code>store</code> nicht, oder diese Instanz ist eine
    Generalprobe, die RFC-0031 SS2.2 bewusst keinen Zwilling-Schluessel
    gibt.</p>
</div>"""


def load_form(notice=""):
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')
    parts.append("""<div class="card">
  <h2>Kunde laden</h2>
  <p class="hint">Die ID kommt aus <a href="../partnerverwaltung/">Partnerverwaltung</a>s
    Objektseite -- eine Referenz, keine eigene Ablage (D6). Eine Suche gibt
    es in oaap.data.twin 0.1 noch nicht.</p>
  <form class="stack" method="get" action="/">
    <label>Kunde-ID<input type="text" name="kunde" required placeholder="urn:oaap:obj:..."></label>
    <button type="submit">Laden</button>
  </form>
</div>""")
    return "".join(parts)


def render_customer(customer_id, notice=""):
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')
    try:
        customer = twin.get_object(customer_id)
    except twin.TwinError as exc:
        parts.append(f'<div class="card attention"><p>{esc(exc.detail or str(exc))}</p></div>')
        return "".join(parts)
    if customer is None:
        parts.append('<div class="card attention"><p>Kein Kunde mit dieser ID.</p></div>')
        return "".join(parts)
    if customer["type"] != "Customer":
        parts.append(f'<div class="card attention"><p>'
                      f'<code>{esc(customer_id)}</code> ist ein {esc(customer["type"])}, '
                      f'kein Customer.</p></div>')
        return "".join(parts)

    group = (customer.get("groups") or {}).get(ASSIGNMENT_GROUP, {})
    rows = []
    for rel in group.get("relations") or []:
        if rel["key"] != "responsible":
            continue
        try:
            person = twin.get_object(rel["target"])
        except twin.TwinError:
            person = None
        who = esc(person["title"]) if person else f"<code>{esc(rel['target'])}</code>"
        rows.append(f"<li><b>{who}</b> <span class='badge'>responsible</span></li>")
    assignments = (f"<ul class='assign'>{''.join(rows)}</ul>" if rows
                   else "<p class='muted' style='margin:0'>noch niemand zugewiesen</p>")

    parts.append(f"""<div class="card">
  <h2>{esc(customer['title'])} <span class="badge">Customer</span></h2>
  <p class="muted"><code>{esc(customer_id)}</code></p>
  <p class="hint" style="margin-top:.8rem"><b>raci.assignments</b> -- verantwortlich (responsible):</p>
  {assignments}
</div>
<div class="card">
  <h2>Zuweisen</h2>
  <p class="hint">Die Mitarbeiter-ID kommt ebenfalls aus
    <a href="../partnerverwaltung/">Partnerverwaltung</a> (dort als Person angelegt).</p>
  <form class="stack" method="post" action="/assign">
    <input type="hidden" name="customer_id" value="{esc(customer_id)}">
    <label>Mitarbeiter-ID<input type="text" name="person_id" required
      placeholder="urn:oaap:obj:..."></label>
    <button type="submit">Als verantwortlich zuweisen</button>
  </form>
</div>""")
    return "".join(parts)


def render_home(kunde_id=None, notice=""):
    if not twin.configured():
        return not_configured_card()
    body = load_form(notice if not kunde_id else "")
    if kunde_id:
        body += render_customer(kunde_id, notice if kunde_id else "")
    return body


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-raci/" + VERSION

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
        if path != "/":
            self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>', user, roles))
            return
        kunde_id = (parse_qs(parsed.query).get("kunde") or [""])[0].strip()
        self.send_html(200, page(render_home(kunde_id or None), user, roles))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        user, roles = self.identity()
        if not roles:
            self.send_html(403, page('<div class="card"><p>Diese Seite braucht eine '
                                     'angemeldete Sitzung.</p></div>', "", ""))
            return
        if path != "/assign":
            self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>', user, roles))
            return
        form = self.read_form()
        customer_id = (form.get("customer_id") or "").strip()
        person_id = (form.get("person_id") or "").strip()
        notice = ""
        if not (customer_id and person_id):
            notice = "Kunde- und Mitarbeiter-ID werden beide gebraucht."
        else:
            try:
                twin.write_group(customer_id, ASSIGNMENT_GROUP,
                                 relations=[{"key": "responsible", "target": person_id}])
            except twin.TwinError as exc:
                notice = exc.detail or str(exc)
        self.send_html(200, page(render_home(customer_id or None, notice), user, roles))


def main():
    sys.stdout.write(f"OAAP RACI {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
