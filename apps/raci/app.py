"""Zuständigkeiten (RACI) -- Contributor in ein fremdes Objekt (RFC-0031
Schritt 4, eine der Referenz-Apps des digitalen Zwillings; Oberfläche
nach RFC-0118: Rahmen, Listenbericht, Auswahl statt URN, Hilfe, MCP).

RACI hält fest, welche Kontaktperson für eine Firma verantwortlich ist:
eine Beziehung 'responsible' in der eigenen Gruppe 'raci.assignments' auf
der Firma -- einem Objekttyp, den RACI weder besitzt noch verändert. Der
Zwilling verweigert jedem anderen das Schreiben in diese Gruppe und RACI
das Schreiben in die Kerngruppe der Firma ("nobody else writes there").

RACI KONSUMIERT 'Firma' und 'Kontaktperson' nur als Referenz (Kennung,
Titel; bei der Kontaktperson zusätzlich die E-Mail) und zeigt auch nur
das -- nie die Stammdaten der Partnerverwaltung. Firmen und Personen
werden aus den Listen des Zwillings gewählt (GET /twin/objects?type=…,
eine Anfrage je Typ mit Titeln und Gruppen), nicht als URN eingefügt.
Keine eigene Ablage.

Kein eigener Login, ein HTTP-Port, Gesundheitspfad, nur
Standardbibliothek plus twin.py, appframe/ und mcpdoor/ (Kopien aus lib/).
"""
import html
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote_plus, urlparse

import appframe as af
import mcpdoor
import twin

APP_NAME = "Zuständigkeiten (RACI)"
APP_ID = "raci"
VERSION = "0.3.1"
PORT = 8000

ASSIGNMENT_GROUP = "raci.assignments"
FIRMA_TYPE = "Firma"
KONTAKT_TYPE = "Kontaktperson"

esc = html.escape


def responsible_of(firma):
    g = (firma.get("groups") or {}).get(ASSIGNMENT_GROUP) or {}
    return [r for r in g.get("relations") or [] if r.get("key") == "responsible" and r.get("target")]


def email_of(person):
    g = (person.get("groups") or {}).get("crm.contact") or {}
    v = (g.get("attributes") or {}).get("Email") or {}
    return str(v.get("value") or "")


def load(type_key):
    try:
        objs, truncated = twin.list_objects(type_key)
    except twin.TwinError as exc:
        label = "Firmen" if type_key == FIRMA_TYPE else "Kontaktpersonen"
        return [], f"Die Liste der {label} ist gerade nicht lesbar ({exc.status or 'keine Verbindung'})."
    return objs, ("Es werden die ersten 500 angezeigt." if truncated else "")


def person_title(pid, people):
    p = people.get(pid)
    if p:
        return p.get("title") or ""
    try:
        o = twin.get_object(pid)
    except twin.TwinError:
        o = None
    if o:
        people[pid] = o
        return o.get("title") or ""
    return "nicht lesbare Person"


# --------------------------------------------------------------- Optik

STYLE = (".rc-kennung code{overflow-wrap:anywhere}"
         ".rc-kennung button{margin-left:8px;padding:2px 8px;font-size:.85em}"
         ".rc-list{margin:0;padding-left:1.2rem}")


def render(title, body, obj=None, obj_title=None):
    # eine einzige Fläche -- keine Reiterleiste (RFC-0118 §6.1)
    return af.page(title, body, app=APP_NAME, version=VERSION, product=APP_ID,
                   obj=obj, obj_title=obj_title, style=STYLE)


def not_configured_card():
    return af.card("Keine Verbindung zum Zwilling",
                   "<p>Diese Instanz ist nicht mit dem Zwilling des Mandanten verbunden. "
                   "Ohne ihn kann sie keine Zuständigkeiten anzeigen oder festhalten. "
                   "Bitte die Betreuung der Plattform fragen.</p>", "oaap-warn")


def kennung(obj_id):
    return (f'<span class="rc-kennung"><code id="kennung">{esc(obj_id)}</code>'
            '<button type="button" onclick="navigator.clipboard&amp;&amp;navigator.clipboard.writeText('
            "document.getElementById('kennung').textContent)\">Kopieren</button></span>")


def render_list(params):
    """Übersicht: alle Firmen des Mandanten mit ihren Verantwortlichen."""
    firmen, n1 = load(FIRMA_TYPE)
    personen, n2 = load(KONTAKT_TYPE)
    people = {p["id"]: p for p in personen}

    def names(f):
        return ", ".join(person_title(r["target"], people) for r in responsible_of(f))
    q = af.Query(params, filters=("person", "offen"), default_sort="firma")
    used = sorted({r["target"] for f in firmen for r in responsible_of(f) if r["target"] in people},
                  key=lambda i: (people[i].get("title") or "").lower())
    rows = firmen
    if q.filters.get("person"):
        rows = [f for f in rows if any(r["target"] == q.filters["person"] for r in responsible_of(f))]
    if q.filters.get("offen") == "ohne":
        rows = [f for f in rows if not responsible_of(f)]
    elif q.filters.get("offen") == "mit":
        rows = [f for f in rows if responsible_of(f)]
    cols = [("Kunde (Firma)", "firma", lambda f: f.get("title") or "", ""),
            ("Verantwortlich", "verantwortlich", names, ""),
            ("Anzahl", "anzahl", lambda f: len(responsible_of(f)), "oaap-num")]

    def row(f):
        n = len(responsible_of(f))
        return (f'<tr><td><a href="?kunde={esc(quote(f["id"], safe=""))}">{esc(f.get("title") or "")}</a></td>'
                f'<td>{esc(names(f)) if n else af.badge("niemand zugewiesen", "warn")}</td>'
                f'<td class="oaap-num">{n}</td></tr>')
    body = (af.pagehead("Zuständigkeiten")
            + "".join(af.msg(n, error=True) for n in (n1, n2) if n)
            + af.list_report(
                q, rows, cols, row, noun="Firmen", placeholder="Firma oder Person",
                choices=[("person", "Verantwortlich", [(i, people[i].get("title") or "") for i in used]),
                         ("offen", "Zuweisung", [("ohne", "niemand zugewiesen"), ("mit", "mit Verantwortlichen")])],
                empty_text="Noch keine Firmen. Firmen werden in der Partnerverwaltung angelegt."))
    return body


def render_customer(customer_id, notice=""):
    """Die Seite einer Firma: wer verantwortlich ist, und wer es werden kann."""
    parts = [af.back("./", "Zurück zur Übersicht")]
    if notice:
        parts.append(af.msg(notice, error=True))
    try:
        customer = twin.get_object(customer_id)
    except twin.TwinError as exc:
        parts.append(af.msg(f"Diese Firma ist nicht lesbar ({exc.status or 'keine Verbindung'}).", error=True))
        return "".join(parts)
    if customer is None:
        parts.append(af.msg("Kein Kunde mit dieser Kennung.", error=True))
        return "".join(parts)
    if customer["type"] != FIRMA_TYPE:
        parts.append(af.msg(f"„{customer['title']}“ ist keine Firma.", error=True))
        return "".join(parts)
    personen, note = load(KONTAKT_TYPE)
    people = {p["id"]: p for p in personen}
    assigned = responsible_of(customer)
    parts.append(f'<div class="oaap-objhead"><h1>{esc(customer["title"])}</h1>'
                 + af.facts([("Art", "Kunde (Firma)"), ("Verantwortlich", str(len(assigned))),
                             ("Kennung", kennung(customer["id"]))]) + "</div>")
    if note:
        parts.append(af.msg(note, error=True))
    if assigned:
        items = []
        for r in assigned:
            mail = email_of(people.get(r["target"]) or {})
            since = f' <span class="oaap-muted">seit {esc(str(r["valid_from"]))}</span>' if r.get("valid_from") else ""
            items.append(f"<li><b>{esc(person_title(r['target'], people))}</b>"
                         f"{' · ' + esc(mail) if mail else ''}{since}</li>")
        inner = f'<ul class="rc-list">{"".join(items)}</ul>'
    else:
        inner = af.empty("Noch niemand verantwortlich. Unten eine Kontaktperson wählen und zuweisen.")
    parts.append(af.card("Verantwortlich", inner))
    taken = {r["target"] for r in assigned}
    opts = "".join(f'<option value="{esc(p["id"])}">{esc(p.get("title") or "")}'
                   f'{" (" + esc(email_of(p)) + ")" if email_of(p) else ""}</option>'
                   for p in sorted(personen, key=lambda p: (p.get("title") or "").lower())
                   if p["id"] not in taken)
    if opts:
        form = ('<form class="oaap-form" method="post" action="assign">'
                f'<input type="hidden" name="customer_id" value="{esc(customer["id"])}">'
                f'<label>Kontaktperson<select name="person_id" required><option value="">– bitte wählen –</option>{opts}</select></label>'
                '<div><button type="submit" class="primary">Als verantwortlich zuweisen</button></div></form>')
    else:
        form = af.empty("Keine weitere Kontaktperson zur Auswahl. Kontaktpersonen werden in der "
                        "Partnerverwaltung angelegt.")
    parts.append(af.card("Zuweisen", form))
    return "".join(parts)


def render_home(kunde_id=None, notice="", params=None):
    if not twin.configured():
        return not_configured_card()
    if kunde_id:
        return render_customer(kunde_id, notice)
    return (af.msg(notice, error=True) if notice else "") + render_list(params or {})


# ------------------------------------------------------------- MCP-Tür

door = mcpdoor.Door(f"oaap-{APP_ID}", VERSION,
                    "Zuständigkeiten lesen: welche Kontaktperson für welche Firma verantwortlich ist.")


def _need_session(who):
    # dieselbe Prüfung wie die Seiten: ohne Rollen keine Daten
    if not who.get("roles"):
        raise mcpdoor.Refused("Dafür braucht es eine angemeldete Sitzung.")
    if not twin.configured():
        raise mcpdoor.Refused("Diese Instanz ist nicht mit dem Zwilling verbunden.")


def _list_or_refuse(type_key):
    try:
        return twin.list_objects(type_key)
    except twin.TwinError as exc:
        raise mcpdoor.Refused(f"Der Zwilling antwortet nicht ({exc.status or 'keine Verbindung'}).") from None


@door.tool("raci_search", "Zuständigkeiten suchen",
           "Sucht Firmen mit ihren Verantwortlichen, nach Firmen- oder Personenname. "
           "Mit ohne_verantwortliche=true nur Firmen, für die niemand zuständig ist.",
           {"q": {"type": "string", "description": "Firma oder Person, leer für alle"},
            "ohne_verantwortliche": {"type": "boolean", "description": "nur Firmen ohne Verantwortliche"},
            "limit": {"type": "integer", "description": "höchstens so viele Treffer (1–50, Standard 20)"}})
def _mcp_search(args, who):
    _need_session(who)
    firmen, trunc = _list_or_refuse(FIRMA_TYPE)
    personen, _t = _list_or_refuse(KONTAKT_TYPE)
    people = {p["id"]: p for p in personen}
    try:
        limit = max(1, min(50, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    q = af.Query({"q": str(args.get("q") or "")})
    hits = []
    for f in firmen:
        names = [people.get(r["target"], {}).get("title") or "nicht lesbare Person" for r in responsible_of(f)]
        if args.get("ohne_verantwortliche") is True and names:
            continue
        if q.matches(f.get("title"), " ".join(names)):
            hits.append({"kennung": f["id"], "firma": f.get("title"), "verantwortlich": names})
    return {"treffer": hits[:limit], "anzahl": len(hits), "gekuerzt": len(hits) > limit or trunc}


@door.tool("raci_read", "Zuständigkeit einer Firma lesen",
           "Liest, wer für eine Firma verantwortlich ist, mit E-Mail und Datum.",
           {"kennung": {"type": "string", "description": "Kennung der Firma aus raci_search"}},
           required=("kennung",))
def _mcp_read(args, who):
    _need_session(who)
    oid = str(args.get("kennung") or "").strip()
    if not oid:
        raise mcpdoor.Refused("Welche Kennung?")
    try:
        firma = twin.get_object(oid)
    except twin.TwinError as exc:
        raise mcpdoor.Refused(f"Nicht lesbar ({exc.status or 'keine Verbindung'}).") from None
    if not firma or firma.get("type") != FIRMA_TYPE:
        raise mcpdoor.Refused("Keine Firma mit dieser Kennung.")
    personen, _t = _list_or_refuse(KONTAKT_TYPE)
    people = {p["id"]: p for p in personen}
    return {"kennung": firma["id"], "firma": firma["title"],
            "verantwortlich": [{"kennung": r["target"], "name": person_title(r["target"], people),
                                "email": email_of(people.get(r["target"]) or {}) or None,
                                "seit": r.get("valid_from")} for r in responsible_of(firma)]}


door.prompt("raci_wer_ist_zustaendig", "Wer ist zuständig?",
            "Findet die Verantwortlichen für eine Firma.",
            "Wer ist für die Firma „{firma}“ verantwortlich? Nutze raci_search und raci_read und nenne "
            "die Personen mit E-Mail.", arguments=("firma",))
door.prompt("raci_luecken", "Firmen ohne Zuständigkeit",
            "Listet die Firmen, für die noch niemand verantwortlich ist.",
            "Für welche Firmen ist noch niemand verantwortlich? Nutze raci_search mit "
            "ohne_verantwortliche=true und nenne die Firmen beim Namen.")


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

    def page(self, status, body, kunde_title=None, kunde_id=None):
        self.send_html(status, render(kunde_title or "Übersicht", body,
                                      obj=f"Firma:{kunde_id}" if kunde_id else None, obj_title=kunde_title))

    def refuse(self):
        self.page(403, af.card("", "<p>Diese Seite braucht eine angemeldete Sitzung.</p>"))

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/mcp":
            door.serve(self)
            return
        path = parsed.path.rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION,
                                  "twin": twin.configured()})
            return
        _user, roles = self.identity()
        if not roles:
            self.refuse()
            return
        if path != "/":
            self.page(404, af.empty('Diese Seite gibt es nicht. <a href="./">Zur Übersicht</a>'))
            return
        qs = parse_qs(parsed.query)
        kunde_id = (qs.get("kunde") or [""])[0].strip()
        # der Titel der Firma steht im Objektkopf; für Fragen/Feedback reicht die Kennung
        self.page(200, render_home(kunde_id or None, params=qs), None, kunde_id or None)

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/mcp":
            door.serve(self)
            return
        path = path.rstrip("/") or "/"
        _user, roles = self.identity()
        if not roles:
            self.refuse()
            return
        if path != "/assign":
            self.page(404, af.empty("Diese Aktion gibt es nicht."))
            return
        form = self.read_form()
        customer_id = (form.get("customer_id") or "").strip()
        person_id = (form.get("person_id") or "").strip()
        notice = ""
        if not (customer_id and person_id):
            notice = "Bitte eine Kontaktperson wählen."
        else:
            try:
                twin.write_group(customer_id, ASSIGNMENT_GROUP,
                                 relations=[{"key": "responsible", "target": person_id}])
            except twin.TwinError as exc:
                notice = f"Zuweisen abgelehnt: {exc.detail or exc}"
        if notice:
            self.page(200, render_home(customer_id or None, notice), None, customer_id or None)
            return
        self.send_response(303)
        self.send_header("Location", "/?kunde=" + quote(customer_id, safe=""))
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    sys.stdout.write(f"{APP_NAME} {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
