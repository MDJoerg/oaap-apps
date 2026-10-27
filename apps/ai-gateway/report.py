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
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone

# Name nach außen → Ausdruck in der Tabelle `usage u` (mit `keys k`).
# `key` meint das Etikett: eindeutig und nie wiederverwendet, weil ein
# widerrufener Schlüssel sein Etikett behält.
DIMENSIONS = {
    "account": "u.ctx_account",
    "project": "u.ctx_project",
    "cost_center": "u.ctx_cost_center",
    "application": "u.ctx_application",
    "owner": "u.ctx_owner",
    "key": "COALESCE(k.label, u.key_id)",
    "model": "u.alias",
    "supplier": "u.supplier",
    "outcome": "u.outcome",
    "day": "substr(u.time,1,10)",
    "month": "substr(u.time,1,7)",
}
METRICS = ("calls", "in_tokens", "out_tokens", "calls_without_token_counts",
           "bytes_up", "bytes_down", "calls_without_bytes")
PARAMS = {"from", "to", "group_by", "format"} | set(DIMENSIONS)

# Eine Messzeile bekommt ihre Zeit beim Schreiben, also am ENDE der
# Anfrage (auch ein Strom wird erst nach seinem letzten Stück gebucht).
# Eine Anfrage, die über Mitternacht läuft, zählt deshalb zum Tag, an dem
# sie endet — und ein Zeitraum wächst nach seinem Ende nicht mehr, bis
# auf das kurze Stück zwischen Zeitstempel und Speichern. Dafür steht
# dieser Abstand, großzügig über der Wartezeit der Datenbank (10 s).
SETTLE_SECONDS = 30

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH = re.compile(r"^\d{4}-\d{2}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z?$")


def stamp(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text, name):
    """`2026-10`, `2026-10-01` oder `2026-10-01T12:00:00Z` → gespeicherte Form (UTC)."""
    text = (text or "").strip()
    try:
        if _MONTH.match(text):
            return stamp(datetime.strptime(text, "%Y-%m"))
        if _DAY.match(text):
            return stamp(datetime.strptime(text, "%Y-%m-%d"))
        if _STAMP.match(text):
            raw = text.rstrip("Z")
            fmt = "%Y-%m-%dT%H:%M:%S" if raw.count(":") == 2 else "%Y-%m-%dT%H:%M"
            return stamp(datetime.strptime(raw, fmt))
    except ValueError:
        pass
    raise ValueError(f"„{name}“ ist kein Zeitpunkt. Erwartet wird 2026-10, 2026-10-01 "
                     "oder 2026-10-01T00:00:00Z (UTC).")


def complete_until(now=None):
    """Bis hierher sind die Zahlen endgültig."""
    now = now or datetime.now(timezone.utc)
    return stamp(now - timedelta(seconds=SETTLE_SECONDS))


def parse_query(pairs):
    """Prüft die Parameter der Schnittstelle. `pairs` = Liste (name, wert).

    Wirft ValueError mit einem Satz. Ein unbekannter Parameter ist ein
    Fehler, keine Kleinigkeit: Ein vertippter Filter (`cost_centre=`)
    würde sonst still entfallen, und der Bericht zählte alles.
    """
    frm = to = None
    group_by, filters, fmt = [], {}, "json"
    for name, value in pairs:
        if name not in PARAMS:
            raise ValueError(f"Unbekannter Parameter „{name}“. Bekannt: "
                             f"{', '.join(sorted(PARAMS))}.")
        if name == "from":
            frm = parse_time(value, "from")
        elif name == "to":
            to = parse_time(value, "to")
        elif name == "format":
            if value not in ("json", "csv"):
                raise ValueError("format ist json oder csv.")
            fmt = value
        elif name == "group_by":
            for dim in (d.strip() for d in value.split(",")):
                if not dim:
                    continue
                if dim not in DIMENSIONS:
                    raise ValueError(f"Nach „{dim}“ kann nicht gruppiert werden. Möglich: "
                                     f"{', '.join(DIMENSIONS)}.")
                if dim not in group_by:
                    group_by.append(dim)
        else:
            # Mehrfach genannt heißt „eines davon“; leer heißt „ohne Angabe“
            # — auch das ist eine Gruppe, die jemand abrechnen will.
            filters.setdefault(name, []).append(value)
    if not frm or not to:
        raise ValueError("from und to sind Pflicht — ein Abrechnungszeitraum wird genannt, "
                         "nicht vermutet (from einschließlich, to ausschließlich, UTC).")
    return frm, to, group_by, filters, fmt


def usage(db, frm, to, group_by=(), filters=None, now=None):
    """Die eine Abfrage. `from` einschließlich, `to` ausschließlich.

    Gibt ein Dokument zurück, das die Schnittstelle unverändert als JSON
    ausliefert und aus dem die Grafiken zeichnen.
    """
    if frm >= to:
        raise ValueError("from muss vor to liegen.")
    filters = filters or {}
    where, args = ["u.time >= ?", "u.time < ?"], [frm, to]
    for name, values in sorted(filters.items()):
        if name not in DIMENSIONS:
            raise ValueError(f"Nach „{name}“ kann nicht gefiltert werden.")
        where.append(f"{DIMENSIONS[name]} IN ({','.join('?' * len(values))})")
        args.extend(values)
    cols = [f"{DIMENSIONS[d]} AS g{i}" for i, d in enumerate(group_by)]
    sql = ("SELECT " + ", ".join(cols + [
        "COUNT(*) AS calls",
        "COALESCE(SUM(u.in_tokens),0) AS in_tokens",
        "COALESCE(SUM(u.out_tokens),0) AS out_tokens",
        "SUM(CASE WHEN u.in_tokens IS NULL AND u.out_tokens IS NULL THEN 1 ELSE 0 END)"
        " AS calls_without_token_counts",
        "COALESCE(SUM(u.bytes_up),0) AS bytes_up",
        "COALESCE(SUM(u.bytes_down),0) AS bytes_down",
        "SUM(CASE WHEN u.bytes_up IS NULL THEN 1 ELSE 0 END) AS calls_without_bytes"])
        + " FROM usage u LEFT JOIN keys k ON k.id = u.key_id WHERE " + " AND ".join(where))
    if group_by:
        order = ", ".join(f"g{i}" for i in range(len(group_by)))
        sql += f" GROUP BY {order} ORDER BY {order}"
    groups = []
    for row in db.execute(sql, args):
        if not group_by and not row["calls"]:
            continue
        item = {d: row[f"g{i}"] for i, d in enumerate(group_by)}
        item.update({m: int(row[m] or 0) for m in METRICS})
        groups.append(item)
    # Die Summe aus den Gruppen, nicht aus einer zweiten Abfrage: Sie kann
    # dann gar nicht anders lauten als die Zeilen darüber.
    totals = {m: sum(g[m] for g in groups) for m in METRICS}
    until = complete_until(now)
    return {"from": frm, "to": to, "group_by": list(group_by),
            "filters": {k: v for k, v in sorted(filters.items())},
            "complete_until": until, "complete": to <= until,
            "groups": groups, "totals": totals,
            "unit_note": "Volumen, keine Preise. Zeit = Ende der Anfrage (UTC)."}


def to_csv(doc):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(doc["group_by"] + list(METRICS))
    for g in doc["groups"]:
        writer.writerow([g[d] for d in doc["group_by"]] + [g[m] for m in METRICS])
    return out.getvalue()


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


def values_of(db, dim, frm=None, to=None):
    """Die vorkommenden Werte einer Dimension — für die Auswahllisten der Seite."""
    sql = f"SELECT DISTINCT {DIMENSIONS[dim]} AS v FROM usage u LEFT JOIN keys k ON k.id=u.key_id"
    args = []
    if frm and to:
        sql += " WHERE u.time >= ? AND u.time < ?"
        args = [frm, to]
    return [r["v"] for r in db.execute(sql + " ORDER BY v", args)]
