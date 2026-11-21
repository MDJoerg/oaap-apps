"""Paketkatalog 0.5 — freigegebene ZIP-Pakete mit Versionsverlauf (RFC-0050),
seit 0.2 auch als Spiegel eines Marketplace-Feeds (RFC-0077 §7.2, upstream.py).

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

Seit 0.5 (RFC-0118): der Rahmen der Plattform (appframe), die Pakete als
Listenbericht, die Handlungen je Paket auf seiner Objektseite
(/paket/<id>), Hilfe unter docs/ und eine lesende MCP-Tür (/mcp).
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import appframe as af
import catalog as cat
import mcpdoor
import multipart
import upstream
import zipinfo

APP_NAME = "Paketkatalog"
VERSION = "0.5.0"
PORT = 8000
DATA_DIR = os.environ.get("CATALOG_DATA_DIR", "/data")
try:
    MAX_MB = max(1, min(256, int(os.environ.get("CATALOG_MAX_PACKAGE_MB") or 256)))
except ValueError:
    MAX_MB = 256
MAX_BYTES = MAX_MB * 1024 * 1024
MAX_FORM = 16 * 1024
ALLOWED_ROLES = {"admin", "server_admin"}

esc = af.esc
CATALOG = None
MIRROR = None

# Nur, was dieser App eigen ist; Farben aus dem Kit.
STYLE = """
.pc-inline{display:flex;flex-wrap:wrap;gap:var(--oaap-space-2);align-items:end;margin:0}
.pc-inline label{display:grid;gap:2px;font-size:.9em}
.pc-sub{display:block;font-size:.85em;color:var(--oaap-color-text-muted)}
.pc-sel td{background:var(--oaap-badge-info-bg)}
"""


# ------------------------------------------------------------- Rahmen
def tabs():
    t = [("/", "Übersicht"), ("/neu", "Neue Version")]
    if MIRROR and MIRROR.enabled:
        t.append(("/spiegel", "Spiegel"))
    t.append(("/einrichtung", "Einrichtung"))
    return t


def page(title, body, active="/", msg="", ok=True, obj=None, obj_title=None):
    note = af.msg(msg, error=not ok) if msg else ""
    return af.page(title, note + body, app=APP_NAME, version=VERSION,
                   product="package-catalog", tabs=tabs(), active=active,
                   obj=obj, obj_title=obj_title, style=STYLE)


def plain(title, text=""):
    return page(title, af.pagehead(title) + (f"<p>{esc(text)}</p>" if text else ""), active=None)


# ------------------------------------------------------------- Sichten
def summary(app_id, name, versions):
    """Eine Zeile je App für Liste und MCP."""
    newest = versions[0] if versions else {}
    released = [r["version"] for r in versions if r.get("released")]
    return {"id": app_id, "name": name,
            "newest": newest.get("version", ""),
            "released": released[0] if released else "",
            "versions": len(versions),
            "unreleased": sum(1 for r in versions if not r.get("released")),
            "visible_to": CATALOG.visible_to(app_id),
            "uploaded_at": (newest.get("uploaded_at") or "")[:10],
            "from_node": any(r.get("origin") for r in versions),
            "withdrawn_upstream": any(r.get("withdrawn_upstream") for r in versions)}


def all_summaries():
    return [summary(*a) for a in CATALOG.apps()]


STATUS_CHOICES = [("frei", "mit freigegebener Version"),
                  ("offen", "wartet auf Freigabe"),
                  ("ohne", "nichts freigegeben")]


def status_ok(s, want):
    if want == "frei":
        return bool(s["released"])
    if want == "offen":
        return s["unreleased"] > 0
    if want == "ohne":
        return not s["released"]
    return True


def status_badge(s):
    out = []
    if s["released"]:
        out.append(af.badge(f"freigegeben {s['released']}", "ok"))
    else:
        out.append(af.badge("nichts freigegeben"))
    if s["unreleased"] and s["newest"] != s["released"]:
        out.append(af.badge(f"{s['newest']} wartet", "warn"))
    if s["withdrawn_upstream"]:
        out.append(af.badge("im Marktplatz zurückgerufen", "err"))
    return " ".join(out)


def vis_text(labels):
    return "nur " + ", ".join(labels) if labels else "alle Mandanten"


def render_index(params, msg="", ok=True):
    query = af.Query(params, filters=("status",), default_sort="name")
    rows = [s for s in all_summaries() if status_ok(s, query.filters.get("status"))]
    columns = [("App", "name", lambda s: f"{s['name']} {s['id']}", ""),
               ("Neueste Version", "newest", lambda s: cat.version_key(s["newest"]), ""),
               ("Status", "", None, ""),
               ("Sichtbar für", "", None, "oaap-minor"),
               ("Hochgeladen", "uploaded", lambda s: s["uploaded_at"], "oaap-minor")]

    def row(s):
        href = "/paket/" + quote(s["id"])
        return (f'<tr><td><a href="{esc(href)}"><strong>{esc(s["name"])}</strong></a>'
                f'<span class="pc-sub"><code>{esc(s["id"])}</code></span></td>'
                f'<td>{esc(s["newest"])} <span class="oaap-muted">({s["versions"]} '
                f'{"Version" if s["versions"] == 1 else "Versionen"})</span></td>'
                f'<td>{status_badge(s)}</td>'
                f'<td class="oaap-minor">{esc(vis_text(s["visible_to"]))}</td>'
                f'<td class="oaap-minor">{esc(s["uploaded_at"])}'
                + (" " + af.badge("vom Knoten", "info") if s["from_node"] else "") + '</td></tr>')

    # Die Suche geht über Name und Kennung; list_report sucht danach noch
    # einmal über die Sortierschlüssel -- die enthalten Name und Kennung,
    # also ändert das nichts mehr.
    rows = [s for s in rows if query.matches(s["name"], s["id"])]
    report = af.list_report(query, rows, columns, row, base="/?", noun="Apps",
                            empty_text='Noch kein Paket. <a href="/neu">Neue Version</a> '
                                       'lädt die erste ZIP hoch.',
                            choices=[("status", "Status", STATUS_CHOICES)],
                            placeholder="Name oder Kennung")
    body = af.pagehead("Pakete", '<a class="oaap-btn primary" href="/neu">Neue Version</a>') + report
    return page("Übersicht", body, "/", msg, ok)


def render_package(app_id, params, msg="", ok=True):
    entry = next((a for a in CATALOG.apps() if a[0] == app_id), None)
    if not entry:
        return None
    _id, name, versions = entry
    s = summary(*entry)
    want = (params.get("version") or [""])[0]
    sel = next((r for r in versions if r["version"] == want), versions[0])
    aid = esc(app_id)

    head = (af.back("/", "Zurück zur Liste")
            + f'<div class="oaap-objhead"><h1>{esc(name)}</h1>'
            + af.facts([("Kennung", f"<code>{aid}</code>"),
                        ("Freigegeben", esc(s["released"]) or "keine Version"),
                        ("Versionen", str(len(versions))),
                        ("Sichtbar für", esc(vis_text(s["visible_to"])))])
            + "</div>")

    vrows = []
    for r in versions:
        origin = r.get("origin") or {}
        cls = ' class="pc-sel"' if r is sel else ""
        href = f"/paket/{quote(app_id)}?version={quote(r['version'])}"
        vrows.append(
            f'<tr{cls}><td><a href="{esc(href)}"><strong>{esc(r["version"])}</strong></a></td>'
            f'<td>{af.badge("freigegeben", "ok") if r["released"] else af.badge("nicht freigegeben")}'
            + (" " + af.badge("im Marktplatz zurückgerufen", "err") if r.get("withdrawn_upstream") else "")
            + f'</td><td class="oaap-minor">{r["size"] / 1048576:.1f} MB</td>'
            f'<td>{esc(r["uploaded_at"][:10])} <span class="pc-sub">{esc(r["uploaded_by"])}'
            + (" · vom Knoten übernommen" if origin else "") + '</span></td>'
            f'<td class="oaap-minor">{esc(r.get("note", ""))}</td></tr>')
    vtable = af.table("<th>Version</th><th>Status</th><th class=\"oaap-minor\">Größe</th>"
                      "<th>Hochgeladen</th><th class=\"oaap-minor\">Notiz</th>", vrows)

    ver = esc(sel["version"])
    hidden = f'<input type="hidden" name="app" value="{aid}"><input type="hidden" name="version" value="{ver}">'
    rel = sel["released"]
    facts = [("Prüfsumme (SHA-256)", f'<code>{esc(sel["sha256"])}</code>'),
             ("Größe", f'{sel["size"] / 1048576:.1f} MB'),
             ("Hochgeladen", esc(f'{sel["uploaded_at"][:16]} von {sel["uploaded_by"]}')),
             ("Freigegeben", esc(f'{(sel.get("released_at") or "")[:16]} von {sel.get("released_by", "")}')
              if rel else ""),
             ("Beschreibung", esc(sel.get("description") or "")),
             ("Notiz", esc(sel.get("note") or ""))]
    if sel.get("withdrawn_upstream"):
        facts.append(("Im Marktplatz zurückgerufen", esc(sel["withdrawn_upstream"])))
    actions = (
        f'<a class="oaap-btn" href="/download/{quote(app_id)}/{quote(sel["version"])}">Laden</a> '
        f'<form class="pc-inline" style="display:inline-flex" method="post" action="/release">{hidden}'
        f'<input type="hidden" name="released" value="{0 if rel else 1}">'
        f'<button class="{"" if rel else "primary"}">{"Zurückziehen" if rel else "Freigeben"}</button></form>')
    delete = (
        f'<form class="pc-inline" method="post" action="/delete">{hidden}'
        f'<label>Grund{" (nötig, die Version ist freigegeben)" if rel else " (freiwillig)"}'
        f'<input type="text" name="reason" size="30"{" required" if rel else ""}></label>'
        '<button class="danger">Löschen</button></form>')
    version_card = af.card(f"Version {sel['version']}",
                           af.facts(facts) + f'<p style="margin-top:1rem">{actions}</p>'
                           '<h3>Löschen</h3>' + delete
                           + '<p class="oaap-muted">Eine gelöschte Version ist weg; '
                             'eine Version wird nie überschrieben.</p>')
    vis = CATALOG.visible_to(app_id)
    vis_card = af.card("Sichtbar für",
                       f'<form class="pc-inline" method="post" action="/visible">'
                       f'<input type="hidden" name="app" value="{aid}">'
                       f'<label>Mandanten-Kennungen, durch Komma getrennt; leer = alle'
                       f'<input type="text" name="visible_to" size="30" value="{esc(", ".join(vis))}" '
                       'placeholder="alle"></label><button>Speichern</button></form>'
                       f'<p class="oaap-muted">Heute: {esc(vis_text(vis))}. Die App erscheint nur im '
                       'Store der genannten Mandanten.</p>')
    body = head + af.card("Versionen", vtable) + version_card + vis_card
    return page(name, body, None, msg, ok, obj=f"Paket:{app_id}", obj_title=name)


def render_upload(msg="", ok=True):
    body = (af.pagehead("Neue Version hochladen")
            + af.card("", '<form class="oaap-form" method="post" action="/upload" enctype="multipart/form-data">'
                      '<label>ZIP-Paket<input type="file" name="file" accept=".zip,application/zip" required></label>'
                      '<label>Notiz (freiwillig)<input type="text" name="note"></label>'
                      '<div><button class="primary">Hochladen</button></div></form>'
                      f'<p class="oaap-muted">Bis {MAX_MB} MB. Eine Version wird nie überschrieben; '
                      'hochgeladen heißt noch nicht freigegeben.</p>'))
    return page("Neue Version", body, "/neu", msg, ok)


def render_setup(msg="", ok=True):
    body = (af.pagehead("Einrichtung")
            + af.card("Als Store-Quelle des Knotens",
                      '<p>Der Knoten liest die freigegebenen Pakete direkt aus dem Speicher dieser '
                      'Instanz. Einmal eintragen, auf dem Knoten:</p>'
                      '<p><code>sudo oaap store add-catalog &lt;Name dieser Instanz&gt;</code></p>'
                      '<p class="oaap-muted">Danach erscheinen die freigegebenen Apps im Store jedes '
                      'Mandanten-Administrators; Installieren und Aktualisieren gehen wie bei jeder '
                      'anderen Quelle. Der Knoten prüft jedes Paket vor dem Entpacken noch einmal.</p>')
            + af.card("Ein getestetes Paket übernehmen",
                      '<p>Ein getestetes Paket kommt ohne Herunter- und Hochladen hierher: im Portal auf '
                      'der Instanz, Reiter Deployment, „In den Katalog übernehmen“ (oder '
                      '<code>sudo oaap app catalog-put &lt;Instanz&gt;</code>). Es landet als '
                      '<strong>nicht freigegeben</strong>; freigeben bleibt hier Deine Entscheidung. '
                      'Mit „Sichtbar für“ auf der Seite eines Pakets erscheint eine App nur im Store '
                      'der genannten Mandanten.</p>'))
    return page("Einrichtung", body, "/einrichtung", msg, ok)


def mirror_card():
    """Der Spiegel eines Marktplatzes -- nur, wenn eine Destination zugeordnet ist."""
    if not MIRROR or not MIRROR.enabled:
        return ""
    st = MIRROR.status()
    err = st.get("last_error") or ""
    taken = ", ".join(st.get("taken") or []) or "noch keine"
    inner = (af.facts([("Feed", esc(st.get("feed_name") or "?")),
                       ("Kanal", f"<code>{esc(MIRROR.channel)}</code>"),
                       ("Freigabe", "beim Eintreffen" if MIRROR.mode == "auto" else "durch den Betreiber"),
                       ("Abstand", f"alle {MIRROR.interval} min"),
                       ("Zuletzt abgerufen", esc(st.get("last_poll") or "nie")),
                       ("Zuletzt erfolgreich", esc(st.get("last_ok") or "nie")),
                       ("Übernommen", esc(taken))])
             + (af.msg(err, error=True) if err else "")
             + '<form method="post" action="/upstream/poll" style="margin-top:1rem">'
               '<button class="primary">Jetzt abrufen</button></form>')
    return af.card("Spiegel eines Marktplatzes", inner) + follow_block(st)


def follow_block(st):
    """Aus Zwischenablage hinzufügen -- ein Link aus dem Marktplatz."""
    offered = set(st.get("offered") or [])
    follows = MIRROR.follows()
    rows = [f'<tr><td><code>{esc(f["id"])}</code></td><td>'
            + (af.badge("beziehbar", "ok") if f["id"] in offered else af.badge("(noch) nicht beziehbar"))
            + f'</td><td class="oaap-minor">{esc(f.get("added") or "")}</td></tr>' for f in follows]
    scope = ("nur die Folgen-Liste" if MIRROR.scope == "followed"
             else "alles Beziehbare")
    unfollow = ""
    if follows:
        opts = "".join(f'<option value="{esc(f["id"])}">{esc(f["id"])}</option>' for f in follows)
        unfollow = ('<form class="pc-inline" method="post" action="/upstream/unfollow" style="margin-top:.6rem">'
                    f'<label>App<select name="app">{opts}</select></label>'
                    '<button>Nicht mehr folgen</button></form>')
    return af.card("Aus Zwischenablage hinzufügen",
                   f'<p class="oaap-muted">Gespiegelt wird: {esc(scope)}. Den Link einer Detailseite '
                   'im Marktplatz („Teilen“ → „Link kopieren“) hier einfügen. Der Katalog ruft den Link nicht '
                   'auf; er prüft nur, dass er zum angebundenen Marktplatz gehört.</p>'
                   '<form class="pc-inline" method="post" action="/upstream/follow">'
                   '<label>Link<input type="text" name="link" size="50" '
                   'placeholder="https://market.example/assets/kennung" required></label>'
                   '<button>Folgen</button></form>'
                   + (af.table("<th>App</th><th>Stand</th><th class=\"oaap-minor\">Seit</th>", rows)
                      if rows else "") + unfollow)


def render_mirror(msg="", ok=True):
    body = af.pagehead("Spiegel") + (mirror_card() or af.empty(
        "Kein Spiegel eingerichtet. Ordne im Portal der Instanz die Destination „upstream“ zu."))
    return page("Spiegel", body, "/spiegel", msg, ok)


# --------------------------------------------------------------- MCP
def allowed(who):
    return bool(set(who.get("roles") or []) & ALLOWED_ROLES)


door = mcpdoor.Door(
    "oaap-package-catalog", VERSION,
    "Der Paketkatalog des Betreibers: welche App-Pakete in welchen Versionen da sind, "
    "was freigegeben ist und für wen es sichtbar ist. Nur lesen.")


def _guard(who):
    if not allowed(who):
        raise mcpdoor.Refused("Der Paketkatalog ist Sache des Betreibers (Rolle admin).")


@door.tool("package_catalog_list", "Pakete auflisten",
           "Listet die Apps im Paketkatalog mit neuester und freigegebener Version, "
           "Sichtbarkeit und offenen Freigaben. Optional nach Wort oder Status filtern.",
           {"q": {"type": "string", "description": "Wort in Name oder Kennung"},
            "status": {"type": "string", "enum": ["frei", "offen", "ohne"],
                       "description": "frei = mit freigegebener Version, offen = wartet auf Freigabe, "
                                      "ohne = nichts freigegeben"}})
def _mcp_list(args, who):
    _guard(who)
    q = af.Query({"q": str(args.get("q") or "")})
    want = str(args.get("status") or "")
    hits = [s for s in all_summaries() if status_ok(s, want) and q.matches(s["name"], s["id"])]
    return {"count": len(hits), "apps": hits[:200]}


@door.tool("package_catalog_read", "Paket lesen",
           "Liest eine App im Paketkatalog mit allen Versionen: Status, Größe, Prüfsumme, "
           "wer wann hochgeladen und freigegeben hat, Notiz.",
           {"app": {"type": "string", "description": "Kennung der App, z. B. wegweiser"}},
           required=("app",))
def _mcp_read(args, who):
    _guard(who)
    app_id = str(args.get("app") or "").strip()
    entry = next((a for a in CATALOG.apps() if a[0] == app_id), None)
    if not entry:
        raise mcpdoor.Refused(f"Die App „{app_id}“ steht nicht im Katalog.")
    keep = ("version", "released", "released_at", "released_by", "size", "sha256",
            "description", "type", "uploaded_by", "uploaded_at", "note", "withdrawn_upstream")
    out = summary(*entry)
    out["versions"] = [{k: r[k] for k in keep if k in r} | {"from_node": bool(r.get("origin"))}
                       for r in entry[2]]
    return out


door.prompt("package_catalog_review", "Offene Freigaben prüfen",
            "Welche hochgeladenen Versionen warten auf Freigabe?",
            "Prüfe im Paketkatalog (package_catalog_list mit status offen, dann "
            "package_catalog_read je App), welche Versionen noch nicht freigegeben sind. "
            "Nenne je App die wartende Version, wer sie wann hochgeladen hat und die Notiz, "
            "und was heute freigegeben ist. Gib nichts frei; das bleibt beim Betreiber.")
door.prompt("package_catalog_app", "Stand einer App",
            "Versionen, Freigabe und Sichtbarkeit einer App im Katalog",
            "Beschreibe den Stand der App {app} im Paketkatalog (package_catalog_read): "
            "welche Versionen es gibt, welche freigegeben ist, für welche Mandanten sie sichtbar ist.",
            arguments=("app",))


# ------------------------------------------------------------ Handler
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

    def back(self, msg, ok=True, to="/"):
        self.send(303, "", extra={"Location": to + "?" + ("ok=1&" if ok else "")
                                  + "m=" + quote(msg)})

    def back_to(self, app_id, msg, ok=True):
        """Zurück auf die Seite des Pakets, wenn es es noch gibt, sonst zur Liste."""
        to = "/paket/" + quote(app_id) if app_id and CATALOG.state["apps"].get(app_id) else "/"
        return self.back(msg, ok, to)

    def drain(self):
        """Einen kleinen, abgelehnten Rumpf noch lesen: sonst bricht Windows die
        Verbindung ab, bevor der Client die Ablehnung liest."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if 0 < n <= MAX_FORM:
            self.rfile.read(n)
        else:
            self.close_connection = True

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
        if path == "/mcp":
            return door.serve(self)
        who = self.user()
        if who is None:
            return self.send(403, plain("Kein Zugriff", "Der Paketkatalog ist Sache des "
                                        "Betreibers (Rolle admin)."))
        q = parse_qs(u.query)
        msg = (q.get("m") or [""])[0][:300]
        ok = bool(q.get("ok"))
        if path == "/":
            CATALOG.ingest_inbox(MAX_BYTES)     # was der Knoten gerade gebracht hat
            return self.send(200, render_index(q, msg, ok))
        if path == "/neu":
            return self.send(200, render_upload(msg, ok))
        if path == "/einrichtung":
            return self.send(200, render_setup(msg, ok))
        if path == "/spiegel":
            return self.send(200, render_mirror(msg, ok))
        if path.startswith("/paket/"):
            app_id = unquote(path[len("/paket/"):])
            html_ = render_package(app_id, q, msg, ok) if "/" not in app_id else None
            if html_ is None:
                return self.send(404, plain("Nicht gefunden", "Diese App steht nicht im Katalog."))
            return self.send(200, html_)
        if path.startswith("/download/"):
            parts = [unquote(p) for p in path.split("/")[2:]]
            if len(parts) != 2 or CATALOG.get(*parts) is None:
                return self.send(404, plain("Nicht gefunden"))
            fp = CATALOG.path_of(*parts)
            try:
                f = open(fp, "rb")
            except OSError:
                return self.send(404, plain("Die Datei fehlt"))
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
        self.send(404, plain("Nicht gefunden"))

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/")
        if path == "/mcp":
            return door.serve(self)
        who = self.user()
        if who is None:
            self.drain()
            return self.send(403, plain("Kein Zugriff"))
        if self.cross_site():
            self.drain()
            return self.send(403, plain("Anfrage von fremder Seite abgelehnt"))
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if path == "/upload":
            return self.upload(who, length)
        if path == "/upstream/poll":
            if not MIRROR or not MIRROR.enabled:
                return self.back("Keine Destination „upstream“ zugeordnet (Portal: Seite der Instanz, Reiter Netz & Adressen, Destinationen, „Zuordnen“).", False, "/spiegel")
            st = MIRROR.poll()
            return self.back(("Abgerufen." if not st.get("last_error")
                              else "Abruf fehlgeschlagen: " + st["last_error"]),
                             not st.get("last_error"), "/spiegel")
        if length > MAX_FORM:
            return self.back("Das Formular ist zu groß.", False)
        form = {k: v[0] for k, v in parse_qs(
            self.rfile.read(length).decode("utf-8", "replace")).items()}
        app, version = form.get("app", ""), form.get("version", "")
        if path == "/upstream/follow":
            good, msg = (MIRROR.follow(form.get("link", ""), who) if MIRROR else
                         (False, "Kein Spiegel."))
            return self.back(msg, good, "/spiegel")
        if path == "/upstream/unfollow" and MIRROR:
            MIRROR.unfollow(app)
            return self.back(f"{app} wird nicht mehr gefolgt (Vorhandenes bleibt).", True, "/spiegel")
        if path == "/visible":
            try:
                labels = cat.parse_labels(form.get("visible_to", ""))
                CATALOG.set_visible(app, labels, who)
            except cat.CatalogError as e:
                return self.back_to(app, str(e), False)
            return self.back_to(app, f"{app}: sichtbar für "
                                + (", ".join(labels) if labels else "alle Mandanten"))
        try:
            if path == "/release":
                CATALOG.set_released(app, version, form.get("released") == "1", who)
                return self.back_to(app, ("Freigegeben: " if form.get("released") == "1"
                                          else "Zurückgezogen: ") + f"{app} {version}")
            if path == "/delete":
                CATALOG.delete(app, version, form.get("reason", ""), who)
                return self.back_to(app, f"Gelöscht: {app} {version}")
        except cat.CatalogError as e:
            return self.back_to(app, str(e), False)
        self.send(404, plain("Nicht gefunden"))

    def upload(self, who, length):
        files = {}
        try:
            fields, files = multipart.parse(
                self.rfile, self.headers.get("Content-Type", ""), length,
                MAX_BYTES + 1024 * 1024, tmpdir=CATALOG.tmp_dir())
            up = files.get("file")
            if not up:
                return self.back("Es kam keine Datei an.", False, "/neu")
            rec = CATALOG.add(up["path"], MAX_BYTES, who, fields.get("note", ""))
            files.pop("file", None)          # moved into the store
            app_id = rec["file"].split("/")[1]
            return self.back_to(app_id, f"Aufgenommen: {rec['version']} — noch nicht freigegeben.")
        except multipart.MultipartError as e:
            return self.back(str(e), False, "/neu")
        except (zipinfo.PackageError, cat.CatalogError) as e:
            return self.back(str(e), False, "/neu")
        finally:
            multipart.cleanup(files)


def make_server(data_dir=None, port=PORT, env=None, opener=None):
    global CATALOG, MIRROR
    CATALOG = cat.Catalog(data_dir or DATA_DIR)
    MIRROR = upstream.Mirror(CATALOG, os.environ if env is None else env, MAX_BYTES, opener)
    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


INBOX_SECONDS = 5


def _inbox_loop():
    """Der Eingang des Knotens (RFC-0050 §9): kurz getaktet, weil auf der
    anderen Seite ein Mensch auf "übernommen" oder den Grund der Ablehnung
    wartet. Leer kostet das ein listdir."""
    import time
    while True:
        try:
            CATALOG.ingest_inbox(MAX_BYTES)
        except Exception as e:                       # noqa: BLE001
            print(f"inbox: {type(e).__name__}: {e}", flush=True)
        time.sleep(INBOX_SECONDS)


def main():
    import threading
    srv = make_server()
    MIRROR.start()
    threading.Thread(target=_inbox_loop, daemon=True).start()
    print(f"package-catalog {VERSION} on :{PORT}, data {DATA_DIR}"
          + (f", mirror {MIRROR.channel}/{MIRROR.mode}" if MIRROR.enabled else ""), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
