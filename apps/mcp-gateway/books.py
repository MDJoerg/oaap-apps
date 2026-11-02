"""Die Bücher des MCP-Gateways — eine SQLite-Datei unter /data.

Was beide Gateways gleich führen (Schlüssel nur als SHA-256, Kontext,
Kopie des Kontexts in der Messzeile, Prüfspur, Rate), kommt aus
`gwcore/`. Hier steht, was nur dieses Gateway hat:

- `servers`  — MCP-Server: Adresse, Zugang (Zugangsdaten nur
               verschlüsselt), Ampel, Grenzen, zuletzt ausgehandelte
               Protokollversion
- `catalog`  — was jeder Server anbietet, mit Fingerabdruck (Spec §4)
- `views`    — Sichten: Name = Pfad, ein Server in 0.1, höchste Klasse
- `items`    — die freigegebenen Tools einer Sicht, mit dem
               **festgehaltenen** Text (Spec §4.2)
- `usage`    — eine Zeile je `tools/call` (Spec §9)
- `protocol` — Protokollverkehr, je Tag gezählt, nicht als Verbrauch

Die härteste Regel (Spec §10) ist auch hier eine Eigenschaft der
Bauform: **Es gibt keine Spalte, in die ein Argument oder ein Ergebnis
passen würde.**
"""
import hashlib
import json
import re

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
CREATE TABLE IF NOT EXISTS catalog (
  server      TEXT NOT NULL,
  name        TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  doc         TEXT NOT NULL,
  present     INTEGER NOT NULL DEFAULT 1,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  PRIMARY KEY (server, name)
);
CREATE TABLE IF NOT EXISTS views (
  name        TEXT PRIMARY KEY,
  server      TEXT NOT NULL,
  max_effect  TEXT NOT NULL DEFAULT 'read',
  created     TEXT NOT NULL,
  updated     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
  view         TEXT NOT NULL,
  tool         TEXT NOT NULL,
  effect       TEXT NOT NULL,
  approved_fp  TEXT NOT NULL,
  approved_doc TEXT NOT NULL,
  approved_by  TEXT NOT NULL,
  approved_at  TEXT NOT NULL,
  PRIMARY KEY (view, tool)
);
"""

ADDED_COLUMNS = tuple(("usage", col, decl) for col, decl in context.USAGE_COLUMNS)

KEY_PREFIX = "mcp-oaap-"
EFFECTS = ("read", "write", "execute")
EFFECT_RANK = {e: i for i, e in enumerate(EFFECTS)}
EFFECT_WORDS = {"read": "lesen", "write": "schreiben", "execute": "ausführen"}
LIGHTS = ("green", "yellow", "red")
LIGHT_RANK = {e: i for i, e in enumerate(LIGHTS)}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
AUTH_KINDS = ("none", "bearer", "basic")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")
# Was ein Server über ein Tool sagt und was der Fingerabdruck deckt (Spec §4.2).
TOOL_FIELDS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations")


def connect(path):
    # Viele Threads, eine Datei: jede Anweisung unter einer Sperre (gwcore.ledger.Serialized).
    return ledger.connect_serialized(path, SCHEMA, migrate)


def migrate(db):
    ledger.add_columns(db, ADDED_COLUMNS)


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
    raise ValueError("Zugang ist none, bearer, basic oder header:<Name>.")


# ------------------------------------------------------------------ Schlüssel

def issue(db, label, actor, views=(), max_effect="read", ceiling="yellow", budget_calls=0,
          rate_per_min=0, tenant="default", **ctx):
    if not views:
        raise ValueError("Ein Schlüssel braucht mindestens eine Sicht — ohne genannte Sicht "
                         "erreicht er nichts.")
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
             "rate_per_min": max(0, int(rate_per_min or 0))}
    return ledger.issue(db, label, actor, KEY_PREFIX, extra, ctx, tenant,
                        f"sichten={','.join(views)} bis={max_effect} ampel={ceiling}")


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


def save_server(db, actor, name, address, auth_kind, light, limits, sealed_credential=None,
                drop_credential=False):
    """Legt an oder ändert. `sealed_credential` ist schon verschlüsselt; None = unverändert."""
    valid_name(name, "eines Servers")
    auth_kind = valid_auth(auth_kind)
    if light not in LIGHT_RANK:
        raise ValueError("Ampel ist green, yellow oder red.")
    old = server_row(db, name)
    credential = old["credential"] if old else ""
    if drop_credential:
        credential = ""
    if sealed_credential is not None:
        credential = sealed_credential
    if address.startswith("destination:") and credential:
        raise ValueError("Ein Server hinter einer Destination trägt keine eigenen Zugangsdaten: "
                         "Die Plattform setzt die der Destination ein (eine Wahrheit, nicht zwei).")
    if auth_kind != "none" and not credential:
        raise ValueError(f"Zugang {auth_kind} braucht Zugangsdaten.")
    stamp = now()
    db.execute("INSERT INTO servers (name,address,auth_kind,credential,light,limits,created,updated) "
               "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET address=excluded.address,"
               "auth_kind=excluded.auth_kind,credential=excluded.credential,light=excluded.light,"
               "limits=excluded.limits,updated=excluded.updated",
               (name, address, auth_kind, credential, light, json.dumps(limits, sort_keys=True),
                stamp, stamp))
    changes = []
    if not old:
        changes.append("angelegt")
    else:
        for field, new in (("address", address), ("auth_kind", auth_kind), ("light", light),
                           ("limits", json.dumps(limits, sort_keys=True))):
            if old[field] != new:
                changes.append(f"{field}: {old[field]} → {new}")
        if old["credential"] != credential:
            changes.append("Zugangsdaten " + ("entfernt" if not credential else "gesetzt"))
    if changes:
        log(db, actor, "server.save", name, "; ".join(changes))
    db.commit()


def delete_server(db, actor, name):
    used = [r["name"] for r in db.execute("SELECT name FROM views WHERE server=?", (name,))]
    if used:
        raise ValueError(f"Server „{name}“ steht noch in Sichten: {', '.join(used)}.")
    db.execute("DELETE FROM servers WHERE name=?", (name,))
    db.execute("DELETE FROM catalog WHERE server=?", (name,))
    log(db, actor, "server.delete", name)
    db.commit()


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

def tool_doc(tool):
    """Was vom Server über ein Tool festgehalten wird — genau die Felder des Fingerabdrucks."""
    return {f: tool[f] for f in TOOL_FIELDS if f in tool}


def fingerprint(doc):
    canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def store_catalog(db, server, tools):
    """Schreibt das Gelesene. Gibt die Namen freigegebener Tools zurück, die neu gesperrt sind."""
    stamp = now()
    before = {(i["view"], i["tool"]): item_state(db, server, i) for i in approved_items(db, server)}
    seen = set()
    for tool in tools:
        doc = tool_doc(tool)
        fp = fingerprint(doc)
        seen.add(doc["name"])
        db.execute("INSERT INTO catalog (server,name,fingerprint,doc,present,first_seen,last_seen) "
                   "VALUES (?,?,?,?,1,?,?) ON CONFLICT(server,name) DO UPDATE SET "
                   "fingerprint=excluded.fingerprint,doc=excluded.doc,present=1,"
                   "last_seen=excluded.last_seen",
                   (server, doc["name"], fp, json.dumps(doc, ensure_ascii=False), stamp, stamp))
    for row in db.execute("SELECT name FROM catalog WHERE server=?", (server,)).fetchall():
        if row["name"] not in seen:
            db.execute("UPDATE catalog SET present=0 WHERE server=? AND name=?",
                       (server, row["name"]))
    changed = []
    for item in approved_items(db, server):
        state = item_state(db, server, item)
        if state != "active" and before.get((item["view"], item["tool"])) == "active":
            changed.append(f"{item['view']}/{item['tool']}")
            log(db, "katalog", "item." + state, f"{item['view']}/{item['tool']}",
                "Der Server meldet einen anderen Text als freigegeben" if state == "suspended"
                else "Der Server bietet das Tool nicht mehr an")
    db.commit()
    return changed


def catalog(db, server):
    return list(db.execute("SELECT * FROM catalog WHERE server=? ORDER BY present DESC, name",
                           (server,)))


def catalog_row(db, server, name):
    return db.execute("SELECT * FROM catalog WHERE server=? AND name=?", (server, name)).fetchone()


# ------------------------------------------------------------------ Sichten

def view_row(db, name):
    return db.execute("SELECT * FROM views WHERE name=?", (name,)).fetchone()


def views(db):
    return list(db.execute("SELECT * FROM views ORDER BY name"))


def save_view(db, actor, name, server, max_effect):
    valid_name(name, "einer Sicht")
    if not server_row(db, server):
        raise ValueError(f"Einen Server „{server}“ gibt es nicht.")
    if max_effect not in EFFECT_RANK:
        raise ValueError("Höchste Wirkungsklasse ist read, write oder execute.")
    old = view_row(db, name)
    if old and old["server"] != server and items(db, name):
        raise ValueError("Der Server einer Sicht mit Tools lässt sich nicht wechseln — "
                         "erst die Tools herausnehmen.")
    stamp = now()
    db.execute("INSERT INTO views (name,server,max_effect,created,updated) VALUES (?,?,?,?,?) "
               "ON CONFLICT(name) DO UPDATE SET server=excluded.server,"
               "max_effect=excluded.max_effect,updated=excluded.updated",
               (name, server, max_effect, stamp, stamp))
    if not old:
        log(db, actor, "view.save", name, f"angelegt, Server {server}, bis {max_effect}")
    elif old["max_effect"] != max_effect or old["server"] != server:
        log(db, actor, "view.save", name,
            f"Server {old['server']} → {server}; bis {old['max_effect']} → {max_effect}")
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


def items(db, view):
    return list(db.execute("SELECT * FROM items WHERE view=? ORDER BY tool", (view,)))


def approved_items(db, server):
    """Alle freigegebenen Einträge über alle Sichten dieses Servers."""
    return [dict(r) for r in db.execute("SELECT i.*, v.server FROM items i "
                                        "JOIN views v ON v.name=i.view WHERE v.server=?", (server,))]


def item_state(db, server, item):
    """`active`, `suspended` oder `missing` (Spec §4.2)."""
    cat = catalog_row(db, server, item["tool"])
    if not cat or not cat["present"]:
        return "missing"
    return "active" if cat["fingerprint"] == item["approved_fp"] else "suspended"


def approve(db, actor, view, tool, effect):
    """Legt ein Tool in eine Sicht — oder bestätigt einen geänderten Text. Hält den Text fest."""
    import argcheck
    vrow = view_row(db, view)
    if not vrow:
        raise ValueError(f"Eine Sicht „{view}“ gibt es nicht.")
    if effect not in EFFECT_RANK:
        raise ValueError("Wirkungsklasse ist read, write oder execute.")
    cat = catalog_row(db, vrow["server"], tool)
    if not cat or not cat["present"]:
        raise ValueError(f"Der Server bietet „{tool}“ nicht an (Katalog neu lesen?).")
    doc = json.loads(cat["doc"])
    remote = argcheck.remote_refs(doc.get("inputSchema"))
    if remote:
        raise ValueError(f"Das Schema von „{tool}“ verweist nach außen ({remote[0]}); "
                         "so ein Schema lässt sich nicht prüfen und wird nicht freigegeben.")
    old = db.execute("SELECT * FROM items WHERE view=? AND tool=?", (view, tool)).fetchone()
    db.execute("INSERT INTO items (view,tool,effect,approved_fp,approved_doc,approved_by,approved_at) "
               "VALUES (?,?,?,?,?,?,?) ON CONFLICT(view,tool) DO UPDATE SET effect=excluded.effect,"
               "approved_fp=excluded.approved_fp,approved_doc=excluded.approved_doc,"
               "approved_by=excluded.approved_by,approved_at=excluded.approved_at",
               (view, tool, effect, cat["fingerprint"], cat["doc"], actor, now()))
    if not old:
        detail = f"freigegeben als {effect}, Fingerabdruck {cat['fingerprint'][:12]}"
    else:
        bits = []
        if old["approved_fp"] != cat["fingerprint"]:
            bits.append(f"Text bestätigt {old['approved_fp'][:12]} → {cat['fingerprint'][:12]}")
        if old["effect"] != effect:
            bits.append(f"Klasse {old['effect']} → {effect}")
        detail = "; ".join(bits) or "unverändert"
    log(db, actor, "item.approve", f"{view}/{tool}", detail)
    db.commit()


def remove_item(db, actor, view, tool):
    cur = db.execute("DELETE FROM items WHERE view=? AND tool=?", (view, tool))
    if cur.rowcount:
        log(db, actor, "item.remove", f"{view}/{tool}")
    db.commit()
    return bool(cur.rowcount)


# ------------------------------------------------------------------ Messung

def record(db, key, view, tool, server, server_tool, outcome, reason, ms, bytes_up, bytes_down):
    """Eine Messzeile je `tools/call`. `key` ist die Schlüsselzeile vom Beginn der Anfrage."""
    cols = ["time", "key_id", "view", "tool", "server", "server_tool", "procedure", "outcome",
            "reason", "ms", "bytes_up", "bytes_down"] + context.snapshot_columns()
    values = [now(), key["id"], view, tool, server, server_tool, "T", outcome, reason, int(ms),
              int(bytes_up), int(bytes_down)] + context.snapshot_values(key)
    db.execute(f"INSERT INTO usage ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
               values)
    db.commit()


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
