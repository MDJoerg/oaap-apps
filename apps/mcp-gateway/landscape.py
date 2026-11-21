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

Stufe 1 (D6): nur Werkzeuge. Stufe 2 (mcp-gateway 0.38.0): auch Prompts -- jeder
einmal, mit dem Pflichtargument `system` wie ein Werkzeug -- und Ressourcen: je
System, auf dem sie gehen, einmal, die URI mit dem System davor
(`oaap-system://<system>/<uri>`). Eine Ansicht (`ui://`, RFC-0075) bleibt die des
Referenzsystems: sie ist Code, den das Werkzeug zeigt, nicht Inhalt eines Systems.
Dazu **Umwandeln**: aus einer Sicht, die mehrere Systeme mit Präfix-Namen trägt,
wird eine NEUE Multi-System-Sicht; die alte bleibt, wie sie war (D9).
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
KIND_LABELS = {"tool": "Werkzeug", "prompt": "Prompt", "resource": "Ressource",
               "template": "Ressourcenvorlage"}
# Stufe 2: die URI einer Ressource trägt das System (D6)
URI_PREFIX = "oaap-system://"
PLAIN_URI = ("ui://",)

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
    dieses System -- für jede Art gleich (Stufe 2: auch Prompts und Ressourcen)."""
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


def system_uri(sid, uri):
    return f"{URI_PREFIX}{sid}/{uri}"


def split_uri(uri):
    """`oaap-system://<system>/<uri>` -> (system, uri); sonst ("", uri)."""
    if not isinstance(uri, str) or not uri.startswith(URI_PREFIX):
        return "", uri
    sid, slash, inner = uri[len(URI_PREFIX):].partition("/")
    if not slash or not books.NAME.match(sid) or not inner:
        return "", uri
    return sid, inner


def plain_uri(uri):
    return isinstance(uri, str) and uri.startswith(PLAIN_URI)


def shaped_prompt(base, choices):
    """Der Prompt, wie der Client ihn sieht, mit dem Pflichtargument `system` vorn."""
    out = json.loads(json.dumps(base))
    args = [a for a in (out.get("arguments") or []) if a.get("name") != SYSTEM_ARG]
    out["arguments"] = [{"name": SYSTEM_ARG, "required": True,
                         "description": ("Zielsystem, eins von: " + "; ".join(
                             system_line(r) for r, _s, _d in choices))}] + args
    return out


def shaped_resource(base, kind, row):
    """Die Ressource (oder Vorlage) für EIN System: URI mit dem System davor, der
    Name mit seinem Titel."""
    out = json.loads(json.dumps(base))
    field = "uri" if kind == "resource" else "uriTemplate"
    out[field] = system_uri(row["id"], out[field])
    out["name"] = f"{out.get('name') or base[field]} ({row['title']})"
    if "title" in out:
        out["title"] = f"{out['title']} ({row['title']})"
    return out


def landscape_doc(db, view, rank=2):
    """Was der Chat über die Systeme dieser Sicht lesen darf (S1: nie Adresse,
    Destination, Zugang)."""
    tools = books.items(db, view, "tool")
    prompts = books.items(db, view, "prompt")
    res = [i for i in books.items(db, view) if i["kind"] in ("resource", "template")
           and not plain_uri(i["name"])]
    out = []
    for sid in view_system_ids(db, view):
        row = system_row(db, sid)
        if not row:
            continue
        light = system_light(db, row)
        avail = [i["name"] for i in tools if availability(db, view, i, sid)[0] in SERVED]
        p_avail = [i["name"] for i in prompts if availability(db, view, i, sid)[0] in SERVED]
        r_avail = [system_uri(sid, i["name"]) for i in res
                   if availability(db, view, i, sid)[0] in SERVED]
        out.append({"id": row["id"], "title": row["title"], "role": row["role"],
                    "role_label": ROLE_WORDS.get(row["role"], row["role"]),
                    "kind": row["kind"], "active": bool(row["active"]), "light": light,
                    "usable_now": bool(row["active"]) and books.LIGHT_RANK[light] <= rank,
                    "reference": sid == reference_of(db, view),
                    "facts": facts_of(row), "description": row["description"],
                    "tools": avail, "prompts": p_avail, "resources": r_avail})
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


# ------------------------------------------------------------ Umwandeln (Stufe 2)

def convert_plan(db, view, reference):
    """Was aus einer Sicht mit Präfix-Namen würde, wenn sie eine Multi-System-Sicht mit
    diesem Referenzsystem wäre. -> {"systems", "rows", "problems"}; nichts geändert.

    Je Eintrag eines anderen Systems braucht es an der Referenz ein Gegenstück (dieselbe
    Art, derselbe Name beim Server) mit derselben Wirkungsklasse, denselben festen
    Argumenten und Grenzen. Eine Freigabe, die die alte Sicht für den Server des
    anderen Systems hielt, geht mit (S3: sie war für genau diesen Server); ein
    Eintrag, den die alte Sicht für ein System NICHT hatte, kommt hinzu, wenn sein
    Server denselben Text meldet -- die Zeile sagt es."""
    problems, rows = [], []
    if not books.view_row(db, view):
        return {"systems": [], "rows": [], "problems": [f"Eine Sicht „{view}“ gibt es nicht."]}
    if is_multi(db, view):
        problems.append(f"„{view}“ ist schon eine Multi-System-Sicht.")
    if not system_row(db, reference):
        problems.append(f"Ein Zielsystem „{reference}“ gibt es nicht.")
    if problems:
        return {"systems": [], "rows": [], "problems": problems}
    mine = set(servers_of(db, reference))
    all_items = books.items(db, view)
    ref_items = [i for i in all_items if i["server"] in mine]
    if not ref_items:
        problems.append(f"Die Sicht hat keinen Eintrag auf einem Server von „{reference}“.")
    by_source = {(i["kind"], i["source"]): i for i in ref_items}
    systems_seen, taken = [], {}
    for i in all_items:
        if i["server"] in mine:
            continue
        sid = system_of_server(db, i["server"])
        if not sid:
            problems.append(f"„{i['name']}“: der Server „{i['server']}“ gehört zu keinem "
                            "Zielsystem -- erst in der Systemlandschaft zuordnen.")
            continue
        if sid not in systems_seen:
            systems_seen.append(sid)
        ref = by_source.get((i["kind"], i["source"]))
        if not ref:
            problems.append(f"„{i['name']}“ ({sid}) hat an der Referenz kein Gegenstück "
                            f"({KIND_LABELS.get(i['kind'], i['kind'])} „{i['source']}“).")
            continue
        diff = [w for w, col in (("Wirkungsklasse", "effect"), ("feste Argumente", "fixed"),
                                 ("Grenzen", "bounds")) if (i[col] or "") != (ref[col] or "")]
        if diff:
            problems.append(f"„{i['name']}“ ({sid}) weicht von „{ref['name']}“ ab: "
                            + ", ".join(diff) + " -- eine Multi-System-Sicht hat eine Auswahl.")
            continue
        if taken.get((sid, ref["kind"], ref["name"])):
            problems.append(f"„{i['name']}“ und „{taken[(sid, ref['kind'], ref['name'])]}“ "
                            f"({sid}) sind beide „{ref['name']}“.")
            continue
        taken[(sid, ref["kind"], ref["name"])] = i["name"]
        rows.append({"system": sid, "kind": ref["kind"], "name": ref["name"], "old": i["name"],
                     "server": i["server"],
                     "carry": i["approved_fp"] != ref["approved_fp"],
                     "fp": i["approved_fp"], "doc": i["approved_doc"],
                     "by": i["approved_by"]})
    for sid in systems_seen:
        for ref in ref_items:
            if taken.get((sid, ref["kind"], ref["name"])):
                continue
            rows.append({"system": sid, "kind": ref["kind"], "name": ref["name"], "old": "",
                         "server": "", "carry": False, "fp": "", "doc": "", "by": ""})
    if ref_items and not systems_seen:
        problems.append("Die Sicht trägt nur Einträge von „" + reference + "“ -- es gibt nichts "
                        "umzuwandeln; „Zur Multi-System-Sicht machen“ genügt.")
    return {"systems": systems_seen, "rows": rows, "problems": problems,
            "reference_items": [i["name"] for i in ref_items]}


def convert(db, actor, view, reference, new):
    """Legt die neue Multi-System-Sicht `new` an: die Einträge der Referenz (Kopie
    der alten Sicht, Freigaben wie gehalten), die anderen Systeme zugeordnet,
    gehaltene Freigaben je System übernommen. Die alte Sicht bleibt (D9). Wirft
    ValueError mit den Sätzen des Plans."""
    plan = convert_plan(db, view, reference)
    if plan["problems"]:
        raise ValueError(" ".join(plan["problems"]))
    books.copy_view(db, actor, view, new)
    mine = set(servers_of(db, reference))
    for i in books.items(db, new):
        if i["server"] not in mine:
            books.remove_item(db, actor, new, i["kind"], i["name"])
    make_multi(db, actor, new, reference)
    for sid in plan["systems"]:
        add_system(db, actor, new, sid)
    stamp = books.now()
    carried = 0
    for r in plan["rows"]:
        if r["carry"]:
            db.execute("INSERT OR REPLACE INTO system_items (view,system,kind,name,server,"
                       "approved_fp,approved_doc,approved_by,approved_at) VALUES (?,?,?,?,?,?,?,?,?)",
                       (new, r["system"], r["kind"], r["name"], r["server"], r["fp"], r["doc"],
                        r["by"], stamp))
            carried += 1
    books.log(db, actor, "view.convert", new,
              f"aus {view}; referenz={reference} systeme={','.join(plan['systems'])} "
              f"freigaben übernommen={carried}")
    db.commit()
    return plan
