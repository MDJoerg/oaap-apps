"""Das Schaufenster (RFC-0077 §8, Stufe 2): Suche mit Karten und Facetten,
Detailseite mit Bildern, Beschreibung, Versionen und Verlauf, Freischalt-Anfragen,
Verwaltung.

Jede Liste und jede Zahl entsteht NACH `access.can_see` (§5): ein verborgenes
Asset ändert keine Facette, keinen Zähler, keine Trefferliste. Markdown geht
durch `render.py` (erst escapen, dann wenige Formen).
"""
import datetime
import html
import json
from urllib.parse import quote, urlencode

import access
import render

esc = html.escape

STYLE = """<style>
  :root{--blue:#1e3a8a;--blue-2:#2563eb;--bg:#f4f6fa;--text:#1f2937;--muted:#6b7280;--card:#fff;
        --border:#e5e7eb;--ok:#15803d;--err:#b91c1c;--chip:#dbeafe;--chip-t:#1e3a8a;--tile:#e0e7ff}
  @media (prefers-color-scheme: dark){:root{--bg:#0b1220;--text:#e5e7eb;--muted:#94a3b8;
        --card:#111827;--border:#1f2937;--chip:#1e3a8a;--chip-t:#dbeafe;--tile:#1e293b}}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.45}
  header{background:var(--blue);color:#fff;padding:.7rem 1.2rem;display:flex;gap:1rem;
         justify-content:space-between;align-items:center}
  header .name{font-weight:600} header a{color:#fff;font-size:.9rem}
  main{max-width:72rem;margin:1.2rem auto;padding:0 1rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:.7rem;padding:1.1rem;margin-bottom:1rem}
  .layout{display:grid;grid-template-columns:14rem 1fr;gap:1.2rem}
  @media (max-width: 760px){.layout{grid-template-columns:1fr}}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(16rem,1fr));gap:1rem}
  a.tilecard{display:flex;gap:.8rem;text-decoration:none;color:inherit;min-height:7.5rem}
  a.tilecard:hover{border-color:var(--blue-2)}
  .icon{width:3.2rem;height:3.2rem;border-radius:.6rem;flex:none;object-fit:cover;background:var(--tile);
        display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.3rem;color:var(--blue-2)}
  .icon.big{width:4.5rem;height:4.5rem;font-size:1.8rem}
  h1{font-size:1.35rem;margin:.1rem 0 .3rem} h2{font-size:1.02rem;margin:0 0 .6rem} h3{font-size:.95rem;margin:.2rem 0}
  table{border-collapse:collapse;width:100%;font-size:.9rem}
  th,td{text-align:left;padding:.4rem .45rem;border-bottom:1px solid var(--border);vertical-align:top}
  .muted{color:var(--muted)} .ok{color:var(--ok)} .err{color:var(--err)} .small{font-size:.85rem}
  .badge{font-size:.72rem;padding:.12rem .55rem;border-radius:1rem;background:var(--chip);color:var(--chip-t);white-space:nowrap}
  .badge.free{background:#dcfce7;color:#166534} .badge.locked{background:#fef3c7;color:#92400e}
  .badge.mine{background:#e0e7ff;color:#3730a3} .badge.beta{background:#fae8ff;color:#86198f}
  button,.btn{padding:.45rem .9rem;border:0;border-radius:.4rem;background:var(--blue-2);color:#fff;
        cursor:pointer;font-size:.9rem;text-decoration:none;display:inline-block;min-height:2.4rem}
  .btn.quiet,button.quiet{background:transparent;color:var(--blue-2);border:1px solid var(--border)}
  button.danger{background:var(--err)}
  input,select,textarea{padding:.45rem;border:1px solid var(--border);border-radius:.35rem;max-width:100%;
        background:var(--card);color:var(--text);font:inherit}
  textarea{width:100%;min-height:8rem}
  form.inline{display:inline}
  code{background:rgba(127,127,127,.15);padding:.05rem .3rem;border-radius:.2rem}
  .facet a{display:flex;justify-content:space-between;text-decoration:none;color:inherit;padding:.15rem .3rem;border-radius:.3rem}
  .facet a.on{background:var(--chip);color:var(--chip-t);font-weight:600}
  .facet h3{margin-top:.8rem;color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
  .gallery{display:flex;gap:.6rem;overflow-x:auto;padding-bottom:.3rem}
  .gallery img{height:12rem;border-radius:.5rem;border:1px solid var(--border)}
  .md p{margin:.4rem 0} .md pre{overflow-x:auto;background:rgba(127,127,127,.12);padding:.6rem;border-radius:.4rem}
  dl.facts{display:grid;grid-template-columns:auto 1fr;gap:.3rem 1rem;margin:0;font-size:.9rem}
  dl.facts dt{color:var(--muted)} dl.facts dd{margin:0}
  .head{display:flex;gap:1rem;align-items:flex-start}
  .search{display:flex;gap:.5rem;margin-bottom:1rem} .search input{flex:1}
</style>"""


def _date(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%d.%m.%Y")


def page(name, title, body, base, signed, admin=False):
    nav = []
    if not signed:
        nav.append('<a href="/my/">Anmelden</a>')
    else:
        nav.append(f'<a href="{base}/">Übersicht</a>')
        if admin:
            nav.append('<a href="/admin">Verwaltung</a>')
    return ("<!doctype html><html lang=de><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)} – {esc(name)}</title>{STYLE}"
            f"<header><a class=name href='{base}/' style='text-decoration:none;font-size:1rem'>{esc(name)}</a>"
            f"<span style='display:flex;gap:1rem'>{''.join(nav)}</span></header><main>{body}</main></html>")


def icon_html(a, base, big=False):
    cls = "icon big" if big else "icon"
    if a.get("icon"):
        return f'<img class="{cls}" src="{base}/img/{a["icon"]}" alt="">'
    letter = esc((a.get("title") or a["id"])[:1].upper())
    return f'<span class="{cls}" aria-hidden=true>{letter}</span>'


# ------------------------------------------------------------------- state ---
def view(M, p, a, ents):
    """Was eine Karte und die Detailseite über ein Asset sagen."""
    st = access.state(p, a, ents, M.channels)
    per, how = {}, set()
    for ch in M.channels:
        cur = M.current(a["id"], ch)
        if not cur:
            continue
        ok = access.can_obtain(p, a, ch, ents)
        per[ch] = {"release": cur, "obtainable": ok}
        if ok:
            if ch in access.free_channels(a):
                how.add("frei")
            elif access.is_admin(p):
                how.add("Betreiber")
            else:
                how.add("berechtigt")
    latest = next((per[ch]["release"] for ch in M.channels if ch in per), None)
    beta_only = bool(per) and all(ch != M.channels[0] for ch in per)
    return {"state": st, "per": per, "how": how, "latest": latest, "beta_only": beta_only}


def badges(v, channels):
    out = []
    if v["state"] == "locked":
        out.append('<span class="badge locked">freischalten</span>')
    elif "frei" in v["how"]:
        out.append('<span class="badge free">frei</span>')
    elif v["how"]:
        out.append(f'<span class="badge mine">{esc(sorted(v["how"])[0])}</span>')
    if v["beta_only"]:
        out.append(f'<span class="badge beta">{esc(next(iter(v["per"])))}</span>')
    return " ".join(out)


# ------------------------------------------------------------------- search --
FACETS = (("type", "Typ"), ("cat", "Kategorie"), ("tag", "Schlagwort"), ("channel", "Kanal"))


def _facet_values(M, a):
    return {"type": {a["type"]}, "cat": set(a["categories_list"]), "tag": set(a["tags_list"]),
            "channel": {ch for ch in M.channels if M.current(a["id"], ch)}}


def search(M, p, q="", chosen=None):
    """(Treffer, Facetten). Erst sichtbar, dann Suchtext, dann Facetten -- die
    Zähler einer Facette rechnen alle ANDEREN gewählten Facetten mit."""
    chosen = {k: v for k, v in (chosen or {}).items() if v}
    ents_all = M.entitlements()
    hits = M.search_ids(q)
    order = {aid: i for i, aid in enumerate(hits)} if hits is not None else None
    pool = []
    for a in M.assets():
        if order is not None and a["id"] not in order:
            continue
        ents = [e for e in ents_all if e["asset"] == a["id"]]
        if not access.can_see(p, a, ents):
            continue                                     # vor jedem Zählen
        pool.append((a, ents, _facet_values(M, a)))
    if order is not None:
        pool.sort(key=lambda x: order[x[0]["id"]])

    def passes(fv, skip=None):
        return all(v in fv[k] for k, v in chosen.items() if k != skip)

    items = [(a, e) for a, e, fv in pool if passes(fv)]
    facets = {}
    for key, _label in FACETS:
        counts = {}
        for _a, _e, fv in pool:
            if passes(fv, skip=key):
                for v in fv[key]:
                    counts[v] = counts.get(v, 0) + 1
        facets[key] = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return items, facets


def render_list(M, name, p, base, q, chosen):
    items, facets = search(M, p, q, chosen)
    cards = []
    for a, ents in items:
        v = view(M, p, a, ents)
        ver = f'v{esc(v["latest"]["version"])}' if v["latest"] else "noch kein Release"
        pub = f' · {esc(a["publisher"])}' if a.get("publisher") else ""
        cards.append(
            f'<a class="card tilecard" href="{base}/assets/{quote(a["id"])}">{icon_html(a, base)}'
            f'<span><h2 style="margin:0 0 .2rem">{esc(a.get("title") or a["id"])}</h2>'
            f'<span class="muted small">{ver}{pub}</span>'
            f'<p class=small style="margin:.35rem 0">{esc((a.get("summary") or "")[:140])}</p>'
            f'{badges(v, M.channels)}</span></a>')

    def link(key, val):
        nxt = dict(chosen)
        if nxt.get(key) == val:
            nxt.pop(key)
        else:
            nxt[key] = val
        args = {k: v for k, v in dict(nxt, q=q).items() if v}
        return f"{base}/?{urlencode(args)}" if args else f"{base}/"

    side = []
    for key, label in FACETS:
        vals = facets[key]
        if not vals:
            continue
        side.append(f"<h3>{label}</h3>" + "".join(
            f'<a class="{"on" if chosen.get(key) == v else ""}" href="{esc(link(key, v))}">'
            f'<span>{esc(v)}</span><span class=muted>{n}</span></a>' for v, n in vals))
    hidden = "".join(f'<input type=hidden name="{k}" value="{esc(v)}">' for k, v in chosen.items() if v)
    body = (f'<form class=search method=get action="{base}/"><input type=search name=q value="{esc(q)}" '
            f'placeholder="Suchen: Name, Beschreibung, Schlagwort" aria-label="Suchen">{hidden}'
            '<button>Suchen</button></form>'
            f'<div class=layout><aside class="card facet">{"".join(side) or "<p class=muted>Keine Filter.</p>"}</aside>'
            f'<section><p class="muted small" style="margin-top:0">{len(items)} Asset{"s" if len(items) != 1 else ""}</p>'
            f'<div class=grid>{"".join(cards) or "<p class=muted>Nichts gefunden.</p>"}</div></section></div>')
    return page(name, "Übersicht", body, base, not p["anon"], access.is_admin(p))


# ------------------------------------------------------------------- detail --
def render_detail(M, name, p, base, a, ents, msg="", ok=True):
    v = view(M, p, a, ents)
    dl = "/d/" if p["anon"] else "/my/d/"
    actions = []
    for ch, x in v["per"].items():
        if x["obtainable"]:
            f = next((f for f in x["release"]["files"] if f["role"] == "primary"), None)
            if f:
                actions.append(f'<a class=btn href="{dl}{f["sha256"]}">Laden · {esc(ch)} '
                               f'{esc(x["release"]["version"])} ({f["size"] / 1048576:.1f} MB)</a>')
    locked_chs = [ch for ch, x in v["per"].items() if not x["obtainable"]]
    req_box = ""
    if locked_chs or v["state"] == "locked":
        if p["anon"]:
            req_box = (f'<p class=small>Gesperrt. <a href="/my/assets/{quote(a["id"])}">Anmelden</a>, '
                       'um eine Freischaltung anzufragen.</p>')
        else:
            mine = M.requests(asset_id=a["id"], principal=p["user"])
            last = mine[0] if mine else None
            status = ""
            if last and last["state"] == "open":
                status = (f'<p class=small>Deine Anfrage vom {_date(last["at"])} ist offen. '
                          'Eine neue Nachricht ergänzt sie.</p>')
            elif last and last["state"] == "rejected":
                status = (f'<p class="small err">Abgelehnt am {_date(last["decided_at"])}: '
                          f'{esc(last.get("answer") or "")}</p>')
            opts = "".join(f"<option>{esc(ch)}</option>" for ch in (locked_chs or M.channels))
            req_box = (f'<div class=card><h2>Freischaltung anfragen</h2>{status}'
                       f'<form method=post action="/my/assets/{quote(a["id"])}/request">'
                       f'<p><label>Kanal <select name=channel>{opts}</select></label></p>'
                       '<p><textarea name=note placeholder="Wofür brauchst du es? (optional)" '
                       'style="min-height:4rem" maxlength=500></textarea></p>'
                       '<button>Anfragen</button></form></div>')
    shots = "".join(f'<img src="{base}/img/{s}" alt="Screenshot" loading=lazy>' for s in a["screenshots_list"])
    facts = [("Kennung", f"<code>{esc(a['id'])}</code>"), ("Typ", esc(a["type"]))]
    for ch, x in v["per"].items():
        f = next((f for f in x["release"]["files"] if f["role"] == "primary"), None)
        facts.append((f"Kanal {esc(ch)}", f'{esc(x["release"]["version"])} · {_date(x["release"]["created_at"])}'
                      + (f' · {f["size"] / 1048576:.1f} MB' if f else "")))
    if a.get("publisher"):
        facts.append(("Herausgeber", esc(a["publisher"])))
    if a.get("license"):
        facts.append(("Lizenz", esc(a["license"])))
    if a["links_list"]:
        facts.append(("Links", " · ".join(
            f'<a href="{esc(l["url"], quote=True)}" rel="noopener noreferrer" target=_blank>{esc(l["label"])}</a>'
            for l in a["links_list"])))
    if a["tags_list"] or a["categories_list"]:
        facts.append(("Schlagworte", " ".join(f'<span class=badge>{esc(t)}</span>'
                                              for t in a["categories_list"] + a["tags_list"])))
    hist = []
    for r in M.releases(a["id"]):
        if r["yanked_at"] and not access.is_admin(p):
            continue
        can = not r["yanked_at"] and any(access.can_obtain(p, a, ch, ents) for ch in r["channels"])
        files = " ".join(f'<a class="btn quiet" href="{dl}{f["sha256"]}">Laden</a>' for f in r["files"]) if can else ""
        hist.append(f'<tr><td><strong>{esc(r["version"])}</strong><br><span class="muted small">'
                    f'{_date(r["created_at"])}</span>'
                    f'{"<br><span class=err>zurückgerufen: " + esc(r.get("yank_reason") or "") + "</span>" if r["yanked_at"] else ""}'
                    f'</td><td>{" ".join(f"<span class=badge>{esc(c)}</span>" for c in r["channels"]) or "–"}</td>'
                    f'<td class=md>{render.to_html(r.get("notes") or "") or "<span class=muted>–</span>"}</td>'
                    f'<td>{files}</td></tr>')
    note = f'<div class=card><p class="{"ok" if ok else "err"}" style="margin:0">{esc(msg)}</p></div>' if msg else ""
    body = (note +
            f'<div class=card><div class=head>{icon_html(a, base, big=True)}<div style="flex:1">'
            f'<h1>{esc(a.get("title") or a["id"])}</h1>'
            f'<p class=muted style="margin:.1rem 0 .5rem">{esc(a.get("summary") or "")}</p>'
            f'{badges(v, M.channels)}</div></div>'
            f'<p style="margin:.9rem 0 0;display:flex;gap:.5rem;flex-wrap:wrap">{"".join(actions)}</p></div>'
            + (f'<div class=card><div class=gallery>{shots}</div></div>' if shots else "")
            + req_box
            + f'<div class=layout style="grid-template-columns:1fr 20rem">'
              f'<div class="card md"><h2>Beschreibung</h2>'
              f'{render.to_html(a.get("description") or "") or "<p class=muted>Keine Beschreibung.</p>"}</div>'
              f'<div class=card><h2>Fakten</h2><dl class=facts>'
              + "".join(f"<dt>{k}</dt><dd>{val}</dd>" for k, val in facts) + '</dl></div></div>'
            + '<div class=card style="overflow-x:auto"><h2>Versionen</h2><table><tr><th>Version</th>'
              f'<th>Kanäle</th><th>Notizen</th><th></th></tr>{"".join(hist)}</table></div>')
    return page(name, a.get("title") or a["id"], body, base, not p["anon"], access.is_admin(p))


# ------------------------------------------------------------------- admin ---
def mode_options(current):
    return "".join(f'<option{" selected" if m == current else ""}>{m}</option>'
                   for m in ("anyone", "signed-in", "principals"))


def render_admin(M, name, p, msg=""):
    a_rows = []
    for a in M.assets():
        vis = access.visibility_of(a)
        cur = ", ".join(f"{ch} {M.current(a['id'], ch)['version']}" for ch in M.channels
                        if M.current(a["id"], ch))
        a_rows.append(
            f'<tr><td><a href="/admin/assets/{quote(a["id"])}"><code>{esc(a["id"])}</code></a>'
            f'<br><span class="muted small">{esc(a["type"])}</span></td>'
            f'<td>{esc(cur or "–")}</td>'
            f'<td><form class=inline method=post action="/admin/asset">'
            f'<input type=hidden name=asset value="{esc(a["id"])}">'
            f'<select name=mode>{mode_options(vis["mode"])}</select> '
            f'<input type=text name=free value="{esc(",".join(sorted(access.free_channels(a))))}" '
            'placeholder="frei: stable" size=10> <button class=quiet>Setzen</button></form></td></tr>')
    q_rows = []
    for r in M.requests("open"):
        chans = ",".join(json.loads(r["channels"] or '["*"]'))
        q_rows.append(
            f'<tr><td><code>{esc(r["asset"])}</code></td><td>{esc(r["principal"])}</td>'
            f'<td>{esc(chans)}</td><td>{esc(r.get("note") or "")}</td><td>{_date(r["at"])}</td>'
            f'<td><form class=inline method=post action="/admin/request-grant"><input type=hidden name=id '
            f'value="{r["id"]}"><input type=text name=channels value="{esc(chans)}" size=8> '
            f'<button>Freischalten</button></form> '
            f'<form class=inline method=post action="/admin/request-reject"><input type=hidden name=id '
            f'value="{r["id"]}"><input type=text name=answer placeholder="Antwort (nötig)" size=14 required> '
            f'<button class=danger>Ablehnen</button></form></td></tr>')
    e_rows = "".join(
        f'<tr><td><code>{esc(e["asset"])}</code></td><td>{esc(e["principal_kind"])}: '
        f'{esc(e["principal"])}</td><td>{esc(", ".join(json.loads(e["channels"])))}</td>'
        f'<td class="muted small">{esc(e.get("note") or "")}</td>'
        f'<td><form class=inline method=post action="/admin/revoke"><input type=hidden name=id '
        f'value="{e["id"]}"><button class=quiet>Entziehen</button></form></td></tr>' for e in M.entitlements())
    p_rows = "".join(
        f'<tr><td><code>{esc(r["asset_pattern"])}</code></td><td>{esc(r["principal_kind"])}: '
        f'{esc(r["principal"])}</td><td>{esc(", ".join(json.loads(r["channels"])))}</td>'
        f'<td><form class=inline method=post action="/admin/publisher-remove"><input type=hidden '
        f'name=id value="{r["id"]}"><button class=quiet>Entfernen</button></form></td></tr>' for r in M.publishers())
    r_rows = "".join(
        f'<tr><td>{esc(r["principal"])}</td><td>{esc(r["channel"])}</td>'
        f'<td>{_date(r["last_poll"])}</td><td class="muted small">{esc(r["offered"])}</td></tr>'
        for r in M.recipients())
    kind_sel = '<select name=kind><option>user</option><option>group</option></select>'
    note = f'<div class=card><p style="margin:0">{esc(msg)}</p></div>' if msg else ""
    body = (note + '<h1>Verwaltung</h1>'
            f'<div class=card style="overflow-x:auto"><h2>Offene Anfragen ({len(q_rows)})</h2>'
            + (f'<table><tr><th>Asset</th><th>Wer</th><th>Kanäle</th><th>Nachricht</th><th>Seit</th><th></th></tr>{"".join(q_rows)}</table>'
               if q_rows else '<p class=muted>Keine.</p>') + '</div>'
            f'<div class=card style="overflow-x:auto"><h2>Assets</h2><p class="muted small">Name anklicken: '
            'Beschreibung, Schlagworte, Links, Bilder.</p><table><tr><th>Asset</th><th>Aktuell</th>'
            f'<th>Sichtbar · frei</th></tr>{"".join(a_rows)}</table></div>'
            f'<div class=card style="overflow-x:auto"><h2>Berechtigungen</h2><table>{e_rows}</table>'
            '<form method=post action="/admin/grant"><p>'
            f'<input type=text name=asset placeholder=Asset size=14 required> {kind_sel} '
            '<input type=text name=name placeholder="Benutzer/Gruppe" size=16 required> '
            '<input type=text name=channels placeholder="Kanäle (leer = alle)" size=16> '
            '<button>Berechtigen</button></p></form></div>'
            f'<div class=card style="overflow-x:auto"><h2>Veröffentlichen dürfen</h2><table>{p_rows}</table>'
            '<form method=post action="/admin/publisher"><p>'
            f'<input type=text name=pattern placeholder="hbvp oder oaap-*" size=14 required> {kind_sel} '
            '<input type=text name=name placeholder="Benutzer/Gruppe" size=16 required> '
            '<input type=text name=channels placeholder="Kanäle (leer = alle)" size=16> '
            '<button>Erlauben</button></p></form></div>'
            f'<div class=card style="overflow-x:auto"><h2>Empfänger</h2><p class="muted small">Angeboten und '
            f'abgerufen -- nicht „installiert“.</p><table>{r_rows}</table></div>')
    return page(name, "Verwaltung", body, "/my", True, True)


def render_asset_edit(M, name, p, a, msg=""):
    links = "\n".join(f'{l["label"]} | {l["url"]}' for l in a["links_list"])
    imgs = "".join(
        f'<div style="display:inline-block;margin:.3rem;text-align:center">'
        f'<img src="/my/img/{i["sha256"]}" style="height:5rem;border-radius:.4rem" alt=""><br>'
        f'<span class="muted small">{esc(i["role"])}</span><br>'
        f'<form class=inline method=post action="/admin/assets/{quote(a["id"])}/image-remove">'
        f'<input type=hidden name=sha value="{i["sha256"]}"><button class=quiet>Entfernen</button></form></div>'
        for i in M.images(a["id"]))
    note = f'<div class=card><p style="margin:0">{esc(msg)}</p></div>' if msg else ""
    body = (note + f'<p><a href="/admin">← Verwaltung</a> · <a href="/my/assets/{quote(a["id"])}">Detailseite</a></p>'
            f'<h1>{esc(a.get("title") or a["id"])}</h1>'
            f'<form class=card method=post action="/admin/assets/{quote(a["id"])}/meta">'
            f'<p><label>Titel<br><input type=text name=title value="{esc(a.get("title") or "")}" size=50></label></p>'
            f'<p><label>Kurzbeschreibung<br><input type=text name=summary value="{esc(a.get("summary") or "")}" size=80 maxlength=300></label></p>'
            f'<p><label>Beschreibung (Markdown)<br><textarea name=description>{esc(a.get("description") or "")}</textarea></label></p>'
            f'<p><label>Kategorien (kommagetrennt)<br><input type=text name=categories value="{esc(",".join(a["categories_list"]))}" size=40></label></p>'
            f'<p><label>Schlagworte (kommagetrennt)<br><input type=text name=tags value="{esc(",".join(a["tags_list"]))}" size=60></label></p>'
            f'<p><label>Herausgeber<br><input type=text name=publisher value="{esc(a.get("publisher") or "")}" size=40></label> '
            f'<label>Lizenz<br><input type=text name=license value="{esc(a.get("license") or "")}" size=20></label></p>'
            f'<p><label>Links (je Zeile: Name | https://…)<br><textarea name=links style="min-height:4rem">{esc(links)}</textarea></label></p>'
            '<button>Speichern</button></form>'
            f'<div class=card><h2>Bilder</h2>{imgs or "<p class=muted>Noch keine.</p>"}'
            f'<form method=post action="/admin/assets/{quote(a["id"])}/image" enctype="multipart/form-data">'
            '<p><input type=file name=file accept="image/png,image/jpeg,image/webp" required> '
            '<select name=role><option value=screenshot>Screenshot</option><option value=icon>Icon</option></select> '
            '<button>Hochladen</button></p><p class="muted small">PNG, JPEG oder WebP, höchstens 5 MB, '
            'bis zu 8 Screenshots. Kein SVG.</p></form></div>')
    return page(name, "Asset bearbeiten", body, "/my", True, True)
