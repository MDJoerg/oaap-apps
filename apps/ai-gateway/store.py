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
import secrets

from gwcore import context, ledger
from gwcore.context import (CONTEXT_FIELDS, LABEL_NAME, MAX_LABELS, MAX_TEXT,  # noqa: F401
                            labels_text, parse_labels)
from gwcore.ledger import (RateLimiter, audit, digest, keys, log, now,  # noqa: F401
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
-- Seit 0.10.0 (RFC-0104 §3.1): was die Einrichtungstür angelegt hat --
-- wer (der Auslöser des Aufbaus), wann und ein Digest des gewünschten
-- Eintrags. Entfernt wird nur, was noch genau so ist.
CREATE TABLE IF NOT EXISTS setup_items (
  kind   TEXT NOT NULL,
  id     TEXT NOT NULL,
  actor  TEXT NOT NULL,
  time   TEXT NOT NULL,
  digest TEXT NOT NULL,
  PRIMARY KEY (kind, id)
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
) + tuple(("usage", col, decl) for col, decl in context.USAGE_COLUMNS) + (
    # 0.6 (Spec 0.5 §2): Ablauf eines Schlüssels (UTC-Zeitstempel, leer =
    # nie) und die Kohorte, aus der er stammt (leer = einzeln ausgestellt).
    # Ein abgelaufener Schlüssel antwortet wie ein widerrufener.
    ("keys", "expires", "TEXT NOT NULL DEFAULT ''"),
    ("keys", "cohort", "TEXT NOT NULL DEFAULT ''"),
    # 0.6.1: temperature/top_p/top_k für diese Quelle nicht weitergeben —
    # neuere Claude-Modelle lehnen sie mit 400 ab, viele Clients schicken sie.
    ("suppliers", "drop_sampling", "INTEGER NOT NULL DEFAULT 0"),
    # 0.9.0 (RFC-0101 §2.2): die Ampel, die die Anfrage als Decke verlangte
    # (X-OAAP-Light), leer ohne. Ein Wort aus einer festen Liste, kein Inhalt.
    ("usage", "light", "TEXT NOT NULL DEFAULT ''"),
    # 0.10.0 (RFC-0104 §4): `key` = ein API-Schlüssel wie bisher, `client` =
    # die Freigabe für einen Chat derselben Plattform. Eine Freigabe hat
    # keinen benutzbaren Digest (siehe grant()) und wird nie über einen
    # Bearer-Wert gefunden (siehe find()).
    ("keys", "kind", "TEXT NOT NULL DEFAULT 'key'"),
)

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
          account="default", tenant="default", application="", labels="", expires="",
          cohort=""):
    """Stellt einen Schlüssel aus und gibt ihn **einmalig** zurück (gwcore/ledger.py)."""
    if expires and expires <= now():
        raise ValueError("Das Ablaufdatum liegt in der Vergangenheit — so ein Schlüssel "
                         "wäre schon beim Ausstellen ungültig.")
    extra = {"aliases": ",".join(aliases), "ceiling": ceiling or "yellow",
             "personal_data": 1 if personal_data else 0,
             "budget_tokens": int(budget_tokens or 0), "rate_per_min": int(rate_per_min or 0),
             "expires": expires or "", "cohort": cohort or ""}
    ctx = {"owner": owner, "account": account, "project": project,
           "cost_center": cost_center, "application": application, "labels": labels}
    detail = (f"aliases={','.join(aliases) or 'alle'} bis={ceiling or 'yellow'} "
              f"pbd={'freigegeben' if personal_data else 'nein'}"
              + (f" ablauf={expires}" if expires else "") + (f" kohorte={cohort}" if cohort else ""))
    return ledger.issue(db, label, actor, KEY_PREFIX, extra, ctx, tenant, detail)


def find(db, value):
    """Ein Schlüssel über seinen Bearer-Wert -- **nie** eine Freigabe für einen Chat.

    Doppelt verriegelt: Der Digest einer Freigabe ist `client:<etikett>`,
    kein SHA-256 aus 64 Hexzeichen, also passt kein Wert darauf; und selbst
    wenn eine Zeile der Art `client` gefunden würde, gibt es sie hier nicht.
    Ein leerer Wert findet nie etwas.
    """
    if not value:
        return None
    row = ledger.find(db, value)
    if row is None or row["kind"] == "client":
        return None
    return row


CLIENT_DIGEST = "client:"


def grant(db, label, actor, aliases=(), ceiling="yellow", personal_data=False):
    """Die Freigabe für einen Chat (RFC-0104 §4): eine Zeile der Art `client` in `keys`.

    Etikett = Name der Chat-Instanz. Es gibt keinen Wert, der zu ihr passt --
    sie gilt nur für einen Aufruf durch die via-Tür der Plattform, die
    `X-OAAP-AI-Client` selbst setzt. Modellnamen, Ampel, Freigabe für
    personenbezogene Daten, Budget und Rate wirken wie bei einem Schlüssel.
    """
    label = (label or "").strip()
    if not label:
        raise ValueError("Eine Freigabe braucht den Namen der Chat-Instanz.")
    if db.execute("SELECT 1 FROM keys WHERE label=?", (label,)).fetchone():
        raise ValueError(f"Das Etikett {label!r} ist schon vergeben.")
    key_id = secrets.token_hex(6)
    names = ",".join(sorted(dict.fromkeys(a for a in aliases if a)))
    db.execute("INSERT INTO keys (id,label,digest,created,aliases,ceiling,personal_data,kind) "
               "VALUES (?,?,?,?,?,?,?,'client')",
               (key_id, label, CLIENT_DIGEST + label, now(), names, ceiling or "yellow",
                1 if personal_data else 0))
    log(db, actor, "client.grant", label,
        f"aliases={names or 'alle'} bis={ceiling or 'yellow'} "
        f"pbd={'freigegeben' if personal_data else 'nein'}")
    db.commit()
    return key_id


def grant_of(db, client):
    """Die Freigabe für die Chat-Instanz `client`, oder None. Nur Zeilen der Art `client`."""
    if not client:
        return None
    return db.execute("SELECT * FROM keys WHERE label=? AND kind='client'", (client,)).fetchone()


def expired(row, at=None):
    """Abgelaufen? Ein Schlüssel ohne Ablauf läuft nie ab."""
    return bool(row["expires"]) and row["expires"] <= (at or now())


def usable(row, at=None):
    return not row["revoked"] and not expired(row, at)


def cohorts(db):
    """Je Kohorte: Anzahl, davon nutzbar, spätester Ablauf, erste Ausgabe."""
    at = now()
    return list(db.execute(
        "SELECT cohort, COUNT(*) AS seats, "
        "SUM(CASE WHEN revoked='' AND (expires='' OR expires>?) THEN 1 ELSE 0 END) AS live, "
        "MAX(expires) AS expires, MIN(created) AS created, MAX(project) AS project "
        "FROM keys WHERE cohort!='' GROUP BY cohort ORDER BY MIN(created) DESC", (at,)))


def cohort_keys(db, name):
    return list(db.execute("SELECT * FROM keys WHERE cohort=? ORDER BY label", (name,)))


def extend_cohort(db, name, expires, actor):
    """Neuer Ablauf für alle nicht widerrufenen Schlüssel einer Kohorte — auch abgelaufene
    leben damit wieder auf. Gibt die Anzahl zurück."""
    if not expires or expires <= now():
        raise ValueError("Das neue Ende muss in der Zukunft liegen.")
    cur = db.execute("UPDATE keys SET expires=? WHERE cohort=? AND revoked=''", (expires, name))
    count = cur.rowcount
    log(db, actor, "cohort.extend", name, f"ablauf={expires} schluessel={count}")
    db.commit()
    return count


def revoke_cohort(db, name, actor):
    """Widerruft alle noch gültigen Schlüssel einer Kohorte; gibt die Etiketten zurück."""
    labels = [r["label"] for r in cohort_keys(db, name) if not r["revoked"]]
    for label in labels:
        revoke(db, label, actor)
    log(db, actor, "cohort.revoke", name, f"schluessel={len(labels)}")
    db.commit()
    return labels


def record(db, key, alias, supplier, model, in_tokens, out_tokens, ms, outcome,
           bytes_up, bytes_down, light=""):
    """Eine Messzeile. `key` ist die Schlüsselzeile **vom Beginn der Anfrage**.

    Ihr Kontext wird kopiert, nicht verwiesen: Wer die Kostenstelle
    während eines langen Stroms ändert, ändert sie für die nächste
    Anfrage, nicht für diese.
    """
    cols = ["time", "key_id", "alias", "supplier", "model", "in_tokens", "out_tokens", "ms",
            "outcome", "bytes_up", "bytes_down", "light"] + context.snapshot_columns()
    values = [now(), key["id"], alias, supplier, model, in_tokens, out_tokens, int(ms), outcome,
              int(bytes_up), int(bytes_down),
              light if light in ("green", "yellow", "red") else ""] + context.snapshot_values(key)
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

def set_aliases(db, label, actor, aliases):
    """Ändert die Modellnamen eines gültigen Schlüssels (0.8.0, L26).

    Bis 0.7 ließen sie sich nur mit einem neuen Schlüssel ändern -- und
    ein neuer Schlüssel heißt, das Geheimnis beim Verbraucher zu tauschen.
    Ampel und Freigabe für personenbezogene Daten bleiben, was beim
    Ausstellen entschieden wurde (§2): sie begrenzen, wohin ein Modellname
    führen darf, und gelten damit auch für jeden hinzugefügten.
    Gibt (alt, neu) zurück; gleich, wenn sich nichts änderte.
    """
    row = db.execute("SELECT * FROM keys WHERE label=?", (label,)).fetchone()
    if not row:
        raise ValueError(f"Einen Schlüssel „{label}“ gibt es nicht.")
    if row["revoked"]:
        raise ValueError(f"„{label}“ ist widerrufen; dafür ändert niemand mehr Modellnamen.")
    if row["cohort"]:
        raise ValueError(f"„{label}“ gehört zur Kohorte „{row['cohort']}“; "
                         "deren Schlüssel haben alle dieselben Modellnamen.")
    new = ",".join(sorted(dict.fromkeys(a for a in aliases if a)))
    old = row["aliases"] or ""
    if ",".join(sorted(a for a in old.split(",") if a)) == new:
        return old, old
    db.execute("UPDATE keys SET aliases=? WHERE label=?", (new, label))
    log(db, actor, "key.aliases", label, f"{old or 'alle'} → {new or 'alle'}")
    db.commit()
    return old, new
