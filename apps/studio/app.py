"""OAAP Studio 0.1 — registry of app development projects.

First increment of the Studio (see program/studio/ideas.md): manage
development projects and generate the AI briefing that starts them.

Built as a normal OAAP app according to the App Deployment Contract
v0.4 — the platform's own functions are apps, like everyone else's:
no own login (identity arrives as gateway headers), one HTTP port,
persistence only under /data, configuration via declared env vars,
logs to stdout, health endpoint, instance-safe, offline-first.

Standard library only, on purpose: the package is built on the target
node (also arm64), so a dependency-free build has nothing to resolve,
nothing to compile and no supply chain.

Look & feel follows oaap-design/docs/design-guidelines.md v0.1 —
blue palette, hexagon mark, German UI, floorplans (list report,
object page, dialog page), no external resources.
"""

import html
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# Development override only — not operator configuration (contract
# rule 4: operator config comes from the manifest's declared env vars).
DATA_DIR = os.environ.get("STUDIO_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "studio.db")
PORT = 8000

# Declared configuration (manifest `config`)
CONTRACT_URL = os.environ.get(
    "STUDIO_CONTRACT_URL",
    "https://github.com/MDJoerg/oaap-spec/blob/main/docs/app-deployment-contract.md")
GIT_BASE = os.environ.get("STUDIO_GIT_BASE", "").rstrip("/")

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
          "hook_url", "test_url", "notes")


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
  OAAP Studio {VERSION} — Vorstufe des Studios: Vorhaben verwalten, Briefings erzeugen
</footer>
</html>"""


VERSION = "0.1.0"


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
              "Es wird einmalig erzeugt und direkt der KI übergeben.")}
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
  <h2>Wichtig zum Deploy-Token</h2>
  <p>Das Token ist ein Schlüssel: Es erlaubt der KI, die <b>Test</b>-Instanz
  neu auszurollen. Es wird auf dem Server erzeugt
  (<code>sudo oaap app token create &lt;instanz&gt;</code>), einmalig angezeigt
  und direkt der KI übergeben — das Studio speichert es bewusst nicht.
  Wurde es weitergegeben oder ist es abhandengekommen:
  <code>sudo oaap app token revoke &lt;instanz&gt;</code> und ein neues erzeugen.</p>
</div>
<div class="card">
  <h2>Die verbindlichen Regeln</h2>
  <p class="muted">Der App Deployment Contract beschreibt technisch, was eine
  App mitbringen muss. Das Briefing verweist darauf:
  <br><code>{esc(CONTRACT_URL)}</code></p>
</div>"""


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

    deploy = [
        "## 6. Test-Deployment (Deploy-Hook)",
        "",
        "Getestete Stände rollst du selbst auf die **Test-Instanz** aus —",
        "Produktivsetzung bleibt eine menschliche Entscheidung mit",
        "Versions-Sprung.",
        "",
    ]
    if hook:
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
    else:
        deploy += [
            "Der Hook ist für dieses Vorhaben noch nicht eingerichtet.",
            "Die Plattform-Administration erzeugt ihn mit",
            f"`sudo oaap app token create {instance}` und nennt dir Adresse",
            "und Token.",
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
        """Reject cross-site form posts (CSRF defence, no dependencies).

        The gateway authenticates by session cookie, so a foreign page
        could otherwise submit a form in the user's name. Browsers
        announce the context in Sec-Fetch-Site; older ones at least send
        Origin. Both absent (curl, tests) is treated as same-site.
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

            m = re.fullmatch(r"/vorhaben/([a-z0-9-]{1,40})(/briefing(\.md)?|/loeschen)?", path)
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
        form = self.read_form()
        roles_label = ", ".join(sorted(roles))
        con = db()
        try:
            if path == "/vorhaben":
                return self.create(con, form, user, roles_label)
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

    # -- write operations -------------------------------------------------
    def values(self, form):
        v = {f: (form.get(f) or "").strip() for f in FIELDS}
        if v["app_type"] not in APP_TYPES:
            v["app_type"] = "native"
        if v["status"] not in STATUSES:
            v["status"] = "idee"
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
