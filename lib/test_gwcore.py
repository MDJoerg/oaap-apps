#!/usr/bin/env python3
"""gwcore für sich: was ein zweites Gateway braucht, das ai-gateway nicht prüft.

Die Bücher des KI-Gateways prüfen dessen eigene Tests (apps/ai-gateway),
und die laufen seit Stufe 0 über gwcore. Hier steht, was erst mit einer
zweiten App zählt: getrennte Tresore bei gleichem Hauptschlüssel, eine
Abfrage mit anderen Dimensionen und Kennzahlen, Schlüssel mit anderen
eigenen Spalten.

Run: python3 lib/test_gwcore.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gwcore import charts, context, ledger, report, vault  # noqa: E402

ok = fail = 0
TMP = tempfile.mkdtemp(prefix="gwcore-")


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


print("-- Tresor: ein Zweck je App")
MASTER = "derselbe-hauptschluessel-in-zwei-apps"
ai = vault.Vault(b"oaap-ai-gateway-vault|", "AIGW_MASTER_KEY")
mcp = vault.Vault(b"oaap-mcp-gateway-vault|", "MCPGW_MASTER_KEY")
token = mcp.seal(MASTER, "geheim")
check("die eigene App öffnet", mcp.open_(MASTER, token) == "geheim")
try:
    ai.open_(MASTER, token)
    check("die andere App öffnet NICHT, trotz gleichem Hauptschlüssel", False)
except vault.VaultError as exc:
    check("die andere App öffnet NICHT, trotz gleichem Hauptschlüssel", True)
    check("und die Meldung nennt ihre eigene Variable", "AIGW_MASTER_KEY" in str(exc), exc)
try:
    mcp.seal("kurz", "x")
    check("zu kurzer Hauptschlüssel wird abgelehnt", False)
except vault.VaultError as exc:
    check("zu kurzer Hauptschlüssel wird abgelehnt, mit dem Namen der Variablen",
          "MCPGW_MASTER_KEY" in str(exc), exc)

print("\n-- Bücher eines zweiten Gateways: andere eigene Spalten")
SCHEMA = ledger.AUDIT_SCHEMA + """
CREATE TABLE IF NOT EXISTS keys (
  id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, digest TEXT NOT NULL UNIQUE,
  created TEXT NOT NULL, revoked TEXT NOT NULL DEFAULT '',
  views TEXT NOT NULL DEFAULT '', max_effect TEXT NOT NULL DEFAULT 'read',
  owner TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT 'default',
  project TEXT NOT NULL DEFAULT '', cost_center TEXT NOT NULL DEFAULT '',
  application TEXT NOT NULL DEFAULT '', labels TEXT NOT NULL DEFAULT '',
  tenant TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, key_id TEXT NOT NULL,
  view TEXT NOT NULL, tool TEXT NOT NULL, outcome TEXT NOT NULL
);
"""
ADDED = tuple(("usage", c, d) for c, d in context.USAGE_COLUMNS)
db = ledger.connect(os.path.join(TMP, "mcp", "gw.db"), SCHEMA,
                    lambda d: ledger.add_columns(d, ADDED))
value, key_id = ledger.issue(db, "agent", "test", "mcp-oaap-",
                             {"views": "sap-lesen", "max_effect": "read"},
                             {"cost_center": "KS1", "labels": "team=a"}, detail="views=sap-lesen")
check("der Wert trägt das Präfix der App", value.startswith("mcp-oaap-"))
row = ledger.find(db, value)
check("gefunden über den Hash, eigene Spalte gesetzt",
      row and row["id"] == key_id and row["views"] == "sap-lesen", dict(row) if row else None)
check("der Wert selbst steht nirgends in der Datei",
      not any(value in str(tuple(r)) for r in db.execute("SELECT * FROM keys")))
check("leeres Konto wird default", row["account"] == "default")
check("Prüfspur mit dem Detail der App",
      [(r["action"], r["detail"]) for r in ledger.audit(db)] == [("key.issue", "views=sap-lesen")])


def record(key, view, tool, outcome, when):
    cols = ["time", "key_id", "view", "tool", "outcome"] + context.snapshot_columns()
    db.execute(f"INSERT INTO usage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               [when, key["id"], view, tool, outcome] + context.snapshot_values(key))


record(row, "sap-lesen", "read_class", "ok", "2026-11-01T10:00:00Z")
ledger.update_context(db, "agent", "test", cost_center="KS2")
record(ledger.find(db, value), "sap-lesen", "read_class", "refused", "2026-11-01T11:00:00Z")
record(ledger.find(db, value), "sap-lesen", "search", "ok", "2026-11-02T09:00:00Z")
db.commit()

print("\n-- Die eine Abfrage mit anderen Dimensionen und Kennzahlen")
R = report.Report({**report.CONTEXT_DIMENSIONS, "view": "u.view", "tool": "u.tool",
                   "outcome": "u.outcome", **report.TIME_DIMENSIONS},
                  [("calls", "COUNT(*)"),
                   ("refused", "SUM(CASE WHEN u.outcome='refused' THEN 1 ELSE 0 END)")])
NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)
doc = R.usage(db, "2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z", ["cost_center", "tool"], now=NOW)
check("gruppiert über die KOPIE: die alte Kostenstelle behält ihren Aufruf",
      [(g["cost_center"], g["tool"], g["calls"]) for g in doc["groups"]]
      == [("KS1", "read_class", 1), ("KS2", "read_class", 1), ("KS2", "search", 1)], doc["groups"])
check("eigene Kennzahl gezählt", doc["totals"] == {"calls": 3, "refused": 1}, doc["totals"])
check("CSV mit den Kennzahlen der App",
      R.to_csv(doc).splitlines()[0] == "cost_center,tool,calls,refused", R.to_csv(doc))
try:
    R.parse_query([("from", "2026-11"), ("to", "2026-12"), ("model", "x")])
    check("eine Dimension des KI-Gateways ist hier unbekannt", False)
except ValueError as exc:
    check("eine Dimension des KI-Gateways ist hier unbekannt", "model" in str(exc), exc)
check("Filter auf eine eigene Dimension",
      R.usage(db, "2026-11-01", "2026-11-03", [], {"tool": ["search"]}, now=NOW)["totals"]["calls"] == 1)

print("\n-- Grafiken aus derselben Abfrage")
C = charts.Charts({"calls": "Aufrufe", "refused": "Abgelehnt"},
                  lambda o: "good" if o == "ok" else "critical", lambda o: o)
svg = C.stacked_bars(R.usage(db, "2026-11-01T00:00:00Z", "2026-11-03T00:00:00Z",
                             ["day", "tool"], now=NOW), "day", "tool", "calls")
check("Balken mit der Bezeichnung der App", 'aria-label="Aufrufe je Tag"' in svg)
check("Tabelle darunter nennt die Werkzeuge", "read_class" in svg and "search" in svg)
check("Ausgänge mit der Bedeutung der App",
      "refused" in C.outcome_list(R.usage(db, "2026-11-01", "2026-11-03", ["outcome"], now=NOW)))

db.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
