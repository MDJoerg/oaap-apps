"""Wegweiser — Datenschicht: Areas, Links, Zugriffe in einer SQLite-Datei.

Drei Tabellen, ein Grundsatz: Was hier liegt, gehört dem Mandanten der
Instanz. Links hängen an der unveränderlichen Benutzer-UUID (RFC-0040),
nie am Benutzernamen; der Name wird nur zur Anzeige mitgeführt.

Die Speichertiefe der Zugriffe ist eine Eigenschaft der Area
(`log_level`): `count` schreibt nur den Zähler am Link, `minimal` den
Zeitpunkt und das Ergebnis, `standard` dazu die gekürzte Adresse und
Geräteklassen, `full` alles, was der Client mitschickt. Wer eine Area
verantwortet, verantwortet auch das.
"""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

TYPES = ("redirect", "pixel", "download_once", "download_window", "upload_once")
TYPE_LABELS = {
    "redirect": "Weiterleitung",
    "pixel": "Zählpixel",
    "download_once": "Einmal-Download",
    "download_window": "Zeitraum-Download",
    "upload_once": "Einmal-Upload",
}
LOG_LEVELS = ("count", "minimal", "standard", "full")
LOG_LABELS = {
    "count": "nur Zähler",
    "minimal": "Zeitpunkt und Ergebnis",
    "standard": "dazu gekürzte Adresse, Gerät, Browser, Sprache",
    "full": "alles: volle Adresse, User-Agent, Referer, Sprache",
}
RESULTS = ("redirected", "pixel", "landing", "downloaded", "uploaded",
           "pin_wrong", "pin_locked", "expired", "exhausted", "disabled", "not_found")

# Pfadsegmente, die die App selbst braucht — als Area-Slug verboten.
RESERVED_SLUGS = {"manage", "admin", "auth", "healthz", "static", "api", "platform"}

# Erzeugte Keys: ohne 0/O/o und 1/l/I, damit sie sich vorlesen lassen.
KEY_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
KEY_MAX = 8

PIN_FAILS_BEFORE_LOCK = 5
PIN_LOCK_MINUTES = 15

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
  slug TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  responsible TEXT NOT NULL DEFAULT '[]',
  open_for_users INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  key_length INTEGER NOT NULL DEFAULT 6,
  custom_keys INTEGER NOT NULL DEFAULT 1,
  default_ttl_days INTEGER NOT NULL DEFAULT 0,
  max_ttl_days INTEGER NOT NULL DEFAULT 0,
  pin_required INTEGER NOT NULL DEFAULT 0,
  pin_min_length INTEGER NOT NULL DEFAULT 4,
  max_file_mb INTEGER NOT NULL DEFAULT 100,
  allowed_extensions TEXT NOT NULL DEFAULT '',
  log_level TEXT NOT NULL DEFAULT 'standard',
  log_retention_days INTEGER NOT NULL DEFAULT 90,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
  id TEXT PRIMARY KEY,
  area TEXT NOT NULL,
  key TEXT NOT NULL,
  key_norm TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_by_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  expires_at TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  deleted_at TEXT,
  pin_hash TEXT,
  pin_failures INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  lock_count INTEGER NOT NULL DEFAULT 0,
  target_url TEXT,
  file_name TEXT,
  file_size INTEGER,
  file_sha256 TEXT,
  content_type TEXT,
  valid_from TEXT,
  valid_until TEXT,
  max_downloads INTEGER,
  download_count INTEGER NOT NULL DEFAULT 0,
  state TEXT,
  received_at TEXT,
  hit_count INTEGER NOT NULL DEFAULT 0,
  last_hit_at TEXT,
  UNIQUE(area, key_norm)
);
CREATE INDEX IF NOT EXISTS links_owner ON links(created_by, created_at);
CREATE TABLE IF NOT EXISTS accesses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  area TEXT NOT NULL,
  link_id TEXT,
  result TEXT NOT NULL,
  ip TEXT,
  ua TEXT,
  ua_class TEXT,
  browser TEXT,
  os TEXT,
  referer TEXT,
  lang TEXT
);
CREATE INDEX IF NOT EXISTS accesses_link ON accesses(link_id, ts);
CREATE INDEX IF NOT EXISTS accesses_area ON accesses(area, ts);
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL DEFAULT '',
  last_seen TEXT NOT NULL
);
"""

AREA_INT_FIELDS = ("open_for_users", "active", "key_length", "custom_keys",
                   "default_ttl_days", "max_ttl_days", "pin_required",
                   "pin_min_length", "max_file_mb", "log_retention_days")


# ------------------------------------------------------------------ Zeit

def now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def now_iso():
    return iso(now())


def parse_iso(text):
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------- Verbindung

class DB:
    """Eine Verbindung, ein Schloss. Der Server ist mehrfädig, SQLite nicht."""

    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def q(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def x(self, sql, params=()):
        with self.lock:
            cur = self.conn.execute(sql, params)
            return cur.rowcount


def connect(path):
    return DB(path)


# -------------------------------------------------------------- Benutzer

def touch_user(db, user_id, name, display_name):
    if not user_id:
        return
    db.x("INSERT INTO users(id,name,display_name,last_seen) VALUES(?,?,?,?) "
         "ON CONFLICT(id) DO UPDATE SET name=excluded.name, "
         "display_name=excluded.display_name, last_seen=excluded.last_seen",
         (user_id, name, display_name, now_iso()))


# ----------------------------------------------------------------- Areas

def area_row(row):
    if row is None:
        return None
    a = dict(row)
    try:
        a["responsible"] = json.loads(a["responsible"] or "[]")
    except ValueError:
        a["responsible"] = []
    for f in AREA_INT_FIELDS:
        a[f] = int(a[f])
    a["open_for_users"] = bool(a["open_for_users"])
    a["active"] = bool(a["active"])
    a["custom_keys"] = bool(a["custom_keys"])
    a["pin_required"] = bool(a["pin_required"])
    a["type_label"] = TYPE_LABELS.get(a["type"], a["type"])
    return a


def valid_slug(slug):
    if not slug or len(slug) > 16 or slug in RESERVED_SLUGS:
        return False
    return all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug) and slug[0] != "-"


def validate_area(fields, creating):
    """Gibt (bereinigte Felder, Fehlerliste) zurück."""
    out, errors = {}, []
    if creating:
        slug = (fields.get("slug") or "").strip().lower()
        if not valid_slug(slug):
            errors.append("slug: nur a-z, 0-9 und '-', höchstens 16 Zeichen; "
                          "reserviert sind " + ", ".join(sorted(RESERVED_SLUGS)))
        out["slug"] = slug
        t = fields.get("type") or ""
        if t not in TYPES:
            errors.append("type: einer von " + ", ".join(TYPES))
        out["type"] = t
    for name in ("title", "description", "allowed_extensions"):
        if name in fields:
            out[name] = str(fields[name] or "").strip()
    if "allowed_extensions" in out:
        exts = [e.strip().lower().lstrip(".") for e in out["allowed_extensions"].replace(";", ",").split(",")]
        out["allowed_extensions"] = ",".join(e for e in exts if e)
    if "responsible" in fields:
        raw = fields["responsible"]
        if isinstance(raw, str):
            raw = raw.replace(";", ",").replace("\n", ",").split(",")
        names = sorted({str(n).strip() for n in raw if str(n).strip()})
        out["responsible"] = json.dumps(names)
    for name in ("open_for_users", "active", "custom_keys", "pin_required"):
        if name in fields:
            out[name] = 1 if _truthy(fields[name]) else 0
    bounds = {"key_length": (4, KEY_MAX), "default_ttl_days": (0, 36500),
              "max_ttl_days": (0, 36500), "pin_min_length": (3, 12),
              "max_file_mb": (1, 100000), "log_retention_days": (1, 36500)}
    for name, (lo, hi) in bounds.items():
        if name in fields:
            try:
                v = int(fields[name])
            except (TypeError, ValueError):
                errors.append(f"{name}: ganze Zahl erwartet")
                continue
            if v < lo or v > hi:
                errors.append(f"{name}: zwischen {lo} und {hi}")
            out[name] = v
    if "log_level" in fields:
        if fields["log_level"] not in LOG_LEVELS:
            errors.append("log_level: einer von " + ", ".join(LOG_LEVELS))
        out["log_level"] = fields["log_level"]
    return out, errors


def _truthy(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "on", "yes", "ja")


def create_area(db, fields):
    fields = dict(fields)
    fields["created_at"] = now_iso()
    cols = ",".join(fields)
    marks = ",".join("?" for _ in fields)
    db.x(f"INSERT INTO areas({cols}) VALUES({marks})", tuple(fields.values()))
    return get_area(db, fields["slug"])


def update_area(db, slug, fields):
    if not fields:
        return get_area(db, slug)
    sets = ",".join(f"{k}=?" for k in fields)
    db.x(f"UPDATE areas SET {sets} WHERE slug=?", (*fields.values(), slug))
    return get_area(db, slug)


def delete_area(db, slug):
    db.x("DELETE FROM accesses WHERE area=?", (slug,))
    db.x("DELETE FROM links WHERE area=?", (slug,))
    return db.x("DELETE FROM areas WHERE slug=?", (slug,))


def get_area(db, slug):
    return area_row(db.one("SELECT * FROM areas WHERE slug=?", (slug,)))


def list_areas(db):
    return [area_row(r) for r in db.q("SELECT * FROM areas ORDER BY slug")]


def area_counts(db):
    rows = db.q("SELECT area, COUNT(*) AS n, SUM(hit_count) AS hits FROM links "
                "WHERE deleted_at IS NULL GROUP BY area")
    return {r["area"]: {"links": r["n"], "hits": r["hits"] or 0} for r in rows}


# ----------------------------------------------------------------- Links

def link_row(row):
    if row is None:
        return None
    link = dict(row)
    link["disabled"] = bool(link["disabled"])
    link["has_pin"] = bool(link.pop("pin_hash", None))
    return link


def new_key(db, area, length):
    for _ in range(50):
        key = "".join(secrets.choice(KEY_ALPHABET) for _ in range(length))
        if not db.one("SELECT 1 FROM links WHERE area=? AND key_norm=?", (area, key.lower())):
            return key
    raise RuntimeError("kein freier Key gefunden")


def valid_key(key):
    return bool(key) and len(key) <= KEY_MAX and key.isalnum() and key.isascii()


def pin_hash(secret, link_id, pin):
    salt = (secret + ":" + link_id).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, 60000).hex()


def create_link(db, secret, area, user_id, user_name, fields, key=None, pin=None):
    link_id = uuid.uuid4().hex
    key = key or new_key(db, area["slug"], area["key_length"])
    row = {
        "id": link_id, "area": area["slug"], "key": key, "key_norm": key.lower(),
        "created_by": user_id, "created_by_name": user_name, "created_at": now_iso(),
        "title": fields.get("title", ""), "note": fields.get("note", ""),
        "expires_at": fields.get("expires_at"),
        "pin_hash": pin_hash(secret, link_id, pin) if pin else None,
        "target_url": fields.get("target_url"),
        "valid_from": fields.get("valid_from"), "valid_until": fields.get("valid_until"),
        "max_downloads": fields.get("max_downloads"),
        "state": "waiting" if area["type"] == "upload_once" else None,
    }
    cols = ",".join(row)
    db.x(f"INSERT INTO links({cols}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    return get_link(db, link_id)


def get_link(db, link_id):
    return link_row(db.one("SELECT * FROM links WHERE id=?", (link_id,)))


def find_link(db, area, key):
    return link_row(db.one("SELECT * FROM links WHERE area=? AND key_norm=? AND deleted_at IS NULL",
                           (area, key.lower())))


def list_links(db, user_id=None, area=None, include_deleted=False):
    sql, params = "SELECT * FROM links WHERE 1=1", []
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    if user_id:
        sql += " AND created_by=?"
        params.append(user_id)
    if area:
        sql += " AND area=?"
        params.append(area)
    sql += " ORDER BY created_at DESC"
    return [link_row(r) for r in db.q(sql, params)]


def update_link(db, link_id, fields):
    if not fields:
        return get_link(db, link_id)
    sets = ",".join(f"{k}=?" for k in fields)
    db.x(f"UPDATE links SET {sets} WHERE id=?", (*fields.values(), link_id))
    return get_link(db, link_id)


def set_pin(db, secret, link_id, pin):
    db.x("UPDATE links SET pin_hash=?, pin_failures=0, locked_until=NULL, lock_count=0 WHERE id=?",
         (pin_hash(secret, link_id, pin) if pin else None, link_id))


def soft_delete_link(db, link_id):
    return db.x("UPDATE links SET deleted_at=?, file_name=NULL WHERE id=? AND deleted_at IS NULL",
                (now_iso(), link_id))


def set_file(db, link_id, name, size, sha256, content_type, received=False):
    fields = {"file_name": name, "file_size": size, "file_sha256": sha256,
              "content_type": content_type}
    if received:
        fields["state"] = "received"
        fields["received_at"] = now_iso()
    update_link(db, link_id, fields)


def clear_file(db, link_id):
    db.x("UPDATE links SET file_name=NULL, file_size=NULL, file_sha256=NULL, content_type=NULL "
         "WHERE id=?", (link_id,))


# ------------------------------------------------------------ PIN-Prüfung

def check_pin(db, secret, link, pin):
    """Ergebnis: 'ok', 'wrong' oder 'locked'. Zählt je Link, nicht je Adresse."""
    row = db.one("SELECT pin_hash, pin_failures, locked_until, lock_count FROM links WHERE id=?",
                 (link["id"],))
    if row is None or not row["pin_hash"]:
        return "ok"
    ts = now_iso()
    if row["locked_until"] and row["locked_until"] > ts:
        return "locked"
    if pin and hmac.compare_digest(row["pin_hash"], pin_hash(secret, link["id"], pin)):
        db.x("UPDATE links SET pin_failures=0 WHERE id=?", (link["id"],))
        return "ok"
    failures = int(row["pin_failures"]) + 1
    if failures >= PIN_FAILS_BEFORE_LOCK:
        count = int(row["lock_count"]) + 1
        minutes = PIN_LOCK_MINUTES * (2 ** (count - 1))
        until = iso(now() + timedelta(minutes=min(minutes, 60 * 24 * 7)))
        db.x("UPDATE links SET pin_failures=0, lock_count=?, locked_until=? WHERE id=?",
             (count, until, link["id"]))
        return "locked"
    db.x("UPDATE links SET pin_failures=? WHERE id=?", (failures, link["id"]))
    return "wrong"


# -------------------------------------------------------------- Verbrauch

def consume_download(db, link_id):
    """Zählt einen Download — atomar, damit zwei gleichzeitige Klicks einen
    Einmal-Link nicht zweimal ausliefern. True, wenn der Abruf erlaubt war."""
    n = db.x("UPDATE links SET download_count=download_count+1 WHERE id=? AND deleted_at IS NULL "
             "AND (max_downloads IS NULL OR download_count < max_downloads)", (link_id,))
    return n == 1


def consume_upload(db, link_id):
    n = db.x("UPDATE links SET state='receiving' WHERE id=? AND state='waiting' AND deleted_at IS NULL",
             (link_id,))
    return n == 1


def release_upload(db, link_id):
    db.x("UPDATE links SET state='waiting' WHERE id=? AND state='receiving'", (link_id,))


def exhausted(link):
    return link["max_downloads"] is not None and link["download_count"] >= link["max_downloads"]


# -------------------------------------------------------------- Zugriffe

def short_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return ""
    if ip.version == 4:
        return str(ipaddress.ip_network(f"{ip}/24", strict=False).network_address) + "/24"
    return str(ipaddress.ip_network(f"{ip}/48", strict=False).network_address) + "/48"


def classify_ua(ua):
    """Grobe Klassen, nichts Genaues: Bot / Mobil / Desktop und Familien."""
    u = (ua or "").lower()
    if not u:
        return "unknown", "", ""
    bots = ("bot", "crawler", "spider", "preview", "fetch", "curl", "wget", "python-requests",
            "python-urllib", "java/", "go-http-client", "okhttp", "libwww",
            "googleimageproxy", "slack", "whatsapp", "telegram", "discord", "facebookexternalhit",
            "skypeuripreview", "linkcheck", "monitor", "headless", "validator")
    if any(b in u for b in bots):
        klass = "bot"
    elif any(m in u for m in ("mobile", "android", "iphone", "ipad", "ipod")):
        klass = "mobile"
    else:
        klass = "desktop"
    if "edg/" in u or "edge/" in u:
        browser = "Edge"
    elif "opr/" in u or "opera" in u:
        browser = "Opera"
    elif "firefox/" in u or "fxios/" in u:
        browser = "Firefox"
    elif "chrome/" in u or "crios/" in u:
        browser = "Chrome"
    elif "safari/" in u:
        browser = "Safari"
    elif "outlook" in u or "thunderbird" in u:
        browser = "Mailprogramm"
    else:
        browser = "andere"
    if "windows" in u:
        os_name = "Windows"
    elif "android" in u:
        os_name = "Android"
    elif "iphone" in u or "ipad" in u or "ipod" in u:
        os_name = "iOS"
    elif "mac os" in u or "macintosh" in u:
        os_name = "macOS"
    elif "linux" in u or "x11" in u:
        os_name = "Linux"
    else:
        os_name = "andere"
    return klass, browser, os_name


def referer_host(ref):
    if not ref:
        return ""
    ref = ref.split("://", 1)[-1]
    return ref.split("/", 1)[0].split("?", 1)[0].lower()[:200]


def first_lang(header):
    if not header:
        return ""
    return header.split(",", 1)[0].split(";", 1)[0].strip().lower()[:16]


def record_access(db, area, link, result, client):
    """`client` ist ein dict mit ip, ua, referer, lang. Was davon bleibt,
    entscheidet die Stufe der Area."""
    ts = now_iso()
    if link is not None:
        db.x("UPDATE links SET hit_count=hit_count+1, last_hit_at=? WHERE id=?", (ts, link["id"]))
    level = area["log_level"] if area else "minimal"
    if level == "count":
        return
    row = {"ts": ts, "area": area["slug"] if area else "", "link_id": link["id"] if link else None,
           "result": result, "ip": None, "ua": None, "ua_class": None, "browser": None,
           "os": None, "referer": None, "lang": None}
    if level in ("standard", "full"):
        klass, browser, os_name = classify_ua(client.get("ua", ""))
        row.update(ua_class=klass, browser=browser, os=os_name,
                   referer=referer_host(client.get("referer", "")),
                   lang=first_lang(client.get("lang", "")),
                   ip=short_ip(client.get("ip", "")))
    if level == "full":
        row.update(ip=client.get("ip", "")[:64], ua=(client.get("ua", "") or "")[:512],
                   referer=(client.get("referer", "") or "")[:1024],
                   lang=(client.get("lang", "") or "")[:128])
    cols = ",".join(row)
    db.x(f"INSERT INTO accesses({cols}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))


def accesses(db, link_id=None, area=None, limit=500, offset=0):
    sql, params = "SELECT * FROM accesses WHERE 1=1", []
    if link_id:
        sql += " AND link_id=?"
        params.append(link_id)
    if area:
        sql += " AND area=?"
        params.append(area)
    sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
    params += [max(1, min(int(limit), 5000)), max(0, int(offset))]
    return [dict(r) for r in db.q(sql, params)]


def stats(db, link_id=None, area=None, days=30):
    where, params = "1=1", []
    if link_id:
        where += " AND link_id=?"
        params.append(link_id)
    if area:
        where += " AND area=?"
        params.append(area)
    since = iso(now() - timedelta(days=days))
    out = {"days": days, "total": 0, "by_result": {}, "by_day": [], "top_referers": [],
           "by_class": {}, "by_browser": {}, "by_os": {}, "by_lang": {}}
    for r in db.q(f"SELECT result, COUNT(*) n FROM accesses WHERE {where} GROUP BY result", params):
        out["by_result"][r["result"]] = r["n"]
        out["total"] += r["n"]
    for r in db.q(f"SELECT substr(ts,1,10) d, COUNT(*) n FROM accesses WHERE {where} AND ts>=? "
                  "GROUP BY d ORDER BY d", params + [since]):
        out["by_day"].append({"day": r["d"], "n": r["n"]})
    for r in db.q(f"SELECT referer, COUNT(*) n FROM accesses WHERE {where} AND referer<>'' "
                  "GROUP BY referer ORDER BY n DESC LIMIT 10", params):
        out["top_referers"].append({"referer": r["referer"], "n": r["n"]})
    for col in ("ua_class", "browser", "os", "lang"):
        key = {"ua_class": "by_class", "browser": "by_browser", "os": "by_os", "lang": "by_lang"}[col]
        for r in db.q(f"SELECT {col} v, COUNT(*) n FROM accesses WHERE {where} AND {col} IS NOT NULL "
                      f"AND {col}<>'' GROUP BY {col} ORDER BY n DESC LIMIT 12", params):
            out[key][r["v"]] = r["n"]
    return out


# ------------------------------------------------------------- Aufräumen

def sweep(db, files_dir, log=print):
    """Nächtlicher Lauf, auch von Hand aufrufbar: alte Zugriffszeilen weg,
    verfallene und verbrauchte Dateien weg. Löscht nie einen Link-Eintrag —
    die Statistik bleibt, nur die Bytes gehen."""
    ts = now_iso()
    removed_rows = 0
    for area in list_areas(db):
        cutoff = iso(now() - timedelta(days=area["log_retention_days"]))
        removed_rows += db.x("DELETE FROM accesses WHERE area=? AND ts<?", (area["slug"], cutoff))
    removed_files = 0
    rows = db.q("SELECT id, area, file_name, expires_at, valid_until, max_downloads, download_count, "
                "deleted_at, state FROM links WHERE file_name IS NOT NULL")
    for r in rows:
        gone = (r["deleted_at"] is not None
                or (r["expires_at"] and r["expires_at"] < ts)
                or (r["valid_until"] and r["valid_until"] < ts)
                or (r["max_downloads"] is not None and r["download_count"] >= r["max_downloads"]))
        if not gone:
            continue
        # Der Name bleibt am Eintrag stehen — die Verwaltung soll noch sehen,
        # WAS hier lag. Nur die Bytes gehen.
        if remove_file(files_dir, r["area"], r["id"]):
            removed_files += 1
    # Dateien gelöschter Links, deren Eintrag den Namen schon verloren hat
    for r in db.q("SELECT id, area FROM links WHERE deleted_at IS NOT NULL"):
        if remove_file(files_dir, r["area"], r["id"]):
            removed_files += 1
    log(f"sweep: {removed_rows} Zugriffszeilen und {removed_files} Dateien entfernt")
    return removed_rows, removed_files


def file_dir(files_dir, area, link_id):
    return os.path.join(files_dir, area, link_id)


def file_path(files_dir, link):
    if not link or not link.get("file_name"):
        return None
    return os.path.join(file_dir(files_dir, link["area"], link["id"]), "content")


def remove_file(files_dir, area, link_id):
    d = file_dir(files_dir, area, link_id)
    if not os.path.isdir(d):
        return False
    for name in os.listdir(d):
        try:
            os.remove(os.path.join(d, name))
        except OSError:
            pass
    try:
        os.rmdir(d)
    except OSError:
        pass
    return True


def write_stream(files_dir, area, link_id, reader, length, chunk=1024 * 256):
    """Schreibt genau `length` Bytes aus `reader` nach <dir>/content und
    liefert (sha256, size). Bricht ab, wenn der Strom früher endet."""
    d = file_dir(files_dir, area, link_id)
    os.makedirs(d, exist_ok=True)
    tmp = os.path.join(d, "content.part")
    h = hashlib.sha256()
    written = 0
    with open(tmp, "wb") as fh:
        while written < length:
            data = reader.read(min(chunk, length - written))
            if not data:
                break
            fh.write(data)
            h.update(data)
            written += len(data)
    if written != length:
        os.remove(tmp)
        raise IOError(f"unvollständig: {written} von {length} Bytes")
    os.replace(tmp, os.path.join(d, "content"))
    return h.hexdigest(), written


def safe_filename(name):
    name = os.path.basename((name or "").replace("\\", "/")).strip()
    name = "".join(c for c in name if c.isprintable() and c not in '<>:"|?*')
    return name[:200] or "datei"


def extension_allowed(area, name):
    allowed = [e for e in (area.get("allowed_extensions") or "").split(",") if e]
    if not allowed:
        return True
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in allowed
