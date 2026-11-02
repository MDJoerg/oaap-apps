"""Schlüssel, Verbrauch, Prüfspur — die Bücher des Gateways.

Drei Tabellen, eine SQLite-Datei unter dem deklarierten Mount:

- `keys`    — ausgestellte API-Schlüssel, **nur als SHA-256**
- `usage`   — je Anfrage eine Zeile: Zeit, Schlüssel, Alias, Quelle,
              Token-Zahlen, Bytes, Dauer, Ausgang und eine **Kopie des
              Kontexts** des Schlüssels. **Kein Inhalt.**
- `audit`   — Ausstellen, Widerrufen, Kontext ändern. Anfragen stehen
              hier nicht.

Die härteste Regel der Spec (§6) ist hier eine Eigenschaft der
Bauform, nicht eine Frage der Disziplin: **Es gibt keine Spalte, in
die ein Prompt passen würde.** Was nicht gespeichert werden kann, kann
auch nicht versehentlich in einer Sicherung landen.

Zur Abrechnung (§5): Dieses Gateway führt Buch über **die Schlüssel,
die es selbst ausgegeben hat**. Die Zahlen einer Bezugsquelle sind
deren Wahrheit; abgeglichen wird nichts. Gezählt wird Volumen, nie
Geld (RFC-0064): Preise und Rechnungen entstehen außerhalb.

Seit 0.5.1 (RFC-0065 Stufe 0) steht der Teil, den auch das MCP-Gateway
führt — Schlüssel, Kontext, Prüfspur, Rate —, in `gwcore/`. Hier bleibt,
was nur dieses Gateway hat: seine Tabellen, Modellnamen, Ampel, Token.
"""
from gwcore import context, ledger
from gwcore.context import (CONTEXT_FIELDS, LABEL_NAME, MAX_LABELS, MAX_TEXT,  # noqa: F401
                            labels_text, parse_labels)
from gwcore.ledger import (RateLimiter, audit, digest, find, keys, log, now,  # noqa: F401
                           revoke, update_context)

SCHEMA = """
CREATE TABLE IF NOT EXISTS keys (
  id            TEXT PRIMARY KEY,
  label         TEXT NOT NULL UNIQUE,
  digest        TEXT NOT NULL UNIQUE,
  created       TEXT NOT NULL,
  revoked       TEXT NOT NULL DEFAULT '',
  aliases       TEXT NOT NULL DEFAULT '',
  -- Die schlechteste Ampelfarbe, die dieser Schlüssel benutzen darf,
  -- und die Freigabe für personenbezogene Daten. Beides sind
  -- **Erklärungen** — das Gateway darf nicht in die Anfrage sehen und
  -- kann deshalb nur prüfen, was vorher gesagt wurde.
  ceiling       TEXT NOT NULL DEFAULT 'yellow',
  personal_data INTEGER NOT NULL DEFAULT 0,
  budget_tokens INTEGER NOT NULL DEFAULT 0,
  rate_per_min  INTEGER NOT NULL DEFAULT 0,
  owner         TEXT NOT NULL DEFAULT '',
  cost_center   TEXT NOT NULL DEFAULT '',
  project       TEXT NOT NULL DEFAULT '',
  account       TEXT NOT NULL DEFAULT 'default',
  tenant        TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS usage (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  time      TEXT NOT NULL,
  key_id    TEXT NOT NULL,
  alias     TEXT NOT NULL,
  supplier  TEXT NOT NULL DEFAULT '',
  model     TEXT NOT NULL DEFAULT '',
  in_tokens INTEGER,
  out_tokens INTEGER,
  ms        INTEGER NOT NULL DEFAULT 0,
  outcome   TEXT NOT NULL DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS usage_key ON usage(key_id, id DESC);
CREATE TABLE IF NOT EXISTS audit (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  time    TEXT NOT NULL,
  actor   TEXT NOT NULL,
  action  TEXT NOT NULL,
  subject TEXT NOT NULL,
  detail  TEXT NOT NULL DEFAULT ''
);
-- Seit 0.4 (RFC-0064 Stufe 2) die Wahrheit über Bezugsquellen und
-- Modellnamen; die Konfigurationsvariablen werden einmal übernommen und
-- danach nicht mehr gelesen. `credential` ist nur verschlüsselt
-- gespeichert (vault.py), nie im Klartext.
CREATE TABLE IF NOT EXISTS suppliers (
  name       TEXT PRIMARY KEY,
  url        TEXT NOT NULL,
  light      TEXT NOT NULL,
  credential TEXT NOT NULL DEFAULT '',
  created    TEXT NOT NULL,
  updated    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS aliases (
  name         TEXT PRIMARY KEY,
  targets      TEXT NOT NULL,
  order_listed INTEGER NOT NULL DEFAULT 0,
  created      TEXT NOT NULL,
  updated      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
  name  TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""

# Spalten, die nach 0.2 dazukamen (RFC-0064). Sie werden bei jedem Start
# nachgezogen, auf einer neuen Datei genauso wie auf einer bestehenden —
# es gibt also nur einen Weg zum Schema, nicht zwei.
ADDED_COLUMNS = (
    ("keys", "application", "TEXT NOT NULL DEFAULT ''"),
    ("keys", "labels", "TEXT NOT NULL DEFAULT ''"),
    # Bytes am Verbraucher: was der Schlüsselinhaber geschickt und
    # bekommen hat. NULL heißt „vor 0.3 gezählt, Bytes unbekannt“ — nicht
    # null Bytes.
    ("usage", "bytes_up", "INTEGER"),
    ("usage", "bytes_down", "INTEGER"),
    # Die Kopie des Kontexts zum Zeitpunkt der Anfrage (gwcore/context.py).
) + tuple(("usage", col, decl) for col, decl in context.USAGE_COLUMNS)

# Sichtbares Präfix. Manche fertigen Clients und SDKs erwarten einen
# Schlüssel in OpenAI-Form; `sk-` vorneweg erspart Anwendern eine
# Fehlersuche, die nichts mit uns zu tun hat.
KEY_PREFIX = "sk-oaap-"


def connect(path):
    # Serialisiert: Jede Anfrage läuft in einem eigenen Thread. Mit einer
    # geteilten Verbindung ohne Sperre gingen unter Last Messzeilen
    # verloren — gemessen 2026-11-02 (16 Threads, 422 von 3200 Zeilen).
    return ledger.connect_serialized(path, SCHEMA, migrate)


def migrate(db):
    """Zieht die Spalten aus ADDED_COLUMNS nach und füllt alte Zeilen einmal.

    Alte Messzeilen bekommen den Kontext, den ihr Schlüssel **jetzt**
    hat, und tragen `ctx_source='backfilled'`. Genauer geht es nicht:
    Der damalige Kontext wurde nie aufgeschrieben. Die Prüfspur hält
    fest, wann und wie viele Zeilen das betraf.
    """
    ledger.add_columns(db, ADDED_COLUMNS)
    count = context.backfill(db)
    if count:
        log(db, "migration", "usage.backfill", f"{count} Zeilen",
            "Kontext von vor 0.3 nachgetragen: der damals aktuelle des Schlüssels")


def issue(db, label, actor, aliases=(), ceiling="yellow", personal_data=False,
          budget_tokens=0, rate_per_min=0, owner="", cost_center="", project="",
          account="default", tenant="default", application="", labels=""):
    """Stellt einen Schlüssel aus und gibt ihn **einmalig** zurück (gwcore/ledger.py)."""
    extra = {"aliases": ",".join(aliases), "ceiling": ceiling or "yellow",
             "personal_data": 1 if personal_data else 0,
             "budget_tokens": int(budget_tokens or 0), "rate_per_min": int(rate_per_min or 0)}
    ctx = {"owner": owner, "account": account, "project": project,
           "cost_center": cost_center, "application": application, "labels": labels}
    detail = (f"aliases={','.join(aliases) or 'alle'} bis={ceiling or 'yellow'} "
              f"pbd={'freigegeben' if personal_data else 'nein'}")
    return ledger.issue(db, label, actor, KEY_PREFIX, extra, ctx, tenant, detail)


def record(db, key, alias, supplier, model, in_tokens, out_tokens, ms, outcome,
           bytes_up, bytes_down):
    """Eine Messzeile. `key` ist die Schlüsselzeile **vom Beginn der Anfrage**.

    Ihr Kontext wird kopiert, nicht verwiesen: Wer die Kostenstelle
    während eines langen Stroms ändert, ändert sie für die nächste
    Anfrage, nicht für diese.
    """
    cols = ["time", "key_id", "alias", "supplier", "model", "in_tokens", "out_tokens", "ms",
            "outcome", "bytes_up", "bytes_down"] + context.snapshot_columns()
    values = [now(), key["id"], alias, supplier, model, in_tokens, out_tokens, int(ms), outcome,
              int(bytes_up), int(bytes_down)] + context.snapshot_values(key)
    db.execute(f"INSERT INTO usage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               values)
    db.commit()


def spent(db, key_id):
    """Summe aus Ein- und Ausgabe-Token für die Budget-Prüfung."""
    row = db.execute(
        "SELECT COALESCE(SUM(COALESCE(in_tokens,0)+COALESCE(out_tokens,0)),0) AS t "
        "FROM usage WHERE key_id=?", (key_id,)).fetchone()
    return int(row["t"])


def totals(db):
    return {r["key_id"]: r for r in db.execute(
        "SELECT key_id, COUNT(*) AS calls, "
        "COALESCE(SUM(COALESCE(in_tokens,0)),0) AS in_tokens, "
        "COALESCE(SUM(COALESCE(out_tokens,0)),0) AS out_tokens, "
        "COALESCE(SUM(COALESCE(bytes_up,0)),0) AS bytes_up, "
        "COALESCE(SUM(COALESCE(bytes_down,0)),0) AS bytes_down, "
        "MAX(time) AS last FROM usage GROUP BY key_id")}


def recent(db, key_id=None, limit=50):
    if key_id:
        return list(db.execute(
            "SELECT * FROM usage WHERE key_id=? ORDER BY id DESC LIMIT ?", (key_id, limit)))
    return list(db.execute("SELECT * FROM usage ORDER BY id DESC LIMIT ?", (limit,)))
