"""Projekte -- Owner von Projekt, Consumer von Firma (RFC-0031 Schritt 4,
zweite Welle; Oberfläche nach RFC-0118: Rahmen, Listenbericht, Auswahl
statt URN, Hilfe, MCP).

Diese App DEFINIERT den Objekttyp 'Projekt' (Herkunft 'app:projekt') --
aber sie ist nicht die einzige App, die Projekte ANLEGT: die
Partnerverwaltung bindet sich an denselben Typ und legt auf ihrer
Firma-Seite eigene Projekte an. Ein Typ, zwei unabhängige Owner, jede
Instanz mit ihrer eigenen Herkunft. Die Liste hier zeigt darum ALLE
Projekte des Mandanten aus dem Zwilling (GET /twin/objects?type=Projekt,
eine Anfrage mit Titeln und Gruppen), mit dem Vermerk, welche App sie
angelegt hat.

'Firma' konsumiert diese App nur als Referenz: der Kunde wird aus der
Liste der Firmen des Zwillings gewählt (GET /twin/objects?type=Firma),
nicht als URN eingefügt.

Der alte Index unter /data (nur id + type) wird weiter fortgeschrieben,
aber nicht mehr gelesen. Kein eigener Login, ein HTTP-Port,
Gesundheitspfad, nur Standardbibliothek plus twin.py, appframe/ und
mcpdoor/ (Kopien aus lib/).
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

import appframe as af
import mcpdoor
import twin

APP_NAME = "Projekte"
APP_ID = "projekt"
VERSION = "0.3.0"
PORT = 8000

DATA_DIR = os.environ.get("PROJEKT_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

PROJEKT_TYPE = "Projekt"
PROJECT_GROUP = "project.core"
FIRMA_TYPE = "Firma"

APP_NAMES = {"partnerverwaltung": "Partnerverwaltung",
             "mitarbeiterverwaltung": "Mitarbeiterverwaltung",
             "projekt": "Projekte", "raci": "Zuständigkeiten (RACI)"}
ATTR_TITLES = {"Status": "Status", "StartDate": "Beginn"}
GROUP_TITLES = {"project.core": "Projektdaten"}
REL_TITLES = {"forCustomer": "Kunde (Firma)"}
STATUS_KIND = {"abgeschlossen": "ok", "laufend": "info", "pausiert": "warn", "abgebrochen": "err"}


def made_by(origin):
    o = str(origin or "")
    if o.startswith("app:"):
        return APP_NAMES.get(o[4:], "einer anderen App")
    return "einer anderen Stelle" if o else ""


def attr(obj, key):
    g = (obj.get("groups") or {}).get(PROJECT_GROUP) or {}
    v = (g.get("attributes") or {}).get(key) or {}
    return str(v.get("value")) if v.get("value") is not None else ""


def kunden_of(obj):
    g = (obj.get("groups") or {}).get(PROJECT_GROUP) or {}
    return [r.get("target") for r in g.get("relations") or []
            if r.get("key") == "forCustomer" and r.get("target")]


def status_badge(obj):
    s = attr(obj, "Status")
    return af.badge(s, STATUS_KIND.get(s.lower(), "")) if s else ""


# --------------------------------------------------------- der alte Index

def remember(obj_id):
    """id + type, nichts sonst -- fortgeschrieben, nicht mehr gelesen."""
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


def load(type_key):
    try:
        objs, truncated = twin.list_objects(type_key)
    except twin.TwinError as exc:
        label = "Projekte" if type_key == PROJEKT_TYPE else "Firmen"
        return [], f"Die Liste der {label} ist gerade nicht lesbar ({exc.status or 'keine Verbindung'})."
    return objs, ("Es werden die ersten 500 angezeigt." if truncated else "")


def title_of(obj_id, known_titles):
    if obj_id in known_titles:
        return known_titles[obj_id]
    try:
        o = twin.get_object(obj_id)
    except twin.TwinError:
        o = None
    t = (o or {}).get("title") or "nicht lesbare Firma"
    known_titles[obj_id] = t
    return t


# --------------------------------------------------------------- Optik

STYLE = (".pj-kennung code{overflow-wrap:anywhere}"
         ".pj-kennung button{margin-left:8px;padding:2px 8px;font-size:.85em}")


def render(title, body, obj=None, obj_title=None):
    # eine einzige Fläche -- keine Reiterleiste (RFC-0118 §6.1)
    return af.page(title, body, app=APP_NAME, version=VERSION, product=APP_ID,
                   obj=obj, obj_title=obj_title, style=STYLE)


def not_configured():
    return af.card("Keine Verbindung zum Zwilling",
                   "<p>Diese Instanz ist nicht mit dem Zwilling des Mandanten verbunden. "
                   "Ohne ihn kann diese App keine Projekte anzeigen oder anlegen. "
                   "Bitte die Betreuung der Plattform fragen.</p>", "oaap-warn")


def kennung(obj_id):
    return (f'<span class="pj-kennung"><code id="kennung">{esc(obj_id)}</code>'
            '<button type="button" onclick="navigator.clipboard&amp;&amp;navigator.clipboard.writeText('
            "document.getElementById('kennung').textContent)\">Kopieren</button></span>")


def page_list(params):
    projekte, n1 = load(PROJEKT_TYPE)
    firmen, n2 = load(FIRMA_TYPE)
    ft = {f["id"]: f.get("title") or "" for f in firmen}

    def kunde_text(o):
        return ", ".join(title_of(k, ft) for k in kunden_of(o))
    q = af.Query(params, filters=("status", "kunde", "von"), default_sort="titel")
    statuses = sorted({attr(o, "Status") for o in projekte if attr(o, "Status")}, key=str.lower)
    kunden = sorted({k for o in projekte for k in kunden_of(o) if k in ft}, key=lambda k: ft[k].lower())
    owners = sorted({str(o.get("owner") or "") for o in projekte if o.get("owner")})
    rows = projekte
    if q.filters.get("status"):
        rows = [o for o in rows if attr(o, "Status") == q.filters["status"]]
    if q.filters.get("kunde"):
        rows = [o for o in rows if q.filters["kunde"] in kunden_of(o)]
    if q.filters.get("von"):
        rows = [o for o in rows if str(o.get("owner") or "") == q.filters["von"]]
    cols = [("Titel", "titel", lambda o: o.get("title") or "", ""),
            ("Kunde (Firma)", "kunde", kunde_text, ""),
            ("Status", "status", lambda o: attr(o, "Status"), ""),
            ("Beginn", "beginn", lambda o: attr(o, "StartDate"), "oaap-minor"),
            ("Angelegt von", "von", lambda o: made_by(o.get("owner")), "oaap-minor")]

    def row(o):
        return (f'<tr><td><a href="object?id={esc(o["id"])}">{esc(o.get("title") or "")}</a></td>'
                f'<td>{esc(kunde_text(o))}</td><td>{status_badge(o)}</td>'
                f'<td class="oaap-minor">{esc(attr(o, "StartDate"))}</td>'
                f'<td class="oaap-minor">{esc(made_by(o.get("owner")))}</td></tr>')
    body = (af.pagehead("Projekte", '<a class="oaap-btn primary" href="neu">Neues Projekt</a>')
            + "".join(af.msg(n, error=True) for n in (n1, n2) if n)
            + af.list_report(
                q, rows, cols, row, noun="Projekte", placeholder="Titel, Kunde, Status",
                choices=[("status", "Status", [(s, s) for s in statuses]),
                         ("kunde", "Kunde (Firma)", [(k, ft[k]) for k in kunden]),
                         ("von", "Angelegt von", [(o, made_by(o)) for o in owners])],
                empty_text="Noch keine Projekte. <b>Neues Projekt</b> legt das erste an."))
    return render("Projekte", body)


def page_neu(notice="", form=None):
    form = form or {}
    firmen, note = load(FIRMA_TYPE)
    cur = form.get("kunde_id", "")
    opts = "".join(f'<option value="{esc(f["id"])}"{" selected" if f["id"] == cur else ""}>{esc(f.get("title") or "")}</option>'
                   for f in sorted(firmen, key=lambda f: (f.get("title") or "").lower()))
    body = (af.back("./", "Zurück zur Liste") + af.pagehead("Neues Projekt") + af.msg(notice, error=True)
            + af.msg(note, error=True)
            + '<form class="oaap-form" method="post" action="create">'
              f'<label>Titel<input type="text" name="title" required value="{esc(form.get("title", ""))}"></label>'
              f'<label>Kunde (Firma)<select name="kunde_id"><option value="">– ohne Kunde –</option>{opts}</select></label>'
              f'<label>Status<input type="text" name="Status" placeholder="geplant" list="pj-status" value="{esc(form.get("Status", ""))}"></label>'
              '<datalist id="pj-status"><option value="geplant"><option value="laufend">'
              '<option value="pausiert"><option value="abgeschlossen"></datalist>'
              f'<label>Beginn<input type="date" name="StartDate" value="{esc(form.get("StartDate", ""))}"></label>'
              '<p class="oaap-muted">Firmen werden in der Partnerverwaltung gepflegt.</p>'
              '<div><button type="submit" class="primary">Anlegen</button></div></form>')
    return render("Neues Projekt", body)


def page_object(obj):
    known_titles = {}
    kunden = kunden_of(obj)
    pairs = [("Art", "Projekt"),
             ("Kunde (Firma)", esc(", ".join(title_of(k, known_titles) for k in kunden)) or "–"),
             ("Status", status_badge(obj)), ("Beginn", esc(attr(obj, "StartDate"))),
             ("Angelegt von", esc(made_by(obj.get("owner")))), ("Kennung", kennung(obj["id"]))]
    parts = [af.back("./", "Zurück zur Liste"),
             f'<div class="oaap-objhead"><h1>{esc(obj["title"])}</h1>{af.facts(pairs)}</div>']
    for gk, g in sorted((obj.get("groups") or {}).items()):
        rows = [f"<dt>{esc(ATTR_TITLES.get(k, k))}</dt><dd>{esc(str(v.get('value') or ''))}</dd>"
                for k, v in (g.get("attributes") or {}).items()]
        rows += [f"<dt>{esc(REL_TITLES.get(r.get('key'), 'Beziehung'))}</dt>"
                 f"<dd>{esc(title_of(r.get('target'), known_titles))}</dd>"
                 for r in g.get("relations") or []]
        inner = f'<dl class="oaap-kv">{"".join(rows)}</dl>' if rows else '<p class="oaap-muted">Keine Angaben.</p>'
        if g.get("origin") and g.get("origin") != "app:" + APP_ID:
            inner = f'<p class="oaap-muted">angelegt von {esc(made_by(g.get("origin")))}</p>' + inner
        parts.append(af.card(GROUP_TITLES.get(gk.split("+", 1)[0], "Weitere Angaben"), inner))
    return render(obj["title"], "".join(parts), obj=f"Projekt:{obj['id']}", obj_title=obj["title"])


# ------------------------------------------------------------- MCP-Tür

door = mcpdoor.Door(f"oaap-{APP_ID}", VERSION,
                    "Projekte des Mandanten suchen und lesen, mit Kunde (Firma), Status und Beginn.")


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


def _short(o, ft):
    d = {"kennung": o["id"], "titel": o.get("title"),
         "kunde": ", ".join(ft.get(k) or "" for k in kunden_of(o)).strip(", "),
         "status": attr(o, "Status"), "beginn": attr(o, "StartDate"),
         "angelegt_von": made_by(o.get("owner"))}
    return {k: v for k, v in d.items() if v}


@door.tool("projekt_search", "Projekte suchen",
           "Sucht Projekte nach Titel, Kunde oder Status. Ohne Suchwort liefert es die ersten Einträge.",
           {"q": {"type": "string", "description": "Suchwort(e), leer für alle"},
            "status": {"type": "string", "description": "nur Projekte mit diesem Status"},
            "limit": {"type": "integer", "description": "höchstens so viele Treffer (1–50, Standard 20)"}})
def _mcp_search(args, who):
    _need_session(who)
    projekte, trunc = _list_or_refuse(PROJEKT_TYPE)
    firmen, _t = _list_or_refuse(FIRMA_TYPE)
    ft = {f["id"]: f.get("title") or "" for f in firmen}
    try:
        limit = max(1, min(50, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    status = str(args.get("status") or "").strip().lower()
    q = af.Query({"q": str(args.get("q") or "")})
    hits = []
    for o in projekte:
        s = _short(o, ft)
        if status and attr(o, "Status").lower() != status:
            continue
        if q.matches(s.get("titel"), s.get("kunde"), s.get("status")):
            hits.append(s)
    return {"treffer": hits[:limit], "anzahl": len(hits), "gekuerzt": len(hits) > limit or trunc}


@door.tool("projekt_read", "Projekt lesen",
           "Liest ein Projekt mit Kunde (Firma), Status, Beginn und der App, die es angelegt hat.",
           {"kennung": {"type": "string", "description": "Kennung aus projekt_search"}},
           required=("kennung",))
def _mcp_read(args, who):
    _need_session(who)
    oid = str(args.get("kennung") or "").strip()
    if not oid:
        raise mcpdoor.Refused("Welche Kennung?")
    try:
        obj = twin.get_object(oid)
    except twin.TwinError as exc:
        raise mcpdoor.Refused(f"Nicht lesbar ({exc.status or 'keine Verbindung'}).") from None
    if not obj or obj.get("type") != PROJEKT_TYPE:
        raise mcpdoor.Refused("Kein Projekt mit dieser Kennung.")
    known_titles = {}
    return {"kennung": obj["id"], "titel": obj["title"], "angelegt_von": made_by(obj.get("owner")),
            "status": attr(obj, "Status") or None, "beginn": attr(obj, "StartDate") or None,
            "kunde": [{"kennung": k, "name": title_of(k, known_titles)} for k in kunden_of(obj)]}


door.prompt("projekt_stand", "Stand der Projekte",
            "Fasst zusammen, welche Projekte laufen, geplant oder abgeschlossen sind.",
            "Gib mir einen Überblick über die Projekte (projekt_search): gruppiert nach Status, "
            "mit Kunde und Beginn. Nenne Firmen beim Namen, nicht bei der Kennung.")
door.prompt("projekt_kunde", "Projekte eines Kunden",
            "Listet die Projekte für eine Firma.",
            "Welche Projekte gibt es für die Firma „{kunde}“? Nutze projekt_search und nenne Titel, "
            "Status und Beginn.", arguments=("kunde",))


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

    def refuse(self):
        self.send_html(403, render("Anmeldung nötig", af.card(
            "", "<p>Diese Seite braucht eine angemeldete Sitzung.</p>")))

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
        if not twin.configured() and path in ("/", "/object", "/neu"):
            self.send_html(200, render("Keine Verbindung", not_configured()))
            return
        if path == "/object":
            obj_id = (parse_qs(parsed.query).get("id") or [""])[0].strip()
            try:
                obj = twin.get_object(obj_id) if obj_id else None
            except twin.TwinError as exc:
                self.send_html(200, render("Nicht lesbar", af.back("./") + af.msg(
                    f"Dieses Projekt ist nicht lesbar ({exc.status or 'keine Verbindung'}).", error=True)))
                return
            if obj is None:
                self.send_html(404, render("Nicht gefunden", af.back("./") + af.msg(
                    "Kein Projekt mit dieser Kennung.", error=True)))
                return
            self.send_html(200, page_object(obj))
            return
        if path == "/":
            self.send_html(200, page_list(parse_qs(parsed.query)))
            return
        if path == "/neu":
            self.send_html(200, page_neu())
            return
        self.send_html(404, render("Nicht gefunden", af.empty(
            'Diese Seite gibt es nicht. <a href="./">Zur Liste</a>')))

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
        if path != "/create":
            self.send_html(404, render("Nicht gefunden", af.empty("Diese Aktion gibt es nicht.")))
            return
        form = self.read_form()
        title = (form.get("title") or "").strip()
        kunde_id = (form.get("kunde_id") or "").strip()
        if not title:
            self.send_html(200, page_neu("Ohne Titel kann ich nichts anlegen.", form))
            return
        attrs = {k: form[k].strip() for k in ("Status", "StartDate") if (form.get(k) or "").strip()}
        relations = [{"key": "forCustomer", "target": kunde_id}] if kunde_id else None
        try:
            result = twin.create_object(PROJEKT_TYPE, title, PROJECT_GROUP,
                                        attributes=attrs or None, relations=relations)
        except twin.TwinError as exc:
            self.send_html(200, page_neu(f"Anlegen abgelehnt: {exc.detail or exc}", form))
            return
        remember(result["id"])
        self.send_response(303)
        self.send_header("Location", f"/object?id={result['id']}")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    sys.stdout.write(f"{APP_NAME} {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
