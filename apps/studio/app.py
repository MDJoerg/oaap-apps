"""Studio — Entwicklungsvorhaben, Briefings und Pakete.

Zweite Ausbaustufe (siehe program/studio/ideas.md). 0.1 verwaltete
Vorhaben und erzeugte daraus **Briefings**; 0.2 nimmt die andere Seite
dazu — das **fertige Paket**, das aus so einem Briefing entsteht:

- ein Paket (ZIP) prüfen, ohne es irgendwohin zu schicken,
- das Vorhaben aus dem Manifest füllen,
- das Paket über den Drei-Phasen-Weg (RFC-0019 §2) auf die
  **Test**-Instanz ausrollen,
- und den **Deployment-Zettel** erzeugen, mit dem die Projekt-KI
  denselben Weg selbst geht.

0.3 zieht nach, was die Praxis erzwungen hat: Ein Vorhaben lebt nicht
zwangsläufig auf dem Knoten, auf dem das Studio läuft. Am 23.08. hat
Jörg aus dem Studio auf oaap-demo heraus eine Test-Instanz auf oaapx01
angelegt und sie dort produktiv gesetzt — über eine Knotengrenze
hinweg. Das Studio wusste davon nichts: Es leitete Portal-Verweise aus
seinem **eigenen** Hostnamen ab und zeigte damit ins Leere. Deshalb:

- der **Zielknoten** als benannter Begriff — abgeleitet aus dem
  Deploy-Hook (dorthin gehen die Pakete tatsächlich), notfalls aus
  einem eigenen Feld, notfalls der eigene Knoten;
- die **Produktiv-Instanz** neben der Test-Instanz, beide mit Verweis
  auf ihre Seite im Portal **des Zielknotens**;
- eine **Zustandsanzeige beider Instanzen**, gelesen über die
  Flotten-Auskunft des Zielknotens (`oaap.fleet.status`, RFC-0021) —
  optional, mit einem Schlüssel, der ausschließlich lesen kann
  (Begründung in fleet.py).

0.4 macht den Weg auch für Projekte auf, die es schon gibt. Bis dahin
kam man an die Regeln der Plattform nur über ein Vorhaben — wer nur
noch das Deployment brauchte, musste eine Karteileiche anlegen. Jetzt
gibt es beides ohne Vorhaben zum Herunterladen:

- das **Plattform-Briefing** (alles Verbindliche, kein fachlicher
  Auftrag, keine Adressen, nichts Privilegiertes),
- das **Starter-Paket** als ZIP — das kleinste vollständige Paket, das
  die eigene Paketprüfung ohne einen einzigen Befund passiert.

Dabei gilt: **ein Erzeuger, zwei Ausgaben.** Der Plattformteil steht
genau einmal im Quelltext und geht in beide Blätter; zwei getrennt
gepflegte Texte über dieselbe Sache wären der sichere Weg in die Drift
(das Argument von RFC-0014). Neu darin ist der Abschnitt „Benutzer und
fachliche Rechte" — das Muster aus dem App Deployment Contract, das bis
hierhin in keinem Briefing stand, obwohl Apps mit eigenem Rollenmodell
genau daran scheitern.

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

Seit 0.7 (RFC-0118) im Rahmen der Plattform: Kit /platform/theme.css,
Rahmenskript /platform/app.js, eine Reiterleiste, ein Listenbericht der
Vorhaben, die Hilfe aus docs/ (auch unter /hilfe) und eine lesende
MCP-Tür unter /mcp.
"""

import html
import io
import json
import os
import re
import sqlite3
import sys
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import appframe as af
import deployer
import fleet
import helpdocs
import mcpdoor
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

# Das Starter-Paket liegt als lesbares Verzeichnis im Image und wird auf
# Anfrage zu einer ZIP gepackt. Eine vorgefertigte ZIP mitzuliefern wäre
# bequemer und wäre eine zweite Wahrheit: So prüft die eigene Testsuite
# genau die Dateien, die der Anwender bekommt — mit demselben Prüfer,
# durch den auch fremde Pakete gehen.
STARTER_DIR = os.environ.get(
    "STUDIO_STARTER_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "starter"))
STARTER_FILES = ("oaap-app.yaml", "Dockerfile", "app.py", "README.md", "docs/index.md")

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

# Flotten-Schlüssel je Zielknoten (`knoten=schlüssel`, geheim). Damit
# liest das Studio den Zustand der beiden Instanzen eines Vorhabens —
# und ausschließlich das; die Begründung, warum das die Regel „Das
# Studio hält nie ein Recht" nicht bricht, steht in fleet.py. Ohne
# Eintrag läuft alles Übrige unverändert.
FLEET_KEYS = fleet.parse_keys(os.environ.get("STUDIO_FLEET_KEYS", ""))

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
          "hook_url", "test_url", "deploy_way", "notes",
          # 0.3: wo das Vorhaben läuft. `instance` ist und bleibt die
          # TEST-Instanz — der Spaltenname stammt aus 0.1, als es keine
          # zweite gab; umbenennen hieße eine Datenbank wandern lassen,
          # ohne dass ein einziger Anwender etwas davon hätte.
          "node_url", "prod_instance")

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
    # 0.3: Zielknoten (Adresse) und Produktiv-Instanz. Leer bedeutet in
    # beiden Fällen etwas Vernünftiges — dieser Knoten, noch nicht
    # produktiv —, deshalb genügt der leere Default und kein Vorhaben
    # aus 0.2 muss angefasst werden.
    "node_url": "TEXT NOT NULL DEFAULT ''",
    "prod_instance": "TEXT NOT NULL DEFAULT ''",
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


NODE_SOURCES = {
    "hook": "laut Deploy-Hook",
    "feld": "laut Eintrag im Vorhaben",
    "eigener": "angenommen: der Knoten, auf dem dieses Studio läuft",
}


def target_node(p, own_base):
    """Der **Zielknoten** eines Vorhabens: wo seine Instanzen leben.

    Drei Quellen, in dieser Reihenfolge:

    1. der **Deploy-Hook** — er ist keine Absichtserklärung, sondern die
       Adresse, an die Pakete tatsächlich gehen. Was dort steht, gilt.
    2. das **Feld „Zielknoten"** — für die Zeit, bevor es einen Hook
       gibt (der entsteht erst mit dem ersten Token im Portal).
    3. der **eigene Knoten** — die Vermutung, die bis 0.2 stillschweigend
       für alle galt. Sie steht jetzt als Vermutung da, statt sich als
       Tatsache auszugeben.

    Widersprechen sich 1 und 2, gewinnt der Hook — und der Widerspruch
    wird benannt. Eine der beiden Angaben stillschweigend zu verwerfen
    hieße, den Anwender auf eine Seite schauen zu lassen, die einen
    anderen Knoten meint als sein Paket.
    """
    hook_base = fleet.node_base(p["hook_url"])
    field_base = fleet.node_base(p["node_url"])
    conflict = ""
    if hook_base and field_base and \
            fleet.host_of(hook_base) != fleet.host_of(field_base):
        conflict = (
            f"Der Deploy-Hook zeigt auf {fleet.host_of(hook_base)}, im Feld "
            f"„Zielknoten\" steht {fleet.host_of(field_base)}. Es gilt der "
            "Hook — dorthin gehen die Pakete. Bitte das Feld korrigieren.")
    base = hook_base or field_base or own_base
    source = "hook" if hook_base else ("feld" if field_base else "eigener")
    return {
        "base": base,
        "host": fleet.host_of(base),
        "source": source,
        "source_label": NODE_SOURCES[source],
        "conflict": conflict,
        # „Anderer Knoten" ist nur dann eine Aussage, wenn wir den
        # eigenen kennen — sonst wäre jeder Knoten „anders".
        "remote": bool(base and own_base
                       and fleet.host_of(base) != fleet.host_of(own_base)),
    }


def instance_url(node, name):
    """Verweis auf die Instanzseite im Portal des Zielknotens."""
    if not (node["base"] and name):
        return ""
    return f"{node['base']}/instances/{quote(name, safe='')}"


def prod_proposal(p):
    """Vorschlag für den Namen der Produktiv-Instanz.

    RFC-0020 setzt `<app>-test` nach `<app>` produktiv; das ist der Weg,
    den Jörg gegangen ist. Ein Vorschlag, kein Automatismus — den Namen
    vergibt der Mensch, der übernimmt.
    """
    inst = (p["instance"] or "").strip()
    app_id = (p["app_id"] or "").strip() if "app_id" in p.keys() else ""
    if inst.endswith("-test"):
        return inst[:-5]
    return app_id or ""


# ------------------------------------------------------------ presentation

def esc(v):
    return html.escape(str(v or ""), quote=True)


APP_NAME = "Studio"

# Nur, was dem Studio eigen ist -- Farben ausschließlich über das Kit
# (/platform/theme.css). Kopfleiste, Reiter, Karten, Tabellen, Badges und
# Knöpfe kommen aus dem Rahmen (RFC-0118 §4).
STYLE = """
  .oaap-card label{display:block;font-size:.85rem;color:var(--oaap-color-text-muted);margin-top:.9rem}
  .oaap-card input,.oaap-card select,.oaap-card textarea{width:100%;box-sizing:border-box;margin:.25rem 0 .2rem}
  .oaap-card input[type=checkbox],.oaap-card input[type=radio]{width:auto}
  textarea{min-height:5.5rem;resize:vertical}
  .hint{font-size:.8rem;color:var(--oaap-color-text-muted);margin:0 0 .6rem}
  .muted{color:var(--oaap-color-text-muted);font-size:.9rem}
  .err{color:var(--oaap-color-danger)} .ok{color:var(--oaap-color-success)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 1.2rem}
  .actions{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.1rem;align-items:center}
  .actions form{margin:0}
  pre.briefing{background:var(--oaap-color-bg);color:var(--oaap-color-text);
       border:1px solid var(--oaap-color-border);padding:1.1rem;border-radius:var(--oaap-radius);
       overflow-x:auto;font-size:.82rem;line-height:1.5;white-space:pre-wrap;word-break:break-word}
  .oaap-main article{max-width:75ch}
  @media (max-width:640px){.grid2{grid-template-columns:1fr}}
"""

# Die Bereiche des Studios -- eine Reiterleiste (RFC-0118 §6). Absolut,
# nicht relativ: Die Leiste steht auf JEDER Seite, auch auf
# /vorhaben/<id>/briefing (ein relatives ./hilfe landete dort im Nichts).
TABS = [("/", "Übersicht"), ("/briefing", "Onboarding")]
ACTIVE = {"projects": "/", "onboarding": "/briefing"}


def page(title, body, user="", roles="", active="", obj=None, obj_title=None):
    """Der Rahmen der Plattform (appframe): Kopfleiste, Reiter, Fußzeile.
    Wer angemeldet ist, zeigt das Benutzermenü des Rahmens -- nie die
    Rollen (user/roles bleiben nur für die Aufrufer stehen)."""
    return af.page(title, body, app=APP_NAME, version=VERSION, product="studio",
                   tabs=TABS, active=ACTIVE.get(active), obj=obj, obj_title=obj_title,
                   style=STYLE)


def project_tabs(p, active):
    """Die Reiter eines Vorhabens (RFC-0118 §6.5) -- Unterseiten, die es
    schon gab; die Adressen bleiben."""
    base = f"/vorhaben/{p['id']}"
    return af.tabs([(base, "Übersicht"), (f"{base}/paket", "Paket"),
                    (f"{base}/briefing", "Briefing"), (f"{base}/zettel", "Deployment-Zettel")],
                   base + active)


VERSION = "0.9.0"


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
<div class="oaap-card">
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
<div class="oaap-card">
  <h2>Fachlicher Inhalt (wird zum Briefing)</h2>
  {field("Zielanwender", "target_users", p["target_users"], rows=3,
         hint="Wer benutzt die App, in welcher Situation, auf welchem Gerät?")}
  {field("Was soll die App erreichen?", "goal", p["goal"], rows=4,
         hint="Das Problem in eigenen Worten — nicht die Lösung.")}
  {field("Was muss die erste Version können?", "scope", p["scope"], rows=5,
         hint="Eine Liste knapper Sätze. Alles Weitere kommt später.")}
</div>
<div class="oaap-card">
  <h2>Technische Anbindung</h2>
  {field("Weg auf die Test-Instanz", "deploy_way", p["deploy_way"] or "git",
         options=DEPLOY_WAYS,
         hint="Git: der Knoten holt sich den Stand selbst — dafür braucht er "
              "Zugang zum Repository. Paket: die KI liefert eine ZIP ab, die "
              "Plattform hält dann keinen fremden Schlüssel.")}
  {field("Repository (Git-URL)", "repo_url", p["repo_url"],
         hint="Das Projekt-Repo, z. B. auf dem eigenen Forgejo. Default-Branch: main.")}
</div>
<div class="oaap-card">
  <h2>Zielknoten und Instanzen</h2>
  <p class="hint">Der <b>Zielknoten</b> ist die Plattform, auf der die Instanzen
  dieses Vorhabens laufen — nicht zwangsläufig die, auf der dieses Studio
  läuft. Test und Produktiv liegen auf demselben Knoten: Die Übernahme nach
  Produktiv findet dort statt und reicht dieselben Bytes weiter.</p>
  {field("Zielknoten (Adresse)", "node_url", p["node_url"],
         hint="z. B. https://oaap.joomp.de. Leer = dieser Knoten. Sobald ein "
              "Deploy-Hook eingetragen ist, gilt dessen Knoten — das Feld "
              "hilft in der Zeit davor.")}
  <div class="grid2">
    {field("Test-Instanz (Name)", "instance", p["instance"],
           hint="Name der Test-Instanz auf dem Zielknoten, z. B. reklamationen-test.")}
    {field("Produktiv-Instanz (Name)", "prod_instance", p["prod_instance"],
           hint="Entsteht bei der Übernahme im Portal des Zielknotens — "
                "üblich ist derselbe Name ohne „-test“. Leer = noch nicht "
                "produktiv.")}
  </div>
  {field("Test-Adresse", "test_url", p["test_url"],
         hint="Wo die Test-Instanz erreichbar ist.")}
  {field("Deploy-Hook (URL)", "hook_url", p["hook_url"],
         hint="Nur die Adresse — das Token gehört NICHT ins Studio. "
              "Es wird im Portal erzeugt, gehört Dir und wird bei jedem "
              "Upload einzeln eingegeben.")}
</div>
<div class="oaap-card">
  <h2>Notizen</h2>
  {field("Notizen / nächste Schritte", "notes", p["notes"], rows=4)}
</div>
<div class="actions"><button type="submit" class="primary">{esc(submit_label)}</button></div>
"""


def list_page(rows, user, roles, msg="", params=None):
    """Der Listenbericht der Vorhaben (RFC-0118 §7): Suche, Status und
    App-Typ als Filter, sortierbare Spalten, Blättern ab 50."""
    query = af.Query(params or {}, filters=("status", "typ"), default_sort="name")
    rows = [r for r in rows
            if (not query.filters["status"] or r["status"] == query.filters["status"])
            and (not query.filters["typ"] or r["app_type"] == query.filters["typ"])]
    cols = [("Vorhaben", "name", lambda r: r["name"] or "", ""),
            ("Kontext", "kontext", lambda r: r["context"] or "", ""),
            ("Typ", "typ", lambda r: r["app_type"] or "", "oaap-minor"),
            ("Status", "status", lambda r: list(STATUSES).index(r["status"])
             if r["status"] in STATUSES else 99, ""),
            ("Entwickelt mit", "agent", lambda r: r["agent"] or "", "oaap-minor")]

    def row(r):
        return (f'<tr><td><a href="/vorhaben/{esc(r["id"])}">{esc(r["name"])}</a></td>'
                f'<td>{esc(r["context"]) or "<span class=muted>—</span>"}</td>'
                f'<td class="oaap-minor">{esc(r["app_type"])}</td>'
                f'<td>{af.badge(STATUSES.get(r["status"], r["status"]), STATUS_BADGE.get(r["status"], ""))}</td>'
                f'<td class="oaap-minor">{esc(r["agent"]) or "<span class=muted>—</span>"}</td></tr>')
    choices = [("status", "Status", list(STATUSES.items())),
               ("typ", "App-Typ", [(k, k) for k in APP_TYPES])]
    report = af.list_report(query, rows, cols, row, base="/?", noun="Vorhaben",
                            choices=choices, placeholder="Name, Kontext, Entwickler",
                            empty_text="Noch kein Vorhaben erfasst. <b>Vorhaben anlegen</b> legt "
                                       "das erste an — daraus entsteht das Briefing, mit dem die KI startet.")
    body = f"""
{af.pagehead("Entwicklungsvorhaben", '<a class="oaap-btn primary" href="/vorhaben/neu">Vorhaben anlegen</a>')}
{af.msg(msg)}
{report}
<div class="oaap-card">
  <h2>Projekt onboarden — ohne Vorhaben</h2>
  <p class="muted">Ein Projekt läuft schon und es fehlt nur das Deployment?
  Dann braucht es keinen fachlichen Auftrag, sondern nur die Regeln der
  Plattform: <b>Plattform-Briefing</b> und <b>Starter-Paket</b> herunterladen,
  der KI des Projekts geben, fertiges Paket hier hochladen.</p>
  <p><a class="oaap-btn" href="/briefing">Briefing und Starter-Paket</a></p>
</div>"""
    return page("Vorhaben", body, user, roles, "projects")


def _instance_row(kanal, name, node, st, hint="", found_only=False):
    """Eine Zeile der Instanz-Tabelle — Kanal, Name, Zustand, Adresse.

    Sie entsteht auch **ohne** Auskunft vom Knoten: Name und Verweis
    ins Portal weiß das Studio selbst. Was es nicht weiß, steht als
    „—" da und nicht als Vermutung.

    `found_only` heißt: Diesen Namen hat nicht der Anwender eingetragen,
    sondern der Knoten gemeldet. Das wird gesagt, statt den Fund als
    gepflegte Angabe auszugeben.
    """
    if not name:
        return (f'<tr><td>{esc(kanal)}</td><td colspan="4" class="muted">'
                f'{esc(hint)}</td></tr>')
    link = instance_url(node, name)
    label = (f'<a href="{esc(link)}" target="_blank" '
             f'rel="noopener">{esc(name)}</a>' if link else esc(name))
    if found_only:
        label += (' <span class="oaap-badge warn">der Knoten hat sie, das '
                  'Vorhaben nennt sie nicht</span>')
    row = fleet.instance(st.get("doc"), name)
    if row:
        state = row.get("state", "unknown")
        zustand = (f'<span class="oaap-badge {fleet.STATE_BADGE.get(state, "off")}">'
                   f'{esc(fleet.STATE_LABELS.get(state, state))}</span>')
        version = esc(row.get("version") or "—")
        # Eigene Adresse (RFC-0009), sonst der automatische Name aus
        # Schema 0.3 — der ist der Weg zur Instanz selbst, den das Studio
        # bis dahin nicht kannte (es konnte nur aufs Portal verweisen).
        # Sein Urteil sagt etwas anderes als ein DNS-Urteil: ob die
        # Instanz unter diesem Namen auf ihrem Knoten erreichbar ist.
        addr = row.get("address") or ""
        auto = row.get("auto_address") or ""
        if addr:
            adresse = (f'<a href="https://{esc(addr)}" '
                       f'target="_blank" rel="noopener">{esc(addr)}</a>')
        elif auto:
            a_state = row.get("auto_state", "unknown")
            adresse = (f'<a href="https://{esc(auto)}" '
                       f'target="_blank" rel="noopener">{esc(auto)}</a>'
                       f' <span class="oaap-badge {fleet.STATE_BADGE.get(a_state, "off")}">'
                       f'{esc(fleet.AUTO_LABELS.get(a_state, a_state))}</span>')
        else:
            adresse = '<span class="muted">—</span>' 
        kanal_doc = fleet.CHANNEL_LABELS.get(row.get("channel"), row.get("channel"))
        # Sagt der Knoten einen anderen Kanal, als hier erwartet wird,
        # ist das eine Nachricht — z. B. eine Test-Instanz, die in
        # Wahrheit produktiv läuft.
        if kanal_doc and kanal_doc != kanal:
            kanal = f'{esc(kanal)} <span class="oaap-badge warn">Knoten sagt: {esc(kanal_doc)}</span>'
        else:
            kanal = esc(kanal)
    elif st.get("doc") is not None:
        zustand = ('<span class="oaap-badge off">auf dem Knoten nicht vorhanden</span>')
        version, adresse, kanal = "—", '<span class="muted">—</span>', esc(kanal)
    else:
        zustand = '<span class="muted">—</span>'
        version, adresse, kanal = "—", '<span class="muted">—</span>', esc(kanal)
    return (f"<tr><td>{kanal}</td><td>{label}</td><td>{version}</td>"
            f"<td>{zustand}</td><td>{adresse}</td></tr>")


def _names_note(st, names):
    """Veröffentlichte Namen der Instanzen mit dem DNS-Urteil des Knotens.

    Hier steht die Adresse, die in ausgelieferten Clients steckt
    (RFC-0009). Dass sie stumm verschwinden kann, hat die Flotte am
    23.08. teuer gelernt — im Vorhaben ist sie deshalb sichtbar.
    """
    rows = []
    for name in names:
        for n in fleet.names_for(st.get("doc"), name):
            state = n.get("state", "unknown")
            rows.append(
                f'<li><code>{esc(n.get("name"))}</code> '
                f'<span class="oaap-badge {fleet.STATE_BADGE.get(state, "off")}">'
                f'{esc(fleet.STATE_LABELS.get(state, state))}</span> '
                f'<span class="muted">({esc(n.get("kind"))}, {esc(name)})</span></li>')
    if not rows:
        return ""
    return (f'<p class="muted" style="margin:1rem 0 .3rem">Veröffentlichte '
            f'Namen laut Knoten (DNS-Urteil):</p>'
            f'<ul style="list-style:none;padding:0;margin:0;line-height:1.9">'
            f'{"".join(rows)}</ul>')


def node_card(p, node, st):
    """Zielknoten, beide Instanzen und ihr Zustand — die Objektseite.

    Der Zustand ist ein Blick, kein Wächter: Das Studio fragt beim
    Aufschlagen der Seite nach und hebt nichts auf. Wer die Landschaft
    über die Zeit beobachten will, nimmt FleetView (RFC-0021 §3).
    """
    st = st or {}
    test_name = (p["instance"] or "").strip()
    prod_name = (p["prod_instance"] or "").strip()
    proposal = prod_proposal(p)

    if node["base"]:
        node_dd = (f'<code>{esc(node["host"])}</code> '
                   + ('<span class="oaap-badge test">anderer Knoten</span> '
                      if node["remote"] else "")
                   + f'<span class="muted">— {esc(node["source_label"])}</span>')
        portal_dd = (f'<a href="{esc(node["base"])}/instances" target="_blank" '
                     f'rel="noopener">{esc(node["base"])}/instances</a>')
    else:
        node_dd = ('<span class="muted">nicht bekannt — Adresse eintragen '
                   'oder Deploy-Hook hinterlegen</span>')
        portal_dd = '<span class="muted">—</span>'

    conflict = (f'<p class="err">{esc(node["conflict"])}</p>'
                if node["conflict"] else "")

    # Ist im Vorhaben keine Produktiv-Instanz benannt, heißt das NICHT,
    # dass es keine gibt — auf oaap-demo stand am 24.08. „noch nicht
    # produktiv" neben einem Knoten, der `bdt-app` sehr wohl als
    # produktiv meldete. Also erst den Knoten fragen und den Fund als
    # Fund kennzeichnen; „noch nicht produktiv" bleibt dem Fall
    # vorbehalten, in dem der Knoten wirklich nichts hat.
    found_prod = ""
    if not prod_name and proposal:
        row = fleet.instance(st.get("doc"), proposal)
        if row and row.get("channel") == "production":
            found_prod = proposal

    prod_hint = ("Noch nicht produktiv. Die Übernahme geschieht im Portal des "
                 "Zielknotens auf der Seite der Test-Instanz"
                 + (f" — üblicher Name wäre „{proposal}“." if proposal else "."))
    table = f"""<table class="oaap-table" style="margin-top:1rem">
  <tr><th>Kanal</th><th>Instanz</th><th>Version</th><th>Zustand</th><th>Adresse</th></tr>
  {_instance_row("Test", test_name, node, st,
                 "Noch keine Test-Instanz benannt. Sie entsteht mit dem ersten "
                 "Paket (Anlege-Erlaubnis) oder im Portal des Zielknotens.")}
  {_instance_row("Produktiv", prod_name or found_prod, node, st, prod_hint,
                 found_only=bool(found_prod))}
</table>"""
    if found_prod:
        table += (f'<p class="muted" style="margin:.6rem 0 0">Der Zielknoten '
                  f'führt <code>{esc(found_prod)}</code> im Kanal produktiv, '
                  f'im Vorhaben steht kein Name. Trag ihn unten ein, dann '
                  f'gehört die Zeile dem Vorhaben und nicht einem Fund.</p>')

    # Auffälligkeiten des Knotens, die genau diese Instanzen betreffen.
    att = fleet.attention_for(st.get("doc"), [test_name, prod_name or found_prod])
    att_html = ""
    if att:
        items = "".join(
            f'<li><span class="oaap-badge warn">{esc(a["label"])}</span> '
            f'{esc(a["instance"])} <span class="muted">{esc(a["detail"])}</span></li>'
            for a in att)
        att_html = (f'<p style="margin:1rem 0 .3rem"><b>Der Knoten meldet '
                    f'Handlungsbedarf:</b></p><ul style="list-style:none;'
                    f'padding:0;margin:0;line-height:1.9">{items}</ul>')

    if not st.get("configured"):
        label = esc(node["host"] or "diesen Knoten")
        foot = f"""<p class="muted" style="margin-top:1rem">Der Zustand der
    Instanzen ist nicht eingerichtet: Für <code>{label}</code> ist kein
    Flotten-Schlüssel hinterlegt. Ausgestellt wird er <b>auf dem
    Zielknoten</b> (an der Maschine, denn er ist ein Schlüssel), eingetragen
    wird er in der Konfiguration <b>dieser</b> Studio-Instanz:</p>
    <pre class="briefing"># auf {label} (dem Zielknoten), an der Maschine:
sudo oaap fleet key issue studio@&lt;dein-studio-knoten&gt;

# auf dem Knoten, auf dem dieses Studio läuft:
sudo oaap app config set &lt;studio-instanz&gt; STUDIO_FLEET_KEYS \\
  --append '{label}=&lt;schlüssel&gt;'</pre>
    <p class="muted">Der Schlüssel kann <b>ausschließlich</b> diese eine
    Auskunft lesen — keine Sitzung, keine Rollen, kein Schreibweg.
    Ohne ihn funktioniert alles Übrige
    unverändert; es fehlt nur die Ampel.</p>"""
    elif st.get("error"):
        foot = f"""<p class="muted" style="margin-top:1rem">
    <span class="oaap-badge err">keine Auskunft</span> {esc(st['error'])} —
    <a href="?frisch=1">noch einmal versuchen</a>. Das Studio hebt keinen
    letzten bekannten Stand auf; dafür ist FleetView da.</p>"""
    else:
        doc = st.get("doc") or {}
        age = st.get("age")
        alter = "gerade eben" if not age else f"vor {age} s"
        foot = f"""<p class="muted" style="margin-top:1rem">Knoten meldet sich
    als <code>{esc(doc.get('node') or '—')}</code>, Plattform
    <b>{esc(doc.get('platform_version') or '—')}</b> — abgefragt {esc(alter)}
    · <a href="?frisch=1">neu abfragen</a></p>"""

    return f"""
<div class="oaap-card">
  <h2>Zielknoten und Instanzen</h2>
  {conflict}
  <dl class="oaap-kv">
    <dt>Zielknoten</dt><dd>{node_dd}</dd>
    <dt>Instanzen im Portal</dt><dd>{portal_dd}</dd>
  </dl>
  {table}
  {att_html}
  {_names_note(st, [test_name, prod_name or found_prod])}
  {foot}
</div>"""


def package_card(p):
    """Was zuletzt geprüft wurde — auf der Objektseite, kurz gehalten."""
    if not p["pkg_sha256"]:
        text = ("<p class=\"muted\">Für dieses Vorhaben wurde noch kein Paket "
                "geprüft. Das Studio liest das Manifest aus der ZIP, hält es "
                "gegen die Plattformregeln und rollt es auf Wunsch über den "
                "Deploy-Hook aus.</p>")
    else:
        text = f"""<dl class="oaap-kv">
    <dt>Zuletzt geprüft</dt><dd>{esc(p['pkg_file'])} — Version
      <b>{esc(p['pkg_version'])}</b>, {esc(mb(p['pkg_bytes']))}</dd>
    <dt>Prüfsumme</dt><dd><code>{esc(p['pkg_sha256'][:16])}…</code></dd>
    <dt>Am</dt><dd>{esc(p['pkg_at'])} von {esc(p['pkg_by'])}</dd>
  </dl>"""
    return f"""
<div class="oaap-card">
  <h2>Paket und Deployment</h2>
  {text}
  <div class="actions">
    <a class="oaap-btn primary" href="./{esc(p['id'])}/paket">Paket prüfen und ausrollen …</a>
    <a class="oaap-btn" href="./{esc(p['id'])}/zettel">Deployment-Zettel …</a>
  </div>
</div>"""


def object_page(p, user, roles, msg="", error="", is_admin=False,
                node=None, st=None):
    danger = ""
    if is_admin:
        danger = f"""
<div class="oaap-card oaap-danger">
  <h2>Vorhaben löschen</h2>
  <p class="muted">Entfernt das Vorhaben mitsamt Briefing-Daten. Das Repository
  und installierte Instanzen bleiben unberührt.</p>
  <a class="oaap-btn" href="./{esc(p['id'])}/loeschen">Löschen …</a>
</div>"""
    body = f"""
{af.back("/", "Zurück zur Liste")}
<div class="oaap-objhead">
<div class="oaap-pagehead">
  <h1>{esc(p['name'])}
    {af.badge(STATUSES.get(p['status'], p['status']), STATUS_BADGE.get(p['status'], ''))}
  </h1>
  <a class="oaap-btn primary" href="/vorhaben/{esc(p['id'])}/briefing">Briefing erzeugen</a>
</div></div>
{project_tabs(p, "")}
{af.msg(msg)}{af.msg(error, error=True)}
<div class="oaap-card">
  <dl class="oaap-kv">
    <dt>Kennung</dt><dd><code>{esc(p['id'])}</code></dd>
    <dt>Angelegt</dt><dd>{esc(p['created_at'])} von {esc(p['created_by'])}</dd>
    <dt>Zuletzt geändert</dt><dd>{esc(p['updated_at'])} von {esc(p['updated_by'])}</dd>
  </dl>
</div>
{node_card(p, node, st) if node is not None else ""}
{package_card(p)}
<form method="post" action="./{esc(p['id'])}">
{project_form(p, "Speichern")}
</form>
{danger}"""
    return page(p["name"], body, user, roles, "projects",
                obj=f"Vorhaben:{p['id']}", obj_title=p["name"])


def new_page(p, user, roles, error=""):
    body = f"""
{af.back("/", "Zurück zur Liste")}
{af.pagehead("Neues Vorhaben")}
{af.msg(error, error=True)}
<p class="muted">Alles, was hier steht, landet später im Briefing für die KI.
Kurze, klare Sätze sind besser als Fachjargon.</p>
<form method="post" action="../vorhaben">
{project_form(p, "Vorhaben anlegen")}
</form>"""
    return page("Neues Vorhaben", body, user, roles, "projects")


def delete_page(p, user, roles):
    body = f"""
{af.back(f"/vorhaben/{p['id']}", "Zurück zum Vorhaben")}
{af.pagehead("Vorhaben löschen")}
<div class="oaap-card oaap-danger">
  <p>Soll das Vorhaben <b>{esc(p['name'])}</b> wirklich gelöscht werden?
  Erfasste Angaben und Briefing-Inhalte gehen dabei verloren.</p>
  <p class="muted">Repository, Deploy-Token und installierte App-Instanzen
  sind davon nicht betroffen.</p>
  <form method="post" action="../{esc(p['id'])}/loeschen">
    <div class="actions">
      <button class="danger" type="submit">Endgültig löschen</button>
      <a class="oaap-btn" href="../{esc(p['id'])}">Abbrechen</a>
    </div>
  </form>
</div>"""
    return page("Löschen", body, user, roles, "projects")


def briefing_page(p, text, user, roles):
    body = f"""
{af.back("/", "Zurück zur Liste")}
<div class="oaap-pagehead">
  <h1>{esc(p['name'])}</h1>
  <a class="oaap-btn primary" href="/vorhaben/{esc(p['id'])}/briefing.md" download>Briefing als Datei herunterladen</a>
</div>
{project_tabs(p, "/briefing")}
<div class="oaap-card">
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
    return page("Briefing", body, user, roles, "projects", obj=f"Vorhaben:{p['id']}", obj_title=p["name"])


def onboarding_page(text, user, roles):
    """Das Plattform-Briefing zum Ansehen und Herunterladen — ohne Vorhaben.

    Für den Fall, den es bis 0.3 nicht gab: ein Projekt läuft schon, und
    es fehlt nur das Deployment. Wer dafür erst ein Vorhaben anlegen
    muss, legt eine Karteileiche an.
    """
    body = f"""
<div class="oaap-pagehead">
  <h1>Projekt onboarden</h1>
  <a class="oaap-btn primary" href="./briefing.md" download>Plattform-Briefing (MD)</a>
</div>
<div class="oaap-card">
  <h2>Für Projekte, die es schon gibt</h2>
  <p>Diese beiden Dateien sind alles, was eine fremde Entwicklerin oder eine
  fremde KI braucht, um aus einer bestehenden Anwendung ein Paket zu machen,
  das hier ankommt. Ein Vorhaben ist dafür nicht nötig — es geht um keinen
  fachlichen Auftrag, nur um die Regeln der Plattform.</p>
  <ol style="line-height:1.8;padding-left:1.2rem">
    <li><b>Plattform-Briefing</b> herunterladen und der KI als Kontext geben
        („Mach diese Anwendung OAAP-fähig — als Paket, das durch die Prüfung
        geht.“).</li>
    <li><b>Starter-Paket</b> daneben legen: das kleinste vollständige Paket,
        mit kommentiertem Manifest. Schneller gelesen als beschrieben.</li>
    <li>Das fertige Paket kommt über <b>Paket prüfen → Ausrollen</b> auf eine
        Test-Instanz — oder die KI geht denselben Weg selbst über den
        Deploy-Hook.</li>
  </ol>
  <p><a class="oaap-btn primary" href="./starter.zip" download>Starter-Paket (ZIP)</a></p>
  <p class="muted">Beide Dateien enthalten <b>nichts Privilegiertes</b>:
  keinen Token, keine Anlege-Erlaubnis, keine Adresse einer Instanz. Sie
  dürfen weitergegeben werden; einen Zugang gibt das nicht.</p>
</div>
<div class="oaap-card">
  <h2>Wenn daraus doch ein Vorhaben wird</h2>
  <p class="muted">Sobald es einen fachlichen Auftrag gibt, leg ein Vorhaben
  an: Das Briefing dort enthält <b>denselben</b> Plattformteil wie dieses
  Blatt und zusätzlich Auftrag, Zielknoten und Instanzen. Es wird an einer
  Stelle gepflegt — die beiden Blätter können nicht auseinanderlaufen.</p>
</div>
<pre class="briefing">{esc(text)}</pre>"""
    return page("Projekt onboarden", body, user, roles, "onboarding")


LEVEL_LABEL = {pkg.FEHLER: "Fehler", pkg.BEFUND: "Befund", pkg.HINWEIS: "Hinweis"}
LEVEL_BADGE = {pkg.FEHLER: "err", pkg.BEFUND: "warn", pkg.HINWEIS: ""}


def findings_html(findings):
    if not findings:
        return ('<p class="ok">Keine Beanstandung — das Manifest hält, was '
                'die Plattform verlangt.</p>')
    items = []
    for f in findings:
        hint = (f'<br><span class="muted">{esc(f["hint"])}</span>'
                if f.get("hint") else "")
        items.append(
            f'<li style="margin:.5rem 0"><span class="oaap-badge '
            f'{LEVEL_BADGE.get(f["level"], "off")}">'
            f'{esc(LEVEL_LABEL.get(f["level"], f["level"]))}</span> '
            f'{esc(f["text"])}{hint}</li>')
    return f'<ul style="list-style:none;padding:0;margin:0">{"".join(items)}</ul>'


def manifest_html(s):
    """Das Manifest als Tatsachen — was ein Betreiber daran wissen muss."""
    routes = "".join(
        f"<tr><td><code>{esc(r['path'])}</code></td>"
        f"<td>{'<span class=\"oaap-badge test\">ohne Anmeldung</span> ' if 'public' in r['roles'] else ''}"
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
    return f"""<dl class="oaap-kv">
  <dt>App</dt><dd><b>{esc(s.get('name'))}</b> — <code>{esc(s.get('id'))}</code>,
    Version <b>{esc(s.get('version'))}</b>, Typ {esc(s.get('type'))},
    Art {esc(s.get('class'))}</dd>
  <dt>Manifest-Format</dt><dd>{esc(s.get('manifest_version'))}</dd>
  <dt>Dienste</dt><dd>{esc(services)}</dd>
  <dt>Speicher</dt><dd>{esc(storage)}</dd>
  <dt>Konfiguration</dt><dd>{esc(config)}</dd>
  <dt>Endpunkte</dt><dd>{esc(eps)}</dd>
  <dt>Plattform</dt><dd>{esc(", ".join(s.get("capabilities") or []) or "—")}</dd>
  <dt>Gesundheit</dt><dd><code>{esc(s.get('health', {}).get('path'))}</code></dd>
</dl>
<table class="oaap-table" style="margin-top:1rem"><tr><th>Route</th><th>Rollen</th></tr>{routes}</table>"""


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
        envelope = f"""<div class="oaap-card">
  <h2>Rahmen der Instanz</h2>
  {''.join(blocks)}
  <p class="muted">Verglichen wird mit dem <b>zuletzt von hier ausgerollten</b>
  Paket. Was tatsächlich installiert ist, weiß nur der Knoten — er
  entscheidet, und seine Antwort steht im Deployment-Protokoll.</p>
</div>"""
    elif rep.get("compared"):
        envelope = """<div class="oaap-card">
  <h2>Rahmen der Instanz</h2>
  <p class="ok">Nichts, was den Rahmen erweitert — verglichen mit dem zuletzt
  von hier ausgerollten Paket. Das ist der Normalfall und läuft ohne
  Rückfrage durch.</p>
</div>"""
    else:
        envelope = """<div class="oaap-card">
  <h2>Rahmen der Instanz</h2>
  <p class="muted">Noch kein Vergleich möglich: Aus diesem Vorhaben wurde
  hier noch nichts ausgerollt. Was den Rahmen erweitert, entscheidet dann
  der Knoten gegen den <b>installierten</b> Stand — er allein kennt ihn.</p>
</div>"""

    badge = ('<span class="oaap-badge ok">bereit</span>' if rep.get("deployable")
             else '<span class="oaap-badge err">nicht bereit</span>')
    return f"""
<div class="oaap-pagehead" style="margin-top:2rem">
  <h1 style="font-size:1.1rem">Letzte Prüfung {badge}</h1>
  <span class="muted">{esc(rep.get('at'))} von {esc(rep.get('by'))}</span>
</div>
<div class="oaap-card">
  <h2>Paket</h2>
  <dl class="oaap-kv">
    <dt>Datei</dt><dd>{esc(rep.get('file'))} ({esc(mb(rep.get('bytes')))})</dd>
    <dt>Prüfsumme (SHA-256)</dt><dd><code>{esc(rep.get('sha256'))}</code></dd>
    <dt>Inhalt</dt><dd>{esc(rep.get('entries'))} Dateien, entpackt
      {esc(mb(rep.get('uncompressed')))}</dd>
    <dt>Paketwurzel</dt><dd><code>{esc(rep.get('root') or '(Archivwurzel)')}</code></dd>
  </dl>
</div>
<div class="oaap-card">
  <h2>Manifest</h2>
  {manifest_html(s)}
</div>
<div class="oaap-card">
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
    1: '<span class="oaap-badge ok">angenommen</span>',
    0: '<span class="oaap-badge err">abgelehnt</span>',
    # Ohne Antwort weiß das Studio es nicht — und behauptet es nicht.
    UNKLAR: '<span class="oaap-badge warn">Ausgang unklar</span>',
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
    return f"""<div class="oaap-card" style="overflow-x:auto">
  <h2>Vom Studio ausgerollt</h2>
  <table class="oaap-table"><tr><th>Wann</th><th>Version</th><th>Prüfsumme</th><th>Ergebnis</th>
    <th>Antwort des Knotens</th><th>Wer</th></tr>{items}</table>
  <p class="muted" style="margin-top:.8rem">Dieses Verzeichnis ist die Sicht
  des Studios. Das verbindliche Protokoll führt der Knoten — im Portal unter
  der Instanz.</p>
</div>"""


def package_page(p, rep, deploys, user, roles, msg="", error="", node=None):
    hook = (p["hook_url"] or "").strip()
    inst = (p["instance"] or "").strip()
    node = node or {"base": "", "host": "", "remote": False,
                    "source_label": "", "conflict": ""}
    if hook:
        try:
            urls = deployer.hook_urls(hook)
            hook_html = f"""<dl class="oaap-kv">
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
    # Der Verweis geht auf das Portal des **Zielknotens** — bis 0.2 wurde
    # er aus dem eigenen Hostnamen abgeleitet und zeigte bei einer
    # Instanz auf einem anderen Knoten ins Leere.
    target = instance_url(node, inst)
    portal_link = (f'<p class="muted">Instanz im Portal des Zielknotens: '
                   f'<a href="{esc(target)}" target="_blank" rel="noopener">'
                   f'{esc(target)}</a>'
                   + (' <span class="oaap-badge test">anderer Knoten</span>'
                      if node["remote"] else "")
                   + '</p>') if target else ""
    node_line = (f'<p class="hint">Zielknoten: <code>{esc(node["host"])}</code>'
                 f' — {esc(node["source_label"])}.</p>'
                 if node["host"] else "")
    conflict = (f'<p class="err">{esc(node["conflict"])}</p>'
                if node["conflict"] else "")
    body = f"""
{af.back("/", "Zurück zur Liste")}
{af.pagehead(p['name'])}
{project_tabs(p, "/paket")}
{af.msg(msg)}{af.msg(error, error=True)}
<form method="post" action="./paket" enctype="multipart/form-data">
<div class="oaap-card">
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
<div class="oaap-card">
  <h2>Auf die Test-Instanz ausrollen</h2>
  {conflict}
  {node_line}
  {hook_html}
  {portal_link}
  <p class="hint">Das Studio läuft damit genau den Weg, den auch die KI geht:
  anmelden, Freigabe abholen, hochladen. Es bekommt dafür
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
  Du auf der Instanzseite ein normales Deploy-Token.</p>
  <div class="actions">
    <button type="submit" name="action" value="deployen">Prüfen und ausrollen</button>
  </div>
</div>
</form>
{report_card(rep)}
{deployments_card(deploys)}"""
    return page("Paket", body, user, roles, "projects", obj=f"Vorhaben:{p['id']}", obj_title=p["name"])


def deploy_result_page(p, result, user, roles):
    """Was der Knoten geantwortet hat — Phase für Phase."""
    rows = []
    labels = {deployer.ANNOUNCE: "1 · Anmelden", deployer.UPLOAD: "2 · Hochladen",
              deployer.STATUS: "3 · Nachsehen"}
    for st in result.get("steps", []):
        details = "".join(f"<li>{esc(d)}</li>" for d in (st.get("details") or []))
        rows.append(f"""<div class="oaap-card">
  <h2>{esc(labels.get(st['phase'], st['phase']))}
    <span class="oaap-badge {'ok' if st['ok'] else 'err'}">HTTP {esc(st['status'])}</span></h2>
  <p>{esc(st.get('message') or '—')}</p>
  {f'<ul>{details}</ul>' if details else ''}
  {f'<p class="muted">{esc(st["hint"])}</p>' if st.get('hint') else ''}
</div>""")
    res = result.get("result") or {}
    final = ""
    if result.get("ok"):
        url = res.get("url") or (p["test_url"] or "")
        final = f"""<div class="oaap-card">
  <h2 class="ok">Ausgerollt</h2>
  <dl class="oaap-kv">
    <dt>Version</dt><dd>{esc(res.get('version') or '—')}</dd>
    <dt>Stand</dt><dd><code>{esc(res.get('revision') or '—')}</code></dd>
    <dt>Erreichbar unter</dt><dd>{f'<a href="{esc(url)}">{esc(url)}</a>' if url else '—'}</dd>
  </dl>
</div>"""
    elif result.get("pending"):
        final = """<div class="oaap-card">
  <h2>Läuft noch</h2>
  <p>Der Knoten baut das Paket. Das dauert bei einem Bau auf dem Gerät
  Minuten — auf einem Raspi auch länger. Der Stand steht im Portal auf der
  Instanzseite; die Antwort des Hooks lautet dann „fertig".</p>
</div>"""
    body = f"""
{af.back(f"/vorhaben/{p['id']}/paket", "Zurück zum Paket")}
{af.pagehead("Deployment: " + p['name'])}
{''.join(rows)}
{final}
<p class="muted">Der eingegebene Token ist mit dieser Anfrage vergangen —
das Studio hat ihn nirgends abgelegt. Für den nächsten Upload wird er
wieder gebraucht.</p>"""
    return page("Deployment", body, user, roles, "projects")


def sheet_page(p, text, user, roles, with_token=False):
    warn = ("""<div class="oaap-card oaap-danger">
  <h2>Dieses Blatt enthält den Token</h2>
  <p>Er steht hier <b>einmalig</b> — das Studio hat ihn nicht gespeichert.
  Kopiere das Blatt jetzt zur KI (oder in den Passwortmanager) und lade die
  Seite nicht neu; danach ist er hier weg. Der Download-Knopf oben liefert
  dasselbe Blatt bewusst <b>ohne</b> Token: eine Datei mit einem Schlüssel
  darin wandert sonst durch Verzeichnisse, in denen sie nichts zu suchen
  hat.</p>
</div>""" if with_token else "")
    token_form = ("" if with_token else """
<div class="oaap-card">
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
    <div class="actions"><button type="submit" class="primary">Blatt mit Token anzeigen</button></div>
  </form>
</div>""")
    body = f"""
{af.back("/", "Zurück zur Liste")}
<div class="oaap-pagehead">
  <h1>{esc(p['name'])}</h1>
  <a class="oaap-btn primary" href="/vorhaben/{esc(p['id'])}/zettel.md" download>Zettel als Datei herunterladen</a>
</div>
{project_tabs(p, "/zettel")}
{warn}
<div class="oaap-card">
  <h2>Wofür das Blatt ist</h2>
  <p class="muted">Es beantwortet der Projekt-KI genau eine Frage: „Wie
  bekomme ich meinen getesteten Stand auf die Test-Instanz?" — Adressen,
  Ablauf, Regeln, und was passiert, wenn etwas abgelehnt wird. Gehört ins
  Projektverzeichnis neben das Briefing.</p>
</div>
<pre class="briefing">{esc(text)}</pre>
{token_form}"""
    return page("Deployment-Zettel", body, user, roles, "projects", obj=f"Vorhaben:{p['id']}", obj_title=p["name"])


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
    # Der Zielknoten aus der Sicht dieses Blattes: Es wird auf einem
    # fremden Rechner gelesen, deshalb hilft „der eigene Knoten" hier
    # nicht — nur eine echte Adresse. Gibt es keine, steht das da.
    node = fleet.node_base(p["hook_url"]) or fleet.node_base(p["node_url"])
    prod = (p["prod_instance"] or "").strip()

    lines = [
        f"# Deployment-Zettel: {p['name']}",
        "",
        f"Erzeugt vom Studio am {now()} — Vorhaben `{p['id']}`,",
        f"Test-Instanz `{instance}`"
        + (f" auf `{fleet.host_of(node)}`." if node else "."),
        "",
        "Dieses Blatt beantwortet eine Frage: **Wie kommt ein getesteter",
        "Stand auf die Test-Instanz?** Es gehört neben das Briefing ins",
        "Projektverzeichnis.",
        "",
        "## Zielknoten",
        "",
    ]
    if node:
        lines += [
            f"Alle Adressen unten liegen auf **{fleet.host_of(node)}** — das",
            "ist der Knoten dieses Vorhabens. Dort liegt die Test-Instanz,",
            "und dort entsteht später auch die Produktiv-Instanz"
            + (f" (`{prod}`)." if prod else "."),
            "",
            f"- **Portal des Knotens:** {node}",
            f"- **Seite der Test-Instanz:** {node}/instances/{instance}",
        ]
        if prod:
            lines += [f"- **Seite der Produktiv-Instanz:** {node}/instances/{prod}"]
        lines += [
            "",
            "Das Studio, das dieses Blatt erzeugt hat, kann auf einem",
            "**anderen** Knoten laufen — für dich ändert das nichts: Du",
            "sprichst mit den Adressen hier und mit keinen anderen.",
            "",
        ]
    else:
        lines += [
            "_Noch nicht bekannt_ — er ergibt sich aus dem Deploy-Hook,",
            "sobald der eingerichtet ist.",
            "",
        ]
    lines += [
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
        "  Deployment die Version hochzählen. **Eine Ausnahme:** nach einer",
        "  Bestätigung (siehe unten) meldest du dasselbe Paket unverändert",
        "  erneut an — dort wäre ein Hochzählen genau falsch.",
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
        "### Wenn eine Bestätigung nötig war: NICHT hochzählen",
        "",
        "Die Bestätigung gilt für **genau das Manifest**, das abgelehnt",
        "wurde — nicht für „das nächste Deployment\". Der Ablauf ist deshalb:",
        "",
        "1. Du meldest an, die Antwort sagt „braucht eine Bestätigung\".",
        "2. Ein Mensch bestätigt im Portal.",
        "3. Du meldest **dasselbe Paket** an, Byte für Byte unverändert —",
        "   gleiche Version, gleiches Manifest — und lädst hoch.",
        "",
        "Zählst du in Schritt 3 die Version hoch, ist es ein anderes",
        "Manifest, die Bestätigung deckt es nicht, und du landest wieder",
        "bei Schritt 1. Genau so ist am 24.08.2026 eine Endlosschleife",
        "entstanden. Und keine Sorge wegen der Regel „unveränderte Version",
        "wird abgelehnt\": Die vergleicht mit dem, was **installiert** ist —",
        "das abgelehnte Paket ist nicht installiert, seine Version ist also",
        "frei.",
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
        "  eine menschliche Handlung mit Versions-Sprung — im Portal des",
        "  Zielknotens, auf der Seite der Test-Instanz (RFC-0020). Sie",
        "  reicht dieselben Bytes weiter, die du getestet hast; du baust",
        "  dafür nichts neu und lädst nichts erneut hoch.",
        "",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- briefing
#
# Ein Erzeuger, zwei Ausgaben. Der Plattformteil eines Briefings gilt
# für jede App gleich — er steht deshalb genau einmal hier und geht in
# beide Blätter:
#
#   * das **Vorhaben-Briefing** (mit fachlichem Auftrag und Adressen),
#   * das **Plattform-Briefing** (ohne beides, für Projekte, die schon
#     laufen und nur noch das Deployment brauchen).
#
# Zwei getrennt gepflegte Texte über dieselbe Sache wären der sichere
# Weg in die Drift: Einer wird geändert, der andere nicht, und niemand
# merkt es (das Argument von RFC-0014).


def _numbered(sections):
    """Abschnitte (Titel, Zeilen) fortlaufend nummeriert aneinanderhängen."""
    out = []
    for i, (title, lines) in enumerate(sections, 1):
        out += [f"## {i}. {title}", ""] + lines + [""]
    return out


def _sec_platform(app_type=None):
    lines = [
        "Die App läuft auf **OAAP** — Container hinter einem zentralen",
        "Gateway, das **die gesamte Anmeldung erledigt**. Verbindlich ist der",
        "App Deployment Contract:",
        "",
        f"  {CONTRACT_URL}",
        "",
        "**Lies ihn und arbeite danach.** Das Wichtigste in Kürze:",
        "",
        "- **Kein eigener Login.** Die geprüfte Identität kommt als Header,",
        "  nicht fälschbar: `X-OAAP-User-Id` (die Person, eine UUID, die sich",
        "  nie ändert — **daran hängst Du Deine Daten**), `X-OAAP-User` (der",
        "  Anmeldename — anzeigen, nicht darauf bauen, er ist änderbar),",
        "  `X-OAAP-Roles`, `X-OAAP-Display-Name` und `X-OAAP-Email` (nur",
        "  gesetzt, wenn verifiziert). Niemals ein Anmeldeformular bauen.",
        "  **Keine anderen `X-OAAP-*`-Header als Identität lesen** — es gibt",
        "  kein `X-OAAP-Groups` und kein `X-OAAP-Tenant`; was unter solchen",
        "  Namen ankäme, hätte der Client geschickt. Dazu kommen für",
        "  Sonderfälle `X-OAAP-User-Assertion` und `X-OAAP-User-Idp`; die",
        "  vollständige Liste steht in `OAAP_IDENTITY_HEADERS`.",
        "- **Ein HTTP-Port**, kein TLS in der App (das macht das Gateway).",
        "- **Persistenz nur in deklarierten Mounts** (`storage` im Manifest).",
        "- **Konfiguration nur über deklarierte Umgebungsvariablen**,",
        "  Geheimnisse mit `secret: true`.",
        "- **Logs nach stdout**, dazu ein Health-Endpunkt.",
        "- **Mehrfach-instanzfähig und offline-fähig** — keine festen",
        "  Hostnamen, keine absoluten URLs, kein Internetzwang zur Laufzeit.",
        "  Links immer aus `Host` + `X-Forwarded-Proto` der Anfrage bauen.",
        "- **Mandanten:** Jede Instanz gehört genau einem Mandanten; die App",
        "  sieht davon nichts und filtert nicht selbst — die Grenze setzt",
        "  das Gateway durch. Ein eigenes Mandantenmodell der App bleibt.",
        "- **Netz:** nach innen abgeschottet (andere Apps unerreichbar).",
        "  Nach außen ist es offen — **außer** der Mandant ist *geschlossen*",
        "  (RFC-0068): Dann kommt die App nur über eine **Destination**",
        "  hinaus, die ein Mensch bindet (Abschnitt „Was die Plattform Dir",
        "  bietet“). Plane jedes externe Ziel deshalb als Destination oder",
        "  deklarierte Variable, und arbeite bei Ausfall weiter, statt",
        "  abzustürzen.",
        "- **Offene WebSockets** überstehen das Ausrollen anderer Apps,",
        "  nicht das eigene und nicht das nächtliche Backup: Clients",
        "  verbinden selbst neu (mit etwas Zufall im Wartetakt) und senden",
        "  alle 20–30 s einen Ping.",
        "",
    ]
    if app_type:
        lines += [
            f"**App-Typ dieses Vorhabens:** `{app_type}` — "
            f"{APP_TYPES.get(app_type, '')}",
            "",
        ]
    lines += [
        "Liefere im Paket ein gültiges `oaap-app.yaml` (Manifest) und,",
        "bei App-Typ `native`, ein `Dockerfile` je Service. Das Manifest muss",
        "gegen das veröffentlichte JSON-Schema validieren; das Image muss auf",
        "amd64 **und** arm64 bauen. Gebaut wird auf dem Zielknoten, **mit**",
        "Internet (öffentliche Paket-Registries erreichbar), aber **ohne**",
        "Build-Geheimnisse. Grenzen: Paket höchstens 256 MB (ohne",
        "`node_modules`, Build-Ausgaben, virtuelle Umgebungen), ein",
        "Deployment samt Start höchstens 20 Minuten.",
        "",
        "**Pflege des Abbilds:** Das Dockerfile spielt beim Bau Debians",
        "Korrekturen ein (`apt-get update && apt-get -y upgrade`, siehe",
        "Starter-Paket), alle Abhängigkeiten sind **fest gepinnt**, und das",
        "Manifest trägt `oaap_manifest: \"0.23\"` sowie `care: {rebuild: self}`.",
        "Dann darf der Knoten bei Sicherheitsmeldungen selbst neu bauen,",
        "ohne auf Deine nächste Version zu warten. Kannst Du das nicht",
        "zusagen, schreib `rebuild: release`.",
    ]
    return ("Die Plattform, auf der die App läuft", lines)


def _sec_access():
    """Zugänge ohne Browser-Anmeldung: Programme und Geräte.

    Seit 0.4.2. Das Handball-Infoboard (18.09.) plante für beides
    `public`-Routen mit eigenen Schlüsseln -- für Programme unnötig
    (RFC-0027 gibt es seit 0.1.62), für Geräte richtig, aber mit Fakten,
    die in keinem Briefing standen: keine Identität, eine Bremse, ein
    Zugriffsprotokoll mit Pfad. Stand im Contract erst ab v0.6.
    """
    return ("Zugänge ohne Browser-Anmeldung", [
        "Nicht jeder Aufrufer ist ein Mensch mit Browser. Zwei Fälle, zwei",
        "Wege — bitte nichts selbst erfinden, was es schon gibt:",
        "",
        "**Programme** (Desktop-Client, Webhook, anderes System, Skript):",
        "**API-Schlüssel** der Plattform. Der Betreiber legt einen",
        "Maschinen-Zugang mit Rolle an und stellt einen Schlüssel aus, auf",
        "**eine Instanz** begrenzt und mit Ablaufdatum. Das Programm ruft",
        "die **normale, geschützte Route** mit",
        "`Authorization: Bearer oaapk_…` — die App bekommt `X-OAAP-User`,",
        "`X-OAAP-User-Id`, `X-OAAP-Roles` und `X-OAAP-Display-Name` (den",
        "Titel des Zugangs), **kein** `X-OAAP-Email`, und den",
        "`Authorization`-Header selbst. Keine `public`-Route,",
        "kein eigenes Schlüsselverfahren. Zwei Stolpersteine:",
        "",
        "- Den `Authorization`-Header auf geschützten Routen **nicht** für",
        "  Eigenes benutzen und einen unbekannten Wert dort nicht ablehnen",
        "  — der Aufrufer ist schon geprüft.",
        "- Ruft eine Browserseite fremder Herkunft die API mit Schlüssel,",
        "  kommt vorher ein `OPTIONS`-Preflight **ohne** Anmeldung bei der",
        "  App an: mit `200`/`204` und CORS-Kopfzeilen beantworten.",
        "",
        "**Geräte ohne Benutzer** (Anzeige, geteiltes Tablet, Brille): Ein",
        "Browser kann bei Seitenaufruf und WebSocket-Aufbau keinen",
        "Schlüssel-Header senden. Hier ist eine `public`-Route mit",
        "**eigenem** Schlüssel richtig — mit diesen Fakten:",
        "",
        "- Auf `public`-Routen fehlen `X-OAAP-User`/`X-OAAP-Roles`",
        "  **immer**, auch wenn jemand angemeldet ist.",
        "- Schlüssel lang und zufällig, widerrufbar, Sperre nach",
        "  Fehlversuchen — das weiß nur die App.",
        "- Vor der Route sitzt eine **Bremse**: Standard 300 Anfragen je",
        "  60 s je Client-Adresse, dann `429` mit `Retry-After`. Ein",
        "  WebSocket zählt einmal; alle Geräte hinter einem Anschluss",
        "  zählen als **einer**. Braucht die App mehr, im Postkasten sagen.",
        "- Das Zugriffsprotokoll des Gateways speichert den **Pfad**, nicht",
        "  den Query-Teil. Schlüssel deshalb **ins Fragment** der Adresse",
        "  (`…/display#k=…`), das der Browser nie an einen Server schickt;",
        "  nach dem Laden mit `history.replaceState` aus der Adresse nehmen.",
        "- `public` erweitert den Rahmen: Ein Mensch bestätigt das Manifest",
        "  einmal. In einer Generalprobe (Testcode auf Produktivdaten) gibt",
        "  es keine `public`-Route.",
    ])


def _sec_capabilities():
    """Was eine App von der Plattform bekommen KANN (Manifest 0.3–0.23).

    0.9.0: bis Manifest 0.23 nachgezogen (Ereignisse, Einladen,
    Einrichtungstür, Hilfe, Vorlagen, fremder Inhalt, eigene Anmeldung)
    -- das Briefing endete bei 0.18, der Knoten kannte längst 0.23.

    Seit 0.5.0. Bis 0.4.5 stand hier nichts davon: Das Briefing kannte
    den Stand vom September, die Plattform hatte inzwischen Zwilling,
    fachliche Berechtigungen, Destinationen, Vorgänge, MCP und Assets.
    Eine App-KI baut nach, was im Briefing steht — was fehlt, erfindet
    sie selbst nach. Die Zwillingsgrenzen stehen hier mit Absicht so
    deutlich: Ein append-only-Speicher ohne Löschweg ist für
    personenbezogene Daten die falsche Ablage, und das muss gesagt sein,
    BEVOR jemand Daten über Personen hineinschreibt.
    """
    return ("Was die Plattform Dir bietet", [
        "Jeder Abschnitt ist **optional**, steht auskommentiert im",
        "Starter-Paket und folgt derselben Regel: **Die App erklärt im",
        "Manifest, ein Mensch im Mandanten gewährt, die App prüft.** Eine",
        "Erklärung allein gewährt nichts. Die Plattform reicht dafür",
        "`OAAP_*`-Variablen in den Container; die deklarierst Du **nicht**",
        "unter `config`, und niemand kann sie überschreiben. Fehlt eine,",
        "läuft die App ohne diese Fähigkeit weiter, statt abzustürzen.",
        "",
        "| Bedarf | Manifest | Die App bekommt |",
        "|---|---|---|",
        "| Gemeinsame Daten mit anderen Apps des Mandanten | `data_model`, `contributes`, `consumes` | `OAAP_TWIN_URL`, `OAAP_PLATFORM_KEY` |",
        "| Feine fachliche Rechte, vom Mandanten gepflegt | `authorization` | `OAAP_AUTHZ_URL`, `OAAP_AUTHZ_KEY` |",
        "| Ein Ziel draußen (anderes System, KI-Dienst) | `destinations` | `OAAP_DESTINATION_<NAME>_URL` |",
        "| Menschen etwas zur Bearbeitung vorlegen | `cases: {submit: true}` | `OAAP_CASES_URL`, `OAAP_CASES_KEY` |",
        "| Werkzeuge für KI-Assistenten anbieten | `mcp: {path: /mcp}` | Aufrufe im Namen der fragenden Person |",
        "| Kachel im Portal gruppieren | `launchpad: {group: …}` | — |",
        "| Hilfe mitbringen (Pflicht mit Oberfläche) | `docs` | Deine Ordner in der Hilfe des Mandanten |",
        "| Ereignisse senden und empfangen | `events` | `OAAP_EVENTS_URL`, `OAAP_EVENTS_KEY` |",
        "| Menschen in den Mandanten einladen | `people: {invite: true}` | `OAAP_PEOPLE_URL`, `OAAP_PEOPLE_KEY` |",
        "| Von einem Rezept eingerichtet werden | `setup: {path: …}` | `OAAP_SETUP_KEY` |",
        "| Vorlagen für Assistenten zu den eigenen Werkzeugen | `assistant_templates` (nur mit `mcp`) | — |",
        "| MCP-Tür liefert fremden Inhalt (Webseiten) | `mcp_content: {untrusted: true}` | nur lesende Sichten vorgeschlagen |",
        "| Clients ohne Browser selbst anmelden (Git) | `identity: {oidc_client: …}` | `OAAP_OIDC_*` |",
        "",
        "Einzelheiten zu jedem Abschnitt stehen im Contract und in der",
        "Spezifikation `oaap.apps.runtime`. Die Startseite des",
        "Starter-Pakets zeigt, welche dieser Türen eine Instanz",
        "tatsächlich bekommen hat — das ist der schnellste Test.",
        "",
        "Ab Manifest 0.19 ist jede neue Fähigkeit ein **eigener Block**",
        "oben im Manifest (nie ein neuer Schlüssel unter `mcp:`): Ein",
        "älterer Knoten überspringt ihn, statt die App abzulehnen.",
        "",
        "**Ohne Erklärung bekommt jede Instanz drei Tatsachen:**",
        "`OAAP_EGRESS` (`closed` oder `open` — im geschlossenen Mandanten",
        "biete nicht an, was nicht gehen kann, und sag warum),",
        "`OAAP_IDENTITY_HEADERS` (die Kopfzeilen, die Du glauben darfst)",
        "und `OAAP_PORTAL_URL` (die Seite der Instanz im Portal, für einen",
        "Link aus Deiner Verwaltung).",
        "",
        "**Ereignisse:** Jeder gesendete Typ heißt",
        "`app.<app-id>.<objekt>.<was>.v1` (jeder Teil nur Kleinbuchstaben",
        "und Ziffern — aus `montage-doku` wird `montagedoku`), hat einen Titel und ein",
        "JSON-Schema als Datei im Paket. Gesendet wird ein CloudEvent per",
        "`POST` an `OAAP_EVENTS_URL`, gelesen unter `/feed` — oder die",
        "Plattform schiebt an Deinen `endpoint`. Jedes Abo gewährt der",
        "Mandanten-Admin. **Einladen:** Die Plattform legt die Anmeldung an",
        "(Rolle `partner`) und schickt die Mail; ob Deine App einladen",
        "darf, entscheidet ein Schalter des Mandanten, standardmäßig aus.",
        "**MCP:** Lesende Werkzeuge tragen die Annotation",
        "`readOnlyHint: true` — ohne sie nimmt das MCP-Gateway ein",
        "Werkzeug als schreibend.",
        "",
        "### Der Digitale Zwilling",
        "",
        "Der Zwilling ist das gemeinsame Gedächtnis eines Mandanten.",
        "Mehrere Apps beschreiben **dieselben Objekte**, und die Daten",
        "wachsen über **Relationen** zusammen:",
        "",
        "- Wer ein Objekt anlegt, ist `owner` und besitzt dessen",
        "  **Kerngruppe**. Andere Apps schreiben als `contributor` in ihre",
        "  **eigene Gruppe** am selben Objekt — niemand überschreibt die",
        "  Gruppe eines anderen. `consumes` liest.",
        "- Jedes Objekt trägt Deinen fachlichen Schlüssel (`source_key`),",
        "  damit es dieselbe Sache bleibt, auch wenn eine zweite App sie",
        "  kennt. Doppelte Objekte kann ein Mensch zusammenführen; alte",
        "  Kennungen antworten danach weiter.",
        "- `OAAP_TWIN_URL` endet schon auf `/twin` — hänge nur",
        "  `/objects`, `/objects/{id}` usw. an, nie ein zweites `/twin`.",
        "  Den Schlüssel `OAAP_PLATFORM_KEY` sendest Du als Bearer.",
        "- Der Zwilling lebt **je Mandant auf seinem Knoten** und nur dort,",
        "  wo der Knoten das Profil `store` trägt. Sonst installiert die",
        "  App normal, bekommt die beiden Variablen aber nicht.",
        "",
        "**Grenzen, die Du heute kennen musst:**",
        "",
        "- **Der Zwilling vergisst nichts.** Jede Änderung wird angehängt,",
        "  nichts gelöscht; einen Löschweg gibt es noch nicht. Deshalb:",
        "  **keine personenbezogenen Daten im Klartext** in den Zwilling",
        "  (erst recht keine besonders schützenswerten wie medizinische).",
        "  Hinein gehören Schlüssel (gern pseudonym), Titel, Relationen,",
        "  **Zustände** — auch der Gesundheitszustand Deiner App und ihrer",
        "  Teile — und andere Fakten ohne Personenbezug;",
        "  die eigentlichen Daten bleiben in Deiner App unter `/data`, wo",
        "  sie gelöscht werden können.",
        "- **Lesen gilt je Objekttyp.** Jede App, die einen Typ liest, sieht",
        "  alle Gruppen dieses Typs. Einzelne Gruppen nur für bestimmte",
        "  Leser gibt es noch nicht.",
        "- **Eine Generalprobe** (Testcode auf einer Kopie der",
        "  Produktivdaten) bekommt keinen Zwilling.",
        "",
        "Wenn Deine App Daten liefern will, die an diesen Grenzen hängen",
        "bleiben, schreib einen Brief — die Grenzen sind bekannt und",
        "werden bearbeitet.",
    ])


def _sec_ui():
    """Oberfläche, Hilfe, MCP und Zwilling nach RFC-0118 („Ein Gesicht für
    alle OAAP-Apps“, Studio 0.7.0). Bis 0.6 waren Farben und Rahmen nur
    empfohlen -- seither sind sie die Regel, und eine fremde App soll sie
    von Anfang an einhalten statt später umgebaut zu werden."""
    return ("Oberfläche, Hilfe und KI", [
        "Die App soll aussehen wie ein Teil der Plattform, nicht wie ein",
        "eigenes Projekt. Verbindlich (RFC-0118):",
        "",
        "**Name.** `app.name` sagt auf Deutsch, was die App tut, kurz und",
        "**ohne „OAAP“** (*Aufträge*, nicht *OAAP Auftragsverwaltung*); ein",
        "Fremdprodukt steht in Klammern hinter der Funktion. Derselbe Name",
        "steht im Seitentitel, in der Kopfleiste und in `docs.title`.",
        "",
        "**Rahmen.** Jede Seite lädt das Kit und das Rahmenskript der",
        "Plattform — beide liefert das Gateway unter dem eigenen Namen der",
        "Instanz aus, es sind also keine fremden Ressourcen:",
        "",
        "```html",
        '<link rel="stylesheet" href="/platform/theme.css">',
        '<script src="/platform/app.js" defer></script>',
        '<body class="oaap-app">',
        '<header class="oaap-header" data-oaap-product="<app-id>">',
        '  <a class="oaap-brand" href="./" data-oaap-portal>',
        '    <span class="oaap-logo"><!-- Sechseck-SVG --></span>',
        '    <span class="oaap-tenant"></span><span class="oaap-appname">Aufträge</span></a>',
        '  <span class="oaap-actions"></span>',
        "</header>",
        '<nav class="oaap-tabs">…Reiter als Links…</nav>',
        '<main class="oaap-main">…</main>',
        '<footer class="oaap-footer"><span>Aufträge 1.2.0</span><span class="oaap-platform"></span></footer>',
        "```",
        "",
        "Die App schreibt Kopfleiste und Fußzeile selbst (Name, Weg zurück);",
        "das Skript ergänzt Mandant, **Hilfe**, **Fragen** (Chat mit dem",
        "Kontext der Seite), das **Benutzermenü** und **Feedback**. Ohne das",
        "Skript bleibt die Seite ganz. Das Starter-Paket zeigt es lauffähig.",
        "",
        "- **Keine eigene Farbpalette**: Farben nur über die Variablen des",
        "  Kits (`var(--oaap-color-…)`, `var(--oaap-badge-…)`), Dunkelmodus",
        "  inklusive. Klassen des Kits: `oaap-card`, `oaap-table` in",
        "  `oaap-tablewrap`, `oaap-badge` (ok/warn/err/info/test),",
        "  `oaap-filter`, `oaap-btn primary/danger`, `oaap-msg`, `oaap-kv`,",
        "  `oaap-pagehead`, `oaap-back`, `oaap-empty`.",
        "- **Keine Rollen und keine Kopfzeilen** auf der Seite — wer",
        "  angemeldet ist, zeigt das Benutzermenü. Kein Slogan, keine",
        "  Großbuchstaben im Kopf, keine Versionsnummer außer in der Fußzeile.",
        "- **Keine externen Ressourcen** im Browser (keine Webfonts, keine",
        "  CDNs, keine Tracker).",
        "- Deutsch als Oberflächensprache, echte Umlaute (ä ö ü ß), keine",
        "  Entwicklerwörter (keine RFC-Nummern, keine internen Kennungen).",
        "- **Tablet zuerst**: Bedienelemente mindestens 44 px hoch.",
        "",
        "**Navigation.** Die Bereiche der App sind **eine** Reiterleiste aus",
        "Links unter der Kopfleiste (Unterpfad wie `/bereiche` oder `?tab=`),",
        "nie Knöpfe mit Skript, keine Seitenleiste. Der erste Reiter liest",
        "(*Übersicht*), Einstellungen und Verwaltung kommen zuletzt. Eine",
        "Objektseite hat „← Zurück zur Liste“ über dem Titel.",
        "",
        "**Listen.** Ab einer Handvoll Zeilen ein Listenbericht: oben eine",
        "Filterleiste als GET-Formular (Suche `q` zuerst, höchstens vier",
        "Auswahlen, *Zurücksetzen*), eine Zählzeile („23 von 340“),",
        "sortierbare Spalten (`sort=spalte`/`sort=-spalte`), Blättern ab 50",
        "(`offset`, nie eine stille Grenze). **Eine** Zeilenaktion: die Zeile",
        "öffnet die Objektseite — Formulare und Knopfreihen gehören nie in",
        "Tabellenzeilen. Status als Badge. Eine leere Liste sagt, was zu tun",
        "ist. Nach dem Speichern umleiten (kein erneutes Absenden).",
        "",
        "**Hilfe (`docs:`) ist Pflicht.** Lege einen Ordner `docs/` mit",
        "Markdown an: `index.md` (was die App tut, für wen), eine Anleitung",
        "je Hauptaufgabe, `faq.md` — für die echten Anwender, in einfacher",
        "Sprache. Im Manifest (ab `oaap_manifest: \"0.17\"`):",
        "",
        "```yaml",
        "docs:",
        "  - product: <app-id>",
        "    title: \"Aufträge\"",
        "    path: docs/",
        "    entry: index.md",
        "    lang: de",
        "    audience: [user, keyuser, admin]",
        "    readers: [user]",
        "    kinds: {index.md: overview, auftrag-anlegen.md: howto, faq.md: faq}",
        "```",
        "",
        "Die Plattform gibt die Dateien an die Hilfe des Mandanten; *Hilfe*",
        "in der Kopfleiste öffnet sie, und der Chat antwortet daraus. Keine",
        "eigene Hilfeseite daneben — eine Quelle.",
        "",
        "**MCP (`mcp:`) für jede App, die Daten hält.** Die App bedient",
        "`mcp: {path: /mcp}` mit **Lesewerkzeugen** für ihre Hauptobjekte",
        "(`<app>_list`, `<app>_search`, `<app>_read`, deutsche",
        "Beschreibungen, kleine JSON-Antworten, nie Geheimnisse) und ein bis",
        "drei Vorlagen (*prompts*) für die Hauptaufgaben. Jedes Werkzeug",
        "prüft dieselben Rollen wie die Seite — es zeigt nie mehr, als die",
        "Seite derselben Person zeigt. **Schreibwerkzeuge** nur dort, wo die",
        "Seite dieselbe Handlung anbietet. `/mcp` beantwortest Du vor jeder",
        "eigenen Prüfung, die ein Browserformular erwartet; die Route im",
        "Manifest muss `/mcp` für die berechtigten Rollen abdecken. Keine",
        "App baut einen eigenen Chat — KI kommt über diese Tür und über",
        "*Fragen* in der Kopfleiste.",
        "",
        "**Zwilling.** Kennt die App Geschäftsobjekte, die es auch außerhalb",
        "von ihr gibt — eine Firma, eine Person, ein Projekt, eine Maschine —,",
        "legt sie sie **in den Zwilling** (`contributes`) oder **liest die des",
        "Zwillings** (`consumes`), statt eine Privatkopie daneben zu führen.",
        "Zeige Zwillingsobjekte mit ihrem Titel, nie mit der URN. Hat die",
        "App keine solchen Objekte, deklariert sie nichts — ein leeres",
        "Datenmodell erfüllt keine Pflicht.",
    ])


def _sec_users():
    """Benutzer und fachliche Rechte — die Naht zwischen App und Plattform.

    Steht seit dem 04.08. als empfohlenes Muster im App Deployment
    Contract und wurde bis 0.4 in keinem Briefing erwähnt; genau daran
    scheitern Apps mit einem eigenen Rollenmodell. Der letzte Absatz
    sagt bewusst, was es **nicht** gibt: Selbstregistrierung mit
    Fremdkonten ist heute keine Plattformfunktion. Etwas anderes zu
    behaupten wäre schlimmer als die Lücke.
    """
    return ("Benutzer und fachliche Rechte", [
        "Der Grundsatz, an dem die Naht verläuft:",
        "",
        "> **Die Plattform entscheidet, wer jemand ist und ob er die App",
        "> betreten darf. Die App entscheidet, wer er *darin* ist.**",
        "",
        "Konkret:",
        "",
        "- **Der Zutritt ist Sache des Manifests.** Die Rollen an der Route",
        "  entscheiden, wer die App überhaupt erreicht; das Gateway setzt",
        "  das durch. Die Plattform kennt `admin`, `keyuser`, `user`,",
        "  `guest`, `partner` und `support` — mehr nicht, und diese Liste",
        "  wächst nicht je App.",
        "- **`partner` und `support` nicht verwechseln.** `partner` ist die",
        "  externe Firma in deinen Abläufen (Lieferant, Sponsor) und trägt",
        "  keinerlei Plattformrechte. `support` ist der Dienstleister, der",
        "  den Knoten betreut, und liest Zustandsseiten über alle Mandanten",
        "  hinweg. Ein Lieferantenbereich gehört an `partner`, nie an",
        "  `support` (RFC-0039).",
        "- **Braucht die App ein feineres Rollenmodell, baut sie es selbst**",
        "  — das ist erwartet und richtig, kein Umweg.",
        "- **Beim ersten Kontakt einen eigenen Benutzersatz anlegen.**",
        "  Kommt ein unbekannter `X-OAAP-User`, legt die App ihren eigenen",
        "  Datensatz dazu an. Entweder leitet sie eine fachliche",
        "  Anfangsrolle aus `X-OAAP-Roles` ab — oder sie legt ihn, bei",
        "  sensiblen Anwendungen, **gesperrt und ohne Rechte** an und zeigt",
        "  einen Hinweis „bitte an die Administration wenden“. Dazu eine",
        "  **Freigabeansicht**, in der neue Anmeldungen eine fachliche",
        "  Rolle bekommen oder mit einem **vorhandenen Stammsatz verknüpft**",
        "  werden (ein Mitarbeiter existiert meist schon, bevor er sich das",
        "  erste Mal anmeldet — verknüpfen, nicht doppeln).",
        "- **Nach der ersten Zuordnung gehört die Rolle der App.** Niemals",
        "  bei späteren Anfragen erneut aus `X-OAAP-Roles` ableiten: Ein",
        "  Dauerabgleich überschreibt lautlos, was die Administration",
        "  eingestellt hat. Für die Sicherheit braucht man ihn auch nicht —",
        "  wer auf der Plattform deaktiviert wird, kommt am Gateway nicht",
        "  mehr vorbei und erreicht die App nie wieder.",
        "- **`X-OAAP-User-Id` ist der stabile Schlüssel** zum Verknüpfen —",
        "  nicht `X-OAAP-User`: Anmelde- und Anzeigenamen sind änderbar, und",
        "  ein frei gewordener Name kann später jemand anderem gehören.",
        "  Echte Personen als Stammdaten ohne Login anlegen und beim ersten",
        "  Kontakt verknüpfen.",
        "- **Fachliche Rechte kann die Plattform vergeben** (RFC-0045):",
        "  Deine App erklärt im Manifest-Abschnitt `authorization` Objekte",
        "  und Tätigkeiten (etwa „Auftrag: lesen, bearbeiten“), der Mandant",
        "  vergibt sie im Portal unter „Rollen & Rechte“, Deine App fragt",
        "  nach. Das ist der empfohlene Weg, wenn mehrere Menschen im",
        "  Mandanten Rechte pflegen sollen; ein eigenes Rollenmodell bleibt",
        "  erlaubt.",
        "",
        "**Was es heute nicht gibt — ehrlich gesagt:** Eine",
        "**Selbstregistrierung** von Anwendern ist keine Funktion, die",
        "Deine App auslösen kann; Benutzer legt die Administration an",
        "(oder ein angeschlossener Identitätsanbieter des Mandanten).",
        "Braucht die App so etwas, ist das keine Bastelaufgabe — schreib",
        "einen Brief in den Postkasten, bevor Du eine eigene Anmeldung",
        "baust.",
    ])


def _sec_collab(repo_line=None):
    lines = []
    if repo_line:
        lines += [f"**Repository:** {repo_line}", "",
                  "- Default-Branch ist **`main`**.",
                  "- Committe selbstständig in kleinen, nachvollziehbaren",
                  "  Schritten mit aussagekräftigen Nachrichten."]
    lines += [
        "- **Postkasten:** Rückfragen, Testergebnisse und Befunde laufen als",
        "  Markdown-Briefe im Projekt-Repository unter `collab/letters/`",
        "  bzw. `collab/reports/`. Regeln: zu Sitzungsbeginn **immer erst",
        "  `git pull`**, Briefe sind unveränderlich (Antwort = neuer Brief",
        "  mit `re:`-Betreff), Brief sofort committen und pushen, und so",
        "  schreiben, dass die beteiligten Menschen mitlesen können.",
        "- Schreib einen ersten Brief, sobald Du dieses Blatt gelesen hast:",
        "  was Du verstanden hast, was Du zuerst baust, was Dir fehlt.",
    ]
    if not repo_line:
        lines += [
            "",
            "Gibt es kein gemeinsames Repository — etwa weil das Projekt nur",
            "Pakete abliefert —, vereinbart einen anderen festen Ort für",
            "diese Briefe. Wichtig ist nicht das Verzeichnis, sondern dass",
            "Fragen und Befunde **schriftlich an einer Stelle** stehen, an",
            "der beide Seiten nachsehen.",
        ]
    # Ohne Repository hieße die Überschrift die Hälfte des Blattes lang
    # etwas, was darin nicht steht.
    return ("Repository und Zusammenarbeit" if repo_line
            else "Zusammenarbeit und Postkasten", lines)


def _sec_unclear():
    return ("Wenn etwas unklar ist", [
        "Rate nicht bei fachlichen Fragen — leg einen Brief in den Postkasten",
        "und arbeite so lange an dem weiter, was klar ist. Technische",
        "Unklarheiten zur Plattform beantwortet der Contract; bleibt eine",
        "Lücke, schreib sie in einen Brief (das verbessert die Plattform).",
    ])


def starter_name():
    """`oaap-starter-<version>` — Dateiname der ZIP und Ordner darin.

    Seit 0.5.1. Vorher hieß jeder Download `oaap-starter.zip` mit einem
    Ordner `starter/`, und zwei Stände ließen sich nicht auseinander-
    halten (Jörg, 17.11.). Die Version kommt aus dem Manifest des
    Starters selbst, nicht aus der des Studios: Name, Ordner und Inhalt
    sagen damit dasselbe, und ein Studio-Patch ohne Starter-Änderung
    erzeugt keinen scheinbar neuen Starter.
    """
    with open(os.path.join(STARTER_DIR, "oaap-app.yaml"), encoding="utf-8") as fh:
        m = pkg.parse_manifest(fh.read())
    version = str(((m or {}).get("app") or {}).get("version") or "").strip()
    if not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
        raise OSError("Starter-Manifest ohne lesbare app.version")
    return f"oaap-starter-{version}"


def _starter_label():
    """Für den Text im Briefing — das Briefing gibt es auch ohne Starter."""
    try:
        return starter_name()
    except OSError:
        return "oaap-starter"


def starter_zip():
    """Das Starter-Paket als ZIP — im Speicher, aus den echten Dateien.

    Feste Zeitstempel und sortierte Reihenfolge: Zwei Abrufe ergeben
    Byte für Byte dieselbe Datei, damit eine Prüfsumme etwas bedeutet.
    Alles liegt in genau einem Ordner `oaap-starter-<version>/` — den
    erlaubt die Paketprüfung des Knotens ausdrücklich.
    """
    folder = starter_name()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in STARTER_FILES:
            path = os.path.join(STARTER_DIR, name)
            with open(path, "rb") as fh:
                data = fh.read()
            info = zipfile.ZipInfo(f"{folder}/{name}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def platform_briefing():
    """Das Briefing ohne Vorhaben — alles Verbindliche, nichts Konkretes.

    Für Projekte, die schon laufen und denen nur das Deployment fehlt.
    Es enthält bewusst **nichts Privilegiertes**: keinen Token, keine
    Anlege-Erlaubnis, keine Adresse einer Instanz. Es ist ein Dokument,
    kein Zugang.
    """
    lines = [
        "# OAAP — Plattform-Briefing",
        "",
        f"Erzeugt vom Studio {VERSION} am {now()}.",
        f"Verbindlich ist der App Deployment Contract: {CONTRACT_URL}",
        "",
        "Dieses Blatt ist für die KI oder die Entwicklerin eines Projekts,",
        "das **schon existiert** und auf OAAP laufen soll. Es enthält keinen",
        "fachlichen Auftrag — nur das, was jede App auf dieser Plattform",
        "erfüllen muss, damit ihr Paket angenommen wird.",
        "",
        "Dein Auftrag lautet sinngemäß: **„Mach diese Anwendung",
        "OAAP-fähig — als Paket, das durch die Prüfung geht.“**",
        "",
        f"Zu diesem Blatt gehört das **Starter-Paket** (`{_starter_label()}.zip`)",
        "aus derselben Quelle: das kleinste vollständige Paket, mit",
        "kommentiertem Manifest, Dockerfile und einer App, die genau die",
        "Regeln unten vorführt. Sieh es Dir zuerst an — es ist schneller",
        "gelesen als beschrieben.",
        "",
    ]
    sections = [
        _sec_platform(),
        _sec_ui(),
        _sec_users(),
        _sec_access(),
        _sec_capabilities(),
        ("Was Du abgibst: das Paket", [
            "Ein OAAP-Paket ist eine **ZIP** mit dem Manifest",
            "`oaap-app.yaml` im Wurzelverzeichnis (ein einzelner",
            "umschließender Ordner ist erlaubt) und allem, was zum Bauen",
            "nötig ist.",
            "",
            "Hart abgelehnt wird ein Paket, das",
            "",
            "- eine **andere App-Kennung** trägt als die installierte Instanz,",
            "- eine **unveränderte `app.version`** mitbringt (zähl sie vor",
            "  jedem Deployment hoch),",
            "- ein **ungültiges Manifest** hat,",
            "- **absolute Pfade**, `..` oder **Symlinks** enthält.",
            "",
            "Die App-Kennung (`app.id`) ist nicht der **Instanzname**: Den",
            "wählt, wer die Instanz anlegt, und er lässt sich später",
            "umbenennen. Baue deshalb nie einen Instanznamen oder eine",
            "Adresse in den Code ein.",
            "",
            "Eine **Bestätigung durch einen Menschen** kostet, was den Rahmen",
            "erweitert: neue öffentliche Routen, neue Speicher, neue Ports am",
            "Gateway vorbei. Das ist kein Fehler, sondern eine Rückfrage.",
            "",
            "**Wichtig nach so einer Bestätigung:** Melde **dasselbe Paket",
            "unverändert** erneut an. Die Bestätigung gilt für genau das",
            "Manifest, das abgelehnt wurde — zählst Du die Version hoch,",
            "deckt sie es nicht mehr, und Du drehst Dich im Kreis. Die Regel",
            "gegen unveränderte Versionen greift hier nicht: Sie vergleicht",
            "mit dem, was **installiert** ist.",
        ]),
        ("Wie das Paket auf die Plattform kommt", [
            "Drei Phasen, immer dieselben (RFC-0019):",
            "",
            "1. **Anmelden** — Version, vollständiges Manifest, Prüfsumme und",
            "   Größe. Der Knoten prüft das, *bevor* etwas übertragen wird.",
            "2. **Freigabe** — bei Erfolg ein Einmal-Token, 15 Minuten gültig,",
            "   an genau diese Instanz und Prüfsumme gebunden.",
            "3. **Hochladen** — nur damit.",
            "",
            "Diesen Weg gehst Du entweder selbst gegen den **Deploy-Hook**",
            "Deiner Test-Instanz, oder ein Mensch lädt die ZIP im **Studio**",
            "hoch („Paket prüfen“ → „Ausrollen“). Beides ist derselbe",
            "Ablauf mit denselben Prüfungen.",
            "",
            "**Adresse und Token stehen absichtlich nicht in diesem Blatt.**",
            "Sie gehören zu einer bestimmten Instanz und werden getrennt",
            "übergeben — wer dieses Blatt weitergibt, gibt damit keinen",
            "Zugang weiter. Gibt es die Instanz noch gar nicht, stellt ein",
            "`server_admin` im Portal eine einmalige **Anlege-Erlaubnis**",
            "aus, die an ihrer Stelle tritt.",
            "",
            "**Produktiv setzen ist nie Deine Handlung**: Das bleibt eine",
            "bewusste Entscheidung eines Menschen, mit Versions-Sprung.",
        ]),
        _sec_collab(),
        _sec_unclear(),
    ]
    lines += _numbered(sections)
    lines += [
        "---",
        "",
        "*Dieses Blatt wurde zu einem Zeitpunkt erzeugt und veraltet, wenn",
        "sich die Plattform ändert. Maßgeblich ist immer der Contract unter",
        f"{CONTRACT_URL} — bei einem Widerspruch gilt er, nicht dieses Blatt.*",
    ]
    return "\n".join(lines) + "\n"


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
    node = fleet.node_base(p["hook_url"]) or fleet.node_base(p["node_url"])
    deploy = [
        "Getestete Stände rollst du selbst auf die **Test-Instanz** aus —",
        "Produktivsetzung bleibt eine menschliche Entscheidung mit",
        "Versions-Sprung.",
        "",
    ]
    if node:
        deploy += [
            f"Deine Instanz `{instance}` liegt auf dem Knoten",
            f"**{fleet.host_of(node)}** ({node}). Alle Adressen unten gehören",
            "dorthin; ein Studio auf einem anderen Knoten ändert daran",
            "nichts.",
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
        "**Wenn der Knoten eine Bestätigung verlangt** („would widen what",
        "the instance may reach\"), gilt eine Ausnahme von der Regel",
        "„Version vor jedem Deployment hochzählen\": Die Bestätigung eines",
        "Menschen gilt für genau das Manifest, das abgelehnt wurde. Melde",
        "danach **dasselbe Paket unverändert** erneut an — zählst du hoch,",
        "deckt die Bestätigung es nicht mehr und du drehst dich im Kreis.",
        "Ausführlich steht das im Deployment-Zettel.",
        "",
        "Das Token bekommst du separat (nie im Repository ablegen, nie in",
        "einen Brief schreiben, nicht in Commits committen).",
    ]

    lines = [
        f"# Briefing: {p['name']}",
        "",
        f"Erzeugt vom Studio am {now()} — Vorhaben `{p['id']}`.",
        "",
        "Du bist der KI-Entwicklungsagent für dieses Vorhaben. Dieses",
        "Dokument ist deine Arbeitsanweisung: fachlicher Auftrag, geltende",
        "Regeln und die Art, wie wir zusammenarbeiten. Lies es vollständig,",
        "bevor du Code schreibst, und frag nach, wenn etwas fehlt.",
        "",
    ] + _numbered([
        ("Worum es geht", [
            block(p["goal"], "Ziel noch nicht beschrieben — bitte beim Auftraggeber erfragen."),
            "",
            f"**Auftraggeber:** {block(p['owner'], 'nicht benannt')}",
            f"**Kontext:** {block(p['context'], 'nicht angegeben')}",
        ]),
        ("Wer die App benutzt", [
            block(p["target_users"], "Zielanwender noch nicht beschrieben — bitte erfragen."),
        ]),
        ("Was die erste Version können muss", [
            block(p["scope"], "Umfang noch nicht festgelegt — bitte gemeinsam schärfen."),
            "",
            "Halte dich an diesen Umfang. Ideen darüber hinaus schreibst du",
            "auf, statt sie einzubauen.",
        ]),
        _sec_platform(p["app_type"]),
        _sec_ui(),
        _sec_users(),
        _sec_access(),
        _sec_capabilities(),
        ("Test-Deployment (Deploy-Hook)", deploy),
        _sec_collab(repo_line),
        _sec_unclear(),
    ]) + [
        "---",
        "",
        f"*Status des Vorhabens: {STATUSES.get(p['status'], p['status'])}*",
    ]
    if (p["notes"] or "").strip():
        lines += ["", "*Notizen des Auftraggebers:*", "", p["notes"].strip()]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ server

# ------------------------------------------------------------- MCP-Tür
#
# RFC-0118 §9: die Assistenten des Mandanten lesen, was die Seiten zeigen --
# Vorhaben und Briefings. Dieselben Rollen wie die Seite (keyuser, admin).
# Nie ein Token: Das Studio hält keinen, und die Paketprüfung gibt nur ihre
# Zusammenfassung heraus. Kein Schreibwerkzeug in dieser Stufe.

PAGE_ROLES = {"keyuser", "admin"}

DOOR = mcpdoor.Door(
    "oaap-studio", VERSION,
    "App-Vorhaben dieses Studios lesen: welche es gibt, ihren Stand, ihr Briefing "
    "für die KI und das Plattform-Briefing für neue Apps. Nur lesend.")


def _may_read(who):
    if not PAGE_ROLES & set(who.get("roles") or []):
        raise mcpdoor.Refused("Das Studio ist nur für keyuser und admin.")


def _project(con, pid):
    p = con.execute("SELECT * FROM projects WHERE id = ?", (str(pid or "").strip(),)).fetchone()
    if not p:
        raise mcpdoor.Refused("Dieses Vorhaben gibt es nicht. studio_projects nennt die Vorhaben.")
    return p


@DOOR.tool("studio_projects", "Vorhaben suchen",
           "Nennt die App-Vorhaben mit Kennung, Name, Kontext, Status, App-Typ und wer "
           "entwickelt. Optional nach Suchwort und Status gefiltert.",
           {"q": {"type": "string", "description": "Suchwort in Name, Kontext, Auftraggeber oder Entwickler"},
            "status": {"type": "string", "description": "idee, briefing, entwicklung, test, produktiv oder ruht"}})
def _mcp_projects(args, who):
    _may_read(who)
    q = str(args.get("q") or "").lower().split()
    status = str(args.get("status") or "").strip()
    con = db()
    try:
        rows = con.execute("SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        hay = f"{r['id']} {r['name']} {r['context']} {r['owner']} {r['agent']}".lower()
        if (q and not all(w in hay for w in q)) or (status and r["status"] != status):
            continue
        out.append({"id": r["id"], "name": r["name"], "kontext": r["context"],
                    "status": STATUSES.get(r["status"], r["status"]), "typ": r["app_type"],
                    "entwickelt_mit": r["agent"]})
    return {"vorhaben": out, "anzahl": len(out)}


@DOOR.tool("studio_project_read", "Vorhaben lesen",
           "Liest ein Vorhaben: fachlicher Inhalt (Zielanwender, Ziel, erste Version), "
           "Zielknoten, Test- und Produktiv-Instanz und das zuletzt geprüfte Paket.",
           {"id": {"type": "string", "description": "Kennung des Vorhabens"}}, required=("id",))
def _mcp_project(args, who):
    _may_read(who)
    con = db()
    try:
        p = _project(con, args.get("id"))
    finally:
        con.close()
    return {"id": p["id"], "name": p["name"], "kontext": p["context"], "auftraggeber": p["owner"],
            "status": STATUSES.get(p["status"], p["status"]), "typ": p["app_type"],
            "entwickelt_mit": p["agent"], "zielanwender": p["target_users"], "ziel": p["goal"],
            "erste_version": p["scope"], "weg": p["deploy_way"], "repository": p["repo_url"],
            "zielknoten": p["node_url"], "test_instanz": p["instance"],
            "produktiv_instanz": p["prod_instance"], "test_adresse": p["test_url"],
            "paket": {"datei": p["pkg_file"], "version": p["pkg_version"],
                      "geprueft_am": p["pkg_at"]} if p["pkg_file"] else None,
            "notizen": p["notes"], "geaendert": p["updated_at"]}


@DOOR.tool("studio_briefing", "Briefing lesen",
           "Liest das Briefing für die KI: zu einem Vorhaben (Auftrag und Regeln der "
           "Plattform) oder ohne Kennung das Plattform-Briefing für ein Projekt, das es "
           "schon gibt. Markdown-Text.",
           {"id": {"type": "string", "description": "Kennung des Vorhabens (leer = Plattform-Briefing)"}})
def _mcp_briefing(args, who):
    _may_read(who)
    if not str(args.get("id") or "").strip():
        return platform_briefing()
    con = db()
    try:
        p = _project(con, args.get("id"))
    finally:
        con.close()
    return briefing(p)


DOOR.prompt("studio_new_app", "Neue App beschreiben",
            "Hilft, ein neues App-Vorhaben so zu beschreiben, dass ein gutes Briefing entsteht.",
            "Ich möchte im Studio ein neues App-Vorhaben anlegen: {idee}. Frag mich nach "
            "den Zielanwendern, dem Problem in meinen Worten und dem, was die erste Version "
            "können muss — kurz und ohne Fachjargon. Fasse am Ende die Felder Name, "
            "Zielanwender, Ziel und erste Version zusammen, damit ich sie ins Studio "
            "übernehmen kann. Das Plattform-Briefing (studio_briefing ohne Kennung) "
            "nennt die Regeln, die jede App erfüllen muss.", ("idee",))
DOOR.prompt("studio_project_status", "Stand eines Vorhabens",
            "Fasst den Stand eines Vorhabens zusammen und nennt den nächsten Schritt.",
            "Lies das Vorhaben {id} im Studio (studio_project_read) und fasse zusammen: "
            "Wofür ist die App, wo steht sie (Status, Paket, Instanzen) und was ist der "
            "nächste sinnvolle Schritt auf dem Weg Idee → Briefing → Test → Produktiv.",
            ("id",))


class Handler(BaseHTTPRequestHandler):
    server_version = "oaap-studio/" + VERSION
    protocol_version = "HTTP/1.1"

    # -- helpers ---------------------------------------------------------
    def log_message(self, fmt, *args):  # stdout, contract rule 5
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def send_bytes(self, raw, status=200, content_type="application/octet-stream",
                   extra_headers=()):
        """Wie send_html, nur für fertige Bytes (das Starter-Paket)."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

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
        self.send_html(page("Nur hinter dem Gateway", af.card(
            "Diese App läuft nur hinter dem OAAP-Gateway",
            '<p class="muted">Es sind keine geprüften Identitätsdaten angekommen. '
            "Das Studio hat bewusst keinen eigenen Login — die Anmeldung erledigt "
            "die Plattform. Bitte über das Portal-Launchpad öffnen.</p>")), status=403)

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
        if path == "/mcp":
            return DOOR.serve(self)

        user, roles = self.identity()
        if not user:
            return self.deny_without_gateway()
        if not roles & {"keyuser", "admin"}:
            return self.send_html(page("Keine Berechtigung", af.msg(
                "Das Studio ist für keyuser und admin.", error=True)), status=403)
        roles_label = ", ".join(sorted(roles))
        con = db()
        try:
            if path == "/":
                rows = con.execute(
                    "SELECT * FROM projects ORDER BY name COLLATE NOCASE").fetchall()
                return self.send_html(list_page(rows, user, roles_label,
                                                (query.get("msg") or [""])[0], query))
            if path == "/hilfe" or path.startswith("/hilfe/"):
                # Die Hilfe aus docs/ -- dieselben Dateien, die über `docs:`
                # an die Hilfe des Mandanten gehen (eine Quelle, RFC-0118 §9).
                got = helpdocs.load(path[len("/hilfe/"):] if path != "/hilfe" else "")
                if not got:
                    return self.send_html(page("Nicht gefunden", af.msg(
                        "Diese Hilfeseite gibt es nicht.", error=True), user, roles_label), status=404)
                title, html_ = got
                return self.send_html(page(title, (
                    f'<div class="grid2" style="grid-template-columns:3fr 1fr;align-items:start">'
                    f'<article class="oaap-card">{html_}</article>'
                    f'<aside class="oaap-card"><h2>Hilfe</h2>{helpdocs.toc(path[7:])}</aside></div>'),
                    user, roles_label))
            # Onboarding: beide Blätter hängen an KEINEM Vorhaben — das ist
            # der Sinn der Sache (siehe onboarding_page).
            if path == "/briefing":
                return self.send_html(
                    onboarding_page(platform_briefing(), user, roles_label))
            if path == "/briefing.md":
                return self.send_html(
                    platform_briefing(),
                    content_type="text/markdown; charset=utf-8",
                    extra_headers=[("Content-Disposition",
                                    'attachment; filename="oaap-plattform-briefing.md"')])
            if path == "/starter.zip":
                try:
                    data = starter_zip()
                    zip_name = starter_name() + ".zip"
                except OSError as exc:
                    self.log_message("starter-paket nicht lesbar: %s", exc)
                    return self.send_html(
                        "<p>Das Starter-Paket liegt in dieser Installation nicht "
                        "vor. Das Plattform-Briefing gibt es unabhängig davon.</p>",
                        status=404)
                return self.send_bytes(
                    data, content_type="application/zip",
                    extra_headers=[("Content-Disposition",
                                    f'attachment; filename="{zip_name}"')])
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
                    return self.send_html(page("Nicht gefunden", af.back("/", "Zurück zur Liste") + af.msg(
                        "Dieses Vorhaben gibt es nicht.", error=True), user, roles_label), status=404)
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
                        target_node(p, portal_base(self.headers.get("Host", "")))))
                if sub == "/loeschen":
                    if "admin" not in roles:
                        return self.send_html("<p>Löschen ist Administratoren vorbehalten.</p>",
                                              status=403)
                    return self.send_html(delete_page(p, user, roles_label))
                # Der Zielknoten wird gefragt, wenn ein Schlüssel für ihn
                # hinterlegt ist — sonst gar nicht. `?frisch=1` umgeht die
                # kurz vorgehaltene Antwort.
                node = target_node(p, portal_base(self.headers.get("Host", "")))
                st = fleet.status(node["base"], FLEET_KEYS,
                                  fresh=bool(query.get("frisch")))
                return self.send_html(object_page(p, user, roles_label,
                                                  (query.get("msg") or [""])[0],
                                                  is_admin="admin" in roles,
                                                  node=node, st=st))
            self.send_html(page("Nicht gefunden", af.back("/", "Zur Übersicht") + af.msg(
                "Diese Seite gibt es nicht.", error=True), user, roles_label), status=404)
        finally:
            con.close()

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/mcp":
            # Vor dem Herkunftsschutz: MCP kommt vom Gateway, nicht aus
            # einem Browserformular; die Tür prüft die Rollen selbst.
            return DOOR.serve(self)
        user, roles = self.identity()
        if not user:
            return self.deny_without_gateway()
        if not roles & {"keyuser", "admin"}:
            return self.send_html(page("Keine Berechtigung", af.msg(
                "Das Studio ist für keyuser und admin.", error=True)), status=403)
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
                # Auf dem Zielknoten hat sich gerade nachweislich etwas
                # geändert — die vorgehaltene Auskunft ist damit alt.
                fleet.forget(fleet.node_base(p["hook_url"]))
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
    print(f"Studio {VERSION} listening on 0.0.0.0:{PORT}, data in {DATA_DIR}",
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
