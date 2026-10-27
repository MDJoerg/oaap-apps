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
"""
import hashlib
import json
import os
import re
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
    # Die Kopie des Kontexts zum Zeitpunkt der Anfrage. Wie ein Beleg die
    # Kontierung behält, mit der er gebucht wurde: Eine später geänderte
    # Kostenstelle verschiebt keine alten Monate.
    ("usage", "ctx_owner", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_account", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_project", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_cost_center", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_application", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_tenant", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "ctx_labels", "TEXT NOT NULL DEFAULT ''"),
    # `snapshot` = beim Aufruf mitgeschrieben; `backfilled` = Zeile von
    # vor 0.3, einmalig mit dem damals aktuellen Kontext des Schlüssels
    # gefüllt. Eine Rekonstruktion, die sagt, dass sie eine ist.
    ("usage", "ctx_source", "TEXT NOT NULL DEFAULT ''"),
)

# Der Kontext eines Schlüssels — das, wonach ausgewertet und außerhalb
# verrechnet wird. `tenant` gehört nicht dazu: Den füllt RFC-0022, nicht
# der Betreiber von Hand.
CONTEXT_FIELDS = ("owner", "account", "project", "cost_center", "application", "labels")
MAX_LABELS = 5
MAX_TEXT = 120
LABEL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

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
    migrate(db)
    db.commit()
    return db


def migrate(db):
    """Zieht die Spalten aus ADDED_COLUMNS nach und füllt alte Zeilen einmal.

    Alte Messzeilen bekommen den Kontext, den ihr Schlüssel **jetzt**
    hat, und tragen `ctx_source='backfilled'`. Genauer geht es nicht:
    Der damalige Kontext wurde nie aufgeschrieben. Die Prüfspur hält
    fest, wann und wie viele Zeilen das betraf.
    """
    for table, column, decl in ADDED_COLUMNS:
        have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    cur = db.execute(
        "UPDATE usage SET "
        "ctx_owner=COALESCE((SELECT owner FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_account=COALESCE((SELECT account FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_project=COALESCE((SELECT project FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_cost_center=COALESCE((SELECT cost_center FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_application=COALESCE((SELECT application FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_tenant=COALESCE((SELECT tenant FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_labels=COALESCE((SELECT labels FROM keys WHERE keys.id=usage.key_id),''),"
        "ctx_source='backfilled' WHERE ctx_source=''")
    if cur.rowcount:
        log(db, "migration", "usage.backfill", f"{cur.rowcount} Zeilen",
            "Kontext von vor 0.3 nachgetragen: der damals aktuelle des Schlüssels")


def parse_labels(text):
    """`name=wert`, getrennt durch Komma oder Zeilenumbruch → JSON-Text.

    Wirft ValueError mit einem Satz, der den Fehler behebt. Leer ist
    erlaubt und ergibt ''.
    """
    labels = {}
    for part in re.split(r"[,\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not LABEL_NAME.match(name):
            raise ValueError(f"Etikett „{part}“ hat nicht die Form name=wert "
                             "(Name: Buchstaben, Ziffern, _ . -, höchstens 32 Zeichen).")
        if len(value) > MAX_TEXT:
            raise ValueError(f"Der Wert von „{name}“ ist länger als {MAX_TEXT} Zeichen.")
        labels[name] = value
    if len(labels) > MAX_LABELS:
        raise ValueError(f"Höchstens {MAX_LABELS} freie Etiketten je Schlüssel, "
                         f"angegeben waren {len(labels)}.")
    return json.dumps(labels, ensure_ascii=False, sort_keys=True) if labels else ""


def labels_text(stored):
    """Die gespeicherte Form zurück in `name=wert, name=wert`."""
    try:
        doc = json.loads(stored) if stored else {}
    except ValueError:
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(doc.items()))


def _clean_context(fields):
    out = {}
    for name in CONTEXT_FIELDS:
        if name not in fields:
            continue
        if name == "labels":
            out[name] = parse_labels(fields[name])
            continue
        value = (fields[name] or "").strip()
        if len(value) > MAX_TEXT:
            raise ValueError(f"„{name}“ ist länger als {MAX_TEXT} Zeichen.")
        if name == "account":
            value = value or "default"
        out[name] = value
    return out


def issue(db, label, actor, aliases=(), ceiling="yellow", personal_data=False,
          budget_tokens=0, rate_per_min=0, owner="", cost_center="", project="",
          account="default", tenant="default", application="", labels=""):
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
    ctx = _clean_context({"owner": owner, "account": account, "project": project,
                          "cost_center": cost_center, "application": application,
                          "labels": labels})
    value = KEY_PREFIX + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(6)
    db.execute(
        "INSERT INTO keys (id,label,digest,created,aliases,ceiling,personal_data,"
        "budget_tokens,rate_per_min,owner,cost_center,project,account,tenant,"
        "application,labels) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (key_id, label, digest(value), now(), ",".join(aliases), ceiling or "yellow",
         1 if personal_data else 0,
         int(budget_tokens or 0), int(rate_per_min or 0), ctx["owner"], ctx["cost_center"],
         ctx["project"], ctx["account"], tenant or "default", ctx["application"],
         ctx["labels"]))
    log(db, actor, "key.issue", label,
        f"aliases={','.join(aliases) or 'alle'} bis={ceiling or 'yellow'} "
        f"pbd={'freigegeben' if personal_data else 'nein'}")
    db.commit()
    return value, key_id


def update_context(db, label, actor, **fields):
    """Ändert den Kontext eines gültigen Schlüssels — wirksam ab der nächsten Anfrage.

    Gibt die Liste der tatsächlich geänderten Felder zurück. Jede
    Änderung steht mit altem und neuem Wert in der Prüfspur; schon
    gezählte Anfragen behalten ihre Kopie (RFC-0064 §3).
    """
    row = db.execute("SELECT * FROM keys WHERE label=?", (label,)).fetchone()
    if not row:
        raise ValueError(f"Einen Schlüssel „{label}“ gibt es nicht.")
    if row["revoked"]:
        raise ValueError(f"„{label}“ ist widerrufen; es kommen keine Anfragen mehr, "
                         "denen ein neuer Kontext etwas nützen würde.")
    ctx = _clean_context(fields)
    changed = [name for name, value in ctx.items() if row[name] != value]
    if not changed:
        return []
    db.execute(f"UPDATE keys SET {', '.join(n + '=?' for n in changed)} WHERE label=?",
               [ctx[n] for n in changed] + [label])
    log(db, actor, "key.context", label,
        "; ".join(f"{n}: {row[n] or '—'} → {ctx[n] or '—'}" for n in changed))
    db.commit()
    return changed


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


def record(db, key, alias, supplier, model, in_tokens, out_tokens, ms, outcome,
           bytes_up, bytes_down):
    """Eine Messzeile. `key` ist die Schlüsselzeile **vom Beginn der Anfrage**.

    Ihr Kontext wird kopiert, nicht verwiesen: Wer die Kostenstelle
    während eines langen Stroms ändert, ändert sie für die nächste
    Anfrage, nicht für diese.
    """
    db.execute(
        "INSERT INTO usage (time,key_id,alias,supplier,model,in_tokens,out_tokens,ms,outcome,"
        "bytes_up,bytes_down,ctx_owner,ctx_account,ctx_project,ctx_cost_center,"
        "ctx_application,ctx_tenant,ctx_labels,ctx_source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'snapshot')",
        (now(), key["id"], alias, supplier, model, in_tokens, out_tokens, int(ms), outcome,
         int(bytes_up), int(bytes_down), key["owner"], key["account"], key["project"],
         key["cost_center"], key["application"], key["tenant"], key["labels"]))
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
