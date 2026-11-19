"""Die Bücher des MCP-Gateways — eine SQLite-Datei unter /data.

Was beide Gateways gleich führen (Schlüssel nur als SHA-256, Kontext,
Kopie des Kontexts in der Messzeile, Prüfspur, Rate), kommt aus
`gwcore/`. Hier steht, was nur dieses Gateway hat:

- `servers`  — MCP-Server: Adresse, Zugang (Zugangsdaten nur
               verschlüsselt), Ampel, Grenzen, zuletzt ausgehandelte
               Protokollversion
- `catalog`  — was jeder Server anbietet — Tools, Resources,
               Resource-Vorlagen, Prompts —, mit Fingerabdruck (Spec §4)
- `views`    — Sichten: Name = Pfad, höchste Klasse, Rückfragen
- `items`    — die freigegebenen Einträge einer Sicht, seit 0.2 aus
               beliebig vielen Servern, unter einem Namen der Sicht, mit
               dem **festgehaltenen** Text (Spec §4.2, §5)
- `usage`    — eine Zeile je `tools/call`, `resources/read`,
               `prompts/get` (Spec §9)
- `protocol` — Protokollverkehr, je Tag gezählt, nicht als Verbrauch

Die härteste Regel (Spec §10) ist auch hier eine Eigenschaft der
Bauform: **Es gibt keine Spalte, in die ein Argument oder ein Ergebnis
passen würde.**
"""
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from gwcore import context, ledger
from gwcore.context import CONTEXT_FIELDS, labels_text  # noqa: F401
from gwcore.ledger import (RateLimiter, audit, find, keys, log, now,  # noqa: F401
                           revoke, update_context)

SCHEMA = ledger.AUDIT_SCHEMA + """
CREATE TABLE IF NOT EXISTS keys (
  id            TEXT PRIMARY KEY,
  label         TEXT NOT NULL UNIQUE,
  digest        TEXT NOT NULL UNIQUE,
  created       TEXT NOT NULL,
  revoked       TEXT NOT NULL DEFAULT '',
  views         TEXT NOT NULL DEFAULT '',
  max_effect    TEXT NOT NULL DEFAULT 'read',
  ceiling       TEXT NOT NULL DEFAULT 'yellow',
  budget_calls  INTEGER NOT NULL DEFAULT 0,
  rate_per_min  INTEGER NOT NULL DEFAULT 0,
  owner         TEXT NOT NULL DEFAULT '',
  account       TEXT NOT NULL DEFAULT 'default',
  project       TEXT NOT NULL DEFAULT '',
  cost_center   TEXT NOT NULL DEFAULT '',
  application   TEXT NOT NULL DEFAULT '',
  labels        TEXT NOT NULL DEFAULT '',
  tenant        TEXT NOT NULL DEFAULT 'default'
);
CREATE TABLE IF NOT EXISTS usage (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  time        TEXT NOT NULL,
  key_id      TEXT NOT NULL,
  view        TEXT NOT NULL,
  tool        TEXT NOT NULL,
  server      TEXT NOT NULL DEFAULT '',
  server_tool TEXT NOT NULL DEFAULT '',
  procedure   TEXT NOT NULL DEFAULT 'T',
  outcome     TEXT NOT NULL,
  reason      TEXT NOT NULL DEFAULT '',
  ms          INTEGER NOT NULL DEFAULT 0,
  bytes_up    INTEGER NOT NULL DEFAULT 0,
  bytes_down  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS usage_key ON usage(key_id, id DESC);
-- 0.22 (RFC-0097 Stufe 1): Assistenten liegen im Gateway, neben den Sichten, die
-- sie nennen. `assistants` hält die geltende Revision, `assistant_revisions` jede
-- (nur anhängen; verdichtet wird nach D6, die Zeile bleibt, ihr Text geht).
CREATE TABLE IF NOT EXISTS assistants (
  id          TEXT PRIMARY KEY,
  rev         INTEGER NOT NULL,
  doc         TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'active',
  created     TEXT NOT NULL,
  changed_at  TEXT NOT NULL,
  changed_by  TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS assistant_revisions (
  id          TEXT NOT NULL,
  rev         INTEGER NOT NULL,
  doc         TEXT NOT NULL,
  changed_at  TEXT NOT NULL,
  changed_by  TEXT NOT NULL DEFAULT '',
  compacted   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (id, rev)
);
-- 0.23 (RFC-0101 Stufe 2): Sterne je Antwort und je Gespräch, mit den Umständen.
-- Kein Name, keine Kennung: `who` ist ein HMAC der Person mit einem Schlüssel dieses
-- Gateways (D3), `slot` ein HMAC von Gespräch, Umfang und Runde -- dieselbe Person
-- ersetzt ihre Bewertung, statt eine zweite anzulegen. `text` nur mit Haken (D4).
CREATE TABLE IF NOT EXISTS ratings (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  assistant   TEXT NOT NULL,
  rev         INTEGER NOT NULL,
  who         TEXT NOT NULL,
  slot        TEXT NOT NULL,
  scope       TEXT NOT NULL,
  round       INTEGER NOT NULL DEFAULT 0,
  stars       INTEGER NOT NULL,
  model       TEXT NOT NULL DEFAULT '',
  model_light TEXT NOT NULL DEFAULT '',
  light       TEXT NOT NULL DEFAULT '',
  view        TEXT NOT NULL DEFAULT '',
  gateway     TEXT NOT NULL DEFAULT '',
  tools       TEXT NOT NULL DEFAULT '[]',
  ms          INTEGER NOT NULL DEFAULT 0,
  at          TEXT NOT NULL,
  text        TEXT NOT NULL DEFAULT '',
  UNIQUE (assistant, who, slot)
);
-- 0.25 (RFC-0101 Stufe 4): dass zu einer Revision ein Vorgang „Nicht zufrieden“ ging --
-- gezählt, nicht beschrieben. `slot` ist ein HMAC von Person und Vorgangsnummer: derselbe
-- Vorgang zählt einmal, die Nummer selbst steht hier nicht.
CREATE TABLE IF NOT EXISTS assistant_cases (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  assistant   TEXT NOT NULL,
  rev         INTEGER NOT NULL,
  who         TEXT NOT NULL,
  slot        TEXT NOT NULL,
  at          TEXT NOT NULL,
  UNIQUE (assistant, slot)
);
CREATE TABLE IF NOT EXISTS protocol (
  day     TEXT NOT NULL,
  key_id  TEXT NOT NULL,
  view    TEXT NOT NULL,
  method  TEXT NOT NULL,
  count   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, key_id, view, method)
);
CREATE TABLE IF NOT EXISTS servers (
  name        TEXT PRIMARY KEY,
  address     TEXT NOT NULL,
  auth_kind   TEXT NOT NULL DEFAULT 'none',
  credential  TEXT NOT NULL DEFAULT '',
  light       TEXT NOT NULL DEFAULT 'red',
  limits      TEXT NOT NULL DEFAULT '{}',
  version     TEXT NOT NULL DEFAULT '',
  last_read   TEXT NOT NULL DEFAULT '',
  last_error  TEXT NOT NULL DEFAULT '',
  created     TEXT NOT NULL,
  updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS personal_credentials (
  iss         TEXT NOT NULL,
  sub         TEXT NOT NULL,
  server      TEXT NOT NULL,
  sealed      TEXT NOT NULL,
  stored      TEXT NOT NULL,
  last_used   TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (iss, sub, server)
);
CREATE TABLE IF NOT EXISTS person_links (
  iss          TEXT NOT NULL,
  sub          TEXT NOT NULL,
  platform_sub TEXT NOT NULL,
  seen         TEXT NOT NULL,
  PRIMARY KEY (iss, sub)
);
CREATE TABLE IF NOT EXISTS views (
  name        TEXT PRIMARY KEY,
  server      TEXT NOT NULL DEFAULT '',
  max_effect  TEXT NOT NULL DEFAULT 'read',
  created     TEXT NOT NULL,
  updated     TEXT NOT NULL
);
""" + """
CREATE TABLE IF NOT EXISTS catalog (
  server      TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'tool',
  name        TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  doc         TEXT NOT NULL,
  present     INTEGER NOT NULL DEFAULT 1,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  PRIMARY KEY (server, kind, name)
);
CREATE TABLE IF NOT EXISTS items (
  view         TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'tool',
  name         TEXT NOT NULL,
  server       TEXT NOT NULL,
  source       TEXT NOT NULL,
  effect       TEXT NOT NULL DEFAULT 'read',
  description  TEXT NOT NULL DEFAULT '',
  fixed        TEXT NOT NULL DEFAULT '{}',
  bounds       TEXT NOT NULL DEFAULT '{}',
  approved_fp  TEXT NOT NULL,
  approved_doc TEXT NOT NULL,
  approved_by  TEXT NOT NULL,
  approved_at  TEXT NOT NULL,
  PRIMARY KEY (view, kind, name)
);
"""

# Rückfragen vom Server an den Client, je Sicht (Spec §6.1). Eine Sicht aus
# 0.1 bekommt beim Nachziehen „alles ablehnen“ — so hat sie sich bisher
# verhalten, und ein Update soll das nicht still ändern. Neue Sichten
# bekommen die Vorgabe des RFC (REQUESTS_NEW).
REQUESTS = ("elicitation", "roots", "sampling")
REQUESTS_OLD = '{"elicitation": "refuse", "roots": "refuse", "sampling": "refuse"}'
REQUESTS_NEW = {"elicitation": "pass", "roots": "pass", "sampling": "refuse"}
REQUEST_METHODS = {"elicitation/create": "elicitation", "roots/list": "roots",
                   "sampling/createMessage": "sampling"}

ADDED_COLUMNS = tuple(("usage", col, decl) for col, decl in context.USAGE_COLUMNS) + (
    ("usage", "kind", "TEXT NOT NULL DEFAULT 'tool'"),
    ("views", "requests", f"TEXT NOT NULL DEFAULT '{REQUESTS_OLD}'"),
    # 0.3 (RFC-0065 Stufe 3): Zugangsprofile stehen neben den Schlüsseln —
    # dieselben Bücher, derselbe Kontext, derselbe Widerruf. Ein Profil hat
    # keinen benutzbaren Wert; `kind` sorgt dafür, dass es nie als Schlüssel gilt.
    ("keys", "kind", "TEXT NOT NULL DEFAULT 'key'"),
    ("keys", "groups", "TEXT NOT NULL DEFAULT ''"),
    # Welche Wege eine Sicht annimmt. Sichten aus 0.2 behalten `key`.
    ("views", "ways", "TEXT NOT NULL DEFAULT 'key'"),
    # Verfahren X: Token-Endpunkt, eigener Client, Ziel-Audience (Spec §3.5).
    ("servers", "exchange", "TEXT NOT NULL DEFAULT '{}'"),
    # Der Mensch hinter einem angemeldeten Aufruf: sub und Issuer (D16), nie ein Name.
    ("usage", "user_sub", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "user_iss", "TEXT NOT NULL DEFAULT ''"),
    # 0.4 (RFC-0073 Stufe 1): ein Server, den das Gateway aus der Quellenliste
    # der Plattform eingetragen hat, nennt seine Instanz; verschwindet sie aus
    # der Liste, steht hier wann — die Einträge in den Sichten bleiben sichtbar.
    ("servers", "source", "TEXT NOT NULL DEFAULT ''"),
    ("servers", "gone", "TEXT NOT NULL DEFAULT ''"),
    # 0.7 (RFC-0074 Stufe 1, Verfahren A): durch welchen Chat die Plattform den
    # Menschen trug, und welcher Assistent dort sprach — beides nur zur
    # Auskunft, keine Entscheidung hängt daran (RFC-0074 §5.5).
    ("usage", "client", "TEXT NOT NULL DEFAULT ''"),
    ("usage", "assistant", "TEXT NOT NULL DEFAULT ''"),
    # 0.7.2 (RFC-0074 §5.6): `yes`, wenn der Chat sagt, die Person habe diesen
    # Aufruf bestätigt -- eine Angabe des Chats, keine Prüfung des Gateways.
    ("usage", "confirmed", "TEXT NOT NULL DEFAULT ''"),
    # 0.15 (RFC-0095 Stufe 1, D3): wer abgelehnt hat und mit welchem HTTP-Status --
    # zwei strukturierte Spalten, damit /me den Grund in Worten sagen kann. Eine
    # Zahl und ein Wort aus einer festen Liste: Kein Inhalt passt hinein (§10).
    ("usage", "status", "INTEGER NOT NULL DEFAULT 0"),
    ("usage", "door", "TEXT NOT NULL DEFAULT ''"),
    # 0.16 (RFC-0095 Stufe 2, Verfahren U): wie der persönliche Zugang einer
    # Person zum Server geht -- {send, prefix, hint, link}. Kein Wert darin.
    ("servers", "personal", "TEXT NOT NULL DEFAULT '{}'"),
    # 0.21 (RFC-0101 §2.2): die Ampel, die die Anfrage als Decke verlangte
    # (X-OAAP-Light), leer ohne. Ein Wort aus einer festen Liste, kein Inhalt (§10).
    ("usage", "light", "TEXT NOT NULL DEFAULT ''"),
    # 0.22 (RFC-0097 Stufe 1): die Revision des Assistenten, die sprach
    # (X-OAAP-Assistant: <id>@<rev>), 0 = ohne Angabe.
    ("usage", "assistant_rev", "INTEGER NOT NULL DEFAULT 0"),
)
DOORS = ("gateway", "platform", "destination", "server")
# Zugänge einer Sicht: Schlüssel, Anmeldung am Anmeldedienst (Verfahren X/I) und
# seit 0.7 die Plattform (Verfahren A: ein Chat trägt den Menschen, RFC-0074).
WAY_PARTS = ("key", "signin", "platform")
WAYS = ("key", "signin", "key,signin", "platform", "key,platform", "signin,platform",
        "key,signin,platform")
_WAY_WORD = {"key": "Schlüssel", "signin": "Anmeldung", "platform": "Plattform (Chat)"}
WAY_WORDS = {w: " und ".join(_WAY_WORD[p] for p in w.split(",")) for w in WAYS}
# Gruppen eines Zugangsprofils: volle Pfade aus dem Token (/sap-dev), * für alle
# angemeldeten Menschen des Issuers — und seit 0.7 @<chat-instanz> bzw. @* für
# Menschen, die die Plattform durch einen Chat trägt (Verfahren A).
GROUP = re.compile(r"^(\*|/[^,]{1,200}|@\*|@[a-z0-9][a-z0-9._-]{0,62})$")

KEY_PREFIX = "mcp-oaap-"
EFFECTS = ("read", "write", "execute")
EFFECT_RANK = {e: i for i, e in enumerate(EFFECTS)}
EFFECT_WORDS = {"read": "lesen", "write": "schreiben", "execute": "ausführen"}
LIGHTS = ("green", "yellow", "red")
LIGHT_RANK = {e: i for i, e in enumerate(LIGHTS)}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
AUTH_KINDS = ("none", "bearer", "basic", "exchange", "platform", "propagate", "personal")
# Zugänge, die einen angemeldeten Menschen brauchen: X (Tausch zum Server),
# I (Tausch zur Plattform, RFC-0073) und P (die Person über eine Destination bis
# ins Zielsystem, RFC-0066, 0.12). Nie in einer Sicht, die Schlüssel annimmt.
# 0.16: U (personal) -- jede Person bringt ihren eigenen Zugang mit (RFC-0095).
PERSON_KINDS = ("exchange", "platform", "propagate", "personal")
UNUSED_DAYS = 180                    # RFC-0095 D8: danach auf der Betreiberseite markiert
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def connect(path):
    # Viele Threads, eine Datei: jede Anweisung unter einer Sperre (gwcore.ledger.Serialized).
    return ledger.connect_serialized(path, SCHEMA, migrate)


def migrate(db):
    ledger.add_columns(db, ADDED_COLUMNS)
    # 0.15: /me fragt je Person (RFC-0095).
    db.execute("CREATE INDEX IF NOT EXISTS usage_person ON usage(user_iss, user_sub, id DESC)")
    _rebuild_01(db)


def _columns(db, table):
    return {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}


def _rebuild_01(db):
    """Katalog und Einträge aus 0.1 in die Form 0.2 bringen — einmal, ohne eine Freigabe zu verlieren.

    In 0.1 gehörte eine Sicht genau einem Server, und ein Eintrag war ein
    Tool unter seinem eigenen Namen. Jetzt trägt jeder Eintrag seinen
    Server, seine Art und seinen Namen in der Sicht. Fingerabdruck und
    festgehaltener Text bleiben Byte für Byte, damit kein Tool durch das
    Update gesperrt wird.
    """
    if "kind" not in _columns(db, "catalog"):
        db.execute("ALTER TABLE catalog RENAME TO catalog_01")
        db.executescript(SCHEMA)
        db.execute("INSERT INTO catalog (server,kind,name,fingerprint,doc,present,first_seen,last_seen) "
                   "SELECT server,'tool',name,fingerprint,doc,present,first_seen,last_seen FROM catalog_01")
        db.execute("DROP TABLE catalog_01")
    if "kind" not in _columns(db, "items"):
        db.execute("ALTER TABLE items RENAME TO items_01")
        db.executescript(SCHEMA)
        moved = db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,"
                           "approved_doc,approved_by,approved_at) SELECT i.view,'tool',i.tool,"
                           "v.server,i.tool,i.effect,i.approved_fp,i.approved_doc,i.approved_by,"
                           "i.approved_at FROM items_01 i JOIN views v ON v.name=i.view").rowcount
        db.execute("DROP TABLE items_01")
        log(db, "migration", "items.0.2", f"{moved} Einträge",
            "Sichten aus 0.1: jeder Eintrag trägt jetzt seinen Server; Texte unverändert")


def valid_name(name, what):
    if not NAME.match(name or ""):
        raise ValueError(f"Der Name {what} besteht aus Kleinbuchstaben, Ziffern und "
                         "Bindestrichen, beginnt mit Buchstabe oder Ziffer, höchstens 48 Zeichen.")
    return name


def valid_auth(kind):
    kind = (kind or "none").strip()
    if kind in AUTH_KINDS:
        return kind
    if kind.startswith("header:") and HEADER_NAME.match(kind[7:]):
        if kind[7:].lower() in ("host", "content-length", "content-type", "mcp-session-id",
                                "mcp-protocol-version", "accept"):
            raise ValueError(f"Die Kopfzeile {kind[7:]} setzt das Gateway selbst.")
        return kind
    raise ValueError("Zugang ist none, bearer, basic, header:<Name>, exchange, platform, "
                     "propagate oder personal.")


def valid_personal(spec):
    """Wie der persönliche Zugang geht (Verfahren U): {send, prefix, hint, link}.

    `send` ist `bearer`, `basic` oder `header:<Name>` -- dieselben Formen wie beim
    Zugang eines Servers. `basic` (0.18): die Person nennt Benutzer (etwa ihre E-Mail)
    und Token getrennt, das Gateway bildet `Authorization: Basic base64(benutzer:token)`
    erst beim Senden -- so verlangt es etwa der MCP-Server von Atlassian Cloud.
    Wirft ValueError mit einem Satz."""
    spec = dict(spec or {})
    send = (spec.get("send") or "bearer").strip()
    if send not in ("bearer", "basic"):
        send = valid_auth(send if send.startswith("header:") else "header:" + send)
        if not send.startswith("header:"):
            raise ValueError("Verfahren U schickt den Zugang als bearer oder in einer Kopfzeile.")
    prefix = (spec.get("prefix") or "")
    if len(prefix) > 40 or any(not ch.isprintable() for ch in prefix):
        raise ValueError("Das Präfix ist höchstens 40 druckbare Zeichen lang.")
    hint = " ".join((spec.get("hint") or "").split())
    if len(hint) > 300:
        raise ValueError("Der Hinweis für die Person ist höchstens 300 Zeichen lang.")
    link = (spec.get("link") or "").strip()
    if link and (len(link) > 300 or not re.match(r"^https?://[^\s\"'<>]+$", link)):
        raise ValueError("Der Link für die Person beginnt mit http:// oder https://.")
    return {"send": send, "prefix": prefix if send.startswith("header:") else "", "hint": hint,
            "link": link}


# ------------------------------------------------------------------ Schlüssel

def issue(db, label, actor, views=(), max_effect="read", ceiling="yellow", budget_calls=0,
          rate_per_min=0, tenant="default", kind="key", groups=(), **ctx):
    """Stellt einen Schlüssel aus — oder legt ein Zugangsprofil an (`kind="profile"`, Spec §2.3).

    Ein Profil bekommt denselben Datensatz wie ein Schlüssel, aber sein
    Wert wird verworfen: Niemand kennt ihn, und `kind` verhindert, dass
    die Zeile je als Schlüssel angenommen wird.
    """
    word = "Ein Zugangsprofil" if kind == "profile" else "Ein Schlüssel"
    if not views:
        raise ValueError(f"{word} braucht mindestens eine Sicht — ohne genannte Sicht "
                         "erreicht es nichts.")
    known = {r["name"] for r in db.execute("SELECT name FROM views")}
    unknown = [v for v in views if v not in known]
    if unknown:
        raise ValueError("Diese Sichten gibt es nicht: " + ", ".join(unknown) + ".")
    if max_effect not in EFFECT_RANK:
        raise ValueError("Höchste Wirkungsklasse ist read, write oder execute.")
    if ceiling not in LIGHT_RANK:
        raise ValueError("Ampel ist green, yellow oder red.")
    extra = {"views": ",".join(views), "max_effect": max_effect, "ceiling": ceiling,
             "budget_calls": max(0, int(budget_calls or 0)),
             "rate_per_min": max(0, int(rate_per_min or 0)), "kind": kind}
    detail = f"sichten={','.join(views)} bis={max_effect} ampel={ceiling}"
    if kind == "profile":
        extra["groups"] = clean_groups(groups)
        detail = f"Zugangsprofil, gruppen={extra['groups']}; " + detail
    elif kind != "key":
        raise ValueError("Art ist key oder profile.")
    return ledger.issue(db, label, actor, KEY_PREFIX, extra, ctx, tenant, detail)


def clean_groups(groups):
    if isinstance(groups, str):
        groups = groups.split(",")
    out = [g.strip() for g in groups if g and g.strip()]
    if not out:
        raise ValueError("Ein Zugangsprofil nennt mindestens eine Gruppe (/pfad) oder * für alle.")
    bad = [g for g in out if not GROUP.match(g)]
    if bad:
        raise ValueError("Gruppen sind volle Pfade wie /sap-dev, * für alle Personen des "
                         "Issuers, @<chat-instanz> oder @* für Personen aus einem Chat der "
                         "Plattform: " + ", ".join(bad))
    return ",".join(sorted(set(out)))


def profile_groups(row):
    return [g for g in (row["groups"] or "").split(",") if g]


def set_profile_groups(db, actor, label, groups):
    row = db.execute("SELECT * FROM keys WHERE label=? AND kind='profile'", (label,)).fetchone()
    if not row:
        raise ValueError(f"Ein Zugangsprofil „{label}“ gibt es nicht.")
    new = clean_groups(groups)
    if new != row["groups"]:
        db.execute("UPDATE keys SET groups=? WHERE id=?", (new, row["id"]))
        log(db, actor, "profile.groups", label, f"{row['groups']} → {new}")
        db.commit()
        return True
    return False


def set_key_limits(db, actor, label, views, max_effect, ceiling):
    """Sichten und Grenzen eines Schlüssels oder Zugangsprofils ändern (0.7.3).

    Der Wert eines Schlüssels bleibt derselbe -- wer ihn benutzt, merkt nur die
    neue Reichweite. Dieselben Regeln wie beim Ausstellen; Prüfspur mit alt → neu."""
    row = db.execute("SELECT * FROM keys WHERE label=? AND revoked=''", (label,)).fetchone()
    if not row:
        raise ValueError(f"„{label}“ gibt es nicht, oder es ist widerrufen.")
    views = [v.strip() for v in (views.split(",") if isinstance(views, str) else views) if v and v.strip()]
    if not views:
        raise ValueError("Mindestens eine Sicht -- ohne genannte Sicht erreicht es nichts.")
    known = {r["name"] for r in db.execute("SELECT name FROM views")}
    unknown = [v for v in views if v not in known]
    if unknown:
        raise ValueError("Diese Sichten gibt es nicht: " + ", ".join(unknown) + ".")
    if max_effect not in EFFECT_RANK:
        raise ValueError("Höchste Wirkungsklasse ist read, write oder execute.")
    if ceiling not in LIGHT_RANK:
        raise ValueError("Ampel ist green, yellow oder red.")
    new = (",".join(views), max_effect, ceiling)
    old = (row["views"], row["max_effect"], row["ceiling"])
    if new == old:
        return []
    db.execute("UPDATE keys SET views=?, max_effect=?, ceiling=? WHERE id=?", new + (row["id"],))
    log(db, actor, "key.limits", label,
        f"sichten={old[0]} bis={old[1]} ampel={old[2]} → sichten={new[0]} bis={new[1]} ampel={new[2]}")
    db.commit()
    return [w for w, a, b in zip(("Sichten", "Wirkungsklasse", "Ampel"), old, new) if a != b]


def match_profile(db, groups, view, wildcard="*"):
    """Das Profil für einen Menschen mit diesen Gruppen in dieser Sicht (Spec §2.3) — oder None.

    Nur Profile, die die Sicht nennen. Darunter zuerst das erste (nach
    Namen), dessen Gruppen sich mit denen des Tokens überschneiden; nur
    wenn keines passt, das erste mit `wildcard`. Ohne die Sicht als Bedingung
    käme ein Mensch mit zwei Profilen für zwei Sichten nie in die zweite
    — gefunden im Test, 2026-11-02.

    Verfahren A (0.7): Gruppen = [@<chat>], wildcard = @*. Ein `*` gilt nur
    für Anmeldungen am Anmeldedienst, nie für Menschen aus einem Chat — wer
    ein Profil „für alle“ angelegt hat, hat an Claude Code gedacht, nicht an
    jeden Chat des Mandanten.
    """
    rows = [r for r in db.execute("SELECT * FROM keys WHERE kind='profile' AND revoked='' "
                                  "ORDER BY label") if view in key_views(r)]
    for row in rows:
        if set(profile_groups(row)) & set(groups):
            return row
    for row in rows:
        if wildcard in profile_groups(row):
            return row
    return None


def key_views(row):
    return [v for v in (row["views"] or "").split(",") if v]


def calls_used(db, key_id):
    """Was gegen das Budget zählt: Aufrufe, die zum Server gingen."""
    return db.execute("SELECT COUNT(*) AS n FROM usage WHERE key_id=? AND outcome IN "
                      "('ok','tool_error','upstream_error')", (key_id,)).fetchone()["n"]


# ------------------------------------------------------------------ Server

def server_row(db, name):
    return db.execute("SELECT * FROM servers WHERE name=?", (name,)).fetchone()


def servers(db):
    return list(db.execute("SELECT * FROM servers ORDER BY name"))


def sync_sources(db, sources, limits):
    """Gleicht die Quellenliste der Plattform ab (RFC-0073 D2, Spec §3.6). Gibt einen Bericht zurück.

    - Eine neue Quelle wird ein Server mit ihrem Instanznamen, Ampel `red`:
      mit Verfahren I (`platform`, Adresse = Tür auf der eigenen Seite), wenn
      die Liste eine Tür nennt (Stufe 2), sonst Zugang `none` an ihrer
      Adresse. In keiner Sicht erscheint etwas, bevor der Betreiber
      freigibt (Standard: alles verboten).
    - Ein Server gleichen Namens, den jemand von Hand angelegt hat, bleibt
      unberührt; der Bericht nennt den Konflikt.
    - Eine bekannte Quelle behält Zugang, Zugangsdaten und Ampel des
      Betreibers; nur eine geänderte Adresse wird nachgezogen — bei
      Verfahren I die Tür, sonst die Adresse. Eine Quelle, die noch nie
      einen Zugang bekam (`none`), wechselt auf Verfahren I, sobald die
      Liste eine Tür nennt.
    - Verschwindet eine Quelle, bleibt ihr Server mit Zeitpunkt in `gone`;
      die Einträge in den Sichten werden nicht still entfernt.
    """
    report = {"added": [], "moved": [], "conflict": [], "gone": [], "back": []}
    seen = set()
    stamp = now()
    for src in sources:
        name = str(src.get("name") or "")
        address = str(src.get("address") or "")
        door = str(src.get("door") or "")
        if not NAME.match(name) or not address.startswith("http://"):
            continue
        if door and not door.startswith("http://"):
            door = ""
        seen.add(name)
        # 0.14.0: ein Server einer Quelle kann umbenannt sein -- gefunden wird
        # er über seine Herkunft (`source` = Instanzname), nicht über den Namen.
        old = db.execute("SELECT * FROM servers WHERE source=?", (name,)).fetchone() \
            or server_row(db, name)
        if old is None:
            kind = "platform" if door else "none"
            db.execute("INSERT INTO servers (name,address,auth_kind,credential,light,limits,source,"
                       "created,updated) VALUES (?,?,?,'','red',?,?,?,?)",
                       (name, door or address, kind, json.dumps(limits, sort_keys=True), name,
                        stamp, stamp))
            log(db, "plattform", "server.source", name,
                f"aus der Quellenliste eingetragen: {src.get('app')} {src.get('version')}")
            report["added"].append(name)
            continue
        if old["source"] != name:
            report["conflict"].append(name)
            continue
        sname = old["name"]
        if old["gone"]:
            db.execute("UPDATE servers SET gone='', updated=? WHERE name=?", (stamp, sname))
            log(db, "plattform", "server.source", sname, "wieder in der Quellenliste")
            report["back"].append(sname)
        kind = old["auth_kind"]
        if kind == "none" and door and not old["credential"]:
            db.execute("UPDATE servers SET auth_kind='platform', updated=? WHERE name=?",
                       (stamp, sname))
            log(db, "plattform", "server.source", sname, "Zugang: none → platform (Verfahren I)")
            kind = "platform"
        want = door if kind == "platform" else address
        if kind == "platform" and not door:
            want = old["address"]               # Tür fehlt in der Liste: nichts umbiegen
        if old["address"] != want:
            db.execute("UPDATE servers SET address=?, updated=? WHERE name=?", (want, stamp, sname))
            log(db, "plattform", "server.source", sname, f"address: {old['address']} → {want}")
            report["moved"].append(sname)
    for row in db.execute("SELECT name,source FROM servers WHERE source<>'' AND gone=''").fetchall():
        if row["source"] not in seen:
            db.execute("UPDATE servers SET gone=?, updated=? WHERE name=?", (stamp, stamp, row["name"]))
            log(db, "plattform", "server.source", row["name"],
                "nicht mehr in der Quellenliste; Einträge in den Sichten bleiben stehen")
            report["gone"].append(row["name"])
    db.commit()
    return report


def save_server(db, actor, name, address, auth_kind, light, limits, sealed_credential=None,
                drop_credential=False, exchange=None, personal=None):
    """Legt an oder ändert. `sealed_credential` ist schon verschlüsselt; None = unverändert.

    `exchange` (nur bei Zugang `exchange`): {token_url, client_id, audience}
    — Spec §3.5. Das Geheimnis ist dann das des eigenen Clients beim
    Token-Endpunkt, nicht eines für den Server.
    """
    valid_name(name, "eines Servers")
    auth_kind = valid_auth(auth_kind)
    old_row = server_row(db, name)
    if auth_kind == "platform" and not (old_row and old_row["source"]):
        raise ValueError("Verfahren I (platform) gibt es nur für Quellen der Plattform; die Tür "
                         "nennt die Quellenliste (source sync).")
    if auth_kind == "propagate" and not address.startswith("destination:"):
        raise ValueError("Verfahren P (propagate) geht nur über eine Destination mit Principal "
                         "Propagation: Adresse destination:<name>[/pfad].")
    if auth_kind in ("platform", "propagate", "personal") and any(r["server"] == name for r in db.execute(
            "SELECT DISTINCT i.server FROM items i JOIN views v ON v.name=i.view "
            "WHERE v.ways LIKE '%key%'")):
        raise ValueError("Der Server liegt in einer Sicht, die Schlüssel annimmt. Verfahren "
                         + {"platform": "I", "propagate": "P"}.get(auth_kind, "U")
                         + " braucht eine Person — erst die Sicht auf „Anmeldung“ oder "
                         "„Plattform“ stellen.")
    if auth_kind == "exchange":
        ex = dict(json.loads(old_row["exchange"] or "{}")) if old_row else {}
        ex.update({k: (v or "").strip() for k, v in (exchange or {}).items() if v is not None})
        missing = [k for k in ("token_url", "client_id", "audience") if not ex.get(k)]
        if missing:
            raise ValueError("Verfahren X braucht Token-Endpunkt, eigenen Client und Ziel-Audience "
                             "(fehlt: " + ", ".join(missing) + ").")
        exchange_json = json.dumps({k: ex[k] for k in ("token_url", "client_id", "audience")},
                                   sort_keys=True)
        if any(r["server"] == name for r in db.execute(
                "SELECT DISTINCT i.server FROM items i JOIN views v ON v.name=i.view "
                "WHERE v.ways LIKE '%key%'")):
            raise ValueError("Der Server liegt in einer Sicht, die Schlüssel annimmt. Verfahren X "
                             "braucht eine Person — erst die Sicht auf „Anmeldung“ (oder für einen "
                             "Chat auf „Plattform“) stellen.")
    else:
        exchange_json = "{}"
    if light not in LIGHT_RANK:
        raise ValueError("Ampel ist green, yellow oder red.")
    old = server_row(db, name)
    credential = old["credential"] if old else ""
    if drop_credential:
        credential = ""
    if sealed_credential is not None:
        credential = sealed_credential
    if address.startswith("destination:") and credential and auth_kind != "exchange":
        raise ValueError("Ein Server hinter einer Destination trägt keine eigenen Zugangsdaten: "
                         "Die Plattform setzt die der Destination ein (eine Wahrheit, nicht zwei).")
    if auth_kind == "propagate" and credential:
        raise ValueError("Verfahren P trägt keine Zugangsdaten: Das Zertifikat der Person stellt "
                         "die Destination aus. Erst „Zugangsdaten entfernen“.")
    if auth_kind == "personal" and credential:
        # RFC-0095 §4.1: nie ein gemeinsamer Rückfall hinter den Personen.
        raise ValueError("Verfahren U trägt keine Zugangsdaten des Servers: Jede Person bringt "
                         "ihren eigenen Zugang. Erst „Zugangsdaten entfernen“.")
    personal_json = json.dumps(valid_personal(personal), sort_keys=True) \
        if auth_kind == "personal" else "{}"
    if auth_kind in ("platform", "propagate", "personal"):
        credential = ""                         # Quellen-Schlüssel bzw. Zertifikat kommt von der Plattform
    elif auth_kind != "none" and not credential:
        raise ValueError(f"Zugang {auth_kind} braucht Zugangsdaten.")
    stamp = now()
    db.execute("INSERT INTO servers (name,address,auth_kind,credential,light,limits,exchange,created,"
               "updated) VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
               "address=excluded.address,auth_kind=excluded.auth_kind,credential=excluded.credential,"
               "light=excluded.light,limits=excluded.limits,exchange=excluded.exchange,"
               "updated=excluded.updated",
               (name, address, auth_kind, credential, light, json.dumps(limits, sort_keys=True),
                exchange_json, stamp, stamp))
    changes = []
    if not old:
        changes.append("angelegt")
    else:
        for field, new in (("address", address), ("auth_kind", auth_kind), ("light", light),
                           ("limits", json.dumps(limits, sort_keys=True)), ("exchange", exchange_json)):
            if old[field] != new:
                changes.append(f"{field}: {old[field]} → {new}")
        if old["credential"] != credential:
            changes.append("Zugangsdaten " + ("entfernt" if not credential else "gesetzt"))
        if (old["personal"] or "{}") != personal_json:
            changes.append("persönlicher Zugang: " + personal_json)
    db.execute("UPDATE servers SET personal=? WHERE name=?", (personal_json, name))
    if changes:
        log(db, actor, "server.save", name, "; ".join(changes))
    db.commit()


def rename_server(db, actor, old, new):
    """Benennt einen Server um (0.14.0). Katalog und Einträge in den Sichten
    ziehen mit; die Namen der Einträge in den Sichten bleiben, wie sie sind
    (auch ein vorgeschlagenes `<server>_<name>`) -- Clients sehen nichts
    davon. Ein Server einer Quelle behält seine Herkunft (`source`) und wird
    beim Abgleich weiter gefunden. Messzeilen behalten den alten Namen.
    Gibt die betroffenen Sichten zurück."""
    valid_name(new, "eines Servers")
    row = server_row(db, old)
    if not row:
        raise ValueError(f"Einen Server „{old}“ gibt es nicht.")
    if new == old:
        return []
    if server_row(db, new):
        raise ValueError(f"Einen Server „{new}“ gibt es schon.")
    views_hit = [r["view"] for r in db.execute(
        "SELECT DISTINCT view FROM items WHERE server=? ORDER BY view", (old,))]
    db.execute("UPDATE servers SET name=?, updated=? WHERE name=?", (new, now(), old))
    db.execute("UPDATE catalog SET server=? WHERE server=?", (new, old))
    db.execute("UPDATE items SET server=? WHERE server=?", (new, old))
    db.execute("UPDATE personal_credentials SET server=? WHERE server=?", (new, old))
    log(db, actor, "server.rename", old, f"→ {new}; Sichten: {', '.join(views_hit) or 'keine'}"
        + (f"; Quelle {row['source']} bleibt" if row["source"] else ""))
    db.commit()
    return views_hit


def views_for_person(db, groups, wildcard, why_not, see_all=False):
    """Die Sichten, die ein Mensch mit diesen Gruppen öffnen kann (0.14.0) --
    dieselbe Regel wie beim Aufruf (Profil, Zugang, Ampel). Mit `see_all`
    (Verwaltung des Mandanten) auch die übrigen, jede mit dem Grund."""
    out = []
    for v in views(db):
        profile = match_profile(db, groups, v["name"], wildcard)
        reason = why_not(profile, v) if profile else "kein Zugangsprofil erlaubt diese Sicht."
        if reason and not see_all:
            continue
        counts = {}
        for i in items(db, v["name"]):
            if item_state(db, i) == "active":
                counts[i["kind"]] = counts.get(i["kind"], 0) + 1
        out.append({"name": v["name"], "ok": not reason, "reason": reason or "",
                    "light": view_light(db, v["name"]), "max_effect": v["max_effect"],
                    "tools": counts.get("tool", 0), "prompts": counts.get("prompt", 0),
                    "resources": counts.get("resource", 0) + counts.get("template", 0)})
    return out


def delete_server(db, actor, name):
    used = [r["view"] for r in db.execute("SELECT DISTINCT view FROM items WHERE server=?", (name,))]
    if used:
        raise ValueError(f"Server „{name}“ steht noch in Sichten: {', '.join(used)}.")
    db.execute("DELETE FROM servers WHERE name=?", (name,))
    db.execute("DELETE FROM catalog WHERE server=?", (name,))
    gone = db.execute("DELETE FROM personal_credentials WHERE server=?", (name,)).rowcount
    log(db, actor, "server.delete", name, f"{gone} persönliche Zugänge gelöscht" if gone else "")
    db.commit()


# ------------------------------------------------- persönliche Zugänge (Verfahren U)
# RFC-0095 §4.2: je (Issuer, sub, Server) ein versiegelter Wert. Nur schreiben,
# ersetzen, löschen -- nie zurückgeben, auch nicht teilweise. Die Prüfspur nennt
# die Person mit ihrer Kennung, nie mit Namen (§10), und nie den Wert.

def personal_spec(srow):
    try:
        spec = json.loads(srow["personal"] or "{}") if "personal" in srow.keys() else {}
    except ValueError:
        spec = {}
    return spec if isinstance(spec, dict) else {}


def personal_row(db, iss, sub, server):
    return db.execute("SELECT * FROM personal_credentials WHERE iss=? AND sub=? AND server=?",
                      (iss, sub, server)).fetchone()


def personal_store(db, iss, sub, server, sealed):
    row = server_row(db, server)
    if not row or row["auth_kind"] != "personal":
        raise ValueError(f"Der Server „{server}“ nimmt keinen persönlichen Zugang.")
    if not (iss and sub):
        raise ValueError("Ohne Kennung der Person wird nichts gespeichert.")
    replaced = personal_row(db, iss, sub, server) is not None
    db.execute("INSERT INTO personal_credentials (iss,sub,server,sealed,stored,last_used) "
               "VALUES (?,?,?,?,?,'') ON CONFLICT(iss,sub,server) DO UPDATE SET "
               "sealed=excluded.sealed, stored=excluded.stored, last_used=''",
               (iss, sub, server, sealed, now()))
    log(db, f"person:{sub}", "personal.store", server, ("ersetzt" if replaced else "neu")
        + ("" if iss == "platform" else f"; Issuer {iss}"))
    db.commit()
    return replaced


def personal_delete(db, iss, sub, server, actor=None):
    """Löscht den Zugang einer Person. `actor` None = die Person selbst; sonst der Betreiber."""
    cur = db.execute("DELETE FROM personal_credentials WHERE iss=? AND sub=? AND server=?",
                     (iss, sub, server))
    if cur.rowcount:
        log(db, actor or f"person:{sub}", "personal.delete", server,
            "von der Person" if actor is None else f"vom Betreiber; Person {sub}")
    db.commit()
    return bool(cur.rowcount)


def personal_touch(db, iss, sub, server):
    db.execute("UPDATE personal_credentials SET last_used=? WHERE iss=? AND sub=? AND server=?",
               (now(), iss, sub, server))
    db.commit()


def personal_of_person(db, iss, sub):
    return {r["server"]: r for r in db.execute(
        "SELECT server, stored, last_used FROM personal_credentials WHERE iss=? AND sub=?",
        (iss, sub))}


def person_link_set(db, iss, sub, platform_sub):
    """D2 (0.17): Die Plattform sagt auf /me, welche Kennung im Realm diese Person hat.
    Gemerkt, damit ein Aufruf mit Anmeldung am Anmeldedienst ihren Zugang (Verfahren U)
    findet. Nur von der Plattform gesetzt, nie aus einem MCP-Aufruf."""
    old = db.execute("SELECT platform_sub FROM person_links WHERE iss=? AND sub=?",
                     (iss, sub)).fetchone()
    db.execute("INSERT INTO person_links (iss,sub,platform_sub,seen) VALUES (?,?,?,?) "
               "ON CONFLICT(iss,sub) DO UPDATE SET platform_sub=excluded.platform_sub, "
               "seen=excluded.seen", (iss, sub, platform_sub, now()))
    if old is None or old["platform_sub"] != platform_sub:
        log(db, f"person:{platform_sub}", "person.link", sub, f"Kennung im Realm {iss}")
    db.commit()


def person_link_get(db, iss, sub):
    row = db.execute("SELECT platform_sub FROM person_links WHERE iss=? AND sub=?",
                     (iss, sub)).fetchone()
    return row["platform_sub"] if row else ""


def personal_holders(db, server):
    """Für den Betreiber: wer (Kennung) wann gespeichert und zuletzt benutzt hat -- nie der Wert."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=UNUSED_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return [{"iss": r["iss"], "sub": r["sub"], "stored": r["stored"], "last_used": r["last_used"],
             "unused": (r["last_used"] or r["stored"]) < cutoff}
            for r in db.execute("SELECT iss, sub, stored, last_used FROM personal_credentials "
                                "WHERE server=? ORDER BY stored", (server,))]


def note_server(db, name, version=None, error=None, read=False):
    sets, args = [], []
    if version is not None:
        sets.append("version=?")
        args.append(version)
    if error is not None:
        sets.append("last_error=?")
        args.append(error)
    if read:
        sets.append("last_read=?")
        args.append(now())
    if sets:
        db.execute(f"UPDATE servers SET {','.join(sets)} WHERE name=?", args + [name])
        db.commit()


# ------------------------------------------------------------------ Katalog

def fingerprint(doc):
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def store_catalog(db, server, offers):
    """Schreibt das Gelesene. `offers` = {kind: [rohe Einträge]}; nur gelesene Arten werden abgeglichen.

    Gibt die Einträge in Sichten zurück, die dadurch neu gesperrt sind oder fehlen.
    """
    import compose
    stamp = now()
    before = {(i["view"], i["kind"], i["name"]): item_state(db, i) for i in approved_items(db, server)}
    ui_errors = offers.pop("ui_errors", None) or {}
    for kind, raws in offers.items():
        seen = set()
        for raw in raws:
            doc = compose.doc_of(kind, raw)
            src = compose.source_of(kind, doc)
            if not isinstance(src, str) or not src or src in seen:
                continue
            seen.add(src)
            db.execute("INSERT INTO catalog (server,kind,name,fingerprint,doc,present,first_seen,"
                       "last_seen) VALUES (?,?,?,?,?,1,?,?) ON CONFLICT(server,kind,name) DO UPDATE "
                       "SET fingerprint=excluded.fingerprint,doc=excluded.doc,present=1,"
                       "last_seen=excluded.last_seen",
                       (server, kind, src, fingerprint(doc), json.dumps(doc, ensure_ascii=False),
                        stamp, stamp))
        for row in db.execute("SELECT name FROM catalog WHERE server=? AND kind=?", (server, kind)):
            if row["name"] not in seen:
                db.execute("UPDATE catalog SET present=0 WHERE server=? AND kind=? AND name=?",
                           (server, kind, row["name"]))
    changed = []
    for item in approved_items(db, server):
        state = item_state(db, item)
        ref = f"{item['view']}/{item['name']}"
        if state != "active" and before.get((item["view"], item["kind"], item["name"])) == "active":
            changed.append(ref)
            log(db, "katalog", "item." + state, ref,
                "Der Server meldet einen anderen Text als freigegeben" if state == "suspended"
                else "Der Server bietet den Eintrag nicht mehr an")
    db.commit()
    return changed


def catalog(db, server, kind=None):
    if kind:
        return list(db.execute("SELECT * FROM catalog WHERE server=? AND kind=? "
                               "ORDER BY present DESC, name", (server, kind)))
    return list(db.execute("SELECT * FROM catalog WHERE server=? ORDER BY kind, present DESC, name",
                           (server,)))


def catalog_row(db, server, kind, name):
    return db.execute("SELECT * FROM catalog WHERE server=? AND kind=? AND name=?",
                      (server, kind, name)).fetchone()


# ------------------------------------------------------------------ Sichten

def view_row(db, name):
    return db.execute("SELECT * FROM views WHERE name=?", (name,)).fetchone()


def views(db):
    return list(db.execute("SELECT * FROM views ORDER BY name"))


def view_requests(vrow):
    try:
        doc = json.loads(vrow["requests"] or REQUESTS_OLD)
    except (ValueError, TypeError):
        doc = json.loads(REQUESTS_OLD)
    return {r: ("pass" if doc.get(r) == "pass" else "refuse") for r in REQUESTS}


def save_view(db, actor, name, max_effect, requests=None, ways=None):
    """Legt an oder ändert. `requests` = {elicitation|roots|sampling: pass|refuse}; None = unverändert.

    `ways` = Komma-Liste aus key, signin, platform (Spec §2); None = unverändert, neu: key.
    """
    valid_name(name, "einer Sicht")
    if max_effect not in EFFECT_RANK:
        raise ValueError("Höchste Wirkungsklasse ist read, write oder execute.")
    old = view_row(db, name)
    old_ways = old["ways"] if old else "key"
    ways = old_ways if ways is None else ways
    if ways not in WAYS:
        raise ValueError("Zugänge sind key, signin und platform, durch Komma getrennt, in "
                         "dieser Reihenfolge (z. B. signin,platform).")
    if "key" in ways and old and exchange_servers(db, name):
        raise ValueError("Die Sicht enthält Server mit Verfahren X, I, P oder U (" + ", ".join(exchange_servers(db, name))
                         + "); die brauchen eine angemeldete Person — Schlüssel gehen hier nicht.")
    current = view_requests(old) if old else dict(REQUESTS_NEW)
    if requests:
        for r, v in requests.items():
            if r not in REQUESTS or v not in ("pass", "refuse"):
                raise ValueError("Rückfragen: elicitation, roots, sampling — je pass oder refuse.")
            current[r] = v
    stamp = now()
    db.execute("INSERT INTO views (name,server,max_effect,requests,ways,created,updated) "
               "VALUES (?,'',?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
               "max_effect=excluded.max_effect,requests=excluded.requests,ways=excluded.ways,"
               "updated=excluded.updated",
               (name, max_effect, json.dumps(current, sort_keys=True), ways, stamp, stamp))
    words = ", ".join(f"{r} {current[r]}" for r in REQUESTS)
    if not old:
        log(db, actor, "view.save", name, f"angelegt, bis {max_effect}, Zugänge {ways}; "
            f"Rückfragen: {words}")
    else:
        bits = []
        if old["max_effect"] != max_effect:
            bits.append(f"bis {old['max_effect']} → {max_effect}")
        before = view_requests(old)
        bits += [f"{r} {before[r]} → {current[r]}" for r in REQUESTS if before[r] != current[r]]
        if old_ways != ways:
            bits.append(f"Zugänge {old_ways} → {ways}")
        if bits:
            log(db, actor, "view.save", name, "; ".join(bits))
    db.commit()


def delete_view(db, actor, name):
    users = [r["label"] for r in db.execute("SELECT label,views FROM keys WHERE revoked=''")
             if name in key_views(r)]
    if users:
        raise ValueError(f"Gültige Schlüssel nennen die Sicht noch: {', '.join(users)}.")
    db.execute("DELETE FROM items WHERE view=?", (name,))
    db.execute("DELETE FROM views WHERE name=?", (name,))
    log(db, actor, "view.delete", name)
    db.commit()


def view_readers(db, name):
    """Wer den Namen einer Sicht nennt (0.13.0): gültige Schlüssel und Zugangsprofile.

    Chat-Assistenten nennen die Sicht auch -- sie stehen in einer anderen App;
    die Seite sagt das, prüfen kann das Gateway es nicht."""
    return [(r["label"], r["kind"]) for r in db.execute(
        "SELECT label,kind,views FROM keys WHERE revoked='' ORDER BY label") if name in key_views(r)]


def copy_view(db, actor, src, new, move_readers=False):
    """Kopiert eine Sicht mit allen Einträgen unter neuem Namen (0.13.0).

    Die Freigaben gehen mit, wie sie sind (festgehaltener Text, Fingerabdruck,
    wer wann) -- dieselbe Prüfung, kein neuer Text. Mit `move_readers` ist es
    ein Umbenennen: Schlüssel und Zugangsprofile nennen danach die neue Sicht,
    die alte ist weg. Messzeilen behalten den alten Namen: sie sagen, was war.
    Gibt die umgestellten Leser zurück."""
    valid_name(new, "einer Sicht")
    old = view_row(db, src)
    if not old:
        raise ValueError(f"Eine Sicht „{src}“ gibt es nicht.")
    if view_row(db, new):
        raise ValueError(f"Eine Sicht „{new}“ gibt es schon.")
    stamp = now()
    cols = [c for c in old.keys() if c not in ("name", "created", "updated")]
    db.execute(f"INSERT INTO views (name,{','.join(cols)},created,updated) "
               f"VALUES (?,{','.join('?' * len(cols))},?,?)",
               [new] + [old[c] for c in cols] + [stamp, stamp])
    rows = items(db, src)
    if rows:
        icols = [c for c in rows[0].keys() if c != "view"]
        for r in rows:
            db.execute(f"INSERT INTO items (view,{','.join(icols)}) VALUES (?,{','.join('?' * len(icols))})",
                       [new] + [r[c] for c in icols])
    moved = []
    if move_readers:
        for r in db.execute("SELECT id,label,views FROM keys WHERE revoked=''").fetchall():
            names = key_views(r)
            if src in names:
                db.execute("UPDATE keys SET views=? WHERE id=?",
                           (",".join(new if v == src else v for v in names), r["id"]))
                moved.append(r["label"])
        db.execute("DELETE FROM items WHERE view=?", (src,))
        db.execute("DELETE FROM views WHERE name=?", (src,))
        log(db, actor, "view.rename", src, f"→ {new}; {len(rows)} Einträge; "
            f"Schlüssel/Profile umgestellt: {', '.join(moved) or 'keine'}")
    else:
        log(db, actor, "view.copy", new, f"Kopie von {src}; {len(rows)} Einträge")
    db.commit()
    return moved


def set_view_items(db, actor, view, wanted, seen, effects=None):
    """Zuordnung als Baum (0.13.0): `wanted` = angekreuzte (server, kind, source),
    `seen` = alle, die das Formular zeigte. Was gezeigt und nicht angekreuzt
    war, geht heraus; was angekreuzt und noch nicht drin ist, wird freigegeben
    (dieselbe Prüfung wie „In Sicht legen“, Name vorgeschlagen). `effects`
    setzt die Wirkungsklasse von Tools. Was das Formular nicht zeigte (etwa ein
    Eintrag, den der Server nicht mehr anbietet), bleibt, wie es ist.
    Gibt (hinzu, heraus, geändert, fehler) zurück -- Fehler je Eintrag, der Rest gilt."""
    effects = effects or {}
    if not view_row(db, view):
        raise ValueError(f"Eine Sicht „{view}“ gibt es nicht.")
    have = {}
    for i in items(db, view):
        have.setdefault((i["server"], i["kind"], i["source"]), []).append(i)
    added, removed, changed, errors = [], [], [], []
    for key in sorted(set(seen) - set(wanted)):
        for i in have.get(key, []):
            remove_item(db, actor, view, i["kind"], i["name"])
            removed.append(i["name"])
    for key in sorted(set(wanted) & set(seen)):
        server, kind, source = key
        eff = effects.get(key) or "read"
        if key in have:
            for i in have[key]:
                if kind == "tool" and effects.get(key) and i["effect"] != eff:
                    try:
                        tailor_item(db, actor, view, kind, i["name"], effect=eff)
                        changed.append(i["name"])
                    except ValueError as exc:
                        errors.append(f"{i['name']}: {exc}")
            continue
        try:
            added.append(approve(db, actor, view, kind, server, source, None, eff))
        except ValueError as exc:
            errors.append(f"{server}:{source}: {exc}")
    return added, removed, changed, errors


def exchange_servers(db, view):
    return [r["name"] for r in db.execute(
        "SELECT DISTINCT s.name FROM items i JOIN servers s ON s.name=i.server "
        "WHERE i.view=? AND s.auth_kind IN ('exchange','platform','propagate','personal') ORDER BY s.name",
        (view,))]


def view_ways(vrow):
    return [w for w in (vrow["ways"] or "key").split(",") if w]


def view_servers(db, view):
    return [r["server"] for r in db.execute("SELECT DISTINCT server FROM items WHERE view=? "
                                            "ORDER BY server", (view,))]


def view_light(db, view):
    """Die Ampel einer Sicht ist die ihres schlechtesten Servers (RFC-0065 §9.3)."""
    worst = "green"
    for name in view_servers(db, view):
        srow = server_row(db, name)
        light = srow["light"] if srow else "red"
        if LIGHT_RANK[light] > LIGHT_RANK[worst]:
            worst = light
    return worst


# ------------------------------------------------------------------ Einträge

def items(db, view, kind=None):
    if kind:
        return list(db.execute("SELECT * FROM items WHERE view=? AND kind=? ORDER BY name",
                               (view, kind)))
    return list(db.execute("SELECT * FROM items WHERE view=? ORDER BY kind, name", (view,)))


def item_row(db, view, kind, name):
    return db.execute("SELECT * FROM items WHERE view=? AND kind=? AND name=?",
                      (view, kind, name)).fetchone()


def approved_items(db, server):
    """Alle freigegebenen Einträge dieses Servers, über alle Sichten."""
    return [dict(r) for r in db.execute("SELECT * FROM items WHERE server=?", (server,))]


def item_state(db, item):
    """`active`, `suspended` oder `missing` (Spec §4.2).

    0.8.0 (RFC-0075 D3): ein Tool mit Ansicht ist nur aktiv, wenn auch seine
    Ansicht in derselben Sicht aktiv ist -- eine geänderte Ansicht sperrt das Tool."""
    cat = catalog_row(db, item["server"], item["kind"], item["source"])
    if not cat or not cat["present"]:
        return "missing"
    if cat["fingerprint"] != item["approved_fp"]:
        return "suspended"
    if item["kind"] == "tool":
        uri = ui_uri(item)
        if uri:
            view_item = item_row(db, item["view"], "resource", uri)
            if not view_item or item_state(db, view_item) != "active":
                return "suspended"
    return "active"


def ui_uri(item):
    """Die URI der freigegebenen Ansicht eines Tools, oder None."""
    try:
        doc = json.loads(item["approved_doc"] or "{}")
    except ValueError:
        return None
    ui = (doc.get("_meta") or {}).get("ui") if isinstance(doc.get("_meta"), dict) else None
    uri = ui.get("resourceUri") if isinstance(ui, dict) else None
    return uri if isinstance(uri, str) and uri.startswith("ui://") else None


def approve(db, actor, view, kind, server, source, name=None, effect="read"):
    """Legt einen Eintrag in eine Sicht. Hält den Text fest. Ein belegter Name wird abgewiesen."""
    import argcheck
    import compose
    if not view_row(db, view):
        raise ValueError(f"Eine Sicht „{view}“ gibt es nicht.")
    if kind not in compose.KINDS:
        raise ValueError("Art ist tool, resource, template oder prompt.")
    if not server_row(db, server):
        raise ValueError(f"Einen Server „{server}“ gibt es nicht.")
    effect = effect if kind == "tool" else "read"
    if effect not in EFFECT_RANK:
        raise ValueError("Wirkungsklasse ist read, write oder execute.")
    if server_row(db, server)["auth_kind"] in PERSON_KINDS and "key" in view_ways(view_row(db, view)):
        raise ValueError(f"Server „{server}“ arbeitet mit Verfahren X, I oder P und braucht eine angemeldete "
                         f"Person; die Sicht „{view}“ nimmt Schlüssel an. Erst die Sicht auf "
                         "„Anmeldung“ (oder für einen Chat auf „Plattform“) stellen.")
    cat = catalog_row(db, server, kind, source)
    if not cat or not cat["present"]:
        raise ValueError(f"Der Server „{server}“ bietet {compose.KIND_WORDS[kind]} „{source}“ "
                         "nicht an (Katalog neu lesen?).")
    doc = json.loads(cat["doc"])
    if kind in ("resource", "template"):
        name = source                           # Resources behalten ihre URI (RFC-0065 §3)
    elif not name:
        taken = {i["name"] for i in items(db, view, kind)}
        name = compose.propose(kind, server, source, taken)
    compose.valid_exposed(kind, name)
    if kind == "tool":
        remote = argcheck.remote_refs(doc.get("inputSchema"))
        if remote:
            raise ValueError(f"Das Schema von „{source}“ verweist nach außen ({remote[0]}); "
                             "so ein Schema lässt sich nicht prüfen und wird nicht freigegeben.")
    if kind == "template" and not compose.template_supported(source):
        raise ValueError(f"Die Vorlage „{source}“ nutzt Formen, die das Gateway nicht eindeutig "
                         "prüfen kann (erlaubt: {x} und {+x}).")
    taken = item_row(db, view, kind, name)
    if taken:
        same = taken["server"] == server and taken["source"] == source
        raise ValueError(
            f"In der Sicht „{view}“ gibt es „{name}“ schon"
            + (" — derselbe Eintrag; ein geänderter Text wird auf der Seite der Sicht bestätigt."
               if same else f" (von Server „{taken['server']}“). Bitte einen anderen Namen wählen."))
    ui = (doc.get("_meta") or {}).get("ui") if kind == "tool" else None
    ui_cat = None
    if ui:
        # RFC-0075 D3: die Ansicht kommt mit dem Tool -- genau die eine, die es nennt.
        ui_cat = catalog_row(db, server, "resource", ui["resourceUri"])
        if not ui_cat or not ui_cat["present"]:
            raise ValueError(f"„{source}“ bringt die Ansicht {ui['resourceUri']} mit, die sich "
                             "nicht lesen ließ (Katalog neu lesen?). Ohne lesbare Ansicht keine "
                             "Freigabe.")
    db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,approved_doc,"
               "approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (view, kind, name, server, source, effect, cat["fingerprint"], cat["doc"], actor,
                now()))
    log(db, actor, "item.approve", f"{view}/{name}",
        f"{kind} {server}:{source} als {effect}, Fingerabdruck {cat['fingerprint'][:12]}")
    if ui_cat and not item_row(db, view, "resource", ui["resourceUri"]):
        db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,approved_doc,"
                   "approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (view, "resource", ui["resourceUri"], server, ui["resourceUri"], "read",
                    ui_cat["fingerprint"], ui_cat["doc"], actor, now()))
        log(db, actor, "item.approve", f"{view}/{ui['resourceUri']}",
            f"Ansicht von {name}, Inhalt {json.loads(ui_cat['doc']).get('content_sha256', '')[:12]}")
    db.commit()
    return name


def confirm(db, actor, view, kind, name, effect=None):
    """Übernimmt den Text, den der Server jetzt meldet. Feste Argumente und Grenzen müssen weiter passen."""
    import compose
    item = item_row(db, view, kind, name)
    if not item:
        raise ValueError("Diesen Eintrag gibt es nicht.")
    cat = catalog_row(db, item["server"], kind, item["source"])
    if not cat or not cat["present"]:
        raise ValueError("Der Server bietet den Eintrag nicht mehr an; bestätigen lässt sich nur "
                         "ein Text, den es gibt.")
    effect = (effect or item["effect"]) if kind == "tool" else "read"
    if effect not in EFFECT_RANK:
        raise ValueError("Wirkungsklasse ist read, write oder execute.")
    if kind == "tool":
        try:
            compose.tailor(json.loads(cat["doc"]), json.loads(item["fixed"]),
                           json.loads(item["bounds"]))
        except ValueError as exc:
            raise ValueError(f"Der neue Text verträgt sich nicht mit der Sicht: {exc} "
                             "Erst feste Argumente oder Grenzen anpassen.") from None
    db.execute("UPDATE items SET effect=?,approved_fp=?,approved_doc=?,approved_by=?,approved_at=? "
               "WHERE view=? AND kind=? AND name=?",
               (effect, cat["fingerprint"], cat["doc"], actor, now(), view, kind, name))
    if kind == "tool":
        # RFC-0075: wer das Tool bestätigt, bestätigt seine Ansicht mit (die neue, falls geändert).
        ui = (json.loads(cat["doc"]).get("_meta") or {}).get("ui") or {}
        uri = ui.get("resourceUri")
        ui_cat = catalog_row(db, item["server"], "resource", uri) if uri else None
        if uri and (not ui_cat or not ui_cat["present"]):
            raise ValueError(f"Die Ansicht {uri} ließ sich nicht lesen; ohne sie keine Bestätigung.")
        if ui_cat:
            if item_row(db, view, "resource", uri):
                db.execute("UPDATE items SET approved_fp=?,approved_doc=?,approved_by=?,approved_at=? "
                           "WHERE view=? AND kind='resource' AND name=?",
                           (ui_cat["fingerprint"], ui_cat["doc"], actor, now(), view, uri))
            else:
                db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,"
                           "approved_doc,approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                           (view, "resource", uri, item["server"], uri, "read", ui_cat["fingerprint"],
                            ui_cat["doc"], actor, now()))
    bits = []
    if item["approved_fp"] != cat["fingerprint"]:
        bits.append(f"Text bestätigt {item['approved_fp'][:12]} → {cat['fingerprint'][:12]}")
    if item["effect"] != effect:
        bits.append(f"Klasse {item['effect']} → {effect}")
    log(db, actor, "item.confirm", f"{view}/{name}", "; ".join(bits) or "unverändert")
    db.commit()


def tailor_item(db, actor, view, kind, name, new_name=None, description=None, fixed=None,
                bounds=None, effect=None):
    """Ändert, was die Sicht aus einem Eintrag macht. None = unverändert. Geprüft am FESTGEHALTENEN Text."""
    import compose
    item = item_row(db, view, kind, name)
    if not item:
        raise ValueError("Diesen Eintrag gibt es nicht.")
    changes, sets, args = [], [], []
    if new_name is not None and new_name != name:
        if kind not in ("tool", "prompt"):
            raise ValueError("Resources behalten ihre URI.")
        compose.valid_exposed(kind, new_name)
        if item_row(db, view, kind, new_name):
            raise ValueError(f"In der Sicht „{view}“ gibt es „{new_name}“ schon.")
        sets.append("name=?")
        args.append(new_name)
        changes.append(f"Name {name} → {new_name}")
    if description is not None and description != item["description"]:
        if len(description) > 4000:
            raise ValueError("Eine Beschreibung hat höchstens 4000 Zeichen.")
        sets.append("description=?")
        args.append(description)
        changes.append("Beschreibung " + ("ersetzt" if description else "wieder die des Servers"))
    if fixed is not None or bounds is not None:
        if kind != "tool":
            raise ValueError("Feste Argumente und Grenzen gibt es nur für Tools.")
        f = fixed if fixed is not None else json.loads(item["fixed"])
        b = bounds if bounds is not None else json.loads(item["bounds"])
        f, b = compose.tailor(json.loads(item["approved_doc"]), f, b)
        fj = json.dumps(f, sort_keys=True, ensure_ascii=False)
        bj = json.dumps(b, sort_keys=True, ensure_ascii=False)
        if fj != item["fixed"]:
            sets.append("fixed=?")
            args.append(fj)
            # Feste Werte sind Vorgabe des Betreibers, kein Argument eines Clients (Spec §10).
            changes.append(f"fest {item['fixed']} → {fj}")
        if bj != item["bounds"]:
            sets.append("bounds=?")
            args.append(bj)
            changes.append(f"Grenzen {item['bounds']} → {bj}")
    if effect is not None and effect != item["effect"]:
        if kind != "tool":
            raise ValueError("Resources und Prompts sind immer „lesen“.")
        if effect not in EFFECT_RANK:
            raise ValueError("Wirkungsklasse ist read, write oder execute.")
        sets.append("effect=?")
        args.append(effect)
        changes.append(f"Klasse {item['effect']} → {effect}")
    if not sets:
        return []
    db.execute(f"UPDATE items SET {','.join(sets)} WHERE view=? AND kind=? AND name=?",
               args + [view, kind, name])
    log(db, actor, "item.tailor", f"{view}/{name}", "; ".join(changes))
    db.commit()
    return changes


def remove_item(db, actor, view, kind, name):
    cur = db.execute("DELETE FROM items WHERE view=? AND kind=? AND name=?", (view, kind, name))
    if cur.rowcount:
        log(db, actor, "item.remove", f"{view}/{name}", kind)
    db.commit()
    return bool(cur.rowcount)


# ------------------------------------------------------------------ Messung

def record(db, key, view, kind, name, server, source, outcome, reason, ms, bytes_up, bytes_down,
           person=None, procedure="T", status=0, door="", light=""):
    """Eine Messzeile je Aufruf. `key` ist die Schlüsselzeile vom Beginn der Anfrage.

    `name`/`source` sind Namen in der Sicht und beim Server — bei einer
    Vorlage die Vorlage, nie die eingesetzte URI: In ihr stünden Werte.
    """
    cols = ["time", "key_id", "view", "kind", "tool", "server", "server_tool", "procedure",
            "outcome", "reason", "ms", "bytes_up", "bytes_down", "user_sub",
            "user_iss", "status", "door", "light"] + context.snapshot_columns()
    values = [now(), key["id"], view, kind, name, server, source, procedure, outcome, reason,
              int(ms), int(bytes_up), int(bytes_down), (person or {}).get("sub", ""),
              (person or {}).get("iss", ""), int(status or 0),
              door if door in DOORS else "",
              light if light in LIGHT_RANK else ""] + context.snapshot_values(key)
    if (person or {}).get("client"):
        cols += ["client", "assistant", "confirmed", "assistant_rev"]
        values += [person["client"], person.get("assistant", ""), person.get("confirmed", ""),
                   int(person.get("assistant_rev") or 0)]
    db.execute(f"INSERT INTO usage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               values)
    db.commit()


PROBLEMS = ("refused", "limited", "upstream_error", "tool_error")


def person_calls(db, iss, sub, days=30, problems=False, view="", day="", limit=500, also=()):
    """Die Messzeilen EINER Person (RFC-0095 Stufe 1): höchstens `days` Tage zurück.

    Nur Zeilen mit genau diesem Issuer und dieser Kennung -- Schlüsselaufrufe
    haben keine Person und erscheinen nie. Ohne Kennung: nichts."""
    if not (iss and sub):
        return []
    who = [(iss, sub)] + [(i, s) for i, s in also if i and s]
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 0.17 (D2): dieselbe Person auch mit ihrer Kennung aus dem Realm.
    sql = ["SELECT * FROM usage WHERE (" + " OR ".join("(user_iss=? AND user_sub=?)" for _ in who)
           + ") AND time >= ?"]
    args = [x for pair in who for x in pair] + [since]
    if problems:
        sql.append(f"AND outcome IN ({','.join('?' * len(PROBLEMS))})")
        args += PROBLEMS
    if view:
        sql.append("AND view=?")
        args.append(view)
    if day:
        sql.append("AND substr(time,1,10)=?")
        args.append(day)
    sql.append("ORDER BY id DESC LIMIT ?")
    args.append(limit)
    return list(db.execute(" ".join(sql), args))


def count_protocol(db, key_id, view, method):
    db.execute("INSERT INTO protocol (day,key_id,view,method,count) VALUES (?,?,?,?,1) "
               "ON CONFLICT(day,key_id,view,method) DO UPDATE SET count=count+1",
               (now()[:10], key_id, view, method))
    db.commit()


def recent(db, key_id=None, limit=50):
    if key_id:
        return list(db.execute("SELECT * FROM usage WHERE key_id=? ORDER BY id DESC LIMIT ?",
                               (key_id, limit)))
    return list(db.execute("SELECT * FROM usage ORDER BY id DESC LIMIT ?", (limit,)))


def totals(db):
    return {r["key_id"]: r for r in db.execute(
        "SELECT key_id, COUNT(*) AS calls, COALESCE(SUM(bytes_up),0) AS bytes_up, "
        "COALESCE(SUM(bytes_down),0) AS bytes_down, MAX(time) AS last FROM usage GROUP BY key_id")}


def protocol_totals(db, days=30):
    return list(db.execute("SELECT method, SUM(count) AS n FROM protocol WHERE day >= "
                           "date('now', ?) GROUP BY method ORDER BY n DESC", (f"-{days} days",)))


# ------------------------------------------------------------------ Assistenten
# RFC-0097 Stufe 1. Wer was sehen und ändern darf, entscheidet app.py mit dem
# gemeinsamen Modul `assistants`; hier liegen nur die Bücher.

ASSISTANT_STATES = ("active", "inactive", "deleted")
KEEP_LAST = 20                      # D6: dazu jede Revision, auf die eine Messzeile zeigt
import threading as _threading      # noqa: E402
_ASSIST_LOCK = _threading.Lock()


def _assistant(r):
    return {"doc": json.loads(r["doc"]), "rev": r["rev"], "state": r["state"],
            "created": r["created"], "changed_at": r["changed_at"], "changed_by": r["changed_by"]}


def assistant_rows(db):
    return [_assistant(r) for r in db.execute("SELECT * FROM assistants ORDER BY id")]


def assistant_row(db, aid):
    r = db.execute("SELECT * FROM assistants WHERE id=?", (aid,)).fetchone()
    return _assistant(r) if r else None


def assistant_revision(db, aid, rev):
    """{doc|None, rev, changed_at, changed_by, compacted} oder None."""
    r = db.execute("SELECT * FROM assistant_revisions WHERE id=? AND rev=?", (aid, int(rev))).fetchone()
    if not r:
        return None
    return {"doc": None if r["compacted"] else json.loads(r["doc"]), "rev": r["rev"],
            "changed_at": r["changed_at"], "changed_by": r["changed_by"],
            "compacted": bool(r["compacted"])}


def assistant_revisions(db, aid):
    return [{"rev": r["rev"], "changed_at": r["changed_at"], "changed_by": r["changed_by"],
             "compacted": bool(r["compacted"])}
            for r in db.execute("SELECT rev, changed_at, changed_by, compacted FROM assistant_revisions "
                                "WHERE id=? ORDER BY rev DESC", (aid,))]


def assistant_write(db, doc, by, state="active"):
    """Eine neue Revision (n+1) und die geltende Zeile. Gibt die neue Nummer zurück.

    Die Nummer vergibt das Gateway, nie der Schreibende."""
    aid, at = doc["id"], now()
    text = json.dumps(doc, ensure_ascii=False, sort_keys=True)
    with _ASSIST_LOCK:
        last = db.execute("SELECT MAX(rev) AS m FROM assistant_revisions WHERE id=?", (aid,)).fetchone()
        rev = int((last["m"] if last else 0) or 0) + 1
        db.execute("INSERT INTO assistant_revisions (id, rev, doc, changed_at, changed_by) "
                   "VALUES (?,?,?,?,?)", (aid, rev, text, at, by))
        db.execute("INSERT INTO assistants (id, rev, doc, state, created, changed_at, changed_by) "
                   "VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET rev=excluded.rev, "
                   "doc=excluded.doc, state=excluded.state, changed_at=excluded.changed_at, "
                   "changed_by=excluded.changed_by", (aid, rev, text, state, at, at, by))
        db.commit()
        compact_assistant(db, aid)
    return rev


def assistant_set_state(db, aid, state, by):
    if state not in ASSISTANT_STATES:
        raise ValueError(state)
    db.execute("UPDATE assistants SET state=?, changed_at=?, changed_by=? WHERE id=?",
               (state, now(), by, aid))
    db.commit()


def compact_assistant(db, aid, keep_last=KEEP_LAST):
    """D6: behalten werden die letzten `keep_last`, die geltende und jede Revision, auf
    die eine Messzeile zeigt (so lange wie die Messzeile lebt). Bei den übrigen geht der
    Text; Nummer, Zeit und wer bleiben. Gibt die Zahl der verdichteten zurück."""
    revs = [r["rev"] for r in db.execute(
        "SELECT rev FROM assistant_revisions WHERE id=? AND compacted=0 ORDER BY rev DESC", (aid,))]
    if len(revs) <= keep_last:
        return 0
    used = {r["assistant_rev"] for r in db.execute(
        "SELECT DISTINCT assistant_rev FROM usage WHERE assistant=? AND assistant_rev>0", (aid,))}
    # 0.23: ebenso jede Revision, die eine Bewertung nennt (RFC-0101 D8: ein Stern, der
    # einen Prompt nennt, den niemand mehr lesen kann, misst nichts).
    used |= {r["rev"] for r in db.execute("SELECT DISTINCT rev FROM ratings WHERE assistant=?", (aid,))}
    # 0.25: und jede, zu der ein Vorgang ging
    used |= {r["rev"] for r in db.execute("SELECT DISTINCT rev FROM assistant_cases WHERE assistant=?", (aid,))}
    cur = db.execute("SELECT rev FROM assistants WHERE id=?", (aid,)).fetchone()
    keep = set(revs[:keep_last]) | used | ({cur["rev"]} if cur else set())
    drop = [r for r in revs if r not in keep]
    for r in drop:
        db.execute("UPDATE assistant_revisions SET doc='', compacted=1 WHERE id=? AND rev=?", (aid, r))
    db.commit()
    return len(drop)


# ---- Bewertungen (0.23, RFC-0101 Stufe 2)

RATING_TEXT_DAYS = 180               # D4: danach geht der Text, die Sterne bleiben
RATING_FIELDS = ("rev", "scope", "round", "stars", "model", "model_light", "light", "view",
                 "gateway", "tools", "ms")


def rating_write(db, aid, who, slot, r):
    """Setzt (oder ersetzt) die Bewertung dieser Person an dieser Stelle. `r` ist geprüft."""
    at = now()
    db.execute(
        "INSERT INTO ratings (assistant, rev, who, slot, scope, round, stars, model, model_light,"
        " light, view, gateway, tools, ms, at, text) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(assistant, who, slot) DO UPDATE SET rev=excluded.rev, stars=excluded.stars,"
        " model=excluded.model, model_light=excluded.model_light, light=excluded.light,"
        " view=excluded.view, gateway=excluded.gateway, tools=excluded.tools, ms=excluded.ms,"
        " at=excluded.at, text=excluded.text",
        (aid, r["rev"], who, slot, r["scope"], r["round"], r["stars"], r["model"], r["model_light"],
         r["light"], r["view"], r["gateway"], json.dumps(r["tools"], ensure_ascii=False), r["ms"], at,
         json.dumps(r["text"], ensure_ascii=False) if r.get("text") else ""))
    purge_rating_texts(db)
    db.commit()


def rating_withdraw(db, aid, who, slot):
    n = db.execute("DELETE FROM ratings WHERE assistant=? AND who=? AND slot=?", (aid, who, slot)).rowcount
    db.commit()
    return n


def purge_rating_texts(db, days=RATING_TEXT_DAYS):
    """D4: Texte älter als `days` Tage gehen; die Bewertung bleibt."""
    cut = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return db.execute("UPDATE ratings SET text='' WHERE text!='' AND at<?", (cut,)).rowcount


def _rating(r, with_who=False):
    d = {k: r[k] for k in RATING_FIELDS}
    d["tools"] = json.loads(r["tools"] or "[]")
    d["at"] = r["at"]
    if r["text"]:
        d["text"] = json.loads(r["text"])
    if with_who:
        d["assistant"] = r["assistant"]
    return d


def ratings_of(db, aid, limit=500):
    """Für die, die den Assistenten pflegen: Zusammenfassung je Revision und die Bewertungen
    (ohne `who` -- gezählt wird, wie viele Personen, nicht welche)."""
    revs = {}
    for r in db.execute("SELECT rev, stars, COUNT(*) AS n FROM ratings WHERE assistant=? "
                        "GROUP BY rev, stars", (aid,)):
        d = revs.setdefault(r["rev"], {"rev": r["rev"], "count": 0, "stars": [0] * 6})
        d["stars"][r["stars"]] += r["n"]
        d["count"] += r["n"]
    for r in db.execute("SELECT rev, COUNT(DISTINCT who) AS p FROM ratings WHERE assistant=? "
                        "GROUP BY rev", (aid,)):
        revs[r["rev"]]["persons"] = r["p"]
    for d in revs.values():
        d["low"] = round(sum(d["stars"][:3]) / d["count"], 3) if d["count"] else 0
    rows = [_rating(r) for r in db.execute(
        "SELECT * FROM ratings WHERE assistant=? ORDER BY id DESC LIMIT ?", (aid, int(limit)))]
    return {"revisions": sorted(revs.values(), key=lambda d: -d["rev"]), "ratings": rows}


# ---- Auswertung je Revision (0.25, RFC-0097 Stufe 2 / RFC-0101 Stufe 4)

def case_note(db, aid, rev, who, slot):
    """Ein Vorgang zu `aid@rev`; derselbe (slot) zählt einmal. True, wenn neu gezählt."""
    cur = db.execute("INSERT OR IGNORE INTO assistant_cases (assistant, rev, who, slot, at) "
                     "VALUES (?,?,?,?,?)", (aid, int(rev), who, slot, now()))
    db.commit()
    return cur.rowcount == 1


def assistant_evaluation(db, aid):
    """Je Revision: wann und von wem, aus den Messzeilen Aufrufe, Ablehnungen, Fehler und
    Personen (nur gezählt), dazu Sterne und Vorgänge. Die Messzeilen leben nur so lange wie
    ihre Aufbewahrung -- das steht dabei, wo sie gezeigt werden."""
    out = {r["rev"]: dict(r, calls=0, refused=0, errors=0, persons=0, cases=0,
                          ratings={"count": 0, "stars": [0] * 6, "low": 0, "persons": 0})
           for r in assistant_revisions(db, aid)}
    for r in db.execute(
            "SELECT assistant_rev AS rev, COUNT(*) AS calls, "
            "SUM(outcome IN ('refused','limited')) AS refused, "
            "SUM(outcome IN ('upstream_error','tool_error')) AS errors, "
            "COUNT(DISTINCT NULLIF(user_sub,'')) AS persons "
            "FROM usage WHERE assistant=? AND assistant_rev>0 GROUP BY assistant_rev", (aid,)):
        if r["rev"] in out:
            out[r["rev"]].update(calls=r["calls"], refused=r["refused"] or 0,
                                 errors=r["errors"] or 0, persons=r["persons"])
    for d in ratings_of(db, aid, limit=0)["revisions"]:
        if d["rev"] in out:
            out[d["rev"]]["ratings"] = {"count": d["count"], "stars": d["stars"], "low": d["low"],
                                        "persons": d.get("persons", 0)}
    for r in db.execute("SELECT rev, COUNT(*) AS n FROM assistant_cases WHERE assistant=? "
                        "GROUP BY rev", (aid,)):
        if r["rev"] in out:
            out[r["rev"]]["cases"] = r["n"]
    oldest = db.execute("SELECT MIN(time) AS t FROM usage").fetchone()
    return {"revisions": sorted(out.values(), key=lambda d: -d["rev"]),
            "usage_since": (oldest["t"] if oldest else "") or ""}


def assistant_diff(db, aid, a, b):
    """(Zeilen eines Vergleichs von Revision a nach b, None) oder (None, Satz)."""
    import difflib
    docs = []
    for rev in (a, b):
        r = assistant_revision(db, aid, rev)
        if r is None:
            return None, f"Revision {rev} gibt es nicht."
        if r["doc"] is None:
            return None, f"Revision {rev} ist verdichtet (D6): ihr Text ist nicht mehr da."
        d = {k: v for k, v in r["doc"].items() if k != "system"}
        text = json.dumps(d, ensure_ascii=False, indent=1, sort_keys=True).splitlines()
        text += ["", "--- system ---"] + str(r["doc"].get("system") or "").splitlines()
        docs.append(text)
    return list(difflib.unified_diff(docs[0], docs[1], f"Revision {a}", f"Revision {b}",
                                     lineterm="", n=2)), None


def assistant_export(db, aid, rev=None):
    """Austauschdatei {v: 1, assistants: [doc]} einer Revision, ohne Besitzer und Nummer
    (RFC-0097 §2.3): `owner` ist "-" (das Format verlangt das Feld; wer importiert, wird
    Besitzer) -- oder None (unbekannt oder verdichtet)."""
    row = assistant_row(db, aid)
    if not row:
        return None
    doc = row["doc"] if rev in (None, row["rev"]) else (assistant_revision(db, aid, rev) or {}).get("doc")
    if not doc:
        return None
    return {"v": 1, "assistants": [dict({k: v for k, v in doc.items() if k not in ("owner", "rev")}, owner="-")]}


def ratings_mine(db, who, limit=500):
    return [_rating(r, with_who=True) for r in db.execute(
        "SELECT * FROM ratings WHERE who=? ORDER BY id DESC LIMIT ?", (who, int(limit)))]
