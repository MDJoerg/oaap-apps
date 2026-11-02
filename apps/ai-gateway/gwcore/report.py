"""Die eine Abfrage hinter Grafiken und Schnittstelle (RFC-0064 Stufe 3).

Die Grafiken auf der Betreiber-Seite und `GET /report/v1/usage` lesen
**beide über `Report.usage()`**. Läsen sie über zwei Abfragen,
widersprächen sich Bild und Rechnung eines Tages, und niemand könnte
sagen, welches recht hat.

Gezählt wird Volumen, nie Geld. Was eine Zahl nicht kennt, sagt sie:
Eine App kann Kennzahlen der Form `calls_without_…` führen, die Zeilen
zählen, deren Beitrag zur Summe eine ehrliche Null ist, keine gemessene.

Gruppiert und gefiltert wird über die **Kopie des Kontexts** in der
Messzeile (`ctx_*`), nicht über den heutigen Kontext des Schlüssels:
Eine geänderte Kostenstelle verschiebt keine alten Monate.

Welche Dimensionen und Kennzahlen es gibt, sagt die App; dieses Modul
weiß nicht, ob es Token oder Tool-Aufrufe zählt.
"""
import csv
import io
import re
from datetime import datetime, timedelta, timezone

# Die Dimensionen, die jedes Gateway hat. Name nach außen → Ausdruck in
# der Tabelle `usage u` (mit `keys k`). `key` meint das Etikett:
# eindeutig und nie wiederverwendet, weil ein widerrufener Schlüssel sein
# Etikett behält.
CONTEXT_DIMENSIONS = {
    "account": "u.ctx_account",
    "project": "u.ctx_project",
    "cost_center": "u.ctx_cost_center",
    "application": "u.ctx_application",
    "owner": "u.ctx_owner",
    "key": "COALESCE(k.label, u.key_id)",
}
TIME_DIMENSIONS = {
    "day": "substr(u.time,1,10)",
    "month": "substr(u.time,1,7)",
}

# Eine Messzeile bekommt ihre Zeit beim Schreiben, also am ENDE der
# Anfrage (auch ein Strom wird erst nach seinem letzten Stück gebucht).
# Eine Anfrage, die über Mitternacht läuft, zählt deshalb zum Tag, an dem
# sie endet — und ein Zeitraum wächst nach seinem Ende nicht mehr, bis
# auf das kurze Stück zwischen Zeitstempel und Speichern. Dafür steht
# dieser Abstand, großzügig über der Wartezeit der Datenbank (10 s).
SETTLE_SECONDS = 30

UNIT_NOTE = "Volumen, keine Preise. Zeit = Ende der Anfrage (UTC)."

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


class Report:
    """Die Abfrage eines Gateways.

    `dimensions`: Name → SQL-Ausdruck (Reihenfolge = Reihenfolge in
    Meldungen). `metrics`: Liste `(name, SQL-Ausdruck)`, jeder Ausdruck
    eine Summe über `usage u`.
    """

    def __init__(self, dimensions, metrics,
                 source="usage u LEFT JOIN keys k ON k.id = u.key_id"):
        self.dimensions = dict(dimensions)
        self.metric_sql = list(metrics)
        self.metrics = tuple(name for name, _ in metrics)
        self.params = {"from", "to", "group_by", "format"} | set(self.dimensions)
        self.source = source

    def parse_query(self, pairs):
        """Prüft die Parameter der Schnittstelle. `pairs` = Liste (name, wert).

        Wirft ValueError mit einem Satz. Ein unbekannter Parameter ist ein
        Fehler, keine Kleinigkeit: Ein vertippter Filter (`cost_centre=`)
        würde sonst still entfallen, und der Bericht zählte alles.
        """
        frm = to = None
        group_by, filters, fmt = [], {}, "json"
        for name, value in pairs:
            if name not in self.params:
                raise ValueError(f"Unbekannter Parameter „{name}“. Bekannt: "
                                 f"{', '.join(sorted(self.params))}.")
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
                    if dim not in self.dimensions:
                        raise ValueError(f"Nach „{dim}“ kann nicht gruppiert werden. Möglich: "
                                         f"{', '.join(self.dimensions)}.")
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

    def usage(self, db, frm, to, group_by=(), filters=None, now=None):
        """Die eine Abfrage. `from` einschließlich, `to` ausschließlich.

        Gibt ein Dokument zurück, das die Schnittstelle unverändert als JSON
        ausliefert und aus dem die Grafiken zeichnen.
        """
        if frm >= to:
            raise ValueError("from muss vor to liegen.")
        filters = filters or {}
        where, args = ["u.time >= ?", "u.time < ?"], [frm, to]
        for name, values in sorted(filters.items()):
            if name not in self.dimensions:
                raise ValueError(f"Nach „{name}“ kann nicht gefiltert werden.")
            where.append(f"{self.dimensions[name]} IN ({','.join('?' * len(values))})")
            args.extend(values)
        cols = [f"{self.dimensions[d]} AS g{i}" for i, d in enumerate(group_by)]
        sql = ("SELECT " + ", ".join(cols + [f"{expr} AS {name}" for name, expr in self.metric_sql])
               + f" FROM {self.source} WHERE " + " AND ".join(where))
        if group_by:
            order = ", ".join(f"g{i}" for i in range(len(group_by)))
            sql += f" GROUP BY {order} ORDER BY {order}"
        groups = []
        for row in db.execute(sql, args):
            if not group_by and not row["calls"]:
                continue
            item = {d: row[f"g{i}"] for i, d in enumerate(group_by)}
            item.update({m: int(row[m] or 0) for m in self.metrics})
            groups.append(item)
        # Die Summe aus den Gruppen, nicht aus einer zweiten Abfrage: Sie kann
        # dann gar nicht anders lauten als die Zeilen darüber.
        totals = {m: sum(g[m] for g in groups) for m in self.metrics}
        until = complete_until(now)
        return {"from": frm, "to": to, "group_by": list(group_by),
                "filters": {k: v for k, v in sorted(filters.items())},
                "complete_until": until, "complete": to <= until,
                "groups": groups, "totals": totals,
                "unit_note": UNIT_NOTE}

    def to_csv(self, doc):
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(doc["group_by"] + list(self.metrics))
        for g in doc["groups"]:
            writer.writerow([g[d] for d in doc["group_by"]] + [g[m] for m in self.metrics])
        return out.getvalue()

    def values_of(self, db, dim, frm=None, to=None):
        """Die vorkommenden Werte einer Dimension — für die Auswahllisten der Seite."""
        sql = f"SELECT DISTINCT {self.dimensions[dim]} AS v FROM {self.source}"
        args = []
        if frm and to:
            sql += " WHERE u.time >= ? AND u.time < ?"
            args = [frm, to]
        return [r["v"] for r in db.execute(sql + " ORDER BY v", args)]
