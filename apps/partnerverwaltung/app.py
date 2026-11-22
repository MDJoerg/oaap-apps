"""Partnerverwaltung -- Owner von Firma und Kontaktperson im digitalen
Zwilling (RFC-0031 Schritt 4, eine der Referenz-Apps; Oberfläche nach
RFC-0118: Rahmen, Listenbericht, Auswahl statt URN, Hilfe, MCP).

Die registrierten Typschlüssel heißen 'Firma'/'Kontaktperson' (nicht
'Customer'/'Person', die auf oaap-test von einer Wegwerf-Probe dauerhaft
belegt sind -- siehe oaap-app.yaml).

Was die App tut:
- Firmen und Kontaktpersonen anlegen (Owner, Kerngruppe crm.core bzw.
  crm.contact);
- eine Kontaktperson mit einer Firma verknüpfen (isContactOf, gültig ab
  einem Datum) -- die Firma wird aus einer Liste gewählt, nicht als URN
  eingefügt;
- eine Aktivität (Anruf/Aufgabe) auf einem Objekt erfassen;
- Projekte für eine Firma anlegen (zweiter Owner des Typs 'Projekt',
  definiert von der App „Projekte“).

Was sie NICHT tut: eine eigene Kopie der Daten halten. Listen und Seiten
lesen den Zwilling frisch (GET /twin/objects?type=… -- alle Objekte des
Typs im Mandanten, auch die anderer Apps, in EINER Anfrage mit Titeln und
Gruppen). Der alte Index unter /data (nur id + type) wird weiter
fortgeschrieben, damit nichts verloren geht, aber nicht mehr gelesen.

Kein eigener Login (Identität als Gateway-Kopfzeile), ein HTTP-Port,
Gesundheitspfad, nur Standardbibliothek plus twin.py, appframe/ und
mcpdoor/ (Kopien aus lib/).
"""
import html
import json
import os
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote_plus, urlparse

import appframe as af
import mcpdoor
import twin

APP_NAME = "Partnerverwaltung"
APP_ID = "partnerverwaltung"
VERSION = "0.3.0"
PORT = 8000

DATA_DIR = os.environ.get("PARTNERVERWALTUNG_DATA_DIR", "/data")
INDEX_PATH = os.path.join(DATA_DIR, "index.jsonl")

esc = html.escape

FIRMA_TYPE = "Firma"
KONTAKT_TYPE = "Kontaktperson"
FIRMA_GROUP = "crm.core"
KONTAKT_GROUP = "crm.contact"
# 'Projekt' definiert die App „Projekte“; diese App bindet sich nur an den
# schon registrierten Typ und legt eigene Instanzen an (ein Typ, zwei Owner).
PROJEKT_TYPE = "Projekt"
PROJECT_GROUP = "project.core"

# ------------------------------------------------- Wörter für Menschen
APP_NAMES = {"partnerverwaltung": "Partnerverwaltung",
             "mitarbeiterverwaltung": "Mitarbeiterverwaltung",
             "projekt": "Projekte", "raci": "Zuständigkeiten (RACI)"}
TYPE_TITLES = {"Firma": "Firma", "Kontaktperson": "Kontaktperson",
               "Projekt": "Projekt", "Mitarbeiter": "Mitarbeiter"}
ATTR_TITLES = {"VatId": "USt-ID", "Email": "E-Mail", "Location": "Standort",
               "Phone": "Telefon", "Status": "Status", "StartDate": "Beginn",
               "Department": "Abteilung"}
GROUP_TITLES = {"crm.core": "Stammdaten", "crm.contact": "Kontaktdaten",
                "project.core": "Projektdaten", "hr.core": "Personaldaten",
                "raci.assignments": "Zuständigkeiten"}
REL_TITLES = {"isContactOf": "Ansprechpartner von", "forCustomer": "Kunde",
              "responsible": "Verantwortlich"}
ACT_TITLES = {"PhoneCall": "Anruf", "Task": "Aufgabe"}
ACT_STATUS = {"open": ("offen", "info"), "done": ("erledigt", "ok"),
              "planned": ("geplant", ""), "cancelled": ("abgebrochen", "")}


def made_by(origin):
    """„angelegt von <App-Name>“ statt der rohen Herkunft 'app:<id>'."""
    o = str(origin or "")
    if o.startswith("app:"):
        return APP_NAMES.get(o[4:], "einer anderen App")
    return "einer anderen Stelle" if o else ""


def attr(obj, group_key, key):
    g = (obj.get("groups") or {}).get(group_key) or {}
    v = (g.get("attributes") or {}).get(key) or {}
    return str(v.get("value") or "") if v.get("value") is not None else ""


def relation_targets(obj, group_key, rel_key):
    g = (obj.get("groups") or {}).get(group_key) or {}
    return [r.get("target") for r in g.get("relations") or [] if r.get("key") == rel_key and r.get("target")]


# --------------------------------------------------------- der alte Index

def remember(obj_id, type_key):
    """id + type, nichts sonst. Wird fortgeschrieben, aber nicht mehr
    gelesen -- die Listen kommen aus dem Zwilling."""
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


# ------------------------------------------------------------ Zwilling

def load(type_key):
    """Alle Objekte eines Typs: (Liste, Hinweis). Ein Fehler wird zum
    Hinweis, nie zum Absturz der Seite."""
    try:
        objs, truncated = twin.list_objects(type_key)
    except twin.TwinError as exc:
        return [], f"Die Liste „{TYPE_TITLES.get(type_key, type_key)}“ ist gerade nicht lesbar ({exc.status or 'keine Verbindung'})."
    note = "Es werden die ersten 500 angezeigt." if truncated else ""
    return objs, note


def titles(objs):
    return {o["id"]: o.get("title") or "" for o in objs}


def title_of(obj_id, known_titles):
    """Titel eines Ziels: erst aus schon geladenen Listen, sonst einzeln."""
    if obj_id in known_titles:
        return known_titles[obj_id]
    try:
        o = twin.get_object(obj_id)
    except twin.TwinError:
        o = None
    t = (o or {}).get("title") or "nicht lesbares Objekt"
    known_titles[obj_id] = t
    return t


# --------------------------------------------------------------- Optik

STYLE = (".pv-kennung code{overflow-wrap:anywhere}"
         ".pv-kennung button{margin-left:8px;padding:2px 8px;font-size:.85em}"
         ".pv-list{margin:0;padding-left:1.2rem}")

TABS = [("./", "Firmen"), ("kontaktpersonen", "Kontaktpersonen")]


def render(title, body, active=None, obj=None, obj_title=None):
    return af.page(title, body, app=APP_NAME, version=VERSION, product=APP_ID,
                   tabs=TABS, active=active, obj=obj, obj_title=obj_title, style=STYLE)


def not_configured():
    return af.card("Keine Verbindung zum Zwilling",
                   "<p>Diese Instanz ist nicht mit dem Zwilling des Mandanten verbunden. "
                   "Ohne ihn kann die Partnerverwaltung nichts anzeigen oder anlegen. "
                   "Bitte die Betreuung der Plattform fragen.</p>", "oaap-warn")


def kennung(obj_id):
    """Die Kennung -- einmal im Objektkopf, kopierbar."""
    return (f'<span class="pv-kennung"><code id="kennung">{esc(obj_id)}</code>'
            '<button type="button" onclick="navigator.clipboard&amp;&amp;navigator.clipboard.writeText('
            "document.getElementById('kennung').textContent)\">Kopieren</button></span>")


def objhead(obj, facts):
    pairs = [("Art", esc(TYPE_TITLES.get(obj["type"], obj["type"])))] + facts
    pairs.append(("Angelegt von", esc(made_by(obj.get("owner")))))
    pairs.append(("Kennung", kennung(obj["id"])))
    return f'<div class="oaap-objhead"><h1>{esc(obj["title"])}</h1>{af.facts(pairs)}</div>'


def group_card(group_key, group, known_titles, own_origin="app:" + APP_ID):
    """Eine Gruppe des Objekts in Worten: Angaben, Beziehungen, Aktivitäten."""
    base = group_key.split("+", 1)[0]
    rows = []
    for k, v in (group.get("attributes") or {}).items():
        span = ""
        if v.get("valid_from") or v.get("valid_to"):
            span = (f' <span class="oaap-muted">(gültig {"ab " + esc(str(v["valid_from"])) if v.get("valid_from") else ""}'
                    f'{" bis " + esc(str(v["valid_to"])) if v.get("valid_to") else ""})</span>')
        rows.append(f"<dt>{esc(ATTR_TITLES.get(k, k))}</dt><dd>{esc(str(v.get('value') or ''))}{span}</dd>")
    for r in group.get("relations") or []:
        t = title_of(r.get("target"), known_titles)
        since = f' <span class="oaap-muted">(seit {esc(str(r["valid_from"]))})</span>' if r.get("valid_from") else ""
        rows.append(f"<dt>{esc(REL_TITLES.get(r.get('key'), 'Beziehung'))}</dt>"
                    f"<dd><a href=\"object?id={esc(r.get('target') or '')}\">{esc(t)}</a>{since}</dd>")
    acts = []
    for a in group.get("activities") or []:
        label, kind = ACT_STATUS.get(a.get("status") or "", (a.get("status") or "", ""))
        acts.append(f"<li>{esc(ACT_TITLES.get(a.get('key'), 'Aktivität'))} "
                    f"{af.badge(label, kind) if label else ''}</li>")
    inner = f'<dl class="oaap-kv">{"".join(rows)}</dl>' if rows else '<p class="oaap-muted">Keine Angaben.</p>'
    if acts:
        inner += f'<h3>Aktivitäten</h3><ul class="pv-list">{"".join(acts)}</ul>'
    if group.get("origin") and group.get("origin") != own_origin:
        inner = f'<p class="oaap-muted">angelegt von {esc(made_by(group.get("origin")))}</p>' + inner
    return af.card(GROUP_TITLES.get(base, "Weitere Angaben"), inner)


def note_html(*notes):
    return "".join(af.msg(n, error=True) for n in notes if n)


# ------------------------------------------------------------ Listen

def page_firmen(params):
    firmen, note = load(FIRMA_TYPE)
    q = af.Query(params, default_sort="name")
    cols = [("Name", "name", lambda o: o.get("title") or "", ""),
            ("Standort", "ort", lambda o: attr(o, FIRMA_GROUP, "Location"), ""),
            ("E-Mail", "email", lambda o: attr(o, FIRMA_GROUP, "Email"), "oaap-minor"),
            ("Telefon", "tel", lambda o: attr(o, FIRMA_GROUP, "Phone"), "oaap-minor")]

    def row(o):
        return (f'<tr><td><a href="object?id={esc(o["id"])}">{esc(o.get("title") or "")}</a></td>'
                f'<td>{esc(attr(o, FIRMA_GROUP, "Location"))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, FIRMA_GROUP, "Email"))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, FIRMA_GROUP, "Phone"))}</td></tr>')
    head = af.pagehead("Firmen", '<a class="oaap-btn primary" href="neu?art=firma">Neue Firma</a>')
    body = head + note_html(note) + af.list_report(
        q, firmen, cols, row, noun="Firmen", placeholder="Name, Standort, E-Mail",
        empty_text="Noch keine Firmen. <b>Neue Firma</b> legt die erste an.")
    return render("Firmen", body, active="./")


def page_kontakte(params):
    personen, note1 = load(KONTAKT_TYPE)
    firmen, note2 = load(FIRMA_TYPE)
    ft = titles(firmen)

    def firmen_of(o):
        return [t for t in relation_targets(o, KONTAKT_GROUP, "isContactOf")]

    def firma_text(o):
        return ", ".join(ft.get(t) or "nicht lesbare Firma" for t in firmen_of(o))
    used = sorted({t for o in personen for t in firmen_of(o) if t in ft}, key=lambda i: ft[i].lower())
    q = af.Query(params, filters=("firma",), default_sort="name")
    rows = personen
    if q.filters.get("firma"):
        rows = [o for o in rows if q.filters["firma"] in firmen_of(o)]
    cols = [("Name", "name", lambda o: o.get("title") or "", ""),
            ("Firma", "firma", firma_text, ""),
            ("E-Mail", "email", lambda o: attr(o, KONTAKT_GROUP, "Email"), "oaap-minor"),
            ("Telefon", "tel", lambda o: attr(o, KONTAKT_GROUP, "Phone"), "oaap-minor")]

    def row(o):
        return (f'<tr><td><a href="object?id={esc(o["id"])}">{esc(o.get("title") or "")}</a></td>'
                f'<td>{esc(firma_text(o))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, KONTAKT_GROUP, "Email"))}</td>'
                f'<td class="oaap-minor">{esc(attr(o, KONTAKT_GROUP, "Phone"))}</td></tr>')
    head = af.pagehead("Kontaktpersonen",
                       '<a class="oaap-btn primary" href="neu?art=kontaktperson">Neue Kontaktperson</a>')
    body = head + note_html(note1, note2) + af.list_report(
        q, rows, cols, row, noun="Kontaktpersonen", placeholder="Name, Firma, E-Mail",
        choices=[("firma", "Firma", [(i, ft[i]) for i in used])],
        empty_text="Noch keine Kontaktpersonen. <b>Neue Kontaktperson</b> legt die erste an.")
    return render("Kontaktpersonen", body, active="kontaktpersonen")


# ------------------------------------------------------- Neu anlegen

FIELDS = {
    "firma": (FIRMA_TYPE, "Neue Firma", [("title", "Name", True), ("VatId", "USt-ID", False),
                                         ("Email", "E-Mail", False), ("Location", "Standort", False),
                                         ("Phone", "Telefon", False)]),
    "kontaktperson": (KONTAKT_TYPE, "Neue Kontaktperson", [("title", "Name", True), ("Email", "E-Mail", False),
                                                            ("Phone", "Telefon", False)]),
}


def page_neu(art, notice="", form=None):
    if art not in FIELDS:
        art = "firma"
    _type, heading, fields = FIELDS[art]
    form = form or {}
    inputs = "".join(
        f'<label>{esc(label)}<input type="text" name="{key}" value="{esc(form.get(key, ""))}"'
        f'{" required" if req else ""}></label>' for key, label, req in fields)
    back_href = "./" if art == "firma" else "kontaktpersonen"
    body = (af.back(back_href) + af.pagehead(heading) + af.msg(notice, error=True)
            + f'<form class="oaap-form" method="post" action="create/{art}">{inputs}'
              '<div><button type="submit" class="primary">Anlegen</button></div></form>')
    return render(heading, body, active=back_href)


# ------------------------------------------------------- Objektseiten

def page_object(obj, notice=""):
    t = obj["type"]
    parts = [note_html(notice)]
    known_titles = {}
    if t == FIRMA_TYPE:
        personen, n1 = load(KONTAKT_TYPE)
        projekte, n2 = load(PROJEKT_TYPE)
        known_titles.update(titles(personen))
        known_titles[obj["id"]] = obj["title"]
        parts.insert(0, af.back("./", "Zurück zu den Firmen"))
        parts.append(objhead(obj, [("Standort", esc(attr(obj, FIRMA_GROUP, "Location")))]))
        parts.append(note_html(n1, n2))
        groups = obj.get("groups") or {}
        parts.append(group_card(FIRMA_GROUP, groups.get(FIRMA_GROUP) or {}, known_titles))
        contacts = [p for p in personen if obj["id"] in relation_targets(p, KONTAKT_GROUP, "isContactOf")]
        parts.append(af.card("Ansprechpartner", (
            '<ul class="pv-list">' + "".join(
                f'<li><a href="object?id={esc(p["id"])}">{esc(p["title"])}</a>'
                f'{" · " + esc(attr(p, KONTAKT_GROUP, "Email")) if attr(p, KONTAKT_GROUP, "Email") else ""}</li>'
                for p in sorted(contacts, key=lambda p: (p.get("title") or "").lower())) + "</ul>")
            if contacts else af.empty("Noch keine Ansprechpartner. Auf der Seite einer Kontaktperson "
                                      "verknüpft <b>Mit Firma verknüpfen</b> sie mit dieser Firma.")))
        mine = [p for p in projekte if obj["id"] in relation_targets(p, PROJECT_GROUP, "forCustomer")]
        if mine:
            rows = [f'<tr><td><a href="object?id={esc(p["id"])}">{esc(p["title"])}</a></td>'
                    f'<td>{af.badge(attr(p, PROJECT_GROUP, "Status")) if attr(p, PROJECT_GROUP, "Status") else ""}</td>'
                    f'<td class="oaap-minor">{esc(made_by(p.get("owner")))}</td></tr>'
                    for p in sorted(mine, key=lambda p: (p.get("title") or "").lower())]
            proj_html = af.table("<th>Projekt</th><th>Status</th><th class=\"oaap-minor\">Angelegt von</th>", rows)
        else:
            proj_html = af.empty("Noch keine Projekte für diese Firma.")
        proj_html += (f'<h3>Projekt anlegen</h3><form class="oaap-form" method="post" action="create/projekt">'
                      f'<input type="hidden" name="kunde_id" value="{esc(obj["id"])}">'
                      '<label>Titel<input type="text" name="title" required></label>'
                      '<label>Status<input type="text" name="Status" placeholder="geplant" list="pv-status"></label>'
                      '<datalist id="pv-status"><option value="geplant"><option value="laufend">'
                      '<option value="abgeschlossen"></datalist>'
                      '<div><button type="submit">Projekt anlegen</button></div></form>')
        parts.append(af.card("Projekte", proj_html))
        for gk, g in sorted(groups.items()):
            if gk != FIRMA_GROUP:
                parts.append(group_card(gk, g, known_titles))
        parts.append(activity_form(obj["id"], FIRMA_GROUP))
        active = "./"
    elif t == KONTAKT_TYPE:
        firmen, n1 = load(FIRMA_TYPE)
        ft = titles(firmen)
        known_titles.update(ft)
        linked = relation_targets(obj, KONTAKT_GROUP, "isContactOf")
        parts.insert(0, af.back("kontaktpersonen", "Zurück zu den Kontaktpersonen"))
        parts.append(objhead(obj, [("Firma", esc(", ".join(title_of(i, known_titles) for i in linked)))]))
        parts.append(note_html(n1))
        groups = obj.get("groups") or {}
        parts.append(group_card(KONTAKT_GROUP, groups.get(KONTAKT_GROUP) or {}, known_titles))
        for gk, g in sorted(groups.items()):
            if gk != KONTAKT_GROUP:
                parts.append(group_card(gk, g, known_titles))
        opts = "".join(f'<option value="{esc(f["id"])}">{esc(f["title"])}</option>'
                       for f in sorted(firmen, key=lambda f: (f.get("title") or "").lower()))
        if opts:
            link = (f'<form class="oaap-form" method="post" action="link">'
                    f'<input type="hidden" name="person_id" value="{esc(obj["id"])}">'
                    f'<label>Firma<select name="customer_id" required><option value="">– bitte wählen –</option>{opts}</select></label>'
                    f'<label>Gültig ab<input type="date" name="valid_from" value="{date.today().isoformat()}"></label>'
                    '<div><button type="submit">Verknüpfen</button></div></form>')
        else:
            link = af.empty('Noch keine Firmen. <a href="neu?art=firma">Neue Firma</a> legt die erste an.')
        parts.append(af.card("Mit Firma verknüpfen", link))
        parts.append(activity_form(obj["id"], KONTAKT_GROUP))
        active = "kontaktpersonen"
    else:
        # ein Projekt, das diese App (oder die App „Projekte“) angelegt hat
        firmen, n1 = load(FIRMA_TYPE)
        known_titles.update(titles(firmen))
        kunden = relation_targets(obj, PROJECT_GROUP, "forCustomer")
        back_href = f"object?id={kunden[0]}" if kunden else "./"
        parts.insert(0, af.back(back_href, "Zurück zur Firma" if kunden else "Zurück zu den Firmen"))
        parts.append(objhead(obj, [("Kunde (Firma)", esc(", ".join(title_of(i, known_titles) for i in kunden))),
                                   ("Status", esc(attr(obj, PROJECT_GROUP, "Status")))]))
        parts.append(note_html(n1))
        for gk, g in sorted((obj.get("groups") or {}).items()):
            parts.append(group_card(gk, g, known_titles))
        active = "./"
    type_label = TYPE_TITLES.get(t, t)
    return render(obj["title"], "".join(parts), active=active,
                  obj=f"{type_label}:{obj['id']}", obj_title=obj["title"])


def activity_form(obj_id, group_key):
    return af.card("Aktivität erfassen", (
        f'<form class="oaap-form" method="post" action="activity">'
        f'<input type="hidden" name="object_id" value="{esc(obj_id)}">'
        f'<input type="hidden" name="group_key" value="{esc(group_key)}">'
        '<label>Art<select name="activity_key"><option value="PhoneCall">Anruf</option>'
        '<option value="Task">Aufgabe</option></select></label>'
        '<div><button type="submit">Erfassen</button></div></form>'))


# ------------------------------------------------------------- MCP-Tür

door = mcpdoor.Door(f"oaap-{APP_ID}", VERSION,
                    "Firmen und Kontaktpersonen des Mandanten suchen und lesen, "
                    "mit Ansprechpartnern und Projekten einer Firma.")
OWN_TYPES = (FIRMA_TYPE, KONTAKT_TYPE)


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
    if o["type"] == FIRMA_TYPE:
        return {"kennung": o["id"], "art": "Firma", "name": o.get("title"),
                "standort": attr(o, FIRMA_GROUP, "Location") or None,
                "email": attr(o, FIRMA_GROUP, "Email") or None}
    return {"kennung": o["id"], "art": "Kontaktperson", "name": o.get("title"),
            "firma": [ft.get(t) for t in relation_targets(o, KONTAKT_GROUP, "isContactOf") if ft.get(t)],
            "email": attr(o, KONTAKT_GROUP, "Email") or None}


@door.tool("partnerverwaltung_search", "Firmen und Kontaktpersonen suchen",
           "Sucht Firmen und Kontaktpersonen nach Name, Standort, E-Mail oder Firma. "
           "Ohne Suchwort liefert es die ersten Einträge.",
           {"q": {"type": "string", "description": "Suchwort(e), leer für alle"},
            "art": {"type": "string", "enum": ["Firma", "Kontaktperson"],
                    "description": "nur diese Art; leer für beide"},
            "limit": {"type": "integer", "description": "höchstens so viele Treffer (1–50, Standard 20)"}})
def _mcp_search(args, who):
    _need_session(who)
    art = str(args.get("art") or "").strip()
    types = [art] if art in OWN_TYPES else list(OWN_TYPES)
    try:
        limit = max(1, min(50, int(args.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20
    firmen, trunc = _list_or_refuse(FIRMA_TYPE)
    ft = titles(firmen)
    pool = (firmen if FIRMA_TYPE in types else [])
    if KONTAKT_TYPE in types:
        personen, t2 = _list_or_refuse(KONTAKT_TYPE)
        pool = pool + personen
        trunc = trunc or t2
    q = af.Query({"q": str(args.get("q") or "")})
    hits = []
    for o in pool:
        s = _short(o, ft)
        if q.matches(s.get("name"), s.get("standort"), s.get("email"), " ".join(s.get("firma") or [])):
            hits.append({k: v for k, v in s.items() if v not in (None, [], "")})
    return {"treffer": hits[:limit], "anzahl": len(hits), "gekuerzt": len(hits) > limit or trunc}


@door.tool("partnerverwaltung_read", "Firma oder Kontaktperson lesen",
           "Liest eine Firma oder Kontaktperson mit allen Angaben; bei einer Firma auch "
           "Ansprechpartner und Projekte, bei einer Kontaktperson ihre Firmen.",
           {"kennung": {"type": "string", "description": "Kennung aus partnerverwaltung_search"}},
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
    if not obj or obj.get("type") not in OWN_TYPES:
        raise mcpdoor.Refused("Keine Firma oder Kontaktperson mit dieser Kennung.")
    known_titles = {}
    out = {"kennung": obj["id"], "art": obj["type"], "name": obj["title"],
           "angelegt_von": made_by(obj.get("owner")), "angaben": {}, "beziehungen": [], "aktivitaeten": []}
    for gk, g in (obj.get("groups") or {}).items():
        for k, v in (g.get("attributes") or {}).items():
            out["angaben"][ATTR_TITLES.get(k, k)] = v.get("value")
        for r in g.get("relations") or []:
            out["beziehungen"].append({"art": REL_TITLES.get(r.get("key"), "Beziehung"),
                                       "name": title_of(r.get("target"), known_titles),
                                       "kennung": r.get("target"), "seit": r.get("valid_from")})
        for a in g.get("activities") or []:
            out["aktivitaeten"].append({"art": ACT_TITLES.get(a.get("key"), "Aktivität"),
                                        "status": ACT_STATUS.get(a.get("status") or "", (a.get("status"),))[0]})
    if obj["type"] == FIRMA_TYPE:
        personen, _t = _list_or_refuse(KONTAKT_TYPE)
        projekte, _t = _list_or_refuse(PROJEKT_TYPE)
        out["ansprechpartner"] = [{"kennung": p["id"], "name": p["title"]} for p in personen
                                  if obj["id"] in relation_targets(p, KONTAKT_GROUP, "isContactOf")]
        out["projekte"] = [{"kennung": p["id"], "titel": p["title"],
                            "status": attr(p, PROJECT_GROUP, "Status") or None,
                            "angelegt_von": made_by(p.get("owner"))} for p in projekte
                           if obj["id"] in relation_targets(p, PROJECT_GROUP, "forCustomer")]
    return out


door.prompt("partnerverwaltung_firma_ueberblick", "Überblick über eine Firma",
            "Fasst eine Firma mit Ansprechpartnern, Projekten und offenen Aktivitäten zusammen.",
            "Suche in der Partnerverwaltung die Firma „{firma}“ (partnerverwaltung_search), lies sie "
            "(partnerverwaltung_read) und fasse zusammen: Stammdaten, Ansprechpartner, Projekte mit "
            "Status und offene Aktivitäten. Nenne Firmen und Personen beim Namen, nicht bei der Kennung.",
            arguments=("firma",))
door.prompt("partnerverwaltung_ansprechpartner", "Ansprechpartner finden",
            "Findet die Ansprechpartner einer Firma mit ihren Kontaktdaten.",
            "Wer ist bei der Firma „{firma}“ Ansprechpartner? Suche die Firma in der Partnerverwaltung, "
            "lies sie und nenne die Ansprechpartner mit E-Mail und Telefon, soweit vorhanden.",
            arguments=("firma",))


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

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

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
        qs = parse_qs(parsed.query)
        if not twin.configured() and path in ("/", "/kontaktpersonen", "/object", "/neu"):
            self.send_html(200, render("Keine Verbindung", not_configured()))
            return
        if path == "/object":
            obj_id = (qs.get("id") or [""])[0].strip()
            try:
                obj = twin.get_object(obj_id) if obj_id else None
            except twin.TwinError as exc:
                self.send_html(200, render("Nicht lesbar", af.back("./") + af.msg(
                    f"Dieses Objekt ist nicht lesbar ({exc.status or 'keine Verbindung'}).", error=True)))
                return
            if obj is None:
                self.send_html(404, render("Nicht gefunden", af.back("./") + af.msg(
                    "Kein Objekt mit dieser Kennung.", error=True)))
                return
            self.send_html(200, page_object(obj))
            return
        if path == "/":
            self.send_html(200, page_firmen(qs))
            return
        if path == "/kontaktpersonen":
            self.send_html(200, page_kontakte(qs))
            return
        if path == "/neu":
            self.send_html(200, page_neu((qs.get("art") or ["firma"])[0]))
            return
        self.send_html(404, render("Nicht gefunden", af.empty(
            'Diese Seite gibt es nicht. <a href="./">Zu den Firmen</a>')))

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
        form = self.read_form()
        try:
            if path in ("/create/firma", "/create/kontaktperson"):
                art = path.rsplit("/", 1)[1]
                type_key = FIRMA_TYPE if art == "firma" else KONTAKT_TYPE
                group_key = FIRMA_GROUP if art == "firma" else KONTAKT_GROUP
                title = (form.get("title") or "").strip()
                if not title:
                    self.send_html(200, page_neu(art, "Ohne Namen kann ich nichts anlegen.", form))
                    return
                attrs = {k: form[k].strip() for k in ("VatId", "Email", "Location", "Phone")
                         if (form.get(k) or "").strip()}
                try:
                    result = twin.create_object(type_key, title, group_key, attributes=attrs or None)
                except twin.TwinError as exc:
                    self.send_html(200, page_neu(art, f"Anlegen abgelehnt: {exc.detail or exc}", form))
                    return
                remember(result["id"], type_key)
                self.redirect(f"/object?id={result['id']}")
                return
            if path == "/create/projekt":
                title = (form.get("title") or "").strip()
                kunde_id = (form.get("kunde_id") or "").strip()
                if not title:
                    return self.back_to(kunde_id, "Ohne Titel kann ich kein Projekt anlegen.")
                attrs = {k: form[k].strip() for k in ("Status",) if (form.get(k) or "").strip()}
                relations = [{"key": "forCustomer", "target": kunde_id}] if kunde_id else None
                result = twin.create_object(PROJEKT_TYPE, title, PROJECT_GROUP,
                                            attributes=attrs or None, relations=relations)
                remember(result["id"], PROJEKT_TYPE)
                self.redirect(f"/object?id={kunde_id}" if kunde_id else f"/object?id={result['id']}")
                return
            if path == "/link":
                person_id = (form.get("person_id") or "").strip()
                customer_id = (form.get("customer_id") or "").strip()
                valid_from = (form.get("valid_from") or "").strip() or None
                if not (person_id and customer_id):
                    return self.back_to(person_id, "Bitte eine Firma wählen.")
                twin.write_group(person_id, KONTAKT_GROUP, relations=[
                    {"key": "isContactOf", "target": customer_id, "valid_from": valid_from}])
                self.redirect(f"/object?id={person_id}")
                return
            if path == "/activity":
                object_id = (form.get("object_id") or "").strip()
                group_key = (form.get("group_key") or "").strip()
                activity_key = (form.get("activity_key") or "").strip()
                if not (object_id and group_key in (FIRMA_GROUP, KONTAKT_GROUP) and activity_key in ACT_TITLES):
                    return self.back_to(object_id, "Bitte eine Art wählen.")
                twin.write_group(object_id, group_key, activities=[{"key": activity_key, "status": "open"}])
                self.redirect(f"/object?id={object_id}")
                return
        except twin.TwinError as exc:
            target = form.get("person_id") or form.get("object_id") or form.get("kunde_id") or ""
            return self.back_to(target.strip(), f"Abgelehnt: {exc.detail or exc}")
        self.send_html(404, render("Nicht gefunden", af.empty("Diese Aktion gibt es nicht.")))

    def back_to(self, obj_id, notice):
        """Nach einem Fehler: die Objektseite mit dem Hinweis, sonst die Liste."""
        obj = None
        if obj_id:
            try:
                obj = twin.get_object(obj_id)
            except twin.TwinError:
                obj = None
        if obj:
            self.send_html(200, page_object(obj, notice))
        else:
            self.send_html(200, render("Firmen", af.msg(notice, error=True) + af.back("./")))


def main():
    sys.stdout.write(f"{APP_NAME} {VERSION} auf Port {PORT} -- "
                     f"Zwilling: {'konfiguriert' if twin.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
