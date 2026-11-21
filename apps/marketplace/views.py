"""Das Schaufenster (RFC-0077 §8, Stufe 2, abgeschlossen in §15.8): Startseite,
Suche mit Karten und Facetten, Detailseite als ObjectPage, Teilen. Die
Verwaltung und „Meine Assets“ stehen in admin_views.py (im Rahmen der
Plattform, RFC-0118).

Jede Liste und jede Zahl entsteht NACH `access.can_see` (§5): ein verborgenes
Asset ändert keine Facette, keinen Zähler, keine Reihe. Markdown geht durch
`render.py` (erst escapen, dann wenige Formen). Öffentliche und angemeldete
Seiten sprechen Englisch oder Deutsch (i18n.py), die Verwaltung Deutsch.
"""
import datetime
import html
import re
from urllib.parse import quote, urlencode

import access
import db
import i18n
import render

try:
    import segno
except ImportError:                     # ohne segno: kein QR-Code, der Link bleibt
    segno = None

esc = html.escape
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ROWS = ("featured", "landscapes", "new", "popular")

STYLE = """<style>
  :root{--blue:var(--oaap-blue-900,#1e40af);--blue-2:var(--oaap-blue-600,#2563eb);--bg:#f4f6fa;--text:#1f2937;--muted:#6b7280;--card:#fff;
        --border:#e5e7eb;--ok:#15803d;--err:#b91c1c;--chip:#dbeafe;--chip-t:#1e3a8a;--tile:#e0e7ff}
  @media (prefers-color-scheme: dark){:root{--bg:#0b1220;--text:#e5e7eb;--muted:#94a3b8;
        --card:#111827;--border:#1f2937;--chip:#1e3a8a;--chip-t:#dbeafe;--tile:#1e293b}}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:var(--bg);color:var(--text);line-height:1.45}
  header{background:var(--blue);color:#fff;padding:.6rem 1.2rem;display:flex;gap:1rem;
         justify-content:space-between;align-items:center;flex-wrap:wrap}
  header .name{font-weight:600;color:#fff;text-decoration:none;display:flex;gap:.6rem;align-items:center}
  header .name img{height:1.8rem;border-radius:.3rem}
  header nav{display:flex;gap:1rem;align-items:center;flex-wrap:wrap} header a{color:#fff;font-size:.9rem}
  header .lang a{opacity:.7;text-decoration:none} header .lang a.on{opacity:1;font-weight:600;text-decoration:underline}
  main{max-width:74rem;margin:1.2rem auto;padding:0 1rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:.7rem;padding:1.1rem;margin-bottom:1rem}
  .layout{display:grid;grid-template-columns:15rem 1fr;gap:1.2rem}
  @media (max-width: 760px){.layout{grid-template-columns:1fr}}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(16rem,1fr));gap:1rem}
  .row{display:grid;grid-auto-flow:column;grid-auto-columns:minmax(16rem,18rem);gap:1rem;overflow-x:auto;padding-bottom:.4rem}
  a.tilecard{display:flex;gap:.8rem;text-decoration:none;color:inherit;min-height:7.5rem;margin:0}
  a.tilecard:hover,a.lscard:hover,a.hero:hover{border-color:var(--blue-2)}
  .icon{width:3.2rem;height:3.2rem;border-radius:.6rem;flex:none;object-fit:cover;background:var(--tile);
        display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.3rem;color:var(--blue-2)}
  .icon.big{width:5rem;height:5rem;font-size:2rem;border-radius:1rem}
  h1{font-size:1.5rem;margin:.1rem 0 .3rem} h2{font-size:1.05rem;margin:0 0 .6rem} h3{font-size:.95rem;margin:.2rem 0}
  .section-title{display:flex;justify-content:space-between;align-items:baseline;margin:1.4rem 0 .6rem}
  .section-title h2{font-size:1.2rem;margin:0}
  table{border-collapse:collapse;width:100%;font-size:.9rem}
  th,td{text-align:left;padding:.4rem .45rem;border-bottom:1px solid var(--border);vertical-align:top}
  .muted{color:var(--muted)} .ok{color:var(--ok)} .err{color:var(--err)} .small{font-size:.85rem}
  .badge{font-size:.72rem;padding:.12rem .55rem;border-radius:1rem;background:var(--chip);color:var(--chip-t);white-space:nowrap}
  .badge.free{background:#dcfce7;color:#166534} .badge.locked{background:#fef3c7;color:#92400e}
  .badge.mine{background:#e0e7ff;color:#3730a3} .badge.beta{background:#fae8ff;color:#86198f}
  .badge.review{background:#fee2e2;color:#991b1b} .badge.ct{background:transparent;border:1px solid var(--border);color:var(--muted)}
  button,.btn{padding:.5rem 1rem;border:0;border-radius:.45rem;background:var(--blue-2);color:#fff;
        cursor:pointer;font-size:.92rem;text-decoration:none;display:inline-block;min-height:2.4rem;font-family:inherit}
  .btn.big{padding:.7rem 1.6rem;font-size:1rem;font-weight:600}
  .btn.quiet,button.quiet{background:transparent;color:var(--blue-2);border:1px solid var(--border)}
  button.danger{background:var(--err)}
  input,select,textarea{padding:.45rem;border:1px solid var(--border);border-radius:.35rem;max-width:100%;
        background:var(--card);color:var(--text);font:inherit}
  textarea{width:100%;min-height:8rem}
  form.inline{display:inline}
  code{background:rgba(127,127,127,.15);padding:.05rem .3rem;border-radius:.2rem;word-break:break-all}
  .facet a{display:flex;justify-content:space-between;text-decoration:none;color:inherit;padding:.15rem .3rem;border-radius:.3rem}
  .facet a.on{background:var(--chip);color:var(--chip-t);font-weight:600}
  .facet h3{margin-top:.8rem;color:var(--muted);font-size:.78rem;text-transform:uppercase;letter-spacing:.04em}
  .gallery{display:flex;gap:.8rem;overflow-x:auto;padding-bottom:.3rem;scroll-snap-type:x mandatory}
  .gallery img{height:18rem;max-width:90vw;border-radius:.6rem;border:1px solid var(--border);scroll-snap-align:start;object-fit:cover}
  @media (max-width: 760px){.gallery img{height:12rem}}
  .md p{margin:.4rem 0} .md pre{overflow-x:auto;background:rgba(127,127,127,.12);padding:.6rem;border-radius:.4rem}
  dl.facts{display:grid;grid-template-columns:auto 1fr;gap:.35rem 1rem;margin:0;font-size:.9rem}
  dl.facts dt{color:var(--muted)} dl.facts dd{margin:0;min-width:0}
  .objhead{display:grid;grid-template-columns:auto 1fr auto;gap:1.2rem;align-items:start}
  @media (max-width: 760px){.objhead{grid-template-columns:auto 1fr}.objhead .acts{grid-column:1 / -1}}
  .objhead .acts{display:flex;flex-direction:column;gap:.5rem;align-items:stretch;min-width:13rem}
  .chips{display:flex;gap:.35rem;flex-wrap:wrap;margin:.4rem 0}
  .anchors{position:sticky;top:0;z-index:2;background:var(--bg);display:flex;gap:1.4rem;padding:.6rem .2rem;
           border-bottom:1px solid var(--border);margin-bottom:1rem;overflow-x:auto}
  .anchors a{color:var(--text);text-decoration:none;font-weight:600;white-space:nowrap}
  .anchors a:hover{color:var(--blue-2)}
  section{scroll-margin-top:3.5rem}
  details.share{position:relative} details.share summary{list-style:none;cursor:pointer}
  details.share summary::-webkit-details-marker{display:none}
  .sharebox{margin-top:.5rem;padding:.8rem;border:1px solid var(--border);border-radius:.5rem;background:var(--card);max-width:22rem}
  .sharebox svg{background:#fff;padding:.4rem;border-radius:.3rem;display:block;margin:0 auto .6rem}
  .copyrow{display:flex;gap:.4rem} .copyrow input{flex:1;min-width:0;font-size:.8rem}
  .search{display:flex;gap:.5rem;margin-bottom:1rem} .search input{flex:1}
  a.hero{display:grid;grid-template-columns:1fr 1.3fr;gap:0;padding:0;overflow:hidden;text-decoration:none;color:inherit;min-height:13rem}
  @media (max-width: 760px){a.hero{grid-template-columns:1fr}}
  a.hero .txt{padding:1.4rem;display:flex;flex-direction:column;justify-content:center;gap:.4rem}
  a.hero .txt h2{font-size:1.4rem;margin:0} a.hero img.banner{width:100%;height:100%;object-fit:cover;min-height:13rem}
  a.hero .ph{background:linear-gradient(135deg,var(--blue),var(--blue-2));min-height:13rem}
  a.lscard{display:flex;gap:.9rem;text-decoration:none;color:inherit;align-items:flex-start;margin:0}
  .ls-logo{width:3rem;height:3rem;border-radius:.6rem;object-fit:cover;flex:none;background:var(--blue-2);color:#fff;
           display:flex;align-items:center;justify-content:center;font-weight:700}
  .tabs a{padding:.3rem .7rem;border-radius:.4rem;text-decoration:none} .tabs a.on{background:var(--chip);color:var(--chip-t);font-weight:600}
</style>"""


def _date(ts):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%d.%m.%Y")


class Ctx:
    """Wer fragt, in welcher Sprache, auf welchem Pfad -- und die Einstellungen."""

    def __init__(self, M, name, p, base, lang="en", origin="", path="/", query="",
                 app_name="Marktplatz", version=""):
        self.M, self.p, self.base = M, p, base
        self.lang = lang
        self.ul = i18n.ui_lang(lang)
        # Ohne MARKET_NAME heißt das Schaufenster auf Deutsch „Marktplatz“, auf
        # Englisch „Marketplace“; die Verwaltung trägt den Namen der App (§3).
        self.app_name = name or app_name
        self.name = name or ("Marketplace" if self.ul == "en" else app_name)
        self.version = version
        self.origin = origin
        self.path, self.query = path, query
        self.settings = M.settings()
        self.signed = not p["anon"]
        self.admin = access.is_admin(p)

    def t(self, key, **kw):
        return i18n.t(self.ul, key, **kw)


def accent_css(ctx):
    acc = ctx.settings.get("accent") or ""
    if not HEX_RE.match(acc):
        return ""
    return f"<style>:root{{--blue:{acc};--blue-2:{acc}}}</style>"


def page(ctx, title, body):
    """Das öffentliche Gesicht (das Schaufenster behält sein eigenes
    Aussehen -- Sprache, Akzentfarbe, Name des Betreibers). Die Farben kommen aus
    den Variablen des Kits, wo es geladen ist; die Verwaltung trägt den Rahmen
    (admin_views.py)."""
    nav = []
    langs = []
    for code in i18n.UI_LANGS:
        args = dict(_qs(ctx.query), lang=code)
        langs.append(f'<a class="{"on" if ctx.ul == code else ""}" href="{esc(ctx.path)}?'
                     f'{esc(urlencode(args))}" hreflang={code}>{code.upper()}</a>')
    nav.append(f'<span class=lang aria-label="{ctx.t("language")}">{" ".join(langs)}</span>')
    if not ctx.signed:
        nav.append(f'<a href="/my{esc(ctx.path if ctx.path != "/" else "/")}">{ctx.t("sign_in")}</a>')
    else:
        nav.append(f'<a href="/my/">{ctx.t("overview")}</a>')
        pubs = ctx.M.publishers()
        if not ctx.admin and any(access.can_manage(ctx.p, a["id"], pubs) for a in ctx.M.assets()):
            nav.append(f'<a href="/my/manage">{ctx.t("manage")}</a>')
        if ctx.admin:
            nav.append(f'<a href="/admin">{ctx.t("admin")}</a>')
    logo = ctx.settings.get("logo") or ""
    logo_html = f'<img src="/logo/{esc(logo)}" alt="">' if db_sha(logo) else ""
    home = "/my/" if ctx.signed else "/"
    return (f"<!doctype html><html lang={ctx.ul}><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)} – {esc(ctx.name)}</title>"
            "<link rel=stylesheet href=/platform/theme.css>"
            f"{STYLE}{accent_css(ctx)}"
            "<script src=/static/market.js defer></script>"
            f"<header><a class=name href='{home}'>{logo_html}<span>{esc(ctx.name)}</span></a>"
            f"<nav>{''.join(nav)}</nav></header><main>{body}</main></html>")


def db_sha(s):
    return bool(re.match(r"^[0-9a-f]{64}$", s or ""))


def _qs(query):
    from urllib.parse import parse_qsl
    return {k: v for k, v in parse_qsl(query or "") if k != "lang"}


def icon_html(a, base, big=False, title=""):
    cls = "icon big" if big else "icon"
    if a.get("icon"):
        return f'<img class="{cls}" src="{base}/img/{a["icon"]}" alt="">'
    letter = esc((title or a.get("title") or a["id"])[:1].upper())
    return f'<span class="{cls}" aria-hidden=true>{letter}</span>'


def tx(ctx, a):
    """Die Texte eines Assets in der gewünschten Sprache (mit Rückfall)."""
    r = db.resolve(a.get("texts_map") or {}, ctx.lang)
    r.setdefault("title", "")
    if not r.get("title"):
        r["title"] = a.get("title") or a["id"]
    return r


def label_of(ctx, row):
    r = db.resolve(row.get("texts_map") or {}, ctx.lang)
    return r.get("title") or row["id"], r.get("description") or ""


# ------------------------------------------------------------------- state ---
def content_of(M, a):
    """Inhaltstyp (§15.8.2) -- abgeleitet, nicht gespeichert."""
    if a["type"] == "oaap-app":
        return "app"
    if a["type"] == "oaap-profile":
        return "template"
    for r in M.releases(a["id"]):
        f = next((x for x in r["files"] if x["role"] == "primary"), None)
        if f:
            ext = (f["name"].rsplit(".", 1)[-1] if "." in f["name"] else "").lower()
            return {"pdf": "pdf", "json": "json", "png": "image", "jpg": "image", "jpeg": "image",
                    "webp": "image", "txt": "text", "md": "text", "zip": "archive"}.get(ext, "file")
    return "file"


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
                how.add("free")
            elif access.is_admin(p):
                how.add("operator")
            else:
                how.add("entitled")
    latest = next((per[ch]["release"] for ch in M.channels if ch in per), None)
    beta_only = bool(per) and all(ch != M.channels[0] for ch in per)
    return {"state": st, "per": per, "how": how, "latest": latest, "beta_only": beta_only}


def pending_of(M, p, a):
    """Releases in Prüfung -- nur für Betreiber und wer einstellen darf."""
    if not (access.is_admin(p) or access.can_manage(p, a["id"], M.publishers())):
        return []
    return [r for r in M.releases(a["id"]) if not db.counts(r) and not r["yanked_at"]]


def badges(ctx, v, a=None):
    out = []
    if v["state"] == "locked":
        out.append(f'<span class="badge locked">{ctx.t("b_locked")}</span>')
    elif "free" in v["how"]:
        out.append(f'<span class="badge free">{ctx.t("b_free")}</span>')
    elif v["how"]:
        out.append(f'<span class="badge mine">{ctx.t("b_" + sorted(v["how"])[0])}</span>')
    if v["beta_only"]:
        out.append(f'<span class="badge beta">{esc(next(iter(v["per"])))}</span>')
    if a is not None and pending_of(ctx.M, ctx.p, a):
        out.append(f'<span class="badge review">{ctx.t("b_review")}</span>')
    if a is not None:
        out.append(f'<span class="badge ct">{ctx.t("c_" + content_of(ctx.M, a))}</span>')
    return " ".join(out)


# ------------------------------------------------------------------- search --
FACETS = (("ls", "f_ls"), ("cat", "f_cat"), ("content", "f_content"), ("tag", "f_tag"),
          ("channel", "f_channel"))


def _facet_values(M, a):
    return {"ls": set(a["landscapes_list"]), "cat": set(a["categories_list"]),
            "content": {content_of(M, a)}, "tag": set(a["tags_list"]),
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
        return all(v in fv[k] for k, v in chosen.items() if k != skip and k in fv)

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


def card(ctx, a, ents):
    v = view(ctx.M, ctx.p, a, ents)
    t = tx(ctx, a)
    ver = f'v{esc(v["latest"]["version"])}' if v["latest"] else ctx.t("no_release")
    pub = f' · {esc(a["publisher"])}' if a.get("publisher") else ""
    return (f'<a class="card tilecard" href="{ctx.base}/assets/{quote(a["id"])}">'
            f'{icon_html(a, ctx.base, title=t["title"])}'
            f'<span><h2 style="margin:0 0 .2rem">{esc(t["title"])}</h2>'
            f'<span class="muted small">{ver}{pub}</span>'
            f'<p class=small style="margin:.35rem 0">{esc((t.get("summary") or "")[:140])}</p>'
            f'{badges(ctx, v, a)}</span></a>')


def search_form(ctx, q="", chosen=None):
    hidden = "".join(f'<input type=hidden name="{k}" value="{esc(v)}">'
                     for k, v in (chosen or {}).items() if v)
    return (f'<form class=search method=get action="{ctx.base}/"><input type=search name=q '
            f'value="{esc(q)}" placeholder="{ctx.t("search_ph")}" aria-label="{ctx.t("search")}">'
            f'{hidden}<button>{ctx.t("search")}</button></form>')


def facet_label(ctx, key, val, ls_map, cat_map):
    if key == "ls" and val in ls_map:
        return label_of(ctx, ls_map[val])[0]
    if key == "cat" and val in cat_map:
        return label_of(ctx, cat_map[val])[0]
    if key == "content":
        return ctx.t("c_" + val)
    return val


def render_list(ctx, q, chosen):
    M = ctx.M
    items, facets = search(M, ctx.p, q, chosen)
    ls_map = {r["id"]: r for r in M.landscapes()}
    cat_map = {r["id"]: r for r in M.categories()}
    cards = [card(ctx, a, ents) for a, ents in items]

    def link(key, val):
        nxt = dict(chosen)
        if nxt.get(key) == val:
            nxt.pop(key)
        else:
            nxt[key] = val
        if key == "ls":
            nxt.pop("cat", None)
        args = {k: v for k, v in dict(nxt, q=q).items() if v}
        args.setdefault("all", "1")
        return f"{ctx.base}/?{urlencode(args)}"

    side = []
    for key, label in FACETS:
        vals = facets[key]
        if key == "cat" and chosen.get("ls"):
            vals = [(v, n) for v, n in vals
                    if cat_map.get(v, {}).get("landscape") in (chosen["ls"], db.DEFAULT_LANDSCAPE)]
        if not vals:
            continue
        side.append(f"<h3>{ctx.t(label)}</h3>" + "".join(
            f'<a class="{"on" if chosen.get(key) == v else ""}" href="{esc(link(key, v))}">'
            f'<span>{esc(facet_label(ctx, key, v, ls_map, cat_map))}</span><span class=muted>{n}</span></a>'
            for v, n in vals))
    n = len(items)
    count = ctx.t("asset_1") if n == 1 else ctx.t("assets_n", n=n)
    head = ""
    if chosen.get("ls") in ls_map:
        title, desc = label_of(ctx, ls_map[chosen["ls"]])
        head = (f'<div class=card style="display:flex;gap:1rem;align-items:center">'
                f'{ls_logo(ls_map[chosen["ls"]], title)}<div><h1 style="margin:0">{esc(title)}</h1>'
                f'<p class=muted style="margin:.2rem 0 0">{esc(desc)}</p></div></div>')
    body = (search_form(ctx, q, dict(chosen, all="1")) + head +
            f'<div class=layout><aside class="card facet">{"".join(side) or "<p class=muted>" + ctx.t("no_filters") + "</p>"}</aside>'
            f'<section><p class="muted small" style="margin-top:0">{count}</p>'
            f'<div class=grid>{"".join(cards) or "<p class=muted>" + ctx.t("nothing") + "</p>"}</div></section></div>')
    return page(ctx, ctx.t("overview"), body)


def ls_logo(row, title):
    if db_sha(row.get("logo") or ""):
        return f'<img class=ls-logo src="/logo/{row["logo"]}" alt="">'
    return f'<span class=ls-logo aria-hidden=true>{esc(title[:1].upper())}</span>'


# ------------------------------------------------------------------- home ---
def render_home(ctx):
    """Startseite ohne Suche/Filter: Empfohlen, Landschaften, Neu, Meistgeladen (§15.8.6)."""
    M = ctx.M
    items, facets = search(M, ctx.p)
    rows_setting = [r for r in (ctx.settings.get("rows") or ",".join(ROWS)).split(",") if r in ROWS]
    parts = [search_form(ctx)]
    for row in rows_setting:
        if row == "featured":
            feat = [(a, e) for a, e in items if a.get("featured")]
            for a, ents in feat[:3]:
                t = tx(ctx, a)
                v = view(M, ctx.p, a, ents)
                banner = (f'<img class=banner src="{ctx.base}/img/{a["screenshots_list"][0]}" alt="">'
                          if a["screenshots_list"] else '<span class=ph></span>')
                parts.append(
                    f'<a class="card hero" href="{ctx.base}/assets/{quote(a["id"])}">'
                    f'<span class=txt><span class="muted small">{ctx.t("featured")}</span>'
                    f'<span style="display:flex;gap:.8rem;align-items:center">{icon_html(a, ctx.base, title=t["title"])}'
                    f'<h2>{esc(t["title"])}</h2></span>'
                    f'<span>{esc(t.get("summary") or "")}</span><span>{badges(ctx, v, a)}</span></span>'
                    f'{banner}</a>')
        elif row == "landscapes":
            counts = dict(facets["ls"])
            tiles = []
            for ls in M.landscapes():
                if not counts.get(ls["id"]):
                    continue
                title, desc = label_of(ctx, ls)
                tiles.append(f'<a class="card lscard" href="{ctx.base}/?{urlencode({"ls": ls["id"], "all": "1"})}">'
                             f'{ls_logo(ls, title)}<span><h2 style="margin:0 0 .2rem">{esc(title)}</h2>'
                             f'<span class="small muted">{esc(desc[:160])}</span><br>'
                             f'<span class="small">{ctx.t("assets_n", n=counts[ls["id"]]) if counts[ls["id"]] != 1 else ctx.t("asset_1")}</span>'
                             f'</span></a>')
            if tiles:
                parts.append(f'<div class=section-title><h2>{ctx.t("landscapes")}</h2></div>'
                             f'<div class=grid>{"".join(tiles)}</div>')
        elif row == "new":
            def newest(x):
                r = view(M, ctx.p, x[0], x[1])["latest"]
                return r["created_at"] if r else 0
            new = sorted([x for x in items if view(M, ctx.p, x[0], x[1])["latest"]], key=newest,
                         reverse=True)[:8]
            if new:
                parts.append(_row(ctx, "new", new))
        elif row == "popular":
            dc = M.download_counts()
            pop = sorted([x for x in items if dc.get(x[0]["id"])], key=lambda x: -dc[x[0]["id"]])[:8]
            if pop:
                parts.append(_row(ctx, "popular", pop))
    parts.append(f'<p style="margin-top:1.4rem"><a class="btn quiet" href="{ctx.base}/?all=1">'
                 f'{ctx.t("show_all")} ({len(items)})</a></p>')
    return page(ctx, ctx.t("overview"), "".join(parts))


def _row(ctx, key, pairs):
    return (f'<div class=section-title><h2>{ctx.t(key)}</h2>'
            f'<a href="{ctx.base}/?all=1">{ctx.t("show_all")}</a></div>'
            f'<div class=row>{"".join(card(ctx, a, e) for a, e in pairs)}</div>')


# ------------------------------------------------------------------- detail --
def qr_svg(url):
    if not segno:
        return ""
    return segno.make(url, error="m").svg_inline(scale=4, border=2, dark="#000", light="#fff")


def share_box(ctx, link):
    return (f'<details class=share><summary class="btn quiet" style="width:100%;text-align:center">'
            f'{ctx.t("share")}</summary><div class=sharebox>{qr_svg(link)}'
            f'<div class=copyrow><input type=text readonly value="{esc(link)}" aria-label="Link">'
            f'<button type=button class=quiet data-copy="{esc(link)}" data-done="{ctx.t("copied")}">'
            f'{ctx.t("copy_link")}</button></div>'
            f'<p class="muted small" style="margin:.5rem 0 0">{ctx.t("share_hint")}</p></div></details>')


def _linkify(text):
    out = esc(text or "")
    return re.sub(r"(https?://[^\s<>&\"']+)",
                  lambda m: f'<a href="{m.group(1)}" rel="noopener noreferrer" target=_blank>{m.group(1)}</a>', out)


def render_detail(ctx, a, ents, msg="", ok=True):
    M, p, base = ctx.M, ctx.p, ctx.base
    v = view(M, p, a, ents)
    t = tx(ctx, a)
    dl = "/d/" if p["anon"] else "/my/d/"
    canonical = f"{ctx.origin}/assets/{quote(a['id'])}"   # ctx.origin achtet auf MARKET_PUBLIC_URL
    actions = []
    for ch, x in v["per"].items():
        if x["obtainable"]:
            f = next((f for f in x["release"]["files"] if f["role"] == "primary"), None)
            if f:
                cls = "btn big" if not actions else "btn quiet"
                actions.append(f'<a class="{cls}" href="{dl}{f["sha256"]}">{ctx.t("download")} · '
                               f'{esc(ch)} {esc(x["release"]["version"])}</a>')
    locked_chs = [ch for ch, x in v["per"].items() if not x["obtainable"]]
    req_box = ""
    granted_note = ""
    if not p["anon"]:
        mine_all = M.requests(asset_id=a["id"], principal=p["user"])
        if mine_all and mine_all[0]["state"] == "granted" and mine_all[0].get("answer"):
            granted_note = (f'<div class=card><p class="small ok" style="margin:0">'
                            f'{ctx.t("req_granted", date=_date(mine_all[0]["decided_at"]))} '
                            f'{_linkify(mine_all[0]["answer"])}</p></div>')
    if locked_chs or v["state"] == "locked":
        if p["anon"]:
            req_box = (f'<div class=card><p class=small style="margin:0">'
                       f'{ctx.t("locked_anon", href="/my/assets/" + quote(a["id"]))}</p></div>')
            if not actions:
                actions.append(f'<a class="btn big" href="/my/assets/{quote(a["id"])}">{ctx.t("request")}</a>')
        else:
            mine = M.requests(asset_id=a["id"], principal=p["user"])
            last = mine[0] if mine else None
            status = ""
            if last and last["state"] == "open":
                status = f'<p class=small>{ctx.t("req_open", date=_date(last["at"]))}</p>'
            elif last and last["state"] == "rejected":
                status = (f'<p class="small err">{ctx.t("req_rejected", date=_date(last["decided_at"]))} '
                          f'{_linkify(last.get("answer") or "")}</p>')
            elif last and last["state"] == "granted" and last.get("answer"):
                status = (f'<p class="small ok">{ctx.t("req_granted", date=_date(last["decided_at"]))} '
                          f'{_linkify(last.get("answer") or "")}</p>')
            opts = "".join(f"<option>{esc(ch)}</option>" for ch in (locked_chs or M.channels))
            req_box = (f'<div class=card id=request><h2>{ctx.t("request")}</h2>{status}'
                       f'<form method=post action="/my/assets/{quote(a["id"])}/request">'
                       f'<p><label>{ctx.t("channel")} <select name=channel>{opts}</select></label></p>'
                       f'<p><textarea name=note placeholder="{ctx.t("req_note_ph")}" '
                       'style="min-height:4rem" maxlength=500></textarea></p>'
                       f'<button>{ctx.t("req_send")}</button></form></div>')
            if not actions:
                actions.append(f'<a class="btn big" href="#request">{ctx.t("request")}</a>')
    actions.append(share_box(ctx, canonical))
    ls_map = {r["id"]: r for r in M.landscapes()}
    cat_map = {r["id"]: r for r in M.categories()}
    chips = "".join(f'<a class=badge href="{base}/?{urlencode({"ls": x, "all": "1"})}">'
                    f'{esc(label_of(ctx, ls_map[x])[0])}</a>' for x in a["landscapes_list"] if x in ls_map)
    chips += "".join(f'<a class="badge ct" href="{base}/?{urlencode({"cat": x, "all": "1"})}">'
                     f'{esc(label_of(ctx, cat_map[x])[0])}</a>' for x in a["categories_list"] if x in cat_map)
    pub = f'<span class=muted>{esc(a["publisher"])}</span>' if a.get("publisher") else ""
    fallback = ""
    if t.get("_lang") and t["_lang"] != ctx.lang and a.get("texts_map"):
        fallback = (f'<p class="muted small" style="margin:.2rem 0">'
                    f'{ctx.t("only_in", lang=i18n.LANG_NAMES.get(t["_lang"], t["_lang"]))}</p>')
    pend = pending_of(M, p, a)
    pend_note = "".join(f'<div class=card><p class="small err" style="margin:0">'
                        f'{ctx.t("in_review", v=esc(r["version"]))}</p></div>' for r in pend[:2])
    shots = "".join(f'<img src="{base}/img/{s}" alt="Screenshot" loading=lazy>' for s in a["screenshots_list"])
    note = (f'<div class=card><p class="{"ok" if ok else "err"}" style="margin:0">{esc(msg)}</p></div>'
            if msg else "")
    head = (f'<div class="card objhead">{icon_html(a, base, big=True, title=t["title"])}'
            f'<div><h1>{esc(t["title"])}</h1>{pub}<div class=chips>{chips}</div>'
            f'<div>{badges(ctx, v, a)}</div>'
            f'<p style="margin:.6rem 0 0">{esc(t.get("summary") or "")}</p>{fallback}</div>'
            f'<div class=acts>{"".join(actions)}</div></div>')
    # Übersicht
    links = ""
    if a["links_list"]:
        links = (f'<h3 style="margin-top:1rem">{ctx.t("links")}</h3><p>' + " · ".join(
            f'<a href="{esc(link["url"], quote=True)}" rel="noopener noreferrer" target=_blank>'
            f'{esc(link["label"])}</a>' for link in a["links_list"]) + "</p>")
    tags = ""
    if a["tags_list"]:
        tags = (f'<p class=chips>' + " ".join(
            f'<a class=badge href="{base}/?{urlencode({"tag": x, "all": "1"})}">#{esc(x)}</a>'
            for x in a["tags_list"]) + "</p>")
    overview = (f'<section id=overview class="card md"><h2>{ctx.t("description")}</h2>'
                f'{render.to_html(t.get("description") or "") or "<p class=muted>" + ctx.t("no_description") + "</p>"}'
                f'{links}{tags}</section>')
    # Versionen
    hist = []
    can_mng = access.is_admin(p) or access.can_manage(p, a["id"], M.publishers())
    for r in M.releases(a["id"]):
        if (r["yanked_at"] or not db.counts(r)) and not can_mng:
            continue
        can = (not r["yanked_at"] and db.counts(r)
               and any(access.can_obtain(p, a, ch, ents) for ch in r["channels"]))
        files = " ".join(f'<a class="btn quiet" href="{dl}{f["sha256"]}">{ctx.t("download")}</a>'
                         for f in r["files"]) if can else ""
        notes = db.resolve({k: {"description": x} for k, x in r["notes_map"].items()}, ctx.lang)
        note_txt = notes.get("description") or r.get("notes") or ""
        state = ""
        if r["yanked_at"]:
            state = f'<br><span class=err>{ctx.t("yanked")}: {esc(r.get("yank_reason") or "")}</span>'
        elif not db.counts(r):
            state = f'<br><span class="badge review">{ctx.t("b_review")} · {esc(r.get("review") or "")}</span>'
        hist.append(f'<tr><td><strong>{esc(r["version"])}</strong><br><span class="muted small">'
                    f'{_date(r["created_at"])}</span>{state}</td>'
                    f'<td>{" ".join(f"<span class=badge>{esc(c)}</span>" for c in r["channels"]) or "–"}</td>'
                    f'<td class=md>{render.to_html(note_txt) or "<span class=muted>–</span>"}</td>'
                    f'<td>{files}</td></tr>')
    versions = (f'<section id=versions class=card style="overflow-x:auto"><h2>{ctx.t("tab_versions")}</h2>'
                f'<table><tr><th>{ctx.t("version")}</th><th>{ctx.t("channels")}</th><th>{ctx.t("notes")}</th>'
                f'<th></th></tr>{"".join(hist)}</table></section>')
    # Technische Details
    facts = [(ctx.t("id"), f"<code>{esc(a['id'])}</code>"), (ctx.t("type"), f"<code>{esc(a['type'])}</code>"),
             (ctx.t("f_content"), ctx.t("c_" + content_of(M, a)))]
    if a.get("publisher"):
        facts.append((ctx.t("publisher"), esc(a["publisher"])))
    if a.get("license"):
        facts.append((ctx.t("license"), esc(a["license"])))
    for ch, x in v["per"].items():
        f = next((f for f in x["release"]["files"] if f["role"] == "primary"), None)
        if f:
            facts.append((f"{ctx.t('channel')} {esc(ch)}",
                          f'{esc(x["release"]["version"])} · {_date(x["release"]["created_at"])} · '
                          f'{f["size"] / 1024:.0f} KB<br><code>{esc(f["name"])}</code><br>'
                          f'<span class="muted small">SHA-256</span> <code>{f["sha256"]}</code>'))
            if x["obtainable"] and a["type"] in ("oaap-profile", "file"):
                facts.append((ctx.t("dl_link"), f'<code>{esc(ctx.origin + "/d/" + f["sha256"]) if ch in access.free_channels(a) else esc(ctx.origin + dl + f["sha256"])}</code>'))
    facts.append((ctx.t("share_link"), f"<code>{esc(canonical)}</code>"))
    tech = (f'<section id=tech class=card><h2>{ctx.t("tab_tech")}</h2><dl class=facts>'
            + "".join(f"<dt>{k}</dt><dd>{val}</dd>" for k, val in facts) + "</dl></section>")
    anchors = (f'<nav class=anchors><a href="#overview">{ctx.t("tab_overview")}</a>'
               f'<a href="#versions">{ctx.t("tab_versions")}</a><a href="#tech">{ctx.t("tab_tech")}</a></nav>')
    body = (note + head + pend_note + (granted_note if not req_box else "")
            + (f'<div class=card><div class=gallery>{shots}</div></div>' if shots else "")
            + req_box + anchors + overview + versions + tech)
    return page(ctx, t["title"], body)


JS = """// Marketplace: Link kopieren (RFC-0077 §15.8.7). Ohne Skript bleibt das Feld zum Markieren.
document.addEventListener('click', function (e) {
  var b = e.target.closest('[data-copy]');
  if (!b) return;
  var done = function () { var t = b.textContent; b.textContent = b.dataset.done || 'OK';
    setTimeout(function () { b.textContent = t; }, 1500); };
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(b.dataset.copy).then(done, function () {});
  } else {
    var i = b.parentNode.querySelector('input'); if (i) { i.select(); document.execCommand('copy'); done(); }
  }
});
"""
