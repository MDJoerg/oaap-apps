"""Der Rahmen einer OAAP-App (RFC-0118 §4-§7, Design-Guidelines v0.3 §8).

Eine Kopie in jeder App mit Oberfläche (check-shared.py zählt sie), die
Quelle ist `lib/appframe/`. Nur Standardbibliothek.

Er schreibt, was jede Seite gleich hat -- Kopfleiste, Reiter, Fußzeile,
Kit und Rahmenskript der Plattform -- und die Bausteine des Listenberichts:
Filterleiste, sortierbare Spalten, Zählzeile, Blättern, Badges, leere
Liste. Die Farben kommen aus /platform/theme.css; eine App setzt keine
eigenen außer über dessen Variablen.

    import appframe as af
    html = af.page("Übersicht", body, app="Wegweiser", version=VERSION,
                   product="wegweiser",
                   tabs=[("./", "Meine Links"), ("areas", "Bereiche")], active="./")

Ohne das Rahmenskript bleibt die Seite ganz: Name, Rückweg zum Portal,
Reiter, Inhalt. Das Skript fügt Mandant, Hilfe, Fragen, Benutzermenü und
Feedback hinzu (RFC-0118 P2).
"""
import html as _html
import os
from urllib.parse import urlencode

esc = _html.escape

HEX_SVG = ('<svg viewBox="0 0 100 100" width="26" height="26" aria-hidden="true">'
           '<polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="none" stroke="currentColor" '
           'stroke-width="8" stroke-linejoin="round"/>'
           '<polygon points="50,30 72,42 72,62 50,74 28,62 28,42" fill="currentColor" opacity=".85"/></svg>')
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
           "%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

PAGE_SIZE = 50


def portal_url():
    """Der Portal-Eingang des Mandanten aus OAAP_PORTAL_URL (die Seite dieser
    Instanz im Portal) -- '' ohne; dann führt die Marke auf die App selbst."""
    u = (os.environ.get("OAAP_PORTAL_URL") or "").strip()
    return u.rsplit("/instances/", 1)[0] + "/" if "/instances/" in u else u


def tabs(items, active):
    """Die eine Reiterleiste: [(href, Text) oder (href, Text, Zahl)]. Leer bei
    weniger als zwei Reitern (RFC-0118 §6.1)."""
    if not items or len(items) < 2:
        return ""
    out = []
    for it in items:
        href, text = it[0], it[1]
        count = it[2] if len(it) > 2 else None
        cur = ' aria-current="page"' if href == active else ""
        c = f' <span class="oaap-count">{esc(str(count))}</span>' if count not in (None, "") else ""
        out.append(f'<a href="{esc(href)}"{cur}>{esc(text)}{c}</a>')
    return '<nav class="oaap-tabs" aria-label="Bereiche">' + "".join(out) + "</nav>"


def page(title, body, *, app, version, product=None, tabs_html="", tabs=None, active=None,
         obj=None, obj_title=None, style="", head="", scripts="", lang="de",
         home=None, footer_extra="", status=200):
    """Ein ganzes HTML-Dokument im Rahmen.

    app       der Name der App (RFC-0118 §3: ohne „OAAP“, derselbe wie im Manifest)
    product   der Name ihres Hilfeprodukts (docs:), Standard: die App-Kennung aus dem Kontext
    tabs      [(href, Text[, Zahl])], active = href des aktiven Reiters
    obj       "Art:Kennung" des Objekts, das die Seite zeigt (für Fragen und Feedback)
    style     nur, was der App eigen ist -- Farben über var(--oaap-…)
    """
    nav = tabs_html or (globals()["tabs"](tabs, active) if tabs else "")
    brand_href = home or portal_url() or "./"
    attrs = ""
    if product:
        attrs += f' data-oaap-product="{esc(product)}"'
    if obj:
        attrs += f' data-oaap-object="{esc(obj)}"'
    if obj_title:
        attrs += f' data-oaap-object-title="{esc(obj_title)}"'
    full_title = f"{title} · {app}" if title and title != app else app
    return (
        f'<!doctype html><html lang="{esc(lang)}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{esc(full_title)}</title>'
        f'<link rel="icon" href="{FAVICON}">'
        '<link rel="stylesheet" href="/platform/theme.css">'
        '<script src="/platform/app.js" defer></script>'
        f'{f"<style>{style}</style>" if style else ""}{head}</head>'
        '<body class="oaap-app">'
        f'<header class="oaap-header"{attrs}>'
        f'<a class="oaap-brand" href="{esc(brand_href)}" data-oaap-portal>'
        f'<span class="oaap-logo">{HEX_SVG}</span><span class="oaap-tenant"></span>'
        f'<span class="oaap-appname">{esc(app)}</span></a>'
        '<span class="oaap-actions"></span></header>'
        f'{nav}<main class="oaap-main">{body}</main>'
        f'<footer class="oaap-footer"><span>{esc(app)} {esc(version or "")}{footer_extra}</span>'
        '<span class="oaap-platform"></span></footer>'
        f'{scripts}</body></html>')


# ----------------------------------------------------------- Seitenteile
def pagehead(title, actions=""):
    return f'<div class="oaap-pagehead"><h1>{esc(title)}</h1><div>{actions}</div></div>'


def back(href, text="Zurück zur Liste"):
    return f'<a class="oaap-back" href="{esc(href)}">← {esc(text)}</a>'


def msg(text, error=False):
    if not text:
        return ""
    return f'<div class="oaap-msg{" err" if error else ""}" role="status">{esc(text)}</div>'


def badge(text, kind=""):
    """kind: ok, warn, err, info, test oder leer (neutral)."""
    k = f" {kind}" if kind in ("ok", "warn", "err", "info", "test") else ""
    return f'<span class="oaap-badge{k}">{esc(str(text))}</span>'


def card(title, inner, cls=""):
    h = f"<h2>{esc(title)}</h2>" if title else ""
    return f'<section class="oaap-card{" " + cls if cls else ""}">{h}{inner}</section>'


def empty(text):
    return f'<div class="oaap-empty">{text}</div>'


def facts(pairs):
    """Die Kennzahlen eines Objektkopfs: [(Bezeichnung, HTML)]."""
    return ('<dl class="oaap-facts">' + "".join(
        f"<div><dt>{esc(k)}</dt><dd>{v}</dd></div>" for k, v in pairs if v not in (None, "")) + "</dl>")


# --------------------------------------------- Listenbericht (RFC-0118 §7)
class Query:
    """Die Parameter einer Liste aus der Adresse: q, sort, offset und die
    Filter. Liest einen dict wie parse_qs ihn liefert oder einen einfachen."""

    def __init__(self, params, filters=(), default_sort="", page_size=PAGE_SIZE):
        def one(k):
            v = params.get(k, "")
            return (v[0] if isinstance(v, list) and v else v if isinstance(v, str) else "").strip()
        self.q = one("q")[:200]
        self.sort = one("sort") or default_sort
        try:
            self.offset = max(0, int(one("offset") or 0))
        except ValueError:
            self.offset = 0
        self.size = page_size
        self.filters = {f: one(f) for f in filters}

    def params(self, **change):
        p = {"q": self.q, "sort": self.sort, "offset": self.offset, **self.filters}
        p.update(change)
        return {k: v for k, v in p.items() if v not in ("", None, 0)}

    def href(self, base="?", **change):
        qs = urlencode(self.params(**change))
        return f"{base}{qs}" if qs else (base.rstrip("?") or "?")

    def matches(self, *texts):
        """Volltext: alle Wörter von q kommen in einem der Texte vor."""
        if not self.q:
            return True
        hay = " ".join(str(t or "") for t in texts).lower()
        return all(w in hay for w in self.q.lower().split())

    def sorted(self, rows, keys):
        """rows nach self.sort ordnen; keys: {Spalte: Funktion(row)}."""
        col = self.sort.lstrip("-")
        fn = keys.get(col)
        if not fn:
            return list(rows)

        def k(r):
            v = fn(r)
            return (v is None, v if not isinstance(v, str) else v.lower())
        return sorted(rows, key=k, reverse=self.sort.startswith("-"))

    def window(self, rows):
        rows = list(rows)
        if self.offset >= len(rows):
            self.offset = 0
        return rows[self.offset:self.offset + self.size]


def filter_bar(query, choices=(), base="", search_label="Suchen", placeholder="", hidden=None):
    """Die Filterleiste: Suche zuerst, dann höchstens vier Auswahlen, dann
    Zurücksetzen. choices: [(name, Bezeichnung, [(wert, Text)])]. Ein GET-
    Formular -- die Adresse trägt den Filter (merkbar, versendbar)."""
    parts = [f'<form class="oaap-filter" method="get" action="{esc(base)}" role="search">',
             f'<label>{esc(search_label)}<input type="search" name="q" value="{esc(query.q)}"'
             f' placeholder="{esc(placeholder)}"></label>']
    for name, label, opts in list(choices)[:4]:
        cur = query.filters.get(name, "")
        o = "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(t)}</option>'
                    for v, t in [("", "alle")] + list(opts))
        parts.append(f'<label>{esc(label)}<select name="{esc(name)}" onchange="this.form.submit()">{o}</select></label>')
    if query.sort:
        parts.append(f'<input type="hidden" name="sort" value="{esc(query.sort)}">')
    # ein GET-Formular verwirft die Abfrage seiner Adresse: was die Seite
    # braucht (etwa ?tab=), geht als verstecktes Feld mit
    for k, v in (hidden or {}).items():
        parts.append(f'<input type="hidden" name="{esc(k)}" value="{esc(str(v))}">')
    parts.append('<button type="submit">Anzeigen</button>')
    if query.q or any(query.filters.values()):
        parts.append(f'<a class="oaap-btn" href="{esc(base or "?")}">Zurücksetzen</a>')
    parts.append("</form>")
    return "".join(parts)


def countline(shown_from, shown_to, total, noun="Einträge", one=None):
    """„23 von 340 Einträgen“ -- one: das Wort für genau einen (Standard „Eintrag“
    bei „Einträge“, sonst das Wort selbst)."""
    if total == 0:
        return ""
    if total == 1:
        word = one or {"Einträge": "Eintrag", "Zeilen": "Zeile", "Treffer": "Treffer"}.get(noun, noun)
        return f'<p class="oaap-countline">1 {esc(word)}</p>'
    if shown_from == 1 and shown_to == total:
        return f'<p class="oaap-countline">{total} {esc(noun)}</p>'
    return f'<p class="oaap-countline">{shown_from}–{shown_to} von {total} {esc(noun)}</p>'


def th(label, col, query, base="?", cls=""):
    """Ein sortierbarer Spaltenkopf (▲/▼, aria-sort). col='' → nicht sortierbar."""
    c = f' class="{cls}"' if cls else ""
    if not col:
        return f"<th{c}>{esc(label)}</th>"
    cur = query.sort.lstrip("-") == col
    desc = query.sort.startswith("-")
    aria = (' aria-sort="descending"' if desc else ' aria-sort="ascending"') if cur else ""
    nxt = ("-" + col) if (cur and not desc) else col
    return f'<th{c}{aria}><a href="{esc(query.href(base, sort=nxt, offset=0))}">{esc(label)}</a></th>'


def table(head_html, rows_html, empty_text="Nichts gefunden."):
    """head_html: die <th>-Zellen; rows_html: fertige <tr>…</tr>."""
    if not rows_html:
        return empty(empty_text)
    return (f'<div class="oaap-tablewrap"><table class="oaap-table"><thead><tr>{head_html}</tr></thead>'
            f'<tbody>{"".join(rows_html)}</tbody></table></div>')


def paging(query, total, base="?"):
    """Blättern ab mehr als einer Seite -- nie eine stille Grenze."""
    if total <= query.size:
        return ""
    parts = ['<nav class="oaap-paging" aria-label="Seiten">']
    if query.offset > 0:
        parts.append(f'<a href="{esc(query.href(base, offset=max(0, query.offset - query.size)))}">← Vorige {query.size}</a>')
    parts.append(f"<span>{query.offset + 1}–{min(total, query.offset + query.size)} von {total}</span>")
    if query.offset + query.size < total:
        parts.append(f'<a href="{esc(query.href(base, offset=query.offset + query.size))}">Weitere {query.size} →</a>')
    parts.append("</nav>")
    return "".join(parts)


def list_report(query, rows, columns, row_html, *, base="?", noun="Einträge",
                empty_text="Noch nichts da.", choices=(), search=True, placeholder="", hidden=None):
    """Der ganze Listenbericht in einem Aufruf.

    rows     alle Zeilen (schon gefiltert nach Rechten)
    columns  [(Bezeichnung, Spalte oder '', Schlüsselfunktion oder None, css-Klasse)]
    row_html Funktion(row) → '<tr>…</tr>'
    Gefiltert wird mit query.matches im Aufrufer oder hier über die Texte der
    Schlüsselfunktionen.
    """
    keys = {col: fn for _l, col, fn, _c in columns if col and fn}
    # ohne Suchfeld filtert auch ein q in der Adresse nicht (es gäbe kein Feld, es zu löschen)
    hits = ([r for r in rows if query.matches(*[fn(r) for fn in keys.values()])]
            if (query.q and search) else list(rows))
    hits = query.sorted(hits, keys)
    total = len(hits)
    page_rows = query.window(hits)
    head = "".join(th(label, col if fn else "", query, base, cls) for label, col, fn, cls in columns)
    bar = (filter_bar(query, choices, base.rstrip("?") if base != "?" else "", placeholder=placeholder,
                      hidden=hidden) if search else "")
    nothing = "Keine Treffer für diese Auswahl." if (query.q or any(query.filters.values())) else empty_text
    return (bar + countline(query.offset + 1, query.offset + len(page_rows), total, noun)
            + table(head, [row_html(r) for r in page_rows], nothing) + paging(query, total, base))
