"""Mitarbeiterverwaltung -- Owner von Mitarbeiter im digitalen Zwilling
(RFC-0031 Schritt 4, zweite Welle; Oberfläche nach RFC-0118: Rahmen,
Listenbericht, Hilfe, MCP).

Diese App weiß NICHTS von der Partnerverwaltung und liest/schreibt auch
nichts von ihr (kein consumes, kein contributes auf Firma oder
Kontaktperson) -- das ist der Punkt, nicht ein Versehen: zwei unabhängige
Apps legen dieselbe echte Person zweimal an (Kontaktperson dort,
Mitarbeiter hier). Das Zusammenführen ist Aufgabe des Zwillings-Browsers.

Die Liste liest den Zwilling frisch (GET /twin/objects?type=Mitarbeiter
-- alle Mitarbeiter des Mandanten in EINER Anfrage mit Titeln und
Gruppen). Der alte Index unter /data (nur id + type) wird weiter
fortgeschrieben, aber nicht mehr gelesen.

Kein eigener Login, ein HTTP-Port, Gesundheitspfad, nur
Standardbibliothek plus twin.py, appframe/ und mcpdoor/ (Kopien aus lib/).
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

APP_NAME = "Mitarbeiterverwaltung"
APP_ID = "mitarbeiterverwaltung"
VERSION = "0.3.2"
PORT = 8000

DATA_DIR = os.environ.get("MITARBEITERVERWALTUNG_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

MITARBEITER_TYPE = "Mitarbeiter"
HR_GROUP = "hr.core"

APP_NAMES = {"partnerverwaltung": "Partnerverwaltung",
             "mitarbeiterverwaltung": "Mitarbeiterverwaltung",
             "projekt": "Projekte", "raci": "Zuständigkeiten (RACI)"}
ATTR_TITLES = {"Email": "E-Mail", "Phone": "Telefon", "Department": "Abteilung"}
GROUP_TITLES = {"hr.core": "Personaldaten"}


def made_by(origin):
    o = str(origin or "")
    if o.startswith("app:"):
        return APP_NAMES.get(o[4:], "einer anderen App")
    return "einer anderen Stelle" if o else ""


def attr(obj, key, group_key=HR_GROUP):
    g = (obj.get("groups") or {}).get(group_key) or {}
    v = (g.get("attributes") or {}).get(key) or {}
    return str(v.get("value")) if v.get("value") is not None else ""


# --------------------------------------------------------- der alte Index

def remember(obj_id, type_key):
    """id + type, nichts sonst -- fortgeschrieben, nicht mehr gelesen."""
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


def load():
    try:
        objs, truncated = twin.list_objects(MITARBEITER_TYPE)
    except twin.TwinError as exc:
        return [], f"Die Liste ist gerade nicht lesbar ({exc.status or 'keine Verbindung'})."
    return objs, ("Es werden die ersten 500 angezeigt." if truncated else "")


# --------------------------------------------------------------- Optik

STYLE = (".mv-kennung code{overflow-wrap:anywhere}"
         ".mv-kennung button{margin-left:8px;padding:2px 8px;font-size:.85em}")


def render(title, body, obj=None, obj_title=None):
    # eine einzige Fläche -- keine Reiterleiste (RFC-0118 §6.1)
    return af.page(title, body, app=APP_NAME, version=VERSION, product=APP_ID,
                   obj=obj, obj_title=obj_title, style=STYLE)


def not_configured():
    return af.card("Keine Verbindung zum Zwilling",
                   "<p>Diese Instanz ist nicht mit dem Zwilling des Mandanten verbunden. "
                   "Ohne ihn kann die Mitarbeiterverwaltung nichts anzeigen oder anlegen. "
                   "Bitte die Betreuung der Plattform fragen.</p>", "oaap-warn")


def kennung(obj_id):
    return (f'<span class="mv-kennung"><code id="kennung">{esc(obj_id)}</code>'
            '<button type="button" onclick="navigator.clipboard&amp;&amp;navigator.clipboard.writeText('
            "document.getElementById('kennung').textContent)\">Kopieren</button></span>")


def page_list(params):
    rows, note = load()
    depts = sorted({attr(o, "Department") for o in rows if attr(o, "Department")}, key=str.lower)
    q = af.Query(params, filters=("abteilung",), default_sort="name")
    if q.filters.get("abteilung"):
        rows = [o for o in rows if attr(o, "Department") == q.filters["abteilung"]]
    cols = [("Name", "name", lambda o: o.get("title") or "", ""),
            ("Abteilung", "abteilung", lambda o: attr(o, "Department"), ""),
            ("E-Mail", "email", lambda o: attr(o, "Email"), "oaap-minor"),
            ("Telefon", "tel", lambda o: attr(o, "Phone"), "oaap-minor")]

    def row(o):
        return (f'<tr><td><a href="object?id={esc(o["id"])}">{esc(o.get("title") or "")}</a></td>'
                f'<td>{esc(attr(o, "Department"))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, "Email"))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, "Phone"))}</td></tr>')
    body = (af.pagehead("Mitarbeiter", '<a class="oaap-btn primary" href="neu">Neuer Mitarbeiter</a>')
            + (af.msg(note, error=True) if note else "")
            + af.list_report(q, rows, cols, row, noun="Mitarbeiter", placeholder="Name, Abteilung, E-Mail",
                             choices=[("abteilung", "Abteilung", [(d, d) for d in depts])],
                             empty_text="Noch keine Mitarbeiter. <b>Neuer Mitarbeiter</b> legt den ersten an."))
    return render("Mitarbeiter", body)


def page_neu(notice="", form=None):
    form = form or {}
    fields = [("title", "Name", True), ("Email", "E-Mail", False), ("Phone", "Telefon", False),
              ("Department", "Abteilung", False)]
    inputs = "".join(f'<label>{esc(label)}<input type="text" name="{k}" value="{esc(form.get(k, ""))}"'
                     f'{" required" if req else ""}></label>' for k, label, req in fields)
    body = (af.back("./", "Zurück zur Liste") + af.pagehead("Neuer Mitarbeiter") + af.msg(notice, error=True)
            + f'<form class="oaap-form" method="post" action="create">{inputs}'
              '<div><button type="submit" class="primary">Anlegen</button></div></form>')
    return render("Neuer Mitarbeiter", body)


def page_object(obj):
    pairs = [("Art", "Mitarbeiter"), ("Abteilung", esc(attr(obj, "Department"))),
             ("Angelegt von", esc(made_by(obj.get("owner")))), ("Kennung", kennung(obj["id"]))]
    parts = [af.back("./", "Zurück zur Liste"),
             f'<div class="oaap-objhead"><h1>{esc(obj["title"])}</h1>{af.facts(pairs)}</div>']
    for gk, g in sorted((obj.get("groups") or {}).items()):
        rows = "".join(f"<dt>{esc(ATTR_TITLES.get(k, k))}</dt><dd>{esc(str(v.get('value') or ''))}</dd>"
                       for k, v in (g.get("attributes") or {}).items())
        inner = f'<dl class="oaap-kv">{rows}</dl>' if rows else '<p class="oaap-muted">Keine Angaben.</p>'
        if g.get("origin") and g.get("origin") != "app:" + APP_ID:
            inner = f'<p class="oaap-muted">angelegt von {esc(made_by(g.get("origin")))}</p>' + inner
        parts.append(af.card(GROUP_TITLES.get(gk.split("+", 1)[0], "Weitere Angaben"), inner))
    return render(obj["title"], "".join(parts), obj=f"Mitarbeiter:{obj['id']}", obj_title=obj["title"])


# ------------------------------------------------------------- MCP-Tür

door = mcpdoor.Door(f"oaap-{APP_ID}", VERSION,
                    "Mitarbeiter des Mandanten suchen und lesen: Name, Abteilung, E-Mail, Telefon.")


def _need_session(who):
    # dieselbe Prüfung wie die Seiten: ohne Rollen keine Daten
    if not who.get("roles"):
        raise mcpdoor.Refused("Dafür braucht es eine angemeldete Sitzung.")
    if not twin.configured():
        raise mcpdoor.Refused("Diese Instanz ist nicht mit dem Zwilling verbunden.")


def _short(o):
    d = {"kennung": o["id"], "name": o.get("title"), "abteilung": attr(o, "Department"),
         "email": attr(o, "Email"), "telefon": attr(o, "Phone")}
    return {k: v for k, v in d.items() if v}


@door.tool("mitarbeiterverwaltung_search", "Mitarbeiter suchen",
           "Sucht Mitarbeiter nach Name, Abteilung oder E-Mail. Ohne Suchwort liefert es die ersten Einträge.",
           {"q": {"type": "string", "description": "Suchwort(e), leer für alle"},
            "abteilung": {"type": "string", "description": "nur diese Abteilung (genauer Name)"},
            "limit": {"type": "integer", "description": "höchstens so viele Treffer (1–50, Standard 20)"}})
def _mcp_search(args, who):
    _need_session(who)
    try:
        objs, trunc = twin.list_objects(MITARBEITER_TYPE)
    except twin.TwinError as exc:
        raise mcpdoor.Refused(f"Der Zwilling antwortet nicht ({exc.status or 'keine Verbindung'}).") from None
    try:
        limit = max(1, min(50, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    dept = str(args.get("abteilung") or "").strip().lower()
    q = af.Query({"q": str(args.get("q") or "")})
    hits = [_short(o) for o in objs
            if (not dept or attr(o, "Department").lower() == dept)
            and q.matches(o.get("title"), attr(o, "Department"), attr(o, "Email"))]
    return {"treffer": hits[:limit], "anzahl": len(hits), "gekuerzt": len(hits) > limit or trunc}


@door.tool("mitarbeiterverwaltung_read", "Mitarbeiter lesen",
           "Liest einen Mitarbeiter mit allen Angaben.",
           {"kennung": {"type": "string", "description": "Kennung aus mitarbeiterverwaltung_search"}},
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
    if not obj or obj.get("type") != MITARBEITER_TYPE:
        raise mcpdoor.Refused("Kein Mitarbeiter mit dieser Kennung.")
    angaben = {}
    for g in (obj.get("groups") or {}).values():
        for k, v in (g.get("attributes") or {}).items():
            angaben[ATTR_TITLES.get(k, k)] = v.get("value")
    return {"kennung": obj["id"], "name": obj["title"], "angelegt_von": made_by(obj.get("owner")),
            "angaben": angaben}


door.prompt("mitarbeiterverwaltung_abteilung", "Wer arbeitet in einer Abteilung?",
            "Listet die Mitarbeiter einer Abteilung mit ihren Kontaktdaten.",
            "Welche Mitarbeiter gehören zur Abteilung „{abteilung}“? Nutze mitarbeiterverwaltung_search "
            "und nenne Name, E-Mail und Telefon.", arguments=("abteilung",))
door.prompt("mitarbeiterverwaltung_kontakt", "Kontaktdaten eines Mitarbeiters",
            "Findet E-Mail, Telefon und Abteilung eines Mitarbeiters.",
            "Wie erreiche ich „{name}“? Suche in der Mitarbeiterverwaltung und nenne Abteilung, "
            "E-Mail und Telefon.", arguments=("name",))


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
                    f"Dieser Eintrag ist nicht lesbar ({exc.status or 'keine Verbindung'}).", error=True)))
                return
            if obj is None:
                self.send_html(404, render("Nicht gefunden", af.back("./") + af.msg(
                    "Kein Eintrag mit dieser Kennung.", error=True)))
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
        if not title:
            self.send_html(200, page_neu("Ohne Namen kann ich nichts anlegen.", form))
            return
        attrs = {k: form[k].strip() for k in ("Email", "Phone", "Department") if (form.get(k) or "").strip()}
        try:
            result = twin.create_object(MITARBEITER_TYPE, title, HR_GROUP, attributes=attrs or None)
        except twin.TwinError as exc:
            self.send_html(200, page_neu(f"Anlegen abgelehnt: {exc.detail or exc}", form))
            return
        remember(result["id"], MITARBEITER_TYPE)
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
