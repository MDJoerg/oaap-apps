"""Lücken und Anfragen (RFC-0078 §15, Stufe 2b, I-50).

Eine KI, die keine Anleitung findet, darf -- wenn die Instanz es erlaubt
(HELP_REQUESTS=on, D12) -- eine anfordern. Gleiche Anfragen werden zu
Stimmen (D13); die Anfragen liegen hier, in /data von help (D14), ohne
Adresse und ohne Identität (§15.2, M14). Dazu zählt help Suchen ohne guten
Treffer (§15.4), ebenfalls ohne Personenbezug.

Eigene Datei (requests.db) neben dem Index: der Index wird bei jedem
Abruf neu gebaut, die Anfragen nie.
"""
import datetime
import json
import os
import re
import sqlite3
import threading
from contextlib import closing

from index import STOP, WORD, stem

MAX_TOPIC, MAX_QUESTION, MAX_TERM, MAX_TERMS = 120, 1000, 80, 10
MAX_OPEN, MAX_NEW_PER_HOUR = 200, 20
DUPLICATE = 0.5          # Ähnlichkeit, ab der eine Anfrage eine Stimme ist
KEEP_CLOSED_DAYS = 180   # D17
KEEP_GAP_DAYS = 90       # D17
STATUSES = ("open", "done", "rejected", "duplicate")
CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
SENSITIVE = re.compile(r"\S*(@|://)\S*")

SCHEMA = """
CREATE TABLE IF NOT EXISTS request (
  id TEXT PRIMARY KEY, topic TEXT, question TEXT, searched TEXT, found TEXT,
  lang TEXT, votes INTEGER, first_seen TEXT, last_seen TEXT, status TEXT,
  covered_by TEXT, note TEXT, doc_id TEXT, duplicate_of TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS vote (request_id TEXT, at TEXT, question TEXT);
CREATE TABLE IF NOT EXISTS gap (day TEXT, words TEXT, count INTEGER,
  PRIMARY KEY (day, words));
CREATE TABLE IF NOT EXISTS gap_total (words TEXT PRIMARY KEY, count INTEGER,
  first_day TEXT, last_day TEXT);
"""


class Refused(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(text, limit):
    """Text ohne Steuerzeichen, gekürzt. Gespeichert und gezeigt nur als Text."""
    text = CONTROL.sub(" ", str(text or "")).strip()
    return text[:limit]


def words_of(text):
    """Die Inhaltswörter als Stämme -- für Dubletten und Lücken."""
    out = []
    for w in WORD.findall(SENSITIVE.sub(" ", text or "")):
        lw = w.lower()
        if len(lw) < 2 or lw in STOP:
            continue
        if sum(c.isdigit() for c in lw) > 4:   # Nummern, Telefon, IDs: weg
            continue
        out.append(stem(lw))
    return out


def similarity(a, b):
    """Gewichtete Überlappung: das Thema zählt doppelt (§15.1)."""
    ta, tb = set(words_of(a["topic"])), set(words_of(b["topic"]))
    qa, qb = set(words_of(a["question"])), set(words_of(b["question"]))
    sa, sb = ta | qa, tb | qb
    if not sa or not sb:
        return 0.0
    topic = len(ta & tb) / max(1, len(ta | tb))
    whole = len(sa & sb) / max(1, min(len(sa), len(sb)))
    return (2 * topic + whole) / 3


class Requests:
    def __init__(self, data_dir, env=os.environ):
        self.path = os.path.join(data_dir, "requests.db")
        self.env = env
        self.lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as con:
            con.executescript(SCHEMA)
            con.commit()

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    @property
    def enabled(self):
        return (self.env.get("HELP_REQUESTS") or "").strip().lower() == "on"

    # --- anfordern ---------------------------------------------------------
    def submit(self, args, covered_by_for):
        """Eine Anfrage oder eine Stimme. `covered_by_for(text)` nennt ein
        vorhandenes Dokument, das gut passt, oder None."""
        if not self.enabled:
            raise Refused("Anfragen sind auf dieser Instanz ausgeschaltet.")
        req = {
            "topic": clean(args.get("topic"), MAX_TOPIC),
            "question": clean(args.get("question"), MAX_QUESTION),
            "searched": [clean(x, MAX_TERM) for x in (args.get("searched") or [])
                         if isinstance(x, str)][:MAX_TERMS],
            "found": [clean(x, MAX_TERM) for x in (args.get("found") or [])
                      if isinstance(x, str)][:MAX_TERMS],
            "lang": args.get("lang") if args.get("lang") in ("de", "en") else "",
        }
        if len(req["topic"]) < 5:
            raise Refused("topic fehlt: in einem Satz, was die Anleitung ermöglichen soll.")
        t = now()
        with self.lock, closing(self._con()) as con:
            self._sweep(con, t)
            open_rows = [dict(r) for r in con.execute(
                "SELECT * FROM request WHERE status='open'")]
            best, score = None, 0.0
            for r in open_rows:
                s = similarity(req, r)
                if s > score:
                    best, score = r, s
            if best and score >= DUPLICATE:
                con.execute("UPDATE request SET votes=votes+1, last_seen=? WHERE id=?",
                            (iso(t), best["id"]))
                con.execute("INSERT INTO vote VALUES (?,?,?)", (best["id"], iso(t), req["question"]))
                con.commit()
                return {"status": "vote", "request": best["id"], "votes": best["votes"] + 1,
                        "open_since": best["first_seen"], "topic": best["topic"],
                        "similarity": round(score, 2)}
            hour_ago = iso(t - datetime.timedelta(hours=1))
            recent = con.execute("SELECT count(*) FROM request WHERE first_seen>=?",
                                 (hour_ago,)).fetchone()[0]
            if len(open_rows) >= self.max_open or recent >= self.max_per_hour:
                raise Refused("Gerade werden keine neuen Anfragen angenommen (Grenze erreicht); "
                              "vorhandene Anfragen nehmen weiter Stimmen an.")
            year = t.year
            n = con.execute("SELECT count(*) FROM request WHERE id LIKE ?",
                            (f"R-{year}-%",)).fetchone()[0] + 1
            rid = f"R-{year}-{n:04d}"
            covered = covered_by_for(f"{req['topic']}") or ""
            con.execute("INSERT INTO request VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                rid, req["topic"], req["question"], json.dumps(req["searched"], ensure_ascii=False),
                json.dumps(req["found"], ensure_ascii=False), req["lang"], 1, iso(t), iso(t),
                "open", covered, "", "", "", ""))
            con.execute("INSERT INTO vote VALUES (?,?,?)", (rid, iso(t), req["question"]))
            con.commit()
            out = {"status": "new", "request": rid, "votes": 1, "open_since": iso(t)}
            if covered:
                out["note"] = (f"Das Dokument {covered} scheint die Frage schon zu beantworten -- "
                               "lies es mit help_read. Die Anfrage bleibt trotzdem stehen.")
            return out

    @property
    def max_open(self):
        return _int(self.env.get("HELP_REQUESTS_MAX_OPEN"), MAX_OPEN)

    @property
    def max_per_hour(self):
        return _int(self.env.get("HELP_REQUESTS_PER_HOUR"), MAX_NEW_PER_HOUR)

    # --- bearbeiten ----------------------------------------------------------
    def listing(self, status="open", limit=50):
        if status not in STATUSES and status != "all":
            raise Refused(f"status: {', '.join(STATUSES)} oder all")
        with closing(self._con()) as con:
            self._sweep(con, now())
            q = "SELECT * FROM request" + ("" if status == "all" else " WHERE status=?")
            q += " ORDER BY votes DESC, last_seen DESC LIMIT ?"
            args = ((status,) if status != "all" else ()) + (max(1, min(int(limit or 50), 200)),)
            return [self._row(r) for r in con.execute(q, args)]

    def read(self, rid):
        with closing(self._con()) as con:
            r = con.execute("SELECT * FROM request WHERE id=?", (rid,)).fetchone()
            if not r:
                return None
            out = self._row(r)
            out["questions"] = [dict(v) for v in con.execute(
                "SELECT at, question FROM vote WHERE request_id=? ORDER BY at", (rid,))]
            return out

    def close(self, rid, status, note="", doc_id="", duplicate_of="", doc_exists=lambda i: False):
        if status not in ("done", "rejected", "duplicate"):
            raise Refused("status: done, rejected oder duplicate")
        if status == "done" and not doc_exists(doc_id):
            raise Refused(f"done braucht doc_id eines Dokuments im Index; {doc_id!r} gibt es nicht "
                          "(erst die Doku holen: Verwaltung → Jetzt holen).")
        if status == "rejected" and not clean(note, 500):
            raise Refused("rejected braucht eine Begründung (note).")
        with self.lock, closing(self._con()) as con:
            r = con.execute("SELECT status FROM request WHERE id=?", (rid,)).fetchone()
            if not r:
                raise Refused(f"Anfrage {rid} gibt es nicht.")
            if status == "duplicate":
                d = con.execute("SELECT id FROM request WHERE id=?", (duplicate_of,)).fetchone()
                if not d or duplicate_of == rid:
                    raise Refused("duplicate braucht duplicate_of: die id einer anderen Anfrage.")
                n = con.execute("SELECT votes FROM request WHERE id=?", (rid,)).fetchone()[0]
                con.execute("UPDATE request SET votes=votes+? WHERE id=?", (n, duplicate_of))
            con.execute("UPDATE request SET status=?, note=?, doc_id=?, duplicate_of=?, closed_at=? "
                        "WHERE id=?", (status, clean(note, 500), doc_id if status == "done" else "",
                                       duplicate_of if status == "duplicate" else "", iso(now()), rid))
            con.commit()
        return self.read(rid)

    # --- Lücken (§15.4) ----------------------------------------------------
    def record_gap(self, query):
        ws = sorted(set(words_of(query)))
        if not ws:
            return
        key = " ".join(ws)[:200]
        day = now().strftime("%Y-%m-%d")
        with self.lock, closing(self._con()) as con:
            con.execute("INSERT INTO gap VALUES (?,?,1) ON CONFLICT(day, words) "
                        "DO UPDATE SET count=count+1", (day, key))
            con.commit()

    def gaps(self, days=30, limit=50):
        since = (now() - datetime.timedelta(days=max(1, min(int(days or 30), KEEP_GAP_DAYS)))
                 ).strftime("%Y-%m-%d")
        with closing(self._con()) as con:
            self._sweep(con, now())
            rows = con.execute("SELECT words, sum(count) AS n, min(day) AS first, max(day) AS last "
                               "FROM gap WHERE day>=? GROUP BY words ORDER BY n DESC, last DESC "
                               "LIMIT ?", (since, max(1, min(int(limit or 50), 200)))).fetchall()
            return [dict(r) for r in rows]

    def counts(self):
        with closing(self._con()) as con:
            return {s: con.execute("SELECT count(*) FROM request WHERE status=?", (s,)).fetchone()[0]
                    for s in STATUSES}

    # --- Fristen (D17) ------------------------------------------------------
    def _sweep(self, con, t):
        cut = iso(t - datetime.timedelta(days=KEEP_CLOSED_DAYS))
        old = [r[0] for r in con.execute(
            "SELECT id FROM request WHERE status<>'open' AND closed_at<>'' AND closed_at<?", (cut,))]
        for rid in old:
            con.execute("DELETE FROM vote WHERE request_id=?", (rid,))
            con.execute("DELETE FROM request WHERE id=?", (rid,))
        gcut = (t - datetime.timedelta(days=KEEP_GAP_DAYS)).strftime("%Y-%m-%d")
        for r in con.execute("SELECT words, sum(count), min(day), max(day) FROM gap WHERE day<? "
                             "GROUP BY words", (gcut,)).fetchall():
            con.execute("INSERT INTO gap_total VALUES (?,?,?,?) ON CONFLICT(words) DO UPDATE SET "
                        "count=count+excluded.count, last_day=excluded.last_day", tuple(r))
        con.execute("DELETE FROM gap WHERE day<?", (gcut,))
        con.commit()

    @staticmethod
    def _row(r):
        d = dict(r)
        d["searched"] = json.loads(d["searched"] or "[]")
        d["found"] = json.loads(d["found"] or "[]")
        return d


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default
