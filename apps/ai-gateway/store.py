"""Schlüssel, Verbrauch, Prüfspur — die Bücher des Gateways.

Drei Tabellen, eine SQLite-Datei unter dem deklarierten Mount:

- `keys`    — ausgestellte API-Schlüssel, **nur als SHA-256**
- `usage`   — je Anfrage eine Zeile: Zeit, Schlüssel, Alias, Quelle,
              Token-Zahlen, Dauer, Ausgang. **Kein Inhalt.**
- `audit`   — Ausstellen und Widerrufen. Anfragen stehen hier nicht.

Die härteste Regel der Spec (§6) ist hier eine Eigenschaft der
Bauform, nicht eine Frage der Disziplin: **Es gibt keine Spalte, in
die ein Prompt passen würde.** Was nicht gespeichert werden kann, kann
auch nicht versehentlich in einer Sicherung landen.

Zur Abrechnung (§5): Dieses Gateway führt Buch über **die Schlüssel,
die es selbst ausgegeben hat**. Die Zahlen einer Bezugsquelle sind
deren Wahrheit; abgeglichen wird nichts.
"""
import hashlib
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

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
"""

# Sichtbares Präfix. Manche fertigen Clients und SDKs erwarten einen
# Schlüssel in OpenAI-Form; `sk-` vorneweg erspart Anwendern eine
# Fehlersuche, die nichts mit uns zu tun hat.
KEY_PREFIX = "sk-oaap-"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connect(path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    db.commit()
    return db


def issue(db, label, actor, aliases=(), ceiling="yellow", personal_data=False,
          budget_tokens=0, rate_per_min=0, owner="", cost_center="", project="",
          account="default", tenant="default"):
    """Stellt einen Schlüssel aus und gibt ihn **einmalig** zurück.

    Gespeichert wird nur der SHA-256 (Hygiene wie beim Deploy-Token,
    RFC-0019). Wer den Wert verliert, bekommt einen neuen — er ist
    nirgends nachlesbar.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("Ein Schlüssel braucht ein Etikett — es ist das, was einen Widerruf sinnvoll macht.")
    if db.execute("SELECT 1 FROM keys WHERE label=?", (label,)).fetchone():
        raise ValueError(f"Das Etikett {label!r} ist schon vergeben. Erst widerrufen, dann neu ausstellen.")
    value = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(6)
    db.execute(
        "INSERT INTO keys (id,label,digest,created,aliases,ceiling,personal_data,"
        "budget_tokens,rate_per_min,owner,cost_center,project,account,tenant) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key_id, label, digest(value), now(), ",".join(aliases), ceiling or "yellow",
         1 if personal_data else 0,
         int(budget_tokens or 0), int(rate_per_min or 0), owner, cost_center,
         project, account or "default", tenant or "default"))
    log(db, actor, "key.issue", label,
        f"aliases={','.join(aliases) or 'alle'} bis={ceiling or 'yellow'} "
        f"pbd={'freigegeben' if personal_data else 'nein'}")
    db.commit()
    return value, key_id


def revoke(db, label, actor):
    row = db.execute("SELECT id,revoked FROM keys WHERE label=?", (label,)).fetchone()
    if not row:
        return False
    if row["revoked"]:
        return False
    db.execute("UPDATE keys SET revoked=? WHERE label=?", (now(), label))
    log(db, actor, "key.revoke", label)
    db.commit()
    return True


def keys(db, include_revoked=True):
    sql = "SELECT * FROM keys"
    if not include_revoked:
        sql += " WHERE revoked=''"
    return list(db.execute(sql + " ORDER BY created DESC"))


def find(db, value):
    """Sucht einen Schlüssel über seinen SHA-256.

    Widerrufene Schlüssel werden **gefunden und dann abgelehnt** —
    nicht übersehen: Der Aufrufer soll dieselbe Antwort geben wie bei
    einem unbekannten Schlüssel (§8), aber wir wollen den Widerruf in
    der Prüfspur wiederfinden können.
    """
    if not value:
        return None
    return db.execute("SELECT * FROM keys WHERE digest=?", (digest(value),)).fetchone()


def record(db, key_id, alias, supplier, model, in_tokens, out_tokens, ms, outcome):
    db.execute(
        "INSERT INTO usage (time,key_id,alias,supplier,model,in_tokens,out_tokens,ms,outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (now(), key_id, alias, supplier, model, in_tokens, out_tokens, int(ms), outcome))
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
        "MAX(time) AS last FROM usage GROUP BY key_id")}


def recent(db, key_id=None, limit=50):
    if key_id:
        return list(db.execute(
            "SELECT * FROM usage WHERE key_id=? ORDER BY id DESC LIMIT ?", (key_id, limit)))
    return list(db.execute("SELECT * FROM usage ORDER BY id DESC LIMIT ?", (limit,)))


def log(db, actor, action, subject, detail=""):
    db.execute("INSERT INTO audit (time,actor,action,subject,detail) VALUES (?,?,?,?,?)",
               (now(), actor or "?", action, subject, detail))


def audit(db, limit=100):
    return list(db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)))


class RateLimiter:
    """Anfragen je Schlüssel und Minute — im Speicher, absichtlich.

    Die Plattform bremst bereits je Client-Adresse (RFC-0010). Diese
    Bremse ist eine andere: Sie gilt dem **Schlüssel**, nicht der
    Herkunft, und schützt damit auch gegen einen Verbraucher, der aus
    wechselnden Adressen kommt. Ein Neustart setzt sie zurück; das ist
    vertretbar, weil das Budget die harte Grenze trägt.
    """

    def __init__(self):
        self._seen = {}

    def allow(self, key_id, per_minute):
        if not per_minute:
            return True, 0
        cutoff = time.monotonic() - 60
        hits = [t for t in self._seen.get(key_id, ()) if t > cutoff]
        if len(hits) >= per_minute:
            self._seen[key_id] = hits
            return False, max(1, int(60 - (time.monotonic() - hits[0])))
        hits.append(time.monotonic())
        self._seen[key_id] = hits
        return True, 0
