"""Der Speicher des Marketplace: Assets, Releases, Kanäle, Rechte (RFC-0077 §3, §15.3).

SQLite in `/data/market.db` (WAL), im Speicher der Instanz -- das Backup
des Knotens trägt es (D9). Die Dateien selbst liegen in `blobs.py`.

Regeln, die hier und nur hier gelten:

- Ein Release ist unveränderlich. Dieselbe Version mit denselben Bytes ist
  idempotent, mit anderen Bytes eine Ablehnung (409). Eine Version wird
  nie wiederverwendet, auch nach einem Rückruf nicht.
- Ein Kanal ist eine Menge von Platzierungen, keine Kopie. Sein aktuelles
  Release ist die höchste nicht zurückgerufene Version darin.
- Rückruf verlangt einen Grund und bleibt im Ereignisprotokoll.
"""
import json
import re
import sqlite3
import threading
import time
from contextlib import closing

VERSION_SPLIT = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
CHANNEL_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}$")

SCHEMA = [
    """CREATE TABLE asset(id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT,
         summary TEXT, description TEXT, visibility TEXT, free_channels TEXT,
         created_at REAL, created_by TEXT)""",
    """CREATE TABLE release(asset TEXT, version TEXT, notes TEXT, created_at REAL,
         created_by TEXT, yanked_at REAL, yanked_by TEXT, yank_reason TEXT,
         PRIMARY KEY(asset, version))""",
    "CREATE TABLE file(sha256 TEXT PRIMARY KEY, size INTEGER, media_type TEXT)",
    """CREATE TABLE release_file(asset TEXT, version TEXT, sha256 TEXT, name TEXT,
         role TEXT, PRIMARY KEY(asset, version, sha256))""",
    """CREATE TABLE placement(asset TEXT, version TEXT, channel TEXT, placed_at REAL,
         placed_by TEXT, PRIMARY KEY(asset, version, channel))""",
    """CREATE TABLE entitlement(id INTEGER PRIMARY KEY, principal_kind TEXT,
         principal TEXT, asset TEXT, channels TEXT, granted_by TEXT,
         granted_at REAL, expires_at REAL, note TEXT)""",
    """CREATE TABLE publisher(id INTEGER PRIMARY KEY, principal_kind TEXT,
         principal TEXT, asset_pattern TEXT, channels TEXT)""",
    """CREATE TABLE event(id INTEGER PRIMARY KEY, at REAL, kind TEXT, asset TEXT,
         version TEXT, channel TEXT, principal TEXT, detail TEXT)""",
    """CREATE TABLE recipient(principal TEXT, channel TEXT, last_poll REAL,
         offered TEXT, PRIMARY KEY(principal, channel))""",
]

# Stufe 2 (Schaufenster): Felder, Bilder, Freischalt-Anfragen, Volltext.
SCHEMA_2 = [
    "ALTER TABLE asset ADD COLUMN tags TEXT DEFAULT '[]'",
    "ALTER TABLE asset ADD COLUMN categories TEXT DEFAULT '[]'",
    "ALTER TABLE asset ADD COLUMN links TEXT DEFAULT '[]'",
    "ALTER TABLE asset ADD COLUMN license TEXT DEFAULT ''",
    "ALTER TABLE asset ADD COLUMN publisher TEXT DEFAULT ''",
    "ALTER TABLE asset ADD COLUMN icon TEXT DEFAULT ''",
    "ALTER TABLE asset ADD COLUMN screenshots TEXT DEFAULT '[]'",
    """CREATE TABLE image(asset TEXT, sha256 TEXT, media_type TEXT, size INTEGER,
         role TEXT, added_at REAL, added_by TEXT, PRIMARY KEY(asset, sha256))""",
    """CREATE TABLE access_request(id INTEGER PRIMARY KEY, asset TEXT, principal TEXT,
         channels TEXT, note TEXT, at REAL, state TEXT, decided_by TEXT,
         decided_at REAL, answer TEXT)""",
]
# Stufe 2 abgeschlossen (§15.8): Landschaften, Kategorien, Texte je Sprache,
# Review, Einstellungen, Hervorgehobenes.
SCHEMA_3 = [
    "ALTER TABLE asset ADD COLUMN texts TEXT DEFAULT '{}'",
    "ALTER TABLE asset ADD COLUMN landscapes TEXT DEFAULT '[]'",
    "ALTER TABLE asset ADD COLUMN featured INTEGER DEFAULT 0",
    "ALTER TABLE release ADD COLUMN review TEXT DEFAULT 'approved'",
    "ALTER TABLE release ADD COLUMN notes_i18n TEXT DEFAULT '{}'",
    """CREATE TABLE landscape(id TEXT PRIMARY KEY, sort INTEGER DEFAULT 100, logo TEXT DEFAULT '',
         texts TEXT DEFAULT '{}', fixed INTEGER DEFAULT 0)""",
    """CREATE TABLE category(id TEXT PRIMARY KEY, landscape TEXT, sort INTEGER DEFAULT 100,
         logo TEXT DEFAULT '', texts TEXT DEFAULT '{}')""",
    "CREATE TABLE setting(key TEXT PRIMARY KEY, value TEXT)",
    """CREATE TABLE review(id INTEGER PRIMARY KEY, kind TEXT, asset TEXT, version TEXT,
         created_at REAL, created_by TEXT, state TEXT, precheck TEXT DEFAULT '{}',
         decided_by TEXT, decided_at REAL, decided_as TEXT, summary TEXT,
         findings TEXT DEFAULT '[]', detail TEXT DEFAULT '')""",
    "CREATE TABLE reviewer(id INTEGER PRIMARY KEY, principal_kind TEXT, principal TEXT)",
]
OAAP_LANDSCAPE = {
    "en": {"title": "OAAP",
           "description": "Open Application & Automation Platform: apps, setup templates and "
                          "building blocks for your own node or a cloud tenant."},
    "de": {"title": "OAAP",
           "description": "Open Application & Automation Platform: Apps, Aufbau-Vorlagen und "
                          "Bausteine für den eigenen Knoten oder einen Cloud-Mandanten."},
}
DEFAULT_LANDSCAPE = "default"
LANG_RE = re.compile(r"^[a-z]{2}$")
TEXT_FIELDS = ("title", "summary", "description")
TEXT_MAX = {"title": 120, "summary": 300, "description": 20000}
FTS = ("CREATE VIRTUAL TABLE asset_fts USING fts5(id UNINDEXED, title, summary, "
       "description, tags, tokenize='unicode61 remove_diacritics 2')")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
LIST_FIELDS = ("tags", "categories", "links", "screenshots", "landscapes")


class Refused(Exception):
    """Eine Ablehnung mit HTTP-Status und einem Satz für Menschen."""

    def __init__(self, status, text):
        super().__init__(text)
        self.status = status


def version_key(v):
    """1.2.10 > 1.2.9, und 1.0.0-rc1 < 1.0.0 (wie der Paketkatalog)."""
    m = VERSION_SPLIT.match(v or "")
    if not m:
        return (-1, -1, -1, 0, "")
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0 if pre else 1, pre or "")


class Market:
    def __init__(self, path, channels=("stable", "beta")):
        self.path = path
        self.channels = [c for c in channels if CHANNEL_RE.match(c)] or ["stable"]
        self.lock = threading.RLock()
        with closing(self._con()) as con:
            con.execute("PRAGMA journal_mode=WAL")
            if con.execute("PRAGMA user_version").fetchone()[0] < 1:
                for stmt in SCHEMA:
                    con.execute(stmt)
                con.execute("PRAGMA user_version=1")
            if con.execute("PRAGMA user_version").fetchone()[0] < 2:
                for stmt in SCHEMA_2:
                    con.execute(stmt)
                con.execute("PRAGMA user_version=2")
            if con.execute("PRAGMA user_version").fetchone()[0] < 3:
                for stmt in SCHEMA_3:
                    con.execute(stmt)
                self._migrate_3(con)
                con.execute("PRAGMA user_version=3")
            try:
                con.execute(FTS)
            except sqlite3.OperationalError as e:
                # schon da -- oder FTS5 fehlt: dann sucht die App ohne Index
                self.fts = "already exists" in str(e)
            else:
                self.fts = True
            con.commit()
        if self.fts:
            for a in self.assets():
                self._reindex(a)

    @staticmethod
    def _migrate_3(con):
        """Stufe-2-Texte waren deutsch -> texts.de; freie Kategorien -> Landschaft
        default; bestehende OAAP-Apps -> Landschaft oaap. Nichts geht verloren."""
        con.execute("INSERT OR IGNORE INTO landscape(id, sort, texts, fixed) VALUES ('oaap', 0, ?, 1)",
                    (json.dumps(OAAP_LANDSCAPE, ensure_ascii=False),))
        cats = set()
        for r in con.execute("SELECT id, type, title, summary, description, categories FROM asset").fetchall():
            texts = {}
            if any(r[k] for k in TEXT_FIELDS):
                texts["de"] = {k: r[k] or "" for k in TEXT_FIELDS if r[k]}
            try:
                cats |= set(json.loads(r["categories"] or "[]"))
            except ValueError:
                pass
            con.execute("UPDATE asset SET texts=?, landscapes=? WHERE id=?",
                        (json.dumps(texts, ensure_ascii=False),
                         json.dumps(["oaap"] if r["type"] == "oaap-app" else []), r["id"]))
        for c in sorted(cats):
            con.execute("INSERT OR IGNORE INTO category(id, landscape, texts) VALUES (?,?,?)",
                        (c, DEFAULT_LANDSCAPE, json.dumps({"en": {"title": c}})))

    def _con(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _q(self, sql, args=()):
        with closing(self._con()) as con:
            return [dict(r) for r in con.execute(sql, args).fetchall()]

    def _x(self, stmts):
        with self.lock, closing(self._con()) as con:
            for sql, args in stmts:
                con.execute(sql, args)
            con.commit()

    def event(self, kind, asset="", version="", channel="", principal="", detail=""):
        self._x([("INSERT INTO event(at, kind, asset, version, channel, principal, detail) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (time.time(), kind, asset, version, channel, principal, detail[:500]))])

    # -- reading --------------------------------------------------------------
    @staticmethod
    def _decode(a):
        for k in LIST_FIELDS:
            try:
                a[k + "_list"] = json.loads(a.get(k) or "[]")
            except ValueError:
                a[k + "_list"] = []
        a["texts_map"] = _loads(a.get("texts"), {})
        return a

    def assets(self):
        return [self._decode(a) for a in self._q("SELECT * FROM asset ORDER BY id")]

    def asset(self, asset_id):
        r = self._q("SELECT * FROM asset WHERE id=?", (asset_id,))
        return self._decode(r[0]) if r else None

    def search_ids(self, q):
        """Ids, deren Text q trifft (Volltext, Präfix je Wort), oder None = alle.
        Ohne FTS5: Teilwortsuche. Gefiltert wird danach -- in der App."""
        q = (q or "").strip()
        if not q:
            return None
        words = [w for w in re.findall(r"\w+", q.lower()) if w]
        if not words:
            return None
        if self.fts:
            expr = " ".join(f'"{w}"*' for w in words)
            try:
                return [r["id"] for r in self._q(
                    "SELECT id FROM asset_fts WHERE asset_fts MATCH ? ORDER BY bm25(asset_fts)",
                    (expr,))]
            except sqlite3.OperationalError:
                pass
        out = []
        for a in self.assets():
            hay = " ".join([a["id"], self._all_text(a), " ".join(a["tags_list"])]).lower()
            if all(w in hay for w in words):
                out.append(a["id"])
        return out

    @staticmethod
    def _all_text(a, field=None):
        """Alle Sprachen eines Feldes (oder aller Textfelder) -- für die Suche."""
        a = Market._decode(dict(a))
        keys = (field,) if field else TEXT_FIELDS
        parts = [str((tx or {}).get(k) or "") for tx in a["texts_map"].values() for k in keys]
        if not a["texts_map"]:
            parts += [a.get(k) or "" for k in keys]
        return " ".join(parts)

    def _reindex(self, a):
        if not self.fts:
            return
        self._x([("DELETE FROM asset_fts WHERE id=?", (a["id"],)),
                 ("INSERT INTO asset_fts(id, title, summary, description, tags) VALUES (?,?,?,?,?)",
                  (a["id"], self._all_text(a, "title"), self._all_text(a, "summary"),
                   self._all_text(a, "description"),
                   " ".join(self._decode(dict(a))["tags_list"])))])

    def images(self, asset_id):
        return self._q("SELECT * FROM image WHERE asset=? ORDER BY added_at", (asset_id,))

    def image_assets(self, sha):
        return [r["asset"] for r in self._q("SELECT asset FROM image WHERE sha256=?", (sha,))]

    def image_row(self, asset_id, sha):
        r = self._q("SELECT * FROM image WHERE asset=? AND sha256=?", (asset_id, sha))
        return r[0] if r else None

    def requests(self, state=None, asset_id=None, principal=None):
        sql, args = "SELECT * FROM access_request WHERE 1=1", []
        for col, v in (("state", state), ("asset", asset_id), ("principal", principal)):
            if v:
                sql += f" AND {col}=?"
                args.append(v)
        return self._q(sql + " ORDER BY id DESC", args)

    def releases(self, asset_id):
        rs = self._q("SELECT * FROM release WHERE asset=?", (asset_id,))
        for r in rs:
            r["files"] = self._q("SELECT rf.sha256, rf.name, rf.role, f.size, f.media_type "
                                 "FROM release_file rf JOIN file f ON f.sha256=rf.sha256 "
                                 "WHERE rf.asset=? AND rf.version=? ORDER BY rf.role DESC",
                                 (asset_id, r["version"]))
            r["notes_map"] = _loads(r.get("notes_i18n"), {})
            r["channels"] = [p["channel"] for p in self._q(
                "SELECT channel FROM placement WHERE asset=? AND version=?",
                (asset_id, r["version"]))]
        return sorted(rs, key=lambda r: version_key(r["version"]), reverse=True)

    def release(self, asset_id, version):
        return next((r for r in self.releases(asset_id) if r["version"] == version), None)

    def current(self, asset_id, channel):
        """Das aktuelle Release eines Kanals: höchste FREIGEGEBENE (Review, §15.8.5),
        nicht zurückgerufene Version darin."""
        for r in self.releases(asset_id):
            if channel in r["channels"] and not r["yanked_at"] and counts(r):
                return r
        return None

    def withdrawn(self, asset_id, channel):
        return [r for r in self.releases(asset_id) if channel in r["channels"] and r["yanked_at"]]

    def releases_with_file(self, sha):
        return self._q("SELECT asset, version FROM release_file WHERE sha256=?", (sha,))

    def entitlements(self, asset_id=None):
        if asset_id:
            return self._q("SELECT * FROM entitlement WHERE asset=? ORDER BY id", (asset_id,))
        return self._q("SELECT * FROM entitlement ORDER BY asset, id")

    def publishers(self):
        return self._q("SELECT * FROM publisher ORDER BY asset_pattern, id")

    def recipients(self):
        return self._q("SELECT * FROM recipient ORDER BY last_poll DESC")

    def events(self, limit=50):
        return self._q("SELECT * FROM event ORDER BY id DESC LIMIT ?", (int(limit),))

    def file_row(self, sha):
        r = self._q("SELECT * FROM file WHERE sha256=?", (sha,))
        return r[0] if r else None

    # -- changing ---------------------------------------------------------------
    def check_channel(self, ch):
        if ch not in self.channels:
            raise Refused(400, f"Den Kanal „{ch}“ gibt es nicht (Kanäle: "
                               + ", ".join(self.channels) + ").")

    def create_asset(self, asset_id, typ, title, summary, description, by, lang="en"):
        """Neu: sichtbar für alle, beziehbar nur mit Berechtigung (D11)."""
        texts = merge_texts({}, {lang: {"title": title, "summary": summary,
                                        "description": description}})
        self._x([("INSERT INTO asset(id, type, title, summary, description, visibility, "
                  "free_channels, created_at, created_by, texts, landscapes) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (asset_id, typ, title, summary, description,
                   json.dumps({"mode": "anyone", "principals": []}), "[]",
                   time.time(), by, json.dumps(texts, ensure_ascii=False),
                   json.dumps(["oaap"] if typ in ("oaap-app", "oaap-profile") else [])))])
        self.event("asset", asset_id, principal=by, detail=f"type {typ}")
        self._reindex(self.asset(asset_id))

    def add_release(self, asset_id, version, sha, size, name, media_type, notes, by,
                    review="approved", notes_i18n=None):
        """(release, created). Gleiche Bytes: idempotent. Andere Bytes: 409.
        review='pending': zählt erst nach der Freigabe (§15.8.5)."""
        with self.lock:
            old = self.release(asset_id, version)
            if old:
                primary = next((f for f in old["files"] if f["role"] == "primary"), None)
                if primary and primary["sha256"] == sha:
                    return old, False
                raise Refused(409, f"{asset_id} {version} gibt es schon mit anderem Inhalt "
                                   "(Prüfsumme " + (primary["sha256"][:12] if primary else "?")
                                   + "). Eine Version wird nie überschrieben -- auch nach "
                                   "einem Rückruf nicht. Bitte die Version erhöhen.")
            now = time.time()
            self._x([
                ("INSERT OR IGNORE INTO file(sha256, size, media_type) VALUES (?,?,?)",
                 (sha, size, media_type)),
                ("INSERT INTO release(asset, version, notes, created_at, created_by, review, "
                 "notes_i18n) VALUES (?,?,?,?,?,?,?)",
                 (asset_id, version, (notes or "")[:4000], now, by, review,
                  json.dumps(clean_notes(notes_i18n), ensure_ascii=False))),
                ("INSERT INTO release_file(asset, version, sha256, name, role) "
                 "VALUES (?,?,?,?,?)", (asset_id, version, sha, name, "primary")),
            ])
            self.event("publish", asset_id, version, principal=by, detail=sha)
            return self.release(asset_id, version), True

    def place(self, asset_id, version, channel, by):
        self.check_channel(channel)
        r = self.release(asset_id, version)
        if not r:
            raise Refused(404, "Dieses Release gibt es nicht.")
        if r["yanked_at"]:
            raise Refused(409, "Ein zurückgerufenes Release wird in keinen Kanal gelegt.")
        if channel in r["channels"]:
            return False
        self._x([("INSERT INTO placement(asset, version, channel, placed_at, placed_by) "
                  "VALUES (?,?,?,?,?)", (asset_id, version, channel, time.time(), by))])
        self.event("place", asset_id, version, channel, by)
        return True

    def unplace(self, asset_id, version, channel, by):
        self._x([("DELETE FROM placement WHERE asset=? AND version=? AND channel=?",
                  (asset_id, version, channel))])
        self.event("unplace", asset_id, version, channel, by)

    def yank(self, asset_id, version, reason, by):
        reason = (reason or "").strip()
        if not reason:
            raise Refused(400, "Ein Rückruf verlangt einen Grund.")
        r = self.release(asset_id, version)
        if not r:
            raise Refused(404, "Dieses Release gibt es nicht.")
        if r["yanked_at"]:
            return False
        self._x([("UPDATE release SET yanked_at=?, yanked_by=?, yank_reason=? "
                  "WHERE asset=? AND version=?",
                  (time.time(), by, reason[:500], asset_id, version))])
        self.event("yank", asset_id, version, principal=by, detail=reason)
        return True

    def update_asset(self, asset_id, fields, by):
        allowed = {"visibility", "free_channels", "tags", "categories", "landscapes", "links",
                   "license", "publisher", "texts", "featured"}
        fields = dict(fields)
        lang = str(fields.pop("lang", "en"))
        if not LANG_RE.match(lang):
            raise Refused(400, "lang ist ein Sprachkürzel wie en oder de.")
        # die alten Felder title/summary/description = Texte der Sprache `lang`
        legacy = {k: fields.pop(k) for k in TEXT_FIELDS if k in fields}
        if legacy:
            tx = dict(fields.get("texts") or {})
            tx[lang] = dict(tx.get(lang) or {}, **legacy)
            fields["texts"] = tx
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise Refused(400, f"„{k}“ ist kein Feld eines Assets.")
            if k == "texts":
                cur = self.asset(asset_id)
                merged = merge_texts(cur["texts_map"] if cur else {}, v)
                best = resolve(merged, "en")
                sets += ["title=?", "summary=?", "description=?"]
                args += [best.get("title", ""), best.get("summary", ""), best.get("description", "")]
                v = json.dumps(merged, ensure_ascii=False)
            elif k == "featured":
                v = 1 if v in (True, 1, "1", "on", "true") else 0
            elif k == "landscapes":
                if isinstance(v, str):
                    v = [x.strip() for x in v.split(",")]
                if not isinstance(v, list):
                    raise Refused(400, "landscapes ist eine Liste.")
                clean = sorted({str(x).strip().lower() for x in v if str(x).strip()})
                known = {r["id"] for r in self.landscapes()}
                unknown = [x for x in clean if x not in known]
                if unknown:
                    raise Refused(400, f"Die Landschaft „{unknown[0]}“ gibt es nicht "
                                       "(der Betreiber legt sie an).")
                v = json.dumps(clean)
            elif k == "visibility":
                if not isinstance(v, dict) or v.get("mode") not in ("anyone", "signed-in", "principals"):
                    raise Refused(400, "visibility.mode ist anyone, signed-in oder principals.")
                prs = [{"kind": x.get("kind"), "name": str(x.get("name") or "")}
                       for x in v.get("principals") or []
                       if isinstance(x, dict) and x.get("kind") in ("user", "group") and x.get("name")]
                v = json.dumps({"mode": v["mode"], "principals": prs})
            elif k == "free_channels":
                if not isinstance(v, list):
                    raise Refused(400, "free_channels ist eine Liste von Kanälen.")
                for ch in v:
                    self.check_channel(ch)
                v = json.dumps(sorted(set(v)))
            elif k in ("tags", "categories"):
                if isinstance(v, str):
                    v = [x.strip() for x in v.split(",")]
                if not isinstance(v, list):
                    raise Refused(400, f"{k} ist eine Liste.")
                clean = sorted({str(x).strip().lower() for x in v if str(x).strip()})
                bad = [x for x in clean if not SLUG_RE.match(x)]
                if bad:
                    raise Refused(400, f"{k}: nur Kleinbuchstaben, Ziffern, Bindestrich ({bad[0]}).")
                if k == "categories":
                    known = {r["id"] for r in self.categories()}
                    unknown = [x for x in clean if x not in known]
                    if unknown:
                        raise Refused(400, f"Die Kategorie „{unknown[0]}“ gibt es nicht "
                                           "(der Betreiber legt sie an).")
                v = json.dumps(clean[:20])
            elif k == "links":
                if not isinstance(v, list):
                    raise Refused(400, "links ist eine Liste von {label, url}.")
                out = []
                for x in v[:10]:
                    url = str((x or {}).get("url") or "").strip()
                    if not re.match(r"^https?://[^\s<>\"']+$", url):
                        raise Refused(400, f"Ein Link braucht eine http(s)-Adresse ({url[:60]}).")
                    out.append({"label": str(x.get("label") or url)[:80], "url": url[:500]})
                v = json.dumps(out)
            elif k in ("license", "publisher"):
                v = str(v).strip()[:120]
            else:
                v = str(v)[:8000]
            sets.append(f"{k}=?")
            args.append(v)
        if not sets:
            return
        self._x([(f"UPDATE asset SET {', '.join(sets)} WHERE id=?", (*args, asset_id))])
        self.event("asset-update", asset_id, principal=by, detail=",".join(sorted(fields)))
        self._reindex(self.asset(asset_id))

    def grant(self, kind, name, asset_id, channels, by, expires_at=None, note=""):
        if kind not in ("user", "group") or not name:
            raise Refused(400, "Eine Berechtigung gilt einem Benutzer oder einer Gruppe.")
        if not self.asset(asset_id):
            raise Refused(404, "Dieses Asset gibt es nicht.")
        chans = channels or ["*"]
        for ch in chans:
            if ch != "*":
                self.check_channel(ch)
        self._x([("INSERT INTO entitlement(principal_kind, principal, asset, channels, "
                  "granted_by, granted_at, expires_at, note) VALUES (?,?,?,?,?,?,?,?)",
                  (kind, name, asset_id, json.dumps(chans), by, time.time(),
                   expires_at, (note or "")[:300]))])
        self.event("grant", asset_id, principal=by, detail=f"{kind}:{name} {','.join(chans)}")

    def revoke(self, ent_id, by):
        r = self._q("SELECT * FROM entitlement WHERE id=?", (int(ent_id),))
        if not r:
            raise Refused(404, "Diese Berechtigung gibt es nicht.")
        self._x([("DELETE FROM entitlement WHERE id=?", (int(ent_id),))])
        self.event("revoke", r[0]["asset"], principal=by,
                   detail=f"{r[0]['principal_kind']}:{r[0]['principal']}")

    def add_publisher(self, kind, name, pattern, channels, by):
        if kind not in ("user", "group") or not name or not pattern:
            raise Refused(400, "Ein Recht zum Veröffentlichen nennt Benutzer oder Gruppe "
                               "und ein Muster wie hbvp oder oaap-*.")
        if not re.match(r"^[a-z0-9*][a-z0-9*-]{0,40}$", pattern):
            raise Refused(400, "Muster: Kleinbuchstaben, Ziffern, Bindestrich und *.")
        chans = channels or ["*"]
        for ch in chans:
            if ch != "*":
                self.check_channel(ch)
        self._x([("INSERT INTO publisher(principal_kind, principal, asset_pattern, channels) "
                  "VALUES (?,?,?,?)", (kind, name, pattern, json.dumps(chans)))])
        self.event("publisher", pattern, principal=by, detail=f"{kind}:{name} {','.join(chans)}")

    def remove_publisher(self, pub_id, by):
        self._x([("DELETE FROM publisher WHERE id=?", (int(pub_id),))])
        self.event("publisher-remove", principal=by, detail=str(pub_id))

    def note_poll(self, principal, channel, offered):
        self._x([("INSERT INTO recipient(principal, channel, last_poll, offered) VALUES (?,?,?,?) "
                  "ON CONFLICT(principal, channel) DO UPDATE SET last_poll=excluded.last_poll, "
                  "offered=excluded.offered", (principal, channel, time.time(), offered[:2000]))])

    # -- Stufe 2: Bilder ----------------------------------------------------------
    def add_image(self, asset_id, sha, media, size, role, by):
        if role not in ("icon", "screenshot"):
            raise Refused(400, "role ist icon oder screenshot.")
        a = self.asset(asset_id)
        if not a:
            raise Refused(404, "Dieses Asset gibt es nicht.")
        shots = a["screenshots_list"]
        if role == "screenshot" and sha not in shots and len(shots) >= 8:
            raise Refused(400, "Höchstens 8 Screenshots je Asset.")
        stmts = [("INSERT OR REPLACE INTO image(asset, sha256, media_type, size, role, added_at, "
                  "added_by) VALUES (?,?,?,?,?,?,?)",
                  (asset_id, sha, media, size, role, time.time(), by))]
        if role == "icon":
            stmts.append(("UPDATE asset SET icon=? WHERE id=?", (sha, asset_id)))
        elif sha not in shots:
            stmts.append(("UPDATE asset SET screenshots=? WHERE id=?",
                          (json.dumps(shots + [sha]), asset_id)))
        self._x(stmts)
        self.event("image", asset_id, principal=by, detail=f"{role} {sha[:12]}")

    def remove_image(self, asset_id, sha, by):
        a = self.asset(asset_id)
        if not a or not self.image_row(asset_id, sha):
            raise Refused(404, "Dieses Bild gibt es nicht.")
        stmts = [("DELETE FROM image WHERE asset=? AND sha256=?", (asset_id, sha))]
        if a.get("icon") == sha:
            stmts.append(("UPDATE asset SET icon='' WHERE id=?", (asset_id,)))
        if sha in a["screenshots_list"]:
            stmts.append(("UPDATE asset SET screenshots=? WHERE id=?",
                          (json.dumps([x for x in a["screenshots_list"] if x != sha]), asset_id)))
        self._x(stmts)
        self.event("image-remove", asset_id, principal=by, detail=sha[:12])

    def blob_unreferenced(self, sha):
        return not self._q("SELECT 1 FROM release_file WHERE sha256=? UNION ALL "
                           "SELECT 1 FROM image WHERE sha256=?", (sha, sha))

    # -- Stufe 2: Freischalt-Anfragen ---------------------------------------------
    def request_access(self, asset_id, principal, channels, note):
        """Eine offene Anfrage je Person und Asset -- eine zweite ergänzt die erste."""
        chans = channels or ["*"]
        for ch in chans:
            if ch != "*":
                self.check_channel(ch)
        open_ = self.requests("open", asset_id, principal)
        if open_:
            self._x([("UPDATE access_request SET channels=?, note=?, at=? WHERE id=?",
                      (json.dumps(chans), (note or "")[:500], time.time(), open_[0]["id"]))])
            return open_[0]["id"], False
        self._x([("INSERT INTO access_request(asset, principal, channels, note, at, state) "
                  "VALUES (?,?,?,?,?,'open')",
                  (asset_id, principal, json.dumps(chans), (note or "")[:500], time.time()))])
        self.event("request", asset_id, principal=principal, detail=",".join(chans))
        return self.requests("open", asset_id, principal)[0]["id"], True

    def decide_request(self, req_id, grant, by, channels=None, answer=""):
        r = self._q("SELECT * FROM access_request WHERE id=?", (int(req_id),))
        if not r or r[0]["state"] != "open":
            raise Refused(404, "Diese offene Anfrage gibt es nicht.")
        r = r[0]
        if grant:
            chans = channels or json.loads(r["channels"] or '["*"]')
            self.grant("user", r["principal"], r["asset"], chans, by,
                       note=f"Anfrage #{r['id']}")
        elif not (answer or "").strip():
            raise Refused(400, "Eine Ablehnung braucht eine Antwort für die Person.")
        self._x([("UPDATE access_request SET state=?, decided_by=?, decided_at=?, answer=? "
                  "WHERE id=?", ("granted" if grant else "rejected", by, time.time(),
                                 (answer or "")[:500], r["id"]))])
        self.event("request-" + ("grant" if grant else "reject"), r["asset"], principal=by,
                   detail=r["principal"])

    # -- §15.8.1: Landschaften, Kategorien ----------------------------------------
    def landscapes(self):
        return [_dec_tx(r) for r in self._q("SELECT * FROM landscape ORDER BY sort, id")]

    def categories(self, landscape=None):
        """Alle Kategorien; mit landscape: die dieser Landschaft und die übergreifenden."""
        rs = [_dec_tx(r) for r in self._q("SELECT * FROM category ORDER BY sort, id")]
        if landscape:
            rs = [r for r in rs if r["landscape"] in (landscape, DEFAULT_LANDSCAPE)]
        return rs

    def set_landscape(self, lid, texts, sort=100, by=""):
        if not SLUG_RE.match(lid or "") or lid == DEFAULT_LANDSCAPE:
            raise Refused(400, "Kennung: Kleinbuchstaben, Ziffern, Bindestrich (nicht „default“).")
        old = next((r for r in self.landscapes() if r["id"] == lid), None)
        merged = merge_texts(old["texts_map"] if old else {}, texts or {})
        if not resolve(merged, "en").get("title"):
            raise Refused(400, "Eine Landschaft braucht einen Titel.")
        self._x([("INSERT INTO landscape(id, sort, texts) VALUES (?,?,?) ON CONFLICT(id) DO "
                  "UPDATE SET sort=excluded.sort, texts=excluded.texts",
                  (lid, int(sort), json.dumps(merged, ensure_ascii=False)))])
        self.event("landscape", lid, principal=by)

    def delete_landscape(self, lid, by):
        row = next((r for r in self.landscapes() if r["id"] == lid), None)
        if not row:
            raise Refused(404, "Diese Landschaft gibt es nicht.")
        if row["fixed"]:
            raise Refused(409, f"„{lid}“ ist fest und bleibt.")
        if any(lid in a["landscapes_list"] for a in self.assets()):
            raise Refused(409, f"„{lid}“ ist noch Assets zugeordnet.")
        if any(c["landscape"] == lid for c in self.categories()):
            raise Refused(409, f"„{lid}“ hat noch Kategorien.")
        self._x([("DELETE FROM landscape WHERE id=?", (lid,))])
        self.event("landscape-remove", lid, principal=by)

    def set_category(self, cid, landscape, texts, sort=100, by=""):
        if not SLUG_RE.match(cid or ""):
            raise Refused(400, "Kennung: Kleinbuchstaben, Ziffern, Bindestrich.")
        if landscape != DEFAULT_LANDSCAPE and landscape not in {r["id"] for r in self.landscapes()}:
            raise Refused(400, f"Die Landschaft „{landscape}“ gibt es nicht.")
        old = next((r for r in self.categories() if r["id"] == cid), None)
        merged = merge_texts(old["texts_map"] if old else {}, texts or {})
        if not resolve(merged, "en").get("title"):
            raise Refused(400, "Eine Kategorie braucht einen Titel.")
        self._x([("INSERT INTO category(id, landscape, sort, texts) VALUES (?,?,?,?) ON CONFLICT(id) "
                  "DO UPDATE SET landscape=excluded.landscape, sort=excluded.sort, texts=excluded.texts",
                  (cid, landscape, int(sort), json.dumps(merged, ensure_ascii=False)))])
        self.event("category", cid, principal=by)

    def delete_category(self, cid, by):
        if any(cid in a["categories_list"] for a in self.assets()):
            raise Refused(409, f"„{cid}“ ist noch Assets zugeordnet.")
        self._x([("DELETE FROM category WHERE id=?", (cid,))])
        self.event("category-remove", cid, principal=by)

    def set_logo(self, table, rid, sha, by):
        if table not in ("landscape", "category"):
            raise Refused(400, "unbekannt")
        self._x([(f"UPDATE {table} SET logo=? WHERE id=?", (sha, rid))])
        self.event(table + "-logo", rid, principal=by, detail=(sha or "")[:12])

    def is_logo(self, sha):
        """Ist sha ein Logo (Landschaft, Kategorie, Betreiber)? Logos sind öffentlich."""
        if not sha:
            return False
        return bool(self._q("SELECT 1 FROM landscape WHERE logo=? UNION ALL SELECT 1 FROM category "
                            "WHERE logo=? UNION ALL SELECT 1 FROM setting WHERE key='logo' AND value=?",
                            (sha, sha, sha)))

    # -- §15.8.6: Einstellungen ----------------------------------------------------
    def settings(self):
        return {r["key"]: r["value"] for r in self._q("SELECT key, value FROM setting")}

    def set_setting(self, key, value, by):
        self._x([("INSERT INTO setting(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                  "value=excluded.value", (key, str(value)[:500]))])
        self.event("setting", key, principal=by)

    # -- §15.8.5: Review --------------------------------------------------------------
    def reviewers(self):
        return self._q("SELECT * FROM reviewer ORDER BY id")

    def add_reviewer(self, kind, name, by):
        if kind not in ("user", "group") or not name:
            raise Refused(400, "Ein Prüfer ist ein Benutzer oder eine Gruppe.")
        self._x([("INSERT INTO reviewer(principal_kind, principal) VALUES (?,?)", (kind, name))])
        self.event("reviewer", principal=by, detail=f"{kind}:{name}")

    def remove_reviewer(self, rid, by):
        self._x([("DELETE FROM reviewer WHERE id=?", (int(rid),))])
        self.event("reviewer-remove", principal=by, detail=str(rid))

    def open_review(self, kind, asset_id, version, by, precheck=None, detail=""):
        """Ein Fall je Release; ein offener Listing-Fall je Asset wird ergänzt."""
        with self.lock:
            if kind == "listing":
                cur = self._q("SELECT id, detail FROM review WHERE kind='listing' AND asset=? "
                              "AND state='open'", (asset_id,))
                if cur:
                    merged = sorted(set(filter(None, (cur[0]["detail"] or "").split(",")))
                                    | set(filter(None, detail.split(","))))
                    self._x([("UPDATE review SET detail=?, created_by=? WHERE id=?",
                              (",".join(merged)[:300], by, cur[0]["id"]))])
                    return cur[0]["id"]
            self._x([("INSERT INTO review(kind, asset, version, created_at, created_by, state, "
                      "precheck, detail) VALUES (?,?,?,?,?,'open',?,?)",
                      (kind, asset_id, version or "", time.time(), by,
                       json.dumps(precheck or {}, ensure_ascii=False), detail[:300]))])
            rid = self._q("SELECT max(id) AS m FROM review")[0]["m"]
        self.event("review-open", asset_id, version or "", principal=by, detail=f"{kind} #{rid}")
        return rid

    def reviews(self, state=None, asset_id=None, escalate_after_h=24, now=None):
        """Fälle. Ein offener Fall, älter als escalate_after_h, gilt als eskaliert
        (beim Lesen berechnet, kein Zeitgeber). state filtert auf den WIRKSAMEN Zustand."""
        now = now or time.time()
        sql, args = "SELECT * FROM review WHERE 1=1", []
        if asset_id:
            sql += " AND asset=?"
            args.append(asset_id)
        out = []
        for r in self._q(sql + " ORDER BY id DESC", args):
            r["findings_list"] = _loads(r.get("findings"), [])
            r["precheck_map"] = _loads(r.get("precheck"), {})
            r["overdue"] = (r["state"] == "open"
                            and r["created_at"] is not None
                            and now - float(r["created_at"]) > escalate_after_h * 3600)
            r["effective"] = "escalated" if r["overdue"] else r["state"]
            if state and r["effective"] != state:
                continue
            out.append(r)
        return out

    def review(self, rid):
        r = self._q("SELECT id FROM review WHERE id=?", (int(rid),))
        if not r:
            return None
        return next((x for x in self.reviews() if x["id"] == int(rid)), None)

    def decide_review(self, rid, verdict, by, as_="reviewer", summary="", findings=None):
        """approve | reject | escalate. Ein Listing-Fall, den der Prüfer ablehnt,
        geht an den Menschen (die Änderung ist ja schon sichtbar)."""
        r = self.review(rid)
        if not r or r["state"] not in ("open", "escalated"):
            raise Refused(404, "Diesen offenen Fall gibt es nicht.")
        if verdict not in ("approve", "reject", "escalate"):
            raise Refused(400, "verdict ist approve, reject oder escalate.")
        if as_ == "reviewer" and r["effective"] == "escalated":
            raise Refused(409, "Der Fall ist eskaliert; jetzt entscheidet ein Mensch (Betreiber).")
        if as_ == "admin" and verdict == "escalate":
            raise Refused(400, "Der Betreiber entscheidet: approve oder reject.")
        clean = []
        for f in (findings or [])[:50]:
            if isinstance(f, dict):
                sev = f.get("severity") if f.get("severity") in ("info", "warn", "block") else "info"
                clean.append({"severity": sev, "text": str(f.get("text") or "")[:500]})
        state = {"approve": "approved", "reject": "rejected", "escalate": "escalated"}[verdict]
        if r["kind"] == "listing" and verdict == "reject" and as_ == "reviewer":
            state = "escalated"
        stmts = [("UPDATE review SET state=?, decided_by=?, decided_at=?, decided_as=?, summary=?, "
                  "findings=? WHERE id=?", (state, by, time.time(), as_, (summary or "")[:2000],
                                           json.dumps(clean, ensure_ascii=False), r["id"]))]
        if r["kind"] == "release":
            stmts.append(("UPDATE release SET review=? WHERE asset=? AND version=?",
                          ("pending" if state == "escalated" else state, r["asset"], r["version"])))
        self._x(stmts)
        self.event("review-" + verdict, r["asset"], r["version"], principal=by,
                   detail=f"#{r['id']} als {as_}")
        return state

    # -- §15.8.6: Zahlen für die Startseite -----------------------------------------
    def download_counts(self, days=90):
        """Downloads je Asset (ohne Feed-Abrufe der Kataloge: die heißen fetch)."""
        since = time.time() - days * 86400
        return {r["asset"]: r["n"] for r in self._q(
            "SELECT asset, count(*) AS n FROM event WHERE kind='download' AND at>=? GROUP BY asset",
            (since,))}


def counts(release):
    """Zählt ein Release für Kanal, Feed und Download? Nur freigegeben (§15.8.5)."""
    return (release.get("review") or "approved") == "approved"


def _loads(s, default):
    try:
        v = json.loads(s or "")
    except (ValueError, TypeError):
        return default
    return v if isinstance(v, type(default)) else default


def _dec_tx(r):
    r["texts_map"] = _loads(r.get("texts"), {})
    return r


def resolve(texts, lang, default="en"):
    """Der Text in `lang`, sonst `default`, sonst die erste vorhandene Sprache.
    -> dict mit den Feldern und `_lang` (welche Sprache es wurde)."""
    texts = texts or {}

    def has(code):
        return any(str((texts.get(code) or {}).get(k) or "").strip() for k in TEXT_FIELDS)
    for code in (lang, default, *sorted(texts)):
        if code in texts and has(code):
            return dict(texts[code], _lang=code)
    return {"_lang": lang}


def merge_texts(cur, new):
    """new = {lang: {feld: text}}. Nur genannte Felder ändern sich; ein leerer Text
    löscht das Feld; eine Sprache ohne Felder verschwindet."""
    if not isinstance(new, dict):
        raise Refused(400, "texts ist ein Objekt {sprache: {title, summary, description}}.")
    out = {code: dict(v) for code, v in (cur or {}).items() if isinstance(v, dict)}
    for lang, fields in new.items():
        if not LANG_RE.match(str(lang)):
            raise Refused(400, f"„{lang}“ ist kein Sprachkürzel wie en oder de.")
        if not isinstance(fields, dict):
            raise Refused(400, f"texts.{lang} ist ein Objekt.")
        tgt = out.setdefault(lang, {})
        for k, v in fields.items():
            if k not in TEXT_FIELDS:
                raise Refused(400, f"texts.{lang}.{k}: erlaubt sind title, summary, description.")
            v = str(v or "")
            if v.strip():
                tgt[k] = v[:TEXT_MAX[k]] if k == "description" else v.strip()[:TEXT_MAX[k]]
            else:
                tgt.pop(k, None)
        if not tgt:
            out.pop(lang)
    return out


def clean_notes(notes):
    if not notes:
        return {}
    if not isinstance(notes, dict):
        raise Refused(400, "notes ist ein Objekt {sprache: text}.")
    out = {}
    for lang, v in notes.items():
        if not LANG_RE.match(str(lang)):
            raise Refused(400, f"„{lang}“ ist kein Sprachkürzel.")
        if str(v or "").strip():
            out[lang] = str(v)[:4000]
    return out
