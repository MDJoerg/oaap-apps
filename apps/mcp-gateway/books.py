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
)

KEY_PREFIX = "mcp-oaap-"
EFFECTS = ("read", "write", "execute")
EFFECT_RANK = {e: i for i, e in enumerate(EFFECTS)}
EFFECT_WORDS = {"read": "lesen", "write": "schreiben", "execute": "ausführen"}
LIGHTS = ("green", "yellow", "red")
LIGHT_RANK = {e: i for i, e in enumerate(LIGHTS)}
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
AUTH_KINDS = ("none", "bearer", "basic")
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def connect(path):
    # Viele Threads, eine Datei: jede Anweisung unter einer Sperre (gwcore.ledger.Serialized).
    return ledger.connect_serialized(path, SCHEMA, migrate)


def migrate(db):
    ledger.add_columns(db, ADDED_COLUMNS)
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
    used = [r["view"] for r in db.execute("SELECT DISTINCT view FROM items WHERE server=?", (name,))]
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


def save_view(db, actor, name, max_effect, requests=None):
    """Legt an oder ändert. `requests` = {elicitation|roots|sampling: pass|refuse}; None = unverändert."""
    valid_name(name, "einer Sicht")
    if max_effect not in EFFECT_RANK:
        raise ValueError("Höchste Wirkungsklasse ist read, write oder execute.")
    old = view_row(db, name)
    current = view_requests(old) if old else dict(REQUESTS_NEW)
    if requests:
        for r, v in requests.items():
            if r not in REQUESTS or v not in ("pass", "refuse"):
                raise ValueError("Rückfragen: elicitation, roots, sampling — je pass oder refuse.")
            current[r] = v
    stamp = now()
    db.execute("INSERT INTO views (name,server,max_effect,requests,created,updated) "
               "VALUES (?,'',?,?,?,?) ON CONFLICT(name) DO UPDATE SET "
               "max_effect=excluded.max_effect,requests=excluded.requests,updated=excluded.updated",
               (name, max_effect, json.dumps(current, sort_keys=True), stamp, stamp))
    words = ", ".join(f"{r} {current[r]}" for r in REQUESTS)
    if not old:
        log(db, actor, "view.save", name, f"angelegt, bis {max_effect}; Rückfragen: {words}")
    else:
        bits = []
        if old["max_effect"] != max_effect:
            bits.append(f"bis {old['max_effect']} → {max_effect}")
        before = view_requests(old)
        bits += [f"{r} {before[r]} → {current[r]}" for r in REQUESTS if before[r] != current[r]]
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
    """`active`, `suspended` oder `missing` (Spec §4.2)."""
    cat = catalog_row(db, item["server"], item["kind"], item["source"])
    if not cat or not cat["present"]:
        return "missing"
    return "active" if cat["fingerprint"] == item["approved_fp"] else "suspended"


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
    db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,approved_doc,"
               "approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
               (view, kind, name, server, source, effect, cat["fingerprint"], cat["doc"], actor,
                now()))
    log(db, actor, "item.approve", f"{view}/{name}",
        f"{kind} {server}:{source} als {effect}, Fingerabdruck {cat['fingerprint'][:12]}")
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

def record(db, key, view, kind, name, server, source, outcome, reason, ms, bytes_up, bytes_down):
    """Eine Messzeile je Aufruf. `key` ist die Schlüsselzeile vom Beginn der Anfrage.

    `name`/`source` sind Namen in der Sicht und beim Server — bei einer
    Vorlage die Vorlage, nie die eingesetzte URI: In ihr stünden Werte.
    """
    cols = ["time", "key_id", "view", "kind", "tool", "server", "server_tool", "procedure",
            "outcome", "reason", "ms", "bytes_up", "bytes_down"] + context.snapshot_columns()
    values = [now(), key["id"], view, kind, name, server, source, "T", outcome, reason, int(ms),
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
