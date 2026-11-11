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
            con.commit()

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
    def assets(self):
        return self._q("SELECT * FROM asset ORDER BY id")

    def asset(self, asset_id):
        r = self._q("SELECT * FROM asset WHERE id=?", (asset_id,))
        return r[0] if r else None

    def releases(self, asset_id):
        rs = self._q("SELECT * FROM release WHERE asset=?", (asset_id,))
        for r in rs:
            r["files"] = self._q("SELECT rf.sha256, rf.name, rf.role, f.size, f.media_type "
                                 "FROM release_file rf JOIN file f ON f.sha256=rf.sha256 "
                                 "WHERE rf.asset=? AND rf.version=? ORDER BY rf.role DESC",
                                 (asset_id, r["version"]))
            r["channels"] = [p["channel"] for p in self._q(
                "SELECT channel FROM placement WHERE asset=? AND version=?",
                (asset_id, r["version"]))]
        return sorted(rs, key=lambda r: version_key(r["version"]), reverse=True)

    def release(self, asset_id, version):
        return next((r for r in self.releases(asset_id) if r["version"] == version), None)

    def current(self, asset_id, channel):
        """Das aktuelle Release eines Kanals: höchste nicht zurückgerufene Version darin."""
        for r in self.releases(asset_id):
            if channel in r["channels"] and not r["yanked_at"]:
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

    def create_asset(self, asset_id, typ, title, summary, description, by):
        """Neu: sichtbar für alle, beziehbar nur mit Berechtigung (D11)."""
        self._x([("INSERT INTO asset(id, type, title, summary, description, visibility, "
                  "free_channels, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                  (asset_id, typ, title, summary, description,
                   json.dumps({"mode": "anyone", "principals": []}), "[]",
                   time.time(), by))])
        self.event("asset", asset_id, principal=by, detail=f"type {typ}")

    def add_release(self, asset_id, version, sha, size, name, media_type, notes, by):
        """(release, created). Gleiche Bytes: idempotent. Andere Bytes: 409."""
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
                ("INSERT INTO release(asset, version, notes, created_at, created_by) "
                 "VALUES (?,?,?,?,?)", (asset_id, version, (notes or "")[:4000], now, by)),
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
        allowed = {"title", "summary", "description", "visibility", "free_channels"}
        sets, args = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise Refused(400, f"„{k}“ ist kein Feld eines Assets.")
            if k == "visibility":
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
            else:
                v = str(v)[:8000]
            sets.append(f"{k}=?")
            args.append(v)
        if not sets:
            return
        self._x([(f"UPDATE asset SET {', '.join(sets)} WHERE id=?", (*args, asset_id))])
        self.event("asset-update", asset_id, principal=by, detail=",".join(sorted(fields)))

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
