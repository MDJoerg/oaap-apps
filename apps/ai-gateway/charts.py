"""Grafiken als SVG, auf dem Server gezeichnet (RFC-0064 §5).

Die Zeichnung steht seit 0.5.1 in `gwcore/charts.py` (RFC-0065 Stufe 0),
gemeinsam mit dem MCP-Gateway. Hier steht, was dieses Gateway zeigt:
welche Kennzahlen wählbar sind (Token = ein + aus) und was seine
Ausgänge bedeuten.

Gezeichnet wird ausschließlich, was `report.usage()` liefert.
"""
from gwcore import charts as _core
from gwcore.charts import (EMPTY_NAME, H, OTHER, OTHER_NAME, SERIES, STATUS,  # noqa: F401
                           W, buckets, fmt_num, label, legend, nice_max)

METRIC_LABELS = {"calls": "Aufrufe", "tokens": "Token (ein + aus)",
                 "bytes_up": "Bytes hoch", "bytes_down": "Bytes runter"}


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


_CHARTS = _core.Charts(METRIC_LABELS, outcome_status, outcome_word,
                       derived={"tokens": lambda g: g["in_tokens"] + g["out_tokens"]})

metric_of = _CHARTS.metric_of
fmt_metric = _CHARTS.fmt_metric
series_order = _CHARTS.series_order
stacked_bars = _CHARTS.stacked_bars
top_list = _CHARTS.top_list
outcome_list = _CHARTS.outcome_list
