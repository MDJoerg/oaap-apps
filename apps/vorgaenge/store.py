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
                "rejected": "abgelehnt", "reopened": "wieder offen",
                "handed_over": "übergeben"}

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
MANUAL_EXCLUDED = ("handed_over",)
OPEN = ("new", "triage", "in_progress", "answered", "reopened", "handed_over")

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
               allowed_kinds=KINDS, asserted=None):
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
            # Q6: die Frist laeuft ab dem Abschluss, und ein Wiederoeffnen
            # haelt sie an
            ended = _now() if new in ENDED else ""
            if not self.db.execute("UPDATE cases SET status=?, ended=? WHERE id=? AND status=?",
                                   (new, ended, c["id"], c["status"])).rowcount:
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
