"""OAAP Studio 0.2 — Entwicklungsvorhaben, Briefings und Pakete.

Zweite Ausbaustufe (siehe program/studio/ideas.md). 0.1 verwaltete
Vorhaben und erzeugte daraus **Briefings**; 0.2 nimmt die andere Seite
dazu — das **fertige Paket**, das aus so einem Briefing entsteht:

- ein Paket (ZIP) prüfen, ohne es irgendwohin zu schicken,
- das Vorhaben aus dem Manifest füllen,
- das Paket über den Drei-Phasen-Weg (RFC-0019 §2) auf die
  **Test**-Instanz ausrollen,
- und den **Deployment-Zettel** erzeugen, mit dem die Projekt-KI
  denselben Weg selbst geht.

Die Regel, die das alles zusammenhält (RFC-0019, Abschnitt „Studio"):
**Das Studio hält nie ein Recht.** Alles Privilegierte gibt der Anwender
im Augenblick der Handlung — der Deploy-Token wird bei jedem Upload
eingegeben, für die Dauer einer Anfrage gehalten und nirgends abgelegt:
nicht in der Datenbank, nicht im Backup, nicht in einer URL, nicht in
einer Logzeile.

Gebaut als gewöhnliche OAAP-App nach dem App Deployment Contract v0.4 —
die eigenen Funktionen der Plattform gehorchen denselben Regeln wie die
Apps unserer Anwender: kein eigener Login (die geprüfte Identität kommt
als Gateway-Kopfzeile), ein HTTP-Port, Persistenz nur unter /data,
Konfiguration über deklarierte Umgebungsvariablen, Logs nach stdout,
Gesundheitspfad, mehrfach-instanzfähig, offline-fähig.

Abhängigkeit seit 0.2: **PyYAML**, und sonst nichts. 0.1 kam mit der
Standardbibliothek aus, weil es keine fremden Dateien las. Das tut das
Studio jetzt — und ein selbstgebauter YAML-Leser, der eine Schreibweise
missversteht, würde ein Paket für gut erklären, das der Knoten später
ablehnt. Dieselbe Begründung wie beim Store Editor. PyYAML braucht
keinen Übersetzer: ohne libyaml fällt es auf reines Python zurück, der
Bau auf arm64 hat also nichts zu kompilieren.

Optik nach oaap-design/docs/design-guidelines.md v0.1 — Blau-Palette,
Hexagon-Marke, deutsche Oberfläche, Floorplans (Listenbericht,
Objektseite, Dialogseite), keine externen Ressourcen.
"""

import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import deployer
import multipart
import pkg

# Development override only — not operator configuration (contract
# rule 4: operator config comes from the manifest's declared env vars).
DATA_DIR = os.environ.get("STUDIO_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "studio.db")
PORT = 8000

# Hochgeladene Pakete liegen NICHT unter /data: Sie sind Durchgangsware,
# und ein 10-MB-Paket je Prüfung würde sonst in jedem Backup dieser App
# landen. Der Knoten hebt das Artefakt auf — das ist seine Aufgabe
# (RFC-0019 §4), nicht die des Studios.
TMP_DIR = os.environ.get("STUDIO_TMP_DIR", "/tmp")

# Declared configuration (manifest `config`)
CONTRACT_URL = os.environ.get(
    "STUDIO_CONTRACT_URL",
    "https://github.com/MDJoerg/oaap-spec/blob/main/docs/app-deployment-contract.md")
GIT_BASE = os.environ.get("STUDIO_GIT_BASE", "").rstrip("/")


def _int_env(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(key) or default)))
    except ValueError:
        return default


# Obergrenze für ein Paket. Der Knoten nimmt bis 256 MB an; das Studio
# geht dem nicht voraus, sondern bleibt darunter, weil es die Datei
# zwischenlagert. Betreiber können hochsetzen.
MAX_PACKAGE_MB = _int_env("STUDIO_MAX_PACKAGE_MB", 64, 1, 256)
MAX_PACKAGE_BYTES = MAX_PACKAGE_MB * 1024 * 1024

# Wie lange auf den Knoten gewartet wird. Ein Bau auf dem Gerät dauert;
# antwortet der Hook mit 202, fragt das Studio den Status nach.
DEPLOY_TIMEOUT = _int_env("STUDIO_DEPLOY_TIMEOUT_SECONDS", 180, 10, 900)

# Adresse des Portals — nur für Verweise („Instanz im Portal öffnen").
# Leer = das Studio leitet sie aus dem eigenen Hostnamen ab.
PORTAL_URL = os.environ.get("STUDIO_PORTAL_URL", "").rstrip("/")

APP_TYPES = {
    "native": "native (Quellcode, Build auf der Plattform)",
    "image": "image (fertiges Container-Image)",
    "wrapped": "wrapped (fremde Anwendung, von OAAP umschlossen)",
}
STATUSES = {
    "idee": "Idee",
    "briefing": "Briefing erstellt",
    "entwicklung": "In Entwicklung",
    "test": "Im Test",
    "produktiv": "Produktiv",
    "ruht": "Ruht",
}
# Status colours reuse the platform badges: amber = test channel,
# green = productive, grey = dormant.
STATUS_BADGE = {"idee": "off", "briefing": "", "entwicklung": "",
                "test": "test", "produktiv": "ok", "ruht": "off"}

FIELDS = ("name", "context", "owner", "target_users", "goal", "scope",
          "app_type", "status", "agent", "repo_url", "instance",
          "hook_url", "test_url", "deploy_way", "notes")

# Wie dieses Vorhaben auf die Test-Instanz kommt (RFC-0019 Entscheidung 1:
# der ZIP-Weg tritt NEBEN den Git-Weg, nie an seine Stelle).
DEPLOY_WAYS = {
    "git": "Git — der Knoten holt sich den Stand aus dem Repository",
    "artifact": "Paket (ZIP) — die KI liefert ein fertiges Paket ab",
}

# Spalten, die nach 0.1 dazugekommen sind. Bestehende Datenbanken werden
# beim Start ergänzt; eine Instanz aus 0.1 verliert nichts.
LATER_COLUMNS = {
    "deploy_way": "TEXT NOT NULL DEFAULT 'git'",
    "app_id": "TEXT NOT NULL DEFAULT ''",
    "pkg_file": "TEXT NOT NULL DEFAULT ''",
    "pkg_version": "TEXT NOT NULL DEFAULT ''",
    "pkg_sha256": "TEXT NOT NULL DEFAULT ''",
    "pkg_bytes": "INTEGER NOT NULL DEFAULT 0",
    "pkg_at": "TEXT NOT NULL DEFAULT ''",
    "pkg_by": "TEXT NOT NULL DEFAULT ''",
    "pkg_summary": "TEXT NOT NULL DEFAULT ''",
    "pkg_report": "TEXT NOT NULL DEFAULT ''",
    # Zusammenfassung des zuletzt ERFOLGREICH ausgerollten Pakets. Sie
    # ist die Vergleichsbasis der Rahmen-Vorschau — und nicht die des
    # zuletzt geprüften: Sonst könnte man ein Paket nie erst prüfen und
    # dann ausrollen, weil es sich beim zweiten Blick mit sich selbst
    # vergliche und „Version unverändert" meldete (im Lauf gegen
    # oaap-test aufgefallen, 2026-08-16).
    "dep_summary": "TEXT NOT NULL DEFAULT ''",
}


# --------------------------------------------------------------- storage

def db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id           TEXT PRIMARY KEY,
            name         TEXT NOT NULL,
            context      TEXT NOT NULL DEFAULT '',
            owner        TEXT NOT NULL DEFAULT '',
            target_users TEXT NOT NULL DEFAULT '',
            goal         TEXT NOT NULL DEFAULT '',
            scope        TEXT NOT NULL DEFAULT '',
            app_type     TEXT NOT NULL DEFAULT 'native',
            status       TEXT NOT NULL DEFAULT 'idee',
            agent        TEXT NOT NULL DEFAULT '',
            repo_url     TEXT NOT NULL DEFAULT '',
            instance     TEXT NOT NULL DEFAULT '',
            hook_url     TEXT NOT NULL DEFAULT '',
            test_url     TEXT NOT NULL DEFAULT '',
            notes        TEXT NOT NULL DEFAULT '',
            created_at   TEXT NOT NULL,
            created_by   TEXT NOT NULL,
            updated_at   TEXT NOT NULL,
            updated_by   TEXT NOT NULL
        )""")
    have = {r["name"] for r in con.execute("PRAGMA table_info(projects)")}
    for col, decl in LATER_COLUMNS.items():
        if col not in have:
            con.execute(f"ALTER TABLE projects ADD COLUMN {col} {decl}")
    # Was das Studio selbst ausgerollt hat. Ohne Token, ohne Paketinhalt —
    # nur die Tatsache, damit später nachvollziehbar ist, wer wann welche
    # Version geschickt hat. Das verbindliche Protokoll führt der Knoten.
    con.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT NOT NULL,
            at         TEXT NOT NULL,
            by         TEXT NOT NULL,
            version    TEXT NOT NULL DEFAULT '',
            sha256     TEXT NOT NULL DEFAULT '',
            -- 1 = angenommen, 0 = abgelehnt, 2 = Ausgang unklar
            -- (keine Antwort bekommen). Absichtlich eine Zahl statt NULL:
            -- die Spalte gibt es auf Knoten schon, und ein NOT NULL
            -- laesst sich in SQLite nicht nachtraeglich aufweichen.
            ok         INTEGER NOT NULL DEFAULT 0,
            phase      TEXT NOT NULL DEFAULT '',
            message    TEXT NOT NULL DEFAULT ''
        )""")
    con.commit()
    return con


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def slugify(name):
    s = name.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40]


def mb(n):
    n = int(n or 0)
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} kB"


def loads(text, fallback=None):
    try:
        v = json.loads(text or "")
    except ValueError:
        return fallback
    return v if v is not None else fallback


def portal_base(host):
    """Adresse des Portals — für Verweise, nicht für Aufrufe.

    Ist sie konfiguriert, gilt sie. Sonst wird sie aus dem eigenen
    Hostnamen abgeleitet: Eine App-Instanz erscheint als Unteradresse
    des Knotens (`studio.knoten.example` → `knoten.example`), im LAN
    unter einem eigenen Port am selben Rechner. Beides ist eine
    begründete Vermutung und deshalb nur ein Link — nie ein Aufruf, der
    stillschweigend woanders landen könnte.
    """
    if PORTAL_URL:
        return PORTAL_URL
    host = (host or "").strip()
    if not host:
        return ""
    hostname, _, _port = host.partition(":")
    if re.fullmatch(r"[\d.]+", hostname) or hostname in ("localhost",):
        return f"http://{hostname}"
    parts = hostname.split(".")
    if len(parts) >= 3:
        return "https://" + ".".join(parts[1:])
    return f"https://{hostname}"


# ------------------------------------------------------------ presentation

def esc(v):
    return html.escape(str(v or ""), quote=True)


STYLE = """<style>
  :root{
    --oaap-blue-950:#172554; --oaap-blue-900:#1e3a8a; --oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb; --oaap-blue-100:#dbeafe;
    --oaap-bg:#f4f6fa; --oaap-surface:#fff; --oaap-text:#1f2937;
    --oaap-muted:#6b7280; --oaap-border:#e5e7eb;
    --ok:#15803d; --err:#b91c1c; --warn:#b45309;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       margin:0;background:var(--oaap-bg);color:var(--oaap-text)}
  header.oaap{background:linear-gradient(135deg,var(--oaap-blue-900),var(--oaap-blue-950));
       color:#fff;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;padding:.6rem 1.2rem}
  .brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;color:#fff}
  .brand b{font-size:1.15rem;letter-spacing:.08em}
  .brand small{display:block;font-size:.62rem;opacity:.75;letter-spacing:.02em}
  nav.main{display:flex;gap:.25rem;margin-left:1rem;flex:1}
  nav.main a{color:#fff;text-decoration:none;padding:.55rem .9rem;border-radius:.4rem;
       opacity:.85;border-bottom:3px solid transparent}
  nav.main a:hover{background:rgba(255,255,255,.12);opacity:1}
  nav.main a.active{border-bottom-color:var(--oaap-blue-100);opacity:1;font-weight:600}
  .userbox{display:flex;align-items:center;gap:.7rem;font-size:.9rem}
  .userbox .who{text-align:right;line-height:1.2}
  .userbox .who small{opacity:.75}
  main{max-width:62rem;margin:1.6rem auto;padding:0 1.2rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .pagehead{display:flex;align-items:center;justify-content:space-between;gap:1rem;
       flex-wrap:wrap;margin-bottom:1rem}
  .pagehead h1{margin:0}
  .back{display:inline-block;margin-bottom:.8rem;color:var(--oaap-blue-600);text-decoration:none}
  .back:hover{text-decoration:underline}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.danger{border-color:#fecaca}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.test{background:#fef3c7;color:#92400e}
  .badge.off{background:#f3f4f6;color:#6b7280}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  .badge.warn{background:#fef3c7;color:#92400e}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  a.btn.ghost{background:transparent;color:var(--oaap-blue-600);
       border:1px solid var(--oaap-blue-600)}
  a.btn.ghost:hover{background:var(--oaap-blue-100)}
  button.danger{background:var(--err)} button.danger:hover{background:#991b1b}
  label{display:block;font-size:.85rem;color:var(--oaap-muted);margin-top:.9rem}
  input,select,textarea{width:100%;padding:.55rem;margin:.25rem 0 .2rem;
       border:1px solid var(--oaap-border);border-radius:.4rem;font-size:.95rem;
       font-family:inherit}
  textarea{min-height:5.5rem;resize:vertical}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .err{color:var(--err)}.ok{color:var(--ok)}.muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.6rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle}
  th{font-size:.82rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  tr.rowlink:hover td{background:#f8fafc}
  td a.rowaction{color:var(--oaap-blue-600);text-decoration:none;white-space:nowrap}
  td a.rowaction:hover{text-decoration:underline}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 1.2rem}
  .actions{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.1rem;align-items:center}
  dl.facts{margin:0;display:grid;grid-template-columns:11rem 1fr;gap:.45rem 1rem}
  dl.facts dt{color:var(--oaap-muted);font-size:.85rem}
  dl.facts dd{margin:0;word-break:break-word}
  pre.briefing{background:#0f172a;color:#e2e8f0;padding:1.1rem;border-radius:.5rem;
       overflow-x:auto;font-size:.82rem;line-height:1.5;white-space:pre-wrap;
       word-break:break-word}
  footer.oaap{max-width:62rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
       color:var(--oaap-muted);font-size:.8rem;display:flex;gap:.5rem;align-items:center}
  @media (max-width:640px){
    nav.main{order:3;flex-basis:100%;margin-left:0}
    .userbox .who{display:none}
    .grid2{grid-template-columns:1fr}
    dl.facts{grid-template-columns:1fr;gap:.1rem .5rem}
    dl.facts dd{margin-bottom:.5rem}
  }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

# Hexagon cluster = apps/ecosystem/studio (design guidelines 1), inline
# because OAAP UIs load nothing from outside.
LOGO_SVG = ('<svg viewBox="0 0 100 100" width="34" height="34" aria-hidden="true">'
            '<polygon points="34,6 58,20 58,48 34,62 10,48 10,20" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round"/>'
            '<polygon points="72,30 92,41 92,64 72,76 52,64 52,41" fill="#fff" opacity=".85"/>'
            '<polygon points="42,58 66,72 66,94 42,96 22,84 22,70" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round" opacity=".6"/></svg>')


def page(title, body, user, roles, active=""):
    """Shared chrome: header with mark, navigation, user box, footer.

    Mirrors the portal layout so a Studio page and a portal page look
    like one product (the App-UI-Kit idea, hand-rolled until the
    platform ships a real kit).
    """
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — OAAP Studio</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP STUDIO</b><small>Entwicklungsvorhaben und Briefings</small></span>
  </a>
  <nav class="main">
    <a href="./" class="{'active' if active == 'projects' else ''}">Vorhaben</a>
    <a href="./hilfe" class="{'active' if active == 'help' else ''}">Hilfe</a>
  </nav>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP Studio {VERSION} — Vorhaben verwalten, Briefings erzeugen, Pakete prüfen und ausrollen
</footer>
</html>"""


VERSION = "0.2.1"


def field(label, name, value, hint="", kind="text", rows=0, options=None, required=False):
    req = " required" if required else ""
    if options:
        opts = "".join(
            f'<option value="{esc(k)}"{" selected" if str(value) == k else ""}>{esc(v)}</option>'
            for k, v in options.items())
        control = f'<select name="{name}"{req}>{opts}</select>'
    elif rows:
        control = (f'<textarea name="{name}" rows="{rows}"{req}>{esc(value)}</textarea>')
    else:
        control = f'<input type="{kind}" name="{name}" value="{esc(value)}"{req}>'
    hint_html = f'<p class="hint">{esc(hint)}</p>' if hint else ""
    return f"<label>{esc(label)}{control}</label>{hint_html}"


def project_form(p, submit_label):
    """Object page sections (design guidelines 6.2): grouped cards."""
    return f"""
<div class="card">
  <h2>Vorhaben</h2>
  {field("Name des Vorhabens", "name", p["name"], required=True,
         hint="So sprecht ihr über die App, z. B. „Reklamationen“.")}
  <div class="grid2">
    {field("Kontext / Organisation", "context", p["context"],
           hint="Kunde, Bereich oder Mandant — z. B. kuk, bdt, home.")}
    {field("Auftraggeber", "owner", p["owner"],
           hint="Wer will die App haben und entscheidet fachlich?")}
  </div>
  <div class="grid2">
    {field("App-Typ", "app_type", p["app_type"], options=APP_TYPES)}
    {field("Status", "status", p["status"], options=STATUSES)}
  </div>
  {field("Entwickelt mit", "agent", p["agent"],
         hint="Welche KI oder wer entwickelt? z. B. Claude Code, Codex, Team Müller.")}
</div>
<div class="card">
  <h2>Fachlicher Inhalt (wird zum Briefing)</h2>
  {field("Zielanwender", "target_users", p["target_users"], rows=3,
         hint="Wer benutzt die App, in welcher Situation, auf welchem Gerät?")}
  {field("Was soll die App erreichen?", "goal", p["goal"], rows=4,
         hint="Das Problem in eigenen Worten — nicht die Lösung.")}
  {field("Was muss die erste Version können?", "scope", p["scope"], rows=5,
         hint="Eine Liste knapper Sätze. Alles Weitere kommt später.")}
</div>
<div class="card">
  <h2>Technische Anbindung</h2>
  {field("Weg auf die Test-Instanz", "deploy_way", p["deploy_way"] or "git",
         options=DEPLOY_WAYS,
         hint="Git: der Knoten holt sich den Stand selbst — dafür braucht er "
              "Zugang zum Repository. Paket: die KI liefert eine ZIP ab, die "
              "Plattform hält dann keinen fremden Schlüssel (RFC-0019).")}
  {field("Repository (Git-URL)", "repo_url", p["repo_url"],
         hint="Das Projekt-Repo, z. B. auf dem eigenen Forgejo. Default-Branch: main.")}
  <div class="grid2">
    {field("Instanzname auf der Plattform", "instance", p["instance"],
           hint="Name der Test-Instanz, z. B. reklamationen-test.")}
    {field("Test-Adresse", "test_url", p["test_url"],
           hint="Wo die Test-Instanz erreichbar ist.")}
  </div>
  {field("Deploy-Hook (URL)", "hook_url", p["hook_url"],
         hint="Nur die Adresse — das Token gehört NICHT ins Studio. "
              "Es wird im Portal erzeugt, gehört Dir und wird bei jedem "
              "Upload einzeln eingegeben.")}
  {field("Notizen / nächste Schritte", "notes", p["notes"], rows=4)}
</div>
<div class="actions"><button type="submit">{esc(submit_label)}</button></div>
"""


def list_page(rows, user, roles, msg=""):
    if rows:
        body_rows = "".join(f"""
  <tr class="rowlink">
    <td><a class="rowaction" href="./vorhaben/{esc(r['id'])}">{esc(r['name'])}</a></td>
    <td>{esc(r['context']) or '<span class="muted">—</span>'}</td>
    <td>{esc(r['app_type'])}</td>
    <td><span class="badge {STATUS_BADGE.get(r['status'], '')}">{esc(STATUSES.get(r['status'], r['status']))}</span></td>
    <td>{esc(r['agent']) or '<span class="muted">—</span>'}</td>
  </tr>""" for r in rows)
        table = f"""<div class="card" style="overflow-x:auto;padding:.4rem 1.4rem"><table>
  <tr><th>Vorhaben</th><th>Kontext</th><th>Typ</th><th>Status</th><th>Entwickelt mit</th></tr>
  {body_rows}
</table></div>"""
    else:
        table = """<div class="card"><p class="muted">Noch kein Vorhaben erfasst.
        Legt eines an — daraus entsteht das Briefing, mit dem die KI startet.</p></div>"""
    msg_html = f'<p class="ok">{esc(msg)}</p>' if msg else ""
    body = f"""
<div class="pagehead">
  <h1>Entwicklungsvorhaben</h1>
  <a class="btn" href="./vorhaben/neu">Vorhaben anlegen</a>
</div>
{msg_html}
{table}"""
    return page("Vorhaben", body, user, roles, "projects")


def package_card(p):
    """Was zuletzt geprüft wurde — auf der Objektseite, kurz gehalten."""
    if not p["pkg_sha256"]:
        text = ("<p class=\"muted\">Für dieses Vorhaben wurde noch kein Paket "
                "geprüft. Das Studio liest das Manifest aus der ZIP, hält es "
                "gegen die Plattformregeln und rollt es auf Wunsch über den "
                "Deploy-Hook aus.</p>")
    else:
        text = f"""<dl class="facts">
    <dt>Zuletzt geprüft</dt><dd>{esc(p['pkg_file'])} — Version
      <b>{esc(p['pkg_version'])}</b>, {esc(mb(p['pkg_bytes']))}</dd>
    <dt>Prüfsumme</dt><dd><code>{esc(p['pkg_sha256'][:16])}…</code></dd>
    <dt>Am</dt><dd>{esc(p['pkg_at'])} von {esc(p['pkg_by'])}</dd>
  </dl>"""
    return f"""
<div class="card">
  <h2>Paket und Deployment</h2>
  {text}
  <div class="actions">
    <a class="btn" href="./{esc(p['id'])}/paket">Paket prüfen und ausrollen …</a>
    <a class="btn ghost" href="./{esc(p['id'])}/zettel">Deployment-Zettel …</a>
  </div>
</div>"""


def object_page(p, user, roles, msg="", error="", is_admin=False):
    danger = ""
    if is_admin:
        danger = f"""
<div class="card danger">
  <h2>Vorhaben löschen</h2>
  <p class="muted">Entfernt das Vorhaben mitsamt Briefing-Daten. Das Repository
  und installierte Instanzen bleiben unberührt.</p>
  <a class="btn ghost" href="./{esc(p['id'])}/loeschen">Löschen …</a>
</div>"""
    msg_html = f'<p class="ok">{esc(msg)}</p>' if msg else ""
    err_html = f'<p class="err">{esc(error)}</p>' if error else ""
    body = f"""
<a class="back" href="../">← Zurück zur Liste</a>
<div class="pagehead">
  <h1>{esc(p['name'])}
    <span class="badge {STATUS_BADGE.get(p['status'], '')}">{esc(STATUSES.get(p['status'], p['status']))}</span>
  </h1>
  <a class="btn" href="./{esc(p['id'])}/briefing">Briefing erzeugen</a>
</div>
{msg_html}{err_html}
<div class="card">
  <dl class="facts">
    <dt>Kennung</dt><dd><code>{esc(p['id'])}</code></dd>
    <dt>Angelegt</dt><dd>{esc(p['created_at'])} von {esc(p['created_by'])}</dd>
    <dt>Zuletzt geändert</dt><dd>{esc(p['updated_at'])} von {esc(p['updated_by'])}</dd>
  </dl>
</div>
{package_card(p)}
<form method="post" action="./{esc(p['id'])}">
{project_form(p, "Speichern")}
</form>
{danger}"""
    return page(p["name"], body, user, roles, "projects")


def new_page(p, user, roles, error=""):
    err_html = f'<p class="err">{esc(error)}</p>' if error else ""
    body = f"""
<a class="back" href="../">← Zurück zur Liste</a>
<h1>Neues Vorhaben</h1>
{err_html}
<p class="muted">Alles, was hier steht, landet später im Briefing für die KI.
Kurze, klare Sätze sind besser als Fachjargon.</p>
<form method="post" action="../vorhaben">
{project_form(p, "Vorhaben anlegen")}
</form>"""
    return page("Neues Vorhaben", body, user, roles, "projects")


def delete_page(p, user, roles):
    body = f"""
<a class="back" href="../{esc(p['id'])}">← Zurück zum Vorhaben</a>
<h1>Vorhaben löschen</h1>
<div class="card danger">
  <p>Soll das Vorhaben <b>{esc(p['name'])}</b> wirklich gelöscht werden?
  Erfasste Angaben und Briefing-Inhalte gehen dabei verloren.</p>
  <p class="muted">Repository, Deploy-Token und installierte App-Instanzen
  sind davon nicht betroffen.</p>
  <form method="post" action="../{esc(p['id'])}/loeschen">
    <div class="actions">
      <button class="danger" type="submit">Endgültig löschen</button>
      <a class="btn ghost" href="../{esc(p['id'])}">Abbrechen</a>
    </div>
  </form>
</div>"""
    return page("Löschen", body, user, roles, "projects")


def briefing_page(p, text, user, roles):
    body = f"""
<a class="back" href="../{esc(p['id'])}">← Zurück zum Vorhaben</a>
<div class="pagehead">
  <h1>Briefing: {esc(p['name'])}</h1>
  <a class="btn" href="./briefing.md" download>Als Datei herunterladen</a>
</div>
<div class="card">
  <h2>So geht es weiter</h2>
  <ol class="muted" style="margin:0;padding-left:1.2rem;line-height:1.7">
    <li>Briefing herunterladen und im Projektverzeichnis als
        <code>BRIEFING.md</code> ablegen.</li>
    <li>KI der Wahl im Projektverzeichnis starten und ihr das Briefing als
        Kontext geben.</li>
    <li>Erster Satz an die KI: „Ich möchte mit Dir eine App bauen. Die
        konkreten Anweisungen stehen in BRIEFING.md. Lass uns loslegen.“</li>
    <li>Deploy-Token einmalig erzeugen lassen
        (<code>sudo oaap app token create {esc(p['instance']) or '&lt;instanz&gt;'}</code>)
        und der KI direkt geben — nicht im Studio speichern.</li>
  </ol>
</div>
<pre class="briefing">{esc(text)}</pre>"""
    return page("Briefing", body, user, roles, "projects")


LEVEL_LABEL = {pkg.FEHLER: "Fehler", pkg.BEFUND: "Befund", pkg.HINWEIS: "Hinweis"}
LEVEL_BADGE = {pkg.FEHLER: "err", pkg.BEFUND: "test", pkg.HINWEIS: "off"}


def findings_html(findings):
    if not findings:
        return ('<p class="ok">Keine Beanstandung — das Manifest hält, was '
                'die Plattform verlangt.</p>')
    items = []
    for f in findings:
        hint = (f'<br><span class="muted">{esc(f["hint"])}</span>'
                if f.get("hint") else "")
        items.append(
            f'<li style="margin:.5rem 0"><span class="badge '
            f'{LEVEL_BADGE.get(f["level"], "off")}">'
            f'{esc(LEVEL_LABEL.get(f["level"], f["level"]))}</span> '
            f'{esc(f["text"])}{hint}</li>')
    return f'<ul style="list-style:none;padding:0;margin:0">{"".join(items)}</ul>'


def manifest_html(s):
    """Das Manifest als Tatsachen — was ein Betreiber daran wissen muss."""
    routes = "".join(
        f"<tr><td><code>{esc(r['path'])}</code></td>"
        f"<td>{'<span class=\"badge test\">ohne Anmeldung</span> ' if 'public' in r['roles'] else ''}"
        f"{esc(', '.join(r['roles']))}</td></tr>"
        for r in s.get("routes", []))
    storage = ", ".join(f"{x['name']} → {x['mount']}"
                        for x in s.get("storage", [])) or "—"
    config = ", ".join(
        (x["key"] + (" (Geheimnis)" if x["secret"] else ""))
        for x in s.get("config", [])) or "—"
    eps = ", ".join(
        f"{e['name']} {e['protocol']} {e['container_port']}"
        + (" (fest)" if e.get("fixed") else "")
        for e in s.get("endpoints", [])) or "—"
    services = ", ".join(f"{x['name']} :{x['port']} ({x['from']})"
                         for x in s.get("services", [])) or "—"
    return f"""<dl class="facts">
  <dt>App</dt><dd><b>{esc(s.get('name'))}</b> — <code>{esc(s.get('id'))}</code>,
    Version <b>{esc(s.get('version'))}</b>, Typ {esc(s.get('type'))},
    Art {esc(s.get('class'))}</dd>
  <dt>Manifest-Format</dt><dd>{esc(s.get('manifest_version'))}</dd>
  <dt>Dienste</dt><dd>{esc(services)}</dd>
  <dt>Speicher</dt><dd>{esc(storage)}</dd>
  <dt>Konfiguration</dt><dd>{esc(config)}</dd>
  <dt>Endpunkte</dt><dd>{esc(eps)}</dd>
  <dt>Gesundheit</dt><dd><code>{esc(s.get('health', {}).get('path'))}</code></dd>
</dl>
<table style="margin-top:1rem"><tr><th>Route</th><th>Rollen</th></tr>{routes}</table>"""


def report_card(rep):
    if not rep:
        return ""
    s = rep.get("summary") or {}
    counts = rep.get("counts") or {}
    hard = rep.get("envelope_hard") or []
    confirm = rep.get("envelope_confirm") or []
    envelope = ""
    if hard or confirm:
        blocks = []
        if hard:
            blocks.append(
                '<p class="err"><b>Der Knoten lehnt das ab:</b></p><ul>'
                + "".join(f"<li>{esc(x)}</li>" for x in hard) + "</ul>")
        if confirm:
            blocks.append(
                "<p><b>Erweitert den Rahmen — braucht eine Bestätigung durch "
                'einen <code>server_admin</code> im Portal:</b></p><ul>'
                + "".join(f"<li>{esc(x)}</li>" for x in confirm) + "</ul>")
        envelope = f"""<div class="card">
  <h2>Rahmen (RFC-0019 §3)</h2>
  {''.join(blocks)}
  <p class="muted">Verglichen wird mit dem <b>zuletzt von hier ausgerollten</b>
  Paket. Was tatsächlich installiert ist, weiß nur der Knoten — er
  entscheidet, und seine Antwort steht im Deployment-Protokoll.</p>
</div>"""
    elif rep.get("compared"):
        envelope = """<div class="card">
  <h2>Rahmen (RFC-0019 §3)</h2>
  <p class="ok">Nichts, was den Rahmen erweitert — verglichen mit dem zuletzt
  von hier ausgerollten Paket. Das ist der Normalfall und läuft ohne
  Rückfrage durch.</p>
</div>"""
    else:
        envelope = """<div class="card">
  <h2>Rahmen (RFC-0019 §3)</h2>
  <p class="muted">Noch kein Vergleich möglich: Aus diesem Vorhaben wurde
  hier noch nichts ausgerollt. Was den Rahmen erweitert, entscheidet dann
  der Knoten gegen den <b>installierten</b> Stand — er allein kennt ihn.</p>
</div>"""

    badge = ('<span class="badge ok">bereit</span>' if rep.get("deployable")
             else '<span class="badge err">nicht bereit</span>')
    return f"""
<div class="pagehead" style="margin-top:2rem">
  <h1 style="font-size:1.1rem">Letzte Prüfung {badge}</h1>
  <span class="muted">{esc(rep.get('at'))} von {esc(rep.get('by'))}</span>
</div>
<div class="card">
  <h2>Paket</h2>
  <dl class="facts">
    <dt>Datei</dt><dd>{esc(rep.get('file'))} ({esc(mb(rep.get('bytes')))})</dd>
    <dt>Prüfsumme (SHA-256)</dt><dd><code>{esc(rep.get('sha256'))}</code></dd>
    <dt>Inhalt</dt><dd>{esc(rep.get('entries'))} Dateien, entpackt
      {esc(mb(rep.get('uncompressed')))}</dd>
    <dt>Paketwurzel</dt><dd><code>{esc(rep.get('root') or '(Archivwurzel)')}</code></dd>
  </dl>
</div>
<div class="card">
  <h2>Manifest</h2>
  {manifest_html(s)}
</div>
<div class="card">
  <h2>Prüfung — {counts.get(pkg.FEHLER, 0)} Fehler,
      {counts.get(pkg.BEFUND, 0)} Befunde, {counts.get(pkg.HINWEIS, 0)} Hinweise</h2>
  {findings_html(rep.get('findings') or [])}
  <p class="muted" style="margin-top:1rem">Diese Prüfung ist eine
  <b>Vorschau</b>. Verbindlich prüft der Knoten — noch einmal und
  vollständig, bevor er irgendetwas entpackt.</p>
</div>
{envelope}"""


UNKLAR = 2  # Ergebnis eines Versuchs ohne Antwort (siehe record_deploy)

DEPLOY_VERDICT = {
    1: '<span class="badge ok">angenommen</span>',
    0: '<span class="badge err">abgelehnt</span>',
    # Ohne Antwort weiß das Studio es nicht — und behauptet es nicht.
    UNKLAR: '<span class="badge warn">Ausgang unklar</span>',
}


def deployments_card(rows):
    if not rows:
        return ""
    items = "".join(
        f"<tr><td>{esc(r['at'])}</td><td>{esc(r['version']) or '—'}</td>"
        f"<td><code>{esc((r['sha256'] or '')[:12])}</code></td>"
        f"<td>{DEPLOY_VERDICT.get(r['ok'], DEPLOY_VERDICT[0])}</td>"
        f"<td>{esc(r['message'])}</td><td>{esc(r['by'])}</td></tr>"
        for r in rows)
    return f"""<div class="card" style="overflow-x:auto">
  <h2>Vom Studio ausgerollt</h2>
  <table><tr><th>Wann</th><th>Version</th><th>Prüfsumme</th><th>Ergebnis</th>
    <th>Antwort des Knotens</th><th>Wer</th></tr>{items}</table>
  <p class="muted" style="margin-top:.8rem">Dieses Verzeichnis ist die Sicht
  des Studios. Das verbindliche Protokoll führt der Knoten — im Portal unter
  der Instanz.</p>
</div>"""


def package_page(p, rep, deploys, user, roles, msg="", error="", portal=""):
    hook = (p["hook_url"] or "").strip()
    inst = (p["instance"] or "").strip()
    if hook:
        try:
            urls = deployer.hook_urls(hook)
            hook_html = f"""<dl class="facts">
      <dt>Anmelden</dt><dd><code>POST {esc(urls['announce'])}</code></dd>
      <dt>Hochladen</dt><dd><code>PUT {esc(urls['artifact'])}</code></dd>
    </dl>"""
        except deployer.DeployError as e:
            hook_html = f'<p class="err">{esc(str(e))}</p>'
    else:
        hook_html = ('<p class="err">Für dieses Vorhaben ist keine '
                     'Hook-Adresse hinterlegt. Sie entsteht im Portal auf der '
                     'Instanzseite, wenn dort ein Deploy-Token erzeugt wird — '
                     'danach hier im Vorhaben eintragen.</p>')
    portal_link = (f'<p class="muted">Instanz im Portal: '
                   f'<a href="{esc(portal)}/instances/{esc(inst)}">'
                   f'{esc(portal)}/instances/{esc(inst)}</a></p>'
                   if portal and inst else "")
    msg_html = f'<p class="ok">{esc(msg)}</p>' if msg else ""
    err_html = f'<p class="err">{esc(error)}</p>' if error else ""
    body = f"""
<a class="back" href="../{esc(p['id'])}">← Zurück zum Vorhaben</a>
<div class="pagehead">
  <h1>Paket: {esc(p['name'])}</h1>
  <a class="btn ghost" href="./zettel">Deployment-Zettel …</a>
</div>
{msg_html}{err_html}
<form method="post" action="./paket" enctype="multipart/form-data">
<div class="card">
  <h2>Paket prüfen</h2>
  <p class="hint">Die ZIP, wie die KI sie abliefert — mit
  <code>oaap-app.yaml</code> in der Wurzel oder in einem einzelnen
  Oberordner. Höchstens {MAX_PACKAGE_MB} MB. Das Studio entpackt nichts;
  es liest das Inhaltsverzeichnis und das Manifest.</p>
  <label>Paket (ZIP)<input type="file" name="paket" accept=".zip,application/zip" required></label>
  <label class="hint" style="margin-top:.8rem">
    <input type="checkbox" name="uebernehmen" value="1" checked style="width:auto">
    Angaben des Vorhabens aus dem Manifest übernehmen (App-Typ, Instanzname
    als Vorschlag, Kennung der App)
  </label>
  <div class="actions"><button type="submit" name="action" value="pruefen">Nur prüfen</button></div>
</div>
<div class="card">
  <h2>Auf die Test-Instanz ausrollen</h2>
  {hook_html}
  {portal_link}
  <p class="hint">Das Studio läuft damit genau den Weg, den auch die KI geht
  (RFC-0019 §2): anmelden, Freigabe abholen, hochladen. Es bekommt dafür
  <b>kein Sonderrecht</b> — der Token ist das Recht, und der gehört Dir.</p>
  <label>Deploy-Token oder Anlege-Erlaubnis
    <input type="password" name="token" autocomplete="off" autocapitalize="off"
           spellcheck="false" placeholder="wird nicht gespeichert">
  </label>
  <p class="hint">Wird für die Dauer dieser einen Anfrage gehalten und danach
  vergessen: nicht in der Datenbank, nicht im Backup, nicht in einer URL.
  Erzeugt wird er im Portal auf der Instanzseite; verwahre ihn im
  Passwortmanager. Produktiv-Instanzen haben bewusst kein Token.</p>
  <p class="hint"><b>Gibt es die Instanz noch nicht?</b> Dann lass Dir im Portal
  unter „Instanzen“ eine <b>Anlege-Erlaubnis</b> für genau diesen Namen
  ausstellen und trage sie hier ein. Sie gilt einmal und eine halbe Stunde;
  danach entsteht die Test-Instanz aus diesem Paket. Für alles Weitere erzeugst
  Du auf der Instanzseite ein normales Deploy-Token (RFC-0019).</p>
  <div class="actions">
    <button type="submit" name="action" value="deployen">Prüfen und ausrollen</button>
  </div>
</div>
</form>
{report_card(rep)}
{deployments_card(deploys)}"""
    return page("Paket", body, user, roles, "projects")


def deploy_result_page(p, result, user, roles):
    """Was der Knoten geantwortet hat — Phase für Phase."""
    rows = []
    labels = {deployer.ANNOUNCE: "1 · Anmelden", deployer.UPLOAD: "2 · Hochladen",
              deployer.STATUS: "3 · Nachsehen"}
    for st in result.get("steps", []):
        details = "".join(f"<li>{esc(d)}</li>" for d in (st.get("details") or []))
        rows.append(f"""<div class="card">
  <h2>{esc(labels.get(st['phase'], st['phase']))}
    <span class="badge {'ok' if st['ok'] else 'err'}">HTTP {esc(st['status'])}</span></h2>
  <p>{esc(st.get('message') or '—')}</p>
  {f'<ul>{details}</ul>' if details else ''}
  {f'<p class="muted">{esc(st["hint"])}</p>' if st.get('hint') else ''}
</div>""")
    res = result.get("result") or {}
    final = ""
    if result.get("ok"):
        url = res.get("url") or (p["test_url"] or "")
        final = f"""<div class="card">
  <h2 class="ok">Ausgerollt</h2>
  <dl class="facts">
    <dt>Version</dt><dd>{esc(res.get('version') or '—')}</dd>
    <dt>Stand</dt><dd><code>{esc(res.get('revision') or '—')}</code></dd>
    <dt>Erreichbar unter</dt><dd>{f'<a href="{esc(url)}">{esc(url)}</a>' if url else '—'}</dd>
  </dl>
</div>"""
    elif result.get("pending"):
        final = """<div class="card">
  <h2>Läuft noch</h2>
  <p>Der Knoten baut das Paket. Das dauert bei einem Bau auf dem Gerät
  Minuten — auf einem Raspi auch länger. Der Stand steht im Portal auf der
  Instanzseite; die Antwort des Hooks lautet dann „fertig".</p>
</div>"""
    body = f"""
<a class="back" href="./paket">← Zurück zum Paket</a>
<h1>Deployment: {esc(p['name'])}</h1>
{''.join(rows)}
{final}
<p class="muted">Der eingegebene Token ist mit dieser Anfrage vergangen —
das Studio hat ihn nirgends abgelegt. Für den nächsten Upload wird er
wieder gebraucht.</p>"""
    return page("Deployment", body, user, roles, "projects")


def sheet_page(p, text, user, roles, with_token=False):
    warn = ("""<div class="card danger">
  <h2>Dieses Blatt enthält den Token</h2>
  <p>Er steht hier <b>einmalig</b> — das Studio hat ihn nicht gespeichert.
  Kopiere das Blatt jetzt zur KI (oder in den Passwortmanager) und lade die
  Seite nicht neu; danach ist er hier weg. Der Download-Knopf oben liefert
  dasselbe Blatt bewusst <b>ohne</b> Token: eine Datei mit einem Schlüssel
  darin wandert sonst durch Verzeichnisse, in denen sie nichts zu suchen
  hat.</p>
</div>""" if with_token else "")
    token_form = ("" if with_token else """
<div class="card">
  <h2>Token mit aufnehmen (einmalig)</h2>
  <p class="hint">Die KI braucht den Deploy-Token, um selbst ausrollen zu
  können. Gib ihn hier ein, wenn er im Blatt stehen soll — er wird
  <b>nicht gespeichert</b> und erscheint genau einmal auf der folgenden
  Seite. Ohne Eingabe steht im Blatt ein Platzhalter, und Du übergibst den
  Token getrennt.</p>
  <form method="post" action="./zettel">
    <label>Deploy-Token
      <input type="password" name="token" autocomplete="off" spellcheck="false"
             placeholder="optional — wird nicht gespeichert"></label>
    <div class="actions"><button type="submit">Blatt mit Token anzeigen</button></div>
  </form>
</div>""")
    body = f"""
<a class="back" href="../{esc(p['id'])}">← Zurück zum Vorhaben</a>
<div class="pagehead">
  <h1>Deployment-Zettel: {esc(p['name'])}</h1>
  <a class="btn" href="./zettel.md" download>Als Datei herunterladen</a>
</div>
{warn}
<div class="card">
  <h2>Wofür das Blatt ist</h2>
  <p class="muted">Es beantwortet der Projekt-KI genau eine Frage: „Wie
  bekomme ich meinen getesteten Stand auf die Test-Instanz?" — Adressen,
  Ablauf, Regeln, und was passiert, wenn etwas abgelehnt wird. Gehört ins
  Projektverzeichnis neben das Briefing.</p>
</div>
<pre class="briefing">{esc(text)}</pre>
{token_form}"""
    return page("Deployment-Zettel", body, user, roles, "projects")


HELP_BODY = f"""
<h1>Hilfe</h1>
<div class="card">
  <h2>Wozu das Studio da ist</h2>
  <p>Das Studio ist das Verzeichnis eurer App-Vorhaben — und der Ort, an dem
  aus euren Antworten ein <b>Briefing</b> für eine KI entsteht. Das Briefing
  enthält den fachlichen Auftrag und alle technischen Regeln, die eine App
  auf dieser Plattform erfüllen muss. Ihr müsst diese Regeln nicht kennen;
  das Studio schreibt sie mit.</p>
</div>
<div class="card">
  <h2>Der Weg von der Idee zur App</h2>
  <ol style="line-height:1.8;padding-left:1.2rem">
    <li><b>Vorhaben anlegen</b> — Name, wer es benutzt, was es können muss.</li>
    <li><b>Briefing erzeugen</b> — herunterladen, ins Projektverzeichnis legen.</li>
    <li><b>KI starten</b> und mit dem Briefing arbeiten lassen.</li>
    <li><b>Testen</b> — die KI rollt getestete Stände über den Deploy-Hook
        selbst auf die Test-Instanz aus.</li>
    <li><b>Produktiv setzen</b> — bleibt bewusst Handarbeit eines Menschen
        (mit Versions-Sprung).</li>
  </ol>
</div>
<div class="card">
  <h2>Zwei Wege auf die Test-Instanz</h2>
  <p><b>Git</b> — der Knoten holt sich den Stand selbst aus dem Repository.
  Der gewohnte Weg, richtig überall dort, wo das Repository erreichbar ist
  und der Zugang Dir gehört.</p>
  <p><b>Paket (ZIP)</b> — die KI liefert ein fertiges Paket ab, Du lädst es
  hier hoch. Der richtige Weg bei einem <b>privaten Repository</b>: Beim
  Git-Weg müsste die Plattform einen fremden Zugangs-Token im Klartext
  aufbewahren (und damit in jedem Backup). Beim Paket-Weg bekommt sie ein
  Artefakt statt eines Zugangsrechts — sie hält nichts Fremdes.
  Er funktioniert außerdem ganz ohne Internet: Datei vom Stick, Browser im
  LAN. Nachzulesen in RFC-0019.</p>
</div>
<div class="card">
  <h2>Wie ein Paket ankommt (drei Phasen)</h2>
  <ol style="line-height:1.8;padding-left:1.2rem">
    <li><b>Anmelden</b> — Version, vollständiges Manifest, Prüfsumme und
    Größe. Der Knoten prüft das, <i>bevor</i> irgendetwas übertragen wird.</li>
    <li><b>Freigabe</b> — bei Erfolg gibt er ein Einmal-Token zurück,
    15 Minuten gültig, an genau diese Instanz und genau diese Prüfsumme
    gebunden.</li>
    <li><b>Hochladen</b> — nur damit. Danach prüft er erneut: Größe,
    Prüfsumme, und ob das Manifest <i>in</i> der ZIP zeichengleich zum
    angemeldeten ist.</li>
  </ol>
  <p class="muted">Abgelehnt wird hart bei geänderter App-Kennung, ungültigem
  Manifest und <b>unveränderter Version</b>. Wer den Rahmen erweitert — neue
  öffentliche Routen, neue Speicher, neue Ports am Gateway vorbei —, braucht
  die Bestätigung eines <code>server_admin</code> im Portal. Alles andere
  läuft durch; das ist der Normalfall.</p>
</div>
<div class="card">
  <h2>Wichtig zum Deploy-Token</h2>
  <p>Das Token ist ein Schlüssel: Es erlaubt, die <b>Test</b>-Instanz neu
  auszurollen. Es wird im <b>Portal</b> auf der Instanzseite erzeugt,
  einmalig angezeigt und gehört <b>Dir</b> — Passwortmanager ist der
  richtige Ort.</p>
  <p><b>Das Studio speichert es nicht.</b> Wenn Du hier ein Paket ausrollst,
  gibst Du den Token bei jedem Upload einzeln ein; er wird für die Dauer
  dieser einen Anfrage gehalten und danach vergessen. Damit steckt in jedem
  Deployment über das Studio ein Mensch — und im Ruhezustand gibt es hier
  nichts zu holen. Ist der Token abhandengekommen: im Portal widerrufen und
  ein neues erzeugen (<code>sudo oaap app token revoke &lt;instanz&gt;</code>
  tut dasselbe auf der Maschine).</p>
</div>
<div class="card">
  <h2>Die verbindlichen Regeln</h2>
  <p class="muted">Der App Deployment Contract beschreibt technisch, was eine
  App mitbringen muss. Das Briefing verweist darauf:
  <br><code>{esc(CONTRACT_URL)}</code></p>
</div>"""


# ------------------------------------------------------- deployment sheet

TOKEN_PLACEHOLDER = "<dein Deploy-Token>"


def deploy_sheet_steps(hook, instance):
    """Der Drei-Phasen-Ablauf als Befehle — kurz, für das Briefing."""
    u = deployer.hook_urls(hook)
    return [
        "Die Instanz wird aus einem **Paket** aufgebaut, nicht aus dem",
        "Repository (RFC-0019). Der Ablauf hat drei Phasen:",
        "",
        "```sh",
        "# 0. Paket bauen: das Projektverzeichnis als ZIP, mit",
        "#    oaap-app.yaml in der Wurzel oder in genau einem Oberordner",
        f"ZIP=paket.zip",
        "SHA=$(sha256sum \"$ZIP\" | cut -d' ' -f1)",
        "BYTES=$(stat -c%s \"$ZIP\")",
        "",
        "# 1. Anmelden — Manifest, Prüfsumme und Größe ankündigen",
        f"curl -sS -X POST {u['announce']} \\",
        "  -H \"Authorization: Bearer $TOKEN\" \\",
        "  -H 'Content-Type: application/json' \\",
        "  -d \"$(jq -n --rawfile m oaap-app.yaml --arg s \"$SHA\" \\",
        "        --argjson b \"$BYTES\" \\",
        "        '{manifest:$m, artifact_sha256:$s, artifact_bytes:$b}')\"",
        "",
        "# 2. Antwort enthält upload_token und upload_url (15 Minuten gültig)",
        "",
        "# 3. Hochladen — nur mit diesem Einmal-Token",
        "curl -sS -X PUT \"$UPLOAD_URL\" \\",
        "  -H \"Authorization: Bearer $UPLOAD_TOKEN\" \\",
        "  -H 'Content-Type: application/zip' \\",
        "  --data-binary @\"$ZIP\"",
        "```",
        "",
        "HTTP 202 heißt „läuft noch\" — dann den Status abfragen:",
        f"`GET {u['status']}` mit dem Deploy-Token.",
    ]


def deployment_sheet(p, token=""):
    """Das Blatt für die Projekt-KI: Adressen, Ablauf, Regeln.

    Der Token steht nur drin, wenn ihn der Anwender in genau diesem
    Augenblick eingegeben hat — das Studio kennt ihn nicht und legt ihn
    nicht ab (RFC-0019, Entscheidung 8).
    """
    instance = (p["instance"] or "").strip() or "<instanz>"
    hook = (p["hook_url"] or "").strip()
    test_url = (p["test_url"] or "").strip()
    artifact_way = (p["deploy_way"] or "git") == "artifact"
    tok = token or TOKEN_PLACEHOLDER

    lines = [
        f"# Deployment-Zettel: {p['name']}",
        "",
        f"Erzeugt vom OAAP Studio am {now()} — Vorhaben `{p['id']}`,",
        f"Instanz `{instance}`.",
        "",
        "Dieses Blatt beantwortet eine Frage: **Wie kommt ein getesteter",
        "Stand auf die Test-Instanz?** Es gehört neben das Briefing ins",
        "Projektverzeichnis.",
        "",
        "## Adressen",
        "",
    ]
    if hook:
        u = deployer.hook_urls(hook)
        lines += [
            f"- **Deploy-Hook:** `{u['deploy']}`",
            f"- **Anmelden:** `POST {u['announce']}`",
            f"- **Hochladen:** `PUT {u['artifact']}`",
            f"- **Status:** `GET {u['status']}`",
        ]
    else:
        lines += [
            "- **Deploy-Hook:** _noch nicht eingerichtet_ — er entsteht im",
            "  Portal auf der Instanzseite, sobald dort ein Deploy-Token",
            "  erzeugt wird.",
        ]
    lines += [
        f"- **Test-Adresse:** {test_url or '_noch nicht bekannt_'}",
        f"- **Verbindliche Regeln:** {CONTRACT_URL}",
        "",
        "## Token",
        "",
        f"```\nTOKEN={tok}\n```",
        "",
    ]
    if token:
        lines += [
            "Dieser Token wurde **einmalig** ausgegeben und ist nirgends",
            "gespeichert — weder im Studio noch in dessen Backup. Bewahre ihn",
            "wie ein Passwort auf: nicht ins Repository, nicht in einen Brief,",
            "nicht in einen Commit. Er gilt nur für diese eine Instanz und nur",
            "für den Test-Kanal.",
        ]
    else:
        lines += [
            "Der Token wird **getrennt** übergeben. Er gilt nur für diese eine",
            "Instanz und nur für den Test-Kanal; ins Repository gehört er",
            "nie.",
        ]
    lines += ["", "## Ablauf", ""]
    if hook and artifact_way:
        lines += deploy_sheet_steps(hook, instance)
    elif hook:
        lines += [
            "Die Instanz holt sich den Stand selbst aus dem Repository:",
            "",
            "```sh",
            "git push",
            f"curl -sS -X POST {hook} -H \"Authorization: Bearer $TOKEN\"",
            "```",
            "",
            "HTTP 202 heißt „läuft noch\" — dann `GET " + hook + "/status`.",
            "",
            "Soll stattdessen ein **Paket** hochgeladen werden (privates",
            "Repository, kein Git-Zugang, offline), steht im Vorhaben der Weg",
            "auf „Paket\" um — dann gilt der Drei-Phasen-Ablauf aus RFC-0019.",
        ]
    else:
        lines += ["_Sobald der Hook eingerichtet ist, steht hier der Ablauf._"]

    lines += [
        "",
        "## Was abgelehnt wird",
        "",
        "Die Regel dahinter: *Ein Deploy-Token rollt innerhalb des bereits",
        "erteilten Rahmens neu aus; alles, was den Rahmen erweitert, braucht",
        "einen Menschen.*",
        "",
        "**Hart abgelehnt (kein Weg drumherum):**",
        "",
        "- andere `app.id` als die installierte Instanz",
        "- Manifest ungültig gegen das Schema",
        "- Manifest in der ZIP ≠ angemeldetes Manifest",
        "- Prüfsumme oder Größe stimmen nicht",
        "- **unveränderte `app.version`** — ohne Commit-Hash ist die Version",
        "  das Einzige, was „was läuft da?\" beantwortet. Also: vor jedem",
        "  Deployment die Version hochzählen.",
        "",
        "**Abgelehnt, bis ein Mensch bestätigt** (`server_admin` im Portal,",
        "auf der Instanzseite):",
        "",
        "- eine Route wird `public`, die es nicht war",
        "- eine Gruppen-Einschränkung fällt weg oder wird weiter",
        "- ein neuer fester Port oder Endpunkt am Gateway vorbei",
        "- eine neue Verbindung zu einer anderen App",
        "- ein neuer Speicher-Mount oder ein geänderter Pfad",
        "",
        "**Läuft ohne Rückfrage durch:** alles andere — neue",
        "Konfigurationsschlüssel mit Vorgabewert, geänderte Texte, interne",
        "Umbauten. Das ist der Normalfall.",
        "",
        "**Beim allerersten Paket** gibt es nichts zu erweitern: Die Instanz",
        "entsteht erst, und was das Manifest verlangt, ist der Rahmen, dem",
        "der Mensch mit der Anlege-Erlaubnis zugestimmt hat. Ab dem zweiten",
        "Paket gelten die Regeln oben.",
        "",
        "## Wenn etwas abgelehnt wird",
        "",
        "Die Antwort enthält immer beides: einen maschinenlesbaren Grund",
        "(`refused`) und einen Satz, der sagt, was zu ändern ist. Lies beides",
        "und korrigiere selbst — es steht kein Mensch daneben. Bleibt es",
        "unklar, schreib einen Brief in den Postkasten",
        "(`collab/letters/`), statt zu raten.",
        "",
        "## Grenzen",
        "",
        "- Das Paket ist eine ZIP mit `oaap-app.yaml` in der Wurzel oder in",
        "  genau einem Oberordner.",
        "- Keine absoluten Pfade, kein `..`, keine Symlinks im Archiv — der",
        "  Knoten entpackt so nicht.",
        "- Das Einmal-Token gilt 15 Minuten und genau einmal.",
        "- **Produktiv gibt es keinen Token.** Die Produktivsetzung bleibt",
        "  eine menschliche Handlung mit Versions-Sprung.",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- briefing

def briefing(p):
    """Generate the AI briefing for one project.

    Everything an agent needs to start: the business assignment in the
    owner's words, the binding platform rules (by reference, so the
    contract stays the single source of truth), the repository and
    mailbox conventions, and the deploy hook — deliberately without the
    token, which is handed over out of band.
    """
    def block(value, fallback):
        value = (value or "").strip()
        return value if value else f"_{fallback}_"

    repo = (p["repo_url"] or "").strip()
    repo_line = repo or "_noch nicht angelegt — bitte beim Auftraggeber erfragen_"
    hook = (p["hook_url"] or "").strip()
    instance = (p["instance"] or "").strip() or "<instanz>"
    test_url = (p["test_url"] or "").strip()

    artifact_way = (p["deploy_way"] or "git") == "artifact"
    deploy = [
        "## 6. Test-Deployment (Deploy-Hook)",
        "",
        "Getestete Stände rollst du selbst auf die **Test-Instanz** aus —",
        "Produktivsetzung bleibt eine menschliche Entscheidung mit",
        "Versions-Sprung.",
        "",
    ]
    if not hook:
        deploy += [
            "Der Hook ist für dieses Vorhaben noch nicht eingerichtet.",
            "Die Plattform-Administration erzeugt ihn im Portal auf der",
            f"Instanzseite von `{instance}` und nennt dir Adresse und Token.",
        ]
    elif artifact_way:
        deploy += deploy_sheet_steps(hook, instance) + [
            "",
            "Die ausführliche Fassung mit allen Ablehnungsgründen steht im",
            "**Deployment-Zettel**, den du zu diesem Briefing bekommst.",
        ]
    else:
        deploy += [
            "```sh",
            "git push                       # erst den Stand veröffentlichen",
            f"curl -X POST {hook} \\",
            '  -H "Authorization: Bearer <dein Deploy-Token>"',
            "```",
            "",
            "HTTP 202 heißt „läuft noch\" — dann den Status abfragen:",
            f"`GET {hook}/status`.",
        ]
    if test_url:
        deploy += ["", f"Testen kannst du danach unter: {test_url}"]
    deploy += [
        "",
        "Das Token bekommst du separat (nie im Repository ablegen, nie in",
        "einen Brief schreiben, nicht in Commits committen).",
    ]

    lines = [
        f"# Briefing: {p['name']}",
        "",
        f"Erzeugt vom OAAP Studio am {now()} — Vorhaben `{p['id']}`.",
        "",
        "Du bist der KI-Entwicklungsagent für dieses Vorhaben. Dieses",
        "Dokument ist deine Arbeitsanweisung: fachlicher Auftrag, geltende",
        "Regeln und die Art, wie wir zusammenarbeiten. Lies es vollständig,",
        "bevor du Code schreibst, und frag nach, wenn etwas fehlt.",
        "",
        "## 1. Worum es geht",
        "",
        block(p["goal"], "Ziel noch nicht beschrieben — bitte beim Auftraggeber erfragen."),
        "",
        f"**Auftraggeber:** {block(p['owner'], 'nicht benannt')}",
        f"**Kontext:** {block(p['context'], 'nicht angegeben')}",
        "",
        "## 2. Wer die App benutzt",
        "",
        block(p["target_users"], "Zielanwender noch nicht beschrieben — bitte erfragen."),
        "",
        "## 3. Was die erste Version können muss",
        "",
        block(p["scope"], "Umfang noch nicht festgelegt — bitte gemeinsam schärfen."),
        "",
        "Halte dich an diesen Umfang. Ideen darüber hinaus schreibst du auf,",
        "statt sie einzubauen.",
        "",
        "## 4. Die Plattform, auf der die App läuft",
        "",
        "Die App läuft auf **OAAP** — Container hinter einem zentralen",
        "Gateway, das **die gesamte Anmeldung erledigt**. Verbindlich ist der",
        "App Deployment Contract:",
        "",
        f"  {CONTRACT_URL}",
        "",
        "**Lies ihn und arbeite danach.** Das Wichtigste in Kürze:",
        "",
        "- **Kein eigener Login.** Die geprüfte Identität kommt als Header",
        "  `X-OAAP-User` und `X-OAAP-Roles`; sie sind nicht fälschbar.",
        "  Niemals ein Anmeldeformular bauen.",
        "- **Ein HTTP-Port**, kein TLS in der App (das macht das Gateway).",
        "- **Persistenz nur in deklarierten Mounts** (`storage` im Manifest).",
        "- **Konfiguration nur über deklarierte Umgebungsvariablen**,",
        "  Geheimnisse mit `secret: true`.",
        "- **Logs nach stdout**, dazu ein Health-Endpunkt.",
        "- **Mehrfach-instanzfähig und offline-fähig** — keine festen",
        "  Hostnamen, keine absoluten URLs, kein Internetzwang zur Laufzeit.",
        "",
        f"**App-Typ dieses Vorhabens:** `{p['app_type']}` — {APP_TYPES.get(p['app_type'], '')}",
        "",
        "Liefere im Repository ein gültiges `oaap-app.yaml` (Manifest) und,",
        "bei App-Typ `native`, ein `Dockerfile` je Service. Das Manifest muss",
        "gegen das veröffentlichte JSON-Schema validieren; das Image muss auf",
        "amd64 **und** arm64 bauen.",
        "",
        "## 5. Oberfläche",
        "",
        "Die App soll aussehen, als gehöre sie zur Plattform:",
        "",
        "- Deutsch als Oberflächensprache, Blau als Leitfarbe",
        "  (`#2563eb`, dunkler Kopf `#1e3a8a`), Systemschriften,",
        "  **keine externen Ressourcen** (keine Webfonts, keine CDNs).",
        "- **Tablet zuerst**: Bedienelemente mindestens 44 px hoch.",
        "- **Listen zeigen, Objektseiten pflegen** — Formulare gehören nie",
        "  in Tabellenzeilen. Nach dem Speichern umleiten (kein erneutes",
        "  Absenden beim Neuladen).",
        "",
    ] + deploy + [
        "",
        "## 7. Repository und Zusammenarbeit",
        "",
        f"**Repository:** {repo_line}",
        "",
        "- Default-Branch ist **`main`**.",
        "- Committe selbstständig in kleinen, nachvollziehbaren Schritten",
        "  mit aussagekräftigen Nachrichten.",
        "- **Postkasten:** Rückfragen, Testergebnisse und Befunde laufen als",
        "  Markdown-Briefe im Repository unter `collab/letters/` bzw.",
        "  `collab/reports/`. Regeln: zu Sitzungsbeginn **immer erst",
        "  `git pull`**, Briefe sind unveränderlich (Antwort = neuer Brief",
        "  mit `re:`-Betreff), Brief sofort committen und pushen, und so",
        "  schreiben, dass die beteiligten Menschen mitlesen können.",
        "- Schreib einen ersten Brief, sobald du das Briefing gelesen hast:",
        "  was du verstanden hast, was du zuerst baust, was dir fehlt.",
        "",
        "## 8. Wenn etwas unklar ist",
        "",
        "Rate nicht bei fachlichen Fragen — leg einen Brief in den Postkasten",
        "und arbeite so lange an dem weiter, was klar ist. Technische",
        "Unklarheiten zur Plattform beantwortet der Contract; bleibt eine",
        "Lücke, schreib sie in einen Brief (das verbessert die Plattform).",
        "",
        "---",
        "",
        f"*Status des Vorhabens: {STATUSES.get(p['status'], p['status'])}*",
    ]
    if (p["notes"] or "").strip():
        lines += ["", "*Notizen des Auftraggebers:*", "", p["notes"].strip()]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "oaap-studio/" + VERSION
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt, *args):  # stdout, contract rule 5
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def send_html(self, body, status=200, content_type="text/html; charset=utf-8",
                  extra_headers=()):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        # The app renders only its own data, never third-party content.
        self.send_header("Content-Security-Policy",
                         "default-src 'none'; style-src 'unsafe-inline'; img-src data:; form-action 'self'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def identity(self):
        """Verified identity from the gateway (contract guarantee 1)."""
        user = self.headers.get("X-OAAP-User", "")
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles", "") or "").split(",") if r.strip()}
        return user, roles

    def deny_without_gateway(self):
        """Without gateway headers the app has no identity — say so.

        The app never renders a login (contract rule 1); running it
        outside OAAP is a configuration mistake, not a use case.
        """
        self.send_html(
            "<!doctype html><meta charset='utf-8'><title>OAAP Studio</title>"
            + STYLE +
            "<main style='max-width:34rem;margin:4rem auto;padding:0 1.2rem'>"
            "<div class='card'><h2>Diese App läuft nur hinter dem OAAP-Gateway</h2>"
            "<p class='muted'>Es sind keine geprüften Identitätsdaten angekommen. "
            "Das Studio hat bewusst keinen eigenen Login — die Anmeldung erledigt "
            "die Plattform. Bitte über das Portal-Launchpad öffnen.</p></div></main>",
            status=403)

    def read_form(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 1_000_000:
            return {}
        raw = self.rfile.read(length).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def cross_site_post(self):
        """Reject cross-site form posts (CSRF defence in depth).

        The platform's session cookie is already SameSite=Lax, which
        keeps browsers from sending it with a foreign form post. This
        is the second layer, owned by the app itself: browsers announce
        the context in Sec-Fetch-Site, older ones at least send Origin.
        Both absent (curl, tests) is treated as same-site.
        """
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "same-site", "none"):
            return True
        origin = self.headers.get("Origin")
        if origin:
            host = self.headers.get("Host", "")
            return urlparse(origin).netloc != host
        return False

    # -- routing ---------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        query = parse_qs(urlparse(self.path).query)

        # Health first: no identity, no side effects (contract rule 6).
        if path == "/healthz":
            self.send_html(json.dumps({"status": "ok", "version": VERSION}),
                           content_type="application/json")
            return

        user, roles = self.identity()
        if not user:
            return self.deny_without_gateway()
        if not roles & {"keyuser", "admin"}:
            return self.send_html("<p>Keine Berechtigung für das Studio.</p>", status=403)
        roles_label = ", ".join(sorted(roles))
        con = db()
        try:
            if path == "/":
                rows = con.execute(
                    "SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
                return self.send_html(list_page(rows, user, roles_label,
                                                (query.get("msg") or [""])[0]))
            if path == "/hilfe":
                return self.send_html(page("Hilfe", HELP_BODY, user, roles_label, "help"))
            if path == "/vorhaben/neu":
                empty = {f: "" for f in FIELDS}
                empty.update(app_type="native", status="idee")
                return self.send_html(new_page(empty, user, roles_label))

            m = re.fullmatch(
                r"/vorhaben/([a-z0-9-]{1,40})"
                r"(/briefing(\.md)?|/zettel(\.md)?|/paket|/loeschen)?", path)
            if m:
                p = con.execute("SELECT * FROM projects WHERE id = ?", (m.group(1),)).fetchone()
                if not p:
                    return self.send_html("<p>Vorhaben nicht gefunden.</p>", status=404)
                sub = m.group(2) or ""
                if sub == "/briefing":
                    return self.send_html(briefing_page(p, briefing(p), user, roles_label))
                if sub == "/briefing.md":
                    return self.send_html(
                        briefing(p), content_type="text/markdown; charset=utf-8",
                        extra_headers=[("Content-Disposition",
                                        f'attachment; filename="{p["id"]}-briefing.md"')])
                if sub == "/zettel":
                    return self.send_html(
                        sheet_page(p, deployment_sheet(p), user, roles_label))
                if sub == "/zettel.md":
                    # Bewusst ohne Token: eine Datei mit einem Schlüssel darin
                    # wandert durch Verzeichnisse, in denen sie nichts zu
                    # suchen hat. Mit Token gibt es das Blatt nur auf dem
                    # Bildschirm, nach ausdrücklicher Eingabe.
                    return self.send_html(
                        deployment_sheet(p),
                        content_type="text/markdown; charset=utf-8",
                        extra_headers=[("Content-Disposition",
                                        f'attachment; filename="{p["id"]}-deployment.md"')])
                if sub == "/paket":
                    rep = loads(p["pkg_report"])
                    deploys = con.execute(
                        "SELECT * FROM deployments WHERE project_id = ?"
                        " ORDER BY id DESC LIMIT 20", (p["id"],)).fetchall()
                    return self.send_html(package_page(
                        p, rep, deploys, user, roles_label,
                        (query.get("msg") or [""])[0],
                        (query.get("fehler") or [""])[0],
                        portal_base(self.headers.get("Host", ""))))
                if sub == "/loeschen":
                    if "admin" not in roles:
                        return self.send_html("<p>Löschen ist Administratoren vorbehalten.</p>",
                                              status=403)
                    return self.send_html(delete_page(p, user, roles_label))
                return self.send_html(object_page(p, user, roles_label,
                                                  (query.get("msg") or [""])[0],
                                                  is_admin="admin" in roles))
            self.send_html("<p>Seite nicht gefunden.</p>", status=404)
        finally:
            con.close()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        user, roles = self.identity()
        if not user:
            return self.deny_without_gateway()
        if not roles & {"keyuser", "admin"}:
            return self.send_html("<p>Keine Berechtigung für das Studio.</p>", status=403)
        if self.cross_site_post():
            return self.send_html("<p>Abgelehnt: Anfrage kam von einer fremden Seite.</p>",
                                  status=403)
        roles_label = ", ".join(sorted(roles))

        # Der Paket-Upload kommt als multipart und wird im Fluss gelesen —
        # deshalb VOR read_form(), das den Körper verbrauchen würde.
        m = re.fullmatch(r"/vorhaben/([a-z0-9-]{1,40})/paket", path)
        if m:
            con = db()
            try:
                p = con.execute("SELECT * FROM projects WHERE id = ?",
                                (m.group(1),)).fetchone()
                if not p:
                    return self.send_html("<p>Vorhaben nicht gefunden.</p>", status=404)
                return self.package(con, p, user, roles_label)
            finally:
                con.close()

        form = self.read_form()
        con = db()
        try:
            if path == "/vorhaben":
                return self.create(con, form, user, roles_label)
            m = re.fullmatch(r"/vorhaben/([a-z0-9-]{1,40})/zettel", path)
            if m:
                p = con.execute("SELECT * FROM projects WHERE id = ?",
                                (m.group(1),)).fetchone()
                if not p:
                    return self.send_html("<p>Vorhaben nicht gefunden.</p>", status=404)
                # Kein Redirect: Der Token darf in keine URL. Diese Seite
                # ist die einzige Stelle, an der er auftaucht, und sie
                # entsteht genau einmal, aus der Eingabe des Anwenders.
                token = (form.get("token") or "").strip()
                print(f"deployment sheet rendered: {p['id']} by {user}"
                      f" (mit Token: {'ja' if token else 'nein'})", flush=True)
                return self.send_html(sheet_page(
                    p, deployment_sheet(p, token), user, roles_label,
                    with_token=bool(token)))
            m = re.fullmatch(r"/vorhaben/([a-z0-9-]{1,40})(/loeschen)?", path)
            if m:
                p = con.execute("SELECT * FROM projects WHERE id = ?", (m.group(1),)).fetchone()
                if not p:
                    return self.send_html("<p>Vorhaben nicht gefunden.</p>", status=404)
                if m.group(2):
                    if "admin" not in roles:
                        return self.send_html("<p>Löschen ist Administratoren vorbehalten.</p>",
                                              status=403)
                    con.execute("DELETE FROM projects WHERE id = ?", (p["id"],))
                    con.commit()
                    print(f"project deleted: {p['id']} by {user}", flush=True)
                    return self.redirect("../../?msg=Vorhaben+gel%C3%B6scht")
                return self.update(con, p, form, user, roles_label)
            self.send_html("<p>Seite nicht gefunden.</p>", status=404)
        finally:
            con.close()

    # -- packages ---------------------------------------------------------
    def package(self, con, p, user, roles_label):
        """Ein Paket annehmen: prüfen, übernehmen, auf Wunsch ausrollen.

        Der Token (falls einer eingegeben wurde) lebt in dieser Methode
        und nirgends sonst: Er geht nicht in die Datenbank, nicht in eine
        Weiterleitung, nicht in eine Logzeile.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        try:
            fields, files = multipart.parse(
                self.rfile, self.headers.get("Content-Type"), length,
                MAX_PACKAGE_BYTES + (1 << 20), TMP_DIR)
        except multipart.MultipartError as e:
            # Der Körper ist womöglich nur halb gelesen — auf einer
            # wiederverwendeten Verbindung läse die nächste Anfrage
            # seinen Rest als ihren Anfang. Also Schluss damit.
            self.close_connection = True
            return self.redirect(f"paket?fehler={quote(str(e))}")

        upload = files.get("paket")
        try:
            if not upload or not upload["bytes"]:
                return self.redirect(
                    "paket?fehler=" + quote("Es kam keine Datei an."))
            # Verglichen wird mit dem zuletzt AUSGEROLLTEN Paket — das ist
            # die beste Näherung an den installierten Stand, den nur der
            # Knoten kennt.
            previous = loads(p["dep_summary"])
            try:
                result = pkg.inspect(upload["path"], MAX_PACKAGE_BYTES, previous)
            except pkg.PackageError as e:
                print(f"package rejected: {p['id']} ({upload['filename']}) "
                      f"by {user}: {e}", flush=True)
                return self.redirect(f"paket?fehler={quote(str(e))}")

            report = {
                "file": upload["filename"], "bytes": result["bytes"],
                "sha256": result["sha256"], "entries": result["entries"],
                "uncompressed": result["uncompressed"], "root": result["root"],
                "summary": result["summary"], "findings": result["findings"],
                "counts": result["counts"],
                "envelope_hard": result["envelope_hard"],
                "envelope_confirm": result["envelope_confirm"],
                "deployable": result["deployable"],
                "compared": bool(previous),
                "at": now(), "by": user,
            }
            s = result["summary"]
            sets = {
                "pkg_file": upload["filename"], "pkg_version": s["version"],
                "pkg_sha256": result["sha256"], "pkg_bytes": result["bytes"],
                "pkg_at": report["at"], "pkg_by": user,
                "pkg_report": json.dumps(report, ensure_ascii=False),
                # Das zuletzt GEPRÜFTE Paket. Merkposten, keine
                # Vergleichsbasis — die ist `dep_summary`.
                "pkg_summary": json.dumps(s, ensure_ascii=False),
            }
            taken = ""
            if fields.get("uebernehmen"):
                # Das Manifest ist die Wahrheit über das Paket — die
                # Angaben des Vorhabens dürfen ihm folgen. Nur der
                # Instanzname wird NICHT überschrieben: Er benennt etwas
                # auf dem Knoten, das dem Paket nicht gehört.
                sets.update(app_id=s["id"], app_type=s["type"] or p["app_type"],
                            deploy_way="artifact")
                if not (p["instance"] or "").strip() and s["id"]:
                    sets["instance"] = f"{s['id']}-test"
                taken = " Angaben aus dem Manifest übernommen."
            con.execute(
                f"UPDATE projects SET {', '.join(k + ' = ?' for k in sets)},"
                " updated_at = ?, updated_by = ? WHERE id = ?",
                (*sets.values(), now(), user, p["id"]))
            con.commit()
            print(f"package checked: {p['id']} {s['id']} {s['version']} "
                  f"sha={result['sha256'][:12]} "
                  f"findings={result['counts']} by {user}", flush=True)

            if fields.get("action") != "deployen":
                return self.redirect(
                    "paket?msg=" + quote("Paket geprüft." + taken))

            token = (fields.get("token") or "").strip()
            if not token:
                return self.redirect("paket?fehler=" + quote(
                    "Zum Ausrollen fehlt der Deploy-Token. Er wird im Portal "
                    "auf der Instanzseite erzeugt und hier bei jedem Upload "
                    "einzeln eingegeben. Gibt es die Instanz noch nicht, "
                    "gehört hier die Anlege-Erlaubnis aus dem Portal hinein."))
            if not result["deployable"]:
                return self.redirect("paket?fehler=" + quote(
                    "Nicht ausgerollt: Das Paket hat Fehler, an denen der "
                    "Knoten ohnehin scheitern würde. Sie stehen unten in der "
                    "Prüfung."))
            try:
                outcome = deployer.deploy(
                    p["hook_url"], token, result["manifest_text"],
                    result["sha256"], upload["path"], s["version"],
                    DEPLOY_TIMEOUT)
            except deployer.DeployError as e:
                # Keine Antwort ist KEINE Ablehnung. Auf einem kleinen
                # Knoten kann der erste Bau eines Images länger dauern,
                # als ein Browser oder dieses Studio wartet — der Knoten
                # baut derweil weiter und rollt aus. Das als „abgelehnt"
                # zu führen wäre eine Falschaussage über eine Instanz,
                # die es hinterher gibt (am 16.08. auf oaap-test genau
                # so passiert: Knoten fertig, Studio im Zeitfehler).
                unklar = (
                    f"{e} — der Knoten baut möglicherweise weiter. "
                    "Das verbindliche Protokoll führt der Knoten: im "
                    "Portal unter der Instanz nachsehen, bevor Du es "
                    "erneut versuchst.")
                self.record_deploy(con, p, s["version"], result["sha256"],
                                   None, "", unklar, user)
                return self.redirect(f"paket?fehler={quote(unklar)}")
            last = outcome["steps"][-1]
            self.record_deploy(con, p, s["version"], result["sha256"],
                               outcome["ok"], last["phase"],
                               last.get("message") or last.get("hint") or "",
                               user)
            if outcome["ok"]:
                # Ab jetzt ist DAS der Stand, gegen den das nächste Paket
                # gehalten wird.
                con.execute("UPDATE projects SET dep_summary = ? WHERE id = ?",
                            (json.dumps(s, ensure_ascii=False), p["id"]))
                con.commit()
            return self.send_html(deploy_result_page(p, outcome, user, roles_label))
        finally:
            multipart.cleanup(files)

    def record_deploy(self, con, p, version, sha, ok, phase, message, user):
        """Ein Versuch im Verzeichnis. `ok=None` heißt „Ausgang unklar" —
        das Studio hat keine Antwort bekommen und weiß es nicht."""
        con.execute(
            "INSERT INTO deployments (project_id, at, by, version, sha256,"
            " ok, phase, message) VALUES (?,?,?,?,?,?,?,?)",
            (p["id"], now(), user, version, sha,
             UNKLAR if ok is None else (1 if ok else 0), phase,
             (message or "")[:500]))
        con.commit()
        print(f"deployment: {p['id']} version={version} sha={sha[:12]} "
              f"ok={'unklar' if ok is None else bool(ok)} phase={phase} "
              f"by {user}", flush=True)

    # -- write operations -------------------------------------------------
    def values(self, form):
        v = {f: (form.get(f) or "").strip() for f in FIELDS}
        if v["app_type"] not in APP_TYPES:
            v["app_type"] = "native"
        if v["status"] not in STATUSES:
            v["status"] = "idee"
        if v["deploy_way"] not in DEPLOY_WAYS:
            v["deploy_way"] = "git"
        return v

    def create(self, con, form, user, roles_label):
        v = self.values(form)
        if not v["name"]:
            return self.send_html(new_page(v, user, roles_label,
                                           "Bitte einen Namen für das Vorhaben angeben."))
        base = slugify(v["name"]) or "vorhaben"
        pid, n = base, 2
        while con.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
            pid, n = f"{base[:36]}-{n}", n + 1
        stamp = now()
        con.execute(
            f"INSERT INTO projects (id, {', '.join(FIELDS)}, created_at, created_by,"
            f" updated_at, updated_by) VALUES (?{', ?' * (len(FIELDS) + 4)})",
            (pid, *[v[f] for f in FIELDS], stamp, user, stamp, user))
        con.commit()
        print(f"project created: {pid} by {user}", flush=True)
        # relative to POST /vorhaben, whose base is "/" — hence the prefix
        return self.redirect(f"vorhaben/{pid}?msg=Vorhaben+angelegt")

    def update(self, con, p, form, user, roles_label):
        v = self.values(form)
        if not v["name"]:
            merged = dict(p)
            merged.update(v)
            return self.send_html(object_page(merged, user, roles_label, error="Bitte einen Namen angeben."))
        con.execute(
            f"UPDATE projects SET {', '.join(f + ' = ?' for f in FIELDS)},"
            " updated_at = ?, updated_by = ? WHERE id = ?",
            (*[v[f] for f in FIELDS], now(), user, p["id"]))
        con.commit()
        print(f"project updated: {p['id']} by {user}", flush=True)
        return self.redirect(f"./{p['id']}?msg=Gespeichert")


def main():
    db().close()  # fail fast if /data is not writable
    print(f"OAAP Studio {VERSION} listening on 0.0.0.0:{PORT}, data in {DATA_DIR}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
