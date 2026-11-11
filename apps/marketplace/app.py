"""Marketplace 0.2 -- digitale Assets mit Kanälen, Sichtbarkeit und Berechtigungen (RFC-0077 Stufe 1).

Ein Asset hat unveränderliche Releases und Kanäle (stable, beta), die auf
Releases zeigen. Ob jemand ein Asset SIEHT und ob er es BEKOMMT, sind zwei
Fragen -- beide beantwortet `access.py`, an genau einer Stelle.

Wege (Manifest):
  /, /assets/*, /d/*, /api/public/v1/*   öffentlich: was `anyone` sehen darf
  /my/*                                    dieselben Seiten angemeldet
  /api/v1/*                                Suche, Details, Veröffentlichen (Sitzung oder API-Schlüssel)
  /feed/v1/*                               Empfänger, z. B. ein Paketkatalog im Spiegelmodus
  /admin/*                                 Verwaltung (admin)

Die App führt nichts Hochgeladenes aus und entpackt nichts außer dem
Manifest eines OAAP-Pakets (im Speicher, `zipinfo.py`).
"""
import hashlib
import html
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import access
import blobs
import db
import images
import multipart
import views
import zipinfo

VERSION = "0.2.1"
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
TYPES = {t.strip() for t in (os.environ.get("MARKET_TYPES") or "oaap-app,file").split(",")
         if t.strip()} & {"oaap-app", "file"}
EXTENSIONS = {e.strip().lower().lstrip(".") for e in
              (os.environ.get("MARKET_FILE_EXTENSIONS") or "zip,pdf,png,jpg,txt,md").split(",")
              if e.strip()}
MAX_FORM = 64 * 1024
PUBLIC_PREFIXES = ("/assets", "/d/", "/img/", "/api/public/", "/static/")
SAFE_INLINE = {"application/pdf", "image/png", "image/jpeg", "text/plain"}

esc = html.escape
M = None          # db.Market
B = None          # blobs.BlobStore


def is_public_path(path):
    """Öffentliche Wege tragen keine Identität -- die App liest dort keine."""
    return path == "/" or path == "/healthz" or path.startswith(PUBLIC_PREFIXES)


# ---------------------------------------------------------------- views -----
def visible_assets(p, q="", chosen=None):
    """Filtern VOR dem Zählen und Ordnen (§5) -- dieselbe Suche wie die Seite."""
    return views.search(M, p, q, chosen)[0]


def asset_view(p, a, ents, detail=False):
    st = access.state(p, a, ents, M.channels)
    per = {}
    for ch in M.channels:
        cur = M.current(a["id"], ch)
        if cur:
            per[ch] = {"version": cur["version"],
                       "obtainable": access.can_obtain(p, a, ch, ents)}
    v = {"id": a["id"], "type": a["type"], "title": a.get("title") or a["id"],
         "summary": a.get("summary") or "", "state": st, "channels": per,
         "free_channels": sorted(access.free_channels(a)),
         "tags": a.get("tags_list", []), "categories": a.get("categories_list", []),
         "publisher": a.get("publisher") or "", "license": a.get("license") or "",
         "links": a.get("links_list", []), "icon": a.get("icon") or "",
         "screenshots": a.get("screenshots_list", [])}
    if detail:
        v["description"] = a.get("description") or ""
        rels = []
        for r in M.releases(a["id"]):
            if r["yanked_at"] and not access.is_admin(p):
                continue
            item = {"version": r["version"], "channels": r["channels"],
                    "notes": r.get("notes") or "", "yanked": bool(r["yanked_at"])}
            ok = any(access.can_obtain(p, a, ch, ents) for ch in r["channels"]) \
                and not r["yanked_at"]
            if ok:
                item["files"] = [{"sha256": f["sha256"], "name": f["name"],
                                  "size": f["size"]} for f in r["files"]]
            rels.append(item)
        v["releases"] = rels
    return v


def file_obtainable(p, sha):
    """Eine Datei darf laden, wer EIN nicht zurückgerufenes, platziertes Release
    mit ihr auf einem Kanal beziehen darf."""
    for ref in M.releases_with_file(sha):
        a = M.asset(ref["asset"])
        r = M.release(ref["asset"], ref["version"])
        if not a or not r or r["yanked_at"]:
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
                apps.append({"id": a["id"], "name": a.get("title") or a["id"],
                             "version": cur["version"],
                             "released": _date(cur["created_at"]),
                             "summary": (a.get("summary") or "")[:300],
                             "description": a.get("description") or "",
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


# ---------------------------------------------------------------- HTML ------
STYLE = """<style>
  :root{--blue:#1e3a8a;--bg:#f4f6fa;--text:#1f2937;--muted:#6b7280;--card:#fff;
        --border:#e5e7eb;--ok:#15803d;--err:#b91c1c;--chip:#dbeafe}
  @media (prefers-color-scheme: dark){:root{--bg:#0f172a;--text:#e5e7eb;--muted:#94a3b8;
        --card:#111827;--border:#1f2937;--chip:#1e3a8a}}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;background:var(--bg);color:var(--text)}
  header{background:var(--blue);color:#fff;padding:.7rem 1.2rem;font-weight:600;display:flex;
         justify-content:space-between;align-items:center}
  header a{color:#fff;font-weight:400;font-size:.9rem}
  main{max-width:64rem;margin:1.4rem auto;padding:0 1rem}
  .card{background:var(--card);border:1px solid var(--border);border-radius:.6rem;padding:1.1rem;margin-bottom:1rem}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:1rem}
  h1{font-size:1.3rem;margin:.1rem 0 .8rem} h2{font-size:1.02rem;margin:0 0 .6rem}
  table{border-collapse:collapse;width:100%;font-size:.9rem}
  th,td{text-align:left;padding:.35rem .45rem;border-bottom:1px solid var(--border);vertical-align:top}
  .muted{color:var(--muted)} .ok{color:var(--ok)} .err{color:var(--err)}
  .badge{font-size:.72rem;padding:.1rem .5rem;border-radius:1rem;background:var(--chip)}
  .badge.free{background:#dcfce7;color:#166534} .badge.locked{background:#fef3c7;color:#92400e}
  button,.btn{padding:.35rem .8rem;border:0;border-radius:.35rem;background:var(--blue);color:#fff;
        cursor:pointer;font-size:.88rem;text-decoration:none;display:inline-block}
  input,select{padding:.35rem;border:1px solid var(--border);border-radius:.3rem;max-width:100%}
  form.inline{display:inline} code{background:rgba(127,127,127,.15);padding:.05rem .3rem;border-radius:.2rem}
</style>"""


def page(title, body, base, signed):
    login = (f'<a href="/my/">Anmelden</a>' if not signed else
             f'<span><a href="{base}/">Übersicht</a></span>')
    return ("<!doctype html><html lang=de><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)} – {esc(NAME)}</title>{STYLE}"
            f"<header><span>{esc(NAME)}</span>{login}</header><main>{body}</main></html>")


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
                         "form-action 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, status, doc, extra=None):
        self.send(status, json.dumps(doc, ensure_ascii=False, indent=1),
                  "application/json; charset=utf-8", extra)

    def not_found(self, api=False):
        if api:
            return self.json(404, {"error": "not found"})
        return self.send(404, page("Nicht gefunden", "<h1>Nicht gefunden</h1>", "", False))

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
            for block in iter(lambda: f.read(1 << 20), b""):
                self.wfile.write(block)

    # -- GET ------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path, q = u.path, parse_qs(u.query)
        one = lambda k, d="": (q.get(k) or [d])[0]  # noqa: E731
        if path == "/healthz":
            return self.json(200, {"status": "ok", "version": VERSION})
        p = self.who(path)
        try:
            # Seiten: öffentlich unter /, angemeldet unter /my
            chosen = {k: one(k)[:40] for k, _l in views.FACETS}
            if path in ("/", "/my", "/my/"):
                base = "" if path == "/" else "/my"
                if base and p["anon"]:
                    return self.send(401, page("Anmeldung", "<p>Bitte anmelden.</p>", "", False))
                return self.send(200, views.render_list(M, NAME, p, base, one("q")[:80], chosen))
            for base in ("", "/my"):
                if path.startswith(base + "/assets/"):
                    aid = unquote(path[len(base + "/assets/"):]).strip("/")
                    a = M.asset(aid)
                    ents = M.entitlements(aid) if a else []
                    if not a or not access.can_see(p, a, ents):
                        return self.not_found()
                    return self.send(200, views.render_detail(M, NAME, p, base, a, ents,
                                                              one("m")[:300], not one("err")))
                if path.startswith(base + "/d/"):
                    sha = path[len(base + "/d/"):].strip("/")
                    return self.download(p, sha)
                if path.startswith(base + "/img/"):
                    return self.image(p, path[len(base + "/img/"):].strip("/"))
            if path == "/admin" or path.startswith("/admin/"):
                if not access.is_admin(p):
                    return self.not_found()
                if path.startswith("/admin/assets/"):
                    a = M.asset(unquote(path[len("/admin/assets/"):]).strip("/"))
                    if not a:
                        return self.not_found()
                    return self.send(200, views.render_asset_edit(M, NAME, p, a, one("m")[:300]))
                return self.send(200, views.render_admin(M, NAME, p, one("m")[:300]))
            if path in ("/api/public/v1/assets", "/api/v1/assets"):
                items = [asset_view(p, a, e) for a, e in visible_assets(p, one("q")[:80], chosen)]
                return self.json(200, {"count": len(items), "assets": items})
            for pre in ("/api/public/v1/assets/", "/api/v1/assets/"):
                if path.startswith(pre):
                    aid = unquote(path[len(pre):]).strip("/")
                    a = M.asset(aid)
                    ents = M.entitlements(aid) if a else []
                    if not a or not access.can_see(p, a, ents):
                        return self.not_found(api=True)
                    return self.json(200, asset_view(p, a, ents, detail=True))
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
        M.event("download", a["id"], r["version"], principal=p["user"] or "anonymous")
        return self.send_file(sha, f["name"], f.get("media_type") or "")

    def image(self, p, sha):
        """Ein Bild sieht, wer EIN Asset sieht, das es trägt -- sonst 404."""
        if not blobs.SHA_RE.match(sha or ""):
            return self.not_found()
        for aid in M.image_assets(sha):
            a = M.asset(aid)
            if a and access.can_see(p, a, M.entitlements(aid)):
                row = M.image_row(aid, sha)
                try:
                    f = B.open(sha)
                except (OSError, ValueError):
                    return self.not_found()
                with f:
                    data = f.read()
                return self.send(200, data, row["media_type"],
                                 {"Content-Disposition": "inline",
                                  "Content-Security-Policy": "default-src 'none'; sandbox"})
        return self.not_found()

    def feed(self, p, channel):
        if p["anon"]:
            return self.json(401, {"error": "Der Feed braucht einen Empfänger (API-Schlüssel)."})
        M.check_channel(channel)
        doc = feed_doc(p, channel)
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
            if path.startswith("/api/v1/assets/"):
                return self.asset_write(p, method, path[len("/api/v1/assets/"):].strip("/").split("/"))
            if path.startswith("/my/assets/") and path.endswith("/request") and method == "POST":
                return self.request_access(p, unquote(path[len("/my/assets/"):-len("/request")]))
            if path.startswith("/admin/") and method == "POST":
                if not access.is_admin(p):
                    return self.not_found()
                return self.admin_form(p, path)
        except db.Refused as e:
            if path.startswith("/admin/assets/"):
                aid = path[len("/admin/assets/"):].split("/")[0]
                return self.send(303, "", extra={"Location": f"/admin/assets/{aid}?m=" + quote(str(e))})
            if path.startswith("/admin/"):
                return self.send(303, "", extra={"Location": "/admin?m=" + quote(str(e))})
            if path.startswith("/my/assets/"):
                aid = path[len("/my/assets/"):].split("/")[0]
                return self.send(303, "", extra={"Location": f"/my/assets/{aid}?err=1&m=" + quote(str(e))})
            return self.json(e.status, {"error": str(e)})
        return self.not_found(api=True)

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
            chans = [c.strip() for c in (fields.get("channels") or "").split(",") if c.strip()]
            for ch in chans:
                M.check_channel(ch)
            name = os.path.basename(up.get("filename") or "file")
            if typ == "oaap-app":
                try:
                    info = zipinfo.read(up["path"], MAX_BYTES)
                except zipinfo.PackageError as e:
                    raise db.Refused(400, str(e))
                app = info["app"]
                aid, version = app["id"], app["version"]
                title, summary, desc = app["name"], app["description"][:300], app["description"]
                media = "application/zip"
                name = f"{aid}-{version}.zip"
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
                title, summary, desc = (fields.get("title") or aid)[:120], "", ""
                media = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg",
                         "txt": "text/plain", "md": "text/plain"}.get(ext, "application/octet-stream")
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
                M.create_asset(aid, typ, title, summary, desc, p["user"])
            rel, created = M.add_release(aid, version, sha, size, name, media,
                                         fields.get("notes") or "", p["user"])
            for ch in chans:
                M.place(aid, version, ch, p["user"])
            return self.json(201 if created else 200,
                             {"asset": aid, "version": version, "sha256": sha, "size": size,
                              "created": created,
                              "channels": M.release(aid, version)["channels"]})
        finally:
            multipart.cleanup(files)

    def request_access(self, p, aid):
        a = M.asset(aid)
        if not a or not access.can_see(p, a, M.entitlements(aid)):
            return self.not_found()
        form = {k: v[0].strip() for k, v in parse_qs(self.body_bytes().decode("utf-8", "replace")).items()}
        ch = form.get("channel", "")
        _rid, new = M.request_access(aid, p["user"], [ch] if ch else [], form.get("note", ""))
        msg = ("Anfrage gestellt. Der Betreiber entscheidet; du siehst die Antwort hier."
               if new else "Deine offene Anfrage ist ergänzt.")
        return self.send(303, "", extra={"Location": f"/my/assets/{quote(aid)}?m=" + quote(msg)})

    def take_image(self, p, aid, role_default="screenshot"):
        """Ein Bild aus einem multipart-Formular annehmen -> (sha, role)."""
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
            except images.ImageRefused as e:
                raise db.Refused(400, str(e))
            role = (fields.get("role") or role_default).strip()
            sha, size = B.put_file(up["path"])
            files.pop("file", None)
            M.add_image(aid, sha, mt, size, role, p["user"])
            return sha, role
        finally:
            multipart.cleanup(files)

    def drop_image(self, p, aid, sha):
        M.remove_image(aid, sha, p["user"])
        if M.blob_unreferenced(sha):
            B.delete(sha)

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
                return self.json(201, {"asset": aid, "sha256": sha, "role": role})
            if len(parts) == 3 and method == "DELETE":
                self.drop_image(p, aid, parts[2])
                return self.json(200, {"asset": aid, "removed": parts[2]})
            return self.not_found(api=True)
        if len(parts) == 1 and method == "PUT":
            if not access.can_publish(p, aid, None, pubs):
                raise db.Refused(403, f"Du darfst „{aid}“ nicht ändern.")
            try:
                doc = json.loads(self.body_bytes() or b"{}")
            except ValueError:
                raise db.Refused(400, "kein JSON")
            if not isinstance(doc, dict):
                raise db.Refused(400, "ein JSON-Objekt erwartet")
            if ("visibility" in doc or "free_channels" in doc) and not access.is_admin(p):
                raise db.Refused(403, "Sichtbarkeit und freie Kanäle setzt der Betreiber.")
            M.update_asset(aid, doc, p["user"])
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

    def admin_form(self, p, path):
        if path.startswith("/admin/assets/"):
            return self.admin_asset(p, path)
        form = {k: v[0].strip() for k, v in parse_qs(self.body_bytes().decode("utf-8", "replace")).items()}
        chans = [c.strip() for c in form.get("channels", "").split(",") if c.strip()]
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
            chans = [c.strip() for c in form.get("channels", "").split(",") if c.strip()]
            M.decide_request(form.get("id", "0"), True, p["user"], chans or None)
            msg = "Freigeschaltet."
        elif path == "/admin/request-reject":
            M.decide_request(form.get("id", "0"), False, p["user"], answer=form.get("answer", ""))
            msg = "Abgelehnt."
        elif path == "/admin/asset":
            free = [c.strip() for c in form.get("free", "").split(",") if c.strip()]
            M.update_asset(form.get("asset", ""), {"visibility": {"mode": form.get("mode", "anyone")},
                                                   "free_channels": free}, p["user"])
            msg = "Gesetzt."
        else:
            return self.not_found()
        return self.send(303, "", extra={"Location": "/admin?m=" + quote(msg)})


def _links_from_text(text):
    out = []
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        label, _, url = line.rpartition("|") if "|" in line else ("", "", line)
        out.append({"label": label.strip() or url.strip(), "url": url.strip()})
    return out


def _admin_asset(self, p, path):
    parts = path[len("/admin/assets/"):].split("/")
    aid, action = unquote(parts[0]), (parts[1] if len(parts) > 1 else "")
    if not M.asset(aid):
        return self.not_found()
    if action == "image":
        self.take_image(p, aid)
        msg = "Bild aufgenommen."
    else:
        form = {k: v[0] for k, v in parse_qs(self.body_bytes(256 * 1024).decode("utf-8", "replace"),
                                             keep_blank_values=True).items()}
        if action == "meta":
            M.update_asset(aid, {"title": form.get("title", "").strip()[:120],
                                 "summary": form.get("summary", "").strip()[:300],
                                 "description": form.get("description", ""),
                                 "tags": form.get("tags", ""), "categories": form.get("categories", ""),
                                 "publisher": form.get("publisher", ""), "license": form.get("license", ""),
                                 "links": _links_from_text(form.get("links", ""))}, p["user"])
            msg = "Gespeichert."
        elif action == "image-remove":
            self.drop_image(p, aid, form.get("sha", ""))
            msg = "Bild entfernt."
        else:
            return self.not_found()
    return self.send(303, "", extra={"Location": f"/admin/assets/{quote(aid)}?m=" + quote(msg)})


Handler.admin_asset = _admin_asset


def make_server(data_dir=None, port=PORT):
    global M, B
    d = data_dir or DATA_DIR
    os.makedirs(d, exist_ok=True)
    M = db.Market(os.path.join(d, "market.db"), CHANNELS)
    B = blobs.BlobStore(os.path.join(d, "blobs"))
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main():
    srv = make_server()
    print(f"marketplace {VERSION} on :{PORT}, data {DATA_DIR}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
