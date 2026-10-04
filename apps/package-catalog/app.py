"""Paketkatalog 0.1 — freigegebene ZIP-Pakete mit Versionsverlauf (RFC-0050).

Der Betreiber lädt Pakete hoch, gibt Versionen frei und kann jede
gespeicherte ZIP wieder herunterladen. Der Katalog führt nie Code aus und
entpackt nie mehr als das Manifest. Was hier "freigegeben" ist, schreibt
die App als Liste (`/data/store.json`); der Knoten liest sie direkt aus
diesem Verzeichnis und macht sie zur Store-Quelle -- ohne Netz, ohne
Schlüssel, ohne dass der Katalog etwas freigibt, was der Knoten nicht
selbst nachprüft (Größe, SHA-256, Id und Version im Manifest).

Ein Weg, eine Rolle: alles ist `admin` (Manifest), und die App prüft es
noch einmal. Ein Paket ist Code, den ein Knoten baut und ausführt -- wer
hier hochlädt, bestimmt, was Mandanten installieren können.
"""
import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import catalog as cat
import multipart
import zipinfo

VERSION = "0.1.0"
PORT = 8000
DATA_DIR = os.environ.get("CATALOG_DATA_DIR", "/data")
try:
    MAX_MB = max(1, min(256, int(os.environ.get("CATALOG_MAX_PACKAGE_MB") or 256)))
except ValueError:
    MAX_MB = 256
MAX_BYTES = MAX_MB * 1024 * 1024
MAX_FORM = 16 * 1024
ALLOWED_ROLES = {"admin", "server_admin"}

esc = html.escape
CATALOG = None


STYLE = """<style>
  :root{--blue:#1e3a8a;--bg:#f4f6fa;--text:#1f2937;--muted:#6b7280;
        --border:#e5e7eb;--ok:#15803d;--err:#b91c1c}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;margin:0;
       background:var(--bg);color:var(--text)}
  header{background:var(--blue);color:#fff;padding:.7rem 1.2rem;font-weight:600}
  main{max-width:60rem;margin:1.4rem auto;padding:0 1.2rem}
  .card{background:#fff;border:1px solid var(--border);border-radius:.6rem;
        padding:1.2rem;margin-bottom:1.1rem}
  h1{font-size:1.3rem;margin:.1rem 0 .8rem} h2{font-size:1.02rem;margin:0 0 .7rem}
  table{border-collapse:collapse;width:100%;font-size:.92rem}
  th,td{text-align:left;padding:.35rem .5rem;border-bottom:1px solid var(--border);
        vertical-align:top}
  .muted{color:var(--muted)} .ok{color:var(--ok)} .err{color:var(--err)}
  .badge{font-size:.72rem;padding:.1rem .5rem;border-radius:1rem;background:#dbeafe;
         color:#1e3a8a}
  .badge.on{background:#dcfce7;color:#166534}
  button,.btn{padding:.35rem .8rem;border:0;border-radius:.35rem;background:var(--blue);
        color:#fff;cursor:pointer;font-size:.88rem;text-decoration:none}
  button.quiet{background:#e5e7eb;color:var(--text)}
  button.danger{background:var(--err)}
  input[type=text],input[type=file]{padding:.35rem;border:1px solid var(--border);
        border-radius:.3rem;max-width:100%}
  form.inline{display:inline}
  code{background:#f3f4f6;padding:.05rem .3rem;border-radius:.2rem}
</style>"""


def page(body, msg="", ok=True):
    note = (f'<div class="card"><p class="{"ok" if ok else "err"}" style="margin:0">'
            f"{esc(msg)}</p></div>") if msg else ""
    return ("<!doctype html><html lang=de><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Paketkatalog</title>{STYLE}<header>Paketkatalog</header>"
            f"<main>{note}{body}</main></html>")


def render_index():
    rows = []
    for app_id, name, versions in CATALOG.apps():
        for i, r in enumerate(versions):
            rel = r["released"]
            ver, aid = esc(r["version"]), esc(app_id)
            q = f'<input type=hidden name=app value="{aid}"><input type=hidden name=version value="{ver}">'
            actions = [
                f'<a class="btn quiet" href="/download/{quote(app_id)}/{quote(r["version"])}">Laden</a>',
                f'<form class="inline" method=post action="/release">{q}'
                f'<input type=hidden name=released value="{0 if rel else 1}">'
                f'<button class="{"quiet" if rel else ""}">'
                f'{"Zurückziehen" if rel else "Freigeben"}</button></form>',
                f'<form class="inline" method=post action="/delete">{q}'
                f'<input type=text name=reason placeholder="{"Grund (nötig)" if rel else "Grund"}" size=14 '
                f'{"required" if rel else ""}>'
                f'<button class="danger">Löschen</button></form>']
            rows.append(
                f"<tr><td>{esc(name) if i == 0 else ''}"
                f"{'<br><span class=muted><code>' + aid + '</code></span>' if i == 0 else ''}</td>"
                f"<td><strong>{ver}</strong> "
                f'<span class="badge {"on" if rel else ""}">'
                f'{"freigegeben" if rel else "nicht freigegeben"}</span></td>'
                f'<td class=muted>{r["size"] / 1048576:.1f} MB<br>'
                f'<code>{esc(r["sha256"][:12])}</code></td>'
                f'<td class=muted>{esc(r["uploaded_at"][:10])}<br>{esc(r["uploaded_by"])}</td>'
                f'<td>{esc(r.get("note", ""))}</td><td>{" ".join(actions)}</td></tr>')
    table = ("<table><tr><th>App</th><th>Version</th><th>Paket</th><th>Hochgeladen</th>"
             "<th>Notiz</th><th></th></tr>" + "".join(rows) + "</table>") if rows else \
            '<p class="muted">Noch kein Paket. Lade unten die erste ZIP hoch.</p>'
    body = (
        "<h1>Pakete</h1>"
        f'<div class="card">{table}</div>'
        '<div class="card"><h2>Neue Version hochladen</h2>'
        '<form method=post action="/upload" enctype="multipart/form-data">'
        '<p><input type=file name=file accept=".zip,application/zip" required></p>'
        '<p><input type=text name=note placeholder="Notiz (optional)" size=40></p>'
        f'<p><button>Hochladen</button> <span class=muted>bis {MAX_MB} MB. '
        'Eine Version wird nie überschrieben; hochgeladen heißt noch nicht freigegeben.'
        '</span></p></form></div>'
        '<div class="card"><h2>Als Store-Quelle des Knotens</h2>'
        '<p style="margin:0">Der Knoten liest die freigegebenen Pakete direkt aus dem '
        'Speicher dieser Instanz. Einmal eintragen, auf dem Knoten:</p>'
        '<p><code>sudo oaap store add-catalog &lt;Name dieser Instanz&gt;</code></p>'
        '<p class="muted" style="margin:0">Danach erscheinen die freigegebenen Apps im Store '
        'jedes Mandanten-Administrators; Installieren und Aktualisieren gehen wie bei jeder '
        'anderen Quelle. Der Knoten prüft jedes Paket vor dem Entpacken noch einmal.</p></div>')
    return page(body)


class Handler(BaseHTTPRequestHandler):
    server_version = f"package-catalog/{VERSION}"

    def log_message(self, fmt, *args):     # no request lines: URIs may carry names
        pass

    # -- plumbing -----------------------------------------------------------
    def send(self, status, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def back(self, msg, ok=True):
        self.send(303, "", extra={"Location": "/?" + ("ok=1&" if ok else "")
                                  + "m=" + quote(msg)})

    def user(self):
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles", "") or "").split(",")
                 if r.strip()}
        return (self.headers.get("X-OAAP-User", "") or "?") if roles & ALLOWED_ROLES else None

    def cross_site(self):
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "same-site", "none"):
            return True
        origin = self.headers.get("Origin")
        return bool(origin) and urlparse(origin).netloc != self.headers.get("Host", "")

    # -- routes ---------------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        if path == "/healthz":
            return self.send(200, json.dumps({"status": "ok", "version": VERSION}),
                             "application/json")
        who = self.user()
        if who is None:
            return self.send(403, page("<h1>Kein Zugriff</h1><p>Der Paketkatalog ist "
                                       "Sache des Betreibers (Rolle admin).</p>"))
        if path == "/":
            q = parse_qs(u.query)
            msg = (q.get("m") or [""])[0][:300]
            body = render_index()
            if msg:
                body = body.replace("<main>", "<main>" + (
                    f'<div class="card"><p class="{"ok" if q.get("ok") else "err"}" '
                    f'style="margin:0">{esc(msg)}</p></div>'), 1)
            return self.send(200, body)
        if path.startswith("/download/"):
            parts = [unquote(p) for p in path.split("/")[2:]]
            if len(parts) != 2 or CATALOG.get(*parts) is None:
                return self.send(404, page("<h1>Nicht gefunden</h1>"))
            fp = CATALOG.path_of(*parts)
            try:
                f = open(fp, "rb")
            except OSError:
                return self.send(404, page("<h1>Die Datei fehlt</h1>"))
            with f:
                size = os.fstat(f.fileno()).st_size
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Length", str(size))
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{parts[0]}-{parts[1]}.zip"')
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                for block in iter(lambda: f.read(1 << 20), b""):
                    self.wfile.write(block)
            return
        self.send(404, page("<h1>Nicht gefunden</h1>"))

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        who = self.user()
        if who is None:
            return self.send(403, page("<h1>Kein Zugriff</h1>"))
        if self.cross_site():
            return self.send(403, page("<h1>Anfrage von fremder Seite abgelehnt</h1>"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if path == "/upload":
            return self.upload(who, length)
        if length > MAX_FORM:
            return self.back("Das Formular ist zu groß.", False)
        form = {k: v[0] for k, v in parse_qs(
            self.rfile.read(length).decode("utf-8", "replace")).items()}
        app, version = form.get("app", ""), form.get("version", "")
        try:
            if path == "/release":
                CATALOG.set_released(app, version, form.get("released") == "1", who)
                return self.back(("Freigegeben: " if form.get("released") == "1"
                                  else "Zurückgezogen: ") + f"{app} {version}")
            if path == "/delete":
                CATALOG.delete(app, version, form.get("reason", ""), who)
                return self.back(f"Gelöscht: {app} {version}")
        except cat.CatalogError as e:
            return self.back(str(e), False)
        self.send(404, page("<h1>Nicht gefunden</h1>"))

    def upload(self, who, length):
        files = {}
        try:
            fields, files = multipart.parse(
                self.rfile, self.headers.get("Content-Type", ""), length,
                MAX_BYTES + 1024 * 1024, tmpdir=CATALOG.tmp_dir())
            up = files.get("file")
            if not up:
                return self.back("Es kam keine Datei an.", False)
            rec = CATALOG.add(up["path"], MAX_BYTES, who, fields.get("note", ""))
            files.pop("file", None)          # moved into the store
            return self.back(f"Aufgenommen: {rec['version']} — noch nicht freigegeben.")
        except multipart.MultipartError as e:
            return self.back(str(e), False)
        except (zipinfo.PackageError, cat.CatalogError) as e:
            return self.back(str(e), False)
        finally:
            multipart.cleanup(files)


def make_server(data_dir=None, port=PORT):
    global CATALOG
    CATALOG = cat.Catalog(data_dir or DATA_DIR)
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def main():
    srv = make_server()
    print(f"package-catalog {VERSION} on :{PORT}, data {DATA_DIR}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
