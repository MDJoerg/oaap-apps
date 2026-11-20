"""Systemlandschaft und Multi-System-Sichten (RFC-0114 Stufe 1, mcp-gateway 0.32.0).

Ein **Zielsystem** ist, wohin ein Werkzeugaufruf über einen MCP-Server reicht:
ein SAP-System, eine Jira-Seite, ein Wiki. Die **Systemlandschaft** ist die
Liste der Zielsysteme dieses Gateways (D1, D3). Eine **Multi-System-Sicht**
wird einmal an einem **Referenzsystem** ausgeprägt -- ihre Einträge sind die
gewohnten, auf Servern des Referenzsystems -- und für weitere Systeme
angeboten: je Eintrag und System *gleich* (derselbe Text wie freigegeben),
*freigegeben* (abweichend, aber für dieses System eigens freigegeben),
*abweichend* (nicht angeboten, bis jemand freigibt), *fehlt* oder
*mehrdeutig*. Nichts wird still übernommen (RFC-0065, default deny).

Im Chat steht jedes Werkzeug einmal, mit dem Pflichtargument `system` (D2):
nur aktive Systeme, deren Ampel das Gespräch und der Schlüssel zulassen und
für die der Eintrag angeboten ist. Das Gateway nimmt das Argument heraus und
ruft den Server dieses Systems. Das Werkzeug `systeme` nennt die Systeme der
Sicht -- nie eine Adresse, eine Destination oder einen Zugang (S1).

Stufe 1 (D6): nur Werkzeuge; Ressourcen und Prompts einer Multi-System-Sicht
bleiben die des Referenzsystems.
"""
import json
import re

import books

ROLES = ("development", "quality", "production", "sandbox", "training", "other")
ROLE_WORDS = {"development": "Entwicklung", "quality": "Qualität", "production": "Produktion",
              "sandbox": "Sandbox", "training": "Schulung", "other": "sonstige"}
# D5: die Ampel kommt aus der Rolle, wenn niemand eine setzt; nie lockerer als
# ein Server des Systems (S5)
ROLE_LIGHT = {"production": "yellow"}
SYSTEM_TOOL = "systeme"
SYSTEM_ARG = "system"
FACT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,23}$")
FACTS_MAX = 10
FACT_MAX = 100
TITLE_MAX = 120
DESC_MAX = 1000
SECRET_WORDS = ("key", "token", "secret", "password", "credential", "passwort", "kennwort")
STATE_WORDS = {"referenz": "Referenz", "gleich": "gleich", "freigegeben": "freigegeben",
               "abweichend": "abweichend", "fehlt": "fehlt", "mehrdeutig": "mehrdeutig",
               "gesperrt": "gesperrt (Referenz)"}
SERVED = ("referenz", "gleich", "freigegeben")

SCHEMA = books.LANDSCAPE_SCHEMA   # in books, damit connect() sie anlegt


class Refused(Exception):
    """Ein Aufruf geht nicht an einen Server: (reason, Satz)."""

    def __init__(self, reason, text):
        super().__init__(text)
        self.reason, self.text = reason, text


# ------------------------------------------------------------ Zielsysteme

def system_row(db, sid):
    return db.execute("SELECT * FROM systems WHERE id=?", (sid,)).fetchone()


def systems(db):
    return list(db.execute("SELECT * FROM systems ORDER BY id"))


def servers_of(db, sid):
    return [r["server"] for r in db.execute(
        "SELECT server FROM system_servers WHERE system=? ORDER BY server", (sid,))]


def system_of_server(db, server):
    r = db.execute("SELECT system FROM system_servers WHERE server=?", (server,)).fetchone()
    return r["system"] if r else ""


def facts_of(row):
    try:
        f = json.loads(row["facts"] or "{}")
    except ValueError:
        f = {}
    return f if isinstance(f, dict) else {}


def clean_facts(facts):
    """Angaben je Art (SAP: sid, client) -- nie geheim, kurz, wenige."""
    if facts in (None, ""):
        return {}
    if not isinstance(facts, dict):
        raise ValueError("Angaben sind name=wert, durch Kommas getrennt.")
    if len(facts) > FACTS_MAX:
        raise ValueError(f"Höchstens {FACTS_MAX} Angaben.")
    out = {}
    for k, v in facts.items():
        k = str(k).strip().lower()
        if not FACT_KEY.match(k):
            raise ValueError(f"„{k}“ ist kein Name einer Angabe (a-z, 0-9, _).")
        if any(w in k for w in SECRET_WORDS):
            raise ValueError(f"„{k}“ klingt nach einem Geheimnis -- die Systemlandschaft hält nie "
                             "einen Zugang (RFC-0114 S1).")
        v = str(v).strip()
        if len(v) > FACT_MAX:
            raise ValueError(f"„{k}“: höchstens {FACT_MAX} Zeichen.")
        out[k] = v
    return out


def parse_facts(text):
    out = {}
    for part in [x for x in str(text or "").split(",") if x.strip()]:
        k, eq, v = part.partition("=")
        if not eq:
            raise ValueError(f"„{part.strip()}“ ist nicht name=wert.")
        out[k.strip()] = v.strip()
    return clean_facts(out)


def save_system(db, actor, sid, title, role="other", active=True, kind="", facts=None,
                light="", description="", servers=None):
    """Legt an oder ändert. `servers` None = unverändert. Wirft ValueError."""
    books.valid_name(sid, "eines Zielsystems")
    title = str(title or "").strip()
    if not title or len(title) > TITLE_MAX:
        raise ValueError(f"Ein Zielsystem braucht einen Titel (1 bis {TITLE_MAX} Zeichen).")
    if role not in ROLES:
        raise ValueError("Rolle ist eine von: " + ", ".join(ROLE_WORDS[r] for r in ROLES) + ".")
    if light and light not in books.LIGHTS:
        raise ValueError("Ampel ist green, yellow, red oder leer (aus der Rolle).")
    kind = str(kind or "").strip().lower()
    if kind and not books.NAME.match(kind):
        raise ValueError("Die Art ist ein kurzes Wort (z. B. sap, jira).")
    description = str(description or "").strip()[:DESC_MAX]
    facts = clean_facts(facts)
    if servers is not None:
        for s in servers:
            if not books.server_row(db, s):
                raise ValueError(f"Einen Server „{s}“ gibt es nicht.")
            other = system_of_server(db, s)
            if other and other != sid:
                raise ValueError(f"Der Server „{s}“ gehört schon zum Zielsystem „{other}“; ein "
                                 "Server reicht an genau einen Ort.")
    old = system_row(db, sid)
    stamp = books.now()
    if old:
        db.execute("UPDATE systems SET title=?, role=?, active=?, kind=?, facts=?, light=?, "
                   "description=?, updated=? WHERE id=?",
                   (title, role, 1 if active else 0, kind, json.dumps(facts, sort_keys=True),
                    light, description, stamp, sid))
    else:
        db.execute("INSERT INTO systems (id,title,role,active,kind,facts,light,description,"
                   "created,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (sid, title, role, 1 if active else 0, kind, json.dumps(facts, sort_keys=True),
                    light, description, stamp, stamp))
    if servers is not None:
        db.execute("DELETE FROM system_servers WHERE system=?", (sid,))
        for s in servers:
            db.execute("INSERT INTO system_servers (server, system) VALUES (?,?)", (s, sid))
    books.log(db, actor, "system.save" if old else "system.create", sid,
              f"rolle={role} aktiv={'ja' if active else 'nein'} art={kind or '-'} "
              f"ampel={light or 'aus der Rolle'} server={','.join(servers_of(db, sid)) or '-'}")
    db.commit()
    return system_row(db, sid)


def delete_system(db, actor, sid):
    used = [r["view"] for r in db.execute("SELECT view FROM view_systems WHERE system=?", (sid,))]
    if used:
        raise ValueError(f"Das Zielsystem „{sid}“ steht in Sichten: {', '.join(used)} -- erst dort "
                         "herausnehmen.")
    db.execute("DELETE FROM system_servers WHERE system=?", (sid,))
    db.execute("DELETE FROM systems WHERE id=?", (sid,))
    books.log(db, actor, "system.delete", sid, "")
    db.commit()


def system_light(db, row):
    """Die Ampel eines Systems: die gesetzte, sonst die der Rolle, sonst grün --
    und nie lockerer als einer seiner Server (S5)."""
    light = row["light"] or ROLE_LIGHT.get(row["role"], "green")
    for s in servers_of(db, row["id"]):
        srow = books.server_row(db, s)
        sl = srow["light"] if srow else "red"
        if books.LIGHT_RANK[sl] > books.LIGHT_RANK[light]:
            light = sl
    return light


# ------------------------------------------------------- Multi-System-Sicht

def reference_of(db, view):
    vrow = books.view_row(db, view)
    return (vrow["reference"] if vrow and "reference" in vrow.keys() else "") or ""


def is_multi(db, view):
    return bool(reference_of(db, view))


def view_system_ids(db, view):
    ref = reference_of(db, view)
    rows = [r["system"] for r in db.execute(
        "SELECT system FROM view_systems WHERE view=? ORDER BY added_at, system", (view,))]
    return ([ref] if ref else []) + [s for s in rows if s != ref]


def make_multi(db, actor, view, reference):
    """Eine Sicht wird Multi-System-Sicht mit diesem Referenzsystem. Ihre Einträge
    müssen auf Servern des Referenzsystems liegen."""
    if not books.view_row(db, view):
        raise ValueError(f"Eine Sicht „{view}“ gibt es nicht.")
    if not system_row(db, reference):
        raise ValueError(f"Ein Zielsystem „{reference}“ gibt es nicht.")
    if is_multi(db, view):
        raise ValueError(f"„{view}“ ist schon eine Multi-System-Sicht (Referenz "
                         f"„{reference_of(db, view)}“).")
    mine = set(servers_of(db, reference))
    if not mine:
        raise ValueError(f"Dem Zielsystem „{reference}“ ist noch kein Server zugeordnet.")
    alien = sorted({i["server"] for i in books.items(db, view)} - mine)
    if alien:
        raise ValueError(f"Die Sicht hat Einträge von Servern außerhalb von „{reference}“ "
                         f"({', '.join(alien)}) -- erst herausnehmen.")
    if any(i["name"] == SYSTEM_TOOL for i in books.items(db, view, "tool")):
        raise ValueError(f"Ein Eintrag heißt „{SYSTEM_TOOL}“ -- der Name gehört in einer "
                         "Multi-System-Sicht dem Gateway; erst umbenennen.")
    db.execute("UPDATE views SET reference=?, updated=? WHERE name=?", (reference, books.now(), view))
    db.execute("INSERT OR IGNORE INTO view_systems (view, system, added_by, added_at) "
               "VALUES (?,?,?,?)", (view, reference, actor, books.now()))
    books.log(db, actor, "view.multi", view, f"referenz={reference}")
    db.commit()


def add_system(db, actor, view, sid):
    if not is_multi(db, view):
        raise ValueError(f"„{view}“ ist keine Multi-System-Sicht -- erst ein Referenzsystem wählen.")
    if not system_row(db, sid):
        raise ValueError(f"Ein Zielsystem „{sid}“ gibt es nicht.")
    if not servers_of(db, sid):
        raise ValueError(f"Dem Zielsystem „{sid}“ ist noch kein Server zugeordnet.")
    db.execute("INSERT OR IGNORE INTO view_systems (view, system, added_by, added_at) "
               "VALUES (?,?,?,?)", (view, sid, actor, books.now()))
    books.log(db, actor, "view.system.add", view, f"system={sid}")
    db.commit()


def remove_system(db, actor, view, sid):
    if sid == reference_of(db, view):
        raise ValueError("Das Referenzsystem bleibt; eine andere Referenz heißt: neue Sicht.")
    db.execute("DELETE FROM view_systems WHERE view=? AND system=?", (view, sid))
    db.execute("DELETE FROM system_items WHERE view=? AND system=?", (view, sid))
    books.log(db, actor, "view.system.remove", view, f"system={sid}")
    db.commit()


def availability(db, view, item, sid):
    """(Zustand, Server, freigegebener Text oder None, Notiz) dieses Eintrags für
    dieses System. Nur Werkzeuge kennen andere Systeme als die Referenz (D6)."""
    ref = reference_of(db, view)
    ref_state = books.item_state(db, item)
    if sid == ref:
        if ref_state != "active":
            return "gesperrt", item["server"], None, f"am Referenzsystem {ref_state}"
        return "referenz", item["server"], json.loads(item["approved_doc"]), ""
    if ref_state != "active":
        return "gesperrt", "", None, f"am Referenzsystem {ref_state}"
    hits = []
    for s in servers_of(db, sid):
        cat = books.catalog_row(db, s, item["kind"], item["source"])
        if cat and cat["present"]:
            hits.append((s, cat))
    if not hits:
        return "fehlt", "", None, "kein Server dieses Systems bietet es an"
    if len(hits) > 1:
        return "mehrdeutig", "", None, ("mehrere Server dieses Systems bieten es an: "
                                        + ", ".join(s for s, _c in hits))
    server, cat = hits[0]
    if cat["fingerprint"] == item["approved_fp"]:
        return "gleich", server, json.loads(item["approved_doc"]), ""
    own = db.execute("SELECT * FROM system_items WHERE view=? AND system=? AND kind=? AND name=?",
                     (view, sid, item["kind"], item["name"])).fetchone()
    if own and own["server"] == server and own["approved_fp"] == cat["fingerprint"]:
        return "freigegeben", server, json.loads(own["approved_doc"]), own["approved_by"]
    return "abweichend", server, None, "der Server meldet einen anderen Text als die Referenz"


def approve_for_system(db, actor, view, sid, kind, name):
    """Gibt einen abweichenden Eintrag für dieses System frei, mit dem Text, den
    sein Server JETZT meldet (festgehalten wie jede Freigabe)."""
    item = books.item_row(db, view, kind, name)
    if not item:
        raise ValueError(f"Die Sicht hat keinen Eintrag „{name}“.")
    if sid not in view_system_ids(db, view):
        raise ValueError(f"Das Zielsystem „{sid}“ gehört nicht zu dieser Sicht.")
    state, server, _doc, note = availability(db, view, item, sid)
    if state not in ("abweichend", "freigegeben"):
        raise ValueError(f"„{name}“ ist für „{sid}“ {STATE_WORDS.get(state, state)}"
                         + (f" ({note})" if note else "") + " -- nichts freizugeben.")
    cat = books.catalog_row(db, server, kind, item["source"])
    db.execute("INSERT OR REPLACE INTO system_items (view,system,kind,name,server,approved_fp,"
               "approved_doc,approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (view, sid, kind, name, server, cat["fingerprint"], cat["doc"], actor, books.now()))
    books.log(db, actor, "view.system.approve", view, f"system={sid} eintrag={name} server={server}")
    db.commit()


def view_light(db, view):
    """Die Ampel einer Multi-System-Sicht für das Öffnen: die des harmlosesten
    aktiven Systems (ein Gespräch auf grün darf die Entwicklung nutzen, auch wenn
    die Produktion gelb ist); geprüft wird je Aufruf (§4.4). Ohne aktives System rot."""
    best = None
    for sid in view_system_ids(db, view):
        row = system_row(db, sid)
        if not row or not row["active"]:
            continue
        light = system_light(db, row)
        if best is None or books.LIGHT_RANK[light] < books.LIGHT_RANK[best]:
            best = light
    return best or "red"


def ceiling_rank(key_ceiling, asked):
    r = books.LIGHT_RANK.get(key_ceiling, 1)
    if asked in books.LIGHT_RANK:
        r = min(r, books.LIGHT_RANK[asked])
    return r


def usable(db, view, item, rank):
    """[(system row, server, doc)] -- wo dieser Eintrag jetzt aufgerufen werden darf."""
    out = []
    for sid in view_system_ids(db, view):
        row = system_row(db, sid)
        if not row or not row["active"]:
            continue
        if books.LIGHT_RANK[system_light(db, row)] > rank:
            continue
        state, server, doc, _n = availability(db, view, item, sid)
        if state in SERVED:
            out.append((row, server, doc))
    return out


def system_line(row):
    f = facts_of(row)
    bits = [ROLE_WORDS.get(row["role"], row["role"])]
    if f.get("sid"):
        bits.append("SID " + f["sid"])
    if f.get("client"):
        bits.append("Mandant " + f["client"])
    return f"{row['id']} = {row['title']} ({', '.join(bits)})"


def shaped_tool(base, choices):
    """Der Eintrag, wie der Client ihn sieht, mit dem Pflichtargument `system`."""
    out = json.loads(json.dumps(base))
    schema = out.get("inputSchema") or {"type": "object"}
    props = dict(schema.get("properties") or {})
    props[SYSTEM_ARG] = {"type": "string", "enum": [r["id"] for r, _s, _d in choices],
                         "description": "Zielsystem: " + "; ".join(system_line(r) for r, _s, _d
                                                                    in choices)}
    schema["properties"] = props
    req = [x for x in (schema.get("required") or []) if x != SYSTEM_ARG]
    schema["required"] = [SYSTEM_ARG] + req
    schema.setdefault("type", "object")
    out["inputSchema"] = schema
    return out


def system_tool(version):
    out = {"name": SYSTEM_TOOL,
           "description": ("Nennt die Zielsysteme dieser Sicht: Kennung, Titel, Rolle (Entwicklung, "
                           "Qualität, Produktion …), aktiv, Ampel und welche Werkzeuge dort "
                           "verfügbar sind. Vor einem Aufruf das passende System wählen (Argument "
                           "`system`) oder die Person fragen."),
           "inputSchema": {"type": "object", "properties": {}}}
    if version >= "2025-06-18":
        out["_meta"] = {"oaap.ai/effect": "read"}
        out["annotations"] = {"readOnlyHint": True}
    return out


def landscape_doc(db, view, rank=2):
    """Was der Chat über die Systeme dieser Sicht lesen darf (S1: nie Adresse,
    Destination, Zugang)."""
    tools = books.items(db, view, "tool")
    out = []
    for sid in view_system_ids(db, view):
        row = system_row(db, sid)
        if not row:
            continue
        light = system_light(db, row)
        avail = [i["name"] for i in tools if availability(db, view, i, sid)[0] in SERVED]
        out.append({"id": row["id"], "title": row["title"], "role": row["role"],
                    "role_label": ROLE_WORDS.get(row["role"], row["role"]),
                    "kind": row["kind"], "active": bool(row["active"]), "light": light,
                    "usable_now": bool(row["active"]) and books.LIGHT_RANK[light] <= rank,
                    "reference": sid == reference_of(db, view),
                    "facts": facts_of(row), "description": row["description"],
                    "tools": avail})
    return {"view": view, "systems": out}


def pick(db, view, item, arguments, rank):
    """Das System aus den Argumenten -> (system row, server, doc, args ohne system).
    Wirft Refused, bevor etwas hinausgeht (S2)."""
    args = dict(arguments) if isinstance(arguments, dict) else {}
    sid = args.pop(SYSTEM_ARG, None)
    if not isinstance(sid, str) or not sid:
        raise Refused("system_missing", f"Das Argument „{SYSTEM_ARG}“ fehlt: welches Zielsystem? "
                                        f"(Werkzeug „{SYSTEM_TOOL}“ nennt sie.)")
    if sid not in view_system_ids(db, view):
        raise Refused("system_unknown", f"Das Zielsystem „{sid[:40]}“ gehört nicht zu dieser Sicht.")
    row = system_row(db, sid)
    if not row or not row["active"]:
        raise Refused("system_inactive", f"Das Zielsystem „{sid}“ ist abgeschaltet.")
    light = system_light(db, row)
    if books.LIGHT_RANK[light] > rank:
        raise Refused("system_light", f"Das Zielsystem „{sid}“ ist {light} eingestuft; das Gespräch "
                                      "oder der Zugang reicht nicht so weit.")
    state, server, doc, note = availability(db, view, item, sid)
    if state not in SERVED:
        raise Refused("system_" + state, f"„{item['name']}“ ist für „{sid}“ "
                                        f"{STATE_WORDS.get(state, state)}"
                                        + (f" ({note})" if note else "") + ".")
    return row, server, doc, args
