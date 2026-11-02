"""Die eine Abfrage hinter Grafiken und Schnittstelle (RFC-0064 Stufe 3).

Die Grafiken auf der Betreiber-Seite und `GET /report/v1/usage` lesen
**beide über `usage()`**. Läsen sie über zwei Abfragen, widersprächen
sich Bild und Rechnung eines Tages, und niemand könnte sagen, welches
recht hat.

Gezählt wird Volumen, nie Geld: Aufrufe, Token (wenn die Quelle sie
nennt), Bytes am Verbraucher. Was eine Zahl nicht kennt, sagt sie:
`calls_without_token_counts` und `calls_without_bytes` zählen die
Zeilen, deren Summen-Beitrag eine ehrliche Null ist, keine gemessene.

Gruppiert und gefiltert wird über die **Kopie des Kontexts** in der
Messzeile (`ctx_*`), nicht über den heutigen Kontext des Schlüssels:
Eine geänderte Kostenstelle verschiebt keine alten Monate.

Die Abfrage selbst steht seit 0.5.1 in `gwcore/report.py` (RFC-0065
Stufe 0), gemeinsam mit dem MCP-Gateway. Hier steht, was dieses Gateway
zählt: Modellname und Quelle als Dimensionen, Token als Kennzahlen.
"""
from gwcore import report as _core
from gwcore.report import (SETTLE_SECONDS, complete_until, parse_time,  # noqa: F401
                           stamp)

# Name nach außen → Ausdruck in der Tabelle `usage u` (mit `keys k`).
DIMENSIONS = {
    **_core.CONTEXT_DIMENSIONS,
    "model": "u.alias",
    "supplier": "u.supplier",
    "outcome": "u.outcome",
    **_core.TIME_DIMENSIONS,
}
_REPORT = _core.Report(DIMENSIONS, [
    ("calls", "COUNT(*)"),
    ("in_tokens", "COALESCE(SUM(u.in_tokens),0)"),
    ("out_tokens", "COALESCE(SUM(u.out_tokens),0)"),
    ("calls_without_token_counts",
     "SUM(CASE WHEN u.in_tokens IS NULL AND u.out_tokens IS NULL THEN 1 ELSE 0 END)"),
    ("bytes_up", "COALESCE(SUM(u.bytes_up),0)"),
    ("bytes_down", "COALESCE(SUM(u.bytes_down),0)"),
    ("calls_without_bytes", "SUM(CASE WHEN u.bytes_up IS NULL THEN 1 ELSE 0 END)"),
])
METRICS = _REPORT.metrics
PARAMS = _REPORT.params

parse_query = _REPORT.parse_query
usage = _REPORT.usage
to_csv = _REPORT.to_csv
values_of = _REPORT.values_of


def keys_doc(db, labels_of):
    """`GET /report/v1/keys`: die Schlüssel mit ihrem heutigen Kontext, ohne Schlüsselwert.

    Zum Zuordnen im Abrechnungssystem. Abgerechnet wird trotzdem über die
    Kopie in den Messzeilen — dieser Kontext gilt erst ab heute.
    """
    out = []
    for row in db.execute("SELECT * FROM keys ORDER BY label"):
        out.append({"key": row["label"], "created": row["created"],
                    "revoked": row["revoked"] or None,
                    "owner": row["owner"], "account": row["account"],
                    "project": row["project"], "cost_center": row["cost_center"],
                    "application": row["application"], "tenant": row["tenant"],
                    "labels": labels_of(row["labels"]),
                    "models": [a for a in (row["aliases"] or "").split(",") if a] or None,
                    "ceiling": row["ceiling"], "personal_data_released": bool(row["personal_data"]),
                    "budget_tokens": int(row["budget_tokens"])})
    return {"keys": out}
