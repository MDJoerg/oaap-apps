"""Rollen & Rechte -- das Werkzeug des Mandanten-Admins für
fachliche Berechtigungen (RFC-0045 A7, oaap.core.authorization 0.3 §2.10).

Eine Seite beantwortet die Frage, die ein Vereinsvorstand wirklich hat:
*wer darf was, in welcher App, und warum.* Dahinter stehen vier Begriffe
der Plattform, hier in der Sprache des Alltags:

- **Rolle**: eine Vorlage, die eine App erklaert hat ("Trainer/in"), mit
  den Werten, die der Mandant einsetzt ("Bereich: News");
- **Berechtigungspaket**: ein Buendel von Rollen, das man EINER Person gibt
  (in Spec und API heisst es `collection`);
- **Zuordnung**: Person + Berechtigungspaket, mit Gueltigkeit und, wo die
  Rolle es verlangt, "Gilt fuer" (ein Kontext: "Mannschaft mB");
- **Gruppe des Anmeldedienstes**: eine Gruppe im Realm des Mandanten, die
  ein Berechtigungspaket gibt -- bei JEDER Anmeldung neu gelesen.

Was diese App NICHT ist: eine zweite Wahrheit. Sie haelt nichts. Jede Seite
fragt den Identity-Dienst frisch; ohne ihn zeigt sie nichts. Und sie
entscheidet nie, wer verwalten darf: jeder Aufruf nennt die Person aus
`X-OAAP-User-Id`, und der Identity-Dienst prueft sie selbst (die
Plattformrolle tenant_admin wird Apps nicht weitergegeben -- diese App
kann sie gar nicht lesen). Wer kein Admin ist, sieht eine Seite, die das
sagt, und kann nichts aendern.

Aendernde Formulare (POST) werden nur angenommen, wenn die Anfrage von
DIESER Seite kommt (Origin/Referer gegen Host): die Sitzung der Plattform
ist ein Keks, und ein Keks wird von jeder fremden Seite mitgeschickt.

Seit 0.3.0 im Rahmen der Plattform (RFC-0118): Reiter als Links, Listen
als Listenbericht, und keine Formulare mehr in Tabellenzeilen -- Beenden,
Ausblenden und Entfernen stehen auf der Seite des Objekts, Anlegen auf einer
eigenen Seite. Dazu eine MCP-Tür mit Lesewerkzeugen, die den Dienst als
dieselbe Person fragen wie die Seite.

Nur Standardbibliothek (plus door.py, der Zugang zur Tür, und die Kopien
appframe/ und mcpdoor/ aus lib/).
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import appframe as af
import door
import mcpdoor

VERSION = "0.3.0"
PORT = 8000

esc = html.escape


# ------------------------------------------------ reines Wissen (testbar)

def _decl_of(decls, app):
    for d in decls or ():
        if d.get("app") == app:
            return d.get("declaration") or {}
    return {}


def template_of(decls, app, key):
    for t in _decl_of(decls, app).get("role_templates") or ():
        if t.get("key") == key:
            return t
    return {}


def _declared_values(decl, obj, field):
    for o in decl.get("objects") or ():
        if o.get("key") == obj:
            for f in o.get("fields") or ():
                if f.get("key") == field:
                    return list(f.get("values") or ())
    return []


def value_fields(decls, app, template):
    """[(object, field, [declared value ids])] die der Mandant einsetzt."""
    decl = _decl_of(decls, app)
    out = []
    for g in template_of(decls, app, template).get("grants") or ():
        for fk, spec in g.items():
            if spec == "$value":
                out.append((g["object"], fk,
                            _declared_values(decl, g["object"], fk)))
    return out


def context_fields(decls, roles_by_id, collection):
    """[(feld, objekttyp)] die eine Zuordnung dieses Pakets ausfuellt."""
    out = []
    for rid in collection.get("roles") or ():
        r = roles_by_id.get(rid) or {}
        decl = _decl_of(decls, r.get("app"))
        for g in template_of(decls, r.get("app"), r.get("template")) \
                .get("grants") or ():
            for fk, spec in g.items():
                if spec == "$context":
                    typ = ""
                    for o in decl.get("objects") or ():
                        if o.get("key") == g["object"]:
                            for f in o.get("fields") or ():
                                if f.get("key") == fk:
                                    typ = f.get("context") or ""
                    if (fk, typ) not in out:
                        out.append((fk, typ))
    return out


def hands_on(decls, roles_by_id, collection):
    """Die Vorlagen, die dieses Paket weitergeben laesst (`may_grant`)."""
    out = []
    for rid in collection.get("roles") or ():
        r = roles_by_id.get(rid) or {}
        out += template_of(decls, r.get("app"), r.get("template")).get("may_grant") or []
    return out


def origin_ok(headers):
    """Nur Formulare DIESER Seite (Origin, sonst Referer, gegen Host)."""
    hosts = {h for h in (headers.get("Host", ""),
                         headers.get("X-Forwarded-Host", "")) if h}
    for name in ("Origin", "Referer"):
        v = headers.get(name, "")
        if v:
            return urlparse(v).netloc in hosts
    return False


def parse_form(raw):
    return {k: v for k, v in parse_qs(raw, keep_blank_values=True).items()}


def one(form, key):
    return (form.get(key) or [""])[0].strip()


# --------------------------------------------------------------- Optik
# Der Rahmen kommt aus appframe (Kit und Rahmenskript der Plattform);
# hier steht nur, was dieser App eigen ist.

APP_NAME = "Rollen & Rechte"

PAGE_STYLE = (".rr-checks label{display:block;margin:.15rem 0}"
              ".rr-checks{display:block}"
              ".rr-actions{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:var(--oaap-space-3)}"
              ".rr-actions form{display:inline}")

TABS = [("/", "Personen"), ("/agents", "Agenten"), ("/roles", "Rollen"),
        ("/collections", "Berechtigungspakete"), ("/delegated", "Weitergaben"),
        ("/mappings", "Gruppen → Paket"), ("/log", "Protokoll")]

# Unterseiten gehören zum Reiter ihrer Liste.
TAB_OF = {"/person": "/", "/assign": "/", "/assignment": "/",
          "/role": "/roles", "/roles/new": "/roles",
          "/collection": "/collections", "/collections/new": "/collections",
          "/mapping": "/mappings", "/mappings/new": "/mappings"}

NOTICES = {
    "role-add": "Rolle angelegt.", "role-retire": "Rolle ausgeblendet.",
    "collection-add": "Berechtigungspaket angelegt.",
    "collection-retire": "Berechtigungspaket ausgeblendet.",
    "assign": "Zugeordnet.", "revoke": "Zuordnung beendet.",
    "map-add": "Gruppe zugeordnet – wird bei jeder Anmeldung neu gelesen.",
    "map-remove": "Abbildung entfernt; was sie gab, ist beendet.",
}

LOG_WORDS = {
    "authz.assign": "zugeordnet", "authz.revoke": "Zuordnung beendet",
    "authz.role-create": "Rolle angelegt", "authz.role-retire": "Rolle ausgeblendet",
    "authz.collection-create": "Paket angelegt",
    "authz.collection-retire": "Paket ausgeblendet",
    "authz.mapping-add": "Gruppe zugeordnet", "authz.mapping-remove": "Abbildung entfernt",
    "authz.delegate-assign": "weitergegeben", "authz.delegate-revoke": "Weitergabe beendet",
    "authz.delegate-lookup": "Name nachgeschlagen", "authz.idp-sync": "Gruppen gelesen",
    "authz.register": "Rechte einer App eingetragen",
}


def page(body, active="/", title="", notice=""):
    tab = TAB_OF.get(active, active)
    return af.page(title, af.msg(notice) + body, app=APP_NAME, version=VERSION,
                   product="rollen-rechte", tabs=TABS, active=tab, style=PAGE_STYLE)


def card(text, kind="attention", title=""):
    """Eine Karte mit einem Satz (Hinweis, Fehler, Ablehnung)."""
    cls = "oaap-danger" if kind == "attention" else ""
    return af.card(title, f"<p style='margin:0'>{text}</p>", cls)


def denied_card():
    return card("Dafür braucht es die Rechte eines Mandanten-Administrators. "
                "Die Plattform hat diese Anfrage nicht zugelassen; hier "
                "wurde nichts angezeigt und nichts geändert.",
                title="Kein Zugriff")


def hidden(**kw):
    return "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
                   for k, v in kw.items())


def post_form(action, fields_html, label, back, danger=False, inline=False):
    cls = "" if inline else ' class="oaap-form"'
    btn = "danger" if danger else "primary"
    return (f'<form{cls} method="post" action="{action}">'
            f'{hidden(back=back)}{fields_html}'
            f'<div><button class="{btn}">{esc(label)}</button></div></form>')


def fmt_grant(g):
    fl = "; ".join(f"{esc(k)}: {esc(', '.join(v) if isinstance(v, list) else str(v))}"
                   for k, v in (g.get("fields") or {}).items())
    return (f"<code>{esc(g['object'])}</code> {esc(', '.join(g['activities']))}"
            + (f" <span class='oaap-muted'>({fl})</span>" if fl else ""))


def fmt_ctx(ctx):
    return "; ".join(f"{esc(k)}: {esc(', '.join(v))}" for k, v in (ctx or {}).items())


def _orphan_note(a):
    return af.badge("Geber hält es nicht mehr", "err") if a.get("orphaned") else ""


def _source(a):
    if a.get("source") == "idp":
        return f"Gruppe <code>{esc(a.get('via') or '')}</code> (bei jeder Anmeldung gelesen)"
    if a.get("source") == "delegated":
        return "weitergegeben"
    return "von Hand"


def _state_badge(a):
    if a.get("live"):
        return af.badge("gilt", "ok")
    if a.get("ended"):
        return af.badge("beendet")
    return af.badge("nicht (mehr) gültig")


def _retired_badge(x):
    return af.badge("ausgeblendet") if x.get("retired") else af.badge("aktiv", "ok")


def _status_ok(q, x):
    """Ausgeblendetes nur mit alle=1 (die Adresse trägt es, wie bisher)."""
    return q.filters.get("alle") == "1" or not x.get("retired")


def _all_link(q, base, noun):
    if q.filters.get("alle") == "1":
        return f"<p class='oaap-muted'><a href='{base}'>nur aktive {noun} zeigen</a></p>"
    return f"<p class='oaap-muted'><a href='{base}?alle=1'>auch ausgeblendete zeigen</a></p>"


# ------------------------------------------------------------- die Seiten

def _people_report(users, params, noun, empty_text, base):
    q = af.Query(params, filters=("status",), default_sort="name")
    shown = [u for u in users if not q.filters["status"]
             or (q.filters["status"] == "gesperrt") == (not u.get("active", True))]

    def row(u):
        locked = "" if u.get("active", True) else af.badge("gesperrt", "err")
        return (f"<tr><td><a href='/person?id={quote(u['id'])}'>{esc(u['username'])}</a></td>"
                f"<td>{esc(u.get('display_name') or '')}</td><td>{locked}</td></tr>")
    cols = [("Name", "name", lambda u: u["username"], ""),
            ("Anzeigename", "display", lambda u: u.get("display_name") or "", ""),
            ("Status", "", None, "")]
    return af.list_report(q, shown, cols, row, base=base + "?", noun=noun,
                          empty_text=empty_text,
                          choices=[("status", "Status", [("aktiv", "aktiv"),
                                                         ("gesperrt", "gesperrt")])],
                          placeholder="Name …")


def render_people(users, params=None):
    return (af.pagehead("Personen dieses Mandanten")
            + "<p class='oaap-muted'>Rechte gibt man einer Person über ihre Benutzer-ID. "
              "Konten legt die Plattform an, nicht diese Seite. Ein Klick auf den Namen "
              "zeigt, was die Person darf.</p>"
            + _people_report(users, params or {}, "Personen",
                             "Keine Personen. Konten legt die Plattform an.", "/"))


def render_agents(agents, params=None):
    return (af.pagehead("Agenten dieses Mandanten")
            + "<p class='oaap-muted'>Ein Agent ist ein eigenes Konto mit eigenen Zuordnungen: "
              "Er darf, was ihm zugeordnet ist, nicht was die Person darf, die ihn einsetzt. "
              "In der App und im Protokoll steht sein eigener Name. Weitergeben kann ein "
              "Agent nichts. Anlegen und seinen Schlüssel ausstellen: der Betreiber oder "
              "Mandanten-Admin mit <code>oaap authz agent-add</code>, nicht diese Seite.</p>"
            + _people_report(agents, params or {}, "Agenten",
                             "Keine Agenten. Ein Agent wird mit "
                             "<code>oaap authz agent-add</code> angelegt.", "/agents"))


def render_delegated(assignments, people, collections, params=None):
    """Was Personen weitergegeben haben und noch gilt, das Verwaiste zuerst."""
    names = {u["id"]: u["username"] for u in people}
    colls = {c["id"]: c["name"] for c in collections}
    live = [a for a in assignments if a.get("source") == "delegated" and a.get("live")]
    live.sort(key=lambda a: (not a.get("orphaned"), a.get("granted_at", "")))
    q = af.Query(params or {}, filters=("verwaist",))
    if q.filters["verwaist"]:
        live = [a for a in live if bool(a.get("orphaned")) == (q.filters["verwaist"] == "ja")]

    def row(a):
        return (f"<tr><td>{esc(names.get(a['subject'], a['subject']))}</td>"
                f"<td><a href='/assignment?id={quote(a['id'])}'>"
                f"{esc(colls.get(a['collection'], '?'))}</a></td>"
                f"<td>{fmt_ctx(a.get('context')) or '–'}</td>"
                f"<td>{esc(a.get('valid_to') or '–')}</td>"
                f"<td>{esc(a.get('granted_by') or '')} {_orphan_note(a)}</td></tr>")
    cols = [("Person", "person", lambda a: names.get(a["subject"], a["subject"]), ""),
            ("Berechtigungspaket", "paket", lambda a: colls.get(a["collection"], ""), ""),
            ("Gilt für", "", None, ""),
            ("bis", "bis", lambda a: a.get("valid_to") or "", "oaap-minor"),
            ("Gegeben von", "von", lambda a: a.get("granted_by") or "", "")]
    report = af.list_report(q, live, cols, row, base="/delegated?", noun="Weitergaben",
                            empty_text="Keine gültige Weitergabe. Hier erscheint, was "
                                       "Personen in einer App selbst weitergeben.",
                            choices=[("verwaist", "Verwaist",
                                      [("ja", "ja"), ("nein", "nein")])],
                            placeholder="Person, Paket …")
    return (af.pagehead("Weitergaben")
            + "<p class='oaap-muted'>Hier steht, was Personen selbst weitergegeben haben, "
              "ohne dass ein Admin beteiligt war. Geht eine Person, bleibt, was sie gegeben "
              "hat: Es gilt weiter, bis jemand es beendet. Solche Zeilen stehen oben. "
              "Ein Klick auf das Paket öffnet die Zuordnung; dort lässt sie sich beenden.</p>"
            + "<p class='oaap-muted'>Eine App, die das Weitergeben anbietet, sieht den "
              "Nachweis jeder Person, die sie besucht, und könnte ihn bis zu fünf Minuten "
              "lang wiederverwenden. Mehr als diese Person in dieser App weitergeben darf, "
              "kann sie damit nicht. Das Protokoll nennt jede Weitergabe.</p>"
            + report)


def render_person(uid, eff, collections, roles, decls):
    roles_by_id = {r["id"]: r for r in roles}
    grants = [f"<tr><td>{esc(a['app'])}</td><td>{fmt_grant(g)}</td></tr>"
              for a in eff.get("apps") or [] for g in a["grants"]]
    grants_html = (af.table("<th>App</th><th>darf</th>", grants) if grants else
                   af.empty("Im Moment nichts – keine gültige Zuordnung."))
    rows = []
    for a in eff.get("assignments") or []:
        span = f"{esc(a['valid_from'] or '–')} bis {esc(a['valid_to'] or '–')}"
        rows.append(
            f"<tr><td><a href='/assignment?id={quote(a['id'])}'><b>"
            f"{esc(a['collection_name'])}</b></a><br><span class='oaap-muted'>"
            f"{esc(', '.join(a['roles']))}</span></td>"
            f"<td>{fmt_ctx(a.get('context')) or '–'}</td><td class='oaap-minor'>{span}</td>"
            f"<td>{_source(a)}<br><span class='oaap-muted'>gegeben von "
            f"{esc(a['granted_by'])}</span>"
            f"{'<br>' + _orphan_note(a) if a.get('orphaned') else ''}</td>"
            f"<td>{_state_badge(a)}</td></tr>")
    table = af.table("<th>Berechtigungspaket</th><th>Gilt für</th>"
                     "<th class='oaap-minor'>Zeitraum</th><th>Woher</th><th>Status</th>",
                     rows, "Noch keine Zuordnung.")
    give = []
    agent = eff.get("kind") == "machine"
    for c in collections:
        # Ein Agent gibt nichts weiter; der Dienst lehnt das Paket ohnehin ab.
        if c.get("retired") or (agent and hands_on(decls, roles_by_id, c)):
            continue
        give.append(
            f"<tr><td><a href='/assign?user={quote(uid)}&amp;collection={quote(c['id'])}'>"
            f"{esc(c['name'])}</a></td><td class='oaap-muted'>"
            f"{esc(', '.join((roles_by_id.get(r) or {}).get('name', '?') for r in c['roles']))}"
            "</td></tr>")
    give_html = af.table("<th>Berechtigungspaket</th><th>Rollen</th>", give,
                         "Es gibt noch kein Berechtigungspaket – zuerst unter "
                         "<a href='/roles'>Rollen</a> und "
                         "<a href='/collections'>Berechtigungspakete</a> anlegen.")
    name = eff.get("username") or uid
    head = (af.back("/agents" if agent else "/", "Zu den Agenten" if agent else "Zu den Personen")
            + f'<div class="oaap-objhead"><h1>{esc(name)}</h1>'
            + af.facts([("Art", "Agent" if agent else "Person"),
                        ("Benutzer-ID", f"<code>{esc(uid)}</code>")]) + "</div>")
    return (head
            + af.card(f"{name} darf zur Zeit", grants_html)
            + af.card("Zuordnungen", table)
            + af.card("Ein Berechtigungspaket zuordnen",
                      "<p class='oaap-muted'>Ein Paket bündelt Rollen. Ein Klick auf das Paket "
                      "öffnet das Formular; wo eine Rolle es verlangt, sagt „Gilt für“, "
                      "WOFÜR das Recht gilt (zum Beispiel eine Mannschaft).</p>" + give_html))


def render_assign(uid, eff, collection, roles, decls):
    """Die Dialogseite: ein Paket einer Person zuordnen."""
    roles_by_id = {r["id"]: r for r in roles}
    name = eff.get("username") or uid
    back = "/person?id=" + quote(uid)
    head = af.back(back, f"Zu {name}") + af.pagehead(f"{collection['name']} zuordnen")
    if eff.get("kind") == "machine" and hands_on(decls, roles_by_id, collection):
        return head + card("Ein Agent gibt nichts weiter: Pakete, mit denen man "
                           "weitergeben darf, bekommt er nicht.")
    ctx = "".join(
        f"<label>{esc(fk)}" + (f" (ID eines Objekts vom Typ {esc(typ)})" if typ else "")
        + f"<input type='text' name='ctx_{esc(fk)}' required></label>"
        for fk, typ in context_fields(decls, roles_by_id, collection))
    rolenames = ", ".join((roles_by_id.get(r) or {}).get("name", "?")
                          for r in collection.get("roles") or [])
    return (head
            + af.card("", f"<p>Person: <b>{esc(name)}</b><br>Rollen im Paket: "
                          f"{esc(rolenames) or '–'}</p>"
                      + ("<p class='oaap-muted'>„Gilt für“ wird als ID des Objekts im "
                         "Zwilling eingetragen; mehrere durch Komma.</p>" if ctx else "")
                      + post_form("/do/assign",
                                  hidden(user=uid, collection=collection["id"]) + ctx
                                  + "<label>von (optional)<input type='date' name='from'></label>"
                                    "<label>bis (optional)<input type='date' name='to'></label>",
                                  "Zuordnen", back)))


def render_assignment(a, people, collections):
    """Die Objektseite einer Zuordnung; hier wird sie beendet."""
    names = {u["id"]: u["username"] for u in people}
    colls = {c["id"]: c["name"] for c in collections}
    who = names.get(a["subject"], a["subject"])
    person = "/person?id=" + quote(a["subject"])
    span = f"{esc(a.get('valid_from') or '–')} bis {esc(a.get('valid_to') or '–')}"
    end = (post_form("/do/revoke", hidden(id=a["id"]), "Beenden", person, danger=True,
                     inline=True) if a.get("live") else "")
    return (af.back(person, f"Zu {who}")
            + f'<div class="oaap-objhead"><h1>{esc(colls.get(a["collection"], "?"))} für {esc(who)}</h1>'
            + af.facts([("Status", _state_badge(a) + " " + _orphan_note(a)),
                        ("Person", f"<a href='{person}'>{esc(who)}</a>"),
                        ("Gilt für", fmt_ctx(a.get("context")) or "–"),
                        ("Zeitraum", span), ("Woher", _source(a)),
                        ("Gegeben von", esc(a.get("granted_by") or "–"))]) + "</div>"
            + (af.card("Beenden", "<p class='oaap-muted'>Beenden löscht nichts: die Zuordnung "
                                  "bleibt als „beendet“ sichtbar und im Protokoll.</p>"
                       + f'<div class="rr-actions">{end}</div>') if end else ""))


def _held(collections):
    held = {}
    for c in collections:
        if not c.get("retired"):
            for rid in c["roles"]:
                held.setdefault(rid, []).append(c["name"])
    return held


def render_roles(decls, roles, collections, params=None):
    held = _held(collections)
    q = af.Query(params or {}, filters=("app", "alle"))
    shown = [r for r in roles if _status_ok(q, r)
             and (not q.filters["app"] or r["app"] == q.filters["app"])]

    def title(r):
        return template_of(decls, r["app"], r["template"]).get("title") or r["template"]

    def row(r):
        tpl = template_of(decls, r["app"], r["template"])
        vals = "; ".join(f"{esc(k)} = {esc(', '.join(v))}"
                         for k, v in (r.get("values") or {}).items())
        passes = ", ".join((template_of(decls, r["app"], k).get("title") or k)
                           for k in tpl.get("may_grant") or ())
        return (f"<tr><td><a href='/role?id={quote(r['id'])}'><b>{esc(r['name'])}</b></a></td>"
                f"<td>{esc(r['app'])} / {esc(title(r))}"
                + (f"<br><span class='oaap-muted'>darf weitergeben: {esc(passes)}</span>"
                   if passes else "")
                + f"</td><td>{vals or '–'}</td>"
                f"<td class='oaap-minor'>{esc(', '.join(held.get(r['id'], [])) or '–')}</td>"
                f"<td>{_retired_badge(r)}</td></tr>")
    cols = [("Name", "name", lambda r: r["name"], ""),
            ("Vorlage", "vorlage", lambda r: r["app"] + " " + title(r), ""),
            ("Werte", "", None, ""),
            ("in Paketen", "", None, "oaap-minor"),
            ("Status", "status", lambda r: "1" if r.get("retired") else "0", "")]
    apps = sorted({r["app"] for r in roles} | {d["app"] for d in decls or ()})
    report = af.list_report(q, shown, cols, row, base="/roles?", noun="Rollen",
                            empty_text="Noch keine Rolle. <a href='/roles/new'>Neue Rolle</a> "
                                       "legt die erste aus einer Vorlage an.",
                            choices=[("app", "App", [(a, a) for a in apps])],
                            placeholder="Name, Vorlage …") + _all_link(q, "/roles", "Rollen")
    return (af.pagehead("Rollen", "<a class='oaap-btn primary' href='/roles/new'>Neue Rolle</a>")
            + "<p class='oaap-muted'>Eine Rolle ist eine fachliche Rolle, die eine App vorgibt, "
              "mit den Werten, die dieser Mandant einsetzt. Sie gibt nichts, bis ein "
              "Berechtigungspaket mit ihr einer Person zugeordnet ist.</p>" + report)


def render_roles_new(decls):
    forms = []
    for d in decls or ():
        for t in (d.get("declaration") or {}).get("role_templates") or []:
            vf = value_fields(decls, d["app"], t["key"])
            vals = "".join(
                f"<fieldset class='rr-checks'><legend>{esc(fk)} (mindestens eins)</legend>"
                + "".join(f"<label><input type='checkbox' name='v_{esc(fk)}' "
                          f"value='{esc(v)}'> {esc(v)}</label>" for v in vs)
                + "</fieldset>" for _o, fk, vs in vf)
            forms.append(af.card(
                f"{t.get('title') or t['key']} ({d['app']})",
                post_form("/do/role-add",
                          hidden(app=d["app"], template=t["key"])
                          + "<label>Name der Rolle<input type='text' name='name' "
                            "required maxlength='80'></label>" + vals,
                          "Rolle anlegen", "/roles")))
    return (af.back("/roles", "Zu den Rollen") + af.pagehead("Neue Rolle aus einer Vorlage")
            + ("".join(forms) or card("Keine App dieses Mandanten hat Rechte erklärt.")))


def render_role(r, decls, collections):
    tpl = template_of(decls, r["app"], r["template"])
    passes = ", ".join((template_of(decls, r["app"], k).get("title") or k)
                       for k in tpl.get("may_grant") or ())
    vals = "; ".join(f"{esc(k)} = {esc(', '.join(v))}" for k, v in (r.get("values") or {}).items())
    held = _held(collections).get(r["id"], [])
    act = ("" if r.get("retired") else af.card(
        "Ausblenden",
        "<p class='oaap-muted'>Ausblenden löscht nichts und geht erst, wenn kein sichtbares "
        "Berechtigungspaket die Rolle mehr enthält.</p>"
        + post_form("/do/role-retire", hidden(id=r["id"]), "Ausblenden", "/roles",
                    danger=True, inline=True)))
    return (af.back("/roles", "Zu den Rollen")
            + f'<div class="oaap-objhead"><h1>{esc(r["name"])}</h1>'
            + af.facts([("Status", _retired_badge(r)), ("App", esc(r["app"])),
                        ("Vorlage", esc(tpl.get("title") or r["template"])),
                        ("Werte", vals or "–"),
                        ("Darf weitergeben", esc(passes) or "–"),
                        ("In Paketen", esc(", ".join(held)) or "–")]) + "</div>" + act)


def render_collections(roles, collections, params=None):
    names = {r["id"]: r for r in roles}
    q = af.Query(params or {}, filters=("alle",))
    shown = [c for c in collections if _status_ok(q, c)]

    def rolenames(c):
        return ", ".join((names.get(r) or {}).get("name", "?") for r in c["roles"])

    def row(c):
        return (f"<tr><td><a href='/collection?id={quote(c['id'])}'><b>{esc(c['name'])}</b></a></td>"
                f"<td>{esc(rolenames(c))}</td><td>{_retired_badge(c)}</td></tr>")
    cols = [("Name", "name", lambda c: c["name"], ""),
            ("Rollen", "rollen", rolenames, ""),
            ("Status", "status", lambda c: "1" if c.get("retired") else "0", "")]
    report = af.list_report(q, shown, cols, row, base="/collections?",
                            noun="Berechtigungspakete",
                            empty_text="Noch kein Berechtigungspaket. "
                                       "<a href='/collections/new'>Neues Berechtigungspaket</a> "
                                       "bündelt Rollen.",
                            placeholder="Name, Rolle …") + _all_link(q, "/collections", "Pakete")
    return (af.pagehead("Berechtigungspakete",
                        "<a class='oaap-btn primary' href='/collections/new'>Neues Berechtigungspaket</a>")
            + "<p class='oaap-muted'>Ein Berechtigungspaket bündelt Rollen – auch über mehrere "
              "Apps. Nur Pakete werden Personen zugeordnet, nie einzelne Rollen.</p>" + report)


def render_collections_new(roles):
    pick = "".join(f"<label><input type='checkbox' name='roles' value='{esc(r['id'])}'> "
                   f"{esc(r['name'])} <span class='oaap-muted'>({esc(r['app'])})</span></label>"
                   for r in roles if not r.get("retired"))
    return (af.back("/collections", "Zu den Berechtigungspaketen")
            + af.pagehead("Neues Berechtigungspaket")
            + af.card("", post_form(
                "/do/collection-add",
                "<label>Name<input type='text' name='name' required maxlength='80'></label>"
                "<fieldset class='rr-checks'><legend>Rollen</legend>"
                + (pick or "<span class='oaap-muted'>Zuerst eine <a href='/roles/new'>Rolle "
                            "anlegen</a>.</span>")
                + "</fieldset>", "Berechtigungspaket anlegen", "/collections")))


def render_collection(c, roles):
    names = {r["id"]: r for r in roles}
    rl = "".join(f"<li><a href='/role?id={quote(r)}'>{esc((names.get(r) or {}).get('name', '?'))}</a>"
                 f" <span class='oaap-muted'>({esc((names.get(r) or {}).get('app', ''))})</span></li>"
                 for r in c["roles"])
    act = ("" if c.get("retired") else af.card(
        "Ausblenden",
        "<p class='oaap-muted'>Ausblenden löscht nichts und geht nur, wenn keine gültige "
        "Zuordnung und keine Gruppe mehr auf dem Paket steht. Der Dienst sagt, was im "
        "Weg steht.</p>"
        + post_form("/do/collection-retire", hidden(id=c["id"]), "Ausblenden",
                    "/collections", danger=True, inline=True)))
    return (af.back("/collections", "Zu den Berechtigungspaketen")
            + f'<div class="oaap-objhead"><h1>{esc(c["name"])}</h1>'
            + af.facts([("Status", _retired_badge(c))]) + "</div>"
            + af.card("Rollen im Paket", f"<ul>{rl}</ul>" if rl else af.empty("Keine Rollen."))
            + act)


MAPPING_NOTE = ("<p class='oaap-muted'>Die Gruppen selbst pflegt der Anmeldedienst, nicht "
                "diese Seite; eine Person kann in mehreren sein. Hier legen Sie nur fest, "
                "welches Berechtigungspaket eine Gruppe gibt. Das Token trägt den <b>Pfad</b> "
                "der Gruppe (<code>Verein/Hallenwart</code>); eine umbenannte Gruppe gibt "
                "deshalb nichts mehr. Gelesen wird bei jeder Anmeldung – eine Änderung wirkt "
                "also beim nächsten Login der Person, nicht sofort. Entfernt man die "
                "Abbildung, endet sofort, was sie gab. Pakete, bei denen „Gilt für“ "
                "auszufüllen ist, lassen sich nicht über eine Gruppe geben.</p>")


def render_mappings(mappings, collections=None, params=None):
    q = af.Query(params or {}, default_sort="gruppe")

    def row(m):
        return (f"<tr><td><a href='/mapping?id={quote(m['id'])}'><code>{esc(m['group'])}</code></a></td>"
                f"<td>{esc(m.get('collection_name') or '')}</td></tr>")
    cols = [("Gruppe", "gruppe", lambda m: m["group"], ""),
            ("gibt Berechtigungspaket", "paket", lambda m: m.get("collection_name") or "", "")]
    report = af.list_report(q, mappings, cols, row, base="/mappings?", noun="Abbildungen",
                            empty_text="Noch keine Abbildung. <a href='/mappings/new'>Neue "
                                       "Abbildung</a> lässt eine Gruppe ein Paket geben.",
                            placeholder="Gruppe, Paket …")
    return (af.pagehead("Automatisch über Gruppen des Anmeldedienstes",
                        "<a class='oaap-btn primary' href='/mappings/new'>Neue Abbildung</a>")
            + MAPPING_NOTE + report)


def render_mappings_new(collections):
    opts = "".join(f"<option value='{esc(c['id'])}'>{esc(c['name'])}</option>"
                   for c in collections if not c.get("retired"))
    return (af.back("/mappings", "Zu den Gruppen") + af.pagehead("Neue Abbildung")
            + MAPPING_NOTE
            + af.card("", post_form(
                "/do/map-add",
                "<label>Pfad der Gruppe<input type='text' name='group' "
                "required placeholder='Verein/Hallenwart'></label>"
                "<label>Berechtigungspaket<select name='collection'>" + opts + "</select></label>",
                "Zuordnen", "/mappings")))


def render_mapping(m):
    return (af.back("/mappings", "Zu den Gruppen")
            + f'<div class="oaap-objhead"><h1><code>{esc(m["group"])}</code></h1>'
            + af.facts([("gibt Berechtigungspaket", esc(m.get("collection_name") or "–"))])
            + "</div>"
            + af.card("Entfernen",
                      "<p class='oaap-muted'>Entfernt man die Abbildung, endet sofort, "
                      "was sie gab.</p>"
                      + post_form("/do/map-remove", hidden(id=m["id"]), "Entfernen",
                                  "/mappings", danger=True, inline=True)))


def render_log(rows, params=None):
    q = af.Query(params or {}, default_sort="-wann")

    def row(e):
        act = e.get("action", "")
        return (f"<tr><td>{esc(e.get('when', '')[:19].replace('T', ' '))}</td>"
                f"<td>{esc(e.get('who', ''))}</td>"
                f"<td><span title='{esc(act)}'>{esc(LOG_WORDS.get(act, act))}</span></td>"
                f"<td>{esc(str(e.get('subject', '')))}</td>"
                f"<td class='oaap-muted oaap-minor'>{esc(e.get('detail', ''))}</td></tr>")
    cols = [("Wann (UTC)", "wann", lambda e: e.get("when", ""), ""),
            ("Wer", "wer", lambda e: e.get("who", ""), ""),
            ("Was", "was", lambda e: LOG_WORDS.get(e.get("action", ""), e.get("action", "")), ""),
            ("Betrifft", "betrifft", lambda e: str(e.get("subject", "")), ""),
            ("Einzelheiten", "", None, "oaap-minor")]
    return (af.pagehead("Protokoll der Rechte")
            + "<p class='oaap-muted'>Die letzten Änderungen an Rollen, Berechtigungspaketen, "
              "Zuordnungen und Abbildungen dieses Mandanten – mit der Person, die sie "
              "gemacht hat.</p>"
            + af.list_report(q, rows, cols, row, base="/log?", noun="Einträge",
                             empty_text="Noch nichts geändert.", placeholder="Person, Aktion …"))


# ------------------------------------------------------------------ MCP
# Nur lesen. Jedes Werkzeug fragt den Identity-Dienst als die Person, die
# das MCP-Gateway durchreicht (X-OAAP-User-Id) -- genau wie die Seite. Wer
# kein Mandanten-Admin ist, bekommt die Ablehnung des Dienstes, keine Namen.

DOOR = mcpdoor.Door(
    "oaap-rollen-rechte", VERSION,
    "Fachliche Berechtigungen des Mandanten lesen: Personen, Rollen, Berechtigungspakete, "
    "Zuordnungen und was eine Person zur Zeit darf. Nur für Mandanten-Administratoren.")


def _ask(who, path, params=None):
    try:
        return door.call("GET", path, who.get("user_id") or "", params=params)
    except door.DoorError as e:
        if e.denied:
            raise mcpdoor.Refused("Dafür braucht es die Rechte eines Mandanten-Administrators.")
        raise mcpdoor.Refused(e.detail)


@DOOR.tool("rollen_rechte_people", "Personen und Agenten",
           "Listet die Personen (oder Agenten) des Mandanten mit Benutzer-ID, Name und ob "
           "das Konto gesperrt ist. Optional mit Suchwort.",
           {"q": {"type": "string", "description": "Suchwort im Namen"},
            "kind": {"type": "string", "description": "person (Standard) oder agent"}})
def _mcp_people(args, who):
    params = {"kind": "machine"} if str(args.get("kind") or "") == "agent" else None
    users = _ask(who, "users", params)["users"]
    q = af.Query({"q": str(args.get("q") or "")})
    return {"people": [{"id": u["id"], "name": u["username"],
                        "display_name": u.get("display_name") or "",
                        "active": bool(u.get("active", True))}
                       for u in users if q.matches(u["username"], u.get("display_name"))]}


@DOOR.tool("rollen_rechte_person_read", "Was darf eine Person?",
           "Liest, was eine Person (oder ein Agent) zur Zeit darf, je App, und ihre "
           "Zuordnungen mit Paket, Gilt für, Zeitraum, Herkunft und Status.",
           {"person": {"type": "string", "description": "Benutzer-ID oder Name"}},
           required=("person",))
def _mcp_person(args, who):
    want = str(args.get("person") or "").strip()
    uid = want
    users = _ask(who, "users")["users"] + _ask(who, "users", {"kind": "machine"})["users"]
    hit = next((u for u in users if u["id"] == want or u["username"] == want), None)
    if not hit:
        raise mcpdoor.Refused("Diese Person gibt es in diesem Mandanten nicht.")
    uid = hit["id"]
    eff = _ask(who, "effective", {"user": uid})
    return {"id": uid, "name": eff.get("username") or hit["username"],
            "kind": "agent" if eff.get("kind") == "machine" else "person",
            "may": [{"app": a["app"], "object": g.get("object"),
                     "activities": g.get("activities"), "fields": g.get("fields") or {}}
                    for a in eff.get("apps") or [] for g in a["grants"]],
            "assignments": [{"id": a["id"], "package": a.get("collection_name", ""),
                             "roles": a.get("roles") or [], "context": a.get("context") or {},
                             "valid_from": a.get("valid_from"), "valid_to": a.get("valid_to"),
                             "source": a.get("source") or "manual",
                             "granted_by": a.get("granted_by") or "",
                             "live": bool(a.get("live")), "ended": bool(a.get("ended")),
                             "orphaned": bool(a.get("orphaned"))}
                            for a in eff.get("assignments") or []]}


@DOOR.tool("rollen_rechte_roles", "Rollen und Pakete",
           "Listet die Rollen des Mandanten (App, Vorlage, Werte, ausgeblendet) und die "
           "Berechtigungspakete mit ihren Rollen.",
           {"app": {"type": "string", "description": "nur Rollen dieser App"}})
def _mcp_roles(args, who):
    decls = _ask(who, "declarations")["declarations"]
    roles = _ask(who, "roles")["roles"]
    colls = _ask(who, "collections")["collections"]
    app = str(args.get("app") or "").strip()
    names = {r["id"]: r["name"] for r in roles}
    return {"roles": [{"id": r["id"], "name": r["name"], "app": r["app"],
                       "template": template_of(decls, r["app"], r["template"]).get("title")
                       or r["template"], "values": r.get("values") or {},
                       "retired": bool(r.get("retired"))}
                      for r in roles if not app or r["app"] == app],
            "packages": [{"id": c["id"], "name": c["name"],
                          "roles": [names.get(x, "?") for x in c["roles"]],
                          "retired": bool(c.get("retired"))} for c in colls]}


@DOOR.tool("rollen_rechte_assignments", "Zuordnungen",
           "Listet die gültigen Zuordnungen des Mandanten: wer welches Berechtigungspaket "
           "hat, wofür, bis wann und woher (von Hand, Gruppe, weitergegeben). Optional "
           "nur die eines Pakets.",
           {"package": {"type": "string", "description": "Name des Berechtigungspakets"}})
def _mcp_assignments(args, who):
    users = _ask(who, "users")["users"] + _ask(who, "users", {"kind": "machine"})["users"]
    names = {u["id"]: u["username"] for u in users}
    colls = {c["id"]: c["name"] for c in _ask(who, "collections")["collections"]}
    want = str(args.get("package") or "").strip()
    rows = [a for a in _ask(who, "assignments")["assignments"] if a.get("live")
            and (not want or colls.get(a["collection"]) == want)]
    return {"assignments": [{"id": a["id"], "person": names.get(a["subject"], a["subject"]),
                             "package": colls.get(a["collection"], ""),
                             "context": a.get("context") or {},
                             "valid_to": a.get("valid_to"),
                             "source": a.get("source") or "manual",
                             "granted_by": a.get("granted_by") or "",
                             "orphaned": bool(a.get("orphaned"))} for a in rows[:300]],
            "total": len(rows)}


DOOR.prompt("rollen_rechte_person", "Was darf eine Person?",
            "Erklärt, was eine Person zur Zeit darf und woher.",
            "Lies mit rollen_rechte_person_read, was „{person}“ zur Zeit darf, und erkläre es "
            "in einfachen Worten: je App, was die Person tun darf, und woher das Recht kommt "
            "(Paket, von Hand, Gruppe, weitergegeben, bis wann).", arguments=("person",))
DOOR.prompt("rollen_rechte_who", "Wer darf was in einer App?",
            "Zeigt, wer in einer App welche Rollen hat.",
            "Finde mit rollen_rechte_roles die Rollen der App „{app}“ und mit "
            "rollen_rechte_assignments, wer die Pakete mit diesen Rollen hat. Antworte als "
            "kurze Liste: Rolle – Personen.", arguments=("app",))
DOOR.prompt("rollen_rechte_review", "Rechte prüfen",
            "Sucht Auffälligkeiten in den Rechten des Mandanten.",
            "Prüfe mit rollen_rechte_assignments die gültigen Zuordnungen: Nenne verwaiste "
            "Weitergaben (Geber hält es nicht mehr), Zuordnungen ohne Enddatum, die weitergegeben "
            "wurden, und Pakete, die niemand hat. Kurz und sachlich.")


# --------------------------------------------------------------- Server

def do_action(action, form, person):
    """Eine ändernde Aktion; wirft door.DoorError. Gibt die Kennung."""
    call = door.call
    if action == "role-add":
        values = {k[2:]: v for k, v in form.items() if k.startswith("v_")}
        call("POST", "roles", person, {
            "app": one(form, "app"), "template": one(form, "template"),
            "name": one(form, "name"), "values": values})
    elif action == "role-retire":
        call("POST", "roles/" + quote(one(form, "id")) + "/retire", person)
    elif action == "collection-add":
        call("POST", "collections", person, {
            "name": one(form, "name"), "roles": form.get("roles") or []})
    elif action == "collection-retire":
        call("POST", "collections/" + quote(one(form, "id")) + "/retire", person)
    elif action == "assign":
        ctx = {}
        for k, v in form.items():
            if k.startswith("ctx_"):
                ctx[k[4:]] = [x.strip() for x in v[0].split(",") if x.strip()]
        call("POST", "assignments", person, {
            "collection": one(form, "collection"), "user": one(form, "user"),
            "context": ctx, "valid_from": one(form, "from") or None,
            "valid_to": one(form, "to") or None})
    elif action == "revoke":
        call("DELETE", "assignments/" + quote(one(form, "id")), person)
    elif action == "map-add":
        call("POST", "mappings", person, {
            "group": one(form, "group"), "collection": one(form, "collection")})
    elif action == "map-remove":
        call("DELETE", "mappings/" + quote(one(form, "id")), person)
    else:
        return None
    return action


def safe_back(path):
    """Nur Wege dieser App, nie eine fremde Adresse."""
    p = urlparse(path or "")
    if p.scheme or p.netloc or not (p.path or "/").startswith("/") \
            or (p.path or "/").startswith("//"):
        return "/"
    return (p.path or "/") + ("?" + p.query if p.query else "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-rollen-rechte/" + VERSION

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def person(self):
        return self.headers.get("X-OAAP-User-Id", "").strip()

    def send_html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, doc):
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def redirect(self, where):
        self.send_response(303)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def fail(self, exc, active="/"):
        if exc.denied:
            self.send_html(403, page(denied_card(), active))
        else:
            self.send_html(200, page(card(esc(exc.detail)), active))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION,
                                 "door": door.configured()})
            return
        if path == "/mcp":
            # vor der Sitzungsprüfung: das MCP-Gateway hat die Person
            # angemeldet; die Werkzeuge fragen den Dienst als sie.
            DOOR.serve(self)
            return
        if not self.headers.get("X-OAAP-Roles"):
            self.send_html(403, page(card("Diese Seite braucht eine angemeldete "
                                          "Sitzung."), path))
            return
        who = self.person()
        notice = NOTICES.get((qs.get("ok") or [""])[0], "")
        params = {k: v for k, v in qs.items() if k != "ok"}

        def get(what, **p):
            return door.call("GET", what, who, params=p or None)[what]
        try:
            if path == "/":
                body = render_people(get("users"), params)
            elif path == "/agents":
                body = render_agents(get("users", kind="machine"), params)
            elif path == "/delegated":
                people = get("users") + get("users", kind="machine")
                body = render_delegated(get("assignments"), people,
                                        get("collections"), params)
            elif path == "/person":
                uid = (qs.get("id") or [""])[0]
                eff = door.call("GET", "effective", who, params={"user": uid})
                body = render_person(uid, eff, get("collections"), get("roles"),
                                     get("declarations"))
            elif path == "/assign":
                uid = (qs.get("user") or [""])[0]
                cid = (qs.get("collection") or [""])[0]
                eff = door.call("GET", "effective", who, params={"user": uid})
                coll = next((c for c in get("collections") if c["id"] == cid
                             and not c.get("retired")), None)
                if coll is None:
                    body = card("Dieses Berechtigungspaket gibt es nicht (mehr).")
                else:
                    body = render_assign(uid, eff, coll, get("roles"), get("declarations"))
            elif path == "/assignment":
                aid = (qs.get("id") or [""])[0]
                a = next((x for x in get("assignments") if x["id"] == aid), None)
                body = (render_assignment(a, get("users") + get("users", kind="machine"),
                                          get("collections"))
                        if a else card("Diese Zuordnung gibt es nicht."))
            elif path == "/roles":
                body = render_roles(get("declarations"), get("roles"),
                                    get("collections"), params)
            elif path == "/roles/new":
                body = render_roles_new(get("declarations"))
            elif path == "/role":
                rid = (qs.get("id") or [""])[0]
                r = next((x for x in get("roles") if x["id"] == rid), None)
                body = (render_role(r, get("declarations"), get("collections"))
                        if r else card("Diese Rolle gibt es nicht."))
            elif path == "/collections":
                body = render_collections(get("roles"), get("collections"), params)
            elif path == "/collections/new":
                body = render_collections_new(get("roles"))
            elif path == "/collection":
                cid = (qs.get("id") or [""])[0]
                c = next((x for x in get("collections") if x["id"] == cid), None)
                body = (render_collection(c, get("roles"))
                        if c else card("Dieses Berechtigungspaket gibt es nicht."))
            elif path == "/mappings":
                body = render_mappings(get("mappings"), None, params)
            elif path == "/mappings/new":
                body = render_mappings_new(get("collections"))
            elif path == "/mapping":
                mid = (qs.get("id") or [""])[0]
                mp = next((x for x in get("mappings") if x["id"] == mid), None)
                body = render_mapping(mp) if mp else card("Diese Abbildung gibt es nicht.")
            elif path == "/log":
                body = render_log(get("log"), params)
            else:
                self.send_html(404, page(card("Diese Seite gibt es nicht."), "/"))
                return
        except door.DoorError as exc:
            self.fail(exc, path)
            return
        self.send_html(200, page(body, path, notice=notice))

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/mcp":
            DOOR.serve(self)
            return
        # Den Körper IMMER zuerst lesen: wer ablehnt, ohne ihn zu lesen,
        # lässt auf einer Keep-Alive-Verbindung Reste stehen, und der
        # Browser sieht einen Netzwerkfehler statt der Antwort.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        raw = self.rfile.read(max(0, min(length, 64 * 1024))).decode("utf-8", "replace")
        if not path.startswith("/do/"):
            self.send_html(404, page(card("Das gibt es nicht."), "/"))
            return
        if not self.headers.get("X-OAAP-Roles"):
            self.send_html(403, page(card("Diese Seite braucht eine "
                                          "angemeldete Sitzung."), "/"))
            return
        if not origin_ok(self.headers):
            # eine fremde Seite hat dieses Formular abgeschickt, oder ein
            # Programm ohne Herkunft: nichts wird geändert
            self.send_html(403, page(card("Diese Anfrage kam nicht von "
                                          "dieser Seite und wurde nicht "
                                          "ausgeführt."), "/"))
            return
        form = parse_form(raw)
        back = safe_back(one(form, "back"))
        try:
            done = do_action(path[4:], form, self.person())
        except door.DoorError as exc:
            if exc.denied:
                self.send_html(403, page(denied_card(), "/"))
            else:
                self.send_html(200, page(card(esc(exc.detail)), back.split("?")[0]))
            return
        if not done:
            self.send_html(404, page(card("Diese Aktion gibt es nicht."), "/"))
            return
        sep = "&" if "?" in back else "?"
        self.redirect(back + sep + "ok=" + done)


def main():
    sys.stdout.write(f"Rollen & Rechte {VERSION} auf Port {PORT} -- "
                     f"Verwaltungsschlüssel: "
                     f"{'da' if door.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
