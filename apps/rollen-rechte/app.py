"""OAAP Rollen & Rechte 0.1 -- das Werkzeug des Mandanten-Admins fuer
fachliche Berechtigungen (RFC-0045 A7, oaap.core.authorization 0.3 §2.10).

Eine Seite beantwortet die Frage, die ein Vereinsvorstand wirklich hat:
*wer darf was, in welcher App, und warum.* Dahinter stehen vier Begriffe
der Plattform, hier in der Sprache des Alltags:

- **Rolle**: eine Vorlage, die eine App erklaert hat ("Trainer/in"), mit
  den Werten, die der Mandant einsetzt ("Bereich: News");
- **Rollensammlung**: ein Buendel von Rollen, das man EINER Person gibt;
- **Zuordnung**: Person + Sammlung, mit Gueltigkeit und, wo die Rolle es
  verlangt, einem Kontext ("fuer Mannschaft mB");
- **Gruppe des Anmeldedienstes**: eine Gruppe im Realm des Mandanten, die
  eine Sammlung gibt -- bei JEDER Anmeldung neu gelesen.

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

Nur Standardbibliothek (plus door.py, der Zugang zur Tuer).
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import door

VERSION = "0.1.0"
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
    """[(feld, objekttyp)] die eine Zuordnung dieser Sammlung ausfuellt."""
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

STYLE = """<link rel="stylesheet" href="/platform/theme.css">
<style>
  *{box-sizing:border-box}
  body{font-family:var(--oaap-font-family, system-ui, -apple-system, "Segoe UI", sans-serif);
       font-size:var(--oaap-font-size-base, 15px);margin:0;
       background:var(--oaap-color-bg, #fff);color:var(--oaap-color-text, #1a1d21)}
  main{max-width:62rem;margin:1.6rem auto;padding:0 var(--oaap-space-3, 16px)}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  nav.tabs{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:1.2rem}
  nav.tabs a{padding:.45rem .9rem;border-radius:var(--oaap-radius, 6px);
       border:1px solid var(--oaap-color-border, #d8dbe0);text-decoration:none;
       color:var(--oaap-color-text, #1a1d21);font-size:.9rem}
  nav.tabs a.on{background:var(--oaap-color-primary, #2f6fed);
       color:var(--oaap-color-primary-text, #fff);border-color:transparent}
  .card{background:var(--oaap-color-surface, #f4f5f7);
       border:1px solid var(--oaap-color-border, #d8dbe0);
       border-radius:var(--oaap-radius, 6px);padding:1.4rem;margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .card.good{border-color:#86efac;background:#f0fdf4}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:#fff;color:var(--oaap-color-primary, #2f6fed);white-space:nowrap;
       border:1px solid var(--oaap-color-border, #d8dbe0)}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  button,a.btn{display:inline-block;padding:.5rem 1.1rem;border:0;
       border-radius:var(--oaap-radius, 6px);
       background:var(--oaap-color-primary, #2f6fed);
       color:var(--oaap-color-primary-text, #fff);text-decoration:none;
       font-size:.92rem;cursor:pointer;min-height:40px}
  button.quiet{background:#fff;color:var(--oaap-color-text, #1a1d21);
       border:1px solid var(--oaap-color-border, #d8dbe0)}
  .hint{font-size:.8rem;color:var(--oaap-color-text-muted, #5b616b);margin:0 0 .6rem}
  .muted{color:var(--oaap-color-text-muted, #5b616b);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;
       border-bottom:1px solid var(--oaap-color-border, #d8dbe0);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;
       color:var(--oaap-color-text-muted, #5b616b)}
  code{background:#fff;border-radius:.25rem;padding:.1rem .35rem;
       font-size:.86rem;word-break:break-all}
  form.stack{display:flex;flex-direction:column;gap:.6rem;max-width:30rem}
  form.stack label{font-size:.82rem;color:var(--oaap-color-text-muted, #5b616b)}
  form.inline{display:inline}
  input[type=text],input[type=date],select{padding:.5rem .6rem;
       border-radius:var(--oaap-radius, 6px);
       border:1px solid var(--oaap-color-border, #d8dbe0);font-size:.95rem;
       min-height:40px;width:100%}
  .checks label{display:block;font-size:.92rem;color:inherit;margin:.15rem 0}
  .cols{display:grid;grid-template-columns:1fr 1fr;gap:1.2rem}
  @media (max-width:720px){ .cols{grid-template-columns:1fr} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

BACK_TO_PORTAL = ('href="/" onclick="location.href=location.protocol+\'//\'+'
                  "location.hostname+'/';return false;\"")

TABS = [("/", "Personen"), ("/roles", "Rollen"), ("/collections", "Sammlungen"),
        ("/mappings", "Gruppen"), ("/log", "Protokoll")]

NOTICES = {
    "role-add": "Rolle angelegt.", "role-retire": "Rolle ausgeblendet.",
    "collection-add": "Sammlung angelegt.",
    "collection-retire": "Sammlung ausgeblendet.",
    "assign": "Zugeordnet.", "revoke": "Zuordnung beendet.",
    "map-add": "Gruppe zugeordnet -- wird bei jeder Anmeldung neu gelesen.",
    "map-remove": "Abbildung entfernt; was sie gab, ist beendet.",
}


def page(body, active="/", title="Rollen & Rechte", notice=""):
    tabs = "".join(f'<a href="{p}"{" class=on" if p == active else ""}>{esc(t)}</a>'
                   for p, t in TABS)
    ok = (f'<div class="card good"><p style="margin:0">{esc(notice)}</p></div>'
          if notice else "")
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} -- OAAP</title>
{STYLE}
<header class="oaap-header">
  <span class="oaap-header-title">Rollen &amp; Rechte</span>
  <a class="oaap-header-back" {BACK_TO_PORTAL}>&larr; Portal</a>
</header>
<main><nav class="tabs">{tabs}</nav>{ok}{body}</main>
<footer style="max-width:62rem;margin:2rem auto 1.2rem;padding:0 var(--oaap-space-3, 16px);
  color:var(--oaap-color-text-muted, #5b616b);font-size:.8rem">
  OAAP Rollen &amp; Rechte {VERSION} -- verwaltet die fachlichen Rechte dieses Mandanten
  (RFC-0045). Plattformrollen gehoeren nicht dazu.
</footer>
</html>"""


def card(text, kind="attention", title=""):
    return (f'<div class="card {kind}">' + (f"<h2>{esc(title)}</h2>" if title else "")
            + f"<p style='margin:0'>{text}</p></div>")


def denied_card():
    return card("Dafuer braucht es die Rechte eines Mandanten-Administrators. "
                "Die Plattform hat diese Anfrage nicht zugelassen; hier "
                "wurde nichts angezeigt und nichts geaendert.",
                title="Kein Zugriff")


def hidden(**kw):
    return "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">'
                   for k, v in kw.items())


def post_form(action, fields_html, label, back, cls="stack", quiet=False):
    return (f'<form class="{cls}" method="post" action="{action}">'
            f'{hidden(back=back)}{fields_html}'
            f'<button{" class=quiet" if quiet else ""}>{esc(label)}</button></form>')


def fmt_grant(g):
    fl = "; ".join(f"{esc(k)}: {esc(', '.join(v) if isinstance(v, list) else str(v))}"
                   for k, v in (g.get("fields") or {}).items())
    return (f"<code>{esc(g['object'])}</code> {esc(', '.join(g['activities']))}"
            + (f" <span class='muted'>({fl})</span>" if fl else ""))


# ------------------------------------------------------------- die Seiten

def render_people(users):
    locked = '<span class="badge err">gesperrt</span>'
    rows = "".join(
        f"<tr><td><a href='/person?id={quote(u['id'])}'>{esc(u['username'])}</a></td>"
        f"<td>{esc(u.get('display_name') or '')}</td>"
        f"<td>{'' if u.get('active', True) else locked}</td></tr>"
        for u in users)
    return f"""<div class="card"><h2>Personen dieses Mandanten</h2>
<p class="hint">Rechte gibt man einer Person ueber ihre Benutzer-ID. Konten legt
die Plattform an, nicht diese Seite.</p>
<table><tr><th>Name</th><th>Anzeigename</th><th></th></tr>{rows or
"<tr><td colspan=3 class=muted>Keine Personen.</td></tr>"}</table></div>"""


def _source(a):
    if a.get("source") == "idp":
        return f"Gruppe <code>{esc(a.get('via') or '')}</code> (bei jeder Anmeldung gelesen)"
    return "von Hand"


def render_person(uid, eff, collections, roles, decls, back):
    roles_by_id = {r["id"]: r for r in roles}
    grants = []
    for a in eff.get("apps") or []:
        for g in a["grants"]:
            grants.append(f"<tr><td>{esc(a['app'])}</td><td>{fmt_grant(g)}</td></tr>")
    grants_html = (f"<table><tr><th>App</th><th>darf</th></tr>{''.join(grants)}</table>"
                   if grants else "<p class='muted' style='margin:0'>Im Moment "
                   "nichts -- keine gueltige Zuordnung.</p>")
    rows = []
    for a in eff.get("assignments") or []:
        state = ('<span class="badge ok">gilt</span>' if a["live"] else
                 '<span class="badge">beendet</span>' if a.get("ended") else
                 '<span class="badge">nicht (mehr) gueltig</span>')
        span = f"{esc(a['valid_from'] or '-')} bis {esc(a['valid_to'] or '-')}"
        ctx = "; ".join(f"{esc(k)}: {esc(', '.join(v))}"
                        for k, v in (a.get("context") or {}).items())
        end = (post_form("/do/revoke", hidden(id=a["id"]), "Beenden", back,
                         cls="inline", quiet=True) if a["live"] else "")
        rows.append(
            f"<tr><td><b>{esc(a['collection_name'])}</b><br><span class='muted'>"
            f"{esc(', '.join(a['roles']))}</span></td>"
            f"<td>{ctx or '-'}</td><td>{span}</td><td>{_source(a)}<br>"
            f"<span class='muted'>gegeben von {esc(a['granted_by'])}</span></td>"
            f"<td>{state}</td><td>{end}</td></tr>")
    table = (f"<table><tr><th>Sammlung</th><th>Kontext</th><th>Zeitraum</th>"
             f"<th>Woher</th><th></th><th></th></tr>{''.join(rows)}</table>"
             if rows else "<p class='muted' style='margin:0'>Noch keine Zuordnung.</p>")
    give = []
    for c in collections:
        if c.get("retired"):
            continue
        ctx = "".join(
            f"<label>{esc(fk)}" + (f" (ID eines Objekts vom Typ {esc(typ)})" if typ else "")
            + f"</label><input type='text' name='ctx_{esc(fk)}' required>"
            for fk, typ in context_fields(decls, roles_by_id, c))
        give.append(
            f"<tr><td><b>{esc(c['name'])}</b><br><span class='muted'>"
            f"{esc(', '.join((roles_by_id.get(r) or {}).get('name', '?') for r in c['roles']))}"
            f"</span></td><td>"
            + post_form("/do/assign",
                        hidden(user=uid, collection=c["id"]) + ctx
                        + "<label>von (optional)</label><input type='date' name='from'>"
                        "<label>bis (optional)</label><input type='date' name='to'>",
                        "Zuordnen", back) + "</td></tr>")
    return f"""<div class="card"><h2>{esc(eff.get('username') or uid)} darf zur Zeit</h2>
{grants_html}</div>
<div class="card"><h2>Zuordnungen</h2>{table}</div>
<div class="card"><h2>Eine Sammlung zuordnen</h2>
<p class="hint">Ein Kontext sagt, WOFUER das Recht gilt (zum Beispiel eine
Mannschaft). Er wird als ID des Objekts im Zwilling eingetragen.</p>
<table>{''.join(give) or "<tr><td class=muted>Es gibt noch keine Sammlung -- "
"zuerst unter <a href='/roles'>Rollen</a> und "
"<a href='/collections'>Sammlungen</a> anlegen.</td></tr>"}</table></div>"""


def render_roles(decls, roles, collections, show_all, back):
    held = {}
    for c in collections:
        if not c.get("retired"):
            for rid in c["roles"]:
                held.setdefault(rid, []).append(c["name"])
    rows = []
    for r in roles:
        if r.get("retired") and not show_all:
            continue
        vals = "; ".join(f"{esc(k)} = {esc(', '.join(v))}"
                         for k, v in (r.get("values") or {}).items())
        title = template_of(decls, r["app"], r["template"]).get("title") or r["template"]
        act = ('<span class="badge">ausgeblendet</span>' if r.get("retired") else
               post_form("/do/role-retire", hidden(id=r["id"]), "Ausblenden",
                         back, cls="inline", quiet=True))
        rows.append(f"<tr><td><b>{esc(r['name'])}</b></td><td>{esc(r['app'])} / "
                    f"{esc(title)}</td><td>{vals or '-'}</td>"
                    f"<td>{esc(', '.join(held.get(r['id'], [])) or '-')}</td>"
                    f"<td>{act}</td></tr>")
    forms = []
    for d in decls:
        for t in (d.get("declaration") or {}).get("role_templates") or []:
            vf = value_fields(decls, d["app"], t["key"])
            vals = "".join(
                f"<label>{esc(fk)} (mindestens eins)</label><div class='checks'>"
                + "".join(f"<label><input type='checkbox' name='v_{esc(fk)}' "
                          f"value='{esc(v)}'> {esc(v)}</label>" for v in vs)
                + "</div>" for _o, fk, vs in vf)
            forms.append(
                f"<div class='card'><h2>{esc(t.get('title') or t['key'])} "
                f"<span class='badge'>{esc(d['app'])}</span></h2>"
                + post_form("/do/role-add",
                            hidden(app=d["app"], template=t["key"])
                            + "<label>Name der Rolle</label>"
                              "<input type='text' name='name' required maxlength='80'>"
                            + vals, "Rolle anlegen", back) + "</div>")
    all_link = ("" if show_all else "<p class='hint'><a href='/roles?alle=1'>"
                "auch ausgeblendete zeigen</a></p>")
    return f"""<div class="card"><h2>Rollen</h2>
<p class="hint">Eine Rolle ist eine Vorlage einer App mit den Werten, die dieser
Mandant einsetzt. Sie gibt nichts, bis eine Sammlung mit ihr einer Person
zugeordnet ist.</p>
<table><tr><th>Name</th><th>Vorlage</th><th>Werte</th><th>in Sammlungen</th><th></th></tr>
{''.join(rows) or "<tr><td colspan=5 class=muted>Noch keine Rolle.</td></tr>"}</table>
{all_link}</div>
<h2 style="margin:1.4rem 0 .8rem">Neue Rolle aus einer Vorlage</h2>
{''.join(forms) or card("Keine App dieses Mandanten hat Rechte erklaert.")}"""


def render_collections(roles, collections, show_all, back):
    names = {r["id"]: r for r in roles}
    rows = []
    for c in collections:
        if c.get("retired") and not show_all:
            continue
        act = ('<span class="badge">ausgeblendet</span>' if c.get("retired") else
               post_form("/do/collection-retire", hidden(id=c["id"]),
                         "Ausblenden", back, cls="inline", quiet=True))
        rows.append(f"<tr><td><b>{esc(c['name'])}</b></td><td>"
                    f"{esc(', '.join((names.get(r) or {}).get('name', '?') for r in c['roles']))}"
                    f"</td><td>{act}</td></tr>")
    pick = "".join(f"<label><input type='checkbox' name='roles' value='{esc(r['id'])}'> "
                   f"{esc(r['name'])} <span class='muted'>({esc(r['app'])})</span></label>"
                   for r in roles if not r.get("retired"))
    all_link = ("" if show_all else "<p class='hint'><a href='/collections?alle=1'>"
                "auch ausgeblendete zeigen</a></p>")
    return f"""<div class="card"><h2>Rollensammlungen</h2>
<p class="hint">Eine Sammlung buendelt Rollen -- auch ueber mehrere Apps. Nur
Sammlungen werden Personen zugeordnet. Ausblenden loescht nichts und geht
nur, wenn keine gueltige Zuordnung und keine Gruppe mehr darauf steht.</p>
<table><tr><th>Name</th><th>Rollen</th><th></th></tr>
{''.join(rows) or "<tr><td colspan=3 class=muted>Noch keine Sammlung.</td></tr>"}</table>
{all_link}</div>
<div class="card"><h2>Neue Sammlung</h2>
{post_form("/do/collection-add",
           "<label>Name</label><input type='text' name='name' required maxlength='80'>"
           "<label>Rollen</label><div class='checks'>"
           + (pick or "<span class='muted'>Zuerst eine Rolle anlegen.</span>")
           + "</div>", "Sammlung anlegen", back)}</div>"""


def render_mappings(mappings, collections, back):
    rows = "".join(
        f"<tr><td><code>{esc(m['group'])}</code></td><td>{esc(m.get('collection_name') or '')}"
        f"</td><td>"
        + post_form("/do/map-remove", hidden(id=m["id"]), "Entfernen", back,
                    cls="inline", quiet=True) + "</td></tr>" for m in mappings)
    opts = "".join(f"<option value='{esc(c['id'])}'>{esc(c['name'])}</option>"
                   for c in collections if not c.get("retired"))
    return f"""<div class="card"><h2>Gruppen des Anmeldedienstes</h2>
<p class="hint">Eine Gruppe im Realm dieses Mandanten kann eine Sammlung geben.
Das Token traegt den <b>Pfad</b> der Gruppe (<code>Verein/Hallenwart</code>);
eine umbenannte Gruppe gibt deshalb nichts mehr. Gelesen wird bei jeder
Anmeldung -- eine Aenderung wirkt also beim naechsten Login der Person, nicht
sofort. Entfernt man die Abbildung, endet sofort, was sie gab. Sammlungen mit
Kontext lassen sich nicht ueber eine Gruppe geben.</p>
<table><tr><th>Gruppe</th><th>gibt Sammlung</th><th></th></tr>
{rows or "<tr><td colspan=3 class=muted>Noch keine Abbildung.</td></tr>"}</table></div>
<div class="card"><h2>Neue Abbildung</h2>
{post_form("/do/map-add",
           "<label>Pfad der Gruppe</label><input type='text' name='group' "
           "required placeholder='Verein/Hallenwart'>"
           "<label>Sammlung</label><select name='collection'>" + opts + "</select>",
           "Zuordnen", back)}</div>"""


def render_log(rows):
    body = "".join(
        f"<tr><td>{esc(e.get('when', '')[:19].replace('T', ' '))}</td>"
        f"<td>{esc(e.get('who', ''))}</td><td><code>{esc(e.get('action', ''))}</code></td>"
        f"<td>{esc(str(e.get('subject', '')))}</td>"
        f"<td class='muted'>{esc(e.get('detail', ''))}</td></tr>" for e in rows)
    return f"""<div class="card"><h2>Protokoll der Rechte</h2>
<p class="hint">Die letzten Aenderungen an Rollen, Sammlungen, Zuordnungen und
Abbildungen dieses Mandanten -- mit der Person, die sie gemacht hat.</p>
<table><tr><th>Wann (UTC)</th><th>Wer</th><th>Was</th><th>Betrifft</th><th>Einzelheiten</th></tr>
{body or "<tr><td colspan=5 class=muted>Noch nichts.</td></tr>"}</table></div>"""


# --------------------------------------------------------------- Server

def do_action(action, form, person):
    """Eine aendernde Aktion; wirft door.DoorError. Gibt (Kennung, Ziel)."""
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
        if not self.headers.get("X-OAAP-Roles"):
            self.send_html(403, page(card("Diese Seite braucht eine angemeldete "
                                          "Sitzung."), path))
            return
        who = self.person()
        notice = NOTICES.get((qs.get("ok") or [""])[0], "")
        back = path + ("?" + parsed.query if parsed.query and "ok=" not in parsed.query else "")
        show_all = (qs.get("alle") or [""])[0] == "1"
        try:
            if path == "/":
                body = render_people(door.call("GET", "users", who)["users"])
            elif path == "/person":
                uid = (qs.get("id") or [""])[0]
                eff = door.call("GET", "effective", who, params={"user": uid})
                cols = door.call("GET", "collections", who)["collections"]
                roles = door.call("GET", "roles", who)["roles"]
                decls = door.call("GET", "declarations", who)["declarations"]
                body = render_person(uid, eff, cols, roles, decls,
                                     "/person?id=" + quote(uid))
            elif path == "/roles":
                body = render_roles(
                    door.call("GET", "declarations", who)["declarations"],
                    door.call("GET", "roles", who)["roles"],
                    door.call("GET", "collections", who)["collections"],
                    show_all, back)
            elif path == "/collections":
                body = render_collections(
                    door.call("GET", "roles", who)["roles"],
                    door.call("GET", "collections", who)["collections"],
                    show_all, back)
            elif path == "/mappings":
                body = render_mappings(
                    door.call("GET", "mappings", who)["mappings"],
                    door.call("GET", "collections", who)["collections"], back)
            elif path == "/log":
                body = render_log(door.call("GET", "log", who)["log"])
            else:
                self.send_html(404, page(card("Diese Seite gibt es nicht."), "/"))
                return
        except door.DoorError as exc:
            self.fail(exc, path)
            return
        self.send_html(200, page(body, path if path != "/person" else "/",
                                 notice=notice))

    def do_POST(self):
        path = urlparse(self.path).path
        # Den Koerper IMMER zuerst lesen: wer ablehnt, ohne ihn zu lesen,
        # laesst auf einer Keep-Alive-Verbindung Reste stehen, und der
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
            # Programm ohne Herkunft: nichts wird geaendert
            self.send_html(403, page(card("Diese Anfrage kam nicht von "
                                          "dieser Seite und wurde nicht "
                                          "ausgefuehrt."), "/"))
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
    sys.stdout.write(f"OAAP Rollen & Rechte {VERSION} auf Port {PORT} -- "
                     f"Verwaltungsschluessel: "
                     f"{'da' if door.configured() else 'FEHLT'}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
