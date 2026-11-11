"""Der Index von help (RFC-0078 §4, §14.2): Dokumente, Abschnitte, Suche.

Ein Index wird immer NEU gebaut -- in eine eigene Datei, die erst nach dem
letzten Dokument an die Stelle der alten tritt (`os.replace`). Wer gerade
sucht, liest die alte Datei zu Ende; ein missglückter Abruf erreicht den
Index gar nicht erst (§3.1: nie ein leerer Index wegen eines schlechten
Abrufs).

Nur Standardbibliothek. Der Kopf eines Dokuments ist eine kleine
YAML-Teilmenge (key: value, [a, b], Anführungszeichen) -- genau das, was
oaap-docs schreibt und tools/check-docs.py prüft.
"""
import json
import os
import re
import sqlite3
from contextlib import closing

# Ohne Kopf gilt ein Dokument den Betreiberrollen (RFC-0078 §4, D5):
# ein fehlendes Feld macht den Leserkreis nie größer.
OPERATOR_ROLES = ["server_admin", "tenant_admin", "support"]
FIELDS = ("id", "title", "audience", "applies_from", "applies_until",
          "checked_against", "checked_on", "lang", "tags")
HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
WORD = re.compile(r"\w+", re.UNICODE)
MAX_LIMIT = 20


# --- der Kopf ------------------------------------------------------------

def _scalar(v):
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def front_matter(text):
    """(Felder, Rumpf). Ein Kopf, der sich nicht lesen lässt, zählt als
    keiner -- das Dokument bleibt auffindbar, mit den Voreinstellungen."""
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm = {}
    for line in text[4:end].split("\n"):
        if not line.strip() or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key, val = key.strip(), val.strip()
        if val[:1] not in ("'", '"'):          # Kommentar nur außerhalb von Anführungszeichen
            val = val.split(" #", 1)[0].rstrip()
        if key not in FIELDS:
            continue
        if val.startswith("[") and val.endswith("]"):
            fm[key] = [_scalar(x) for x in val[1:-1].split(",") if x.strip()]
        else:
            fm[key] = _scalar(val)
    return fm, text[end + 5:]


def slug(heading):
    """Der Anker, wie GitHub und Forgejo ihn bilden: klein, Leerzeichen zu
    Bindestrich, Satzzeichen weg (Umlaute bleiben)."""
    s = heading.strip().lower()
    s = re.sub(r"[^\w\- ]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def sections(body):
    """[(Anker, Überschrift, Text)] -- die Einheit, die eine Suche findet.
    Der Text vor der ersten ##-Überschrift gehört zur H1 (Anker '')."""
    out, head, anchor, buf, fence = [], "", "", [], False
    for line in body.split("\n"):
        if FENCE.match(line):
            fence = not fence
        m = None if fence else HEADING.match(line)
        if m and len(m.group(1)) == 1 and not out and not buf:
            head = m.group(2)
            continue
        if m and len(m.group(1)) >= 2:
            if buf or out or head:
                out.append((anchor, head, "\n".join(buf).strip()))
            head, anchor, buf = m.group(2), slug(m.group(2)), []
            continue
        buf.append(line)
    out.append((anchor, head, "\n".join(buf).strip()))
    return [s for s in out if s[1] or s[2]]


def title_of(fm, body, path):
    if fm.get("title"):
        return fm["title"]
    for line in body.split("\n"):
        m = HEADING.match(line)
        if m and len(m.group(1)) == 1:
            return m.group(2)
    return os.path.basename(path)[:-3]


# --- Sichtbarkeit -----------------------------------------------------------

# Wer auf dem öffentlichen Pfad fragt (zentrale Instanz, D8), sieht alles:
# der Inhalt ist die öffentliche Doku. Ausdrücklich zu übergeben -- es gibt
# keine Voreinstellung, damit eine vergessene Angabe nicht „alles“ heißt.
UNFILTERED = None


def visible(doc, roles):
    """Die EINE Prüfung hinter jeder Tür (RFC-0078 §6). `roles` ist
    UNFILTERED (öffentlicher Pfad) oder die Rollen der angemeldeten Person:
    dann zählt die Schnittmenge mit `audience` -- Relevanz, keine
    Geheimhaltung (D5). Ohne Rollen bleibt nur, was `public` ist."""
    if roles is UNFILTERED:
        return True
    aud = doc["audience"]
    return "public" in aud or bool(set(aud) & set(roles))


def version_tuple(v):
    try:
        return tuple(int(x) for x in str(v).split("."))
    except ValueError:
        return None


def applies(doc, version):
    """None: unbekannt; True/False: stimmt der Text für diese Version (D7)."""
    v = version_tuple(version) if version else None
    if not v:
        return None
    lo = version_tuple(doc.get("applies_from") or "")
    hi = version_tuple(doc.get("applies_until") or "")
    if lo and v < lo:
        return False
    if hi and v > hi:
        return False
    return True


# --- bauen ----------------------------------------------------------------

# Füllwörter einer Frage („Wie richte ich auf der CLI das Backup ein?“).
# Sie verlangen hieße: nur Abschnitte, die zufällig alle enthalten.
STOP = set("""
wie was wo wer wann warum welche welcher welches wieso weshalb kann könnte
muss soll will ich du er sie es wir ihr man mein dein sein unser mich mir
der die das den dem des ein eine einen einem einer eines und oder aber auch
auf in im an am zu zum zur von vom mit für über unter bei aus nach vor ist
sind wird werden hat habe haben gibt geht mache macht richte richten ein
dann denn noch nur nicht kein keine so da hier dort bitte gerne
how what where who when why which can could should would will do does did
the a an and or but of to in on at for with from by is are be it i you we
my your this that these those please
""".split())

ENDINGS = ("ern", "en", "er", "es", "e", "n", "s")


def stem(word):
    """Ein grober Stamm für die Präfixsuche: „Backups“ → „Backup“,
    „Instanzen“ → „Instanz“. Nur Endungen ab, nie unter vier Buchstaben;
    ein Präfix findet dann auch die Grundform."""
    w = word.lower()
    for e in ENDINGS:
        if w.endswith(e) and len(w) - len(e) >= 4:
            return w[: -len(e)]
    return w


def fts5_available():
    con = sqlite3.connect(":memory:")
    try:
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


SCHEMA = """
CREATE TABLE doc (source TEXT, id TEXT, path TEXT, title TEXT, audience TEXT,
                  applies_from TEXT, applies_until TEXT, checked_against TEXT,
                  checked_on TEXT, lang TEXT, tags TEXT, body TEXT,
                  priority INTEGER, trust TEXT, shadowed_by TEXT,
                  PRIMARY KEY (source, id));
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build(path, documents):
    """Baut einen Index aus [{source, priority, trust, path, text}] in
    `path` (über eine Nachbardatei, dann os.replace). Gibt die Zahl der
    Dokumente zurück."""
    tmp = path + ".new"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    fts = fts5_available()
    con.executescript(SCHEMA)
    if fts:
        con.execute("CREATE VIRTUAL TABLE section USING fts5(source UNINDEXED, "
                    "id UNINDEXED, anchor UNINDEXED, heading, text, "
                    "tokenize='unicode61 remove_diacritics 2')")
    else:
        # O2-Rückfall: ohne FTS5 eine schlichte Tabelle, gesucht wird mit LIKE.
        con.execute("CREATE TABLE section (source TEXT, id TEXT, anchor TEXT, "
                    "heading TEXT, text TEXT)")
    con.execute("INSERT INTO meta VALUES ('fts5', ?)", ("1" if fts else "0",))
    rows = {}
    for d in documents:
        fm, body = front_matter(d["text"])
        doc_id = fm.get("id") or d["path"][:-3]
        key = (d["source"], doc_id)
        if key in rows:          # zwei Dateien mit derselben id in EINER Quelle:
            continue             # die erste gewinnt, wie check-docs sie meldet
        aud = fm.get("audience") if isinstance(fm.get("audience"), list) else None
        tags = fm.get("tags") if isinstance(fm.get("tags"), list) else []
        rows[key] = {
            "source": d["source"], "id": doc_id, "path": d["path"],
            "title": title_of(fm, body, d["path"]),
            "audience": aud or OPERATOR_ROLES,
            "applies_from": fm.get("applies_from", ""),
            "applies_until": fm.get("applies_until", ""),
            "checked_against": fm.get("checked_against", ""),
            "checked_on": fm.get("checked_on", ""),
            "lang": fm.get("lang", ""), "tags": tags, "body": body,
            "priority": d["priority"], "trust": d["trust"]}
    # Überdecken (§3): dieselbe id in mehreren Quellen -- die höchste
    # Priorität antwortet, die anderen bleiben unter Quelle:id lesbar.
    best = {}
    for (src, doc_id), r in rows.items():
        if doc_id not in best or r["priority"] > rows[(best[doc_id], doc_id)]["priority"]:
            best[doc_id] = src
    for (src, doc_id), r in rows.items():
        shadow = "" if best[doc_id] == src else best[doc_id]
        con.execute("INSERT INTO doc VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            src, doc_id, r["path"], r["title"], json.dumps(r["audience"]),
            r["applies_from"], r["applies_until"], r["checked_against"],
            r["checked_on"], r["lang"], json.dumps(r["tags"], ensure_ascii=False),
            r["body"], r["priority"], r["trust"], shadow))
        for anchor, heading, text in sections(r["body"]):
            con.execute("INSERT INTO section (source, id, anchor, heading, text) "
                        "VALUES (?,?,?,?,?)",
                        (src, doc_id, anchor, heading or r["title"], text))
    con.commit()
    con.close()
    os.replace(tmp, path)
    return len(rows)


# --- lesen ----------------------------------------------------------------

def _doc(row):
    d = dict(row)
    d["audience"] = json.loads(d["audience"])
    d["tags"] = json.loads(d["tags"])
    return d


class Index:
    def __init__(self, path):
        self.path = path

    def _con(self):
        if not os.path.exists(self.path):
            return None
        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con

    def count(self):
        con = self._con()
        if not con:
            return 0
        with closing(con):
            return con.execute("SELECT count(*) FROM doc WHERE shadowed_by=''").fetchone()[0]

    def fts5(self):
        con = self._con()
        if not con:
            return fts5_available()
        with closing(con):
            row = con.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()
            return bool(row and row[0] == "1")

    def get(self, ref):
        """`id` (die antwortende Fassung) oder `quelle:id` (genau diese)."""
        con = self._con()
        if not con:
            return None
        with closing(con):
            if ":" in ref:
                src, doc_id = ref.split(":", 1)
                row = con.execute("SELECT * FROM doc WHERE source=? AND id=?",
                                  (src, doc_id)).fetchone()
            else:
                row = con.execute("SELECT * FROM doc WHERE id=? AND shadowed_by=''",
                                  (ref,)).fetchone()
            if not row:
                return None
            d = _doc(row)
            d["shadows"] = [r[0] for r in con.execute(
                "SELECT source FROM doc WHERE id=? AND source<>? ORDER BY priority DESC",
                (d["id"], d["source"]))]
            return d

    def id_by_path(self, source, path):
        """Für relative Links zwischen Dokumenten: die id hinter einem Pfad."""
        con = self._con()
        if not con:
            return None
        with closing(con):
            row = con.execute("SELECT id FROM doc WHERE source=? AND path=?",
                              (source, path)).fetchone()
            return row[0] if row else None

    def docs(self, prefix="", include_shadowed=False):
        con = self._con()
        if not con:
            return []
        with closing(con):
            q = "SELECT * FROM doc WHERE id LIKE ? ESCAPE '\\'"
            if not include_shadowed:
                q += " AND shadowed_by=''"
            like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            return [_doc(r) for r in con.execute(q + " ORDER BY source, id", (like,))]

    def search(self, query, limit=8, source=None, tags=None):
        """Abschnitte, bestes zuerst; nur antwortende Fassungen (keine
        überdeckten). Erst alle Wörter (UND), ohne Treffer jedes (ODER)."""
        words = [w for w in WORD.findall(query or "") if len(w) > 1]
        content = [w for w in words if w.lower() not in STOP]
        words = (content or words)[:12]
        limit = max(1, min(int(limit or 8), MAX_LIMIT))
        con = self._con()
        if not con or not words:
            return []
        with closing(con):
            fts = con.execute("SELECT value FROM meta WHERE key='fts5'").fetchone()[0] == "1"
            # Erst die Abschnitte mit ALLEN Wörtern, dahinter die mit einigen --
            # eine natürliche Frage trifft selten jedes Wort an einer Stelle.
            find = self._fts if fts else self._like
            rows = list(find(con, words, " AND "))
            if len(words) > 1:
                rows += find(con, words, " OR ")
            out, seen = [], set()
            for r in rows:
                d = con.execute("SELECT * FROM doc WHERE source=? AND id=?",
                                (r["source"], r["id"])).fetchone()
                if not d or d["shadowed_by"]:
                    continue
                d = _doc(d)
                if source and d["source"] != source:
                    continue
                if tags and not set(tags) & set(d["tags"]):
                    continue
                key = (d["source"], d["id"], r["anchor"])
                if key in seen:
                    continue
                seen.add(key)
                out.append({"doc": d, "anchor": r["anchor"], "section": r["heading"],
                            "snippet": r["snippet"]})
                if len(out) >= limit * 3:
                    break
            return out

    @staticmethod
    def _fts(con, words, joiner):
        # Jedes Wort in Anführungszeichen (kein FTS-Operator aus der Frage),
        # ab drei Buchstaben auch als Präfix: „Backup“ findet „Backups“.
        terms = []
        for w in words:
            if len(w) < 3:
                terms.append(f'"{w}"')
                continue
            st = stem(w)
            terms.append(f'("{w}"* OR "{st}"*)' if st != w else f'"{w}"*')
        q = joiner.join(terms)
        try:
            return con.execute(
                "SELECT source, id, anchor, heading, "
                "snippet(section, 4, '**', '**', ' … ', 24) AS snippet, "
                "bm25(section, 0, 0, 0, 5.0, 1.0) AS rank "
                "FROM section WHERE section MATCH ? ORDER BY rank LIMIT 200",
                (q,)).fetchall()
        except sqlite3.OperationalError:
            return []

    @staticmethod
    def _like(con, words, joiner):
        cond = joiner.join(["(heading LIKE ? OR text LIKE ?)"] * len(words))
        args = []
        for w in words:
            args += [f"%{w}%", f"%{w}%"]
        rows = con.execute(f"SELECT source, id, anchor, heading, text FROM section "
                           f"WHERE {cond} LIMIT 200", args).fetchall()
        out = []
        for r in rows:
            score = sum((r["heading"] or "").lower().count(w.lower()) * 5
                        + (r["text"] or "").lower().count(w.lower()) for w in words)
            out.append({"source": r["source"], "id": r["id"], "anchor": r["anchor"],
                        "heading": r["heading"], "snippet": (r["text"] or "")[:200],
                        "rank": -score})
        return sorted(out, key=lambda x: x["rank"])
