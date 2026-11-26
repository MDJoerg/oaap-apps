"""Ablage der Spezialisten: die Datensätze (RFC-0105 §2) und die Läufe.

Die drei Schlüssel eines Spezialisten (Agent, Sicht, KI-Gateway) liegen hier, weil die App
im Hintergrund ohne Menschen läuft. Sie werden nie zurückgezeigt; ein leeres Feld beim
Speichern behält den alten Wert. Die Datei liegt unter /data, nur für die App lesbar.
"""
import json
import os
import re
import sqlite3
import threading
import time

NAME = re.compile(r"^[A-Za-zÄÖÜäöüß0-9][A-Za-zÄÖÜäöüß0-9 ._()-]{0,59}$")
ASSISTANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}@[0-9]{1,6}$")
SECRETS = ("agent_key", "mcp_key", "ai_key")
TOOL = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
URL = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?(/[^\s]*)?$")
TASKS_MAX = 8           # RFC-0134 D4; Vorgänge kann je Mandant weniger sagen


class Refused(Exception):
    pass


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        new = not os.path.exists(path)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        if new:
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        with self.lock, self.db:
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS specialists (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL,
                assistant TEXT NOT NULL, system TEXT NOT NULL, model TEXT NOT NULL,
                vorgaenge_url TEXT NOT NULL, agent_key TEXT NOT NULL DEFAULT '',
                mcp_url TEXT NOT NULL, mcp_key TEXT NOT NULL DEFAULT '',
                ai_url TEXT NOT NULL, ai_key TEXT NOT NULL DEFAULT '',
                max_steps INTEGER NOT NULL, max_minutes INTEGER NOT NULL, max_runs INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 0, uid TEXT NOT NULL DEFAULT '',
                changed TEXT NOT NULL, changed_by TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, specialist INTEGER NOT NULL, case_number TEXT NOT NULL,
                started TEXT NOT NULL, ended TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
                steps INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS runs_spec ON runs(specialist, started);
            CREATE TABLE IF NOT EXISTS audit (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor TEXT NOT NULL,
                what TEXT NOT NULL);
            """)
            # 0.2.0 (RFC-0105 D5): darf Entwürfe an die meldende Person schreiben (Vorgabe: nein)
            if "public" not in {r[1] for r in self.db.execute("PRAGMA table_info(specialists)")}:
                self.db.execute("ALTER TABLE specialists ADD COLUMN public INTEGER NOT NULL DEFAULT 0")
            # 0.3.0 (RFC-0105 D4, Stufe 3): Werkzeuge ohne Freigabe -- eine Entscheidung des tenant_admin
            have = {r[1] for r in self.db.execute("PRAGMA table_info(specialists)")}
            for col in ("no_approval", "lifted_by"):
                if col not in have:
                    self.db.execute(f"ALTER TABLE specialists ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            # 0.7.0 (RFC-0134 §4): „leitet“, sein Team (Namen anderer Spezialisten), Aufträge je Vorgang
            if "leads" not in have:
                self.db.execute("ALTER TABLE specialists ADD COLUMN leads INTEGER NOT NULL DEFAULT 0")
                self.db.execute("ALTER TABLE specialists ADD COLUMN team TEXT NOT NULL DEFAULT ''")
                self.db.execute(f"ALTER TABLE specialists ADD COLUMN max_tasks INTEGER NOT NULL DEFAULT {TASKS_MAX}")

    # ---------------------------------------------------------------- Spezialisten
    def all(self, with_secrets=False):
        with self.lock:
            rows = [dict(r) for r in self.db.execute("SELECT * FROM specialists ORDER BY name")]
        return [r if with_secrets else self.public(r) for r in rows]

    def get(self, sid, with_secrets=False):
        with self.lock:
            r = self.db.execute("SELECT * FROM specialists WHERE id=?", (sid,)).fetchone()
        if not r:
            return None
        r = dict(r)
        return r if with_secrets else self.public(r)

    @staticmethod
    def public(r):
        out = {k: v for k, v in r.items() if k not in SECRETS}
        for k in SECRETS:
            out[k + "_set"] = bool(r.get(k))
        return out

    def save(self, doc, actor, limits, may_lift=False):
        """Anlegen (ohne id) oder ändern. `limits` sind die Obergrenzen des Mandanten
        (D8: je Spezialist nur niedriger). `may_lift`: nur der tenant_admin hebt die Freigabe
        für Werkzeuge auf (D4); wer es nicht darf, lässt die Liste, wie sie ist."""
        name = (doc.get("name") or "").strip()
        if not NAME.match(name):
            raise Refused("Name: Buchstaben, Ziffern, Leerzeichen, . _ ( ) -, höchstens 60 Zeichen.")
        assistant = (doc.get("assistant") or "").strip()
        if not ASSISTANT.match(assistant):
            raise Refused("Assistent als Kennung@Revision, z. B. analyse@3 (RFC-0097).")
        system = (doc.get("system") or "").strip()
        if not system or len(system) > 8000:
            raise Refused("Die Anweisung (Systemprompt) fehlt oder ist länger als 8000 Zeichen.")
        model = (doc.get("model") or "").strip()
        if not model or len(model) > 120:
            raise Refused("Der Modellname des KI-Gateways fehlt.")
        urls = {}
        for k, word in (("vorgaenge_url", "Vorgänge"), ("mcp_url", "Sicht im MCP-Gateway"),
                        ("ai_url", "KI-Gateway")):
            v = (doc.get(k) or "").strip()
            if not URL.match(v):
                raise Refused(f"Adresse {word}: http(s)://host[:port][/pfad].")
            urls[k] = v
        lim = {}
        for k, word in (("max_steps", "Schritte je Lauf"), ("max_minutes", "Minuten je Lauf"),
                        ("max_runs", "Läufe je Vorgang")):
            try:
                v = int(doc.get(k) or limits[k])
            except ValueError:
                raise Refused(f"{word}: eine Zahl.")
            if not 1 <= v <= limits[k]:
                raise Refused(f"{word}: 1 bis {limits[k]} (Grenze des Mandanten).")
            lim[k] = v
        active = 1 if doc.get("active") in (True, "1", "on", 1) else 0
        public = 1 if doc.get("public") in (True, "1", "on", 1) else 0
        sec = {k: (doc.get(k) or "").strip() for k in SECRETS}
        tools = sorted({t for t in (doc.get("no_approval") or "").replace(",", " ").split() if t})
        if any(not TOOL.match(t) for t in tools):
            raise Refused("Werkzeuge ohne Freigabe: Namen, durch Leerzeichen oder Komma getrennt.")
        no_approval = " ".join(tools)
        # 0.7.0 (RFC-0134 §4): ein Leiter und sein Team
        leads = 1 if doc.get("leads") in (True, "1", "on", 1) else 0
        team = [n.strip() for n in (doc.get("team") or "").split(",") if n.strip()]
        team = list(dict.fromkeys(team))
        if name in team:
            raise Refused("Ein Leiter gehört nicht zu seinem eigenen Team.")
        try:
            max_tasks = int(doc.get("max_tasks") or TASKS_MAX)
        except ValueError:
            raise Refused("Aufträge je Vorgang: eine Zahl.")
        if not 1 <= max_tasks <= TASKS_MAX:
            raise Refused(f"Aufträge je Vorgang: 1 bis {TASKS_MAX}.")
        if leads and not team:
            raise Refused("Ein Leiter braucht ein Team: die Namen anderer Spezialisten, durch Komma getrennt.")
        sid = doc.get("id")
        with self.lock, self.db:
            if sid:
                old = self.db.execute("SELECT * FROM specialists WHERE id=?", (sid,)).fetchone()
                if not old:
                    raise Refused("Diesen Spezialisten gibt es nicht.")
                for k in SECRETS:
                    sec[k] = sec[k] or old[k]
                lifted_by = old["lifted_by"]
                if no_approval != old["no_approval"]:
                    if not may_lift:
                        raise Refused("Die Freigabe für Werkzeuge hebt nur der tenant_admin auf.")
                    lifted_by = actor if no_approval else ""
                    self.db.execute("INSERT INTO audit (at, actor, what) VALUES (?,?,?)",
                                    (now(), actor, f"Freigabe aufgehoben bei {name}: {no_approval or 'keine Werkzeuge mehr'}"))
                if self.db.execute("SELECT 1 FROM specialists WHERE name=? AND id!=?", (name, sid)).fetchone():
                    raise Refused(f"Den Namen „{name}“ trägt schon ein anderer Spezialist.")
                self.db.execute(
                    "UPDATE specialists SET name=?, assistant=?, system=?, model=?, vorgaenge_url=?, agent_key=?, "
                    "mcp_url=?, mcp_key=?, ai_url=?, ai_key=?, max_steps=?, max_minutes=?, max_runs=?, active=?, "
                    "public=?, no_approval=?, lifted_by=?, changed=?, changed_by=? WHERE id=?",
                    (name, assistant, system, model, urls["vorgaenge_url"], sec["agent_key"], urls["mcp_url"],
                     sec["mcp_key"], urls["ai_url"], sec["ai_key"], lim["max_steps"], lim["max_minutes"],
                     lim["max_runs"], active, public, no_approval, lifted_by, now(), actor, sid))
                what = f"geändert: {name} ({assistant}){', aktiv' if active else ', aus'}"
            else:
                if self.db.execute("SELECT 1 FROM specialists WHERE name=?", (name,)).fetchone():
                    raise Refused(f"Den Spezialisten „{name}“ gibt es schon.")
                if no_approval and not may_lift:
                    raise Refused("Die Freigabe für Werkzeuge hebt nur der tenant_admin auf.")
                cur = self.db.execute(
                    "INSERT INTO specialists (name, assistant, system, model, vorgaenge_url, agent_key, mcp_url, "
                    "mcp_key, ai_url, ai_key, max_steps, max_minutes, max_runs, active, public, changed, changed_by) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, assistant, system, model, urls["vorgaenge_url"], sec["agent_key"], urls["mcp_url"],
                     sec["mcp_key"], urls["ai_url"], sec["ai_key"], lim["max_steps"], lim["max_minutes"],
                     lim["max_runs"], active, public, now(), actor))
                sid = cur.lastrowid
                if no_approval:
                    self.db.execute("UPDATE specialists SET no_approval=?, lifted_by=? WHERE id=?",
                                    (no_approval, actor, sid))
                    self.db.execute("INSERT INTO audit (at, actor, what) VALUES (?,?,?)",
                                    (now(), actor, f"Freigabe aufgehoben bei {name}: {no_approval}"))
                what = f"angelegt: {name} ({assistant})"
            known = {r["name"] for r in self.db.execute("SELECT name FROM specialists")}
            unknown = [n for n in team if n not in known]
            if unknown:
                raise Refused("Im Team stehen Namen, die es hier nicht gibt: " + ", ".join(unknown))
            self.db.execute("UPDATE specialists SET leads=?, team=?, max_tasks=? WHERE id=?",
                            (leads, ", ".join(team), max_tasks, sid))
            if active and not all(sec.values()):
                raise Refused("Aktiv geht erst, wenn alle drei Schlüssel eingetragen sind.")
            self.db.execute("INSERT INTO audit (at, actor, what) VALUES (?,?,?)", (now(), actor, what))
        return sid

    def set_active(self, sid, on, actor):
        with self.lock, self.db:
            r = self.db.execute("SELECT * FROM specialists WHERE id=?", (sid,)).fetchone()
            if not r:
                raise Refused("Diesen Spezialisten gibt es nicht.")
            if on and not all(r[k] for k in SECRETS):
                raise Refused("Aktiv geht erst, wenn alle drei Schlüssel eingetragen sind.")
            self.db.execute("UPDATE specialists SET active=? WHERE id=?", (1 if on else 0, sid))
            self.db.execute("INSERT INTO audit (at, actor, what) VALUES (?,?,?)",
                            (now(), actor, f"{'eingeschaltet' if on else 'ausgeschaltet'}: {r['name']}"))

    def delete(self, sid, actor):
        with self.lock, self.db:
            r = self.db.execute("SELECT name FROM specialists WHERE id=?", (sid,)).fetchone()
            if not r:
                raise Refused("Diesen Spezialisten gibt es nicht.")
            self.db.execute("DELETE FROM specialists WHERE id=?", (sid,))
            self.db.execute("INSERT INTO audit (at, actor, what) VALUES (?,?,?)",
                            (now(), actor, f"gelöscht: {r['name']} (Schlüssel verworfen)"))

    def set_uid(self, sid, uid):
        with self.lock, self.db:
            self.db.execute("UPDATE specialists SET uid=? WHERE id=?", (uid, sid))

    # ---------------------------------------------------------------- Läufe
    def run_start(self, rid, sid, number):
        with self.lock, self.db:
            self.db.execute("INSERT INTO runs (id, specialist, case_number, started) VALUES (?,?,?,?)",
                            (rid, sid, number, now()))

    def run_end(self, rid, reason, steps):
        with self.lock, self.db:
            self.db.execute("UPDATE runs SET ended=?, reason=?, steps=? WHERE id=?", (now(), reason, steps, rid))

    def runs(self, sid=None, limit=40):
        with self.lock:
            q = "SELECT r.*, s.name FROM runs r LEFT JOIN specialists s ON s.id = r.specialist"
            args = []
            if sid:
                q += " WHERE r.specialist=?"
                args.append(sid)
            return [dict(r) for r in self.db.execute(q + " ORDER BY r.started DESC LIMIT ?", args + [limit])]

    def audit(self, limit=20):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM audit ORDER BY seq DESC LIMIT ?", (limit,))]


def import_assistant(raw):
    """Aus dem Export eines Assistenten (MCP-Gateway, „Exportieren“) die Felder, die ein
    Spezialist festhält: Kennung@Revision, Anweisung, Modell. Ein Export kann mehrere
    enthalten -- dann der erste."""
    try:
        doc = json.loads(raw)
    except ValueError:
        raise Refused("Der Export ist kein JSON.")
    items = doc.get("assistants") if isinstance(doc, dict) and "assistants" in doc else doc
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list) or not items or not isinstance(items[0], dict):
        raise Refused("Im Export steht kein Assistent.")
    a = items[0]
    rev = a.get("rev") or a.get("revision") or 1
    return {"assistant": f"{a.get('id', '')}@{rev}", "system": a.get("system", ""), "model": a.get("model", ""),
            "view": a.get("view", "")}
