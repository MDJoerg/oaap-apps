"""Die Ablage der Vorgaenge (RFC-0061 §1, Q4): SQLite im eigenen Volume.

Nur Standardbibliothek. Alles, was eine Regel ist, steht HIER und nicht
im HTTP-Teil -- damit der Test die Regeln pruefen kann, ohne einen
Server zu starten:

- Eintraege sind nur anhaengbar. Ein Kommentar wird nicht geaendert,
  ein Vorgang nicht geloescht.
- Ein Statuswechsel folgt der Tabelle NEXT (RFC-0061 §4, ohne `voting`).
  `rejected` braucht eine Begruendung. `handed_over` setzt nur eine
  freigegebene Uebergabe, nie das Statusformular.
- Ein Verschieben macht den alten Bereich zum Beobachter.
- Die beglaubigten Angaben (`tech.attested`) kommen aus den Kopfzeilen
  des Gateways; was der Browser schickt, steht getrennt daneben
  (`tech.browser`) und kann die beglaubigten nie ueberschreiben.
"""
import hashlib
import json
import re
import hashlib
import sqlite3
import threading
import time
import uuid
from urllib.parse import unquote

KINDS = ("bug", "question", "idea", "change", "internal")
# was der Knopf anbietet; `internal` legt man in der App an (§6)
BUTTON_KINDS = ("bug", "question", "idea", "change")

KIND_LABEL = {"bug": "Fehler", "question": "Frage", "idea": "Idee",
              "change": "Änderungswunsch", "internal": "Intern"}
STATUS_LABEL = {"new": "neu", "triage": "Klärung", "in_progress": "in Arbeit",
                "answered": "beantwortet", "closed": "geschlossen",
                "rejected": "abgelehnt", "reopened": "wieder offen",
                "handed_over": "übergeben",
                # 0.10.0 (RFC-0103 D4): feste Unterstatus von „in Arbeit“
                "clarify_internal": "in Klärung intern", "clarify_external": "in Klärung extern",
                "waiting_test": "wartet auf Test", "waiting_reporter": "Rückfrage an die Meldende",
                # 0.14.0 (RFC-0105 D6): ein Spezialist wartet auf die Freigabe eines Menschen
                "waiting_approval": "wartet auf Freigabe",
                # 0.15.0 (RFC-0105 D6/D7): eine Rückfrage an einen Spezialisten oder Menschen ist offen
                "waiting_consult": "wartet auf Rückmeldung"}
# 0.10.0: dieselben Übergänge wie `in_progress`. Fest, nicht je Mandant: Auswertungen,
# die MCP-Tür und der Chat brauchen Wörter, die sie kennen.
SUBSTATUS = ("clarify_internal", "clarify_external", "waiting_test", "waiting_reporter", "waiting_approval",
             "waiting_consult")

# RFC-0061 §4. `voting` (§7) kommt mit seiner Stufe. `handed_over` (§5)
# steht hier, damit die Regel EINE Tabelle bleibt; erreicht wird es nur
# ueber release() -- das Statusformular laesst es aus (MANUAL).
NEXT = {
    "new":         ("triage", "in_progress", "answered", "rejected", "handed_over"),
    "triage":      ("in_progress", "answered", "rejected", "handed_over"),
    "in_progress": ("triage", "answered", "rejected", "handed_over"),
    "answered":    ("in_progress", "closed"),
    "closed":      ("reopened",),
    "rejected":    ("reopened",),
    "reopened":    ("triage", "in_progress", "answered", "rejected", "handed_over"),
    "handed_over": ("in_progress", "answered", "rejected"),
}
# 0.14.0: „wartet auf Freigabe“ setzt nur ein Plan des Spezialisten, nie das Statusformular
# 0.15.0: „wartet auf Rückmeldung“ setzt nur eine Rückfrage
MANUAL_EXCLUDED = ("handed_over", "waiting_approval", "waiting_consult")

# 0.15.0 (RFC-0105 §5): Rückfragen an Spezialisten je Vorgang; Antworten von Spezialisten auf
# Spezialisten in Folge, danach ist ein Mensch dran
CONSULT_SPECIALISTS_MAX = 3
SPEC_TO_SPEC_IN_ROW = 2


def _with_substatus(nxt):
    """0.10.0 (RFC-0103 D4): überall, wo `in_progress` erreichbar ist, auch die
    Unterstatus; aus einem Unterstatus dieselben Wege wie aus `in_progress`, dazu
    zurück nach `in_progress` und in die anderen Unterstatus."""
    out = {}
    for k, v in nxt.items():
        v = list(v)
        if "in_progress" in v:
            i = v.index("in_progress") + 1
            v[i:i] = list(SUBSTATUS)
        elif k == "in_progress":
            v = list(SUBSTATUS) + v
        out[k] = tuple(v)
    for sub in SUBSTATUS:
        out[sub] = tuple(["in_progress"] + [x for x in SUBSTATUS if x != sub]
                         + list(nxt["in_progress"]))
    return out


NEXT = _with_substatus(NEXT)
# 0.9.0 (RFC-0103 D10/D11): wie ein Vorgang geschlossen wurde -- eine Markierung am
# Schließen, kein eigener Status. Leer: von den Bearbeitenden geschlossen.
MARK_LABEL = {"accepted": "Lösung passt", "no_response": "ohne Rückmeldung"}
ACCEPT_DAYS = 14
OPEN = ("new", "triage", "in_progress", "answered", "reopened", "handed_over") + SUBSTATUS

# RFC-0061 §5.2 / §10.3: was eine Uebergabe NIE enthalten darf. Geprueft
# wird das fertige Dokument, Schluessel fuer Schluessel und in jeder Tiefe
# -- nicht die Funktion, die es baut. Eine neue Zeile dort, die eine
# Person mitnaehme, faellt hier auf statt beim Empfaenger.
FORBIDDEN_KEYS = frozenset({"reporter", "reporter_name", "user_id", "user",
                            "on_behalf_of", "observers", "actor", "actor_name",
                            "roles", "email", "display_name"})
PARTNER_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
HANDOVER_BROWSER = ("path", "user_agent", "screen", "language", "last_error",
                    "last_failed_fetch")
ENDED = ("closed", "rejected")

# Q6: wer die meldende Person war, nachdem die Frist abgelaufen ist
REMOVED = "removed"
REMOVED_NAME = "entfernt"
RETENTION_MONTHS = 12

DEFAULT_QUEUE = "eingang"
QUEUE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

# Was der Browser mitschicken darf, und wie lang (RFC-0061 §3.1). Alles
# andere wird verworfen, nicht gespeichert.
BROWSER_FIELDS = {"path": 300, "user_agent": 300, "screen": 60,
                  "language": 40, "last_error": 400, "last_failed_fetch": 400}

TITLE_MAX = 200
BODY_MAX = 10000
# 0.6.0 (RFC-0101 Stufe 3): Unterart und Felder, die die Seite einer App mitgibt
# (`chat-answer` vom Chat). Keine Vorlagen nach RFC-0061 §6 -- nur eine geprüfte
# Form: Kennung, flache Schlüssel, JSON-Werte, eine Obergrenze. Getrennt von dem,
# was das Gateway beglaubigt, und so angezeigt.
SUBKIND = re.compile(r"^[a-z][a-z0-9-]{0,39}$")
FIELD_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
FIELDS_MAX = 40000
FIELDS_KEYS = 60


class Refused(Exception):
    """Eine Regel sagt nein; der Text geht so an den Menschen."""


def check_outgoing(doc, path="doc"):
    """Wirft, wenn ein Dokument, das den Mandanten verlaesst, irgendwo
    einen verbotenen Schluessel traegt (RFC-0061 §10.3)."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            if k in FORBIDDEN_KEYS:
                raise Refused(f"Eine Übergabe darf '{k}' nicht enthalten ({path}.{k}).")
            check_outgoing(v, f"{path}.{k}")
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            check_outgoing(v, f"{path}[{i}]")


def canonical(doc):
    """Die Bytes, die gehen: sortiert, ohne Leerraum. Vorschau, Hash und
    Versand lesen dieselbe Fassung."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _months_before(ts, months):
    """Derselbe Zeitpunkt `months` Kalendermonate frueher, als ISO-Zeit.
    Ein Tag, den es im Zielmonat nicht gibt (31.), wird der letzte."""
    t = time.gmtime(ts)
    y, mo = divmod(t.tm_year * 12 + (t.tm_mon - 1) - months, 12)
    mo += 1
    last = [31, 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
    return (f"{y:04d}-{mo:02d}-{min(t.tm_mday, last):02d}T"
            f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}Z")


def _clip(v, n):
    v = "" if v is None else str(v)
    return v[:n]


def _plain(v, depth=0):
    """Nur JSON-Werte, höchstens vier Ebenen tief."""
    if depth > 4:
        return False
    if v is None or isinstance(v, (str, bool, int, float)):
        return True
    if isinstance(v, list):
        return all(_plain(x, depth + 1) for x in v)
    if isinstance(v, dict):
        return all(isinstance(k, str) and _plain(x, depth + 1) for k, x in v.items())
    return False


def check_fields(subkind, fields):
    """(Unterart, Felder) geprüft -- oder Refused mit einem Satz."""
    if subkind in (None, ""):
        if fields:
            raise Refused("Felder gibt es nur mit einer Unterart (subkind).")
        return "", {}
    if not isinstance(subkind, str) or not SUBKIND.match(subkind):
        raise Refused("subkind: Kleinbuchstaben, Ziffern, Bindestrich, höchstens 40 Zeichen.")
    fields = {} if fields is None else fields
    if not isinstance(fields, dict) or len(fields) > FIELDS_KEYS:
        raise Refused(f"fields: ein Objekt mit höchstens {FIELDS_KEYS} Schlüsseln.")
    for k, v in fields.items():
        if not FIELD_KEY.match(k):
            raise Refused(f"fields: {k[:40]!r} ist kein Feldname (a-z, 0-9, _).")
        if not _plain(v):
            raise Refused(f"fields.{k}: nur Text, Zahlen, Listen und Objekte, höchstens vier Ebenen.")
    if len(json.dumps(fields, ensure_ascii=False).encode("utf-8")) > FIELDS_MAX:
        raise Refused(f"fields: zusammen höchstens {FIELDS_MAX} Bytes.")
    return subkind, fields


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
            self.db.execute("INSERT OR IGNORE INTO queues (id, title, created) VALUES (?,?,?)",
                            (DEFAULT_QUEUE, "Eingang", _now()))
            # 0.1.1 (Q6): wann ein Vorgang endete, und wann seine
            # Personendaten entfernt wurden. Eine Ablage aus 0.1.0 bekommt
            # die Spalten hier; `ended` leitet sich aus dem letzten
            # Statuseintrag ab, damit die Frist auch fuer sie laeuft.
            have = {r[1] for r in self.db.execute("PRAGMA table_info(cases)")}
            if "ended" not in have:
                self.db.execute("ALTER TABLE cases ADD COLUMN ended TEXT NOT NULL DEFAULT ''")
                self.db.execute(
                    "UPDATE cases SET ended = COALESCE((SELECT MAX(at) FROM entries "
                    "WHERE entries.case_id = cases.id AND type = 'status'), updated) "
                    "WHERE status IN ('closed', 'rejected')")
            if "scrubbed" not in have:
                self.db.execute("ALTER TABLE cases ADD COLUMN scrubbed TEXT NOT NULL DEFAULT ''")
            # 0.9.0 (RFC-0103 D10): accepted | no_response | '' -- nur bei closed gesetzt
            if "mark" not in have:
                self.db.execute("ALTER TABLE cases ADD COLUMN mark TEXT NOT NULL DEFAULT ''")
            # 0.10.0 (RFC-0103 D3): verantwortlich (ein Zuständiger des Bereichs) und
            # zugewiesen (irgendwer im Mandanten); leer = niemand
            for col in ("responsible", "assignee"):
                if col not in have:
                    self.db.execute(f"ALTER TABLE cases ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            # 0.10.0 (D5): ein Eintrag kann intern sein -- nie für die meldende Person
            if "internal" not in {r[1] for r in self.db.execute("PRAGMA table_info(entries)")}:
                self.db.execute("ALTER TABLE entries ADD COLUMN internal INTEGER NOT NULL DEFAULT 0")
            # 0.13.0 (RFC-0105 Stufe 1): ein Spezialist darf an diesem Vorgang nicht mehr
            # laufen, bis ein Mensch es wieder zulässt
            if "spec_stop" not in have:
                self.db.execute("ALTER TABLE cases ADD COLUMN spec_stop INTEGER NOT NULL DEFAULT 0")
            # 0.13.0: Erstbearbeitung eines Bereichs durch einen Spezialisten (uid, leer = keiner)
            if "first_responder" not in {r[1] for r in self.db.execute("PRAGMA table_info(queues)")}:
                self.db.execute("ALTER TABLE queues ADD COLUMN first_responder TEXT NOT NULL DEFAULT ''")
            # 0.16.0 (RFC-0105 D9): die Ampel eines Bereichs für Spezialisten ('' = keine eigene)
            if "light" not in {r[1] for r in self.db.execute("PRAGMA table_info(queues)")}:
                self.db.execute("ALTER TABLE queues ADD COLUMN light TEXT NOT NULL DEFAULT ''")
            # 0.15.0 (RFC-0105 §6): Rückfragen -- eine Frage, eine Antwort; seq ist der Eintrag der Frage
            self.db.execute("CREATE TABLE IF NOT EXISTS consults (seq INTEGER PRIMARY KEY, case_id TEXT NOT NULL, "
                            "asked_by TEXT NOT NULL, asked_by_specialist INTEGER NOT NULL, target TEXT NOT NULL, "
                            "target_name TEXT NOT NULL, specialist INTEGER NOT NULL, at TEXT NOT NULL, "
                            "answer_seq INTEGER NOT NULL DEFAULT 0, answered TEXT NOT NULL DEFAULT '')")
            self.db.execute("CREATE INDEX IF NOT EXISTS consults_target ON consults(target, answered)")
            # 0.10.0 (D3): wer je verantwortlich oder zugewiesen war, liest weiter
            self.db.execute("CREATE TABLE IF NOT EXISTS involved (case_id TEXT NOT NULL, "
                            "uid TEXT NOT NULL, role TEXT NOT NULL, since TEXT NOT NULL, "
                            "PRIMARY KEY (case_id, uid, role))")
            # 0.2.0 (RFC-0061 §5): Partner, Uebergaben in beide Richtungen,
            # und der Ausgang, aus dem bis zur Quittung wiederholt wird.
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS partners (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
                out_key TEXT NOT NULL DEFAULT '', in_principal TEXT NOT NULL DEFAULT '',
                in_queue TEXT NOT NULL DEFAULT 'eingang', active INTEGER NOT NULL DEFAULT 1,
                created TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS handovers (
                handover_id TEXT PRIMARY KEY, direction TEXT NOT NULL,
                case_id TEXT NOT NULL, partner_id TEXT NOT NULL,
                doc TEXT NOT NULL, sha256 TEXT NOT NULL, created TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS handovers_case ON handovers(case_id);
            CREATE TABLE IF NOT EXISTS outbox (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, partner_id TEXT NOT NULL,
                handover_id TEXT NOT NULL, doc TEXT NOT NULL, created TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_try REAL NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '', acked TEXT NOT NULL DEFAULT '');
            """)

        self._members_schema()
        # 0.7.0: Bis 0.6.0 standen Anzeigenamen so da, wie das Gateway sie schickt --
        # prozentkodiert, sobald sie mehr als ASCII enthalten (RFC-0040 D4). Einmal
        # dekodieren; danach dekodiert die App beim Lesen der Kopfzeile.
        with self.lock, self.db:
            if self.db.execute("PRAGMA user_version").fetchone()[0] < 1:
                for table, col in (("cases", "reporter_name"), ("entries", "actor_name")):
                    for r in self.db.execute(f"SELECT rowid, {col} FROM {table} WHERE {col} LIKE '%\\%%' ESCAPE '\\'").fetchall():
                        self.db.execute(f"UPDATE {table} SET {col}=? WHERE rowid=?",
                                        (unquote(r[1]), r[0]))
                self.db.execute("PRAGMA user_version = 1")

    # ------------------------------------------- Zuständige (0.8.0, RFC-0103 D2)
    # Wer einen Bereich bearbeitet, pflegt der tenant_admin hier -- ohne „Rollen &
    # Rechte“. Grants von dort zählen zusätzlich (Who.may). Personen kennt die App nur,
    # wenn sie sie einmal geöffnet haben: ein Verzeichnis der Plattform bekommt sie nicht.
    MEMBER_KINDS = ("handler", "clearing")

    def _members_schema(self):
        with self.lock, self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS people (
                uid TEXT PRIMARY KEY, name TEXT NOT NULL, username TEXT NOT NULL DEFAULT '',
                seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS queue_members (
                queue TEXT NOT NULL, uid TEXT NOT NULL, kind TEXT NOT NULL,
                added_by TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (queue, uid));
            CREATE TABLE IF NOT EXISTS queue_log (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, queue TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, uid TEXT NOT NULL, kind TEXT NOT NULL DEFAULT '');
            """)
            # 0.15.0 (RFC-0105 Stufe 3): wer die Tür der Spezialisten benutzt, ist ein Spezialist --
            # für die Grenzen der Rückfragen und die Auswahl, wen man fragen kann
            if "specialist" not in {r[1] for r in self.db.execute("PRAGMA table_info(people)")}:
                self.db.execute("ALTER TABLE people ADD COLUMN specialist INTEGER NOT NULL DEFAULT 0")

    def see_person(self, uid, name, username=""):
        """Wer die App öffnet, steht danach in der Auswahl für Zuständige."""
        if not uid:
            return
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO people (uid, name, username, seen) VALUES (?,?,?,?) "
                "ON CONFLICT(uid) DO UPDATE SET name=excluded.name, username=excluded.username, "
                "seen=excluded.seen", (uid, _clip(name, 120), _clip(username, 120), _now()))

    def mark_specialist(self, uid):
        with self.lock, self.db:
            self.db.execute("UPDATE people SET specialist=1 WHERE uid=? AND specialist=0", (uid,))

    def specialists(self):
        with self.lock:
            return {r["uid"] for r in self.db.execute("SELECT uid FROM people WHERE specialist=1")}

    def people(self):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM people ORDER BY name")]

    def person_name(self, uid):
        with self.lock:
            r = self.db.execute("SELECT name FROM people WHERE uid=?", (uid,)).fetchone()
        return r["name"] if r else uid

    def members(self, queue):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT m.*, COALESCE(p.name, m.uid) AS name, COALESCE(p.username, '') AS username "
                "FROM queue_members m LEFT JOIN people p ON p.uid = m.uid WHERE m.queue=? "
                "ORDER BY name", (queue,))]

    def memberships(self, uid):
        """{Bereich: handler|clearing} für diese Person."""
        if not uid:
            return {}
        with self.lock:
            return {r["queue"]: r["kind"] for r in self.db.execute(
                "SELECT queue, kind FROM queue_members WHERE uid=?", (uid,))}

    def set_member(self, queue, uid, kind, actor):
        if kind not in self.MEMBER_KINDS:
            raise Refused("Zuständig als Bearbeiter/in oder Clearing.")
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (queue,)).fetchone():
                raise Refused("Diesen Bereich gibt es nicht.")
            if not self.db.execute("SELECT 1 FROM people WHERE uid=?", (uid,)).fetchone():
                raise Refused("Diese Person kennt die App noch nicht. Sie öffnet die App "
                              "Vorgänge einmal, danach steht sie in der Auswahl.")
            self.db.execute("INSERT INTO queue_members VALUES (?,?,?,?,?) "
                            "ON CONFLICT(queue, uid) DO UPDATE SET kind=excluded.kind, "
                            "added_by=excluded.added_by, at=excluded.at",
                            (queue, uid, kind, actor, _now()))
            self.db.execute("INSERT INTO queue_log (at, queue, actor, action, uid, kind) "
                            "VALUES (?,?,?,?,?,?)", (_now(), queue, actor, "set", uid, kind))

    def remove_member(self, queue, uid, actor):
        with self.lock, self.db:
            if self.db.execute("DELETE FROM queue_members WHERE queue=? AND uid=?",
                               (queue, uid)).rowcount:
                self.db.execute("INSERT INTO queue_log (at, queue, actor, action, uid) "
                                "VALUES (?,?,?,?,?)", (_now(), queue, actor, "remove", uid))

    def queue_log(self, limit=30):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT l.*, COALESCE(p.name, l.uid) AS name, COALESCE(a.name, l.actor) AS actor_name "
                "FROM queue_log l LEFT JOIN people p ON p.uid = l.uid "
                "LEFT JOIN people a ON a.uid = l.actor ORDER BY seq DESC LIMIT ?", (limit,))]

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
            self.db.execute("INSERT INTO queues (id, title, created) VALUES (?,?,?)", (qid, title, _now()))

    def set_first_responder(self, qid, uid):
        """0.13.0 (RFC-0105 §3.1): neue und hierher verschobene Vorgänge bekommt zuerst
        dieser Spezialist als Verantwortlichen. Leer = niemand. Ob uid ein Spezialist ist,
        weiß die App nicht -- sie kennt ihn wie jede Person, sobald er einmal da war."""
        uid = (uid or "").strip()
        with self.lock, self.db:
            if uid and not self.db.execute("SELECT 1 FROM people WHERE uid=?", (uid,)).fetchone():
                raise Refused("Diesen Spezialisten kennt die App noch nicht. Er meldet sich "
                              "beim ersten Lauf der App Spezialisten an; danach steht er in der Auswahl.")
            if not self.db.execute("UPDATE queues SET first_responder=? WHERE id=?",
                                   (uid, qid)).rowcount:
                raise Refused("Diesen Bereich gibt es nicht.")

    QUEUE_LIGHTS = ("", "green", "yellow", "red")

    def log_queue_action(self, queue, actor, action, kind=""):
        with self.lock, self.db:
            self.db.execute("INSERT INTO queue_log (at, queue, actor, action, uid, kind) "
                            "VALUES (?,?,?,?,?,?)", (_now(), queue, actor, action, "", kind))

    def set_queue_light(self, qid, light, actor):
        """0.16.0 (RFC-0105 D9, §8.5): die Ampel, mit der ein Spezialist an Vorgängen dieses
        Bereichs arbeitet -- er schickt sie als X-OAAP-Light an KI- und MCP-Gateway, die sie
        als Decke nehmen (nur verschärfen). Leer = keine eigene: es gelten die Decken der
        Schlüssel, wie der Mandant sie gesetzt hat."""
        light = (light or "").strip().lower()
        if light not in self.QUEUE_LIGHTS:
            raise Refused("Ampel: grün, gelb, rot oder keine eigene.")
        with self.lock, self.db:
            if not self.db.execute("UPDATE queues SET light=? WHERE id=?", (light, qid)).rowcount:
                raise Refused("Diesen Bereich gibt es nicht.")
            self.db.execute("INSERT INTO queue_log (at, queue, actor, action, uid, kind) "
                            "VALUES (?,?,?,?,?,?)", (_now(), qid, actor, "light", "", light or "-"))

    def _first_responder(self, cid, queue):
        """Unter der Sperre: der Spezialist des Bereichs wird verantwortlich (intern vermerkt)."""
        r = self.db.execute("SELECT q.first_responder AS uid, p.name AS name FROM queues q "
                            "JOIN people p ON p.uid = q.first_responder WHERE q.id=? "
                            "AND q.first_responder != ''", (queue,)).fetchone()
        if not r:
            return ""
        self.db.execute("UPDATE cases SET responsible=?, spec_stop=0 WHERE id=?", (r["uid"], cid))
        self.db.execute("INSERT OR IGNORE INTO involved VALUES (?,?,?,?)",
                        (cid, r["uid"], "responsible", _now()))
        self._entry(cid, "system", "Automatisch", "role", r["name"],
                    {"role": "responsible", "from": "", "to": r["uid"], "first_responder": True},
                    internal=True)
        return r["uid"]

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
               allowed_kinds=KINDS, asserted=None, subkind=None, fields=None):
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
        if asserted:
            # §3.2: was die App ueber eine Person SAGT -- nicht beglaubigt,
            # und so gespeichert, dass es niemand dafuer haelt
            tech["asserted"] = {k: _clip(v, 120) for k, v in asserted.items() if v}
        if isinstance(browser, dict):
            tech["browser"] = {k: _clip(browser.get(k), n)
                               for k, n in BROWSER_FIELDS.items() if browser.get(k)}
        sub, flds = check_fields(subkind, fields)
        if sub:
            tech["subkind"], tech["fields"] = sub, flds
        cid = str(uuid.uuid4())
        at = _now()
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (queue,)).fetchone():
                queue = DEFAULT_QUEUE
            number = self._number()
            self.db.execute(
                "INSERT INTO cases (id, number, kind, title, body, status, queue, "
                "reporter, reporter_name, object_kind, object_id, source_instance, "
                "source_app, tech, created, updated) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, number, kind, title, body or "", "new", queue,
                 reporter, _clip(reporter_name, 120), ok_kind, ok_id,
                 _clip((attested or {}).get("instance"), 80),
                 _clip((attested or {}).get("app"), 80),
                 json.dumps(tech, ensure_ascii=False), at, at))
            self._entry(cid, reporter, reporter_name, "created", "",
                        {"queue": queue})
            self._first_responder(cid, queue)
        return {"id": cid, "number": number}

    def _entry(self, cid, actor, actor_name, typ, text, data=None, internal=False):
        self.db.execute(
            "INSERT INTO entries (case_id, at, actor, actor_name, type, text, data, internal) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, _now(), actor, _clip(actor_name, 120), typ, text or "",
             json.dumps(data or {}, ensure_ascii=False), 1 if internal else 0))
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
            # 0.10.0 (D3): alle, die je verantwortlich oder zugewiesen waren
            # 0.15.0 (§6): Befragte nur, solange der Vorgang offen ist
            c["involved"] = sorted({r["uid"] for r in self.db.execute(
                "SELECT uid, role FROM involved WHERE case_id=?", (c["id"],))
                if r["role"] != "consulted" or c["status"] not in ENDED})
            c["consults"] = [dict(r) for r in self.db.execute(
                "SELECT * FROM consults WHERE case_id=? ORDER BY seq", (c["id"],))]
        return c

    def listing(self, *, queues=(), reporter=None, open_only=True, person=None, involved=None):
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
        if person:
            # 0.10.0 (D3): jetzt verantwortlich oder zugewiesen
            # 0.15.0 (§6): und offene Rückfragen an mich
            conds.append("responsible=? OR assignee=? OR id IN (SELECT case_id FROM consults "
                         "WHERE target=? AND answered='')")
            args += [person, person, person]
        if involved:
            # 0.11.0 (RFC-0103 D6): wer je beteiligt war, findet den Vorgang in der Übersicht
            conds.append("id IN (SELECT case_id FROM involved WHERE uid=?)")
            args.append(involved)
        if not conds:
            return []
        sql = "SELECT * FROM cases WHERE (" + " OR ".join(conds) + ")"
        if open_only:
            sql += f" AND status IN ({','.join('?' * len(OPEN))})"
            args += list(OPEN)
        sql += " ORDER BY updated DESC LIMIT 500"
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args)]

    MINE_COLS = "number, title, status, mark, object_kind, object_id, updated"

    def status_of(self, numbers, reporter):
        """0.6.0: Status der eigenen Vorgänge -- nur die, die diese Person gemeldet hat.
        0.9.0: je Nummer die Zeile (Status, Markierung, Bezug, Betreff, letzte Änderung)."""
        numbers = [n for n in numbers if isinstance(n, str) and n][:50]
        if not numbers or not reporter or reporter == REMOVED:
            return {}
        with self.lock:
            rows = self.db.execute(
                f"SELECT {self.MINE_COLS} FROM cases WHERE reporter=? AND number IN "
                f"({','.join('?' * len(numbers))})", (reporter, *numbers)).fetchall()
        return {r["number"]: dict(r) for r in rows}

    def mine(self, reporter, limit=200):
        """0.9.0 (RFC-0103 D8): alle eigenen Meldungen, neueste zuerst, höchstens 200 --
        damit der Chat seine Gespräche mit einem Aufruf färbt."""
        if not reporter or reporter == REMOVED:
            return []
        with self.lock:
            return [dict(r) for r in self.db.execute(
                f"SELECT {self.MINE_COLS} FROM cases WHERE reporter=? "
                "ORDER BY updated DESC, number DESC LIMIT ?", (reporter, int(limit)))]

    # ------------------------------- die meldende Person nimmt ab (0.9.0, RFC-0103 D10)
    def accept(self, c, actor, actor_name):
        """„Lösung passt“: beantwortet -> geschlossen mit der Markierung accepted. Nur die
        meldende Person (die Prüfung steht HIER, damit Seite und Chat-Weg dieselbe ist)."""
        if not actor or c["reporter"] != actor:
            raise Refused("Abnehmen kann nur, wer den Vorgang gemeldet hat.")
        if c["status"] != "answered":
            raise Refused("Abnehmen geht nur bei einem beantworteten Vorgang.")
        with self.lock, self.db:
            if not self.db.execute(
                    "UPDATE cases SET status='closed', mark='accepted', ended=? "
                    "WHERE id=? AND status='answered'", (_now(), c["id"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            self._entry(c["id"], actor, actor_name, "status", "Lösung passt.",
                        {"from": "answered", "to": "closed", "mark": "accepted"})

    def not_fitting(self, c, actor, actor_name, text):
        """„Passt nicht“: beantwortet -> wieder offen, mit einem Kommentar (Pflicht)."""
        if not actor or c["reporter"] != actor:
            raise Refused("Das kann nur, wer den Vorgang gemeldet hat.")
        if c["status"] != "answered":
            raise Refused("Das geht nur bei einem beantworteten Vorgang.")
        text = (text or "").strip()
        if not text:
            raise Refused("Bitte schreib dazu, was nicht passt.")
        if len(text) > BODY_MAX:
            raise Refused(f"Höchstens {BODY_MAX} Zeichen.")
        with self.lock, self.db:
            if not self.db.execute(
                    "UPDATE cases SET status='reopened', mark='', ended='' "
                    "WHERE id=? AND status='answered'", (c["id"],)).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            self._entry(c["id"], actor, actor_name, "status", text,
                        {"from": "answered", "to": "reopened", "mark": "not_fitting"})

    def close_unanswered(self, days=ACCEPT_DAYS, now=None):
        """RFC-0103 D11: ein beantworteter Vorgang ohne jede Bewegung seit `days` Tagen
        schließt sich -- mit der Markierung no_response, nie accepted (Köder 7).
        „Bewegung“ ist jeder Eintrag (cases.updated). Gibt die Nummern zurück."""
        now = now or time.time()
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - int(days) * 86400))
        done = []
        with self.lock, self.db:
            for r in self.db.execute("SELECT id, number FROM cases WHERE status='answered' "
                                     "AND updated <= ?", (cutoff,)).fetchall():
                if self.db.execute("UPDATE cases SET status='closed', mark='no_response', ended=? "
                                   "WHERE id=? AND status='answered'", (_now(), r["id"])).rowcount:
                    self._entry(r["id"], "system", "Automatisch", "status",
                                f"Nach {days} Tagen ohne Rückmeldung geschlossen.",
                                {"from": "answered", "to": "closed", "mark": "no_response"})
                    done.append(r["number"])
        return done

    def comment(self, c, actor, actor_name, text, internal=False):
        """0.10.0: `internal` -- nur für die Bearbeitenden (wer darf, prüft der Aufrufer).
        Antwortet die meldende Person auf eine Rückfrage, geht der Vorgang von selbst
        zurück nach „in Arbeit“ (RFC-0103 §3)."""
        text = (text or "").strip()
        if not text:
            raise Refused("Ein leerer Kommentar wird nicht gespeichert.")
        if len(text) > BODY_MAX:
            raise Refused(f"Höchstens {BODY_MAX} Zeichen.")
        with self.lock, self.db:
            self._entry(c["id"], actor, actor_name, "comment", text, internal=internal)
            if (not internal and actor and actor == c["reporter"]
                    and c["status"] == "waiting_reporter"
                    and self.db.execute("UPDATE cases SET status='in_progress' WHERE id=? "
                                        "AND status='waiting_reporter'", (c["id"],)).rowcount):
                self._entry(c["id"], "system", "Automatisch", "status",
                            "Die Meldende hat geantwortet.",
                            {"from": "waiting_reporter", "to": "in_progress"})

    def set_status(self, c, actor, actor_name, new, text=""):
        if new not in NEXT.get(c["status"], ()):
            raise Refused(f"Von „{STATUS_LABEL.get(c['status'], c['status'])}“ "
                          f"geht es nicht nach „{STATUS_LABEL.get(new, new)}“.")
        text = (text or "").strip()
        if new == "rejected" and not text:
            raise Refused("Eine Ablehnung braucht eine Begründung.")
        with self.lock, self.db:
            # Q6: die Frist laeuft ab dem Abschluss, und ein Wiederoeffnen
            # haelt sie an
            ended = _now() if new in ENDED else ""
            # 0.9.0: wer ohne die meldende Person schließt, lässt die Markierung leer
            if not self.db.execute("UPDATE cases SET status=?, ended=?, mark='' WHERE id=? AND status=?",
                                   (new, ended, c["id"], c["status"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            self._entry(c["id"], actor, actor_name, "status", text,
                        {"from": c["status"], "to": new})

    # ------------------------- verantwortlich und zugewiesen (0.10.0, RFC-0103 D3)
    ROLE_WORD = {"responsible": "verantwortlich", "assignee": "zugewiesen"}

    def set_role(self, c, role, uid, actor, actor_name):
        """Setzt `responsible` oder `assignee` (leer = niemand). Ob die Person das darf
        und ob die neue Person passt (verantwortlich: ein Zuständiger des Bereichs),
        prüft der Aufrufer -- dort stehen die Rechte. Hier: es gibt die Person, und
        jeder Wechsel ist ein Eintrag im Verlauf; wer es war, liest weiter."""
        if role not in self.ROLE_WORD:
            raise Refused("Rolle: verantwortlich oder zugewiesen.")
        uid = (uid or "").strip()
        if c["status"] in ENDED:
            raise Refused("Ein beendeter Vorgang bekommt niemanden mehr zugeteilt.")
        if uid == c.get(role, ""):
            raise Refused(f"Das ist schon so ({self.ROLE_WORD[role]}).")
        with self.lock, self.db:
            name = ""
            if uid:
                r = self.db.execute("SELECT name FROM people WHERE uid=?", (uid,)).fetchone()
                if not r:
                    raise Refused("Diese Person kennt die App noch nicht. Sie öffnet die App "
                                  "Vorgänge einmal, danach steht sie in der Auswahl.")
                name = r["name"]
            if not self.db.execute(f"UPDATE cases SET {role}=? WHERE id=? AND {role}=?",
                                   (uid, c["id"], c.get(role, ""))).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            if uid:
                self.db.execute("INSERT OR IGNORE INTO involved VALUES (?,?,?,?)",
                                (c["id"], uid, role, _now()))
            # intern: wer bearbeitet, ist Sache der Bearbeitenden (die Meldende sieht auch die
            # Zeile „Verantwortlich / Zugewiesen“ nicht)
            self._entry(c["id"], actor, actor_name, "role", name,
                        {"role": role, "from": c.get(role, ""), "to": uid}, internal=True)

    # ------------------------------ Spezialisten (0.13.0, RFC-0105 Stufe 1)
    SPEC_KINDS = ("note", "tool", "end", "plan", "draft")
    # 0.14.0 (RFC-0105 Stufe 2): was ein Mensch freigeben muss -- ein geplanter Aufruf
    # (Werkzeug + Argumente) oder ein Entwurf an die meldende Person
    WAITING = ("plan", "draft")

    @staticmethod
    def plan_hash(kind, data, text):
        """Der Hash, an den eine Freigabe gebunden ist (§7.1): genau dieser Aufruf, genau dieser Text."""
        doc = ({"tool": data.get("tool"), "arguments": data.get("arguments") or {}} if kind == "plan"
               else {"draft": text})
        return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False,
                                         separators=(",", ":")).encode()).hexdigest()

    def turn(self, uid):
        """Offene Vorgänge, für die uid verantwortlich ist und an denen kein Stopp hängt --
        mit dem letzten Eintrag, damit der Spezialist sieht, ob seit seinem Lauf etwas kam."""
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM cases WHERE responsible=? AND spec_stop=0 AND status IN "
                f"({','.join('?' * len(OPEN))}) ORDER BY updated LIMIT 200", [uid] + list(OPEN)).fetchall()
            out = []
            for c in rows:
                last = self.db.execute("SELECT seq, actor, type, at FROM entries WHERE case_id=? "
                                       "AND type != 'role' ORDER BY seq DESC LIMIT 1", (c["id"],)).fetchone()
                runs = self.db.execute("SELECT COUNT(DISTINCT json_extract(data, '$.run')) FROM entries "
                                       "WHERE case_id=? AND actor=? AND type='spec'", (c["id"], uid)).fetchone()[0]
                out.append({"number": c["number"], "title": c["title"], "status": c["status"],
                            "queue": c["queue"], "updated": c["updated"], "runs": runs,
                            "last": dict(last) if last else None})
            # 0.15.0 (§3.4/§6): offene Rückfragen an uid -- je eine, mit der Frage
            for r in self.db.execute(
                    f"SELECT k.seq, k.asked_by, c.number, c.title, c.status, c.queue, c.updated FROM consults k "
                    f"JOIN cases c ON c.id = k.case_id WHERE k.target=? AND k.answered='' AND c.spec_stop=0 "
                    f"AND c.status IN ({','.join('?' * len(OPEN))}) ORDER BY k.seq LIMIT 50",
                    [uid] + list(OPEN)).fetchall():
                out.append({"number": r["number"], "title": r["title"], "status": r["status"],
                            "queue": r["queue"], "updated": r["updated"], "consult": r["seq"]})
        return out

    def spec_entry(self, c, uid, name, kind, text, data):
        """Ein Schritt eines Laufs, immer intern: note (Modelltext), tool (Aufruf, gekürzt),
        end (warum der Lauf endete). Nur, wer verantwortlich ist; nach einem Stopp nur noch end."""
        if kind not in self.SPEC_KINDS:
            raise Refused("Art des Eintrags: note, tool oder end.")
        text = (text or "").strip()
        if len(text) > BODY_MAX:
            text = text[:BODY_MAX - 1] + "…"
        if not text:
            raise Refused("Ein leerer Eintrag wird nicht gespeichert.")
        data = {k: v for k, v in (data or {}).items() if FIELD_KEY.match(str(k)) and _plain(v)}
        if len(json.dumps(data, ensure_ascii=False)) > FIELDS_MAX:
            raise Refused("Die Angaben zum Schritt sind zu groß.")
        data["kind"] = kind
        with self.lock, self.db:
            r = self.db.execute("SELECT responsible, spec_stop, status FROM cases WHERE id=?",
                                (c["id"],)).fetchone()
            if not r or r["responsible"] != uid:
                raise Refused("Schreiben darf hier nur, wer verantwortlich ist.")
            if r["status"] in ENDED:
                raise Refused("Der Vorgang ist beendet.")
            if r["spec_stop"] and kind != "end":
                raise Refused("An diesem Vorgang ist der Spezialist angehalten.")
            if kind in self.WAITING:
                if kind == "plan" and not data.get("tool"):
                    raise Refused("Ein geplanter Aufruf nennt sein Werkzeug.")
                # der Hash entsteht hier, nicht beim Spezialisten: Vorgänge bindet die Freigabe
                data["sha256"] = self.plan_hash(kind, data, text)
            self._entry(c["id"], uid, name, "spec", text, data, internal=True)
            if kind in self.WAITING and "waiting_approval" in NEXT.get(r["status"], ()):
                self.db.execute("UPDATE cases SET status='waiting_approval' WHERE id=?", (c["id"],))
                self._entry(c["id"], uid, name, "status", "",
                            {"from": r["status"], "to": "waiting_approval"}, internal=True)

    def decide(self, c, seq, ok, actor, actor_name, text="", signer=None):
        """§7.1/§7.2: ein Mensch gibt einen geplanten Aufruf oder einen Entwurf frei -- oder nicht.
        Nie der Spezialist selbst, nie zweimal. Ein freigegebener Entwurf wird öffentlich (im
        Namen des Spezialisten, mit der freigebenden Person). Danach ist der Spezialist wieder dran."""
        with self.lock, self.db:
            e = self.db.execute("SELECT * FROM entries WHERE seq=? AND case_id=? AND type='spec'",
                                (seq, c["id"])).fetchone()
            data = json.loads(e["data"] or "{}") if e else {}
            if not e or data.get("kind") not in self.WAITING:
                raise Refused("Diesen geplanten Schritt gibt es nicht.")
            if e["actor"] == actor:
                raise Refused("Seinen eigenen Plan gibt niemand selbst frei.")
            if self.db.execute("SELECT 1 FROM entries WHERE case_id=? AND type='approval' AND "
                               "json_extract(data, '$.plan')=?", (c["id"], seq)).fetchone():
                raise Refused("Darüber ist schon entschieden.")
            if self.plan_hash(data["kind"], data, e["text"]) != data.get("sha256"):
                raise Refused("Der Plan wurde verändert; so wird er nicht freigegeben.")
            r = self.db.execute("SELECT status FROM cases WHERE id=?", (c["id"],)).fetchone()
            if r["status"] in ENDED:
                raise Refused("Der Vorgang ist beendet.")
            adata = {"plan": seq, "sha256": data["sha256"], "ok": bool(ok), "kind": data["kind"]}
            if ok and data["kind"] == "plan" and signer:
                # 0.16.0 (RFC-0105 §7.1): die Freigabe, unterschrieben -- das MCP-Gateway prüft
                # sie selbst (Sicht mit approval=signed), genau diesen Aufruf, genau einmal
                adata["token"] = signer({"case": c["number"], "case_id": c["id"], "plan": seq,
                                         "tool": data.get("tool") or "",
                                         "arguments": data.get("arguments") or {}, "by": actor_name})
            self._entry(c["id"], actor, actor_name, "approval", _clip((text or "").strip(), 2000),
                        adata, internal=True)
            if ok and data["kind"] == "draft":
                self._entry(c["id"], e["actor"], e["actor_name"], "comment", e["text"],
                            {"draft": seq, "approved_by": actor_name}, internal=False)
            if r["status"] == "waiting_approval":
                self.db.execute("UPDATE cases SET status='in_progress' WHERE id=?", (c["id"],))
                self._entry(c["id"], actor, actor_name, "status", "",
                            {"from": "waiting_approval", "to": "in_progress"}, internal=True)

    def give_back(self, c, uid, name, reason, run=""):
        """RFC-0105 §7.3: zurück an die Menschen des Bereichs -- verantwortlich leer, Status
        Klärung (wo erlaubt), ein interner Eintrag mit dem Grund."""
        reason = _clip((reason or "").strip(), 2000) or "zurückgegeben"
        with self.lock, self.db:
            r = self.db.execute("SELECT responsible, status FROM cases WHERE id=?", (c["id"],)).fetchone()
            if not r or r["responsible"] != uid:
                raise Refused("Zurückgeben kann nur, wer verantwortlich ist.")
            self.db.execute("UPDATE cases SET responsible='' WHERE id=?", (c["id"],))
            self._entry(c["id"], uid, name, "role", "",
                        {"role": "responsible", "from": uid, "to": "", "given_back": True}, internal=True)
            self._entry(c["id"], uid, name, "spec", reason,
                        {"kind": "end", "reason": "gave_back", "run": _clip(run, 40)}, internal=True)
            new = "triage" if "triage" in NEXT.get(r["status"], ()) else ""
            if new:
                self.db.execute("UPDATE cases SET status=? WHERE id=?", (new, c["id"]))
                self._entry(c["id"], uid, name, "status", "",
                            {"from": r["status"], "to": new}, internal=True)

    # --------------------------------------- Rückfragen (0.15.0, RFC-0105 Stufe 3, §6)
    def consultable(self, c, asker):
        """Wen man an diesem Vorgang fragen kann: die Zuständigen des Bereichs und alle
        Spezialisten -- nicht sich selbst, nicht die meldende Person."""
        with self.lock:
            rows = self.db.execute(
                "SELECT p.uid, p.name, p.specialist FROM people p WHERE p.specialist=1 OR p.uid IN "
                "(SELECT uid FROM queue_members WHERE queue=?) ORDER BY p.specialist, p.name",
                (c["queue"],)).fetchall()
        return [{"uid": r["uid"], "name": r["name"], "specialist": bool(r["specialist"])}
                for r in rows if r["uid"] not in (asker, c["reporter"])]

    def consult(self, c, asker, asker_name, target, text, data=None, as_handler=False):
        """Eine Rückfrage (§6): eine Frage, eine Antwort; die Befragten werden nie verantwortlich.
        Fragen darf, wer verantwortlich ist -- ein Mensch des Bereichs auch sonst (`as_handler`,
        das prüft der Aufrufer). Grenzen (§5): höchstens CONSULT_SPECIALISTS_MAX Rückfragen an
        Spezialisten je Vorgang, und nach SPEC_TO_SPEC_IN_ROW Antworten von Spezialisten auf
        Spezialisten ist ein Mensch dran."""
        text = (text or "").strip()
        if not text:
            raise Refused("Eine Rückfrage ohne Frage wird nicht gespeichert.")
        if len(text) > BODY_MAX:
            raise Refused(f"Höchstens {BODY_MAX} Zeichen.")
        target = (target or "").strip()
        data = {k: v for k, v in (data or {}).items() if k in ("run", "assistant") and isinstance(v, str)}
        with self.lock, self.db:
            r = self.db.execute("SELECT responsible, spec_stop, status, reporter, queue FROM cases WHERE id=?",
                                (c["id"],)).fetchone()
            if not r or r["status"] in ENDED:
                raise Refused("Der Vorgang ist beendet.")
            if r["responsible"] != asker and not as_handler:
                raise Refused("Rückfragen stellt, wer verantwortlich ist.")
            specs = {x["uid"] for x in self.db.execute("SELECT uid FROM people WHERE specialist=1")}
            if asker in specs and r["spec_stop"]:
                raise Refused("An diesem Vorgang ist der Spezialist angehalten.")
            p = self.db.execute("SELECT name FROM people WHERE uid=?", (target,)).fetchone()
            member = self.db.execute("SELECT 1 FROM queue_members WHERE queue=? AND uid=?",
                                     (r["queue"], target)).fetchone()
            if not p or target in (asker, r["reporter"]) or not (target in specs or member):
                raise Refused("Fragen kann man die Zuständigen des Bereichs und die Spezialisten.")
            if self.db.execute("SELECT 1 FROM consults WHERE case_id=? AND target=? AND answered=''",
                               (c["id"], target)).fetchone():
                raise Refused(f"An {p['name']} ist schon eine Rückfrage offen.")
            if target in specs:
                n = self.db.execute("SELECT COUNT(*) FROM consults WHERE case_id=? AND specialist=1",
                                    (c["id"],)).fetchone()[0]
                if n >= CONSULT_SPECIALISTS_MAX:
                    raise Refused(f"An diesem Vorgang gab es schon {n} Rückfragen an Spezialisten. "
                                  "Weitere Rückfragen gehen an Menschen des Bereichs.")
                if asker in specs and self._spec_to_spec_in_row(c["id"], specs) >= SPEC_TO_SPEC_IN_ROW:
                    raise Refused(f"{SPEC_TO_SPEC_IN_ROW} Antworten von Spezialisten in Folge; "
                                  "jetzt ist ein Mensch dran.")
            data.update({"to": target, "to_name": p["name"], "specialist": target in specs})
            self._entry(c["id"], asker, asker_name, "consult", text, data, internal=True)
            seq = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.execute("INSERT INTO consults (seq, case_id, asked_by, asked_by_specialist, target, "
                            "target_name, specialist, at) VALUES (?,?,?,?,?,?,?,?)",
                            (seq, c["id"], asker, 1 if asker in specs else 0, target, p["name"],
                             1 if target in specs else 0, _now()))
            self.db.execute("INSERT OR IGNORE INTO involved VALUES (?,?,?,?)", (c["id"], target, "consulted", _now()))
            if "waiting_consult" in NEXT.get(r["status"], ()):
                self.db.execute("UPDATE cases SET status='waiting_consult' WHERE id=?", (c["id"],))
                self._entry(c["id"], asker, asker_name, "status", "",
                            {"from": r["status"], "to": "waiting_consult"}, internal=True)
        return seq

    def _spec_to_spec_in_row(self, cid, specs):
        """Beantwortete Rückfragen Spezialist → Spezialist seit dem letzten Eintrag eines Menschen."""
        last_human = 0
        for e in self.db.execute("SELECT seq, actor FROM entries WHERE case_id=? ORDER BY seq DESC", (cid,)):
            if e["actor"] not in specs and e["actor"] != "system":
                last_human = e["seq"]
                break
        return self.db.execute("SELECT COUNT(*) FROM consults WHERE case_id=? AND asked_by_specialist=1 "
                               "AND specialist=1 AND answered!='' AND answer_seq > ?",
                               (cid, last_human)).fetchone()[0]

    def answer_consult(self, c, uid, name, seq, text, data=None):
        """Die eine Antwort auf eine Rückfrage, intern. Nur die befragte Person, nur einmal.
        Sind danach keine Rückfragen mehr offen, ist der Vorgang wieder „in Arbeit“, und wer
        verantwortlich ist, ist wieder dran (§3.3)."""
        text = (text or "").strip()
        if not text:
            raise Refused("Eine leere Antwort wird nicht gespeichert.")
        if len(text) > BODY_MAX:
            text = text[:BODY_MAX - 1] + "…"
        data = {k: v for k, v in (data or {}).items() if k in ("run", "assistant") and isinstance(v, str)}
        with self.lock, self.db:
            k = self.db.execute("SELECT * FROM consults WHERE seq=? AND case_id=?", (seq, c["id"])).fetchone()
            if not k or k["target"] != uid:
                raise Refused("Diese Rückfrage ging nicht an dich.")
            if k["answered"]:
                raise Refused("Diese Rückfrage ist schon beantwortet.")
            r = self.db.execute("SELECT status FROM cases WHERE id=?", (c["id"],)).fetchone()
            if r["status"] in ENDED:
                raise Refused("Der Vorgang ist beendet.")
            data["consult"] = seq
            self._entry(c["id"], uid, name, "consult_answer", text, data, internal=True)
            aseq = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.db.execute("UPDATE consults SET answer_seq=?, answered=? WHERE seq=?", (aseq, _now(), seq))
            still = self.db.execute("SELECT 1 FROM consults WHERE case_id=? AND answered=''", (c["id"],)).fetchone()
            if not still and r["status"] == "waiting_consult":
                # der Status kehrt von selbst zurück -- die Befragten ändern ihn nie (§6)
                self.db.execute("UPDATE cases SET status='in_progress' WHERE id=?", (c["id"],))
                self._entry(c["id"], "system", "Automatisch", "status", "Die Rückfragen sind beantwortet.",
                            {"from": "waiting_consult", "to": "in_progress"}, internal=True)

    def set_spec_stop(self, c, on, actor, actor_name):
        """§5: ein Mensch hält den Spezialisten an diesem Vorgang an (oder lässt ihn wieder zu)."""
        with self.lock, self.db:
            self.db.execute("UPDATE cases SET spec_stop=? WHERE id=?", (1 if on else 0, c["id"]))
            self._entry(c["id"], actor, actor_name, "spec_stop", "", {"on": bool(on)}, internal=True)

    # ------------------------------------------ der Knoten (RFC-0069 Stufe 2)
    def open_by_object(self, kind, oid):
        """Der offene Vorgang zu einem Bezug, oder None. Damit ein Absender,
        der dieselbe Sache ein zweites Mal meldet (ein Wiederholungsversuch
        nach einem Fehler), keinen zweiten Vorgang anlegt."""
        with self.lock:
            r = self.db.execute(
                "SELECT number FROM cases WHERE object_kind=? AND object_id=? "
                "AND status NOT IN ('closed', 'rejected') ORDER BY created DESC LIMIT 1",
                (kind, oid)).fetchone()
        return self.get(r["number"]) if r else None

    def close_by_node(self, c, actor, actor_name, text):
        """Der Knoten schliesst seinen eigenen Vorgang -- mit dem Beweis
        (Neustart erfolgt) oder der Absage -- aus JEDEM offenen Status. Das
        ist keine Bearbeitung, die der Tabelle NEXT folgt, sondern ein
        Tatbestand: die Maschine ist neu gestartet, der Vorgang ist erledigt.
        Ein schon beendeter Vorgang bleibt, wie er ist (False)."""
        if c["status"] in ENDED:
            return False
        text = (text or "").strip()[:BODY_MAX]
        with self.lock, self.db:
            if not self.db.execute(
                    "UPDATE cases SET status='closed', ended=? WHERE id=? AND status=?",
                    (_now(), c["id"], c["status"])).rowcount:
                return False
            self._entry(c["id"], actor, actor_name, "status", text,
                        {"from": c["status"], "to": "closed", "by": "node"})
        return True

    def move(self, c, actor, actor_name, target, text=""):
        if target == c["queue"]:
            raise Refused("Der Vorgang liegt schon dort.")
        with self.lock, self.db:
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (target,)).fetchone():
                raise Refused("Diesen Bereich gibt es nicht.")
            # 0.10.0 (D3): verschieben leert „verantwortlich“ -- der neue Bereich entscheidet
            if not self.db.execute("UPDATE cases SET queue=?, responsible='' WHERE id=? AND queue=?",
                                   (target, c["id"], c["queue"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen verschoben. Bitte neu laden.")
            # RFC-0061 §2.3: der alte Bereich bleibt Beobachter
            self.db.execute("INSERT OR IGNORE INTO observers VALUES (?,?)",
                            (c["id"], c["queue"]))
            self.db.execute("DELETE FROM observers WHERE case_id=? AND queue=?",
                            (c["id"], target))
            self._entry(c["id"], actor, actor_name, "move", (text or "").strip(),
                        {"from": c["queue"], "to": target,
                         **({"responsible_cleared": c["responsible"]} if c.get("responsible") else {})})
            self._first_responder(c["id"], target)

    def remove_person_data(self, months, now=None):
        """Q6: Personendaten eines Vorgangs, der seit `months` Monaten
        geschlossen oder abgelehnt ist, entfernen. Der Vorgang bleibt; die
        meldende Person wird zu „entfernt“ -- im Vorgang, in ihren eigenen
        Eintraegen und in den technischen Angaben (Benutzer-ID, Rollen,
        alles aus dem Browser). Was Bearbeitende geschrieben haben, bleibt.
        Gibt die Nummern zurueck."""
        now = now or time.time()
        cutoff = _months_before(now, months)
        done = []
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT id, number, reporter, tech FROM cases WHERE scrubbed = '' "
                f"AND status IN ({','.join('?' * len(ENDED))}) AND ended != '' AND ended <= ?",
                (*ENDED, cutoff)).fetchall()
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
            for r in rows:
                tech = json.loads(r["tech"] or "{}")
                att = dict(tech.get("attested") or {})
                att.pop("user_id", None)
                att.pop("roles", None)
                tech = {"attested": att}
                self.db.execute(
                    "UPDATE cases SET reporter=?, reporter_name=?, tech=?, scrubbed=? "
                    "WHERE id=?",
                    (REMOVED, REMOVED_NAME, json.dumps(tech, ensure_ascii=False),
                     stamp, r["id"]))
                self.db.execute("UPDATE entries SET actor=?, actor_name=? "
                                "WHERE case_id=? AND actor=?",
                                (REMOVED, REMOVED_NAME, r["id"], r["reporter"]))
                self._entry(r["id"], "system", "Löschfrist", "retention",
                            f"Personendaten der meldenden Person nach {months} "
                            f"Monaten entfernt.", {"months": months})
                done.append(r["number"])
        return done

    # ------------------------------------------------------------ Partner
    def partners(self, active_only=False):
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT * FROM partners ORDER BY name")]
        return [r for r in rows if r["active"]] if active_only else rows

    def partner(self, pid):
        with self.lock:
            r = self.db.execute("SELECT * FROM partners WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None

    def partner_by_principal(self, principal):
        """Wer ruft an? Nur ein aktiver Partner, dessen Prinzipal genau
        dieser ist -- ein leerer Eintrag passt auf niemanden."""
        if not principal:
            return None
        with self.lock:
            r = self.db.execute("SELECT * FROM partners WHERE in_principal=? AND active=1",
                                (principal,)).fetchone()
        return dict(r) if r else None

    def save_partner(self, pid, name, url, out_key, in_principal, in_queue):
        pid = (pid or "").strip().lower()
        name = _clip((name or "").strip(), 80)
        url = (url or "").strip().rstrip("/")
        in_principal = (in_principal or "").strip()
        if not PARTNER_ID.fullmatch(pid):
            raise Refused("Kennung des Partners: Kleinbuchstaben, Ziffern, Bindestrich.")
        if not name:
            raise Refused("Ein Partner braucht einen Namen.")
        if url and not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", url):
            raise Refused("Die Adresse des Partners ist eine http(s)-Adresse seiner "
                          "Vorgänge-App, z. B. https://vorgaenge.dev.example.org")
        with self.lock, self.db:
            if in_queue and not self.db.execute("SELECT 1 FROM queues WHERE id=?",
                                                (in_queue,)).fetchone():
                raise Refused("Diesen Bereich gibt es nicht.")
            if in_principal and self.db.execute(
                    "SELECT 1 FROM partners WHERE in_principal=? AND id!=?",
                    (in_principal, pid)).fetchone():
                raise Refused("Dieser Prinzipal gehört schon zu einem anderen Partner.")
            old = self.db.execute("SELECT * FROM partners WHERE id=?", (pid,)).fetchone()
            if old:
                # ein leeres Schluesselfeld heisst "behalten", nie "loeschen"
                self.db.execute(
                    "UPDATE partners SET name=?, url=?, out_key=?, in_principal=?, "
                    "in_queue=?, active=1 WHERE id=?",
                    (name, url, out_key or old["out_key"], in_principal,
                     in_queue or old["in_queue"], pid))
            else:
                self.db.execute(
                    "INSERT INTO partners (id, name, url, out_key, in_principal, "
                    "in_queue, active, created) VALUES (?,?,?,?,?,?,1,?)",
                    (pid, name, url, out_key or "", in_principal,
                     in_queue or DEFAULT_QUEUE, _now()))

    def deactivate_partner(self, pid):
        with self.lock, self.db:
            self.db.execute("UPDATE partners SET active=0 WHERE id=?", (pid,))

    # ------------------------------------------------------------ Uebergabe
    def handover_draft(self, c, partner, title, body, entry_seqs=(), with_tech=True,
                       handover_id=None, created=None, sender=""):
        """Das Dokument, wie es gehen wuerde (RFC-0061 §5.2). Deterministisch
        bei gleichen Eingaben -- Kennung und Zeit kommen aus der Vorschau
        mit, damit die Freigabe GENAU das schickt, was gezeigt wurde."""
        title = (title or "").strip()
        if not title:
            raise Refused("Die Übergabe braucht einen Betreff.")
        if len(title) > TITLE_MAX or len(body or "") > BODY_MAX:
            raise Refused("Betreff oder Text zu lang.")
        tech = {}
        att = (c.get("tech") or {}).get("attested") or {}
        if with_tech:
            for k in ("app", "version"):
                if att.get(k):
                    tech[k] = att[k]
            br = (c.get("tech") or {}).get("browser") or {}
            tech.update({k: br[k] for k in HANDOVER_BROWSER if br.get(k)})
        picked = {int(x) for x in entry_seqs or ()}
        entries = [{"at": e["at"], "type": e["type"], "text": e["text"]}
                   for e in c.get("entries") or ()
                   if e["seq"] in picked and e["type"] == "comment" and e["text"]]
        body = body or ""
        doc = {"handover_id": handover_id or str(uuid.uuid4()),
               "from": {"sender": sender, "case": c["number"]},
               "to": {"partner": partner["name"]},
               "kind": c["kind"], "title": title, "body": body,
               "object": ({"kind": c["object_kind"], "id": c["object_id"]}
                          if c.get("object_id") else None),
               "tech": tech, "entries": entries,
               "created": created or _now(),
               "body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
        check_outgoing(doc)
        return doc

    def release(self, c, partner, doc, actor, actor_name):
        """Freigegeben: Status `handed_over`, Zuordnung zu Hause, Ausgang."""
        check_outgoing(doc)
        if not partner.get("active") or not partner.get("url") or not partner.get("out_key"):
            raise Refused("Dieser Partner ist nicht zum Senden eingerichtet "
                          "(Adresse und Schlüssel fehlen).")
        if "handed_over" not in NEXT.get(c["status"], ()):
            raise Refused(f"Aus „{STATUS_LABEL.get(c['status'])}“ wird nicht übergeben.")
        raw = canonical(doc)
        with self.lock, self.db:
            if not self.db.execute("UPDATE cases SET status='handed_over', ended='' "
                                   "WHERE id=? AND status=?",
                                   (c["id"], c["status"])).rowcount:
                raise Refused("Der Vorgang wurde inzwischen geändert. Bitte neu laden.")
            self.db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?,?)",
                            (doc["handover_id"], "out", c["id"], partner["id"], raw,
                             hashlib.sha256(raw.encode()).hexdigest(), _now()))
            self.db.execute("INSERT INTO outbox (partner_id, handover_id, doc, created) "
                            "VALUES (?,?,?,?)", (partner["id"], doc["handover_id"], raw, _now()))
            self._entry(c["id"], actor, actor_name, "handover",
                        f"Übergeben an {partner['name']}.",
                        {"from": c["status"], "to": "handed_over",
                         "partner": partner["id"], "handover_id": doc["handover_id"],
                         "doc": doc})
        return doc["handover_id"]

    def receive(self, partner, raw_bytes):
        """Ein Dokument eines Partners. Unveraendert gespeichert, mit Hash;
        der Vorgang hier wird daraus abgeleitet, nie umgekehrt (§10.5).
        Doppelte Zustellung: dieselbe Antwort, nichts doppelt. Gibt
        (status, number) zurueck."""
        try:
            doc = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise Refused("Kein JSON.")
        if not isinstance(doc, dict):
            raise Refused("JSON-Objekt erwartet.")
        hid = str(doc.get("handover_id") or "")
        if not re.fullmatch(r"[0-9a-f-]{36}", hid):
            raise Refused("handover_id fehlt oder ist keine UUID.")
        body = doc.get("body") or ""
        if not isinstance(body, str) or hashlib.sha256(body.encode("utf-8")).hexdigest() \
                != doc.get("body_sha256"):
            raise Refused("body_sha256 passt nicht zum Text.")
        raw = raw_bytes.decode("utf-8")
        sha = hashlib.sha256(raw_bytes).hexdigest()
        with self.lock, self.db:
            seen = self.db.execute("SELECT case_id, partner_id FROM handovers "
                                   "WHERE handover_id=? AND direction='in'", (hid,)).fetchone()
            if seen:
                if seen["partner_id"] != partner["id"]:
                    raise Refused("Diese Kennung gehört einem anderen Partner.")
                num = self.db.execute("SELECT number FROM cases WHERE id=?",
                                      (seen["case_id"],)).fetchone()
                return "duplicate", num["number"] if num else ""
            if doc.get("answer_to"):
                # eine Antwort auf UNSERE Uebergabe -- nur von dem Partner,
                # an den sie ging
                home = self.db.execute(
                    "SELECT case_id FROM handovers WHERE handover_id=? AND direction='out' "
                    "AND partner_id=?", (str(doc["answer_to"]), partner["id"])).fetchone()
                if not home:
                    raise Refused("Auf diese Übergabe kann dieser Partner nicht antworten.")
                c = self.db.execute("SELECT * FROM cases WHERE id=?",
                                    (home["case_id"],)).fetchone()
                self.db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?,?)",
                                (hid, "in", c["id"], partner["id"], raw, sha, _now()))
                self._entry(c["id"], f"partner:{partner['id']}", partner["name"], "answer",
                            _clip(body, BODY_MAX), {"handover_id": hid,
                                                    "answer_to": doc["answer_to"]})
                if c["status"] == "handed_over":
                    self.db.execute("UPDATE cases SET status='answered' WHERE id=?", (c["id"],))
                    self._entry(c["id"], "system", "Übergabe", "status", "",
                                {"from": "handed_over", "to": "answered"})
                return "answer", c["number"]
            kind = doc.get("kind") if doc.get("kind") in KINDS else "bug"
            title = _clip((doc.get("title") or "").strip() or "(ohne Betreff)", TITLE_MAX)
            obj = doc.get("object") if isinstance(doc.get("object"), dict) else {}
            queue = partner.get("in_queue") or DEFAULT_QUEUE
            if not self.db.execute("SELECT 1 FROM queues WHERE id=?", (queue,)).fetchone():
                queue = DEFAULT_QUEUE
            cid = str(uuid.uuid4())
            number = self._number()
            tech = {"handover": {"partner": partner["id"], "from": doc.get("from"),
                                 "sha256": sha},
                    "received": {k: v for k, v in (doc.get("tech") or {}).items()
                                 if isinstance(v, (str, int, float))}}
            at = _now()
            self.db.execute(
                "INSERT INTO cases (id, number, kind, title, body, status, queue, "
                "reporter, reporter_name, object_kind, object_id, source_instance, "
                "source_app, tech, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, number, kind, title, _clip(body, BODY_MAX), "new", queue,
                 f"partner:{partner['id']}", partner["name"],
                 _clip(obj.get("kind"), 40), _clip(obj.get("id"), 200),
                 "", "", json.dumps(tech, ensure_ascii=False), at, at))
            self.db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?,?)",
                            (hid, "in", cid, partner["id"], raw, sha, at))
            self._entry(cid, f"partner:{partner['id']}", partner["name"], "received",
                        "", {"queue": queue, "handover_id": hid})
            for e in doc.get("entries") or ():
                if isinstance(e, dict) and e.get("text"):
                    self._entry(cid, f"partner:{partner['id']}", partner["name"],
                                "comment", _clip(e["text"], BODY_MAX),
                                {"original_at": _clip(e.get("at"), 30)})
            return "created", number

    def received_from(self, c):
        """(handover_id, partner) wenn dieser Vorgang aus einer Uebergabe
        stammt, sonst None."""
        with self.lock:
            r = self.db.execute("SELECT handover_id, partner_id FROM handovers "
                                "WHERE case_id=? AND direction='in' ORDER BY created LIMIT 1",
                                (c["id"],)).fetchone()
        if not r or c["reporter"] != f"partner:{r['partner_id']}":
            return None
        return r["handover_id"], self.partner(r["partner_id"])

    def answer(self, c, actor, actor_name, text):
        """Antwort an den Partner, von dem dieser Vorgang kam (§5.3) --
        auch sie verlaesst den Mandanten, also nur auf ausdruecklichen Klick."""
        got = self.received_from(c)
        if not got or not got[1]:
            raise Refused("Dieser Vorgang kam nicht aus einer Übergabe.")
        hid, partner = got
        text = (text or "").strip()
        if not text:
            raise Refused("Eine leere Antwort wird nicht gesendet.")
        if len(text) > BODY_MAX:
            raise Refused(f"Höchstens {BODY_MAX} Zeichen.")
        doc = {"handover_id": str(uuid.uuid4()), "answer_to": hid, "body": text,
               "created": _now(),
               "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        check_outgoing(doc)
        raw = canonical(doc)
        with self.lock, self.db:
            self.db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?,?)",
                            (doc["handover_id"], "out", c["id"], partner["id"], raw,
                             hashlib.sha256(raw.encode()).hexdigest(), _now()))
            self.db.execute("INSERT INTO outbox (partner_id, handover_id, doc, created) "
                            "VALUES (?,?,?,?)", (partner["id"], doc["handover_id"], raw, _now()))
            self._entry(c["id"], actor, actor_name, "answer_sent", text,
                        {"partner": partner["id"], "handover_id": doc["handover_id"]})
        return doc["handover_id"]

    # ------------------------------------------------------------ Ausgang
    def outbox_due(self, now=None):
        now = now or time.time()
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM outbox WHERE acked='' AND next_try<=? ORDER BY seq", (now,))]

    def outbox_for_case(self, cid):
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT o.* FROM outbox o JOIN handovers h ON h.handover_id=o.handover_id "
                "WHERE h.case_id=? AND h.direction='out' ORDER BY o.seq", (cid,))]

    def outbox_result(self, seq, ok, error="", now=None):
        now = now or time.time()
        with self.lock, self.db:
            if ok:
                self.db.execute("UPDATE outbox SET acked=?, last_error='', "
                                "attempts=attempts+1 WHERE seq=?", (_now(), seq))
            else:
                n = self.db.execute("SELECT attempts FROM outbox WHERE seq=?",
                                    (seq,)).fetchone()["attempts"] + 1
                # 30 s, 1, 2, 4 ... Minuten, hoechstens eine Stunde
                self.db.execute("UPDATE outbox SET attempts=?, next_try=?, last_error=? "
                                "WHERE seq=?",
                                (n, now + min(3600, 30 * 2 ** (n - 1)), _clip(error, 300), seq))

    def submissions_by(self, instance, seconds):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM cases WHERE source_instance=? "
                                   "AND created>=? AND tech LIKE '%\"submitted_by\"%'",
                                   (instance, since)).fetchone()[0]

    def recent_by(self, reporter, seconds):
        since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds))
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM cases WHERE reporter=? "
                                   "AND created>=?", (reporter, since)).fetchone()[0]
