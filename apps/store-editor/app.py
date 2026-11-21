#!/usr/bin/env python3
"""Store-Editor — Store-Listen prüfen und pflegen (RFC-0013 Bauschritte 1+2, RFC-0118).

Eine Store-Liste ist kein Dokument, sondern eine Anweisung, die auf
fremden Rechnern Software installiert. Deshalb ist der **Prüfer** der
Kern dieses Werkzeugs und nicht das Formular: Er hält jede Liste gegen
das Schema UND gegen die Manifeste, auf die sie zeigt (Bauschritt 1,
`checker.py`).

Bauschritt 2 legt das Formular darauf: Was redaktionell ist, wird
bearbeitet; was das Manifest belegt, steht verriegelt da und lässt sich
mit einem ausdrücklichen Griff entriegeln — dann ist die Übersteuerung
**markiert** und überlebt die nächste Neuerzeugung (RFC-0012 §1.3).
Das Ergebnis ist eine **Datei zum Herunterladen**.

**Es wird weiterhin nichts veröffentlicht.** Kein Git, keine
Zugangsdaten. Geschrieben wird nur in die Arbeitskopie dieser Instanz.
Das Zurückschreiben ist Bauschritt 3, mit den drei Betriebsarten aus
RFC-0013 §3.

Gebaut als gewöhnliche OAAP-App nach dem App Deployment Contract:
kein eigener Login (die Anmeldung kommt als Gateway-Kopfzeile), ein
HTTP-Port, Konfiguration über deklarierte Umgebungsvariablen, ein
deklarierter Speicher für die Arbeitskopien, Logs nach stdout,
Gesundheitspfad, offline-fähig.

Abhängigkeit: **PyYAML**, und sonst nichts — fremde Manifeste werden
gelesen, und ein selbstgebauter YAML-Leser, der eine Schreibweise
missversteht, wäre genau die stille Falschaussage, gegen die dieses
Werkzeug antritt.

Seit 0.4 (RFC-0118) im Rahmen der Plattform: Kit /platform/theme.css,
Rahmenskript /platform/app.js, eine Reiterleiste, Listenberichte, die
Hilfe aus docs/ und eine lesende MCP-Tür unter /mcp.
"""

import hashlib
import html
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

import yaml

import appframe as af
import checker as ck
import editor as ed
import helpdocs
import mcpdoor

VERSION = "0.4.0"
PORT = 8000
FETCH_TIMEOUT = 15

# Deklarierte Konfiguration (Manifest `config`). Mehrere Listen je
# Instanz ist RFC-0013 Entscheidung 3. Diese Angabe ist nur noch das
# **Saatgut**: Beim ersten Start wandern die Adressen in die
# Quellenverwaltung des Editors, wo sie ein `keyuser` selbst pflegt.
LISTS = [u.strip() for u in
         os.environ.get("STORE_EDITOR_LISTS", "").replace(chr(10), ",").split(",")
         if u.strip()]

# Zugangsschlüssel für private Listen: **feste, deklarierte Plätze** in
# der Instanz-Konfiguration (RFC-0013, Entscheidung Jörgs vom
# 09.08.2026, Form A). Sie stehen dort als `secret: true` — eintragbar,
# nie zurücklesbar — und liegen damit dort, wo die Plattform ohnehin
# Geheimnisse hält, statt dass diese App sich eigenes Geheimnis-
# Handling ausdenkt.
#
# Die Obergrenze ist Absicht und sichtbar: Erscheint eine vierte
# private Liste, ist genau das der Beleg für eine allgemeine Lösung,
# statt sie vorher zu erraten.
TOKENS = [os.environ.get(f"STORE_EDITOR_TOKEN_{i}", "").strip()
          for i in range(1, ed.TOKEN_SLOTS + 1)]

# Deklarierter Speicher (Manifest `storage`, Mount /data). Die
# Umgebungsvariable ist bewusst NICHT im Manifest deklariert: Sie ist
# kein Einstellwert für Betreiber, sondern erlaubt, die App lokal ohne
# Container zu starten.
DATA_DIR = os.environ.get("STORE_EDITOR_DATA", "/data")

_LOCK = threading.Lock()

LEVEL_LABEL = {ck.FEHLER: "Fehler", ck.BEFUND: "Befund", ck.HINWEIS: "Hinweis"}
# Der Plural steht ausgeschrieben da, statt aus einer Regel zu folgen:
# „Fehler" bleibt gleich, „Befund" und „Hinweis" nicht.
LEVEL_PLURAL = {ck.FEHLER: "Fehler", ck.BEFUND: "Befunde", ck.HINWEIS: "Hinweise"}
LEVEL_BADGE = {ck.FEHLER: "err", ck.BEFUND: "warn", ck.HINWEIS: ""}
LEVEL_HELP = {
    ck.FEHLER: "So ist die Liste nicht benutzbar — ein Knoten würde daran "
               "scheitern oder etwas Falsches tun.",
    ck.BEFUND: "Liste und Manifest sagen beide etwas, und es ist verschieden. "
               "Genau das findet beim Lesen kein Mensch.",
    ck.HINWEIS: "Auffällig, aber vielleicht Absicht — etwa eine Behauptung, "
                "die das Manifest (noch) nicht belegt.",
}

KIND_LABEL = {ed.STRUKTUR: "strukturell", ed.REDAKTIONELL: "redaktionell",
              ed.ERZEUGT: "aus dem Manifest"}
KIND_BADGE = {ed.STRUKTUR: "err", ed.REDAKTIONELL: "info", ed.ERZEUGT: ""}

MESSAGES = {
    "gespeichert": ("ok", "Gespeichert — in der Arbeitskopie dieser Instanz. "
                          "Veröffentlicht wird davon nichts."),
    "uebernommen": ("ok", "Aus den Manifesten übernommen."),
    "nichts": ("muted", "Es gab nichts zu übernehmen: Die Liste deckt sich "
                        "bereits mit den Manifesten."),
    "verworfen": ("ok", "Der Entwurf ist verworfen. Es gilt wieder der "
                        "veröffentlichte Stand."),
    "aufgenommen": ("ok", "Der Eintrag ist aufgenommen. Solange sein Manifest "
                          "nicht abrufbar ist, meldet der Prüfer das bei jedem "
                          "Lauf."),
    "entfernt": ("ok", "Der Eintrag ist aus der Arbeitskopie entfernt."),
    "quelle_aufgenommen": ("ok", "Die Liste ist aufgenommen und war auf Anhieb "
                                 "abrufbar."),
    "quelle_stumm": ("muted", "Die Liste ist aufgenommen, aber noch nicht "
                              "abrufbar. Bei einem privaten Repository fehlt "
                              "dann meist der Schlüssel — er wird im Portal "
                              "eingetragen, nicht hier."),
    "quelle_entfernt": ("ok", "Die Liste ist aus dem Editor genommen. Im "
                              "Repository ändert das nichts."),
    "kein_manifest": ("muted", "Das Manifest dieser App war nicht abrufbar — "
                               "es gibt nichts abzugleichen. Der Eintrag bleibt "
                               "unverändert."),
}


# ------------------------------------------------------------ Darstellung

def esc(v):
    return html.escape(str(v if v is not None else ""), quote=True)


APP_NAME = "Store-Editor"

# Nur, was dem Editor eigen ist -- Farben ausschließlich über das Kit
# (/platform/theme.css). Kopfleiste, Reiter, Karten, Tabellen, Badges und
# Knöpfe kommen aus dem Rahmen (RFC-0118 §4).
STYLE = """
  .oaap-main h3{font-size:.9rem;margin:1.4rem 0 .2rem;color:var(--oaap-color-primary)}
  .oaap-card.locked{background:var(--oaap-color-bg)}
  .oaap-card label{display:block;font-size:.85rem;color:var(--oaap-color-text-muted);margin-top:.9rem}
  .oaap-card input:not([type=checkbox]),.oaap-card select,.oaap-card textarea{width:100%;box-sizing:border-box;margin:.25rem 0 .2rem}
  .oaap-card input:disabled,.oaap-card textarea:disabled{opacity:.75}
  textarea{min-height:5.5rem;resize:vertical} textarea.small{min-height:3.5rem}
  .hint{font-size:.8rem;color:var(--oaap-color-text-muted);margin:0 0 .6rem}
  .muted{color:var(--oaap-color-text-muted);font-size:.9rem}
  .err{color:var(--oaap-color-danger)} .ok{color:var(--oaap-color-success)}
  .grid2{display:grid;grid-template-columns:1fr 1fr;gap:0 1.2rem}
  .actions{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:1.1rem;align-items:center}
  .actions form{margin:0}
  .checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));gap:.1rem .8rem;margin:.4rem 0 .2rem}
  .oaap-card .checks label{display:flex;align-items:center;gap:.5rem;margin:0;padding:.3rem 0;font-size:.92rem;color:var(--oaap-color-text);cursor:pointer}
  .lockline{display:flex;align-items:center;gap:.5rem;font-size:.8rem;color:var(--oaap-color-text-muted);margin:.1rem 0 .6rem}
  .oaap-card .lockline label{margin:0;display:flex;gap:.4rem;align-items:center}
  .oaap-msg.note{background:var(--oaap-badge-neutral-bg);color:var(--oaap-badge-neutral)}
  .oaap-main article{max-width:75ch}
  @media (max-width:640px){.grid2{grid-template-columns:1fr}}
"""

# Die Bereiche des Editors -- eine Reiterleiste, Einrichtung zuletzt (§6).
TABS = [("/", "Übersicht"), ("/pruefen", "Liste einfügen"), ("/quellen", "Listen und Zugang")]
ACTIVE = {"lists": "/", "paste": "/pruefen", "sources": "/quellen"}


def page(title, body, user="", roles="", active="", flash="", obj=None, status_note=""):
    """Der Rahmen der Plattform (appframe): Kopfleiste, Reiter, Fußzeile.
    Wer angemeldet ist, zeigt das Benutzermenü des Rahmens -- nie die
    Rollen (user/roles bleiben nur für die Aufrufer stehen)."""
    note = ""
    if flash in MESSAGES:
        cls, text = MESSAGES[flash]
        note = (af.msg(text) if cls != "muted" else
                f'<div class="oaap-msg note" role="status">{esc(text)}</div>')
    return af.page(title, note + body, app=APP_NAME, version=VERSION, product="store-editor",
                   tabs=TABS, active=ACTIVE.get(active), obj=obj, style=STYLE)


# ------------------------------------------------------------ Arbeitskopie
#
# Die Arbeitskopie liegt im deklarierten Speicher der Instanz und
# überlebt damit Neustart, Redeploy und Update wie die Daten jeder
# anderen App — und sie ist in `oaap backup create` enthalten. Sie
# trägt neben dem bearbeiteten Dokument den **veröffentlichten Stand**
# als Vergleichsbasis und die **Markierungen** der Übersteuerungen
# (RFC-0012 §1.3). Beides gehört nicht in die Liste selbst: Eine Liste
# ist ein Dokument nach `oaap-store.schema.json`, die Buchführung des
# Editors hat darin nichts zu suchen und auf fremden Knoten erst recht
# nicht.

def work_path(url):
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return os.path.join(DATA_DIR, f"liste-{key}.json")


def storage_ready():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        probe = os.path.join(DATA_DIR, ".schreibprobe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def load_work(url):
    try:
        with open(work_path(url), encoding="utf-8") as fh:
            work = json.load(fh)
    except (OSError, ValueError):
        return None
    return work if isinstance(work, dict) and isinstance(work.get("doc"), dict) else None


def save_work(url, work):
    """Erst vollständig schreiben, dann umbenennen — nie halb überschreiben."""
    work["saved"] = time.strftime("%Y-%m-%d %H:%M")
    path = work_path(url)
    tmp = path + ".neu"
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(work, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def drop_work(url):
    try:
        os.remove(work_path(url))
    except OSError:
        pass


def start_work(url, published):
    return {"source": url, "doc": json.loads(json.dumps(published)),
            "published": json.loads(json.dumps(published)), "overrides": {},
            "begonnen": time.strftime("%Y-%m-%d %H:%M")}


# ------------------------------------------------------------- Quellen
#
# Welche Listen dieser Editor pflegt. Liegt in seiner eigenen Ablage,
# weil ein `keyuser` das ändern können soll — die **Schlüssel** dagegen
# stehen in der Instanz-Konfiguration und werden hier nur über ihre
# Platznummer angesprochen. Der Editor sieht den Wert, schreibt ihn
# aber nirgends hin und zeigt ihn nie.

def sources_path():
    return os.path.join(DATA_DIR, "quellen.json")


def load_sources():
    """Die eingetragenen Listen. Beim ersten Mal aus der Konfiguration."""
    try:
        with open(sources_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        items = data.get("quellen")
        if isinstance(items, list):
            return [s for s in items if isinstance(s, dict) and s.get("url")]
    except (OSError, ValueError):
        pass
    return [{"url": u, "name": "", "token": 0} for u in LISTS]


def save_sources(items):
    path = sources_path()
    tmp = path + ".neu"
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"quellen": items}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def token_of(src):
    """Der Schlüssel für diese Quelle — '' wenn keiner eingetragen ist."""
    if not isinstance(src, dict):
        return ""
    slot = src.get("token") or 0
    try:
        slot = int(slot)
    except (TypeError, ValueError):
        return ""
    return TOKENS[slot - 1] if 1 <= slot <= len(TOKENS) else ""


def may_configure(roles):
    """Wer Quellen eintragen darf.

    Eine Liste aufzunehmen ist Einrichtung, nicht Redaktion — also
    `keyuser` und `admin`, nicht jeder `user` (RFC-0013 Entscheidung 2
    trennt vorschlagen von freigeben). Ausdrücklich weiterhin **nicht**
    `server_admin`: Der trägt nur die Schlüssel ein, im Portal, weil
    die Instanz-Konfiguration sein Bereich ist. Beides zusammen ist die
    Arbeitsteilung, die aus Jörgs Entscheidung folgt.
    """
    return bool({"keyuser", "admin"} & set(roles or []))


def overrides_of(work, app_id):
    return set((work.get("overrides") or {}).get(app_id) or [])


def set_overrides(work, app_id, fields):
    marks = work.setdefault("overrides", {})
    if fields:
        marks[app_id] = sorted(fields)
    else:
        marks.pop(app_id, None)


# ------------------------------------------------------------------ Abrufen

def fetch(url, headers=None):
    """Eine Datei holen. Bewusst ohne Umleitung auf andere Hosts."""
    head = {"User-Agent": f"oaap-store-editor/{VERSION}"}
    head.update(headers or {})
    req = urllib.request.Request(url, headers=head)
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def http_reason(exc):
    """Warum es nicht ging — ohne je einen Schlüssel zu zeigen."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return (f"Der Server antwortete mit {exc.code}. Bei einem privaten "
                    "Repository heißt das fast immer: kein oder ein "
                    "unzureichender Zugangsschlüssel.")
        if exc.code == 404:
            return ("Der Server antwortete mit 404. Entweder gibt es die Datei "
                    "nicht — oder das Repository ist privat und der Schlüssel "
                    "fehlt; GitHub antwortet in beiden Fällen gleich, damit "
                    "man private Repositories nicht erraten kann.")
        return f"Der Server antwortete mit {exc.code}."
    if isinstance(exc, (urllib.error.URLError, OSError)):
        return f"Nicht erreichbar: {getattr(exc, 'reason', exc)}"
    return f"Das ist keine gültige JSON-Datei: {exc}"


def fetch_published(url, token=""):
    """Die veröffentlichte Liste holen. Gibt (Dokument, Fehlertext)."""
    target, headers, why = ck.document_ref(url, token)
    try:
        doc = json.loads(fetch(target, headers))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError,
            ValueError) as e:
        return None, http_reason(e) + (f" {why}" if why else "")
    return doc, ""


def fetch_manifest(entry, token="", forge=""):
    """Das Manifest eines Eintrags. Gibt (Manifest, Adresse, Grund).

    Der Schlüssel geht **nur** an den Anbieter, für den er eingetragen
    wurde. Sonst könnte eine Liste allein dadurch, dass sie auf ein
    fremdes Repository zeigt, ein Token dorthin schicken lassen.
    """
    pkg = (entry or {}).get("package") or {}
    mine = token if (token and forge
                     and ck.repo_parts(pkg.get("git"))[0] == forge) else ""
    url, headers, why = ck.manifest_ref(pkg, mine)
    if not url:
        return None, "", why
    try:
        manifest = yaml.safe_load(fetch(url, headers))
    except Exception as exc:                                   # noqa: BLE001
        return None, url, http_reason(exc)
    if not isinstance(manifest, dict):
        return None, url, "unter dieser Adresse liegt kein Manifest"
    return manifest, url, ""


def current(src):
    """Der Stand, mit dem gearbeitet wird: Entwurf, sonst Veröffentlichung.

    Sobald ein Entwurf besteht, prüft der Prüfer **ihn** — nicht mehr
    die Veröffentlichung. Sonst wäre der Wächter blind für genau das,
    was gerade entsteht (RFC-0013: der Prüfer ist der Wächter).
    """
    url = src["url"] if isinstance(src, dict) else src
    work = load_work(url)
    if work:
        return work["doc"], work, ""
    doc, err = fetch_published(url, token_of(src))
    return doc, None, err


# -------------------------------------------------------------- Bausteine

def summary_badges(rep):
    out = []
    for lvl in ck.LEVELS:
        n = rep["counts"][lvl]
        if n:
            word = LEVEL_LABEL[lvl] if n == 1 else LEVEL_PLURAL[lvl]
            out.append(f'<span class="oaap-badge {LEVEL_BADGE[lvl]}">{n} {esc(word)}</span>')
    if not out:
        out.append('<span class="oaap-badge ok">ohne Beanstandung</span>')
    return " ".join(out)


def findings_rows(findings, with_app=True):
    rows = []
    for f in sorted(findings, key=lambda x: (ck.LEVELS.index(x["level"]), x["app"])):
        app_cell = f'<td><code>{esc(f["app"]) or "—"}</code></td>' if with_app else ""
        rows.append(
            f'<tr><td><span class="oaap-badge {LEVEL_BADGE[f["level"]]}">'
            f'{esc(LEVEL_LABEL[f["level"]])}</span></td>{app_cell}'
            f'<td><code>{esc(f["field"])}</code></td>'
            f'<td>{esc(f["text"])}</td>'
            f'<td>{esc(f["list"]) or "—"}</td>'
            f'<td>{esc(f["manifest"]) or "—"}</td></tr>')
    return "".join(rows)


def findings_table(rep):
    if not rep["findings"]:
        return ('<div class="oaap-card"><p class="ok">Keine Beanstandung. Jeder '
                'Eintrag deckt sich mit dem Manifest, auf das er zeigt.</p></div>')
    legend = "".join(
        f'<p class="muted"><span class="oaap-badge {LEVEL_BADGE[l]}">{esc(LEVEL_LABEL[l])}</span> '
        f'{esc(LEVEL_HELP[l])}</p>' for l in ck.LEVELS if rep["counts"][l])
    return f'''<div class="oaap-card" style="overflow-x:auto">
  <h2>Was geprüft wurde</h2>
  <table class="oaap-table">
    <tr><th>Art</th><th>App</th><th>Feld</th><th>Befund</th>
        <th>In der Liste</th><th>Im Manifest</th></tr>
    {findings_rows(rep["findings"])}
  </table>
</div>
<div class="oaap-card">{legend}</div>'''


def checkboxes(name, vocab, labels, chosen):
    """Kontrolliertes Vokabular. Unbekannte Werte bleiben erhalten."""
    out = []
    for value in sorted(vocab) + [c for c in chosen if c not in vocab]:
        mark = " checked" if value in chosen else ""
        label = labels.get(value, f"{value} (unbekanntes Vokabular)")
        out.append(f'<label><input type="checkbox" name="{name}" '
                   f'value="{esc(value)}"{mark}> {esc(label)}</label>')
    return f'<div class="checks">{"".join(out)}</div>'


def select(name, vocab, labels, chosen, empty="— nicht gesetzt —"):
    opts = [f'<option value="">{esc(empty)}</option>']
    for value in list(vocab) + ([chosen] if chosen and chosen not in vocab else []):
        mark = " selected" if value == chosen else ""
        opts.append(f'<option value="{esc(value)}"{mark}>'
                    f'{esc(labels.get(value, value))}</option>')
    return f'<select name="{name}">{"".join(opts)}</select>'


# --------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"store-editor/{VERSION}"

    def log_message(self, fmt, *args):
        # Logs nach stdout, ohne Kopfzeilen: Die tragen Benutzernamen.
        sys.stdout.write(f"{self.command} {self.path} -> {args[1] if len(args) > 1 else ''}\n")

    # Die Anmeldung kommt vom Gateway und ist nicht fälschbar (der
    # Contract verlangt genau das). Die App macht keine eigene.
    def who(self):
        user = self.headers.get("X-OAAP-User", "")
        return user, ", ".join(sorted(self.role_set()))

    def role_set(self):
        return {r.strip() for r in (self.headers.get("X-OAAP-Roles", "") or "").split(",")
                if r.strip()}

    def send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, target):
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        return parse_qs(raw, keep_blank_values=True)

    # ------------------------------------------------------------------ GET

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        if path == "/mcp":
            # Vor allem anderen: das Gateway hat die Person schon angemeldet.
            return DOOR.serve(self)
        q = parse_qs(parsed.query)
        flash = (q.get("m") or [""])[0]
        user, roles = self.who()
        parts = [p for p in path.split("/") if p]

        if path == "/":
            self.send_html(self.lists_page(user, roles, flash))
        elif path == "/pruefen":
            self.send_html(page("Liste einfügen", PASTE_BODY, user, roles, "paste"))
        elif path == "/hilfe" or (parts[:1] == ["hilfe"] and len(parts) == 2):
            self.help_page(parts[1] if len(parts) == 2 else "", user, roles)
        elif path == "/quellen":
            self.send_html(self.sources_page(user, roles, flash))
        elif parts[:1] == ["liste"] and len(parts) == 2:
            self.send_html(self.list_page(parts[1], user, roles, flash, q))
        elif parts[:1] == ["liste"] and parts[2:3] in (["pruefung"], ["entwurf"]) and len(parts) == 3:
            self.send_html(self.list_page(parts[1], user, roles, flash, q, tab=parts[2]))
        elif parts[:1] == ["liste"] and parts[2:3] == ["aenderungen"]:
            self.send_html(self.changes_page(parts[1], user, roles))
        elif parts[:1] == ["liste"] and parts[2:3] == ["datei"]:
            self.download(parts[1], user, roles)
        elif parts[:1] == ["liste"] and parts[2:3] == ["bericht"]:
            self.report(parts[1], "", user, roles)
        elif (parts[:1] == ["liste"] and parts[2:3] == ["eintrag"]
              and parts[4:5] == ["bericht"]):
            self.report(parts[1], unquote(parts[3]), user, roles)
        elif parts[:1] == ["liste"] and parts[2:3] == ["eintrag"] and len(parts) == 4:
            self.send_html(self.entry_page(parts[1], unquote(parts[3]), user, roles, flash,
                                           remove=(q.get("entfernen") or [""])[0] == "1"))
        else:
            self.send_html(page("Nicht gefunden",
                                '<div class="oaap-card"><p class="err">Diese Seite gibt es '
                                'nicht.</p><p><a class="oaap-back" href="/">← Zu den '
                                'Listen</a></p></div>', user, roles), 404)

    # ----------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/mcp":
            return DOOR.serve(self)
        user, roles = self.who()
        parts = [p for p in path.split("/") if p]

        if path == "/pruefen":
            self.paste_result(user, roles)
            return
        if path == "/quellen/aufnehmen":
            with _LOCK:
                self.add_source(user, roles)
            return
        if path == "/quellen/entfernen":
            with _LOCK:
                self.remove_source(user, roles)
            return
        if parts[:1] != ["liste"] or len(parts) < 3:
            self.send_html(page("Nicht gefunden",
                                '<div class="oaap-card"><p class="err">Diese Seite gibt es '
                                'nicht.</p></div>', user, roles), 404)
            return

        idx, action = parts[1], parts[2]
        src = self.source_at(idx)
        if not src:
            self.send_html(page("Nicht gefunden",
                                '<div class="oaap-card"><p class="err">Diese Liste ist nicht '
                                'eingetragen.</p></div>', user, roles), 404)
            return
        url = src["url"]
        form = self.form()

        # Alles unter diesem Schloss: Zwei Browser auf derselben
        # Arbeitskopie dürfen sich nicht gegenseitig überschreiben.
        with _LOCK:
            if action == "verwerfen":
                drop_work(url)
                self.redirect(f"/liste/{quote(idx)}?m=verworfen")
                return
            if action == "uebernehmen":
                self.regenerate_all(idx, src)
                return
            if action == "abgleich":
                self.sync_entry(idx, src, one(form, "id"), user, roles)
                return
            if action == "neu":
                self.add_entry(idx, src, form, user, roles)
                return
            if action == "eintrag" and len(parts) >= 4:
                self.save_entry(idx, src, unquote(parts[3]), form, user, roles)
                return
        self.send_html(page("Nicht gefunden",
                            '<div class="oaap-card"><p class="err">Unbekannte '
                            'Aktion.</p></div>', user, roles), 404)

    # -------------------------------------------------------------- Helfer

    def source_at(self, idx):
        try:
            return load_sources()[int(idx)]
        except (ValueError, IndexError):
            return None

    def manifest_for(self, src, entry):
        """Das Manifest eines Eintrags — mit dem Schluessel dieser Quelle."""
        return fetch_manifest(entry, token_of(src), ed.source_forge(src["url"]))

    def checked(self, src, doc):
        """Ein Dokument pruefen — mit dem Schluessel dieser Quelle."""
        return ck.check_document(doc, fetch=fetch, load_yaml=yaml.safe_load,
                                 token=token_of(src),
                                 token_forge=ed.source_forge(src["url"]))

    def not_found(self, user, roles, text):
        return page("Nicht gefunden",
                    f'<div class="oaap-card"><p class="err">{esc(text)}</p>'
                    f'<p><a class="oaap-back" href="/">← Zu den Listen</a></p></div>',
                    user, roles, "lists")

    # --------------------------------------------------------------- Seiten

    def lists_page(self, user, roles, flash=""):
        warn = ""
        if not storage_ready():
            warn = ('<div class="oaap-card oaap-danger"><h2>Kein Speicher</h2>'
                    '<p class="err">Diese Instanz hat keinen beschreibbaren '
                    'Speicher. Prüfen geht, Bearbeiten nicht — die Arbeitskopie '
                    'hätte keinen Platz.</p><p class="muted">Ein erneutes Ausrollen '
                    'über das Portal legt den Speicher an.</p></div>')
        sources = load_sources()
        if not sources:
            return page("Listen", f'{af.pagehead("Listen")}{warn}{LISTS_EMPTY}',
                        user, roles, "lists", flash)
        rows = []
        for i, src in enumerate(sources):
            url = src["url"]
            doc, work, err = current(src)
            draft = ('<span class="oaap-badge test">Entwurf</span> ' if work else "")
            if err:
                rows.append(f'<tr><td>{draft}<a href="/liste/{i}">{esc(src.get("name") or url)}</a>'
                            f'<br><span class="muted">{esc(url)}</span></td>'
                            f'<td colspan="2"><span class="err">{esc(err)}</span></td></tr>')
                continue
            rep = self.checked(src, doc)
            name = str((doc or {}).get("name") or src.get("name") or url)
            rows.append(
                f'<tr><td>{draft}<a href="/liste/{i}">'
                f'{esc(name)}</a><br><span class="muted">{esc(url)}</span></td>'
                f'<td>{rep["entries"]} Einträge<br><span class="muted">'
                f'{rep["checked"]} geprüft, {rep["unreachable"]} ungeprüft</span></td>'
                f'<td>{summary_badges(rep)}</td></tr>')
        table = af.table("<th>Liste</th><th>Umfang</th><th>Ergebnis</th>", rows)
        body = f'''{af.pagehead("Listen")}{warn}
{table}
<p class="muted">Ein <span class="oaap-badge test">Entwurf</span> ist eine
   Arbeitskopie auf dieser Instanz. Geprüft wird dann der Entwurf und nicht
   mehr die Veröffentlichung. Veröffentlicht wird von hier aus nichts — das
   Ergebnis ist eine Datei zum Herunterladen.</p>'''
        return page("Listen", body, user, roles, "lists", flash)

    def list_page(self, idx, user, roles, flash="", params=None, tab=""):
        """Die Seite einer Liste: Objektkopf und drei Reiter (RFC-0118 §6.5)
        -- Einträge (Listenbericht), Prüfung (Listenbericht der Befunde),
        Entwurf (Arbeitskopie, Abgleich, Aufnehmen, Bericht, Entfernen)."""
        src = self.source_at(idx)
        if not src:
            return self.not_found(user, roles, "Diese Liste ist nicht eingetragen.")
        url = src["url"]
        base = f"/liste/{esc(idx)}"
        doc, work, err = current(src)
        if err:
            return page(url, f'{af.back("/", "Zu den Listen")}'
                             f'<div class="oaap-card oaap-danger"><p class="err">{esc(err)}</p>'
                             f'<p class="muted">Adresse: <code>{esc(url)}</code></p></div>'
                             f'{self.remove_source_card(idx)}',
                        user, roles, "lists", flash)
        rep = self.checked(src, doc)
        title = str(doc.get("name") or url)
        nfind = len(rep["findings"])
        tabs = af.tabs([(base, "Einträge", len([e for e in doc.get("apps") or [] if isinstance(e, dict)])),
                        (f"{base}/pruefung", "Prüfung", nfind or None),
                        (f"{base}/entwurf", "Entwurf")],
                       f"{base}/{tab}" if tab else base)
        head = (f'{af.back("/", "Zu den Listen")}'
                f'<div class="oaap-objhead">{af.pagehead(title, summary_badges(rep))}'
                f'<p class="muted"><code>{esc(url)}</code>'
                f'{" · " + af.badge("Entwurf", "test") if work else ""}</p></div>{tabs}')
        if tab == "pruefung":
            query = af.Query(params or {}, filters=("art",), default_sort="art")
            body = head + self.findings_report(rep, query, f"{base}/pruefung?")
        elif tab == "entwurf":
            body = head + self.draft_tab(idx, src, doc, work, rep)
        else:
            query = af.Query(params or {}, filters=("pruefung",), default_sort="app")
            body = head + self.entries_report(idx, doc, work, rep, query)
        return page(title, body, user, roles, "lists", flash, obj=f"Liste:{idx}")

    def entries_report(self, idx, doc, work, rep, query):
        per_app = {}
        for f in rep["findings"]:
            per_app.setdefault(f["app"], []).append(f["level"])

        def worst(e):
            lv = per_app.get(str(e.get("id") or ""), [])
            return min([ck.LEVELS.index(l) for l in lv] or [len(ck.LEVELS)])

        def state(e):
            w = worst(e)
            return ck.LEVELS[w] if w < len(ck.LEVELS) else "ok"

        entries = [e for e in doc.get("apps") or [] if isinstance(e, dict)]
        want = query.filters.get("pruefung")
        if want:
            entries = [e for e in entries if state(e) == want]
        cols = [("App", "app", lambda e: str(e.get("name") or e.get("id") or ""), ""),
                ("Kennung", "id", lambda e: str(e.get("id") or ""), "oaap-minor"),
                ("Version", "version", lambda e: str(e.get("version") or ""), ""),
                ("Art", "klasse", lambda e: str(e.get("app_class") or ""), "oaap-minor"),
                ("Reifegrad", "reife", lambda e: str(e.get("maturity") or ""), "oaap-minor"),
                ("Prüfung", "pruefung", worst, "")]

        def row(e):
            app_id = str(e.get("id") or "")
            lv = per_app.get(app_id, [])
            badges = " ".join(
                af.badge(f'{lv.count(l)} {LEVEL_LABEL[l] if lv.count(l) == 1 else LEVEL_PLURAL[l]}',
                         LEVEL_BADGE[l]) for l in ck.LEVELS if lv.count(l)) or af.badge("in Ordnung", "ok")
            marks = overrides_of(work, app_id) if work else set()
            mark = f'<br><span class="muted">{len(marks)} übersteuert</span>' if marks else ""
            return (f'<tr><td><a href="/liste/{esc(idx)}/eintrag/{quote(app_id)}">'
                    f'{esc(e.get("name") or app_id)}</a>{mark}</td>'
                    f'<td class="oaap-minor"><code>{esc(app_id)}</code></td>'
                    f'<td>{esc(e.get("version"))}</td>'
                    f'<td class="oaap-minor">{esc(e.get("app_class") or "—")}</td>'
                    f'<td class="oaap-minor">{esc(e.get("maturity") or "—")}</td>'
                    f'<td>{badges}</td></tr>')
        choices = [("pruefung", "Prüfung", [(ck.FEHLER, "mit Fehler"), (ck.BEFUND, "mit Befund"),
                                             (ck.HINWEIS, "mit Hinweis"), ("ok", "in Ordnung")])]
        return af.list_report(query, entries, cols, row, base=f"/liste/{esc(idx)}?", noun="Einträge",
                              choices=choices, placeholder="Name oder Kennung",
                              empty_text="Diese Liste hat noch keinen Eintrag. Im Reiter "
                                         "„Entwurf“ nimmst Du den ersten auf.")

    def findings_report(self, rep, query, base):
        rows = rep["findings"]
        want = query.filters.get("art")
        if want:
            rows = [f for f in rows if f["level"] == want]
        cols = [("Art", "art", lambda f: ck.LEVELS.index(f["level"]), ""),
                ("App", "app", lambda f: f["app"] or "", ""),
                ("Feld", "feld", lambda f: f["field"] or "", ""),
                ("Befund", "text", lambda f: f["text"] or "", ""),
                ("In der Liste", "", None, "oaap-minor"),
                ("Im Manifest", "", None, "oaap-minor")]

        def row(f):
            return (f'<tr><td>{af.badge(LEVEL_LABEL[f["level"]], LEVEL_BADGE[f["level"]])}</td>'
                    f'<td><code>{esc(f["app"]) or "—"}</code></td>'
                    f'<td><code>{esc(f["field"])}</code></td><td>{esc(f["text"])}</td>'
                    f'<td class="oaap-minor">{esc(f["list"]) or "—"}</td>'
                    f'<td class="oaap-minor">{esc(f["manifest"]) or "—"}</td></tr>')
        choices = [("art", "Art", [(l, LEVEL_PLURAL[l]) for l in ck.LEVELS])]
        legend = "".join(
            f'<p class="muted">{af.badge(LEVEL_LABEL[l], LEVEL_BADGE[l])} {esc(LEVEL_HELP[l])}</p>'
            for l in ck.LEVELS if rep["counts"][l])
        report = af.list_report(query, rows, cols, row, base=base, noun="Befunde", choices=choices,
                                placeholder="App, Feld oder Text",
                                empty_text="Keine Beanstandung. Jeder Eintrag deckt sich mit "
                                           "dem Manifest, auf das er zeigt.")
        facts = af.facts([("Einträge", str(rep["entries"])),
                          ("Gegen das Manifest geprüft", str(rep["checked"])),
                          ("Ungeprüft geblieben", str(rep["unreachable"]))])
        return f'{facts}<br>{report}{legend}'

    def draft_tab(self, idx, src, doc, work, rep):
        changes = ed.diff_documents(work["published"], doc) if work else []
        if work:
            c = ed.count_kinds(changes)
            draft_card = f'''<div class="oaap-card">
  <h2>Entwurf <span class="oaap-badge test">nicht veröffentlicht</span></h2>
  <p class="muted">Begonnen am {esc(work.get("begonnen"))}, zuletzt gespeichert
     {esc(work.get("saved") or "—")}. {len(changes)} Änderung(en) gegenüber dem
     veröffentlichten Stand: {c[ed.STRUKTUR]} strukturell, {c[ed.REDAKTIONELL]}
     redaktionell, {c[ed.ERZEUGT]} aus den Manifesten übernommen.</p>
  <div class="actions">
    <a class="oaap-btn" href="/liste/{esc(idx)}/aenderungen">Änderungen ansehen</a>
    <a class="oaap-btn primary" href="/liste/{esc(idx)}/datei">Als Datei herunterladen</a>
    <form method="post" action="/liste/{esc(idx)}/verwerfen"
          onsubmit="return confirm('Den Entwurf verwerfen? Alle Änderungen daran gehen verloren.')">
      <button class="danger">Entwurf verwerfen</button></form>
  </div>
</div>'''
        else:
            draft_card = ('<div class="oaap-card"><h2>Entwurf</h2><p class="muted">Es gibt noch keinen '
                          'Entwurf. Die erste Änderung an einem Eintrag legt eine '
                          'Arbeitskopie an; der veröffentlichte Stand bleibt daneben '
                          'stehen und dient als Vergleich.</p></div>')

        take = f'''<div class="oaap-card">
  <h2>Mit den Manifesten abgleichen</h2>
  <p class="muted">Holt für jeden Eintrag Name, Verpackungsart, Version, Art der
     App und Rollen aus dem Manifest.
     <strong>Übersteuerte Felder bleiben unberührt</strong>, sonst nähme jede
     Neuerzeugung eine bewusste Entscheidung stillschweigend zurück.
     Für eine einzelne App steht <em>Abgleichen</em> auf ihrer Seite.</p>
  <form method="post" action="/liste/{esc(idx)}/uebernehmen" style="margin:0">
    <button>Alle Einträge abgleichen</button></form>
</div>
<div class="oaap-card">
  <h2>Nachpflege-Bericht</h2>
  <p class="muted">Der umgekehrte Weg: Statt die Liste ans Manifest anzupassen,
     sagt der Bericht, <strong>was dem Manifest fehlt</strong> — als Auftrag zum
     Weiterreichen an die KI, die die App betreut, mit einem einsetzbaren
     YAML-Block. Denn das Manifest gehört dem, der die App gebaut hat; der
     Katalog ist nur ein Verzeichnis.</p>
  <div class="actions">
    <a class="oaap-btn" href="/liste/{esc(idx)}/bericht">Bericht für die ganze Liste</a></div>
</div>'''
        return f"{draft_card}{take}{self.new_entry_card(idx)}{self.remove_source_card(idx)}"

    def remove_source_card(self, idx):
        """Eine Liste aus dem Editor nehmen -- früher eine Zeilenaktion auf
        „Listen und Zugang“, jetzt auf der Seite der Liste (RFC-0118 §7.5)."""
        if not may_configure(self.role_set()):
            return ""
        return f'''<div class="oaap-card oaap-danger">
  <h2>Liste aus dem Editor nehmen</h2>
  <p class="muted">Im Repository ändert das nichts. Ein Entwurf zu dieser Liste
     geht dabei verloren.</p>
  <form method="post" action="/quellen/entfernen"
        onsubmit="return confirm('Diese Liste aus dem Editor nehmen? Ein Entwurf dazu geht verloren.')">
    <input type="hidden" name="i" value="{esc(idx)}">
    <button class="danger">Liste aus dem Editor nehmen</button></form>
</div>'''

    def new_entry_card(self, idx, error="", values=None):
        v = values or {}
        msg = f'<p class="err">{esc(error)}</p>' if error else ""
        return f'''<div class="oaap-card">
  <h2>Eintrag aufnehmen</h2>
  <p class="muted">Nur Kennung und Zeiger auf das Paket — alles Weitere holt
     der Abgleich mit dem Manifest. Ein Eintrag darf entstehen,
     <strong>bevor sein Manifest abrufbar ist</strong>;
     der Prüfer meldet dann bei jedem Lauf, dass er ohne Beleg dasteht.</p>
  {msg}
  <form method="post" action="/liste/{esc(idx)}/neu">
    <div class="grid2">
      <label>Kennung der App
        <input name="id" value="{esc(v.get("id"))}" placeholder="uptime-kuma" required></label>
      <label>Git-Repository
        <input name="git" value="{esc(v.get("git"))}"
               placeholder="https://github.com/…" required></label>
      <label>Pfad im Repository (falls die App in einem Unterordner liegt)
        <input name="path" value="{esc(v.get("path"))}" placeholder="apps/uptime-kuma"></label>
      <label>Fester Stand (Tag oder Zweig, sonst der Hauptzweig)
        <input name="ref" value="{esc(v.get("ref"))}" placeholder="v1.23.0"></label>
    </div>
    <div class="actions"><button class="primary">Aufnehmen</button></div>
  </form>
</div>'''

    def help_page(self, slug, user, roles):
        """Die Hilfe aus docs/ -- dieselben Dateien, die über `docs:` an die
        Hilfe des Mandanten gehen (eine Quelle, RFC-0118 §9)."""
        got = helpdocs.load(slug)
        if not got:
            self.send_html(self.not_found(user, roles, "Diese Hilfeseite gibt es nicht."), 404)
            return
        title, html_ = got
        body = (f'<div class="grid2" style="grid-template-columns:3fr 1fr;align-items:start">'
                f'<article class="oaap-card">{html_}</article>'
                f'<aside class="oaap-card"><h2>Hilfe</h2>{helpdocs.toc(slug)}</aside></div>')
        self.send_html(page(title, body, user, roles, "help"))

    def entry_page(self, idx, app_id, user, roles, flash="", remove=False, error=""):
        src = self.source_at(idx)
        if not src:
            return self.not_found(user, roles, "Diese Liste ist nicht eingetragen.")
        url = src["url"]
        doc, work, err = current(src)
        if err:
            return self.not_found(user, roles, err)
        entry = ed.entry_by_id(doc, app_id)
        if entry is None:
            return self.not_found(user, roles, "Diesen Eintrag gibt es in der "
                                               "Liste nicht.")
        marks = overrides_of(work, app_id) if work else set()
        manifest, murl, mwhy = self.manifest_for(src, entry)
        derived = ck.derive(manifest) if manifest else {}
        back = f'/liste/{esc(idx)}'

        if remove:
            body = f'''{af.back(f"{back}/eintrag/{quote(app_id)}", "Zurück")}
<h1>{esc(entry.get("name") or app_id)} entfernen?</h1>
<div class="oaap-card oaap-danger">
  <p>Der Eintrag <code>{esc(app_id)}</code> verschwindet aus der Arbeitskopie.
     Die veröffentlichte Liste bleibt unberührt — entfernt ist er dort erst,
     wenn die neue Datei veröffentlicht wird.</p>
  <form method="post" action="{back}/eintrag/{quote(app_id)}">
    <input type="hidden" name="tun" value="entfernen">
    <div class="actions"><button class="danger">Ja, entfernen</button>
      <a class="oaap-btn" href="{back}/eintrag/{quote(app_id)}">Abbrechen</a></div>
  </form>
</div>'''
            return page("Entfernen", body, user, roles, "lists")

        # Befunde nur zu diesem Eintrag — der Prüfer bleibt beim
        # Bearbeiten sichtbar, statt auf einer eigenen Seite zu warten.
        findings = ck.check_structure({"store": doc.get("store", "0.2"),
                                       "name": "x", "apps": [entry]})
        if manifest:
            findings += ck.compare_entry(entry, manifest)
        elif mwhy:
            findings.append(ck.finding(ck.HINWEIS, app_id, "package",
                                       f"Das Manifest ist nicht abrufbar: {mwhy} — "
                                       "solange steht die Behauptung dieses Eintrags "
                                       "ohne Beleg da.", murl, ""))
        findings = [f for f in findings if f["app"] in (app_id, "")]
        fbox = ('<div class="oaap-card"><p class="ok">Dieser Eintrag deckt sich mit '
                'seinem Manifest.</p></div>' if not findings else
                f'<div class="oaap-card" style="overflow-x:auto"><h2>Prüfung</h2><table class="oaap-table">'
                f'<tr><th>Art</th><th>Feld</th><th>Befund</th><th>In der Liste</th>'
                f'<th>Im Manifest</th></tr>{findings_rows(findings, with_app=False)}'
                f'</table></div>')

        known_links, rest_links = ed.split_links(entry.get("links"))
        link_fields = "".join(
            f'<label>{esc(label)}<input name="link_{rel}" '
            f'value="{esc(known_links[rel]["url"])}" placeholder="https://…"></label>'
            for rel, label in ed.KNOWN_RELS)
        pkg = entry.get("package") or {}

        gen_rows = []
        for field in ed.REGENERABLE:
            value = entry.get(field)
            shown = ", ".join(value) if isinstance(value, list) else (value or "")
            unlocked = field in marks
            manifest_says = (", ".join(derived.get(field) or [])
                             if field == "roles" else derived.get(field, ""))
            if manifest:
                side = (f'Das Manifest sagt: <code>{esc(manifest_says)}</code>'
                        if manifest_says else
                        'Das Manifest sagt dazu nichts — es ist noch Format 0.1.')
            else:
                side = "Das Manifest war nicht abrufbar."
            mark = (' <span class="oaap-badge test">übersteuert</span>' if unlocked else "")
            checked = " checked" if unlocked else ""
            gen_rows.append(f'''<label>{esc(ed.FIELD_LABEL[field])}{mark}
  <input name="gen_{field}" value="{esc(shown)}"{"" if unlocked else " disabled"}></label>
<div class="lockline">
  <label style="margin:0"><input type="checkbox" name="entriegelt" value="{field}"{checked}> abweichend pflegen</label>
  <span>{side}</span></div>''')

        sync = (f'<form method="post" action="{back}/abgleich" style="display:inline">'
                f'<input type="hidden" name="id" value="{esc(app_id)}">'
                f'<button title="Nur die Felder aus dem Manifest, Texte bleiben">Abgleichen</button></form> '
                f'<a class="oaap-btn" href="{back}/eintrag/{quote(app_id)}/bericht">Nachpflege-Bericht</a>')
        body = f'''{af.back(back, "Zur Liste")}
<div class="oaap-objhead">{af.pagehead(entry.get("name") or app_id, sync)}
  <p class="muted"><code>{esc(app_id)}</code></p></div>
{f'<div class="oaap-card oaap-danger"><p class="err">{esc(error)}</p></div>' if error else ""}
{fbox}
<form method="post" action="{back}/eintrag/{quote(app_id)}">
<div class="oaap-card">
  <h2>Redaktionell — worüber ein Mensch nachdenken muss</h2>
  <label>Ein Satz dazu (erscheint in der Übersicht)
    <input name="summary" value="{esc(entry.get("summary"))}" maxlength="200"></label>
  <label>Beschreibung (länger, darf Markdown sein)
    <textarea name="description">{esc(entry.get("description"))}</textarea></label>
  <p class="hint">Die Beschreibung ist in der Liste absichtlich der lange Text.
     Das Manifest liefert nur einen kurzen Satz als Saatgut — verglichen wird
     sie deshalb nicht.</p>
  <h3>Kategorien</h3>
  {checkboxes("categories", ck.CATEGORIES, ed.CATEGORY_LABEL,
              [str(c) for c in (entry.get("categories") or [])])}
  <h3>Für wen</h3>
  {checkboxes("audience", ck.AUDIENCES, ed.AUDIENCE_LABEL,
              [str(a) for a in (entry.get("audience") or [])])}
  <div class="grid2">
    <label>Reifegrad
      {select("maturity", ["alpha", "beta", "preview", "stable"],
              ed.MATURITY_LABEL, str(entry.get("maturity") or ""))}</label>
    <label>Stand
      {select("status", ["active", "deprecated", "archived"],
              ed.STATUS_LABEL, str(entry.get("status") or ""))}</label>
    <label>Schlagwörter (Komma getrennt, nur für die Suche)
      <input name="tags" value="{esc(", ".join(str(t) for t in (entry.get("tags") or [])))}"></label>
    <label>Lizenz
      <input name="license" value="{esc(entry.get("license"))}" placeholder="MIT"></label>
  </div>
</div>

<div class="oaap-card">
  <h2>Verweise und Bilder</h2>
  <div class="grid2">{link_fields}</div>
  <label>Weitere Verweise — je Zeile <code>Beziehung | Adresse | Beschriftung</code>
    <textarea name="links_rest" class="small">{esc(rest_links)}</textarea></label>
  <label>Bildschirmfotos — je Zeile <code>Pfad | Bildunterschrift</code>
    <textarea name="screenshots" class="small">{esc(ed.format_pairs(entry.get("screenshots"), ("src", "caption")))}</textarea></label>
  <p class="hint">Bildpfade gelten <strong>relativ zur Liste</strong>, nicht als
     Adresse eines fremden Servers: Sonst ruft jeder Knoten, der
     die Store-Seite öffnet, einen Rechner auf, den niemand ausgewählt hat — und
     verrät ihm damit seine Existenz.</p>
</div>

<div class="oaap-card locked">
  <h2>Aus dem Manifest erzeugt</h2>
  <p class="hint">Diese fünf Felder gehören dem Paket, nicht dem Katalog. Wer sie
     abweichend pflegen will, hakt das an — dann bleibt die Abweichung bei der
     nächsten Neuerzeugung stehen, statt stillschweigend zurückgenommen zu
     werden.</p>
  {"".join(gen_rows)}
</div>

<div class="oaap-card">
  <h2>Von Hand gepflegt, obwohl sie dem Paket gehören</h2>
  <p class="hint">Diese Felder gehören eigentlich zum Paket, eine Neuerzeugung
     kann sie heute aber nicht liefern:
     <code>profiles</code> kennt das Manifest-Schema nicht, das Freigabedatum
     wäre das eines Git-Tags, und beim Bild liegt es anders — <code>app.icon</code>
     gibt es sehr wohl, aber im Katalog gilt ein Bildpfad relativ zur Liste und
     im Manifest relativ zum Paket; eine Neuerzeugung müsste die Datei
     kopieren. Sie hier verriegelt darzustellen wäre eine Unwahrheit: Es gäbe
     nichts, woraus sie je erzeugt würden. Der <a href="{back}/eintrag/{quote(app_id)}/bericht">Nachpflege-Bericht</a>
     hält das fest, statt es zu verschweigen.</p>
  <div class="grid2">
    <label>Freigegeben am (JJJJ-MM-TT)
      <input name="released" value="{esc(entry.get("released"))}" placeholder="2026-08-09"></label>
    <label>Gedacht für Knotenprofile (Komma getrennt)
      <input name="profiles" value="{esc(", ".join(str(p) for p in (entry.get("profiles") or [])))}"
             placeholder="dev"></label>
    <label>Bild (Pfad relativ zur Liste)
      <input name="icon" value="{esc(entry.get("icon"))}" placeholder="icons/app.svg"></label>
    <label>Git-Repository des Pakets
      <input name="pkg_git" value="{esc(pkg.get("git"))}"></label>
    <label>Pfad im Repository
      <input name="pkg_path" value="{esc(pkg.get("path"))}"></label>
    <label>Fester Stand (Tag oder Zweig)
      <input name="pkg_ref" value="{esc(pkg.get("ref"))}"></label>
  </div>
  <p class="hint">Ein Eintrag <strong>ohne festen Stand</strong> installiert,
     was der Hauptzweig gerade enthält — nicht eine bestimmte Version.</p>
</div>

<div class="actions">
  <button name="tun" value="speichern" class="primary">Speichern</button>
  <button name="tun" value="uebernehmen">Speichern und mit dem Manifest abgleichen</button>
  <a class="oaap-btn" href="{back}">Abbrechen</a>
  <a class="oaap-btn danger" href="{back}/eintrag/{quote(app_id)}?entfernen=1"
     style="margin-left:auto">Eintrag entfernen</a>
</div>
</form>'''
        return page(entry.get("name") or app_id, body, user, roles, "lists", flash,
                    obj=f"Eintrag:{app_id}")

    def changes_page(self, idx, user, roles):
        src = self.source_at(idx)
        if not src:
            return self.not_found(user, roles, "Diese Liste ist nicht eingetragen.")
        url = src["url"]
        work = load_work(url)
        if not work:
            return self.not_found(user, roles, "Zu dieser Liste gibt es keinen Entwurf.")
        changes = ed.diff_documents(work["published"], work["doc"])
        c = ed.count_kinds(changes)
        rows = "".join(
            f'<tr><td><span class="oaap-badge {KIND_BADGE[ch["kind"]]}">'
            f'{esc(KIND_LABEL[ch["kind"]])}</span></td>'
            f'<td><code>{esc(ch["app"])}</code></td>'
            f'<td>{esc(ed.FIELD_LABEL.get(ch["field"], ch["field"]))}</td>'
            f'<td class="muted">{esc(ch["before"]) or "—"}</td>'
            f'<td>{esc(ch["after"]) or "—"}</td></tr>' for ch in changes)
        table = (f'<div class="oaap-card" style="overflow-x:auto"><table class="oaap-table">'
                 f'<tr><th>Art</th><th>App</th><th>Feld</th><th>Vorher</th>'
                 f'<th>Nachher</th></tr>{rows}</table></div>' if changes else
                 '<div class="oaap-card"><p class="muted">Der Entwurf ist mit dem '
                 'veröffentlichten Stand identisch.</p></div>')
        body = f'''{af.back(f"/liste/{esc(idx)}", "Zur Liste")}
<h1>Was sich ändern würde</h1>
<div class="oaap-card">
  <p>Gegenüber dem Stand, der beim Beginn des Entwurfs veröffentlicht war
     ({esc(work.get("begonnen"))}).</p>
  <dl class="oaap-kv">
    <dt>Strukturell</dt><dd>{c[ed.STRUKTUR]} — Einträge aufgenommen, entfernt,
       oder ein Paket, das umzieht</dd>
    <dt>Redaktionell</dt><dd>{c[ed.REDAKTIONELL]} — Texte, Einordnung, Verweise</dd>
    <dt>Aus den Manifesten</dt><dd>{c[ed.ERZEUGT]} — vom Prüfer erzeugt, nicht
       von Hand geschrieben</dd>
  </dl>
  <p class="muted">Die Trennung ist keine Kosmetik: Beim späteren
     Zurückschreiben zählt die Mengenbremse <strong>nur</strong> die ersten beiden. Was der Prüfer selbst
     aus den Manifesten gebildet hat, ist maschinell und nachvollziehbar
     entstanden — zählte es mit, käme die Rückfrage bei jedem Lauf und würde
     weggeklickt.</p>
</div>
{table}
<div class="actions"><a class="oaap-btn primary" href="/liste/{esc(idx)}/datei">Als Datei
   herunterladen</a></div>'''
        return page("Änderungen", body, user, roles, "lists")

    def download(self, idx, user, roles):
        src = self.source_at(idx)
        if not src:
            self.send_html(self.not_found(user, roles, "Diese Liste ist nicht "
                                                       "eingetragen."), 404)
            return
        url = src["url"]
        doc, work, err = current(src)
        if err:
            self.send_html(self.not_found(user, roles, err), 404)
            return
        # Der Prüfer ist der Wächter, nicht der Mensch (RFC-0013 §3). Was
        # strukturell kaputt ist, verlässt dieses Werkzeug nicht als
        # Datei. Befunde und Hinweise halten nicht auf: Ein Eintrag darf
        # vor seinem Manifest entstehen (Frage 4), und der Prüfer sagt
        # bei jedem Lauf, dass er ohne Beleg dasteht.
        broken = [f for f in ck.check_structure(doc) if f["level"] == ck.FEHLER]
        if broken:
            body = (f'{af.back(f"/liste/{esc(idx)}", "Zur Liste")}'
                    f'<h1>Diese Liste wäre nicht benutzbar</h1>'
                    f'<div class="oaap-card oaap-danger"><p>Ein Knoten würde daran scheitern '
                    f'oder etwas Falsches tun. Der Prüfer ist der Wächter — deshalb '
                    f'gibt es die Datei erst, wenn das behoben ist.</p></div>'
                    f'<div class="oaap-card" style="overflow-x:auto"><table class="oaap-table">'
                    f'<tr><th>Art</th><th>App</th><th>Feld</th><th>Befund</th>'
                    f'<th>In der Liste</th><th>Im Manifest</th></tr>'
                    f'{findings_rows(broken)}</table></div>')
            self.send_html(page("Nicht herunterladbar", body, user, roles, "lists"))
            return
        data = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        name = str(doc.get("id") or doc.get("name") or "oaap-store").lower()
        name = "".join(c if c.isalnum() or c in "-." else "-" for c in name)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}.json"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -------------------------------------------------------------- Aktionen

    def ensure_work(self, src):
        """Arbeitskopie holen oder anlegen. ('' oder Fehlertext)"""
        url = src["url"]
        work = load_work(url)
        if work:
            return work, ""
        published, err = fetch_published(url, token_of(src))
        if err:
            return None, ("Der veröffentlichte Stand ist nicht abrufbar, also "
                          f"gibt es keinen Vergleich: {err}")
        return start_work(url, published), ""

    def save_entry(self, idx, src, app_id, form, user, roles):
        url = src["url"]
        work, err = self.ensure_work(src)
        if err:
            self.send_html(self.not_found(user, roles, err))
            return
        entry = ed.entry_by_id(work["doc"], app_id)
        if entry is None:
            self.send_html(self.not_found(user, roles, "Diesen Eintrag gibt es "
                                                       "in der Liste nicht."), 404)
            return
        action = one(form, "tun")

        if action == "entfernen":
            work["doc"]["apps"] = [e for e in work["doc"]["apps"]
                                   if str(e.get("id") or "") != app_id]
            set_overrides(work, app_id, set())
            save_work(url, work)
            self.redirect(f"/liste/{quote(idx)}?m=entfernt")
            return

        # Redaktionelles: leer heißt weglassen, nicht „leer behaupten".
        values = {
            "summary": one(form, "summary"),
            "description": one(form, "description"),
            "categories": form.get("categories") or [],
            "audience": form.get("audience") or [],
            "tags": ed.parse_words(one(form, "tags")),
            "maturity": one(form, "maturity"),
            "status": one(form, "status"),
            "license": one(form, "license"),
            "links": ed.merge_links({rel: one(form, f"link_{rel}")
                                     for rel, _ in ed.KNOWN_RELS},
                                    one(form, "links_rest")),
            "screenshots": ed.parse_pairs(one(form, "screenshots"), ("src", "caption")),
            "released": one(form, "released"),
            "profiles": ed.parse_words(one(form, "profiles")),
            "icon": one(form, "icon"),
        }
        pkg = {}
        for key, field in (("git", "pkg_git"), ("path", "pkg_path"), ("ref", "pkg_ref")):
            if one(form, field):
                pkg[key] = one(form, field)
        values["package"] = pkg
        ed.apply_values(entry, values)

        # Entriegelte Felder: übernehmen und die Übersteuerung markieren,
        # sofern sie wirklich vom Manifest abweicht.
        unlocked = set(form.get("entriegelt") or [])
        marks = overrides_of(work, app_id)
        manifest, _, _ = self.manifest_for(src, entry)
        derived = ck.derive(manifest) if manifest else {}
        for field in ed.REGENERABLE:
            if field not in unlocked:
                marks.discard(field)
                continue
            raw = one(form, f"gen_{field}")
            new = ed.parse_words(raw) if field == "roles" else raw
            before = entry.get(field)
            ed.apply_values(entry, {field: new})
            want = derived.get(field)
            if want:
                same = (sorted(new) == sorted(want) if field == "roles" else new == want)
                marks.discard(field) if same else marks.add(field)
            elif new != before:
                # Ohne Manifest gibt es nichts zu vergleichen — dann
                # schützt die Markierung die eingetragene Abweichung.
                marks.add(field)
        set_overrides(work, app_id, marks)

        if action == "uebernehmen":
            if not manifest:
                save_work(url, work)
                self.send_html(self.entry_page(
                    idx, app_id, user, roles,
                    error="Das Manifest ist nicht abrufbar — es gibt nichts zu "
                          "übernehmen. Gespeichert ist trotzdem."))
                return
            ed.regenerate_entry(entry, manifest, marks)
        save_work(url, work)
        self.redirect(f"/liste/{quote(idx)}/eintrag/{quote(app_id)}"
                      f"?m={'uebernommen' if action == 'uebernehmen' else 'gespeichert'}")

    def sync_entry(self, idx, src, app_id, user, roles):
        """Eine einzelne App gegen ihr Manifest abgleichen.

        Bewusst ein eigener Weg und nicht der Speichern-Knopf des
        Formulars: Dieser hier fasst **nur** die erzeugten Felder an.
        Über das Formular liefe ein Abgleich als vollständiges
        Absenden — ein leeres Feld hieße dort „weglassen", und ein
        Abgleich von der Listenseite aus würde die redaktionellen Texte
        mitnehmen.
        """
        url = src["url"]
        work, err = self.ensure_work(src)
        if err:
            self.send_html(self.not_found(user, roles, err))
            return
        entry = ed.entry_by_id(work["doc"], app_id)
        if entry is None:
            self.send_html(self.not_found(user, roles, "Diesen Eintrag gibt es "
                                                       "in der Liste nicht."), 404)
            return
        manifest, _, _ = self.manifest_for(src, entry)
        if not manifest:
            self.redirect(f"/liste/{quote(idx)}/eintrag/{quote(app_id)}"
                          f"?m=kein_manifest")
            return
        changes = ed.regenerate_entry(entry, manifest, overrides_of(work, app_id))
        touched = sum(1 for c in changes if not c["held"])
        # Ein Abgleich, der nichts findet, darf keinen Entwurf anlegen:
        # Sonst steht nach einem Blick „wie ist der Stand?" ein Entwurf
        # mit null Änderungen da, und das Wort verliert seine Bedeutung.
        if touched or load_work(url):
            save_work(url, work)
        self.redirect(f"/liste/{quote(idx)}/eintrag/{quote(app_id)}"
                      f"?m={'uebernommen' if touched else 'nichts'}")

    def sources_page(self, user, roles, flash="", error="", values=None):
        """Welche Listen dieser Editor pflegt — und welcher Platz gilt."""
        darf = may_configure(self.role_set())
        v = values or {}
        rows = []
        for i, src in enumerate(load_sources()):
            slot = int(src.get("token") or 0)
            if not slot:
                zugang = '<span class="muted">öffentlich</span>'
            elif token_of(src):
                zugang = (f'<span class="oaap-badge ok">Platz {slot}</span><br>'
                          f'<span class="muted">Schlüssel ist hinterlegt</span>')
            else:
                zugang = (f'<span class="oaap-badge err">Platz {slot} leer</span><br>'
                          f'<span class="muted">im Portal eintragen</span>')
            rows.append(
                f'<tr><td><a href="/liste/{i}">'
                f'{esc(src.get("name") or src["url"])}</a><br>'
                f'<span class="muted">{esc(src["url"])}</span></td>'
                f'<td>{zugang}</td></tr>')

        slots = "".join(
            f'<option value="{n}"{" selected" if str(v.get("token")) == str(n) else ""}>'
            f'Platz {n} — {"Schlüssel hinterlegt" if TOKENS[n - 1] else "noch leer"}'
            f'</option>' for n in range(1, len(TOKENS) + 1))
        form = "" if not darf else f'''<div class="oaap-card">
  <h2>Liste aufnehmen</h2>
  {f'<p class="err">{esc(error)}</p>' if error else ""}
  <form method="post" action="/quellen/aufnehmen">
    <label>Adresse der Liste
      <input name="url" value="{esc(v.get("url"))}" required
             placeholder="https://raw.githubusercontent.com/…/oaap-store.json"></label>
    <p class="hint">Eine Adresse aus der Adresszeile des Browsers
       (<code>…/blob/main/…</code>) wird beim Aufnehmen umgeschrieben — sonst
       käme eine HTML-Seite statt der Datei an.</p>
    <label>Name (optional, sonst nimmt der Editor den aus der Liste)
      <input name="name" value="{esc(v.get("name"))}"></label>
    <label>Zugangsdaten
      <select name="token">
        <option value="0">keine — das Repository ist öffentlich</option>
        {slots}
      </select></label>
    <div class="actions"><button class="primary">Aufnehmen</button></div>
  </form>
</div>'''

        hinweis = "" if darf else (
            '<div class="oaap-card"><p class="muted">Listen aufzunehmen oder zu '
            'entfernen ist Einrichtung und braucht die Rolle <code>keyuser</code> '
            'oder <code>admin</code>. Bearbeiten darf jeder, der hierher '
            'kommt.</p></div>')

        table = af.table("<th>Liste</th><th>Zugang</th>", rows,
                         "Noch keine Liste eingetragen. Unten nimmst Du die erste auf.")
        body = f'''{af.pagehead("Listen und Zugangsdaten")}
{table}
<p class="muted">Entfernen steht auf der Seite einer Liste, im Reiter „Entwurf“.</p>
{form}{hinweis}
<div class="oaap-card">
  <h2>Wo die Schlüssel liegen — und warum nicht hier</h2>
  <p>Ein privates Repository verlangt einen Zugangsschlüssel schon zum
     <strong>Lesen</strong>. Der Editor kennt drei <strong>Plätze</strong>; die
     Schlüssel selbst trägt ein <code>server_admin</code> im Portal ein, in der
     Konfiguration dieser Instanz, als <code>STORE_EDITOR_TOKEN_1</code> bis
     <code>_3</code>. Sie sind dort <em>geheim</em>: eintragbar, nie
     zurücklesbar — auch nicht von dieser Seite.</p>
  <p class="muted">Diese App legt bewusst <strong>keine eigene</strong> Ablage
     für Geheimnisse an. Sie würde damit nachbauen, was die Plattform schon hat,
     und zwar schwächer geschützt. Die feste Zahl an Plätzen ist der Preis
     dafür — sichtbar statt versteckt: Braucht es einen vierten, ist genau das
     der Beleg, dass die Plattform eine allgemeine Lösung bekommen sollte.</p>
  <p class="muted">Ein Schlüssel geht <strong>nur an den Anbieter, für den er
     eingetragen ist</strong>. Eine Liste kann nicht dadurch, dass sie auf ein
     fremdes Repository zeigt, dorthin ein Token schicken lassen.</p>
</div>'''
        return page("Listen und Zugangsdaten", body, user, roles, "sources", flash)

    def add_source(self, user, roles):
        form = self.form()
        url = ed.normalise_source(one(form, "url"))
        values = {"url": one(form, "url"), "name": one(form, "name"),
                  "token": one(form, "token")}
        if not may_configure(self.role_set()):
            self.send_html(self.sources_page(
                user, roles, error="Dafür fehlt die Rolle keyuser oder admin."),
                403)
            return
        sources = load_sources()
        problem = ed.check_new_source(sources, url)
        try:
            slot = int(one(form, "token") or 0)
        except ValueError:
            slot = 0
        if not problem and not 0 <= slot <= len(TOKENS):
            problem = "Diesen Zugangsdaten-Platz gibt es nicht."
        if problem:
            self.send_html(self.sources_page(user, roles, error=problem,
                                             values=values))
            return
        sources.append({"url": url, "name": one(form, "name"), "token": slot})
        save_sources(sources)
        # Sofort nachsehen, ob sie überhaupt erreichbar ist — eine Liste,
        # die man einträgt und erst beim nächsten Klick als unerreichbar
        # erlebt, lässt einen im Unklaren, ob die Adresse oder der
        # Schlüssel schuld ist.
        doc, err = fetch_published(url, token_of(sources[-1]))
        self.redirect("/quellen?m=" + ("quelle_aufgenommen" if not err
                                       else "quelle_stumm"))

    def remove_source(self, user, roles):
        if not may_configure(self.role_set()):
            self.send_html(self.sources_page(
                user, roles, error="Dafür fehlt die Rolle keyuser oder admin."),
                403)
            return
        form = self.form()
        sources = load_sources()
        try:
            i = int(one(form, "i"))
            gone = sources.pop(i)
        except (ValueError, IndexError):
            self.redirect("/quellen")
            return
        save_sources(sources)
        drop_work(gone["url"])
        self.redirect("/quellen?m=quelle_entfernt")

    def report(self, idx, app_id, user, roles):
        """Der Nachpflege-Bericht als Datei — Auftrag an die KI der App."""
        src = self.source_at(idx)
        if not src:
            self.send_html(self.not_found(user, roles, "Diese Liste ist nicht "
                                                       "eingetragen."), 404)
            return
        url = src["url"]
        doc, work, err = current(src)
        if err:
            self.send_html(self.not_found(user, roles, err), 404)
            return
        stand = time.strftime("%Y-%m-%d")
        list_name = str(doc.get("name") or url)
        entries = [e for e in (doc.get("apps") or []) if isinstance(e, dict)]
        if app_id:
            entries = [e for e in entries if str(e.get("id") or "") == app_id]
            if not entries:
                self.send_html(self.not_found(user, roles, "Diesen Eintrag gibt "
                                                           "es in der Liste "
                                                           "nicht."), 404)
                return
        reports = []
        for e in entries:
            manifest, murl, why = self.manifest_for(src, e)
            reports.append((str(e.get("id") or "?"), ed.pflegebericht(
                e, manifest, murl, why, list_name,
                overrides_of(work, str(e.get("id") or "")) if work else (),
                stand)))
        if app_id:
            text = reports[0][1]
            name = f"nachpflege-{app_id}.md"
        else:
            text = ed.sammelbericht(reports, list_name, stand)
            base = str(doc.get("id") or "liste").lower()
            name = "nachpflege-" + "".join(
                c if c.isalnum() or c in "-." else "-" for c in base) + ".md"
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/markdown; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def regenerate_all(self, idx, src):
        url = src["url"]
        work, err = self.ensure_work(src)
        if err:
            self.redirect(f"/liste/{quote(idx)}")
            return
        touched = 0
        for entry in work["doc"].get("apps") or []:
            if not isinstance(entry, dict):
                continue
            manifest, _, _ = self.manifest_for(src, entry)
            if not manifest:
                continue
            marks = overrides_of(work, str(entry.get("id") or ""))
            changes = ed.regenerate_entry(entry, manifest, marks)
            touched += sum(1 for c in changes if not c["held"])
        # Siehe sync_entry: ein Abgleich ohne Fund legt keinen Entwurf an.
        if touched or load_work(url):
            save_work(url, work)
        self.redirect(f"/liste/{quote(idx)}?m={'uebernommen' if touched else 'nichts'}")

    def add_entry(self, idx, src, form, user, roles):
        url = src["url"]
        work, err = self.ensure_work(src)
        if err:
            self.send_html(self.not_found(user, roles, err))
            return
        app_id = one(form, "id").lower()
        git = one(form, "git")
        values = {"id": app_id, "git": git, "path": one(form, "path"),
                  "ref": one(form, "ref")}
        problem = ed.check_new_id(work["doc"], app_id)
        if not problem and not git.startswith("https://"):
            problem = "Das Paket muss über https:// erreichbar sein."
        if problem:
            body = (f'{af.back(f"/liste/{esc(idx)}", "Zur Liste")}'
                    f'<h1>Eintrag aufnehmen</h1>'
                    f'{self.new_entry_card(idx, problem, values)}')
            self.send_html(page("Eintrag aufnehmen", body, user, roles, "lists"))
            return
        work["doc"].setdefault("apps", []).append(
            ed.new_entry(app_id, git, values["path"], values["ref"]))
        save_work(url, work)
        self.redirect(f"/liste/{quote(idx)}/eintrag/{quote(app_id)}?m=aufgenommen")

    def paste_result(self, user, roles):
        form = self.form()
        text = one(form, "doc")
        try:
            doc = json.loads(text)
        except ValueError as e:
            self.send_html(page("Liste einfügen",
                                f'<div class="oaap-card oaap-danger">'
                                f'<p class="err">Das ist keine gültige JSON-Datei: '
                                f'{esc(e)}</p></div>{PASTE_BODY}', user, roles, "paste"))
            return
        # Eine eingefügte Liste hat keine Quelle und damit keinen Schlüssel.
        rep = self.checked({"url": "", "token": 0}, doc)
        title = str((doc or {}).get("name") or "Eingefügte Liste")
        body = (f'{af.back("/pruefen", "Zurück")}'
                f'{af.pagehead(title, summary_badges(rep))}'
                f'<div class="oaap-card"><dl class="oaap-kv">'
                f'<dt>Einträge</dt><dd>{rep["entries"]}</dd>'
                f'<dt>Gegen das Manifest geprüft</dt><dd>{rep["checked"]}</dd>'
                f'<dt>Ungeprüft geblieben</dt><dd>{rep["unreachable"]}</dd>'
                f'</dl><p class="muted">Eine eingefügte Liste wird nur geprüft '
                f'und nirgends gespeichert. Bearbeiten geht bei den Listen, die '
                f'in der Konfiguration dieser Instanz stehen.</p></div>'
                f'{findings_table(rep)}')
        self.send_html(page(title, body, user, roles, "paste"))


def one(form, key, default=""):
    return (form.get(key) or [default])[0].strip()


LISTS_EMPTY = '''<div class="oaap-card">
  <h2>Noch keine Liste eingetragen</h2>
  <p class="muted">Unter <a href="/quellen">Listen und Zugang</a> nimmst Du
     eine auf — auch eine aus einem privaten Repository. Die Voreinstellung
     kommt aus der Instanz-Konfiguration (<code>STORE_EDITOR_LISTS</code>).</p>
  <p class="muted">Eine Liste, die noch nirgends veröffentlicht ist, lässt
     sich unter <a href="/pruefen">Liste einfügen</a> trotzdem prüfen.</p>
</div>'''

PASTE_BODY = '''<div class="oaap-pagehead"><h1>Liste einfügen</h1></div>
<div class="oaap-card">
  <p class="muted">Für eine Liste, die noch nicht veröffentlicht ist —
     etwa eine, die gerade entsteht. Der Inhalt wird geprüft und nirgends
     gespeichert. Die Manifeste holt der Prüfer aus den Repositories, auf
     die die Einträge zeigen.</p>
  <form method="post" action="/pruefen">
    <label>Inhalt der Liste (JSON)
      <textarea name="doc" rows="14" required
                placeholder='{"store": "0.2", "name": "…", "apps": [ … ]}'></textarea></label>
    <div class="actions"><button class="primary">Prüfen</button></div>
  </form>
</div>'''

# ------------------------------------------------------------- MCP-Tür
#
# RFC-0118 §9: die Assistenten des Mandanten lesen, was die Seite zeigt --
# Listen, Einträge, Befunde. Dieselben Rollen wie die Seite (Route / im
# Manifest: user, keyuser, admin). Nie ein Schlüssel: Die Quelle nennt nur
# ihren Platz, nicht seinen Inhalt. Kein Schreibwerkzeug in dieser Stufe.

PAGE_ROLES = {"user", "keyuser", "admin"}

DOOR = mcpdoor.Door(
    "oaap-store-editor", VERSION,
    "Store-Listen dieses Editors lesen: welche Listen es gibt, welche Apps sie "
    "führen und was der Prüfer gegen ihre Manifeste findet. Nur lesend.")


def _may_read(who):
    if not PAGE_ROLES & set(who.get("roles") or []):
        raise mcpdoor.Refused("Der Store-Editor ist nur für user, keyuser und admin.")


def _check(src, doc):
    return ck.check_document(doc, fetch=fetch, load_yaml=yaml.safe_load,
                             token=token_of(src), token_forge=ed.source_forge(src["url"]))


def _source(args):
    raw = str(args.get("liste", "")).strip()
    sources = load_sources()
    if raw.isdigit() and int(raw) < len(sources):
        return raw, sources[int(raw)]
    for i, src in enumerate(sources):
        if raw and raw in (src["url"], src.get("name") or ""):
            return str(i), src
    raise mcpdoor.Refused("Diese Liste ist nicht eingetragen. store_editor_lists nennt die Listen.")


@DOOR.tool("store_editor_lists", "Listen zeigen",
           "Nennt die Store-Listen, die dieser Editor pflegt: Nummer, Name, Adresse, "
           "ob ein Entwurf besteht und ob ein Zugangsplatz gilt.")
def _mcp_lists(args, who):
    _may_read(who)
    out = []
    for i, src in enumerate(load_sources()):
        work = load_work(src["url"])
        slot = int(src.get("token") or 0)
        out.append({"liste": str(i), "name": src.get("name") or "", "url": src["url"],
                    "entwurf": bool(work),
                    "zugang": "öffentlich" if not slot else
                              (f"Platz {slot}" + ("" if token_of(src) else " (leer)"))})
    return {"listen": out}


@DOOR.tool("store_editor_entries", "Einträge einer Liste",
           "Nennt die Apps einer Liste mit Version, Art, Reifegrad und der Zahl der "
           "Befunde des Prüfers je App.",
           {"liste": {"type": "string", "description": "Nummer, Name oder Adresse der Liste"},
            "q": {"type": "string", "description": "Suchwort in Name oder Kennung (optional)"}},
           required=("liste",))
def _mcp_entries(args, who):
    _may_read(who)
    idx, src = _source(args)
    doc, work, err = current(src)
    if err:
        return {"liste": idx, "fehler": err}
    rep = _check(src, doc)
    per = {}
    for f in rep["findings"]:
        per.setdefault(f["app"], {}).setdefault(LEVEL_PLURAL[f["level"]], 0)
        per[f["app"]][LEVEL_PLURAL[f["level"]]] += 1
    q = str(args.get("q") or "").lower()
    apps = []
    for e in doc.get("apps") or []:
        if not isinstance(e, dict):
            continue
        app_id = str(e.get("id") or "")
        if q and q not in f"{app_id} {e.get('name') or ''}".lower():
            continue
        apps.append({"id": app_id, "name": e.get("name") or app_id, "version": e.get("version"),
                     "art": e.get("app_class"), "reifegrad": e.get("maturity"),
                     "befunde": per.get(app_id, {})})
    return {"liste": idx, "name": doc.get("name") or src["url"], "entwurf": bool(work),
            "eintraege": apps}


@DOOR.tool("store_editor_findings", "Befunde lesen",
           "Liest die Befunde des Prüfers zu einer Liste oder zu einer App darin: Art "
           "(Fehler, Befund, Hinweis), Feld, Text, Wert in der Liste und im Manifest.",
           {"liste": {"type": "string", "description": "Nummer, Name oder Adresse der Liste"},
            "app": {"type": "string", "description": "Kennung einer App (optional)"}},
           required=("liste",))
def _mcp_findings(args, who):
    _may_read(who)
    idx, src = _source(args)
    doc, _work, err = current(src)
    if err:
        return {"liste": idx, "fehler": err}
    rep = _check(src, doc)
    app_id = str(args.get("app") or "").strip()
    rows = [f for f in rep["findings"] if not app_id or f["app"] in (app_id, "")]
    return {"liste": idx, "eintraege": rep["entries"], "geprueft": rep["checked"],
            "ungeprueft": rep["unreachable"],
            "befunde": [{"art": LEVEL_LABEL[f["level"]], "app": f["app"], "feld": f["field"],
                         "text": f["text"], "liste": f["list"], "manifest": f["manifest"]}
                        for f in rows]}


DOOR.prompt("store_editor_review", "Liste prüfen",
            "Fasst zusammen, was der Prüfer an einer Store-Liste beanstandet.",
            "Prüfe die Store-Liste {liste} im Store-Editor: Lies ihre Befunde "
            "(store_editor_findings) und fasse zusammen, was ein Knoten daran scheitern "
            "ließe (Fehler), wo Liste und Manifest sich widersprechen (Befunde) und was "
            "nur auffällt (Hinweise). Schlage vor, was zuerst zu tun ist.", ("liste",))
DOOR.prompt("store_editor_entry", "Eintrag erklären",
            "Erklärt den Stand einer App in einer Store-Liste.",
            "Erkläre den Eintrag {app} in der Store-Liste {liste}: Lies die Einträge "
            "(store_editor_entries) und die Befunde zu dieser App (store_editor_findings) "
            "und sage in einfachen Worten, ob er sich mit seinem Manifest deckt und was "
            "nachzupflegen wäre.", ("liste", "app"))


def main():
    print(f"Store-Editor {VERSION} auf Port {PORT}; "
          f"{len(load_sources())} Liste(n); {sum(1 for t in TOKENS if t)} von "
          f"{len(TOKENS)} Zugangsdaten-Plätzen belegt; Speicher {DATA_DIR} "
          f"{'beschreibbar' if storage_ready() else 'NICHT beschreibbar'}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
