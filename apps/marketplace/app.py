"""Marketplace 0.3 -- digitale Assets mit Kanälen, Sichtbarkeit und Berechtigungen
(RFC-0077 Stufen 1 und 2, abgeschlossen in §15.8).

Ein Asset hat unveränderliche Releases und Kanäle (stable, beta), die auf
Releases zeigen. Ob jemand ein Asset SIEHT und ob er es BEKOMMT, sind zwei
Fragen -- beide beantwortet `access.py`, an genau einer Stelle. Neu in 0.3:
Landschaften und Kategorien, Texte je Sprache, der Typ oaap-profile
(Aufbau-Vorlage), die Geheimnis-Sperre vor jedem Speichern (`secretguard.py`),
das Review mit vier Augen, die Detailseite als ObjectPage mit Teilen-Link und
QR-Code, Partner light.

Wege (Manifest):
  /, /assets/*, /d/*, /img/*, /logo/*, /static/*, /api/public/v1/*   öffentlich
  /my/*                         dieselben Seiten angemeldet; /my/manage = Partner light
  /api/v1/*                     Suche, Details, Veröffentlichen, Review (Sitzung oder API-Schlüssel)
  /feed/v1/*                    Empfänger, z. B. ein Paketkatalog im Spiegelmodus
  /admin/*                      Verwaltung (admin)

Die App führt nichts Hochgeladenes aus und entpackt nichts außer dem Manifest
eines OAAP-Pakets und den Textdateien, die die Sperre liest (im Speicher).
"""
import hashlib
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import access
import blobs
import db
import i18n
import images
import multipart
import profileinfo
import secretguard
import views
import zipinfo

VERSION = "0.3.0"
PORT = 8000
DATA_DIR = os.environ.get("MARKET_DATA_DIR", "/data")
NAME = (os.environ.get("MARKET_NAME") or "Marketplace").strip()[:60]
try:
    MAX_MB = max(1, min(1024, int(os.environ.get("MARKET_MAX_FILE_MB") or 256)))
except ValueError:
    MAX_MB = 256
MAX_BYTES = MAX_MB * 1024 * 1024
CHANNELS = [c.strip() for c in (os.environ.get("MARKET_CHANNELS") or "stable,beta").split(",")
            if c.strip()]
KNOWN_TYPES = {"oaap-app", "oaap-profile", "file"}
TYPES = {t.strip() for t in (os.environ.get("MARKET_TYPES") or "oaap-app,oaap-profile,file").split(",")
         if t.strip()} & KNOWN_TYPES
EXTENSIONS = {e.strip().lower().lstrip(".") for e in
              (os.environ.get("MARKET_FILE_EXTENSIONS") or "zip,pdf,png,jpg,txt,md,json").split(",")
              if e.strip()}
TEXT_EXT = {"txt", "md", "json"}
REVIEW = (os.environ.get("MARKET_REVIEW") or "off").strip().lower() in ("on", "1", "true", "yes")
try:
    REVIEW_HOURS = max(1, int(os.environ.get("MARKET_REVIEW_ESCALATE_HOURS") or 24))
except ValueError:
    REVIEW_HOURS = 24
DEFAULT_LANG = (os.environ.get("MARKET_DEFAULT_LANG") or "en").strip().lower()[:2] or "en"
MAX_FORM = 64 * 1024
PUBLIC_PREFIXES = ("/assets", "/d/", "/img/", "/logo/", "/api/public/", "/static/")
SAFE_INLINE = {"application/pdf", "image/png", "image/jpeg", "text/plain"}

M = None          # db.Market
B = None          # blobs.BlobStore


def is_public_path(path):
    """Öffentliche Wege tragen keine Identität -- die App liest dort keine."""
    return path == "/" or path == "/healthz" or path.startswith(PUBLIC_PREFIXES)


def leak(e):
    """Ein Fund der Sperre -> Ereignis (nur Ort und Regel) und 422."""
    M.event("gate-refused", detail=f"{e.where[:200]} | {e.rule}")
    return db.Refused(422, str(e))


# ---------------------------------------------------------------- views -----
def visible_assets(p, q="", chosen=None):
    """Filtern VOR dem Zählen und Ordnen (§5) -- dieselbe Suche wie die Seite."""
    return views.search(M, p, q, chosen)[0]


def asset_view(p, a, ents, detail=False, lang=None):
    st = access.state(p, a, ents, M.channels)
    per = {}
    for ch in M.channels:
        cur = M.current(a["id"], ch)
        if cur:
            per[ch] = {"version": cur["version"],
                       "obtainable": access.can_obtain(p, a, ch, ents)}
    t = db.resolve(a.get("texts_map") or {}, lang or DEFAULT_LANG)
    v = {"id": a["id"], "type": a["type"], "title": t.get("title") or a.get("title") or a["id"],
         "summary": t.get("summary") or "", "lang": t.get("_lang"), "state": st, "channels": per,
         "free_channels": sorted(access.free_channels(a)),
         "tags": a.get("tags_list", []), "categories": a.get("categories_list", []),
         "landscapes": a.get("landscapes_list", []), "featured": bool(a.get("featured")),
         "content": views.content_of(M, a),
         "publisher": a.get("publisher") or "", "license": a.get("license") or "",
         "links": a.get("links_list", []), "icon": a.get("icon") or "",
         "screenshots": a.get("screenshots_list", [])}
    if detail:
        v["description"] = t.get("description") or ""
        v["texts"] = a.get("texts_map") or {}
        manage = access.is_admin(p) or access.can_manage(p, a["id"], M.publishers())
        rels = []
        for r in M.releases(a["id"]):
            if (r["yanked_at"] or not db.counts(r)) and not manage:
                continue
            item = {"version": r["version"], "channels": r["channels"],
                    "notes": r.get("notes") or "", "notes_i18n": r["notes_map"],
                    "yanked": bool(r["yanked_at"]), "review": r.get("review") or "approved"}
            ok = (any(access.can_obtain(p, a, ch, ents) for ch in r["channels"])
                  and not r["yanked_at"] and db.counts(r))
            if ok:
                item["files"] = [{"sha256": f["sha256"], "name": f["name"],
                                  "size": f["size"]} for f in r["files"]]
            rels.append(item)
        v["releases"] = rels
    return v


def file_obtainable(p, sha):
    """Eine Datei darf laden, wer EIN freigegebenes, nicht zurückgerufenes,
    platziertes Release mit ihr auf einem Kanal beziehen darf."""
    for ref in M.releases_with_file(sha):
        a = M.asset(ref["asset"])
        r = M.release(ref["asset"], ref["version"])
        if not a or not r or r["yanked_at"] or not db.counts(r):
            continue
        ents = M.entitlements(a["id"])
        if any(access.can_obtain(p, a, ch, ents) for ch in r["channels"]):
            return a, r
    return None, None


def feed_doc(p, channel):
    ents_all = M.entitlements()
    apps, withdrawn = [], []
    for a in M.assets():
        if a["type"] != "oaap-app":
            continue
        ents = [e for e in ents_all if e["asset"] == a["id"]]
        if not access.can_obtain(p, a, channel, ents):
            continue
        cur = M.current(a["id"], channel)
        if cur:
            f = next((x for x in cur["files"] if x["role"] == "primary"), None)
            if f:
                t = db.resolve(a.get("texts_map") or {}, DEFAULT_LANG)
                apps.append({"id": a["id"], "name": t.get("title") or a.get("title") or a["id"],
                             "version": cur["version"],
                             "released": _date(cur["created_at"]),
                             "summary": (t.get("summary") or "")[:300],
                             "description": t.get("description") or "",
                             "package": {"url": "files/" + f["sha256"],
                                         "sha256": f["sha256"], "size": f["size"]}})
        for r in M.withdrawn(a["id"], channel):
            withdrawn.append({"id": a["id"], "version": r["version"],
                              "reason": r.get("yank_reason") or ""})
    return {"schema": "oaap.marketplace.feed/0.1", "name": NAME, "channel": channel,
            "apps": apps, "withdrawn": withdrawn}


def _date(ts):
    import datetime
    return datetime.datetime.fromtimestamp(float(ts or 0), datetime.timezone.utc).strftime("%Y-%m-%d")


def review_case_doc(r, base_url):
    """Was die Prüf-KI zu einem Fall bekommt: alles, um zu urteilen."""
    a = M.asset(r["asset"])
    doc = {"id": r["id"], "kind": r["kind"], "asset": r["asset"], "version": r["version"],
           "state": r["effective"], "created_by": r["created_by"], "created_at": r["created_at"],
           "precheck": r["precheck_map"], "detail": r.get("detail") or ""}
    if a:
        doc["type"] = a["type"]
        doc["texts"] = a.get("texts_map") or {}
        doc["tags"], doc["links"] = a["tags_list"], a["links_list"]
        doc["landscapes"], doc["categories"] = a["landscapes_list"], a["categories_list"]
        doc["license"], doc["publisher"] = a.get("license") or "", a.get("publisher") or ""
        doc["images"] = [{"role": i["role"], "media_type": i["media_type"],
                          "url": f"{base_url}/api/v1/reviews/{r['id']}/blob/{i['sha256']}"}
                         for i in M.images(a["id"])]
        rel = M.release(a["id"], r["version"]) if r["version"] else None
        if rel:
            doc["notes"] = rel.get("notes") or ""
            doc["notes_i18n"] = rel["notes_map"]
            doc["files"] = [{"name": f["name"], "size": f["size"], "sha256": f["sha256"],
                             "media_type": f.get("media_type") or "",
                             "url": f"{base_url}/api/v1/reviews/{r['id']}/blob/{f['sha256']}"}
                            for f in rel["files"]]
    return doc


# ---------------------------------------------------------------- handler ---
class Handler(BaseHTTPRequestHandler):
    server_version = f"marketplace/{VERSION}"

    def log_message(self, fmt, *args):   # keine Anfragezeilen: Pfade tragen Namen
        pass

    def send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; "
                         "script-src 'self'; form-action 'self'; frame-ancestors 'none'")
        if getattr(self, "set_lang", None):
            self.send_header("Set-Cookie", f"market_lang={self.set_lang}; Path=/; Max-Age=31536000; "
                                           "SameSite=Lax")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, status, doc, extra=None):
        self.send(status, json.dumps(doc, ensure_ascii=False, indent=1),
                  "application/json; charset=utf-8", extra)

    def ctx(self, p, base, path="/", query=""):
        settings_lang = M.settings().get("default_lang") or DEFAULT_LANG
        q = parse_qs(query)
        lang, set_cookie = i18n.choose((q.get("lang") or [""])[0], self.headers.get("Cookie"),
                                       self.headers.get("Accept-Language"), settings_lang)
        self.set_lang = lang if set_cookie else None
        return views.Ctx(M, NAME, p, base, lang, self.origin(), path, query)

    def origin(self):
        fixed = (os.environ.get("MARKET_PUBLIC_URL") or "").strip().rstrip("/")
        if fixed:
            return fixed
        host = (self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost").split(",")[0].strip()
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
        if not proto:
            proto = "http" if host.startswith(("127.", "localhost")) else "https"
        return f"{proto}://{host}"

    def not_found(self, api=False):
        if api:
            return self.json(404, {"error": "not found"})
        c = self.ctx(access.ANON, "")
        return self.send(404, views.page(c, c.t("not_found"), f"<h1>{c.t('not_found')}</h1>"))

    def who(self, path):
        return access.principal(self.headers, is_public_path(path))

    def cross_site(self):
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "same-site", "none"):
            return True
        origin = self.headers.get("Origin")
        return bool(origin) and urlparse(origin).netloc != self.headers.get("Host", "")

    def body_bytes(self, limit=MAX_FORM):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > limit:
            raise db.Refused(413, "Die Anfrage ist zu groß.")
        return self.rfile.read(n) if n else b""

    def form(self, limit=MAX_FORM):
        raw = self.body_bytes(limit).decode("utf-8", "replace")
        out = {}
        for k, v in parse_qs(raw, keep_blank_values=True).items():
            out[k] = v[0] if len(v) == 1 else v
        return out

    def send_file(self, sha, name, media_type):
        try:
            f = B.open(sha)
        except (OSError, ValueError):
            return self.not_found()
        with f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            ctype = media_type if media_type in SAFE_INLINE else "application/octet-stream"
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(size))
            safe = "".join(c for c in (name or sha) if c.isalnum() or c in "._-")[:120] or sha
            self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
            self.send_header("ETag", f'"{sha}"')
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            for block in iter(lambda: f.read(1 << 20), b""):
                self.wfile.write(block)

    def send_image(self, sha, media_type):
        try:
            f = B.open(sha)
        except (OSError, ValueError):
            return self.not_found()
        with f:
            data = f.read()
        return self.send(200, data, media_type,
                         {"Content-Disposition": "inline",
                          "Content-Security-Policy": "default-src 'none'; sandbox"})

    # -- GET ------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if path == "/healthz":
            return self.json(200, {"status": "ok", "version": VERSION})
        if path == "/static/market.js":
            return self.send(200, views.JS, "text/javascript; charset=utf-8")
        p = self.who(path)
        try:
            chosen = {k: one(k)[:40] for k, _l in views.FACETS}
            if path in ("/", "/my", "/my/"):
                base = "" if path == "/" else "/my"
                c = self.ctx(p, base, path, u.query)
                if base and p["anon"]:
                    return self.send(401, views.page(c, c.t("sign_in"), f"<p>{c.t('please_sign_in')}</p>"))
                if not one("q") and not any(chosen.values()) and not one("all"):
                    return self.send(200, views.render_home(c))
                return self.send(200, views.render_list(c, one("q")[:80], chosen))
            if path == "/my/manage":
                if p["anon"]:
                    return self.not_found()
                return self.send(200, views.render_manage(self.ctx(p, "/my", path, u.query), one("m")[:300]))
            for base in ("", "/my"):
                if path.startswith(base + "/assets/"):
                    aid = unquote(path[len(base + "/assets/"):]).strip("/")
                    a = M.asset(aid)
                    ents = M.entitlements(aid) if a else []
                    if not a or not access.can_see(p, a, ents):
                        return self.not_found()
                    c = self.ctx(p, base, path, u.query)
                    return self.send(200, views.render_detail(c, a, ents, one("m")[:300], not one("err")))
                if path.startswith(base + "/d/"):
                    return self.download(p, path[len(base + "/d/"):].strip("/"))
                if path.startswith(base + "/img/"):
                    return self.image(p, path[len(base + "/img/"):].strip("/"))
            if path.startswith("/logo/"):
                sha = path[len("/logo/"):].strip("/")
                if not blobs.SHA_RE.match(sha) or not M.is_logo(sha):
                    return self.not_found()
                with B.open(sha) as f:
                    mt = images.media_type(f.read(16)) or "application/octet-stream"
                return self.send_image(sha, mt)
            if path == "/admin" or path.startswith("/admin/"):
                if not access.is_admin(p):
                    return self.not_found()
                c = self.ctx(p, "/my", path, u.query)
                if path.startswith("/admin/assets/"):
                    a = M.asset(unquote(path[len("/admin/assets/"):]).strip("/"))
                    if not a:
                        return self.not_found()
                    lang = one("lang", "en")
                    lang = lang if db.LANG_RE.match(lang) else "en"
                    return self.send(200, views.render_asset_edit(c, a, one("m")[:300], lang))
                if path == "/admin/reviews":
                    return self.send(200, views.render_reviews(c, one("m")[:300], REVIEW, REVIEW_HOURS))
                if path == "/admin/taxonomy":
                    return self.send(200, views.render_taxonomy(c, one("m")[:300]))
                if path == "/admin/settings":
                    return self.send(200, views.render_settings(c, one("m")[:300]))
                return self.send(200, views.render_admin(c, one("m")[:300], REVIEW, REVIEW_HOURS))
            lang = one("lang") if db.LANG_RE.match(one("lang")) else DEFAULT_LANG
            if path in ("/api/public/v1/assets", "/api/v1/assets"):
                items = [asset_view(p, a, e, lang=lang) for a, e in visible_assets(p, one("q")[:80], chosen)]
                return self.json(200, {"count": len(items), "assets": items})
            if path in ("/api/public/v1/taxonomy", "/api/v1/taxonomy"):
                return self.json(200, {
                    "landscapes": [{"id": x["id"], "texts": x["texts_map"], "fixed": bool(x["fixed"])}
                                   for x in M.landscapes()],
                    "categories": [{"id": x["id"], "landscape": x["landscape"], "texts": x["texts_map"]}
                                   for x in M.categories()]})
            for pre in ("/api/public/v1/assets/", "/api/v1/assets/"):
                if path.startswith(pre):
                    aid = unquote(path[len(pre):]).strip("/")
                    a = M.asset(aid)
                    ents = M.entitlements(aid) if a else []
                    if not a or not access.can_see(p, a, ents):
                        return self.not_found(api=True)
                    return self.json(200, asset_view(p, a, ents, detail=True, lang=lang))
            if path == "/api/v1/reviews":
                return self.reviews_list(p, one("state", "open"))
            if path.startswith("/api/v1/reviews/"):
                return self.review_get(p, path[len("/api/v1/reviews/"):].strip("/").split("/"))
            if path == "/feed/v1/oaap-apps":
                return self.feed(p, one("channel", "stable"))
            if path.startswith("/feed/v1/files/"):
                return self.download(p, path[len("/feed/v1/files/"):].strip("/"), api=True)
        except db.Refused as e:
            return self.json(e.status, {"error": str(e)})
        return self.not_found(api=path.startswith(("/api/", "/feed/")))

    def download(self, p, sha, api=False):
        if not blobs.SHA_RE.match(sha or ""):
            return self.not_found(api)
        a, r = file_obtainable(p, sha)
        if not a:
            return self.not_found(api)            # verborgen oder gesperrt: 404, nie 403
        f = next(x for x in r["files"] if x["sha256"] == sha)
        # Feed-Abrufe der Kataloge zählen nicht als Download (§15.8.6)
        M.event("fetch" if api else "download", a["id"], r["version"],
                principal=p["user"] or "anonymous")
        return self.send_file(sha, f["name"], f.get("media_type") or "")

    def image(self, p, sha):
        """Ein Bild sieht, wer EIN Asset sieht, das es trägt -- sonst 404."""
        if not blobs.SHA_RE.match(sha or ""):
            return self.not_found()
        for aid in M.image_assets(sha):
            a = M.asset(aid)
            if a and access.can_see(p, a, M.entitlements(aid)):
                return self.send_image(sha, M.image_row(aid, sha)["media_type"])
        return self.not_found()

    def feed(self, p, channel):
        if p["anon"]:
            return self.json(401, {"error": "Der Feed braucht einen Empfänger (API-Schlüssel)."})
        M.check_channel(channel)
        doc = feed_doc(p, channel)
        # §15.8.7: die eigene Adresse -- ein Katalog vergleicht damit einen eingefügten Link
        doc["origin"] = (os.environ.get("MARKET_PUBLIC_URL") or "").rstrip("/") or self.origin()
        body = json.dumps(doc, ensure_ascii=False, sort_keys=True)
        etag = '"' + hashlib.sha256(body.encode()).hexdigest()[:32] + '"'
        M.note_poll(p["user"], channel, ", ".join(f"{x['id']} {x['version']}" for x in doc["apps"]))
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        doc["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return self.json(200, doc, {"ETag": etag})

    # -- Review (§15.8.5) ----------------------------------------------------------
    def may_review(self, p):
        return access.is_reviewer(p, M.reviewers()) or access.is_admin(p)

    def reviews_list(self, p, state):
        if not self.may_review(p):
            return self.not_found(api=True)
        if state not in ("open", "escalated", "approved", "rejected", "all"):
            raise db.Refused(400, "state ist open, escalated, approved, rejected oder all.")
        rs = M.reviews(None if state == "all" else state, escalate_after_h=REVIEW_HOURS)
        base = self.origin()
        return self.json(200, {"review": "on" if REVIEW else "off",
                               "cases": [review_case_doc(r, base) for r in rs[:100]]})

    def review_get(self, p, parts):
        if not self.may_review(p) or not parts or not parts[0].isdigit():
            return self.not_found(api=True)
        r = M.review(parts[0])
        if not r:
            return self.not_found(api=True)
        if len(parts) == 1:
            return self.json(200, review_case_doc(r, self.origin()))
        if len(parts) == 3 and parts[1] == "blob" and blobs.SHA_RE.match(parts[2]):
            sha = parts[2]
            rel = M.release(r["asset"], r["version"]) if r["version"] else None
            f = next((x for x in (rel or {}).get("files", []) if x["sha256"] == sha), None)
            if f:
                return self.send_file(sha, f["name"], f.get("media_type") or "")
            img = M.image_row(r["asset"], sha)
            if img:
                return self.send_image(sha, img["media_type"])
        return self.not_found(api=True)

    def review_post(self, p, rid):
        r = M.review(rid) if rid.isdigit() else None
        if not r:
            return self.not_found(api=True)
        admin = access.is_admin(p)
        reviewer = access.is_reviewer(p, M.reviewers())
        if not (admin or reviewer):
            return self.not_found(api=True)
        try:
            doc = json.loads(self.body_bytes() or b"{}")
        except ValueError:
            raise db.Refused(400, "kein JSON")
        if not isinstance(doc, dict):
            raise db.Refused(400, "ein JSON-Objekt erwartet")
        as_ = "reviewer" if reviewer and r["effective"] != "escalated" else "admin" if admin else "reviewer"
        if as_ == "reviewer" and r["created_by"] == p["user"]:
            raise db.Refused(403, "Vier Augen: Wer einstellt, prüft nicht selbst.")
        state = M.decide_review(r["id"], str(doc.get("verdict") or ""), p["user"], as_,
                                str(doc.get("summary") or ""), doc.get("findings") or [])
        return self.json(200, {"id": r["id"], "state": state, "decided_as": as_})

    # -- POST / PUT / DELETE ----------------------------------------------------
    def do_POST(self):
        self.write("POST")

    def do_PUT(self):
        self.write("PUT")

    def do_DELETE(self):
        self.write("DELETE")

    def write(self, method):
        path = urlparse(self.path).path
        p = self.who(path)
        if p["anon"]:
            return self.json(401, {"error": "Anmeldung oder API-Schlüssel nötig."})
        if self.cross_site():
            return self.json(403, {"error": "Anfrage von fremder Seite abgelehnt."})
        try:
            if path == "/api/v1/releases" and method == "POST":
                return self.publish(p)
            if path.startswith("/api/v1/reviews/") and method == "POST":
                return self.review_post(p, path[len("/api/v1/reviews/"):].strip("/"))
            if path.startswith("/api/v1/assets/"):
                return self.asset_write(p, method, path[len("/api/v1/assets/"):].strip("/").split("/"))
            if path.startswith("/my/assets/") and path.endswith("/request") and method == "POST":
                return self.request_access(p, unquote(path[len("/my/assets/"):-len("/request")]))
            if path.startswith("/my/manage/") and method == "POST":
                return self.manage_form(p, path)
            if path.startswith("/admin/") and method == "POST":
                if not access.is_admin(p):
                    return self.not_found()
                return self.admin_form(p, path)
        except db.Refused as e:
            back = None
            if path.startswith("/admin/assets/"):
                back = f"/admin/assets/{path[len('/admin/assets/'):].split('/')[0]}"
            elif path.startswith(("/admin/landscape", "/admin/category")):
                back = "/admin/taxonomy"
            elif path.startswith("/admin/settings"):
                back = "/admin/settings"
            elif path.startswith(("/admin/review",)):
                back = "/admin/reviews"
            elif path.startswith("/admin/"):
                back = "/admin"
            elif path.startswith("/my/manage/"):
                back = "/my/manage"
            elif path.startswith("/my/assets/"):
                aid = path[len("/my/assets/"):].split("/")[0]
                return self.send(303, "", extra={"Location": f"/my/assets/{aid}?err=1&m=" + quote(str(e))})
            if back:
                return self.send(303, "", extra={"Location": f"{back}?m=" + quote(str(e))})
            return self.json(e.status, {"error": str(e)})
        return self.not_found(api=True)

    # -- Einchecken (§6, §15.8.2, §15.8.4, §15.8.5) ----------------------------------
    def publish(self, p):
        files = {}
        try:
            try:
                fields, files = multipart.parse(
                    self.rfile, self.headers.get("Content-Type", ""),
                    int(self.headers.get("Content-Length") or 0),
                    MAX_BYTES + 1024 * 1024, tmpdir=B.tmp_dir())
            except multipart.MultipartError as e:
                raise db.Refused(400, str(e))
            up = files.get("file")
            if not up:
                raise db.Refused(400, "Es kam keine Datei an (Feld „file“).")
            typ = (fields.get("type") or "oaap-app").strip()
            if typ not in TYPES:
                raise db.Refused(400, f"Den Asset-Typ „{typ}“ gibt es hier nicht.")
            lang = (fields.get("lang") or DEFAULT_LANG).strip().lower()
            if not db.LANG_RE.match(lang):
                raise db.Refused(400, "lang ist ein Sprachkürzel wie en oder de.")
            chans = [c.strip() for c in (fields.get("channels") or "").split(",") if c.strip()]
            for ch in chans:
                M.check_channel(ch)
            notes = fields.get("notes") or ""
            try:
                notes_i18n = json.loads(fields.get("notes_i18n") or "{}")
            except ValueError:
                raise db.Refused(400, "notes_i18n ist JSON {sprache: text}.")
            name = os.path.basename(up.get("filename") or "file")
            try:
                secretguard.scan_text(notes, "Release-Notizen")
                secretguard.scan_value(notes_i18n, "Release-Notizen")
                if typ == "oaap-app":
                    try:
                        info = zipinfo.read(up["path"], MAX_BYTES)
                    except zipinfo.PackageError as e:
                        raise db.Refused(400, str(e))
                    secretguard.scan_zip(up["path"])
                    app = info["app"]
                    aid, version = app["id"], app["version"]
                    title, summary, desc = app["name"], app["description"][:300], app["description"]
                    media = "application/zip"
                    name = f"{aid}-{version}.zip"
                elif typ == "oaap-profile":
                    try:
                        prof = profileinfo.read(up["path"])
                    except profileinfo.ProfileError as e:
                        raise db.Refused(400, str(e))
                    secretguard.scan_profile(prof)
                    aid, version = prof["id"], prof["version"]
                    title = str(prof.get("title") or aid)[:120]
                    desc = str(prof.get("description") or "")
                    summary = desc[:300]
                    media = "application/json"
                    name = f"{aid}-{version}.json"
                else:
                    aid = (fields.get("asset") or "").strip()
                    version = (fields.get("version") or "").strip()
                    if not db.ID_RE.match(aid):
                        raise db.Refused(400, "asset fehlt oder ist keine gültige Kennung.")
                    if not db.VERSION_RE.match(version):
                        raise db.Refused(400, "version fehlt oder ist keine Version wie 1.2.3.")
                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if ext not in EXTENSIONS:
                        raise db.Refused(400, f"Die Endung „.{ext}“ ist für Dateien nicht erlaubt.")
                    if os.path.getsize(up["path"]) > MAX_BYTES:
                        raise db.Refused(413, f"Größer als {MAX_MB} MB.")
                    secretguard.check_name(name)
                    if ext == "zip":
                        try:
                            secretguard.scan_zip(up["path"])
                        except Exception as e:          # noqa: BLE001 -- kein ZIP: Name reicht
                            if isinstance(e, secretguard.Leak):
                                raise
                    elif ext in TEXT_EXT:
                        with open(up["path"], "rb") as f:
                            data = f.read(MAX_BYTES)
                        if ext == "json":
                            secretguard.scan_json_bytes(data, f"Datei „{name}“")
                        else:
                            secretguard.scan_text(data.decode("utf-8", "replace"), f"Datei „{name}“")
                    title, summary, desc = (fields.get("title") or aid)[:120], "", ""
                    media = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                             "txt": "text/plain", "md": "text/plain",
                             "json": "application/json"}.get(ext, "application/octet-stream")
                secretguard.scan_value([title, summary, desc], "Texte aus dem Paket")
            except secretguard.Leak as e:
                raise leak(e)
            ents_pub = M.publishers()
            if not access.can_publish(p, aid, None, ents_pub):
                raise db.Refused(403, f"Du darfst in „{aid}“ nicht veröffentlichen.")
            for ch in chans:
                if not access.can_publish(p, aid, ch, ents_pub):
                    raise db.Refused(403, f"Du darfst „{aid}“ nicht in „{ch}“ legen.")
            a = M.asset(aid)
            if a and a["type"] != typ:
                raise db.Refused(409, f"„{aid}“ ist ein Asset vom Typ {a['type']}, nicht {typ}.")
            sha, size = B.put_file(up["path"])
            files.pop("file", None)
            if not a:
                M.create_asset(aid, typ, title, summary, desc, p["user"], lang)
            rel, created = M.add_release(aid, version, sha, size, name, media, notes, p["user"],
                                         "pending" if REVIEW else "approved", notes_i18n)
            if created and REVIEW:
                M.open_review("release", aid, version, p["user"],
                              {"gate": "passed", "type": typ, "sha256": sha, "size": size})
            for ch in chans:
                M.place(aid, version, ch, p["user"])
            rel = M.release(aid, version)
            return self.json(201 if created else 200,
                             {"asset": aid, "version": version, "sha256": sha, "size": size,
                              "created": created, "review": rel.get("review") or "approved",
                              "channels": rel["channels"]})
        finally:
            multipart.cleanup(files)

    def request_access(self, p, aid):
        a = M.asset(aid)
        if not a or not access.can_see(p, a, M.entitlements(aid)):
            return self.not_found()
        form = {k: v[0].strip() for k, v in parse_qs(self.body_bytes().decode("utf-8", "replace")).items()}
        ch = form.get("channel", "")
        _rid, new = M.request_access(aid, p["user"], [ch] if ch else [], form.get("note", ""))
        c = self.ctx(p, "/my")
        msg = ("Request sent. The operator decides; you will see the answer here."
               if c.ul == "en" else "Anfrage gestellt. Der Betreiber entscheidet; du siehst die Antwort hier.")
        if not new:
            msg = "Your open request was updated." if c.ul == "en" else "Deine offene Anfrage ist ergänzt."
        return self.send(303, "", extra={"Location": f"/my/assets/{quote(aid)}?m=" + quote(msg)})

    def take_image(self, p, aid, role_default="screenshot", target=None):
        """Ein Bild aus einem multipart-Formular annehmen -> (sha, role). Metadaten
        werden VOR dem Ablegen entfernt; die Prüfsumme gilt dem bereinigten Bild."""
        files = {}
        try:
            try:
                fields, files = multipart.parse(
                    self.rfile, self.headers.get("Content-Type", ""),
                    int(self.headers.get("Content-Length") or 0),
                    images.MAX_IMAGE_BYTES + 64 * 1024, tmpdir=B.tmp_dir())
            except multipart.MultipartError as e:
                raise db.Refused(400, str(e))
            up = files.get("file")
            if not up:
                raise db.Refused(400, "Es kam keine Datei an (Feld „file“).")
            try:
                mt = images.check(up["path"], up["bytes"])
                images.strip_metadata(up["path"], mt)
            except images.ImageRefused as e:
                raise db.Refused(400, str(e))
            role = (fields.get("role") or role_default).strip()
            sha, size = B.put_file(up["path"])
            files.pop("file", None)
            if target:
                target(sha)
            else:
                M.add_image(aid, sha, mt, size, role, p["user"])
            return sha, role
        finally:
            multipart.cleanup(files)

    def drop_image(self, p, aid, sha):
        M.remove_image(aid, sha, p["user"])
        if M.blob_unreferenced(sha) and not M.is_logo(sha):
            B.delete(sha)

    def listing_changed(self, p, aid, what):
        """Listing-Review (§15.8.5): sichtbar sofort, geprüft danach -- nicht für den
        Betreiber im Browser (der ist schon ein Mensch)."""
        if REVIEW and not access.is_admin(p):
            M.open_review("listing", aid, "", p["user"], {"gate": "passed"}, what)

    def asset_write(self, p, method, parts):
        aid = unquote(parts[0]) if parts else ""
        a = M.asset(aid)
        if not a or not access.can_see(p, a, M.entitlements(aid)):
            return self.not_found(api=True)
        pubs = M.publishers()
        if len(parts) >= 2 and parts[1] == "images":
            if not access.can_publish(p, aid, None, pubs):
                raise db.Refused(403, f"Du darfst „{aid}“ nicht ändern.")
            if len(parts) == 2 and method == "POST":
                sha, role = self.take_image(p, aid)
                self.listing_changed(p, aid, "images")
                return self.json(201, {"asset": aid, "sha256": sha, "role": role})
            if len(parts) == 3 and method == "DELETE":
                self.drop_image(p, aid, parts[2])
                return self.json(200, {"asset": aid, "removed": parts[2]})
            return self.not_found(api=True)
        if len(parts) == 1 and method == "PUT":
            if not access.can_publish(p, aid, None, pubs):
                raise db.Refused(403, f"Du darfst „{aid}“ nicht ändern.")
            try:
                doc = json.loads(self.body_bytes(512 * 1024) or b"{}")
            except ValueError:
                raise db.Refused(400, "kein JSON")
            if not isinstance(doc, dict):
                raise db.Refused(400, "ein JSON-Objekt erwartet")
            if ("visibility" in doc or "free_channels" in doc or "featured" in doc) and not access.is_admin(p):
                raise db.Refused(403, "Sichtbarkeit, freie Kanäle und „Empfohlen“ setzt der Betreiber.")
            try:
                secretguard.scan_value(doc, "Asset")
            except secretguard.Leak as e:
                raise leak(e)
            M.update_asset(aid, doc, p["user"])
            self.listing_changed(p, aid, ",".join(sorted(k for k in doc if k != "lang")))
            return self.json(200, asset_view(p, M.asset(aid), M.entitlements(aid), detail=True))
        if len(parts) == 5 and parts[1] == "releases" and parts[3] == "channels":
            version, ch = unquote(parts[2]), unquote(parts[4])
            if not access.can_publish(p, aid, ch, pubs):
                raise db.Refused(403, f"Du darfst „{aid}“ nicht in „{ch}“ legen.")
            if method == "POST":
                M.place(aid, version, ch, p["user"])
            elif method == "DELETE":
                M.unplace(aid, version, ch, p["user"])
            else:
                return self.not_found(api=True)
            return self.json(200, {"asset": aid, "version": version,
                                   "channels": (M.release(aid, version) or {}).get("channels", [])})
        if len(parts) == 4 and parts[1] == "releases" and parts[3] == "yank" and method == "POST":
            if not access.can_publish(p, aid, None, pubs):
                raise db.Refused(403, f"Du darfst „{aid}“ nicht zurückrufen.")
            try:
                doc = json.loads(self.body_bytes() or b"{}")
            except ValueError:
                raise db.Refused(400, "kein JSON")
            M.yank(aid, unquote(parts[2]), (doc or {}).get("reason", ""), p["user"])
            return self.json(200, {"asset": aid, "version": unquote(parts[2]), "yanked": True})
        return self.not_found(api=True)

    # -- Partner light (§15.8.9) -----------------------------------------------------
    def manage_form(self, p, path):
        form = {k: (v if isinstance(v, str) else v[0]).strip() for k, v in self.form().items()}
        pubs = M.publishers()

        def own(aid):
            if not M.asset(aid) or not access.can_manage(p, aid, pubs):
                raise db.Refused(403, "Das darfst du nur für Assets, in die du einstellen darfst.")

        def check_chans(aid, chans):
            for ch in chans:
                if ch != "*" and not access.can_publish(p, aid, ch, pubs):
                    raise db.Refused(403, f"Den Kanal „{ch}“ darfst du für „{aid}“ nicht vergeben.")
                if ch == "*" and not access.can_publish(p, aid, "*", pubs) and not access.is_admin(p):
                    raise db.Refused(403, "Alle Kanäle darfst du nicht vergeben; nenne deine Kanäle.")

        def own_chans(aid):
            return [ch for ch in M.channels if access.can_publish(p, aid, ch, pubs)]

        chans = [c.strip() for c in form.get("channels", "").split(",") if c.strip()]
        if path == "/my/manage/grant":
            aid = form.get("asset", "")
            own(aid)
            chans = chans or own_chans(aid)
            check_chans(aid, chans)
            M.grant(form.get("kind"), form.get("name"), aid, chans, p["user"], note="Partner")
            msg = "Berechtigt."
        elif path == "/my/manage/revoke":
            e = next((x for x in M.entitlements() if str(x["id"]) == form.get("id", "")), None)
            if not e:
                raise db.Refused(404, "Diese Berechtigung gibt es nicht.")
            own(e["asset"])
            M.revoke(e["id"], p["user"])
            msg = "Entzogen."
        elif path == "/my/manage/request":
            r = next((x for x in M.requests("open") if str(x["id"]) == form.get("id", "")), None)
            if not r:
                raise db.Refused(404, "Diese offene Anfrage gibt es nicht.")
            own(r["asset"])
            answer = form.get("answer", "")
            try:
                secretguard.scan_text(answer, "Antwort")
            except secretguard.Leak as e:
                raise leak(e)
            if form.get("do") == "grant":
                chans = chans or [c for c in json.loads(r["channels"] or "[]") if c != "*"] or own_chans(r["asset"])
                check_chans(r["asset"], chans)
                M.decide_request(r["id"], True, p["user"], chans, answer)
                msg = "Freigeschaltet."
            else:
                M.decide_request(r["id"], False, p["user"], answer=answer)
                msg = "Abgelehnt."
        else:
            return self.not_found()
        return self.send(303, "", extra={"Location": "/my/manage?m=" + quote(msg)})

    # -- Verwaltung ----------------------------------------------------------------------
    def admin_form(self, p, path):
        if path.startswith("/admin/assets/"):
            return self.admin_asset(p, path)
        for table in ("landscape", "category"):
            if path.startswith(f"/admin/{table}-logo/"):
                rid = unquote(path[len(f"/admin/{table}-logo/"):])
                self.take_image(p, None, target=lambda sha, t=table, r=rid: M.set_logo(t, r, sha, p["user"]))
                return self.send(303, "", extra={"Location": "/admin/taxonomy?m=" + quote("Logo gesetzt.")})
        if path == "/admin/settings-logo":
            self.take_image(p, None, target=lambda sha: M.set_setting("logo", sha, p["user"]))
            return self.send(303, "", extra={"Location": "/admin/settings?m=" + quote("Logo gesetzt.")})
        form = {k: (v if isinstance(v, str) else v[0]).strip() for k, v in self.form().items()}
        chans = [c.strip() for c in form.get("channels", "").split(",") if c.strip()]
        back = "/admin"
        if path == "/admin/grant":
            M.grant(form.get("kind"), form.get("name"), form.get("asset"), chans, p["user"])
            msg = "Berechtigt."
        elif path == "/admin/revoke":
            M.revoke(form.get("id", "0"), p["user"])
            msg = "Entzogen."
        elif path == "/admin/publisher":
            M.add_publisher(form.get("kind"), form.get("name"), form.get("pattern"), chans, p["user"])
            msg = "Erlaubt."
        elif path == "/admin/publisher-remove":
            M.remove_publisher(form.get("id", "0"), p["user"])
            msg = "Entfernt."
        elif path == "/admin/request-grant":
            M.decide_request(form.get("id", "0"), True, p["user"], chans or None, form.get("answer", ""))
            msg = "Freigeschaltet."
        elif path == "/admin/request-reject":
            M.decide_request(form.get("id", "0"), False, p["user"], answer=form.get("answer", ""))
            msg = "Abgelehnt."
        elif path == "/admin/asset":
            free = [c.strip() for c in form.get("free", "").split(",") if c.strip()]
            M.update_asset(form.get("asset", ""), {"visibility": {"mode": form.get("mode", "anyone")},
                                                   "free_channels": free}, p["user"])
            msg = "Gesetzt."
        elif path in ("/admin/landscape", "/admin/category"):
            texts = {}
            for code in ("en", "de"):
                texts[code] = {"title": form.get(f"title_{code}", ""),
                               "description": form.get(f"description_{code}", "")}
            try:
                secretguard.scan_value(texts, "Texte")
            except secretguard.Leak as e:
                raise leak(e)
            sort = int(form.get("sort") or 100) if (form.get("sort") or "100").lstrip("-").isdigit() else 100
            if path == "/admin/landscape":
                M.set_landscape(form.get("id", "").lower(), texts, sort, p["user"])
            else:
                M.set_category(form.get("id", "").lower(), form.get("landscape", db.DEFAULT_LANDSCAPE),
                               texts, sort, p["user"])
            msg, back = "Gespeichert.", "/admin/taxonomy"
        elif path == "/admin/landscape-remove":
            M.delete_landscape(form.get("id", ""), p["user"])
            msg, back = "Gelöscht.", "/admin/taxonomy"
        elif path == "/admin/category-remove":
            M.delete_category(form.get("id", ""), p["user"])
            msg, back = "Gelöscht.", "/admin/taxonomy"
        elif path == "/admin/settings":
            if form.get("accent_reset"):
                M.set_setting("accent", "", p["user"])
            elif views.HEX_RE.match(form.get("accent", "")):
                M.set_setting("accent", form["accent"], p["user"])
            rows = [r.strip() for r in form.get("rows", "").split(",") if r.strip() in views.ROWS]
            M.set_setting("rows", ",".join(rows), p["user"])
            if form.get("default_lang") in i18n.UI_LANGS:
                M.set_setting("default_lang", form["default_lang"], p["user"])
            msg, back = "Gespeichert.", "/admin/settings"
        elif path == "/admin/review":
            r = M.review(form.get("id", "0")) if form.get("id", "").isdigit() else None
            if not r:
                raise db.Refused(404, "Diesen Fall gibt es nicht.")
            M.decide_review(r["id"], form.get("verdict", ""), p["user"], "admin", form.get("summary", ""))
            msg, back = "Entschieden.", "/admin/reviews"
        elif path == "/admin/reviewer":
            M.add_reviewer(form.get("kind"), form.get("name"), p["user"])
            msg, back = "Prüfer hinzugefügt.", "/admin/reviews"
        elif path == "/admin/reviewer-remove":
            M.remove_reviewer(form.get("id", "0"), p["user"])
            msg, back = "Entfernt.", "/admin/reviews"
        else:
            return self.not_found()
        return self.send(303, "", extra={"Location": f"{back}?m=" + quote(msg)})

    def admin_asset(self, p, path):
        parts = path[len("/admin/assets/"):].split("/")
        aid, action = unquote(parts[0]), (parts[1] if len(parts) > 1 else "")
        if not M.asset(aid):
            return self.not_found()
        lang = "en"
        if action == "image":
            self.take_image(p, aid)
            msg = "Bild aufgenommen."
        else:
            form = self.form(512 * 1024)
            first = {k: (v if isinstance(v, str) else v[0]) for k, v in form.items()}
            try:
                if action == "texts":
                    lang = first.get("lang", "en")
                    texts = {lang: {k: first.get(k, "") for k in db.TEXT_FIELDS}}
                    secretguard.scan_value(texts, "Texte")
                    M.update_asset(aid, {"texts": texts}, p["user"])
                    msg = "Texte gespeichert."
                elif action == "meta":
                    as_list = lambda k: ([form[k]] if isinstance(form.get(k), str) else form.get(k) or [])  # noqa: E731
                    links = _links_from_text(first.get("links", ""))
                    secretguard.scan_value([first.get("tags", ""), first.get("publisher", ""),
                                            first.get("license", ""), links], "Asset")
                    M.update_asset(aid, {"tags": first.get("tags", ""),
                                         "landscapes": as_list("landscapes"),
                                         "categories": as_list("categories"),
                                         "featured": bool(first.get("featured")),
                                         "publisher": first.get("publisher", ""),
                                         "license": first.get("license", ""),
                                         "links": links}, p["user"])
                    msg = "Gespeichert."
                elif action == "image-remove":
                    self.drop_image(p, aid, first.get("sha", ""))
                    msg = "Bild entfernt."
                else:
                    return self.not_found()
            except secretguard.Leak as e:
                raise leak(e)
        return self.send(303, "", extra={"Location": f"/admin/assets/{quote(aid)}?lang={lang}&m=" + quote(msg)})


def _links_from_text(text):
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        label, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        out.append({"label": label.strip() or url.strip(), "url": url.strip()})
    return out


def make_server(data_dir=None, port=PORT):
    global M, B
    d = data_dir or DATA_DIR
    os.makedirs(d, exist_ok=True)
    M = db.Market(os.path.join(d, "market.db"), CHANNELS)
    B = blobs.BlobStore(os.path.join(d, "blobs"))
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main():
    srv = make_server()
    print(f"marketplace {VERSION} on :{PORT}, data {DATA_DIR}, review {'on' if REVIEW else 'off'}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
