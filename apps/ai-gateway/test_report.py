#!/usr/bin/env python3
"""Die eine Abfrage hinter Grafiken und Schnittstelle (RFC-0064 Stufe 3).

Ohne HTTP: Messzeilen mit gesetzter Zeit, damit die Grenzen eines
Zeitraums genau getroffen werden können — eine Sekunde vor, auf und
nach Mitternacht. Die HTTP-Seite (Rolle, CSV, Seite = Schnittstelle)
prüft test_gateway.py.

Run: python3 apps/ai-gateway/test_report.py
"""
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import charts  # noqa: E402
import report  # noqa: E402
import store  # noqa: E402

ok = fail = 0
TMP = tempfile.mkdtemp(prefix="aigw-report-")


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


db = store.connect(os.path.join(TMP, "gateway.db"))
store.issue(db, "kurs", "test", cost_center="KS-1", application="webtop", project="ADT")
store.issue(db, "n8n", "test", cost_center="KS-2", application="n8n")
KEYS = {r["label"]: r for r in store.keys(db)}


def row(time, label, tokens=(10, 5), bytes_=(100, 200), outcome="ok", alias="chat",
        supplier="lokal", source="snapshot"):
    k = KEYS[label]
    db.execute(
        "INSERT INTO usage (time,key_id,alias,supplier,model,in_tokens,out_tokens,ms,outcome,"
        "bytes_up,bytes_down,ctx_owner,ctx_account,ctx_project,ctx_cost_center,"
        "ctx_application,ctx_tenant,ctx_labels,ctx_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (time, k["id"], alias, supplier, "m", tokens[0], tokens[1], 1, outcome,
         bytes_[0], bytes_[1], k["owner"], k["account"], k["project"], k["cost_center"],
         k["application"], k["tenant"], k["labels"], source))


row("2026-09-30T23:59:59Z", "kurs")                    # September
row("2026-10-01T00:00:00Z", "kurs")                    # die erste Sekunde im Oktober
row("2026-10-15T12:00:00Z", "kurs", tokens=(None, None), outcome="ok_no_counts")
row("2026-10-20T08:00:00Z", "n8n", bytes_=(None, None), source="backfilled")
row("2026-10-31T23:59:59Z", "n8n", outcome="budget", tokens=(None, None))
row("2026-11-01T00:00:00Z", "n8n")                     # schon November
db.commit()

print("-- Zeitraum: from einschließlich, to ausschließlich")
okt = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z")
check("vier Aufrufe im Oktober, nicht drei, nicht sechs", okt["totals"]["calls"] == 4,
      okt["totals"])
sep = report.usage(db, "2026-09-01T00:00:00Z", "2026-10-01T00:00:00Z")
check("die letzte Sekunde des Septembers zählt zum September", sep["totals"]["calls"] == 1,
      sep["totals"])
nov = report.usage(db, report.parse_time("2026-11", "from"), report.parse_time("2026-12", "to"))
check("Mitternacht gehört zum neuen Monat", nov["totals"]["calls"] == 1, nov["totals"])
check("jede Zeile genau einmal: Sep + Okt + Nov = alle",
      sep["totals"]["calls"] + okt["totals"]["calls"] + nov["totals"]["calls"] == 6)

print("\n-- Was eine Zahl nicht kennt, sagt sie")
t = okt["totals"]
check("Token: nur aus Zeilen mit Zahlen", (t["in_tokens"], t["out_tokens"]) == (20, 10), t)
check("calls_without_token_counts zählt die Zeilen ohne Zahlen (auch Abweisungen)",
      t["calls_without_token_counts"] == 2, t)
check("calls_without_bytes zählt die Zeile von vor 0.3", t["calls_without_bytes"] == 1, t)
check("Bytes: unbekannt ist nicht null, wird aber auch nicht geschätzt",
      (t["bytes_up"], t["bytes_down"]) == (300, 600), t)

print("\n-- Gruppieren nach der Kopie des Kontexts, nicht nach dem heutigen")
store.update_context(db, "kurs", "test", cost_center="KS-NEU")
by_cc = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", ["cost_center"])
names = {g["cost_center"]: g["calls"] for g in by_cc["groups"]}
check("die Oktober-Aufrufe bleiben bei KS-1, obwohl der Schlüssel jetzt KS-NEU hat",
      names == {"KS-1": 2, "KS-2": 2}, names)
check("die Summe ist die Summe der Gruppen",
      by_cc["totals"]["calls"] == sum(g["calls"] for g in by_cc["groups"]) == 4)

print("\n-- Mehrere Dimensionen, Schlüssel als Etikett, Tag und Monat")
two = report.usage(db, "2026-09-01T00:00:00Z", "2026-12-01T00:00:00Z", ["key", "month"])
pairs = {(g["key"], g["month"]): g["calls"] for g in two["groups"]}
check("Schlüssel × Monat", pairs == {("kurs", "2026-09"): 1, ("kurs", "2026-10"): 2,
                                     ("n8n", "2026-10"): 2, ("n8n", "2026-11"): 1}, pairs)
days = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", ["day"])
check("je Tag", [g["day"] for g in days["groups"]]
      == ["2026-10-01", "2026-10-15", "2026-10-20", "2026-10-31"], days["groups"])

print("\n-- Filter")
f = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", [],
                 {"application": ["webtop"]})
check("nach Anwendung", f["totals"]["calls"] == 2, f["totals"])
f = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", [],
                 {"outcome": ["ok", "budget"]})
check("mehrere Werte heißen „einer davon“", f["totals"]["calls"] == 3, f["totals"])
f = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", [], {"project": [""]})
check("leer heißt „ohne Angabe“ — auch das ist eine Gruppe", f["totals"]["calls"] == 2, f["totals"])
f = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", [], {"key": ["kurs"]})
check("nach Schlüssel-Etikett", f["totals"]["calls"] == 2, f["totals"])
empty = report.usage(db, "2027-01-01T00:00:00Z", "2027-02-01T00:00:00Z")
check("ein leerer Zeitraum hat keine Gruppen und Nullen",
      empty["groups"] == [] and empty["totals"]["calls"] == 0, empty)

print("\n-- Parameter: ein Tippfehler ist ein Fehler, keine Kleinigkeit")
for label, pairs_ in (
        ("vertippter Filter", [("from", "2026-10"), ("to", "2026-11"), ("cost_centre", "KS-1")]),
        ("ohne from", [("to", "2026-11")]),
        ("unbekannte Gruppierung", [("from", "2026-10"), ("to", "2026-11"), ("group_by", "kunde")]),
        ("kein Datum", [("from", "Oktober"), ("to", "2026-11")]),
        ("fremdes Format", [("from", "2026-10"), ("to", "2026-11"), ("format", "xml")])):
    try:
        report.parse_query(pairs_)
        check(f"{label} wird abgelehnt", False)
    except ValueError as exc:
        check(f"{label} wird abgelehnt, mit Satz", len(str(exc)) > 10, str(exc))
try:
    report.usage(db, "2026-11-01T00:00:00Z", "2026-10-01T00:00:00Z")
    check("from nach to wird abgelehnt", False)
except ValueError:
    check("from nach to wird abgelehnt", True)
frm, to, gb, flt, fmt = report.parse_query(
    [("from", "2026-10-01"), ("to", "2026-11-01T00:00Z"), ("group_by", "cost_center,month"),
     ("account", "acme"), ("account", "beta"), ("format", "csv")])
check("eine gültige Anfrage wird verstanden",
      (frm, to, gb, flt, fmt) == ("2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z",
                                  ["cost_center", "month"], {"account": ["acme", "beta"]}, "csv"),
      (frm, to, gb, flt, fmt))

print("\n-- complete_until: wächst der Zeitraum noch?")
now = datetime(2026, 11, 1, 0, 0, 5, tzinfo=timezone.utc)
doc = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", now=now)
check("fünf Sekunden nach Mitternacht ist der Oktober noch nicht endgültig",
      doc["complete"] is False and doc["complete_until"] == "2026-10-31T23:59:35Z", doc)
later = datetime(2026, 11, 1, 0, 1, 0, tzinfo=timezone.utc)
doc = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", now=later)
check("eine Minute danach schon", doc["complete"] is True, doc["complete_until"])

print("\n-- CSV und Schlüsselliste")
csv_text = report.to_csv(by_cc)
lines = csv_text.strip().splitlines()
check("Kopfzeile: Gruppen, dann die Maße", lines[0] == "cost_center," + ",".join(report.METRICS),
      lines[0])
check("eine Zeile je Gruppe", len(lines) == 3 and lines[1].startswith("KS-1,2,"), lines)
kd = report.keys_doc(db, lambda raw: json.loads(raw) if raw else {})
blob = json.dumps(kd)
check("die Schlüsselliste nennt Etikett und heutigen Kontext",
      {k["key"]: k["cost_center"] for k in kd["keys"]} == {"kurs": "KS-NEU", "n8n": "KS-2"}, kd)
check("aber weder Prüfsumme noch Schlüsselwert",
      all(KEYS[l]["digest"] not in blob for l in KEYS) and store.KEY_PREFIX not in blob)
check("kein Preis, keine Währung in irgendeiner Antwort",
      not any(w in (blob + json.dumps(okt)).lower() for w in ("price", "cost\"", "eur", "currency")))

print("\n-- Grafiken zeichnen nur, was die Abfrage sagt")
over = report.usage(db, "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", ["day", "cost_center"])
svg = charts.stacked_bars(over, "day", "cost_center", "calls")
check("ein Balken je Tag des Zeitraums, auch ohne Aufruf (31 Spalten)",
      svg.count("insgesamt</title>") == 31, svg.count("insgesamt</title>"))
check("die Tabellenansicht trägt dieselben Summen",
      "<td>2026-10-01</td>" in svg and "<td>2026-10-02</td>" not in svg)
many = {"groups": [{"day": "2026-10-01", "cost_center": f"KS-{i}", "calls": 10 - i,
                    "in_tokens": 0, "out_tokens": 0} for i in range(10)],
        "from": "2026-10-01T00:00:00Z", "to": "2026-10-02T00:00:00Z"}
svg = charts.stacked_bars(many, "day", "cost_center", "calls")
check("mehr als sieben Reihen: sechs Farben plus „Andere“, keine erfundene achte",
      "Andere" in svg and svg.count('class="lg"') == 7, svg.count('class="lg"'))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
