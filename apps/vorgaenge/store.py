"""Die Ablage der Vorgaenge (RFC-0061 §1, Q4): SQLite im eigenen Volume.

Nur Standardbibliothek. Alles, was eine Regel ist, steht HIER und nicht
im HTTP-Teil -- damit der Test die Regeln pruefen kann, ohne einen
Server zu starten:

- Eintraege sind nur anhaengbar. Ein Kommentar wird nicht geaendert,
  ein Vorgang nicht geloescht.
- Ein Statuswechsel folgt der Tabelle NEXT (RFC-0061 §4, Stufe 1 ohne
  `voting` und `handed_over`). `rejected` braucht eine Begruendung.
- Ein Verschieben macht den alten Bereich zum Beobachter.
- Die beglaubigten Angaben (`tech.attested`) kommen aus den Kopfzeilen
  des Gateways; was der Browser schickt, steht getrennt daneben
  (`tech.browser`) und kann die beglaubigten nie ueberschreiben.
"""
import json
import re
import sqlite3
import threading
import time
import uuid

KINDS = ("bug", "question", "idea", "change", "internal")
# was der Knopf anbietet; `internal` legt man in der App an (§6)
BUTTON_KINDS = ("bug", "question", "idea", "change")

KIND_LABEL = {"bug": "Fehler", "question": "Frage", "idea": "Idee",
              "change": "Änderungswunsch", "internal": "Intern"}
STATUS_LABEL = {"new": "neu", "triage": "Klärung", "in_progress": "in Arbeit",
                "answered": "beantwortet", "closed": "geschlossen",
                "rejected": "abgelehnt", "reopened": "wieder offen"}

# RFC-0061 §4, Stufe 1. `voting` (§7) und `handed_over` (§5) kommen mit
# ihren Stufen; bis dahin bietet sie niemand an.
NEXT = {
    "new":         ("triage", "in_progress", "answered", "rejected"),
    "triage":      ("in_progress", "answered", "rejected"),
    "in_progress": ("triage", "answered", "rejected"),
    "answered":    ("in_progress", "closed"),
    "closed":      ("reopened",),
    "rejected":    ("reopened",),
    "reopened":    ("triage", "in_progress", "answered", "rejected"),
}
OPEN = ("new", "triage", "in_progress", "answered", "reopened")

DEFAULT_QUEUE = "eingang"
QUEUE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

# Was der Browser mitschicken darf, und wie lang (RFC-0061 §3.1). Alles
# andere wird verworfen, nicht gespeichert.
BROWSER_FIELDS = {"path": 300, "user_agent": 300, "screen": 60,
                  "language": 40, "last_error": 400, "last_failed_fetch": 400}

TITLE_MAX = 200
BODY_MAX = 10000


class Refused(Exception):
    """Eine Regel sagt nein; der Text geht so an den Menschen."""


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clip(v, n):
    v = "" if v is None else str(v)
    return v[:n]


class Store:
    def __init__(self, path):
        self.lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock, self.db:
            self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS queues (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cases (
                id TEXT PRIMARY KEY, number TEXT UNIQUE NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                status TEXT NOT NULL, queue TEXT NOT NULL,
                reporter TEXT NOT NULL, reporter_name TEXT NOT NULL,
                object_kind TEXT NOT NULL DEFAULT '', object_id TEXT NOT NULL DEFAULT '',
                source_instance TEXT NOT NULL DEFAULT '',
                source_app TEXT NOT NULL DEFAULT '',
                tech TEXT NOT NULL DEFAULT '{}',
                created TEXT NOT NULL, updated TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS entries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT NOT NULL,
                at TEXT NOT NULL, actor TEXT NOT NULL, actor_name TEXT NOT NULL,
                type TEXT NOT NULL, text TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS observers (
                case_id TEXT NOT NULL, queue TEXT NOT NULL,
                PRIMARY KEY (case_id, queue));
            CREATE TABLE IF NOT EXISTS counters (year TEXT PRIMARY KEY, n INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS cases_queue ON cases(queue, status);
            CREATE INDEX IF NOT EXISTS cases_reporter ON cases(reporter);
            CREATE INDEX IF NOT EXISTS entries_case ON entries(case_id, seq);
            """)
            # RFC-0061 §2.2: jeder Mandant hat einen Eingang, ab Installation
            self.db.execute("INSERT OR IGNORE INTO queues VALUES (?,?,?)",
                            (DEFAULT_QUEUE, "Eingang", _now()))

    # ------------------------------------------------------------ Bereiche
    def queues(self):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM queues ORDER BY id = ? DESC, title", (DEFAULT_QUEUE,))]

    def queue(self, qid):
        with self.lock:
            r = self.db.execute("SELECT * FROM queues WHERE id=?", (qid,)).fetchone()
        return dict(r) if r else None

    def add_queue(self, qid, title):
        qid = (qid or "").strip().lower()
        title = _clip((title or "").strip(), 80)
        if not QUEUE_ID.fullmatch(qid):
            raise Refused("Die Kennung eines Bereichs: Kleinbuchstaben, Ziffern "
                          "und Bindestrich, höchstens 40 Zeichen. Sie ist der "
                          "Kontext, den man in „Rollen & Rechte“ vergibt.")
        if not title:
            raise Refused("Ein Bereich braucht einen Titel.")
        with self.lock, self.db:
            if self.db.execute("SELECT 1 FROM queues WHERE id=?", (qid,)).fetchone():
                raise Refused(f"Den Bereich „{qid}“ gibt es schon.")
            self.db.execute("INSERT INTO queues VALUES (?,?,?)", (qid, title, _now()))

    def rename_queue(self, qid, title):
        title = _clip((title or "").strip(), 80)
        if not title:
            raise Refused("Ein Bereich braucht einen Titel.")
        with self.lock, self.db:
            if not self.db.execute("UPDATE queues SET title=? WHERE id=?",
                                   (title, qid)).rowcount:
                raise Refused("Diesen Bereich gibt es nicht.")

    # ------------------------------------------------------------ Vorgaenge
    def _number(self):
        year = time.strftime("%Y", time.gmtime())
        row = self.db.execute("SELECT n FROM counters WHERE year=?", (year,)).fetchone()
        n = (row["n"] if row else 0) + 1
        self.db.execute("INSERT OR REPLACE INTO counters VALUES (?,?)", (year, n))
        return f"V-{year}-{n:04d}"

    def create(self, *, kind, title, body, reporter, reporter_name,
               queue=DEFAULT_QUEUE, obj=None, attested=None, browser=None,
               allowed_kinds=KINDS):
        """Legt einen Vorgang an. `attested` kommt vom Gateway (oder, fuer
        einen in der App angelegten Vorgang, von der App selbst), `browser`
        vom Client -- getrennt gespeichert, Client-Felder gefiltert."""
        if kind not in allowed_kinds:
            raise Refused("Unbekannte Art.")
        title = (title or "").strip()
        if not title:
            raise Refused("Bitte einen Betreff angeben.")
        if len(title) > TITLE_MAX or len(body or "") > BODY_MAX:
            raise Refused(f"Betreff höchstens {TITLE_MAX}, Beschreibung höchstens "
                          f"{BODY_MAX} Zeichen.")
        if not reporter:
            raise Refused("Ohne angemeldete Person kein Vorgang.")
        ok_kind, ok_id = "", ""
        if isinstance(obj, dict) and obj.get("id"):
            ok_kind = _clip(obj.get("kind"), 40).strip()
            ok_id = _clip(obj.get("id"), 200).strip()
        tech = {"attested": dict(attested or {})}
        if isinstance(browser, dict):
            tech["browser"] = {k: _clip(browser.get(k), n)
                               for k, n in BROWSER_FIELDS.items() if browser.get(k)}
        cid = str(uuid.uuid4())
        at = _now()
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (queue,)).fetchone():
                queue = DEFAULT_QUEUE
            number = self._number()
            self.db.execute(
                "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, number, kind, title, body or "", "new", queue,
                 reporter, _clip(reporter_name, 120), ok_kind, ok_id,
                 _clip((attested or {}).get("instance"), 80),
                 _clip((attested or {}).get("app"), 80),
                 json.dumps(tech, ensure_ascii=False), at, at))
            self._entry(cid, reporter, reporter_name, "created", "",
                        {"queue": queue})
        return {"id": cid, "number": number}

    def _entry(self, cid, actor, actor_name, typ, text, data=None):
        self.db.execute(
            "INSERT INTO entries (case_id, at, actor, actor_name, type, text, data) "
            "VALUES (?,?,?,?,?,?,?)",
            (cid, _now(), actor, _clip(actor_name, 120), typ, text or "",
             json.dumps(data or {}, ensure_ascii=False)))
        self.db.execute("UPDATE cases SET updated=? WHERE id=?", (_now(), cid))

    def get(self, number):
        with self.lock:
            r = self.db.execute("SELECT * FROM cases WHERE number=?", (number,)).fetchone()
            if not r:
                return None
            c = dict(r)
            c["tech"] = json.loads(c["tech"] or "{}")
            c["entries"] = [dict(e, data=json.loads(e["data"] or "{}")) for e in
                            self.db.execute("SELECT * FROM entries WHERE case_id=? "
                                            "ORDER BY seq", (c["id"],))]
            c["observers"] = [o["queue"] for o in self.db.execute(
                "SELECT queue FROM observers WHERE case_id=? ORDER BY queue", (c["id"],))]
        return c

    def listing(self, *, queues=(), reporter=None, open_only=True):
        """Vorgaenge in diesen Bereichen ODER von dieser Person -- nur das,
        was der Aufrufer sehen darf, wird ueberhaupt gefragt."""
        conds, args = [], []
        if queues:
            conds.append(f"queue IN ({','.join('?' * len(queues))})")
            args += list(queues)
            conds.append("id IN (SELECT case_id FROM observers WHERE queue IN "
                         f"({','.join('?' * len(queues))}))")
            args += list(queues)
        if reporter:
            conds.append("reporter=?")
            args.append(reporter)
        if not conds:
            return []
        sql = "SELECT * FROM cases WHERE (" + " OR ".join(conds) + ")"
        if open_only:
            sql += f" AND status IN ({','.join('?' * len(OPEN))})"
            args += list(OPEN)
        sql += " ORDER BY updated DESC LIMIT 500"
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    def comment(self, c, actor, actor_name, text):
        text = (text or "").strip()
        if not text:
            raise Refused("Ein leerer Kommentar wird nicht gespeichert.")
        if len(text) > BODY_MAX:
            raise Refused(f"Höchstens {BODY_MAX} Zeichen.")
        with self.lock, self.db:
            self._entry(c["id"], actor, actor_name, "comment", text)

    def set_status(self, c, actor, actor_name, new, text=""):
        if new not in NEXT.get(c["status"], ()):
            raise Refused(f"Von „{STATUS_LABEL.get(c['status'], c['status'])}“ "
                          f"geht es nicht nach „{STATUS_LABEL.get(new, new)}“.")
        text = (text or "").strip()
        if new == "rejected" and not text:
            raise Refused("Eine Ablehnung braucht eine Begründung.")
        with self.lock, self.db:
            if not self.db.execute("UPDATE cases SET status=? WHERE id=? AND status=?",
                                   (new, c["id"], c["status"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            self._entry(c["id"], actor, actor_name, "status", text,
                        {"from": c["status"], "to": new})

    def move(self, c, actor, actor_name, target, text=""):
        if target == c["queue"]:
            raise Refused("Der Vorgang liegt schon dort.")
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (target,)).fetchone():
                raise Refused("Diesen Bereich gibt es nicht.")
            if not self.db.execute("UPDATE cases SET queue=? WHERE id=? AND queue=?",
                                   (target, c["id"], c["queue"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen verschoben. Bitte neu laden.")
            # RFC-0061 §2.3: der alte Bereich bleibt Beobachter
            self.db.execute("INSERT OR IGNORE INTO observers VALUES (?,?)",
                            (c["id"], c["queue"]))
            self.db.execute("DELETE FROM observers WHERE case_id=? AND queue=?",
                            (c["id"], target))
            self._entry(c["id"], actor, actor_name, "move", (text or "").strip(),
                        {"from": c["queue"], "to": target})

    def recent_by(self, reporter, seconds):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM cases WHERE reporter=? "
                                   "AND created>=?", (reporter, since)).fetchone()[0]
