"""Das Dashboard (0.6): der Verlauf der letzten Stunden auf einen Blick.

Die Auswertung (`/auswertung`, `report.usage()`) rechnet in Tagen und
Monaten und ist die Grundlage für Rechnungen. Das Dashboard fragt etwas
anderes: **Was passiert gerade?** — die letzten 24 Stunden je Stunde und
eine Rangliste für ein frei wählbares kurzes Fenster (1 h bis 30 Tage).
Dafür reicht eine Gruppierung über die Messzeilen; sie liest dieselbe
Tabelle `usage`, nur mit Stunden statt Tagen.

Gezählt wird wie überall im Gateway: Aufrufe, Token, Dauer, Ausgang —
keine Inhalte. Die Zeit einer Messzeile ist ihr Ende in UTC; gezeigt
wird sie in der Ortszeit (`Europe/Berlin`, wenn die Zeitzonendaten da
sind, sonst UTC — und dann steht „UTC“ dran).
"""
import html
from datetime import datetime, timedelta, timezone

from gwcore.charts import STATUS, SERIES, fmt_num

esc = html.escape

try:
    from zoneinfo import ZoneInfo
    LOCAL = ZoneInfo("Europe/Berlin")
    LOCAL_NAME = "Ortszeit"
except Exception:                      # keine Zeitzonendaten im Abbild
    LOCAL = timezone.utc
    LOCAL_NAME = "UTC"

FMT = "%Y-%m-%dT%H:%M:%SZ"

# Fenster der Rangliste: Name in der Adresse → (Stunden, Wort)
WINDOWS = {"1h": (1, "letzte Stunde"), "4h": (4, "letzte 4 Stunden"),
           "24h": (24, "letzte 24 Stunden"), "7d": (24 * 7, "letzte 7 Tage"),
           "30d": (24 * 30, "letzte 30 Tage")}
DEFAULT_WINDOW = "4h"

# Wonach gerankt und gefiltert wird → Ausdruck über `usage u LEFT JOIN keys k`
DIMS = {"model": ("u.alias", "Modellname"),
        "key": ("COALESCE(k.label, u.key_id)", "Schlüssel"),
        "supplier": ("u.supplier", "Bezugsquelle"),
        "outcome": ("u.outcome", "Ausgang")}

METRICS = {"calls": ("COUNT(*)", "Aufrufe"),
           "tokens": ("COALESCE(SUM(COALESCE(u.in_tokens,0)+COALESCE(u.out_tokens,0)),0)",
                      "Token (ein + aus)"),
           "ms": ("CAST(COALESCE(AVG(u.ms),0) AS INTEGER)", "mittlere Dauer")}
DEFAULT_METRIC = "calls"

ANSWERED = "u.outcome LIKE 'ok%'"


def stamp(dt):
    return dt.astimezone(timezone.utc).strftime(FMT)


def parse(text):
    try:
        return datetime.strptime(text, FMT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def local(text, with_date=True):
    """Gespeicherte UTC-Zeit → lesbare Ortszeit."""
    dt = parse(text)
    if dt is None:
        return text or ""
    dt = dt.astimezone(LOCAL)
    return dt.strftime("%d.%m. %H:%M:%S" if with_date else "%H:%M")


def ago(text, now=None):
    """„vor 3 Min“ — für die Kachel „letzte Anfrage“."""
    dt = parse(text)
    if dt is None:
        return "noch nie"
    secs = int(((now or datetime.now(timezone.utc)) - dt).total_seconds())
    if secs < 60:
        return "gerade eben"
    if secs < 3600:
        return f"vor {secs // 60} Min"
    if secs < 86400:
        return f"vor {secs // 3600} Std"
    return f"vor {secs // 86400} Tagen"


def _where(since, until=None, filters=None):
    parts, args = ["u.time >= ?"], [since]
    if until:
        parts.append("u.time < ?")
        args.append(until)
    for dim, value in sorted((filters or {}).items()):
        if dim in DIMS and value:
            parts.append(f"{DIMS[dim][0]} = ?")
            args.append(value)
    return " AND ".join(parts), args


FROM = "FROM usage u LEFT JOIN keys k ON k.id = u.key_id"


def totals(db, since, until=None, filters=None):
    """Summen eines Fensters: Aufrufe, Token, beantwortet, Dauer, Schlüssel."""
    where, args = _where(since, until, filters)
    row = db.execute(
        f"SELECT COUNT(*) AS calls, "
        f"COALESCE(SUM(COALESCE(u.in_tokens,0)),0) AS in_tokens, "
        f"COALESCE(SUM(COALESCE(u.out_tokens,0)),0) AS out_tokens, "
        f"COALESCE(SUM(CASE WHEN {ANSWERED} THEN 1 ELSE 0 END),0) AS answered, "
        f"CAST(COALESCE(AVG(CASE WHEN {ANSWERED} THEN u.ms END),0) AS INTEGER) AS avg_ms, "
        f"COUNT(DISTINCT u.key_id) AS keys, MAX(u.time) AS last "
        f"{FROM} WHERE {where}", args).fetchone()
    out = {k: row[k] for k in ("calls", "in_tokens", "out_tokens", "answered", "avg_ms",
                               "keys", "last")}
    for k in ("calls", "in_tokens", "out_tokens", "answered", "avg_ms", "keys"):
        out[k] = int(out[k] or 0)
    out["tokens"] = out["in_tokens"] + out["out_tokens"]
    out["failed"] = out["calls"] - out["answered"]
    return out


def top(db, since, dim, metric, filters=None, n=10):
    """[(name, value)] absteigend, höchstens n — und wie viele es insgesamt gab."""
    expr, metric_sql = DIMS[dim][0], METRICS[metric][0]
    where, args = _where(since, None, filters)
    rows = [(r["name"] or "", int(r["v"] or 0)) for r in db.execute(
        f"SELECT {expr} AS name, {metric_sql} AS v {FROM} WHERE {where} "
        f"GROUP BY {expr} ORDER BY v DESC, name LIMIT ?", args + [n + 1])]
    return rows[:n], len(rows) > n


def hourly(db, hours=24, now=None):
    """Je Stunde (UTC) der letzten `hours`: beantwortet, gescheitert, Token, Dauer."""
    now = (now or datetime.now(timezone.utc)).replace(minute=0, second=0, microsecond=0)
    first = now - timedelta(hours=hours - 1)
    got = {r["h"]: r for r in db.execute(
        f"SELECT substr(u.time,1,13) AS h, COUNT(*) AS calls, "
        f"SUM(CASE WHEN {ANSWERED} THEN 1 ELSE 0 END) AS answered, "
        f"COALESCE(SUM(COALESCE(u.in_tokens,0)+COALESCE(u.out_tokens,0)),0) AS tokens, "
        f"CAST(COALESCE(AVG(CASE WHEN {ANSWERED} THEN u.ms END),0) AS INTEGER) AS ms "
        f"{FROM} WHERE u.time >= ? GROUP BY h", [stamp(first)])}
    out = []
    for i in range(hours):
        h = first + timedelta(hours=i)
        r = got.get(h.strftime("%Y-%m-%dT%H"))
        calls = int(r["calls"]) if r else 0
        answered = int(r["answered"] or 0) if r else 0
        out.append({"start": h, "calls": calls, "answered": answered,
                    "failed": calls - answered, "tokens": int(r["tokens"]) if r else 0,
                    "ms": int(r["ms"] or 0) if r else 0})
    return out


def values(db, dim, since):
    """Werte einer Dimension im Fenster — für die Filterauswahl."""
    expr = DIMS[dim][0]
    return [r["v"] for r in db.execute(
        f"SELECT DISTINCT {expr} AS v {FROM} WHERE u.time >= ? AND {expr} != '' "
        f"ORDER BY v", [since]) if r["v"]]


# ------------------------------------------------------------------ Zeichnung

def sparkline(points, color=SERIES[0], w=200, h=44):
    """Kleine Linie mit Fläche für eine Kachel. Ohne Werte eine flache Linie."""
    if not points:
        points = [0]
    peak = max(points) or 1
    n = max(len(points) - 1, 1)
    xy = [(i * w / n, h - 3 - (v / peak) * (h - 8)) for i, v in enumerate(points)]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in xy)
    area = f"0,{h} " + line + f" {w},{h}"
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" '
            f'aria-hidden="true"><polygon points="{area}" fill="{color}" opacity=".14"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>'
            f'</svg>')


def ring(share, color, size=56):
    """Ein Ring für einen Anteil (0..1) — die Erfolgsquote."""
    r = size / 2 - 5
    c = 2 * 3.14159265 * r
    dash = max(0.0, min(1.0, share)) * c
    mid = size / 2
    return (f'<svg class="ring" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'aria-hidden="true"><circle cx="{mid}" cy="{mid}" r="{r:.1f}" fill="none" '
            f'stroke="#e5e7eb" stroke-width="7"/>'
            f'<circle cx="{mid}" cy="{mid}" r="{r:.1f}" fill="none" stroke="{color}" '
            f'stroke-width="7" stroke-linecap="round" stroke-dasharray="{dash:.1f} {c:.1f}" '
            f'transform="rotate(-90 {mid} {mid})"/></svg>')


def hour_bars(buckets, w=960, h=210):
    """Gestapelte Säulen je Stunde: beantwortet (grün) und gescheitert (rot)."""
    pad_l, pad_r, pad_t, pad_b = 44, 8, 10, 28
    peak = max((b["calls"] for b in buckets), default=0)
    top_v = _nice(peak)
    inner_w, inner_h = w - pad_l - pad_r, h - pad_t - pad_b
    slot = inner_w / max(len(buckets), 1)
    bw = max(2.0, slot * 0.72)
    good, bad = STATUS["good"][0], STATUS["critical"][0]
    out = [f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" '
           f'aria-label="Anfragen je Stunde, letzte {len(buckets)} Stunden">']
    for i in range(5):
        v = top_v * i / 4
        y = pad_t + inner_h - inner_h * i / 4
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'stroke="#e5e7eb" stroke-width="1"/>'
                   f'<text class="ax" x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end">'
                   f'{esc(fmt_num(int(v)))}</text>')
    for i, b in enumerate(buckets):
        x = pad_l + i * slot + (slot - bw) / 2
        y0 = pad_t + inner_h
        start = b["start"].astimezone(LOCAL)
        title = (f'{start.strftime("%d.%m. %H")}–{(start + timedelta(hours=1)).strftime("%H")} Uhr: '
                 f'{fmt_num(b["answered"])} beantwortet, {fmt_num(b["failed"])} gescheitert, '
                 f'{fmt_num(b["tokens"])} Token')
        for value, color in ((b["answered"], good), (b["failed"], bad)):
            if value <= 0:
                continue
            hh = inner_h * value / top_v
            y0 -= hh
            out.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw:.1f}" height="{hh:.1f}" '
                       f'rx="2" fill="{color}"><title>{esc(title)}</title></rect>')
        if b["calls"] == 0:
            out.append(f'<rect x="{x:.1f}" y="{pad_t}" width="{bw:.1f}" height="{inner_h}" '
                       f'fill="transparent"><title>{esc(title)}</title></rect>')
        if i % 3 == 0 or i == len(buckets) - 1:
            out.append(f'<text class="ax" x="{x + bw / 2:.1f}" y="{h - 8}" '
                       f'text-anchor="middle">{start.strftime("%H")}</text>')
    out.append("</svg>")
    return "".join(out)


def _nice(value):
    if value <= 4:
        return 4
    for step in (5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000):
        if value <= step * 4:
            return step * 4
    return value


def bar_list(rows, more, fmt, color=SERIES[0], empty="Keine Anfragen in diesem Fenster.",
             share=True):
    """Waagerechte Balken einer Rangliste, gleiche Optik wie die Auswertung."""
    if not rows:
        return f'<p class="muted empty">{esc(empty)}</p>'
    peak = max(v for _, v in rows) or 1
    total = sum(v for _, v in rows) or 1
    out = []
    for rank, (name, v) in enumerate(rows, 1):
        shown = name or "(ohne Angabe)"
        out.append(f'<div class="hbar" title="{esc(shown)}: {esc(fmt(v))}">'
                   f'<span class="hl"><span class="rank">{rank}</span> {esc(shown)}</span>'
                   f'<span class="ht"><i style="width:{100 * v / peak:.1f}%;background:{color}">'
                   f'</i></span><span class="hv">{esc(fmt(v))}'
                   + (f' <small>{100 * v / total:.0f} %</small>' if share else '')
                   + '</span></div>')
    if more:
        out.append('<p class="muted">… und weitere, gezeigt sind die ersten zehn.</p>')
    return "".join(out)
