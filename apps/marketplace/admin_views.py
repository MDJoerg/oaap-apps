"""Die Verwaltung des Marktplatzes im Rahmen der Plattform (RFC-0118 §4-§7).

Das Schaufenster (views.py) behält sein eigenes Gesicht -- Sprache, Akzent-
farbe und Name des Betreibers. Hier stehen die Seiten der Werkbank: die
Verwaltung (/admin, Betreiber) und „Meine Assets“ (/my/manage, wer in ein
Asset einstellen darf). Sie tragen den Rahmen (appframe): Kopfleiste,
eine Reiterleiste, Listenberichte, Objektseiten. Eine Tabellenzeile öffnet
ihr Objekt; Handlungen (Freischalten, Entziehen, Löschen …) stehen auf der
Objektseite, nie in der Zeile.

Wer was sieht, entscheidet app.py vor dem Aufruf (access.py); diese Datei
zeigt nur.
"""
import datetime
import json
from urllib.parse import parse_qs, quote

import access
import appframe as af
import db
import views

esc = af.esc

STYLE = """
.mk-icon{width:2.2rem;height:2.2rem;border-radius:var(--oaap-radius);object-fit:cover;vertical-align:middle}
.ls-logo{width:2.2rem;height:2.2rem;border-radius:var(--oaap-radius);object-fit:cover;display:inline-flex;
  align-items:center;justify-content:center;font-weight:700;background:var(--oaap-color-primary);
  color:var(--oaap-color-primary-text)}
.mk-thumbs{display:flex;flex-wrap:wrap;gap:var(--oaap-space-3)}
.mk-thumbs figure{margin:0;text-align:center}
.mk-thumbs img{height:5rem;border-radius:var(--oaap-radius);border:1px solid var(--oaap-color-border);display:block}
.oaap-form textarea{min-height:8rem}
.mk-langs a{margin-right:var(--oaap-space-2)}
.mk-langs a[aria-current]{font-weight:600}
.mk-checks label{display:inline-flex;gap:4px;align-items:center;margin-right:var(--oaap-space-3)}
"""

KINDS = {"user": "Benutzer", "group": "Gruppe"}
MODES = (("anyone", "alle (öffentlich)"), ("signed-in", "Angemeldete"), ("principals", "nur Berechtigte"))
MODE_BADGE = {"anyone": "ok", "signed-in": "info", "principals": "warn"}
REQ_STATES = (("open", "offen"), ("granted", "freigeschaltet"), ("rejected", "abgelehnt"))
REQ_BADGE = {"open": "warn", "granted": "ok", "rejected": "err"}
REV_STATES = (("escalated", "eskaliert"), ("open", "offen"), ("approved", "freigegeben"),
              ("rejected", "abgelehnt"))
REV_BADGE = {"escalated": "err", "open": "warn", "approved": "ok", "rejected": "err", "pending": "warn"}
TYPE_LABEL = {"oaap-app": "OAAP-App", "oaap-profile": "Aufbau-Vorlage", "file": "Datei"}


def _date(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%d.%m.%Y")


def _params(ctx):
    return parse_qs(ctx.query or "")


def _note(ctx):
    p = _params(ctx)
    m = (p.get("m") or [""])[0][:300]
    return af.msg(m, error=bool((p.get("err") or [""])[0]))


def _chans(raw, default="alle"):
    try:
        v = json.loads(raw or "[]")
    except ValueError:
        v = []
    v = [c for c in v if c]
    return ", ".join("alle" if c == "*" else c for c in v) or default


def _kind_select(name="kind"):
    return (f'<select name="{name}"><option value="user">Benutzer</option>'
            '<option value="group">Gruppe (wirkt noch nicht)</option></select>')


def _title(ctx, a):
    return views.tx(ctx, a)["title"]


def _link_row(href, cells):
    first, rest = cells[0], cells[1:]
    return (f'<tr><td><a href="{esc(href)}">{first}</a></td>'
            + "".join(f"<td>{c}</td>" for c in rest) + "</tr>")


def _plain_table(heads, rows, empty_text):
    return af.table("".join(f"<th>{esc(h)}</th>" for h in heads), rows, empty_text)


# ------------------------------------------------------------------ Rahmen ---
def frame(ctx, title, body, active, obj=None, obj_title=None):
    M = ctx.M
    open_n = len(M.requests("open"))
    tabs = [("/admin", "Übersicht"), ("/admin/assets", "Assets", len(M.assets())),
            ("/admin/requests", "Anfragen", open_n or None), ("/admin/rights", "Rechte"),
            ("/admin/reviews", "Prüfungen"), ("/admin/taxonomy", "Landschaften & Kategorien"),
            ("/admin/settings", "Einstellungen")]
    return af.page(title, body, app=ctx.app_name, version=ctx.version, product="marketplace",
                   tabs=tabs, active=active, obj=obj, obj_title=obj_title, style=STYLE)


def partner_frame(ctx, title, body, obj=None, obj_title=None):
    """„Meine Assets“ hat einen Bereich -- keine Reiterleiste (§6.1)."""
    return af.page(title, body, app=ctx.app_name, version=ctx.version, product="marketplace",
                   obj=obj, obj_title=obj_title, style=STYLE)


def _objhead(title, facts, actions=""):
    return (f'<div class="oaap-objhead"><div class="oaap-pagehead"><h1>{esc(title)}</h1>'
            f'<div>{actions}</div></div>{af.facts(facts)}</div>')


# --------------------------------------------------------------- Übersicht ---
def render_admin(ctx, review_on=False, escalate_h=24):
    M = ctx.M
    reqs = M.requests("open")
    esc_n = len(M.reviews("escalated", escalate_after_h=escalate_h))
    open_n = len(M.reviews("open", escalate_after_h=escalate_h))
    amap = {a["id"]: a for a in M.assets()}
    facts = af.facts([
        ("Assets", f'<a href="/admin/assets">{len(amap)}</a>'),
        ("Offene Anfragen", f'<a href="/admin/requests?state=open">{len(reqs)}</a>'),
        ("Prüfung (vier Augen)", af.badge("an", "ok") if review_on else af.badge("aus")),
        ("Eskaliert", f'<a href="/admin/reviews?state=escalated">{esc_n}</a>'),
        ("Offen beim Prüfer", f'<a href="/admin/reviews?state=open">{open_n}</a>'),
    ])
    rows = [_link_row(f"/admin/requests/{r['id']}",
                      [esc(_title(ctx, amap[r["asset"]]) if r["asset"] in amap else r["asset"]),
                       esc(r["principal"]), esc(_chans(r["channels"])), esc(r.get("note") or ""),
                       _date(r["at"])]) for r in reqs[:10]]
    more = (f'<p><a href="/admin/requests?state=open">Alle {len(reqs)} offenen Anfragen</a></p>'
            if len(reqs) > 10 else "")
    rec_rows = [f'<tr><td>{esc(r["principal"])}</td><td>{esc(r["channel"])}</td>'
                f'<td>{_date(r["last_poll"])}</td><td class="oaap-muted">{esc(r["offered"])}</td></tr>'
                for r in M.recipients()]
    body = (_note(ctx)
            + af.pagehead("Verwaltung", '<a class="oaap-btn" href="/my/">Katalog ansehen</a>')
            + af.card("", facts)
            + af.card("Offene Anfragen",
                      _plain_table(("Asset", "Wer", "Kanäle", "Nachricht", "Seit"), rows,
                                   "Keine offenen Anfragen.") + more)
            + af.card("Empfänger",
                      '<p class="oaap-muted">Wer den Feed abruft (z. B. der Paketkatalog eines Knotens): '
                      'angeboten und abgerufen, nicht „installiert“.</p>'
                      + _plain_table(("Wer", "Kanal", "Zuletzt", "Angeboten"), rec_rows,
                                     "Noch hat niemand den Feed abgerufen.")))
    return frame(ctx, "Übersicht", body, "/admin")


# ------------------------------------------------------------------ Assets ---
def _current(M, a):
    return ", ".join(f"{ch} {M.current(a['id'], ch)['version']}" for ch in M.channels
                     if M.current(a["id"], ch))


def _vis_badge(a):
    mode = access.visibility_of(a)["mode"]
    label = dict(MODES)[mode]
    free = sorted(access.free_channels(a))
    return af.badge(label, MODE_BADGE[mode]) + (f' <span class="oaap-muted">frei: {esc(", ".join(free))}</span>'
                                                if free else "")


def render_assets(ctx):
    M = ctx.M
    q = af.Query(_params(ctx), filters=("type", "mode"), default_sort="title")
    rows = [a for a in M.assets()
            if (not q.filters["type"] or a["type"] == q.filters["type"])
            and (not q.filters["mode"] or access.visibility_of(a)["mode"] == q.filters["mode"])]
    columns = [("Titel", "title", lambda a: _title(ctx, a), ""),
               ("Kennung", "id", lambda a: a["id"], ""),
               ("Typ", "type", lambda a: TYPE_LABEL.get(a["type"], a["type"]), ""),
               ("Aktuell", "", None, ""),
               ("Sichtbar", "", None, "")]

    def row(a):
        icon = (f'<img class="mk-icon" src="/my/img/{esc(a["icon"])}" alt=""> ' if a.get("icon") else "")
        feat = " " + af.badge("empfohlen", "info") if a.get("featured") else ""
        return (f'<tr><td>{icon}<a href="/admin/assets/{quote(a["id"])}">{esc(_title(ctx, a))}</a>{feat}</td>'
                f'<td><code>{esc(a["id"])}</code></td><td>{esc(TYPE_LABEL.get(a["type"], a["type"]))}</td>'
                f'<td>{esc(_current(M, a) or "–")}</td><td>{_vis_badge(a)}</td></tr>')
    choices = [("type", "Typ", [(k, v) for k, v in TYPE_LABEL.items()]),
               ("mode", "Sichtbar für", list(MODES))]
    body = (_note(ctx) + af.pagehead("Assets")
            + af.list_report(q, rows, columns, row, base="/admin/assets?", noun="Assets",
                             empty_text="Noch keine Assets. Ein Asset entsteht beim ersten Einchecken "
                                        "(tools/publish.py oder die API).",
                             choices=choices, placeholder="Titel oder Kennung"))
    return frame(ctx, "Assets", body, "/admin/assets")


ASSET_TABS = (("texte", "Texte"), ("merkmale", "Merkmale"), ("bilder", "Bilder"), ("zugang", "Zugang"))


def render_asset(ctx, a, tab="texte", lang="en"):
    M = ctx.M
    tab = tab if tab in dict(ASSET_TABS) else "texte"
    base = f"/admin/assets/{quote(a['id'])}"
    title = _title(ctx, a)
    head = _objhead(title, [("Kennung", f"<code>{esc(a['id'])}</code>"),
                            ("Typ", esc(TYPE_LABEL.get(a["type"], a["type"]))),
                            ("Aktuell", esc(_current(M, a) or "–")),
                            ("Sichtbar", _vis_badge(a))],
                    f'<a class="oaap-btn" href="/my/assets/{quote(a["id"])}">Detailseite ansehen</a>')
    nav = af.tabs([(f"{base}?tab={k}", v) for k, v in ASSET_TABS], f"{base}?tab={tab}")
    if tab == "texte":
        inner = _asset_texts(a, lang, base)
    elif tab == "merkmale":
        inner = _asset_meta(ctx, a, base)
    elif tab == "bilder":
        inner = _asset_images(M, a, base)
    else:
        inner = _asset_access(ctx, a, base)
    body = af.back("/admin/assets", "Zurück zu den Assets") + _note(ctx) + head + nav + inner
    return frame(ctx, title, body, "/admin/assets", obj=f"Asset:{a['id']}", obj_title=title)


def _asset_texts(a, lang, base):
    texts = a.get("texts_map") or {}
    cur = texts.get(lang) or {}
    langs = sorted({"en", "de"} | set(texts) | {lang})
    cur_attr = ' aria-current="page"'
    links = " ".join(
        f'<a href="{base}?tab=texte&amp;lang={c}"{cur_attr if c == lang else ""}>'
        f'{c.upper()}{"" if c in texts else " (leer)"}</a>' for c in langs)
    return af.card("", (
        f'<p class="mk-langs">Sprache: {links} <span class="oaap-muted">· andere Sprache: '
        f'Kürzel an die Adresse hängen, z. B. ?lang=fr</span></p>'
        f'<form class="oaap-form" method="post" action="{base}/texts">'
        f'<input type="hidden" name="lang" value="{esc(lang)}">'
        f'<label>Titel ({esc(lang)})<input type="text" name="title" value="{esc(cur.get("title") or "")}"></label>'
        f'<label>Kurzbeschreibung ({esc(lang)})<input type="text" name="summary" maxlength="300" '
        f'value="{esc(cur.get("summary") or "")}"></label>'
        f'<label>Beschreibung ({esc(lang)}, Markdown)<textarea name="description">'
        f'{esc(cur.get("description") or "")}</textarea></label>'
        '<div><button class="primary">Texte speichern</button> '
        '<span class="oaap-muted">Alle Felder leer = diese Sprache entfernen.</span></div></form>'))


def _asset_meta(ctx, a, base):
    M = ctx.M
    links = "\n".join(f'{x["label"]} | {x["url"]}' for x in a["links_list"])
    ls_boxes = "".join(
        f'<label><input type="checkbox" name="landscapes" value="{esc(x["id"])}"'
        f'{" checked" if x["id"] in a["landscapes_list"] else ""}> {esc(views.label_of(ctx, x)[0])}</label>'
        for x in M.landscapes())
    cat_boxes = "".join(
        f'<label><input type="checkbox" name="categories" value="{esc(c["id"])}"'
        f'{" checked" if c["id"] in a["categories_list"] else ""}> {esc(views.label_of(ctx, c)[0])} '
        f'<span class="oaap-muted">({esc(c["landscape"])})</span></label>'
        for c in M.categories())
    return af.card("", (
        f'<form class="oaap-form" method="post" action="{base}/meta">'
        f'<div><strong>Landschaften</strong><div class="mk-checks">{ls_boxes or "keine"}</div></div>'
        f'<div><strong>Kategorien</strong><div class="mk-checks">'
        f'{cat_boxes or "keine – unter „Landschaften & Kategorien“ anlegen"}</div></div>'
        f'<label style="display:flex;gap:6px"><input type="checkbox" name="featured" value="1"'
        f'{" checked" if a.get("featured") else ""}> Empfohlen (Startseite)</label>'
        f'<label>Schlagworte (kommagetrennt)<input type="text" name="tags" value="{esc(",".join(a["tags_list"]))}"></label>'
        f'<label>Herausgeber<input type="text" name="publisher" value="{esc(a.get("publisher") or "")}"></label>'
        f'<label>Lizenz<input type="text" name="license" value="{esc(a.get("license") or "")}"></label>'
        f'<label>Links (je Zeile: Name | https://…)<textarea name="links" style="min-height:4rem">{esc(links)}</textarea></label>'
        '<div><button class="primary">Speichern</button></div></form>'))


def _asset_images(M, a, base):
    figs = "".join(
        f'<figure><img src="/my/img/{i["sha256"]}" alt=""><figcaption class="oaap-muted">'
        f'{"Icon" if i["role"] == "icon" else "Screenshot"}</figcaption>'
        f'<form method="post" action="{base}/image-remove"><input type="hidden" name="sha" value="{i["sha256"]}">'
        '<button class="danger">Entfernen</button></form></figure>'
        for i in M.images(a["id"]))
    return (af.card("Bilder", f'<div class="mk-thumbs">{figs}</div>' if figs else af.empty(
                "Noch keine Bilder. Unten ein Icon oder Screenshots hochladen."))
            + af.card("Bild hochladen", (
                f'<form class="oaap-form" method="post" action="{base}/image" enctype="multipart/form-data">'
                '<label>Datei<input type="file" name="file" accept="image/png,image/jpeg,image/webp" required></label>'
                '<label>Rolle<select name="role"><option value="screenshot">Screenshot</option>'
                '<option value="icon">Icon</option></select></label>'
                '<div><button class="primary">Hochladen</button></div>'
                '<p class="oaap-muted">PNG, JPEG oder WebP, höchstens 5 MB, bis zu 8 Screenshots. Kein SVG. '
                'Metadaten (EXIF, GPS …) werden entfernt.</p></form>')))


def _asset_access(ctx, a, base):
    M = ctx.M
    vis = access.visibility_of(a)
    back = f"{base}?tab=zugang"
    opts = "".join(f'<option value="{m}"{" selected" if m == vis["mode"] else ""}>{esc(t)}</option>'
                   for m, t in MODES)
    vis_form = af.card("Sichtbarkeit", (
        '<form class="oaap-form" method="post" action="/admin/asset">'
        f'<input type="hidden" name="asset" value="{esc(a["id"])}"><input type="hidden" name="back" value="{esc(back)}">'
        f'<label>Sichtbar für<select name="mode">{opts}</select></label>'
        f'<label>Freie Kanäle (kommagetrennt, z. B. stable)<input type="text" name="free" '
        f'value="{esc(",".join(sorted(access.free_channels(a))))}"></label>'
        '<div><button class="primary">Setzen</button></div>'
        '<p class="oaap-muted">Ein freier Kanal lädt jeder, der das Asset sieht. Sonst braucht es eine '
        'Berechtigung.</p></form>'))
    ents = M.entitlements(a["id"])
    e_rows = [_link_row(f"/admin/grants/{e['id']}",
                        [f'{esc(KINDS.get(e["principal_kind"], e["principal_kind"]))}: {esc(e["principal"])}',
                         esc(_chans(e["channels"])), esc(e.get("note") or "")]) for e in ents]
    grant = (
        '<form class="oaap-form" method="post" action="/admin/grant">'
        f'<input type="hidden" name="asset" value="{esc(a["id"])}"><input type="hidden" name="back" value="{esc(back)}">'
        f'<label>Art{_kind_select()}</label>'
        '<label>Benutzer oder Gruppe<input type="text" name="name" required></label>'
        '<label>Kanäle (leer = alle)<input type="text" name="channels"></label>'
        '<div><button class="primary">Berechtigen</button></div></form>')
    reqs = M.requests("open", a["id"])
    r_rows = [_link_row(f"/admin/requests/{r['id']}", [esc(r["principal"]), esc(_chans(r["channels"])),
                                                         esc(r.get("note") or ""), _date(r["at"])])
              for r in reqs]
    return (vis_form
            + af.card("Berechtigungen", _plain_table(("Wer", "Kanäle", "Notiz"), e_rows,
                                                     "Noch niemand ist für dieses Asset berechtigt.") + grant)
            + af.card("Offene Anfragen", _plain_table(("Wer", "Kanäle", "Nachricht", "Seit"), r_rows,
                                                      "Keine offenen Anfragen.")))


# ---------------------------------------------------------------- Anfragen ---
def render_requests(ctx):
    M = ctx.M
    q = af.Query(_params(ctx), filters=("state",), default_sort="-at")
    amap = {a["id"]: a for a in M.assets()}
    rows = M.requests(q.filters["state"] or None)

    def atitle(r):
        return _title(ctx, amap[r["asset"]]) if r["asset"] in amap else r["asset"]
    columns = [("Asset", "asset", atitle, ""), ("Wer", "who", lambda r: r["principal"], ""),
               ("Kanäle", "", None, ""), ("Nachricht", "note", lambda r: r.get("note") or "", ""),
               ("Seit", "at", lambda r: r["at"] or 0, ""), ("Stand", "", None, "")]

    def row(r):
        return _link_row(f"/admin/requests/{r['id']}",
                         [esc(atitle(r)), esc(r["principal"]), esc(_chans(r["channels"])),
                          esc(r.get("note") or ""), _date(r["at"]),
                          af.badge(dict(REQ_STATES).get(r["state"], r["state"]), REQ_BADGE.get(r["state"], ""))])
    body = (_note(ctx) + af.pagehead("Freischalt-Anfragen")
            + af.list_report(q, rows, columns, row, base="/admin/requests?", noun="Anfragen",
                             empty_text="Noch keine Anfragen. Angemeldete fragen auf der Detailseite "
                                        "eines gesperrten Assets an.",
                             choices=[("state", "Stand", list(REQ_STATES))], placeholder="Asset, Person, Nachricht"))
    return frame(ctx, "Anfragen", body, "/admin/requests")


def _request_facts(ctx, r, a, asset_href):
    facts = [("Asset", f'<a href="{asset_href}">{esc(_title(ctx, a) if a else r["asset"])}</a>'),
             ("Wer", esc(r["principal"])), ("Kanäle", esc(_chans(r["channels"]))),
             ("Seit", _date(r["at"])),
             ("Stand", af.badge(dict(REQ_STATES).get(r["state"], r["state"]), REQ_BADGE.get(r["state"], "")))]
    if r.get("decided_by"):
        facts.append(("Entschieden", f'{esc(r["decided_by"])}, {_date(r.get("decided_at"))}'))
    return facts


def _decide_forms(r, action_grant, action_reject, back, grant_action_field=None):
    chans = ",".join(c for c in json.loads(r["channels"] or "[]") if c != "*")
    if grant_action_field:            # Partner: ein Weg, do=grant|reject
        return (af.card("Freischalten", (
                    f'<form class="oaap-form" method="post" action="{action_grant}">'
                    f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="back" value="{esc(back)}">'
                    f'<label>Kanäle<input type="text" name="channels" value="{esc(chans)}"></label>'
                    '<label>Antwort oder Link (optional)<input type="text" name="answer"></label>'
                    '<div><button class="primary" name="do" value="grant">Freischalten</button></div></form>'))
                + af.card("Ablehnen", (
                    f'<form class="oaap-form" method="post" action="{action_grant}">'
                    f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="back" value="{esc(back)}">'
                    '<label>Antwort (sieht die Person auf der Detailseite)<input type="text" name="answer" required></label>'
                    '<div><button class="danger" name="do" value="reject">Ablehnen</button></div></form>')))
    return (af.card("Freischalten", (
                f'<form class="oaap-form" method="post" action="{action_grant}">'
                f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="back" value="{esc(back)}">'
                f'<label>Kanäle (leer = wie angefragt)<input type="text" name="channels" value="{esc(chans)}"></label>'
                '<label>Antwort oder Link (optional)<input type="text" name="answer"></label>'
                '<div><button class="primary">Freischalten</button></div></form>'))
            + af.card("Ablehnen", (
                f'<form class="oaap-form" method="post" action="{action_reject}">'
                f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="back" value="{esc(back)}">'
                '<label>Antwort (nötig; sieht die Person auf der Detailseite)<input type="text" name="answer" required></label>'
                '<div><button class="danger">Ablehnen</button></div></form>')))


def render_request(ctx, r):
    a = ctx.M.asset(r["asset"])
    title = f"Anfrage von {r['principal']}"
    body = (af.back("/admin/requests", "Zurück zu den Anfragen") + _note(ctx)
            + _objhead(title, _request_facts(ctx, r, a, f"/admin/assets/{quote(r['asset'])}?tab=zugang"))
            + af.card("Nachricht", f'<p>{esc(r.get("note") or "–")}</p>'
                      + (f'<p class="oaap-muted">Antwort: {esc(r["answer"])}</p>' if r.get("answer") else "")))
    if r["state"] == "open":
        body += _decide_forms(r, "/admin/request-grant", "/admin/request-reject", "/admin/requests")
    return frame(ctx, title, body, "/admin/requests", obj=f"Anfrage:{r['id']}", obj_title=title)


# ------------------------------------------------------------------- Rechte ---
def render_rights(ctx):
    M = ctx.M
    q = af.Query(_params(ctx), filters=("kind",), default_sort="asset")
    amap = {a["id"]: a for a in M.assets()}
    rows = [e for e in M.entitlements() if not q.filters["kind"] or e["principal_kind"] == q.filters["kind"]]

    def atitle(e):
        return _title(ctx, amap[e["asset"]]) if e["asset"] in amap else e["asset"]
    columns = [("Asset", "asset", atitle, ""), ("Wer", "who", lambda e: e["principal"], ""),
               ("Art", "", None, ""), ("Kanäle", "", None, ""), ("Notiz", "note", lambda e: e.get("note") or "", "")]

    def row(e):
        return _link_row(f"/admin/grants/{e['id']}",
                         [esc(atitle(e)), esc(e["principal"]),
                          esc(KINDS.get(e["principal_kind"], e["principal_kind"])),
                          esc(_chans(e["channels"])), esc(e.get("note") or "")])
    grant = af.card("Berechtigen", (
        '<form class="oaap-form" method="post" action="/admin/grant">'
        '<input type="hidden" name="back" value="/admin/rights">'
        '<label>Asset (Kennung)<input type="text" name="asset" required></label>'
        f'<label>Art{_kind_select()}</label>'
        '<label>Benutzer oder Gruppe<input type="text" name="name" required></label>'
        '<label>Kanäle (leer = alle)<input type="text" name="channels"></label>'
        '<div><button class="primary">Berechtigen</button></div></form>'))
    p_rows = [_link_row(f"/admin/publishers/{r['id']}",
                        [f"<code>{esc(r['asset_pattern'])}</code>",
                         f'{esc(KINDS.get(r["principal_kind"], r["principal_kind"]))}: {esc(r["principal"])}',
                         esc(_chans(r["channels"]))]) for r in M.publishers()]
    pubs = af.card("Veröffentlichen dürfen", (
        '<p class="oaap-muted">Wer in ein Asset einstellen darf, beantwortet auch dessen Anfragen und '
        'vergibt dessen Berechtigungen („Meine Assets“).</p>'
        + _plain_table(("Muster", "Wer", "Kanäle"), p_rows, "Noch darf niemand einstellen außer dem Betreiber.")
        + '<form class="oaap-form" method="post" action="/admin/publisher">'
        '<input type="hidden" name="back" value="/admin/rights">'
        '<label>Muster für die Kennung (z. B. hbvp oder oaap-*)<input type="text" name="pattern" required></label>'
        f'<label>Art{_kind_select()}</label>'
        '<label>Benutzer oder Gruppe<input type="text" name="name" required></label>'
        '<label>Kanäle (leer = alle)<input type="text" name="channels"></label>'
        '<div><button class="primary">Erlauben</button></div></form>'))
    body = (_note(ctx) + af.pagehead("Berechtigungen")
            + af.list_report(q, rows, columns, row, base="/admin/rights?", noun="Berechtigungen",
                             empty_text="Noch keine Berechtigungen. Unten „Berechtigen“ vergibt die erste.",
                             choices=[("kind", "Art", list(KINDS.items()))], placeholder="Asset oder Person")
            + grant + pubs)
    return frame(ctx, "Rechte", body, "/admin/rights")


def render_grant(ctx, e, partner=False):
    a = ctx.M.asset(e["asset"])
    title = f"Berechtigung für {e['principal']}"
    facts = [("Asset", esc(_title(ctx, a) if a else e["asset"])),
             ("Wer", f'{esc(KINDS.get(e["principal_kind"], e["principal_kind"]))}: {esc(e["principal"])}'),
             ("Kanäle", esc(_chans(e["channels"]))), ("Vergeben von", esc(e.get("granted_by") or "")),
             ("Am", _date(e.get("granted_at"))), ("Notiz", esc(e.get("note") or ""))]
    if partner:
        back_href, action = f"/my/manage/assets/{quote(e['asset'])}", "/my/manage/revoke"
    else:
        back_href, action = "/admin/rights", "/admin/revoke"
    form = af.card("Entziehen", (
        f'<form method="post" action="{action}"><input type="hidden" name="id" value="{e["id"]}">'
        f'<input type="hidden" name="back" value="{esc(back_href)}">'
        '<p class="oaap-muted">Danach lädt diese Person oder Gruppe nur noch freie Kanäle.</p>'
        '<button class="danger">Entziehen</button></form>'))
    body = af.back(back_href) + _note(ctx) + _objhead(title, facts) + form
    if partner:
        return partner_frame(ctx, title, body, obj=f"Berechtigung:{e['id']}", obj_title=title)
    return frame(ctx, title, body, "/admin/rights", obj=f"Berechtigung:{e['id']}", obj_title=title)


def render_publisher(ctx, r):
    title = f"Einstellen: {r['asset_pattern']}"
    facts = [("Muster", f"<code>{esc(r['asset_pattern'])}</code>"),
             ("Wer", f'{esc(KINDS.get(r["principal_kind"], r["principal_kind"]))}: {esc(r["principal"])}'),
             ("Kanäle", esc(_chans(r["channels"])))]
    form = af.card("Entfernen", (
        f'<form method="post" action="/admin/publisher-remove"><input type="hidden" name="id" value="{r["id"]}">'
        '<input type="hidden" name="back" value="/admin/rights">'
        '<button class="danger">Recht zum Einstellen entfernen</button></form>'))
    body = af.back("/admin/rights") + _note(ctx) + _objhead(title, facts) + form
    return frame(ctx, title, body, "/admin/rights")


# ---------------------------------------------------------------- Prüfungen ---
def _case_what(r):
    return f'Release {r["version"]}' if r["kind"] == "release" else f'Texte und Bilder ({r.get("detail") or ""})'


def _case_line(r):
    if r.get("summary"):
        return r["summary"]
    return next((f["text"] for f in r["findings_list"]), "")


def render_reviews(ctx, review_on=False, escalate_h=24):
    M = ctx.M
    q = af.Query(_params(ctx), filters=("state",), default_sort="-id")
    rows = M.reviews(q.filters["state"] or None, escalate_after_h=escalate_h)
    amap = {a["id"]: a for a in M.assets()}

    def atitle(r):
        return _title(ctx, amap[r["asset"]]) if r["asset"] in amap else r["asset"]
    columns = [("Fall", "id", lambda r: r["id"], ""), ("Asset", "asset", atitle, ""),
               ("Was", "", None, ""), ("Von", "by", lambda r: r["created_by"] or "", ""),
               ("Stand", "state", lambda r: r["effective"], ""), ("Begründung", "why", _case_line, "")]

    def row(r):
        st = dict(REV_STATES).get(r["effective"], r["effective"])
        return _link_row(f"/admin/reviews/{r['id']}",
                         [f'#{r["id"]}', esc(atitle(r)), esc(_case_what(r)), esc(r["created_by"] or ""),
                          af.badge(st, REV_BADGE.get(r["effective"], "")) + (" über Frist" if r["overdue"] else ""),
                          f'<span class="oaap-muted">{esc(_case_line(r)[:160])}</span>'])
    rv_rows = [_link_row(f"/admin/reviewers/{r['id']}",
                         [f'{esc(KINDS.get(r["principal_kind"], r["principal_kind"]))}: {esc(r["principal"])}'])
               for r in M.reviewers()]
    info = af.card("", (
        f'<p>Prüfung ist <strong>{"an" if review_on else "aus"}</strong>. Vier Augen: Wer einstellt, gibt nicht '
        'selbst frei. Die Prüf-KI arbeitet über die Schnittstelle; hier landet zur Entscheidung, was sie '
        f'eskaliert oder was länger als {escalate_h} Stunden offen ist.</p>'))
    reviewers = af.card("Prüfer", (
        '<p class="oaap-muted">Ein Benutzer oder eine Gruppe – z. B. der Maschinenbenutzer der Prüf-KI.</p>'
        + _plain_table(("Wer",), rv_rows, "Noch kein Prüfer eingetragen.")
        + '<form class="oaap-form" method="post" action="/admin/reviewer">'
        '<input type="hidden" name="back" value="/admin/reviews">'
        f'<label>Art{_kind_select()}</label><label>Benutzer oder Gruppe<input type="text" name="name" required></label>'
        '<div><button class="primary">Hinzufügen</button></div></form>'))
    body = (_note(ctx) + af.pagehead("Prüfungen") + info
            + af.list_report(q, rows, columns, row, base="/admin/reviews?", noun="Fälle",
                             empty_text="Noch keine Fälle. Mit eingeschalteter Prüfung entsteht je neuem "
                                        "Release ein Fall.",
                             choices=[("state", "Stand", list(REV_STATES))], placeholder="Asset, Person, Begründung")
            + reviewers)
    return frame(ctx, "Prüfungen", body, "/admin/reviews")


def render_review(ctx, r):
    a = ctx.M.asset(r["asset"])
    title = f"Fall #{r['id']}"
    st = dict(REV_STATES).get(r["effective"], r["effective"])
    facts = [("Asset", f'<a href="/admin/assets/{quote(r["asset"])}">{esc(_title(ctx, a) if a else r["asset"])}</a>'),
             ("Was", esc(_case_what(r))), ("Von", esc(r["created_by"] or "")), ("Seit", _date(r["created_at"])),
             ("Stand", af.badge(st, REV_BADGE.get(r["effective"], "")) + (" über Frist" if r["overdue"] else "")),
             ("Geheimnis-Sperre", esc(r["precheck_map"].get("gate") or "–"))]
    if r.get("decided_by"):
        facts.append(("Entschieden", f'{esc(r["decided_by"])}: {esc(r.get("summary") or "")}'))
    finds = "".join(f'<li>{af.badge(f["severity"], "err" if f["severity"] == "block" else "warn")} '
                    f'{esc(f["text"])}</li>' for f in r["findings_list"])
    body = (af.back("/admin/reviews", "Zurück zu den Prüfungen") + _note(ctx) + _objhead(title, facts)
            + af.card("Befunde", f"<ul>{finds}</ul>" if finds else af.empty("Keine Befunde.")))
    if r["effective"] in ("open", "escalated"):
        own = (' <span class="oaap-muted">Eigener Fall – wird so vermerkt.</span>'
               if r["created_by"] == ctx.p["user"] else "")
        body += af.card("Entscheiden", (
            '<form class="oaap-form" method="post" action="/admin/review">'
            f'<input type="hidden" name="id" value="{r["id"]}"><input type="hidden" name="back" value="/admin/reviews">'
            '<label>Begründung<input type="text" name="summary"></label>'
            '<div><button class="primary" name="verdict" value="approve">Freigeben</button> '
            f'<button class="danger" name="verdict" value="reject">Ablehnen</button>{own}</div></form>'))
    return frame(ctx, title, body, "/admin/reviews", obj=f"Prüfung:{r['id']}", obj_title=title)


def render_reviewer(ctx, r):
    title = f"Prüfer {r['principal']}"
    body = (af.back("/admin/reviews", "Zurück zu den Prüfungen") + _note(ctx)
            + _objhead(title, [("Wer", f'{esc(KINDS.get(r["principal_kind"], r["principal_kind"]))}: '
                                       f'{esc(r["principal"])}')])
            + af.card("Entfernen", (
                f'<form method="post" action="/admin/reviewer-remove"><input type="hidden" name="id" value="{r["id"]}">'
                '<input type="hidden" name="back" value="/admin/reviews">'
                '<button class="danger">Als Prüfer entfernen</button></form>')))
    return frame(ctx, title, body, "/admin/reviews")


# --------------------------------------------------- Landschaften, Kategorien ---
def _texts_inputs(texts):
    out = []
    for code in ("en", "de"):
        cur = (texts or {}).get(code) or {}
        out.append(f'<label>Titel {code.upper()}<input type="text" name="title_{code}" '
                   f'value="{esc(cur.get("title") or "")}"></label>'
                   f'<label>Beschreibung {code.upper()}<input type="text" name="description_{code}" '
                   f'value="{esc(cur.get("description") or "")}"></label>')
    return "".join(out)


def render_taxonomy(ctx):
    M = ctx.M
    ls_rows = [f'<tr><td>{views.ls_logo(x, views.label_of(ctx, x)[0])}</td>'
               f'<td><a href="/admin/landscapes/{quote(x["id"])}">{esc(views.label_of(ctx, x)[0])}</a>'
               f'{" " + af.badge("fest") if x["fixed"] else ""}</td>'
               f'<td><code>{esc(x["id"])}</code></td><td>{x["sort"]}</td></tr>' for x in M.landscapes()]
    cat_rows = [f'<tr><td>{views.ls_logo(c, views.label_of(ctx, c)[0])}</td>'
                f'<td><a href="/admin/categories/{quote(c["id"])}">{esc(views.label_of(ctx, c)[0])}</a></td>'
                f'<td><code>{esc(c["id"])}</code></td><td>{esc(c["landscape"])}</td><td>{c["sort"]}</td></tr>'
                for c in M.categories()]
    ls_opts = (f'<option value="{db.DEFAULT_LANDSCAPE}">default (übergreifend)</option>'
               + "".join(f'<option value="{esc(x["id"])}">{esc(views.label_of(ctx, x)[0])}</option>'
                         for x in M.landscapes()))
    body = (_note(ctx) + af.pagehead("Landschaften & Kategorien")
            + af.card("Landschaften", (
                '<p class="oaap-muted">Ziel-Landschaften wie OAAP oder BDT. <code>oaap</code> ist fest. '
                'Titel und Beschreibung je Sprache (Englisch ist die Basis).</p>'
                + af.table("<th></th><th>Titel</th><th>Kennung</th><th>Reihenfolge</th>", ls_rows,
                           "Noch keine Landschaften.")
                + '<h3>Neue Landschaft</h3><form class="oaap-form" method="post" action="/admin/landscape">'
                '<label>Kennung (z. B. bdt)<input type="text" name="id" required></label>'
                f'{_texts_inputs({})}<div><button class="primary">Anlegen</button></div></form>'))
            + af.card("Kategorien", (
                '<p class="oaap-muted">Eine Kategorie gehört zu einer Landschaft – oder zu <code>default</code>, '
                'dann gilt sie übergreifend unter jeder Landschaft.</p>'
                + af.table("<th></th><th>Titel</th><th>Kennung</th><th>Landschaft</th><th>Reihenfolge</th>",
                           cat_rows, "Noch keine Kategorien.")
                + '<h3>Neue Kategorie</h3><form class="oaap-form" method="post" action="/admin/category">'
                '<label>Kennung (z. B. setup-templates)<input type="text" name="id" required></label>'
                f'<label>Landschaft<select name="landscape">{ls_opts}</select></label>'
                f'{_texts_inputs({})}<div><button class="primary">Anlegen</button></div></form>')))
    return frame(ctx, "Landschaften & Kategorien", body, "/admin/taxonomy")


def render_taxon(ctx, table, row):
    """Objektseite einer Landschaft (table='landscape') oder Kategorie."""
    M = ctx.M
    title = views.label_of(ctx, row)[0]
    back = f"/admin/{'landscapes' if table == 'landscape' else 'categories'}/{quote(row['id'])}"
    extra = ""
    if table == "category":
        sel = "".join(f'<option value="{esc(o)}"{" selected" if o == row["landscape"] else ""}>{esc(o)}</option>'
                      for o in [db.DEFAULT_LANDSCAPE] + [x["id"] for x in M.landscapes()])
        extra = f'<label>Landschaft<select name="landscape">{sel}</select></label>'
    facts = [("Kennung", f"<code>{esc(row['id'])}</code>"), ("Logo", views.ls_logo(row, title))]
    if table == "category":
        facts.append(("Landschaft", esc(row["landscape"])))
    edit = af.card("Bearbeiten", (
        f'<form class="oaap-form" method="post" action="/admin/{table}">'
        f'<input type="hidden" name="id" value="{esc(row["id"])}"><input type="hidden" name="back" value="{esc(back)}">'
        f'{extra}{_texts_inputs(row["texts_map"])}'
        f'<label>Reihenfolge<input type="number" name="sort" value="{row["sort"]}"></label>'
        '<div><button class="primary">Speichern</button></div></form>'))
    logo = af.card("Logo", (
        f'<form class="oaap-form" method="post" action="/admin/{table}-logo/{quote(row["id"])}" '
        'enctype="multipart/form-data">'
        '<label>Bild (PNG, JPEG oder WebP)<input type="file" name="file" accept="image/png,image/jpeg,image/webp" required></label>'
        '<div><button class="primary">Logo hochladen</button></div></form>'))
    delete = ""
    if not row.get("fixed"):
        delete = af.card("Löschen", (
            f'<form method="post" action="/admin/{table}-remove"><input type="hidden" name="id" value="{esc(row["id"])}">'
            f'<button class="danger">{"Landschaft" if table == "landscape" else "Kategorie"} löschen</button></form>'))
    body = (af.back("/admin/taxonomy", "Zurück zu Landschaften & Kategorien") + _note(ctx)
            + _objhead(title, facts) + edit + logo + delete)
    return frame(ctx, title, body, "/admin/taxonomy")


# ----------------------------------------------------------- Einstellungen ---
def render_settings(ctx):
    s = ctx.settings
    rows = [r for r in (s.get("rows") or ",".join(views.ROWS)).split(",") if r in views.ROWS]
    labels = {"featured": "Empfohlen (hervorgehobene Assets)", "landscapes": "Landschaften",
              "new": "Neu", "popular": "Meistgeladen (90 Tage, ohne Feed-Abrufe)"}
    logo = s.get("logo") or ""
    lang_opts = "".join(f'<option{" selected" if (s.get("default_lang") or "en") == c else ""}>{c}</option>'
                        for c in views.i18n.UI_LANGS)
    body = (_note(ctx) + af.pagehead("Einstellungen")
            + af.card("Schaufenster", (
                '<form class="oaap-form" method="post" action="/admin/settings">'
                f'<label>Akzentfarbe der öffentlichen Seiten<input type="color" name="accent" '
                f'value="{esc(s.get("accent") or "#2563eb")}"></label>'
                '<label style="display:flex;gap:6px"><input type="checkbox" name="accent_reset" value="1"> '
                'Standardfarbe verwenden</label>'
                f'<label>Startseite: Reihen in dieser Reihenfolge (kommagetrennt)<input type="text" name="rows" '
                f'value="{esc(",".join(rows))}"></label>'
                + "".join(f'<span class="oaap-muted">{k} = {esc(v)}</span>' for k, v in labels.items())
                + f'<label>Sprache, wenn der Browser nichts sagt<select name="default_lang">{lang_opts}</select></label>'
                '<div><button class="primary">Speichern</button></div></form>'))
            + af.card("Logo", (
                (f'<p><img src="/logo/{esc(logo)}" style="height:3rem" alt=""></p>' if views.db_sha(logo)
                 else '<p class="oaap-muted">Noch kein Logo.</p>')
                + '<form class="oaap-form" method="post" action="/admin/settings-logo" enctype="multipart/form-data">'
                '<label>Bild<input type="file" name="file" accept="image/png,image/jpeg,image/webp" required></label>'
                '<div><button class="primary">Hochladen</button></div>'
                '<p class="oaap-muted">PNG, JPEG oder WebP; Metadaten werden entfernt.</p></form>')))
    return frame(ctx, "Einstellungen", body, "/admin/settings")


# ------------------------------------------------------- Meine Assets (Partner) ---
def render_manage(ctx, mine):
    """Partner light: offene Anfragen und die eigenen Assets. mine = Assets,
    in die diese Person einstellen darf (app.py hat geprüft)."""
    M = ctx.M
    ids = {a["id"] for a in mine}
    amap = {a["id"]: a for a in mine}
    q = af.Query(_params(ctx), default_sort="-at")
    reqs = [r for r in M.requests("open") if r["asset"] in ids]
    columns = [("Asset", "asset", lambda r: _title(ctx, amap[r["asset"]]), ""),
               ("Wer", "who", lambda r: r["principal"], ""), ("Kanäle", "", None, ""),
               ("Nachricht", "note", lambda r: r.get("note") or "", ""), ("Seit", "at", lambda r: r["at"] or 0, "")]

    def row(r):
        return _link_row(f"/my/manage/requests/{r['id']}",
                         [esc(_title(ctx, amap[r["asset"]])), esc(r["principal"]), esc(_chans(r["channels"])),
                          esc(r.get("note") or ""), _date(r["at"])])
    a_rows = [_link_row(f"/my/manage/assets/{quote(a['id'])}",
                        [esc(_title(ctx, a)), f"<code>{esc(a['id'])}</code>",
                         str(len(M.entitlements(a["id"])))]) for a in mine]
    body = (_note(ctx)
            + af.pagehead("Meine Assets", '<a class="oaap-btn" href="/my/">Katalog ansehen</a>')
            + '<p class="oaap-muted">Anfragen beantworten und Berechtigungen vergeben – nur für die Assets, '
              'in die du einstellen darfst. Die Sichtbarkeit setzt der Betreiber.</p>'
            + af.card("Offene Anfragen", af.list_report(
                q, reqs, columns, row, base="/my/manage?", noun="Anfragen",
                empty_text="Keine offenen Anfragen.", search=bool(reqs)))
            + af.card("Assets", _plain_table(("Titel", "Kennung", "Berechtigungen"), a_rows,
                                             "Du darfst in kein Asset einstellen.")))
    return partner_frame(ctx, "Meine Assets", body)


def render_manage_asset(ctx, a):
    M = ctx.M
    title = _title(ctx, a)
    back = f"/my/manage/assets/{quote(a['id'])}"
    e_rows = [_link_row(f"/my/manage/grants/{e['id']}",
                        [f'{esc(KINDS.get(e["principal_kind"], e["principal_kind"]))}: {esc(e["principal"])}',
                         esc(_chans(e["channels"])), esc(e.get("note") or "")]) for e in M.entitlements(a["id"])]
    r_rows = [_link_row(f"/my/manage/requests/{r['id']}",
                        [esc(r["principal"]), esc(_chans(r["channels"])), esc(r.get("note") or ""), _date(r["at"])])
              for r in M.requests("open", a["id"])]
    body = (af.back("/my/manage", "Zurück zu meinen Assets") + _note(ctx)
            + _objhead(title, [("Kennung", f"<code>{esc(a['id'])}</code>"), ("Aktuell", esc(_current(M, a) or "–"))],
                       f'<a class="oaap-btn" href="/my/assets/{quote(a["id"])}">Detailseite ansehen</a>')
            + af.card("Offene Anfragen", _plain_table(("Wer", "Kanäle", "Nachricht", "Seit"), r_rows,
                                                      "Keine offenen Anfragen."))
            + af.card("Berechtigungen", _plain_table(("Wer", "Kanäle", "Notiz"), e_rows,
                                                     "Noch niemand ist berechtigt.")
                      + '<form class="oaap-form" method="post" action="/my/manage/grant">'
                      f'<input type="hidden" name="asset" value="{esc(a["id"])}">'
                      f'<input type="hidden" name="back" value="{esc(back)}">'
                      f'<label>Art{_kind_select()}</label>'
                      '<label>Benutzer oder Gruppe<input type="text" name="name" required></label>'
                      '<label>Kanäle (leer = deine Kanäle)<input type="text" name="channels"></label>'
                      '<div><button class="primary">Berechtigen</button></div></form>'))
    return partner_frame(ctx, title, body, obj=f"Asset:{a['id']}", obj_title=title)


def render_manage_request(ctx, r):
    a = ctx.M.asset(r["asset"])
    title = f"Anfrage von {r['principal']}"
    back = f"/my/manage/assets/{quote(r['asset'])}"
    body = (af.back("/my/manage", "Zurück zu meinen Assets") + _note(ctx)
            + _objhead(title, _request_facts(ctx, r, a, back))
            + af.card("Nachricht", f'<p>{esc(r.get("note") or "–")}</p>'))
    if r["state"] == "open":
        body += _decide_forms(r, "/my/manage/request", None, "/my/manage", grant_action_field=True)
    return partner_frame(ctx, title, body, obj=f"Anfrage:{r['id']}", obj_title=title)
