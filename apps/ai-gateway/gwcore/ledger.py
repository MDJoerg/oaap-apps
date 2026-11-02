"""Schlüssel und Prüfspur — der gemeinsame Teil der Gateway-Bücher.

Ein Gateway stellt API-Schlüssel aus und speichert sie **nur als
SHA-256** (Hygiene wie beim Deploy-Token, RFC-0019). Wer den Wert
verliert, bekommt einen neuen — er ist nirgends nachlesbar.

Was je Gateway verschieden ist (welche Modelle, welche Sichten, welches
Budget), steht in der App: Sie gibt ihre eigenen Spalten als `extra` an
`issue()`. Gleich ist der Rest: Etikett, Ausstellen, Kontext ändern mit
altem und neuem Wert in der Prüfspur, Widerruf, Suche über den Hash,
die Rate je Schlüssel.
"""
import hashlib
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone

from . import context

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  time    TEXT NOT NULL,
  actor   TEXT NOT NULL,
  action  TEXT NOT NULL,
  subject TEXT NOT NULL,
  detail  TEXT NOT NULL DEFAULT ''
);
"""


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def connect(path, schema, migrate=None):
    """Öffnet die SQLite-Datei, legt das Schema an und zieht Spalten nach."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(schema)
    if migrate:
        migrate(db)
    db.commit()
    return db


def add_columns(db, added):
    """Zieht Spalten `(tabelle, spalte, deklaration)` nach, die noch fehlen.

    Bei jedem Start, auf einer neuen Datei genauso wie auf einer
    bestehenden — es gibt also nur einen Weg zum Schema, nicht zwei.
    """
    for table, column, decl in added:
        have = {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def log(db, actor, action, subject, detail=""):
    db.execute("INSERT INTO audit (time,actor,action,subject,detail) VALUES (?,?,?,?,?)",
               (now(), actor or "?", action, subject, detail))


def audit(db, limit=100):
    return list(db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)))


def issue(db, label, actor, prefix, extra, ctx_fields, tenant="default", detail=""):
    """Stellt einen Schlüssel aus und gibt `(wert, id)` zurück — den Wert **einmalig**.

    `extra` sind die Spalten der App (Name → Wert), `ctx_fields` die
    Kontextfelder wie im Formular. Die Prüfspur bekommt `detail`.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("Ein Schlüssel braucht ein Etikett — es ist das, was einen Widerruf sinnvoll macht.")
    if db.execute("SELECT 1 FROM keys WHERE label=?", (label,)).fetchone():
        raise ValueError(f"Das Etikett {label!r} ist schon vergeben. Erst widerrufen, dann neu ausstellen.")
    ctx = context.clean({name: ctx_fields.get(name, "") for name in context.CONTEXT_FIELDS})
    value = prefix + secrets.token_urlsafe(32)
    key_id = secrets.token_hex(6)
    cols = {"id": key_id, "label": label, "digest": digest(value), "created": now()}
    cols.update(extra)
    cols.update(ctx)
    cols["tenant"] = tenant or "default"
    db.execute(f"INSERT INTO keys ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               list(cols.values()))
    log(db, actor, "key.issue", label, detail)
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
    ctx = context.clean(fields)
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
    einem unbekannten Schlüssel, aber wir wollen den Widerruf in der
    Prüfspur wiederfinden können.
    """
    if not value:
        return None
    return db.execute("SELECT * FROM keys WHERE digest=?", (digest(value),)).fetchone()


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
