"""Wegweiser 0.1 — Kurzlinks, Zählpixel und Einmal-Übergaben unter eigener Domain.

Eine Adresse der Form  https://go.example.org/<area>/<key>  wird öffentlich
aufgelöst. Die **Area** sagt, was passiert, und trägt Voreinstellungen
und Verantwortung; der **Key** ist alphanumerisch, höchstens 8 Zeichen.

Fünf Servicetypen: Weiterleitung, Zählpixel, Einmal-Download,
Zeitraum-Download, Einmal-Upload.

Drei Wege in dieselbe App, im Manifest mit verschiedenen Rollen:

- `/`        ist `public` — die Plattform authentifiziert dort nichts,
             entfernt gefälschte Identitäts-Kopfzeilen und bremst je Client
             (RFC-0010). PIN, Fristen und Zähler prüft die App selbst.
- `/manage`  und `/api` sind `user` — die Plattform liefert die geprüfte
             Identität als Kopfzeilen (RFC-0040); API-Schlüssel (RFC-0027)
             kommen denselben Weg. Links gehören an die Benutzer-UUID.
- `/admin`   ist `admin` — Areas anlegen und verantworten lassen.

Einmal-Links entwertet nie der erste GET: Mailprogramme und Messenger
rufen Links beim Empfang für Vorschauen auf. Erst der POST von der
Zwischenseite lädt herunter, erst der PUT lädt hoch.
"""
import csv
import io
import json
import os
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import store
import ui

VERSION = "0.1.0"
PORT = 8000

DATA_DIR = os.environ.get("WEGWEISER_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "wegweiser.db")
FILES_DIR = os.path.join(DATA_DIR, "files")
APP_SECRET = os.environ.get("OAAP_APP_SECRET", "") or "kein-geheimnis-gesetzt"

try:
    MAX_UPLOAD_MB = max(1, int(os.environ.get("WEGWEISER_MAX_UPLOAD_MB") or 512))
except ValueError:
    MAX_UPLOAD_MB = 512

MAX_FORM = 64 * 1024
MAX_JSON = 256 * 1024

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("WEGWEISER_TZ") or "Europe/Berlin")
except Exception:  # keine Zonendaten im Image — dann eben UTC
    TZ = timezone.utc

esc = ui.esc

GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
       b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")

DB = store.connect(DB_PATH)
os.makedirs(FILES_DIR, exist_ok=True)


def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# --------------------------------------------------------------- Identität

class Who:
    """Was die Plattform über den Anfragenden sagt (RFC-0040, fünf Kopfzeilen)."""

    def __init__(self, headers):
        self.name = headers.get("X-OAAP-User", "") or ""
        self.id = headers.get("X-OAAP-User-Id", "") or ""
        self.display = urllib.parse.unquote(headers.get("X-OAAP-Display-Name", "") or "")
        self.email = urllib.parse.unquote(headers.get("X-OAAP-Email", "") or "")
        self.roles = {r.strip() for r in (headers.get("X-OAAP-Roles", "") or "").split(",") if r.strip()}
        self.is_admin = "admin" in self.roles
        self.is_keyuser = self.is_admin or "keyuser" in self.roles

    @property
    def known(self):
        return bool(self.id)

    def manages(self, area):
        """Admin überall; Keyuser dort, wo er als Verantwortlicher steht.
        Verantwortliche werden per Benutzername benannt — ein Admin kennt
        keine UUIDs, und die Plattform bietet Apps kein Benutzerverzeichnis."""
        return self.is_admin or (self.is_keyuser and self.name in (area.get("responsible") or []))

    def may_use(self, area):
        return area["active"] and (self.manages(area) or area["open_for_users"])

    def may_see(self, link, area):
        return link["created_by"] == self.id or self.manages(area)


# ---------------------------------------------------------------- Zeiten

def parse_when(text):
    """Eingaben von Menschen (lokal, `datetime-local`) und Maschinen (ISO mit Z)
    → ISO-UTC für die Datenbank. Leer bleibt None. Unlesbar → ValueError."""
    text = (text or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        dt = store.parse_iso(text)
        if dt is None:
            dt = datetime.strptime(text, "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        return store.iso(dt)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    else:
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            raise ValueError(f"Zeitangabe nicht lesbar: {text}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return store.iso(dt.astimezone(timezone.utc))


# ------------------------------------------------------- Link vorbereiten

def prepare_link(who, area, data, existing=None):
    """Aus Formular- oder JSON-Feldern die Spalten eines Links — mit allen
    Regeln der Area. Gibt (fields, key, pin, errors) zurück."""
    errors, fields = [], {}
    manager = who.manages(area)
    t = area["type"]

    title = str(data.get("title") or "").strip()
    note = str(data.get("note") or "").strip()
    if len(title) > 200:
        errors.append("Titel: höchstens 200 Zeichen")
    if len(note) > 2000:
        errors.append("Notiz: höchstens 2000 Zeichen")
    fields["title"], fields["note"] = title[:200], note[:2000]

    key = None
    if existing is None:
        key = str(data.get("key") or "").strip() or None
        if key:
            if not (area["custom_keys"] and manager):
                errors.append("Wunsch-Keys darf hier nur vergeben, wer die Area verantwortet")
            elif not store.valid_key(key):
                errors.append(f"Key: nur Buchstaben und Ziffern, höchstens {store.KEY_MAX} Zeichen")
            elif store.find_link(DB, area["slug"], key):
                errors.append("Key: in dieser Area schon vergeben")

    pin = data.get("pin")
    pin = str(pin).strip() if pin is not None else None
    if pin:
        if t == "pixel":
            errors.append("Ein Zählpixel kann keine PIN tragen")
        elif len(pin) < area["pin_min_length"]:
            errors.append(f"PIN: mindestens {area['pin_min_length']} Zeichen")
        elif len(pin) > 64:
            errors.append("PIN: höchstens 64 Zeichen")
    elif existing is None and area["pin_required"] and t != "pixel":
        errors.append("Diese Area verlangt eine PIN")

    now = store.now()
    try:
        expires = parse_when(data.get("expires_at")) if "expires_at" in data else (
            existing["expires_at"] if existing else None)
    except ValueError as e:
        errors.append(str(e))
        expires = None
    if existing is None and expires is None:
        if area["default_ttl_days"] > 0:
            expires = store.iso(now + timedelta(days=area["default_ttl_days"]))
        elif not manager and area["max_ttl_days"] > 0:
            expires = store.iso(now + timedelta(days=area["max_ttl_days"]))
    if not manager and area["max_ttl_days"] > 0:
        limit = store.iso(now + timedelta(days=area["max_ttl_days"]))
        if expires is None or expires > limit:
            errors.append(f"Ablauf: höchstens {area['max_ttl_days']} Tage in dieser Area")
    fields["expires_at"] = expires

    if t == "redirect":
        url = str(data.get("target_url") or (existing or {}).get("target_url") or "").strip()
        if not url:
            errors.append("Ziel-URL fehlt")
        elif not (url.startswith("http://") or url.startswith("https://")) or len(url) > 2048:
            errors.append("Ziel-URL: muss mit http:// oder https:// beginnen, höchstens 2048 Zeichen")
        fields["target_url"] = url
    if t in ("download_once", "download_window"):
        for name in ("valid_from", "valid_until"):
            try:
                fields[name] = parse_when(data.get(name)) if name in data else (
                    existing[name] if existing else None)
            except ValueError as e:
                errors.append(str(e))
        if fields.get("valid_from") and fields.get("valid_until") and fields["valid_from"] >= fields["valid_until"]:
            errors.append("Zeitraum: bis muss nach von liegen")
        if t == "download_once":
            fields["max_downloads"] = 1
        else:
            raw = data.get("max_downloads") if "max_downloads" in data else (existing or {}).get("max_downloads")
            if raw in (None, ""):
                fields["max_downloads"] = None
            else:
                try:
                    fields["max_downloads"] = int(raw)
                    if fields["max_downloads"] < 1:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append("Höchstzahl Downloads: ganze Zahl ab 1, oder leer")
    if existing is not None and "disabled" in data:
        fields["disabled"] = 1 if store._truthy(data["disabled"]) else 0
    return fields, key, pin, errors


def link_json(link, base, area=None):
    out = {k: v for k, v in link.items() if k not in ("key_norm", "pin_failures", "lock_count")}
    out["url"] = f"{base}/{link['area']}/{link['key']}"
    out["type"] = area["type"] if area else None
    out["locked"] = bool(link.get("locked_until") and link["locked_until"] > store.now_iso())
    out["file_present"] = bool(store.file_path(FILES_DIR, link) and os.path.exists(store.file_path(FILES_DIR, link)))
    return out


def area_json(area, who):
    if who.is_admin:
        out = dict(area)
    else:
        keep = ("slug", "type", "type_label", "title", "description", "key_length", "custom_keys",
                "default_ttl_days", "max_ttl_days", "pin_required", "pin_min_length",
                "max_file_mb", "allowed_extensions", "open_for_users", "active")
        out = {k: area[k] for k in keep}
    out["manager"] = who.manages(area)
    return out


class ApiError(Exception):
    def __init__(self, status, message, code="bad_request"):
        super().__init__(message)
        self.status, self.message, self.code = status, message, code


# ----------------------------------------------------------------- Server

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-wegweiser/" + VERSION

    # ---- Werkzeug

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def client_info(self):
        ip = (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
              or self.client_address[0])
        return {"ip": ip, "ua": self.headers.get("User-Agent", ""),
                "referer": self.headers.get("Referer", ""),
                "lang": self.headers.get("Accept-Language", "")}

    def base_url(self):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "localhost"
        scheme = self.headers.get("X-Forwarded-Proto") or ("http" if host.startswith(("localhost", "127.")) else "https")
        return f"{scheme}://{host}"

    def send_bytes(self, status, body, ctype, extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status, doc, extra=()):
        self.send_bytes(status, json.dumps(doc, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", [("Cache-Control", "no-store"), *extra])

    def send_html(self, status, text, extra=()):
        self.send_bytes(status, text.encode("utf-8"), "text/html; charset=utf-8",
                        [("Cache-Control", "no-store"), *extra])

    def redirect(self, location, status=303):
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def api_error(self, status, message, code="bad_request"):
        self.send_json(status, {"error": {"message": message, "code": code}})

    def content_length(self):
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def read_form(self):
        n = self.content_length()
        if n < 0 or n > MAX_FORM:
            return {}
        self.body_done = True
        raw = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        return {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

    def read_json(self):
        n = self.content_length()
        if n < 0 or n > MAX_JSON:
            raise ApiError(413, "Anfrage zu groß")
        self.body_done = True
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            doc = json.loads(raw)
        except ValueError:
            raise ApiError(400, "Kein gültiges JSON")
        if not isinstance(doc, dict):
            raise ApiError(400, "Ein JSON-Objekt wird erwartet")
        return doc

    def drain(self):
        """Body verwerfen, den wir nicht wollen — sonst hängt die Verbindung.
        Aber nur einmal: ein zweites Lesen wartet auf Bytes, die nie kommen."""
        if getattr(self, "body_done", False):
            return
        self.body_done = True
        n = self.content_length()
        while n > 0:
            chunk = self.rfile.read(min(n, 65536))
            if not chunk:
                break
            n -= len(chunk)

    # ---- Verteilen

    def do_GET(self):
        self.dispatch("GET")

    def do_HEAD(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        self.body_done = False
        self.query = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/") if p]
        try:
            if path == "/healthz":
                self.send_json(200, {"status": "ok", "version": VERSION})
            elif parts and parts[0] == "api":
                self.api(method, parts[1:])
            elif parts and parts[0] == "manage":
                self.manage(method, parts[1:])
            elif parts and parts[0] == "admin":
                self.admin(method, parts[1:])
            else:
                self.public(method, parts)
        except ApiError as e:
            self.drain()
            self.api_error(e.status, e.message, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # =============================================================== public

    def public(self, method, parts):
        if len(parts) != 2:
            self.drain()
            self.send_html(404, ui.not_found_page())
            return
        slug, key = parts
        for ext in (".gif", ".png"):
            if key.lower().endswith(ext):
                key = key[:-len(ext)]
        area = store.get_area(DB, slug)
        if area is None or not area["active"]:
            self.drain()
            self.send_html(404, ui.not_found_page())
            return
        client = self.client_info()
        link = store.find_link(DB, slug, key) if store.valid_key(key) else None

        def deny(result, page=None, status=404):
            self.drain()
            store.record_access(DB, area, link, result, client)
            self.send_html(status, page or ui.not_found_page())

        if link is None:
            return deny("not_found")
        ts = store.now_iso()
        t = area["type"]
        downloadish = t in ("download_once", "download_window")
        if link["disabled"]:
            return deny("disabled")
        if link["expires_at"] and link["expires_at"] < ts:
            return deny("expired", ui.gone_page("Nicht mehr verfügbar", "Dieser Link ist abgelaufen.") if downloadish or t == "upload_once" else None,
                        410 if downloadish or t == "upload_once" else 404)
        url = f"/{slug}/{link['key']}"

        if t == "pixel":
            if method != "GET":
                return deny("not_found")
            store.record_access(DB, area, link, "pixel", client)
            self.send_bytes(200, GIF, "image/gif", [("Cache-Control", "no-store, private, max-age=0"),
                                                     ("Pragma", "no-cache"), ("Expires", "0")])
            return

        if t == "redirect":
            if method == "GET":
                if not link["has_pin"]:
                    store.record_access(DB, area, link, "redirected", client)
                    self.redirect(link["target_url"], 302)
                else:
                    store.record_access(DB, area, link, "landing", client)
                    self.send_html(200, ui.landing_redirect(link, url))
                return
            if method == "POST":
                form = self.read_form()
                verdict = store.check_pin(DB, APP_SECRET, link, form.get("pin", ""))
                if verdict == "ok":
                    store.record_access(DB, area, link, "redirected", client)
                    self.redirect(link["target_url"], 302)
                elif verdict == "locked":
                    store.record_access(DB, area, link, "pin_locked", client)
                    self.send_html(429, ui.landing_redirect(link, url, "Zu viele Fehlversuche. Dieser Link ist vorübergehend gesperrt."))
                else:
                    store.record_access(DB, area, link, "pin_wrong", client)
                    self.send_html(403, ui.landing_redirect(link, url, "Die PIN stimmt nicht."))
                return
            return deny("not_found")

        if downloadish:
            path = store.file_path(FILES_DIR, link)
            if not path or not os.path.exists(path):
                if store.exhausted(link):
                    return deny("exhausted", ui.gone_page("Nicht mehr verfügbar", "Diese Datei wurde bereits abgeholt."), 410)
                return deny("not_found")
            if store.exhausted(link):
                return deny("exhausted", ui.gone_page("Nicht mehr verfügbar", "Diese Datei wurde bereits abgeholt."), 410)
            if link["valid_until"] and link["valid_until"] < ts:
                return deny("expired", ui.gone_page("Nicht mehr verfügbar", "Die Frist für diesen Download ist vorbei."), 410)
            if link["valid_from"] and link["valid_from"] > ts:
                return deny("expired", ui.gone_page("Noch nicht verfügbar",
                                                    f"Dieser Download ist ab {ui.fmt_dt(link['valid_from'], TZ)} möglich."), 404)
            if method == "GET":
                store.record_access(DB, area, link, "landing", client)
                self.send_html(200, ui.landing_download(link, area, url, link["has_pin"]))
                return
            if method == "POST":
                form = self.read_form()
                if link["has_pin"]:
                    verdict = store.check_pin(DB, APP_SECRET, link, form.get("pin", ""))
                    if verdict == "locked":
                        store.record_access(DB, area, link, "pin_locked", client)
                        self.send_html(429, ui.landing_download(link, area, url, True, "Zu viele Fehlversuche. Dieser Link ist vorübergehend gesperrt."))
                        return
                    if verdict == "wrong":
                        store.record_access(DB, area, link, "pin_wrong", client)
                        self.send_html(403, ui.landing_download(link, area, url, True, "Die PIN stimmt nicht."))
                        return
                if not store.consume_download(DB, link["id"]):
                    return deny("exhausted", ui.gone_page("Nicht mehr verfügbar", "Diese Datei wurde bereits abgeholt."), 410)
                store.record_access(DB, area, link, "downloaded", client)
                self.stream_file(link, path)
                fresh = store.get_link(DB, link["id"])
                if fresh and store.exhausted(fresh):
                    store.remove_file(FILES_DIR, link["area"], link["id"])
                return
            return deny("not_found")

        if t == "upload_once":
            if link["state"] != "waiting":
                return deny("exhausted", ui.gone_page("Link verbraucht", "Über diesen Link wurde bereits eine Datei hochgeladen."), 410)
            if method == "GET":
                store.record_access(DB, area, link, "landing", client)
                self.send_html(200, ui.landing_upload(link, area, url, link["has_pin"],
                                                      min(area["max_file_mb"], MAX_UPLOAD_MB),
                                                      area["allowed_extensions"]))
                return
            if method == "PUT":
                self.receive_upload(area, link, client)
                return
            return deny("not_found")
        return deny("not_found")

    def receive_upload(self, area, link, client):
        """Der eine Upload. Prüfen, dann den Zustand atomar belegen, dann
        strömen — und bei jedem Fehler den Zustand wieder freigeben."""
        if link["has_pin"]:
            verdict = store.check_pin(DB, APP_SECRET, link, self.headers.get("X-Pin", ""))
            if verdict != "ok":
                self.drain()
                store.record_access(DB, area, link, "pin_locked" if verdict == "locked" else "pin_wrong", client)
                self.api_error(429 if verdict == "locked" else 403,
                               "Zu viele Fehlversuche." if verdict == "locked" else "Die PIN stimmt nicht.", "pin")
                return
        length = self.content_length()
        limit = min(area["max_file_mb"], MAX_UPLOAD_MB) * 1024 * 1024
        name = store.safe_filename(urllib.parse.unquote(self.headers.get("X-File-Name", "")))
        if length <= 0:
            self.drain()
            self.api_error(411, "Content-Length fehlt oder ist null", "length_required")
            return
        if length > limit:
            self.drain()
            self.api_error(413, f"Die Datei ist zu groß (höchstens {limit // (1024 * 1024)} MB).", "too_large")
            return
        if not store.extension_allowed(area, name):
            self.drain()
            self.api_error(415, "Diese Dateiendung ist hier nicht erlaubt.", "extension")
            return
        if not store.consume_upload(DB, link["id"]):
            self.drain()
            store.record_access(DB, area, link, "exhausted", client)
            self.api_error(410, "Über diesen Link wurde bereits eine Datei hochgeladen.", "gone")
            return
        self.body_done = True
        try:
            sha, size = store.write_stream(FILES_DIR, area["slug"], link["id"], self.rfile, length)
        except (IOError, OSError) as e:
            store.release_upload(DB, link["id"])
            log(f"upload abgebrochen {link['area']}/{link['key']}: {e}")
            self.api_error(400, "Die Übertragung war unvollständig.", "incomplete")
            return
        store.set_file(DB, link["id"], name, size, sha,
                       self.headers.get("Content-Type") or "application/octet-stream", received=True)
        store.record_access(DB, area, link, "uploaded", client)
        self.send_json(201, {"received": True, "name": name, "size": size, "sha256": sha})

    def stream_file(self, link, path, inline=False):
        size = os.path.getsize(path)
        name = link["file_name"] or "datei"
        ascii_name = name.encode("ascii", "replace").decode("ascii").replace('"', "")
        quoted = urllib.parse.quote(name)
        disposition = "inline" if inline else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", link.get("content_type") or "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 256)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ================================================================= API

    def who(self):
        who = Who(self.headers)
        if not who.known:
            raise ApiError(401, "Keine Identität von der Plattform erhalten", "unauthenticated")
        store.touch_user(DB, who.id, who.name, who.display)
        return who

    def api(self, method, parts):
        if not parts or parts[0] != "v1":
            raise ApiError(404, "Unbekannter API-Pfad", "not_found")
        who = self.who()
        parts = parts[1:]
        base = self.base_url()
        head = parts[0] if parts else ""

        if head == "me" and method == "GET":
            areas = [area_json(a, who) for a in store.list_areas(DB) if who.may_use(a) or who.manages(a)]
            self.send_json(200, {"id": who.id, "name": who.name, "display_name": who.display,
                                 "roles": sorted(who.roles), "admin": who.is_admin, "areas": areas})
            return

        if head == "areas":
            if len(parts) == 1:
                if method == "GET":
                    areas = store.list_areas(DB)
                    if not who.is_admin:
                        areas = [a for a in areas if who.may_use(a) or who.manages(a)]
                    self.send_json(200, {"areas": [area_json(a, who) for a in areas]})
                    return
                if method == "POST":
                    self.need_admin(who)
                    fields, errors = store.validate_area(self.read_json(), creating=True)
                    if not errors and store.get_area(DB, fields["slug"]):
                        errors.append("slug: gibt es schon")
                    if errors:
                        raise ApiError(422, "; ".join(errors), "validation")
                    area = store.create_area(DB, fields)
                    self.send_json(201, {"area": area_json(area, who)})
                    return
                raise ApiError(405, "Methode nicht erlaubt", "method")
            area = store.get_area(DB, parts[1])
            if area is None:
                raise ApiError(404, "Area unbekannt", "not_found")
            sub = parts[2] if len(parts) > 2 else ""
            if not sub:
                if method == "GET":
                    if not (who.manages(area) or who.may_use(area)):
                        raise ApiError(403, "Keine Berechtigung für diese Area", "forbidden")
                    self.send_json(200, {"area": area_json(area, who)})
                    return
                if method in ("PATCH", "PUT"):
                    self.need_admin(who)
                    fields, errors = store.validate_area(self.read_json(), creating=False)
                    if errors:
                        raise ApiError(422, "; ".join(errors), "validation")
                    self.send_json(200, {"area": area_json(store.update_area(DB, area["slug"], fields), who)})
                    return
                if method == "DELETE":
                    self.need_admin(who)
                    for link in store.list_links(DB, area=area["slug"], include_deleted=True):
                        store.remove_file(FILES_DIR, area["slug"], link["id"])
                    store.delete_area(DB, area["slug"])
                    self.send_json(200, {"deleted": area["slug"]})
                    return
                raise ApiError(405, "Methode nicht erlaubt", "method")
            if not who.manages(area):
                raise ApiError(403, "Nur wer die Area verantwortet, sieht ihre Zahlen", "forbidden")
            if sub == "stats" and method == "GET":
                self.send_json(200, {"area": area["slug"], "stats": store.stats(DB, area=area["slug"], days=self.days())})
                return
            if sub == "accesses" and method == "GET":
                self.send_accesses(store.accesses(DB, area=area["slug"], limit=self.query.get("limit", 500),
                                                  offset=self.query.get("offset", 0)), f"{area['slug']}-zugriffe")
                return
            raise ApiError(404, "Unbekannter API-Pfad", "not_found")

        if head == "links":
            if len(parts) == 1:
                if method == "GET":
                    slug = self.query.get("area")
                    if slug:
                        area = store.get_area(DB, slug)
                        if area is None:
                            raise ApiError(404, "Area unbekannt", "not_found")
                        links = store.list_links(DB, area=slug) if who.manages(area) else store.list_links(DB, who.id, slug)
                    else:
                        links = store.list_links(DB) if who.is_admin and self.query.get("all") else store.list_links(DB, who.id)
                    areas = {a["slug"]: a for a in store.list_areas(DB)}
                    self.send_json(200, {"links": [link_json(l, base, areas.get(l["area"])) for l in links]})
                    return
                if method == "POST":
                    data = self.read_json()
                    area = store.get_area(DB, str(data.get("area") or ""))
                    if area is None:
                        raise ApiError(404, "Area unbekannt", "not_found")
                    if not who.may_use(area):
                        raise ApiError(403, "In dieser Area dürfen Sie keine Links anlegen", "forbidden")
                    fields, key, pin, errors = prepare_link(who, area, data)
                    if errors:
                        raise ApiError(422, "; ".join(errors), "validation")
                    link = store.create_link(DB, APP_SECRET, area, who.id, who.display or who.name, fields, key, pin)
                    self.send_json(201, {"link": link_json(link, base, area)})
                    return
                raise ApiError(405, "Methode nicht erlaubt", "method")
            link = store.get_link(DB, parts[1])
            if link is None or link["deleted_at"]:
                raise ApiError(404, "Link unbekannt", "not_found")
            area = store.get_area(DB, link["area"])
            if area is None or not who.may_see(link, area):
                raise ApiError(404, "Link unbekannt", "not_found")
            sub = parts[2] if len(parts) > 2 else ""
            if not sub:
                if method == "GET":
                    self.send_json(200, {"link": link_json(link, base, area)})
                    return
                if method in ("PATCH", "PUT"):
                    data = self.read_json()
                    fields, _, pin, errors = prepare_link(who, area, data, existing=link)
                    if errors:
                        raise ApiError(422, "; ".join(errors), "validation")
                    if "pin" in data:
                        store.set_pin(DB, APP_SECRET, link["id"], pin or None)
                    link = store.update_link(DB, link["id"], fields)
                    self.send_json(200, {"link": link_json(link, base, area)})
                    return
                if method == "DELETE":
                    self.delete_link(link)
                    self.send_json(200, {"deleted": link["id"]})
                    return
                raise ApiError(405, "Methode nicht erlaubt", "method")
            if sub == "file":
                if method == "PUT":
                    self.attach_file(area, link)
                    return
                if method == "GET":
                    path = store.file_path(FILES_DIR, link)
                    if not path or not os.path.exists(path):
                        raise ApiError(404, "Keine Datei vorhanden", "not_found")
                    self.stream_file(link, path)
                    return
                if method == "DELETE":
                    store.remove_file(FILES_DIR, link["area"], link["id"])
                    store.clear_file(DB, link["id"])
                    if area["type"] == "upload_once":
                        store.update_link(DB, link["id"], {"state": "waiting", "received_at": None})
                    self.send_json(200, {"deleted": True})
                    return
                raise ApiError(405, "Methode nicht erlaubt", "method")
            if sub == "stats" and method == "GET":
                self.send_json(200, {"link": link["id"], "stats": store.stats(DB, link_id=link["id"], days=self.days())})
                return
            if sub == "accesses" and method == "GET":
                self.send_accesses(store.accesses(DB, link_id=link["id"], limit=self.query.get("limit", 500),
                                                  offset=self.query.get("offset", 0)),
                                   f"{link['area']}-{link['key']}-zugriffe")
                return
            raise ApiError(404, "Unbekannter API-Pfad", "not_found")
        raise ApiError(404, "Unbekannter API-Pfad", "not_found")

    def need_admin(self, who):
        if not who.is_admin:
            raise ApiError(403, "Nur für die Rolle admin", "forbidden")

    def days(self):
        try:
            return max(1, min(3650, int(self.query.get("days", 30))))
        except ValueError:
            return 30

    def send_accesses(self, rows, name):
        if self.query.get("format") == "csv":
            buf = io.StringIO()
            w = csv.writer(buf, delimiter=";")
            w.writerow(["ts", "area", "link_id", "result", "ip", "ua_class", "browser", "os", "lang", "referer", "ua"])
            for r in rows:
                w.writerow([r["ts"], r["area"], r["link_id"] or "", r["result"], r["ip"] or "", r["ua_class"] or "",
                            r["browser"] or "", r["os"] or "", r["lang"] or "", r["referer"] or "", r["ua"] or ""])
            self.send_bytes(200, ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                            [("Content-Disposition", f'attachment; filename="{name}.csv"'), ("Cache-Control", "no-store")])
            return
        self.send_json(200, {"accesses": rows})

    def attach_file(self, area, link):
        """Datei an einen Download-Link hängen (Besitzer oder Verantwortlicher)."""
        if area["type"] not in ("download_once", "download_window"):
            self.drain()
            raise ApiError(409, "Nur Download-Links tragen eine Datei", "type")
        length = self.content_length()
        limit = min(area["max_file_mb"], MAX_UPLOAD_MB) * 1024 * 1024
        name = store.safe_filename(urllib.parse.unquote(self.headers.get("X-File-Name", "")))
        if length <= 0:
            self.drain()
            raise ApiError(411, "Content-Length fehlt oder ist null", "length_required")
        if length > limit:
            self.drain()
            raise ApiError(413, f"Die Datei ist zu groß (höchstens {limit // (1024 * 1024)} MB).", "too_large")
        if not store.extension_allowed(area, name):
            self.drain()
            raise ApiError(415, "Diese Dateiendung ist in der Area nicht erlaubt.", "extension")
        self.body_done = True
        try:
            sha, size = store.write_stream(FILES_DIR, area["slug"], link["id"], self.rfile, length)
        except (IOError, OSError) as e:
            raise ApiError(400, f"Die Übertragung war unvollständig: {e}", "incomplete")
        store.set_file(DB, link["id"], name, size, sha, self.headers.get("Content-Type") or "application/octet-stream")
        self.send_json(201, {"link": link_json(store.get_link(DB, link["id"]), self.base_url(), area)})

    def delete_link(self, link):
        store.remove_file(FILES_DIR, link["area"], link["id"])
        store.soft_delete_link(DB, link["id"])

    # ============================================================== manage

    def page_who(self):
        who = Who(self.headers)
        if not who.known:
            self.drain()
            self.send_html(401, ui.public_page("Keine Identität",
                                               '<div class="card"><h1>Keine Identität</h1><p>Die Plattform hat keine '
                                               'Benutzerangaben mitgegeben. Diese Seite gehört hinter eine angemeldete Route.</p></div>'))
            return None
        store.touch_user(DB, who.id, who.name, who.display)
        return who

    def nav(self, who, on):
        items = [("/manage", "Meine Links", on == "manage")]
        if who.is_keyuser:
            items.append(("/manage/areas", "Areas", on == "areas"))
        if who.is_admin:
            items.append(("/admin", "Verwaltung", on == "admin"))
        return items

    def manage(self, method, parts):
        who = self.page_who()
        if who is None:
            return
        base = self.base_url()
        if not parts:
            if method == "GET":
                self.send_html(200, self.manage_page(who, base))
                return
            self.drain()
            self.send_html(405, ui.page("Nicht erlaubt", "<p>Methode nicht erlaubt.</p>", who, VERSION))
            return
        if parts[0] == "links":
            if len(parts) == 1 and method == "POST":
                self.manage_create(who, base)
                return
            if len(parts) == 2:
                link = store.get_link(DB, parts[1])
                area = store.get_area(DB, link["area"]) if link and not link["deleted_at"] else None
                if link is None or area is None or not who.may_see(link, area):
                    self.drain()
                    self.send_html(404, ui.page("Nicht gefunden", '<div class="card"><h1>Diesen Link gibt es nicht</h1></div>', who, VERSION, self.nav(who, "manage")))
                    return
                if method == "GET":
                    self.send_html(200, self.link_page(who, link, area, base, notice=self.query.get("ok", "")))
                    return
                if method == "POST":
                    self.manage_link_action(who, link, area, base)
                    return
        if parts[0] == "areas" and method == "GET":
            self.send_html(200, self.areas_page(who, base, parts[1] if len(parts) > 1 else ""))
            return
        self.drain()
        self.send_html(404, ui.page("Nicht gefunden", '<div class="card"><h1>Hier ist nichts</h1></div>', who, VERSION, self.nav(who, "manage")))

    def usable_areas(self, who):
        return [a for a in store.list_areas(DB) if who.may_use(a)]

    def new_link_form(self, who, areas, data=None):
        data = data or {}
        if not areas:
            return ('<div class="card attention"><h2>Noch keine Area, in der Sie Links anlegen dürfen</h2>'
                    '<p class="hint">Areas legt die Verwaltung an. Fragen Sie dort nach.</p></div>')
        opts = "".join(
            f'<option value="{esc(a["slug"])}" data-type="{esc(a["type"])}" data-custom="{1 if a["custom_keys"] and who.manages(a) else 0}" '
            f'data-pin="{1 if a["pin_required"] else 0}" {"selected" if data.get("area") == a["slug"] else ""}>'
            f'{esc(a["slug"])} — {esc(a["title"] or a["type_label"])} ({esc(a["type_label"])})</option>'
            for a in areas)
        return f"""<div class="card">
  <h2>Neuer Link</h2>
  <form class="grid" method="post" action="/manage/links" id="newlink">
    <div><label>Area</label><select name="area" id="area">{opts}</select></div>
    <div data-for="custom"><label>Wunsch-Key (leer = erzeugen)</label><input type="text" name="key" maxlength="8" value="{esc(data.get("key", ""))}"></div>
    <div class="wide"><label>Titel (nur für Sie sichtbar)</label><input type="text" name="title" maxlength="200" value="{esc(data.get("title", ""))}"></div>
    <div class="wide" data-for="redirect"><label>Ziel-URL</label><input type="url" name="target_url" value="{esc(data.get("target_url", ""))}" placeholder="https://…"></div>
    <div data-for="pin"><label>PIN (optional)</label><input type="password" name="pin" autocomplete="new-password"></div>
    <div><label>Gültig bis (leer = Vorgabe der Area)</label><input type="datetime-local" name="expires_at" value="{esc(data.get("expires_at", ""))}"></div>
    <div data-for="download"><label>Download ab</label><input type="datetime-local" name="valid_from" value="{esc(data.get("valid_from", ""))}"></div>
    <div data-for="download"><label>Download bis</label><input type="datetime-local" name="valid_until" value="{esc(data.get("valid_until", ""))}"></div>
    <div data-for="download_window"><label>Höchstzahl Downloads (leer = unbegrenzt)</label><input type="number" name="max_downloads" min="1" value="{esc(data.get("max_downloads", ""))}"></div>
    <div class="wide"><label>Notiz</label><input type="text" name="note" maxlength="2000" value="{esc(data.get("note", ""))}"></div>
    <div><button type="submit">Anlegen</button></div>
  </form>
  <p class="hint" style="margin-top:.8rem">Die Datei für einen Download-Link hängen Sie nach dem Anlegen an.</p>
</div>
<script>
(function(){{
  var sel=document.getElementById('area'),form=document.getElementById('newlink');
  function show(){{
    var o=sel.options[sel.selectedIndex]; if(!o) return;
    var t=o.getAttribute('data-type'),custom=o.getAttribute('data-custom')==='1';
    form.querySelectorAll('[data-for]').forEach(function(el){{
      var f=el.getAttribute('data-for'),on=false;
      if(f==='custom') on=custom;
      else if(f==='pin') on=t!=='pixel';
      else if(f==='download') on=(t==='download_once'||t==='download_window');
      else on=(f===t);
      el.style.display=on?'':'none';
    }});
  }}
  sel.addEventListener('change',show); show();
}})();
</script>"""

    def links_table(self, who, links, areas, base, show_owner=False):
        if not links:
            return '<p class="muted">Noch keine Links.</p>'
        ts = store.now_iso()
        rows = []
        for l in links:
            a = areas.get(l["area"])
            rows.append(
                f'<tr><td><a href="/manage/links/{esc(l["id"])}"><code>{esc(l["area"])}/{esc(l["key"])}</code></a></td>'
                f'<td>{esc(a["type_label"] if a else l["area"])}</td>'
                f'<td>{esc(l["title"] or "")}</td>'
                f'<td>{ui.status_badge(l, ts)}</td>'
                f'<td class="num">{l["hit_count"]}</td>'
                + (f'<td>{esc(l["created_by_name"])}</td>' if show_owner else "")
                + f'<td class="muted">{ui.fmt_dt(l["created_at"], TZ)}</td></tr>')
        owner = "<th>Von</th>" if show_owner else ""
        return (f'<table><tr><th>Link</th><th>Typ</th><th>Titel</th><th>Status</th><th class="num">Zugriffe</th>{owner}<th>Angelegt</th></tr>'
                f'{"".join(rows)}</table>')

    def manage_page(self, who, base, errors=(), data=None, notice=""):
        areas = {a["slug"]: a for a in store.list_areas(DB)}
        usable = self.usable_areas(who)
        filt = self.query.get("area", "")
        links = store.list_links(DB, who.id, filt or None)
        chips = "".join(f'<a class="btn quiet" href="/manage?area={esc(s)}">{esc(s)}</a>' for s in sorted({l["area"] for l in store.list_links(DB, who.id)}))
        body = ui.notice_card(notice) + ui.errors_card(errors) + self.new_link_form(who, usable, data)
        body += f"""<div class="card">
  <h2>Meine Links{f" in {esc(filt)}" if filt else ""}</h2>
  <div class="row" style="margin-bottom:.8rem"><a class="btn quiet" href="/manage">alle</a>{chips}</div>
  {self.links_table(who, links, areas, base)}
</div>"""
        return ui.page("Meine Links", body, who, VERSION, self.nav(who, "manage"))

    def manage_create(self, who, base):
        form = self.read_form()
        area = store.get_area(DB, form.get("area", ""))
        if area is None or not who.may_use(area):
            self.send_html(403, self.manage_page(who, base, ["In dieser Area dürfen Sie keine Links anlegen."], form))
            return
        fields, key, pin, errors = prepare_link(who, area, form)
        if errors:
            self.send_html(422, self.manage_page(who, base, errors, form))
            return
        link = store.create_link(DB, APP_SECRET, area, who.id, who.display or who.name, fields, key, pin)
        self.redirect(f"/manage/links/{link['id']}?ok=" + urllib.parse.quote("Link angelegt."))

    def link_page(self, who, link, area, base, notice="", errors=()):
        ts = store.now_iso()
        url = f"{base}/{link['area']}/{link['key']}"
        t = area["type"]
        path = store.file_path(FILES_DIR, link)
        present = bool(path and os.path.exists(path))
        rows = [("Typ", esc(area["type_label"])), ("Status", ui.status_badge(link, ts)),
                ("Angelegt", f'{ui.fmt_dt(link["created_at"], TZ)} von {esc(link["created_by_name"])}'),
                ("Gültig bis", ui.fmt_dt(link["expires_at"], TZ) or '<span class="muted">unbegrenzt</span>'),
                ("PIN", "ja" if link["has_pin"] else "nein"),
                ("Zugriffe", f'{link["hit_count"]}' + (f', zuletzt {ui.fmt_dt(link["last_hit_at"], TZ)}' if link["last_hit_at"] else ""))]
        if t == "redirect":
            rows.append(("Ziel", f'<a href="{esc(link["target_url"] or "")}" rel="noopener">{esc(link["target_url"] or "")}</a>'))
        if t in ("download_once", "download_window", "upload_once"):
            if link["file_name"]:
                rows.append(("Datei", f'{esc(link["file_name"])} · {ui.fmt_size(link["file_size"])} · <code>{esc(link["file_sha256"] or "")}</code>'
                             + ("" if present else ' <span class="badge off">Bytes entfernt</span>')))
            else:
                rows.append(("Datei", '<span class="muted">noch keine</span>'))
        if t in ("download_once", "download_window"):
            rows.append(("Downloads", f'{link["download_count"]} / {link["max_downloads"] if link["max_downloads"] is not None else "unbegrenzt"}'))
            rows.append(("Zeitraum", f'{ui.fmt_dt(link["valid_from"], TZ) or "sofort"} bis {ui.fmt_dt(link["valid_until"], TZ) or "offen"}'))
        if t == "upload_once":
            rows.append(("Eingang", ui.fmt_dt(link["received_at"], TZ) or '<span class="muted">wartet</span>'))
        if link["note"]:
            rows.append(("Notiz", esc(link["note"])))
        details = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in rows)

        body = ui.notice_card(notice) + ui.errors_card(errors)
        body += f"""<div class="card">
  <h1>{esc(link["title"] or (link["area"] + "/" + link["key"]))}</h1>
  <p class="linkurl"><a href="{esc(url)}" target="_blank" rel="noopener" id="theurl">{esc(url)}</a>
    <button type="button" class="quiet" onclick="navigator.clipboard.writeText(document.getElementById('theurl').textContent).then(function(){{document.getElementById('copied').textContent='kopiert';}})">Kopieren</button>
    <span id="copied" class="muted"></span></p>
  {"<p class='hint'>Als Bild einbetten: <code>&lt;img src=&quot;" + esc(url) + ".gif&quot; width=&quot;1&quot; height=&quot;1&quot; alt=&quot;&quot;&gt;</code></p>" if t == "pixel" else ""}
  <table>{details}</table>
</div>"""

        # Datei anhängen / holen
        if t in ("download_once", "download_window"):
            body += f"""<div class="card">
  <h2>Datei</h2>
  <p class="hint">Höchstens {min(area["max_file_mb"], MAX_UPLOAD_MB)} MB{(" · erlaubt: " + esc(area["allowed_extensions"].replace(",", ", "))) if area["allowed_extensions"] else ""}. Eine neue Datei ersetzt die alte.</p>
  <div class="row"><input type="file" id="file"><button type="button" id="go" class="quiet">Anhängen</button></div>
  <progress id="prog" value="0" max="100" hidden></progress><p id="msg" class="muted"></p>
  {("<p><a class='btn quiet' href='/api/v1/links/" + esc(link["id"]) + "/file'>Datei herunterladen</a></p>") if present else ""}
</div>
<script>
(function(){{
  var f=document.getElementById('file'),b=document.getElementById('go'),p=document.getElementById('prog'),m=document.getElementById('msg');
  b.onclick=function(){{
    if(!f.files.length){{m.textContent='Bitte zuerst eine Datei wählen.';return;}}
    var file=f.files[0],x=new XMLHttpRequest(); x.open('PUT','/api/v1/links/{esc(link["id"])}/file');
    x.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    x.upload.onprogress=function(e){{if(e.lengthComputable){{p.hidden=false;p.value=100*e.loaded/e.total;}}}};
    x.onload=function(){{ if(x.status===201) location.reload(); else {{ var d={{}}; try{{d=JSON.parse(x.responseText)}}catch(e){{}} m.textContent=(d.error&&d.error.message)||('Fehler '+x.status); b.disabled=false; }} }};
    b.disabled=true; x.send(file);
  }};
}})();
</script>"""
        if t == "upload_once" and present:
            body += f"""<div class="card okbox"><h2>Eingegangene Datei</h2>
  <p><a class="btn" href="/api/v1/links/{esc(link["id"])}/file">{esc(link["file_name"])} herunterladen</a></p></div>"""

        # Aktionen
        toggle = ("enable", "Wieder aktivieren") if link["disabled"] else ("disable", "Deaktivieren")
        edit_fields = f"""<div class="wide"><label>Titel</label><input type="text" name="title" maxlength="200" value="{esc(link["title"])}"></div>
    <div class="wide"><label>Notiz</label><input type="text" name="note" maxlength="2000" value="{esc(link["note"])}"></div>
    <div><label>Gültig bis</label><input type="datetime-local" name="expires_at" value="{ui.dt_input(link["expires_at"], TZ)}"></div>"""
        if t == "redirect":
            edit_fields += f'<div class="wide"><label>Ziel-URL</label><input type="url" name="target_url" value="{esc(link["target_url"] or "")}"></div>'
        if t in ("download_once", "download_window"):
            edit_fields += (f'<div><label>Download ab</label><input type="datetime-local" name="valid_from" value="{ui.dt_input(link["valid_from"], TZ)}"></div>'
                            f'<div><label>Download bis</label><input type="datetime-local" name="valid_until" value="{ui.dt_input(link["valid_until"], TZ)}"></div>')
        if t == "download_window":
            edit_fields += f'<div><label>Höchstzahl Downloads</label><input type="number" name="max_downloads" min="1" value="{esc(str(link["max_downloads"] or ""))}"></div>'
        pin_form = "" if t == "pixel" else f"""<form method="post" class="row" style="margin-top:1rem">
    <input type="hidden" name="_action" value="pin">
    <div><label>PIN setzen (leer = PIN entfernen)</label><input type="password" name="pin" autocomplete="new-password"></div>
    <button type="submit" class="quiet">PIN speichern</button></form>"""
        body += f"""<div class="card">
  <h2>Bearbeiten</h2>
  <form method="post" class="grid"><input type="hidden" name="_action" value="edit">{edit_fields}<div><button type="submit">Speichern</button></div></form>
  {pin_form}
  <div class="row" style="margin-top:1rem">
    <form method="post"><input type="hidden" name="_action" value="{toggle[0]}"><button type="submit" class="quiet">{toggle[1]}</button></form>
    <form method="post" onsubmit="return confirm('Diesen Link endgültig löschen? Die Datei geht mit.')"><input type="hidden" name="_action" value="delete"><button type="submit" class="danger">Löschen</button></form>
  </div>
</div>"""
        body += self.stats_cards(store.stats(DB, link_id=link["id"], days=30), f"/api/v1/links/{link['id']}/accesses?format=csv&limit=5000",
                                 store.accesses(DB, link_id=link["id"], limit=50), area)
        return ui.page(link["title"] or f"{link['area']}/{link['key']}", body, who, VERSION, self.nav(who, "manage"))

    def stats_cards(self, st, csv_url, recent, area):
        level = area["log_level"]
        hint = {"count": "Diese Area zählt nur; Einzelzugriffe werden nicht gespeichert.",
                "minimal": "Diese Area speichert Zeitpunkt und Ergebnis.",
                "standard": "Diese Area speichert gekürzte Adressen und Geräteklassen.",
                "full": "Diese Area speichert alles, was der Client mitschickt."}[level]
        if level == "count":
            return f'<div class="card"><h2>Statistik</h2><p class="hint">{esc(hint)}</p></div>'
        body = f"""<div class="card">
  <h2>Statistik</h2>
  <p class="hint">{esc(hint)} Aufbewahrung {area["log_retention_days"]} Tage. <a href="{esc(csv_url)}">Als CSV</a></p>
  <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(16rem,1fr));gap:1rem">
    <div><h2>Ergebnisse</h2>{ui.bars(st["by_result"], label_map=ui.RESULT_LABELS)}</div>
    <div><h2>Letzte 30 Tage</h2>{ui.day_table(st["by_day"])}</div>"""
        if level in ("standard", "full"):
            body += f"""<div><h2>Geräte</h2>{ui.bars(st["by_class"], label_map={"desktop": "Desktop", "mobile": "Mobil", "bot": "Automat/Vorschau", "unknown": "unbekannt"})}</div>
    <div><h2>Browser</h2>{ui.bars(st["by_browser"])}</div>
    <div><h2>Betriebssystem</h2>{ui.bars(st["by_os"])}</div>
    <div><h2>Sprache</h2>{ui.bars(st["by_lang"])}</div>
    <div><h2>Herkunft (Referer)</h2>{ui.bars({r["referer"]: r["n"] for r in st["top_referers"]})}</div>"""
        body += "</div></div>"
        if recent:
            rows = "".join(
                f'<tr><td>{ui.fmt_dt(r["ts"], TZ)}</td><td>{esc(ui.RESULT_LABELS.get(r["result"], r["result"]))}</td>'
                f'<td>{esc(r["ip"] or "")}</td><td>{esc(r["ua_class"] or "")} {esc(r["browser"] or "")} {esc(r["os"] or "")}</td>'
                f'<td>{esc(r["lang"] or "")}</td><td>{esc(r["referer"] or "")}</td></tr>' for r in recent)
            body += f'<div class="card"><h2>Letzte Zugriffe</h2><table><tr><th>Zeit</th><th>Ergebnis</th><th>Adresse</th><th>Gerät</th><th>Sprache</th><th>Herkunft</th></tr>{rows}</table></div>'
        return body

    def manage_link_action(self, who, link, area, base):
        form = self.read_form()
        action = form.get("_action", "")
        if action == "delete":
            self.delete_link(link)
            self.redirect("/manage?ok=" + urllib.parse.quote("Link gelöscht."))
            return
        if action in ("disable", "enable"):
            store.update_link(DB, link["id"], {"disabled": 1 if action == "disable" else 0})
            self.redirect(f"/manage/links/{link['id']}?ok=" + urllib.parse.quote("Gespeichert."))
            return
        if action == "pin":
            pin = form.get("pin", "").strip()
            if pin and len(pin) < area["pin_min_length"]:
                self.send_html(422, self.link_page(who, link, area, base, errors=[f"PIN: mindestens {area['pin_min_length']} Zeichen"]))
                return
            store.set_pin(DB, APP_SECRET, link["id"], pin or None)
            self.redirect(f"/manage/links/{link['id']}?ok=" + urllib.parse.quote("PIN gesetzt." if pin else "PIN entfernt."))
            return
        if action == "edit":
            fields, _, _, errors = prepare_link(who, area, {k: v for k, v in form.items() if k != "pin"}, existing=link)
            if errors:
                self.send_html(422, self.link_page(who, link, area, base, errors=errors))
                return
            store.update_link(DB, link["id"], fields)
            self.redirect(f"/manage/links/{link['id']}?ok=" + urllib.parse.quote("Gespeichert."))
            return
        self.send_html(400, self.link_page(who, link, area, base, errors=["Unbekannte Aktion."]))

    def areas_page(self, who, base, slug):
        """Keyuser-Sicht: die verantworteten Areas, alle Links darin, Zahlen."""
        mine = [a for a in store.list_areas(DB) if who.manages(a)]
        if not mine:
            body = '<div class="card"><h2>Sie verantworten keine Area</h2><p class="hint">Die Verwaltung trägt Verantwortliche je Area ein.</p></div>'
            return ui.page("Areas", body, who, VERSION, self.nav(who, "areas"))
        areas = {a["slug"]: a for a in store.list_areas(DB)}
        if slug and slug in areas and who.manages(areas[slug]):
            a = areas[slug]
            links = store.list_links(DB, area=slug)
            body = f"""<div class="card"><h1>{esc(a["slug"])} — {esc(a["title"] or a["type_label"])}</h1>
  <p class="hint">{esc(a["type_label"])} · {esc(a["description"])}</p>
  <p class="muted">Erzeugte Keys {a["key_length"]} Zeichen · Wunsch-Keys {"ja" if a["custom_keys"] else "nein"} · PIN {"Pflicht" if a["pin_required"] else "optional"} ·
     Vorgabe {a["default_ttl_days"] or "unbegrenzt"} Tage · Höchstens {a["max_ttl_days"] or "unbegrenzt"} Tage für Benutzer ·
     Speichertiefe {esc(ui.esc(store.LOG_LABELS[a["log_level"]]))}, {a["log_retention_days"]} Tage</p></div>
<div class="card"><h2>Alle Links dieser Area</h2>{self.links_table(who, links, areas, base, show_owner=True)}</div>"""
            body += self.stats_cards(store.stats(DB, area=slug), f"/api/v1/areas/{slug}/accesses?format=csv&limit=5000",
                                     store.accesses(DB, area=slug, limit=50), a)
            return ui.page(f"Area {slug}", body, who, VERSION, self.nav(who, "areas"))
        counts = store.area_counts(DB)
        rows = "".join(
            f'<tr><td><a href="/manage/areas/{esc(a["slug"])}"><code>{esc(a["slug"])}</code></a></td><td>{esc(a["type_label"])}</td>'
            f'<td>{esc(a["title"])}</td><td>{"aktiv" if a["active"] else "inaktiv"}</td>'
            f'<td class="num">{counts.get(a["slug"], {}).get("links", 0)}</td><td class="num">{counts.get(a["slug"], {}).get("hits", 0)}</td></tr>'
            for a in mine)
        body = f'<div class="card"><h2>Areas, die Sie verantworten</h2><table><tr><th>Area</th><th>Typ</th><th>Titel</th><th>Status</th><th class="num">Links</th><th class="num">Zugriffe</th></tr>{rows}</table></div>'
        return ui.page("Areas", body, who, VERSION, self.nav(who, "areas"))

    # =============================================================== admin

    def admin(self, method, parts):
        who = self.page_who()
        if who is None:
            return
        if not who.is_admin:
            self.drain()
            self.send_html(403, ui.page("Nur Verwaltung", '<div class="card"><h1>Nur für die Rolle admin</h1></div>', who, VERSION, self.nav(who, "admin")))
            return
        base = self.base_url()
        if not parts and method == "GET":
            self.send_html(200, self.admin_page(who, notice=self.query.get("ok", "")))
            return
        if parts and parts[0] == "areas":
            if len(parts) == 1 and method == "POST":
                form = self.read_form()
                # Kästchen, die nicht angekreuzt sind, kommen im Formular nicht vor
                for name in ("open_for_users", "active", "custom_keys", "pin_required"):
                    form.setdefault(name, "0")
                fields, errors = store.validate_area(form, creating=True)
                if not errors and store.get_area(DB, fields["slug"]):
                    errors.append("slug: gibt es schon")
                if errors:
                    self.send_html(422, self.admin_page(who, errors=errors, data=form))
                    return
                area = store.create_area(DB, fields)
                self.redirect(f"/admin/areas/{area['slug']}?ok=" + urllib.parse.quote("Area angelegt."))
                return
            if len(parts) == 2:
                area = store.get_area(DB, parts[1])
                if area is None:
                    self.drain()
                    self.send_html(404, ui.page("Nicht gefunden", '<div class="card"><h1>Diese Area gibt es nicht</h1></div>', who, VERSION, self.nav(who, "admin")))
                    return
                if method == "GET":
                    self.send_html(200, self.admin_area_page(who, area, base, notice=self.query.get("ok", "")))
                    return
                if method == "POST":
                    form = self.read_form()
                    if form.get("_action") == "delete":
                        for link in store.list_links(DB, area=area["slug"], include_deleted=True):
                            store.remove_file(FILES_DIR, area["slug"], link["id"])
                        store.delete_area(DB, area["slug"])
                        self.redirect("/admin?ok=" + urllib.parse.quote(f"Area {area['slug']} gelöscht."))
                        return
                    # Kästchen, die nicht angekreuzt sind, kommen im Formular nicht vor
                    for name in ("open_for_users", "active", "custom_keys", "pin_required"):
                        form.setdefault(name, "0")
                    fields, errors = store.validate_area(form, creating=False)
                    if errors:
                        self.send_html(422, self.admin_area_page(who, area, base, errors=errors))
                        return
                    store.update_area(DB, area["slug"], fields)
                    self.redirect(f"/admin/areas/{area['slug']}?ok=" + urllib.parse.quote("Gespeichert."))
                    return
        self.drain()
        self.send_html(404, ui.page("Nicht gefunden", '<div class="card"><h1>Hier ist nichts</h1></div>', who, VERSION, self.nav(who, "admin")))

    def area_form(self, a, creating):
        """Ein Formular für beide Fälle. `a` ist die Area oder ein dict mit Eingaben."""
        g = lambda k, d="": esc(str(a.get(k, d) if a.get(k) is not None else d))
        chk = lambda k, d: "checked" if (a.get(k, d) if k in a else d) in (True, 1, "1", "on") else ""
        types = "".join(f'<option value="{t}" {"selected" if a.get("type") == t else ""}>{esc(store.TYPE_LABELS[t])}</option>' for t in store.TYPES)
        levels = "".join(f'<option value="{l}" {"selected" if a.get("log_level", "standard") == l else ""}>{l} — {esc(store.LOG_LABELS[l])}</option>' for l in store.LOG_LEVELS)
        head = (f'<div><label>Slug (Pfadsegment)</label><input type="text" name="slug" maxlength="16" required value="{g("slug")}"></div>'
                f'<div><label>Typ</label><select name="type">{types}</select></div>') if creating else \
               f'<div><label>Slug</label><input type="text" value="{g("slug")}" disabled></div><div><label>Typ</label><input type="text" value="{esc(store.TYPE_LABELS.get(a.get("type", ""), ""))}" disabled></div>'
        resp = a.get("responsible", [])
        resp = ", ".join(resp) if isinstance(resp, list) else str(resp)
        return f"""{head}
    <div class="wide"><label>Titel</label><input type="text" name="title" maxlength="200" value="{g("title")}"></div>
    <div class="wide"><label>Beschreibung</label><input type="text" name="description" maxlength="2000" value="{g("description")}"></div>
    <div class="wide"><label>Verantwortliche (Benutzernamen, mit Komma; brauchen die Rolle keyuser)</label><input type="text" name="responsible" value="{esc(resp)}"></div>
    <div><label class="check"><input type="checkbox" name="active" value="1" {chk("active", 1)}> Area aktiv</label></div>
    <div><label class="check"><input type="checkbox" name="open_for_users" value="1" {chk("open_for_users", 1)}> Benutzer dürfen Links anlegen</label></div>
    <div><label class="check"><input type="checkbox" name="custom_keys" value="1" {chk("custom_keys", 1)}> Wunsch-Keys (für Verantwortliche)</label></div>
    <div><label class="check"><input type="checkbox" name="pin_required" value="1" {chk("pin_required", 0)}> PIN ist Pflicht</label></div>
    <div><label>Länge erzeugter Keys (4–8)</label><input type="number" name="key_length" min="4" max="8" value="{g("key_length", 6)}"></div>
    <div><label>PIN-Mindestlänge</label><input type="number" name="pin_min_length" min="3" max="12" value="{g("pin_min_length", 4)}"></div>
    <div><label>Vorgabe Lebensdauer in Tagen (0 = unbegrenzt)</label><input type="number" name="default_ttl_days" min="0" value="{g("default_ttl_days", 0)}"></div>
    <div><label>Höchstens Tage für Benutzer (0 = unbegrenzt)</label><input type="number" name="max_ttl_days" min="0" value="{g("max_ttl_days", 0)}"></div>
    <div><label>Höchstgröße je Datei in MB (Instanz erlaubt {MAX_UPLOAD_MB})</label><input type="number" name="max_file_mb" min="1" value="{g("max_file_mb", 100)}"></div>
    <div><label>Erlaubte Endungen (leer = alle)</label><input type="text" name="allowed_extensions" placeholder="pdf, zip" value="{g("allowed_extensions")}"></div>
    <div class="wide"><label>Speichertiefe der Zugriffe</label><select name="log_level">{levels}</select></div>
    <div><label>Aufbewahrung der Zugriffe in Tagen</label><input type="number" name="log_retention_days" min="1" value="{g("log_retention_days", 90)}"></div>"""

    def admin_page(self, who, errors=(), data=None, notice=""):
        counts = store.area_counts(DB)
        rows = "".join(
            f'<tr><td><a href="/admin/areas/{esc(a["slug"])}"><code>{esc(a["slug"])}</code></a></td><td>{esc(a["type_label"])}</td>'
            f'<td>{esc(a["title"])}</td><td>{esc(", ".join(a["responsible"]))}</td>'
            f'<td>{"aktiv" if a["active"] else "<span class=muted>inaktiv</span>"}{"" if a["open_for_users"] else " · nur Verantwortliche"}</td>'
            f'<td>{esc(a["log_level"])}/{a["log_retention_days"]} T</td>'
            f'<td class="num">{counts.get(a["slug"], {}).get("links", 0)}</td><td class="num">{counts.get(a["slug"], {}).get("hits", 0)}</td></tr>'
            for a in store.list_areas(DB))
        table = (f'<table><tr><th>Area</th><th>Typ</th><th>Titel</th><th>Verantwortliche</th><th>Status</th><th>Speichern</th><th class="num">Links</th><th class="num">Zugriffe</th></tr>{rows}</table>'
                 if rows else '<p class="muted">Noch keine Area. Ohne Area kann niemand einen Link anlegen.</p>')
        body = ui.notice_card(notice) + ui.errors_card(errors)
        body += f'<div class="card"><h2>Areas</h2>{table}</div>'
        body += f"""<div class="card"><h2>Neue Area</h2>
  <p class="hint">Der Typ steht danach fest. Der Slug ist das erste Pfadsegment der öffentlichen Adresse; reserviert sind {esc(", ".join(sorted(store.RESERVED_SLUGS)))}.</p>
  <form class="grid" method="post" action="/admin/areas">{self.area_form(data or {}, creating=True)}<div><button type="submit">Anlegen</button></div></form></div>"""
        body += f"""<div class="card"><h2>REST-API</h2>
  <p class="hint">Basis <code>{esc(self.base_url())}/api/v1</code>, Anmeldung über die Sitzung oder einen API-Schlüssel der Plattform (<code>Authorization: Bearer oaapk_…</code>, RFC-0027).
  Endpunkte: <code>me</code>, <code>areas</code>, <code>areas/&lt;slug&gt;/stats|accesses</code>, <code>links?area=</code>, <code>links/&lt;id&gt;</code>, <code>links/&lt;id&gt;/file|stats|accesses</code>.
  Rollen wie in der Oberfläche. Details in der README der App.</p></div>"""
        return ui.page("Verwaltung", body, who, VERSION, self.nav(who, "admin"))

    def admin_area_page(self, who, area, base, errors=(), notice=""):
        counts = store.area_counts(DB).get(area["slug"], {})
        body = ui.notice_card(notice) + ui.errors_card(errors)
        body += f"""<div class="card"><h1>Area {esc(area["slug"])}</h1>
  <p class="muted">{esc(area["type_label"])} · {counts.get("links", 0)} Links · {counts.get("hits", 0)} Zugriffe · öffentlich unter <code>{esc(base)}/{esc(area["slug"])}/&lt;key&gt;</code></p>
  <form class="grid" method="post">{self.area_form(area, creating=False)}<div><button type="submit">Speichern</button></div></form>
</div>
<div class="card"><h2>Löschen</h2><p class="hint">Nimmt alle Links, Dateien und Zugriffszeilen dieser Area mit. Das ist endgültig.</p>
  <form method="post" onsubmit="return confirm('Area {esc(area["slug"])} mit allen Links löschen?')"><input type="hidden" name="_action" value="delete"><button type="submit" class="danger">Area löschen</button></form></div>"""
        body += self.stats_cards(store.stats(DB, area=area["slug"]), f"/api/v1/areas/{area['slug']}/accesses?format=csv&limit=5000",
                                 store.accesses(DB, area=area["slug"], limit=30), area)
        return ui.page(f"Area {area['slug']}", body, who, VERSION, self.nav(who, "admin"))


# ---------------------------------------------------------------- Aufräumen

def sweeper(interval=3600):
    while True:
        time.sleep(interval)
        try:
            store.sweep(DB, FILES_DIR, log)
        except Exception as e:  # der Lauf darf den Dienst nie mitreißen
            log(f"sweep fehlgeschlagen: {e}")


def cli(argv):
    if argv[:1] == ["sweep"]:
        store.sweep(DB, FILES_DIR, log)
        return 0
    print("Aufrufe: sweep")
    return 2


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    store.sweep(DB, FILES_DIR, log)
    threading.Thread(target=sweeper, daemon=True).start()
    log(f"Wegweiser {VERSION} auf Port {PORT} — Daten unter {DATA_DIR}, "
        f"höchstens {MAX_UPLOAD_MB} MB je Datei, Zeitzone {TZ}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
