"""Grafiken als SVG, auf dem Server gezeichnet (RFC-0064 §5).

Nur Standardbibliothek, kein Skript im Browser: Der Hinweis beim
Darüberfahren ist ein `<title>` im SVG, und unter jeder Grafik steht
dieselbe Zahl als Tabelle — für alle, die Farben nicht unterscheiden,
und für die drei Farben, die auf Weiß unter 3:1 Kontrast liegen.

Gezeichnet wird ausschließlich, was `report.usage()` liefert. Diese
Datei rechnet nichts aus, was die Schnittstelle anders sähe.
"""
import html
from datetime import datetime, timedelta

esc = html.escape

# Feste Reihenfolge, nie zyklisch (geprüft: Farbfehlsichtigkeit ΔE ≥ 9 je
# Nachbarpaar). Mehr als sieben Reihen werden zu „Andere“ zusammengefasst,
# statt eine achte Farbe zu erfinden.
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
OTHER = "#a3a29c"
OTHER_NAME = "Andere"
EMPTY_NAME = "(ohne Angabe)"

# Ausgänge sind Zustände, keine Reihen: Statusfarben, immer mit Zeichen
# und Wort, nie die Farbe allein.
STATUS = {"good": ("#0ca30c", "✓"), "warning": ("#fab219", "!"),
          "serious": ("#ec835a", "▲"), "critical": ("#d03b3b", "✕")}

METRIC_LABELS = {"calls": "Aufrufe", "tokens": "Token (ein + aus)",
                 "bytes_up": "Bytes hoch", "bytes_down": "Bytes runter"}

W, H = 960, 280
PAD_L, PAD_R, PAD_T, PAD_B = 64, 12, 12, 36


def metric_of(group, metric):
    if metric == "tokens":
        return group["in_tokens"] + group["out_tokens"]
    return group[metric]


def fmt_num(value):
    return f"{int(value):,}".replace(",", ".")


def fmt_metric(value, metric):
    if metric in ("bytes_up", "bytes_down"):
        value = int(value)
        for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
            if value >= size:
                return f"{value / size:.1f} {unit}".replace(".", ",")
        return f"{value} B"
    return fmt_num(value)


def outcome_status(outcome):
    if outcome == "ok":
        return "good"
    if outcome.startswith("ok_no"):
        return "warning"            # beantwortet, aber ohne Token-Zahlen
    if outcome.startswith("upstream_4"):
        return "serious"            # die Quelle hat die Anfrage abgewiesen
    return "critical"


OUTCOME_WORDS = {
    "ok": "beantwortet, mit Token-Zahlen",
    "ok_no_counts": "beantwortet, Quelle nannte keine Token-Zahlen",
    "ok_no_usage_support": "beantwortet, Quelle kann keine Token-Zahlen im Strom",
    "budget": "abgewiesen: Budget erschöpft",
    "no_supplier": "abgewiesen: keine Quelle erlaubt oder erreichbar",
    "upstream_failed": "keine Quelle hat geantwortet",
}


def outcome_word(outcome):
    if outcome in OUTCOME_WORDS:
        return OUTCOME_WORDS[outcome]
    if outcome.startswith("upstream_"):
        return f"von der Quelle abgewiesen (HTTP {outcome[9:]})"
    if outcome.startswith("stream_broken"):
        return "Strom abgebrochen"
    return outcome


def buckets(frm, to, step):
    """Alle Tage oder Monate des Zeitraums — auch die ohne Aufruf."""
    start = datetime.strptime(frm[:10], "%Y-%m-%d")
    end = datetime.strptime(to[:19], "%Y-%m-%dT%H:%M:%S")
    out = []
    if step == "day":
        cur = start
        while cur < end:
            out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
    else:
        cur = start.replace(day=1)
        while cur < end:
            out.append(cur.strftime("%Y-%m"))
            cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def nice_max(value):
    if value <= 0:
        return 1
    mag = 10 ** (len(str(int(value))) - 1)
    for f in (1, 2, 2.5, 5, 10):
        if value <= f * mag:
            return f * mag
    return 10 * mag


def series_order(groups, by, metric):
    """Reihen nach Gesamtgröße, die größten sieben behalten ihre Farbe."""
    sums = {}
    for g in groups:
        name = g[by]
        sums[name] = sums.get(name, 0) + metric_of(g, metric)
    ranked = sorted(sums, key=lambda n: (-sums[n], n))
    kept = ranked[:len(SERIES)] if len(ranked) <= len(SERIES) else ranked[:len(SERIES) - 1]
    return kept, len(ranked) > len(kept)


def label(value):
    return EMPTY_NAME if value in ("", None) else str(value)


def legend(names, colors):
    items = "".join(f'<span class="lg"><i style="background:{c}"></i>{esc(n)}</span>'
                    for n, c in zip(names, colors))
    return f'<div class="legend">{items}</div>'


def _bar_top(x, y, w, h, color, title):
    """Ein Balkenstück mit gerundetem oberen Ende (4px), unten gerade."""
    r = min(4, w / 2, h)
    path = (f"M{x:.1f},{y + h:.1f} V{y + r:.1f} Q{x:.1f},{y:.1f} {x + r:.1f},{y:.1f} "
            f"H{x + w - r:.1f} Q{x + w:.1f},{y:.1f} {x + w:.1f},{y + r:.1f} V{y + h:.1f} Z")
    return f'<path d="{path}" fill="{color}"><title>{esc(title)}</title></path>'


def stacked_bars(doc, step, by, metric):
    """Verlauf: je Tag/Monat ein Balken, gestapelt nach einer Dimension."""
    groups = doc["groups"]
    kept, folded = series_order(groups, by, metric)
    names = kept + ([OTHER_NAME] if folded else [])
    colors = list(SERIES[:len(kept)]) + ([OTHER] if folded else [])
    xs = buckets(doc["from"], doc["to"], step)
    cell = {}
    for g in groups:
        name = g[by] if g[by] in kept else OTHER_NAME
        key = (g[step], name)
        cell[key] = cell.get(key, 0) + metric_of(g, metric)
    totals = [sum(cell.get((x, n), 0) for n in names) for x in xs]
    top = nice_max(max(totals or [0]))
    plot_w, plot_h = W - PAD_L - PAD_R, H - PAD_T - PAD_B
    slot = plot_w / max(1, len(xs))
    bar_w = max(2.0, min(36.0, slot * 0.7))
    parts = []
    for i in range(5):                                   # zurückhaltendes Raster
        v = top * i / 4
        y = PAD_T + plot_h - plot_h * i / 4
        parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{y:.1f}" y2="{y:.1f}" '
                     f'stroke="#e5e7eb" stroke-width="1"/>'
                     f'<text x="{PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" '
                     f'class="ax">{esc(fmt_metric(v, metric))}</text>')
    every = max(1, -(-len(xs) // 12))
    for i, x in enumerate(xs):
        cx = PAD_L + slot * i + slot / 2
        y = PAD_T + plot_h
        # Hinter den Stücken eine unsichtbare Fläche über die ganze Spalte:
        # Wer daneben zeigt, bekommt die Summe — größer als jedes Stück.
        parts.append(f'<rect x="{PAD_L + slot * i:.1f}" y="{PAD_T}" width="{slot:.1f}" '
                     f'height="{plot_h}" fill="transparent"><title>{esc(x)}: '
                     f'{esc(fmt_metric(totals[i], metric))} insgesamt</title></rect>')
        stack = [(n, c, cell.get((x, n), 0)) for n, c in zip(names, colors)]
        stack = [s for s in stack if s[2] > 0]
        for j, (n, c, v) in enumerate(stack):
            h = plot_h * v / top
            y -= h
            title = f"{x} · {label(n)}: {fmt_metric(v, metric)}"
            # 2px Fuge zwischen den Stücken: die Fläche, nicht eine Linie.
            gap = 2 if j else 0
            if j == len(stack) - 1:
                parts.append(_bar_top(cx - bar_w / 2, y, bar_w, max(0.0, h - gap), c, title))
            else:
                parts.append(f'<rect x="{cx - bar_w / 2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" '
                             f'height="{max(0.0, h - gap):.1f}" fill="{c}">'
                             f'<title>{esc(title)}</title></rect>')
        if i % every == 0:
            text = x[5:] if step == "day" else x
            parts.append(f'<text x="{cx:.1f}" y="{H - 14}" text-anchor="middle" '
                         f'class="ax">{esc(text)}</text>')
    parts.append(f'<line x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + plot_h}" '
                 f'y2="{PAD_T + plot_h}" stroke="#9ca3af" stroke-width="1"/>')
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" class="chart" '
           f'aria-label="{esc(METRIC_LABELS[metric])} je {"Tag" if step == "day" else "Monat"}">'
           f'{"".join(parts)}</svg>')
    head = "".join(f"<th>{esc(label(n))}</th>" for n in names)
    rows = "".join(
        f"<tr><td>{esc(x)}</td>" + "".join(
            f'<td class="num">{esc(fmt_metric(cell.get((x, n), 0), metric))}</td>' for n in names)
        + f'<td class="num"><b>{esc(fmt_metric(totals[i], metric))}</b></td></tr>'
        for i, x in enumerate(xs) if totals[i])
    table = (f'<details><summary class="muted">als Tabelle</summary><div class="scroll">'
             f'<table><tr><th>{"Tag" if step == "day" else "Monat"}</th>{head}<th>Summe</th></tr>'
             f'{rows or "<tr><td class=muted>keine Aufrufe</td></tr>"}</table></div></details>')
    return legend([label(n) for n in names], colors) + svg + table


def top_list(doc, dim, metric, n=10):
    """Die Größten einer Dimension im Zeitraum — waagerechte Balken, einfarbig."""
    groups = sorted(doc["groups"], key=lambda g: (-metric_of(g, metric), label(g[dim])))
    if not groups:
        return '<p class="muted">Keine Aufrufe in diesem Zeitraum.</p>'
    shown, rest = groups[:n], groups[n:]
    peak = max(metric_of(g, metric) for g in shown) or 1
    total = sum(metric_of(g, metric) for g in groups) or 1
    rows = []
    for g in shown:
        v = metric_of(g, metric)
        rows.append(f'<div class="hbar" title="{esc(label(g[dim]))}: {esc(fmt_metric(v, metric))}">'
                    f'<span class="hl">{esc(label(g[dim]))}</span>'
                    f'<span class="ht"><i style="width:{100 * v / peak:.1f}%;'
                    f'background:{SERIES[0]}"></i></span>'
                    f'<span class="hv">{esc(fmt_metric(v, metric))} '
                    f'<small>{100 * v / total:.0f} %</small></span></div>')
    if rest:
        v = sum(metric_of(g, metric) for g in rest)
        rows.append(f'<p class="muted">und {len(rest)} weitere mit zusammen '
                    f'{esc(fmt_metric(v, metric))}</p>')
    return "".join(rows)


def outcome_list(doc):
    """Anteil der Ausgänge — hier wird `ok_no_counts` sichtbar."""
    groups = sorted(doc["groups"], key=lambda g: -g["calls"])
    total = sum(g["calls"] for g in groups)
    if not total:
        return '<p class="muted">Keine Aufrufe in diesem Zeitraum.</p>'
    rows = []
    for g in groups:
        color, mark = STATUS[outcome_status(g["outcome"])]
        share = 100 * g["calls"] / total
        rows.append(f'<div class="hbar" title="{esc(g["outcome"])}">'
                    f'<span class="hl"><b style="color:{color}">{mark}</b> '
                    f'{esc(outcome_word(g["outcome"]))} <code>{esc(g["outcome"])}</code></span>'
                    f'<span class="ht"><i style="width:{share:.1f}%;background:{color}"></i></span>'
                    f'<span class="hv">{fmt_num(g["calls"])} <small>{share:.0f} %</small></span></div>')
    return "".join(rows)
