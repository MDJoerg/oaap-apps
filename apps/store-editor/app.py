#!/usr/bin/env python3
"""OAAP Store Editor 0.2 — Bauschritt 2 aus RFC-0013: bearbeiten, als Datei.

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

Optik nach oaap-design/docs/design-guidelines.md v0.1 — Rahmen und
Stil sind bewusst aus dem Studio übernommen, damit beide wie ein
Produkt aussehen.
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

import checker as ck
import editor as ed

VERSION = "0.3.0"
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
LEVEL_BADGE = {ck.FEHLER: "err", ck.BEFUND: "test", ck.HINWEIS: "off"}
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
KIND_BADGE = {ed.STRUKTUR: "err", ed.REDAKTIONELL: "test", ed.ERZEUGT: "off"}

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
                          "Lauf — so ist es entschieden (RFC-0013, Frage 4)."),
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
  h3{font-size:.9rem;margin:1.4rem 0 .2rem;color:var(--oaap-blue-900)}
  .pagehead{display:flex;align-items:center;justify-content:space-between;gap:1rem;
       flex-wrap:wrap;margin-bottom:1rem}
  .pagehead h1{margin:0}
  .back{display:inline-block;margin-bottom:.8rem;color:var(--oaap-blue-600);text-decoration:none}
  .back:hover{text-decoration:underline}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.danger{border-color:#fecaca}
  .card.locked{background:#fbfcfe}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.test{background:#fef3c7;color:#92400e}
  .badge.off{background:#f3f4f6;color:#6b7280}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px;font-family:inherit}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  a.btn.ghost,button.ghost{background:transparent;color:var(--oaap-blue-600);
       border:1px solid var(--oaap-blue-600)}
  a.btn.ghost:hover,button.ghost:hover{background:var(--oaap-blue-100)}
  button.danger{background:var(--err)} button.danger:hover{background:#991b1b}
  button.linkish{background:none;color:var(--oaap-blue-600);padding:0;
       min-height:auto;font-size:.95rem;text-align:left}
  button.linkish:hover{background:none;text-decoration:underline}
  label{display:block;font-size:.85rem;color:var(--oaap-muted);margin-top:.9rem}
  input,select,textarea{width:100%;padding:.55rem;margin:.25rem 0 .2rem;
       border:1px solid var(--oaap-border);border-radius:.4rem;font-size:.95rem;
       font-family:inherit}
  input:disabled,textarea:disabled{background:#f3f4f6;color:#4b5563}
  textarea{min-height:5.5rem;resize:vertical}
  textarea.small{min-height:3.5rem}
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
  .checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(13rem,1fr));
       gap:.1rem .8rem;margin:.4rem 0 .2rem}
  .checks label{display:flex;align-items:center;gap:.5rem;margin:0;padding:.3rem 0;
       font-size:.92rem;color:var(--oaap-text);cursor:pointer}
  .checks input{width:auto;margin:0}
  .lockline{display:flex;align-items:center;gap:.5rem;font-size:.8rem;
       color:var(--oaap-muted);margin:.1rem 0 .6rem}
  .lockline input{width:auto;margin:0}
  dl.facts{margin:0;display:grid;grid-template-columns:11rem 1fr;gap:.45rem 1rem}
  dl.facts dt{color:var(--oaap-muted);font-size:.85rem}
  dl.facts dd{margin:0;word-break:break-word}
  .flash{border-left:4px solid var(--ok);background:#f0fdf4;padding:.7rem 1rem;
       border-radius:.3rem;margin-bottom:1.1rem;font-size:.92rem}
  .flash.muted{border-left-color:var(--oaap-muted);background:#f9fafb}
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


def page(title, body, user, roles, active="", flash=""):
    """Gemeinsamer Rahmen: Kopf mit Marke, Navigation, Benutzer, Fuß."""
    note = ""
    if flash in MESSAGES:
        cls, text = MESSAGES[flash]
        note = f'<div class="flash {"muted" if cls == "muted" else ""}">{esc(text)}</div>'
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — OAAP Store Editor</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="/">{LOGO_SVG}
    <span><b>OAAP STORE EDITOR</b><small>App-Listen prüfen und pflegen</small></span>
  </a>
  <nav class="main">
    <a href="/" class="{'active' if active == 'lists' else ''}">Listen</a>
    <a href="/quellen" class="{'active' if active == 'sources' else ''}">Listen und Zugang</a>
    <a href="/pruefen" class="{'active' if active == 'paste' else ''}">Liste einfügen</a>
    <a href="/hilfe" class="{'active' if active == 'help' else ''}">Hilfe</a>
  </nav>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{note}{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP Store Editor {VERSION} — Bauschritt 2: bearbeiten. Geschrieben wird nur
  in die Arbeitskopie dieser Instanz, nicht in ein Repository.
</footer>
</html>"""


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
            out.append(f'<span class="badge {LEVEL_BADGE[lvl]}">{n} {esc(word)}</span>')
    if not out:
        out.append('<span class="badge ok">ohne Beanstandung</span>')
    return " ".join(out)


def findings_rows(findings, with_app=True):
    rows = []
    for f in sorted(findings, key=lambda x: (ck.LEVELS.index(x["level"]), x["app"])):
        app_cell = f'<td><code>{esc(f["app"]) or "—"}</code></td>' if with_app else ""
        rows.append(
            f'<tr><td><span class="badge {LEVEL_BADGE[f["level"]]}">'
            f'{esc(LEVEL_LABEL[f["level"]])}</span></td>{app_cell}'
            f'<td><code>{esc(f["field"])}</code></td>'
            f'<td>{esc(f["text"])}</td>'
            f'<td>{esc(f["list"]) or "—"}</td>'
            f'<td>{esc(f["manifest"]) or "—"}</td></tr>')
    return "".join(rows)


def findings_table(rep):
    if not rep["findings"]:
        return ('<div class="card"><p class="ok">Keine Beanstandung. Jeder '
                'Eintrag deckt sich mit dem Manifest, auf das er zeigt.</p></div>')
    legend = "".join(
        f'<p class="muted"><span class="badge {LEVEL_BADGE[l]}">{esc(LEVEL_LABEL[l])}</span> '
        f'{esc(LEVEL_HELP[l])}</p>' for l in ck.LEVELS if rep["counts"][l])
    return f'''<div class="card" style="overflow-x:auto">
  <h2>Was geprüft wurde</h2>
  <table>
    <tr><th>Art</th><th>App</th><th>Feld</th><th>Befund</th>
        <th>In der Liste</th><th>Im Manifest</th></tr>
    {findings_rows(rep["findings"])}
  </table>
</div>
<div class="card">{legend}</div>'''


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
    server_version = f"oaap-store-editor/{VERSION}"

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
        q = parse_qs(parsed.query)
        flash = (q.get("m") or [""])[0]
        user, roles = self.who()
        parts = [p for p in path.split("/") if p]

        if path == "/":
            self.send_html(self.lists_page(user, roles, flash))
        elif path == "/pruefen":
            self.send_html(page("Liste einfügen", PASTE_BODY, user, roles, "paste"))
        elif path == "/hilfe":
            self.send_html(page("Hilfe", HELP_BODY, user, roles, "help"))
        elif path == "/quellen":
            self.send_html(self.sources_page(user, roles, flash))
        elif parts[:1] == ["liste"] and len(parts) == 2:
            self.send_html(self.list_page(parts[1], user, roles, flash))
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
                                '<div class="card"><p class="err">Diese Seite gibt es '
                                'nicht.</p><p><a class="back" href="/">← Zu den '
                                'Listen</a></p></div>', user, roles), 404)

    # ----------------------------------------------------------------- POST

    def do_POST(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
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
                                '<div class="card"><p class="err">Diese Seite gibt es '
                                'nicht.</p></div>', user, roles), 404)
            return

        idx, action = parts[1], parts[2]
        src = self.source_at(idx)
        if not src:
            self.send_html(page("Nicht gefunden",
                                '<div class="card"><p class="err">Diese Liste ist nicht '
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
                            '<div class="card"><p class="err">Unbekannte '
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
                    f'<div class="card"><p class="err">{esc(text)}</p>'
                    f'<p><a class="back" href="/">← Zu den Listen</a></p></div>',
                    user, roles, "lists")

    # --------------------------------------------------------------- Seiten

    def lists_page(self, user, roles, flash=""):
        warn = ""
        if not storage_ready():
            warn = ('<div class="card danger"><h2>Kein Speicher</h2>'
                    '<p class="err">Diese Instanz hat keinen beschreibbaren '
                    'Speicher. Prüfen geht, Bearbeiten nicht — die Arbeitskopie '
                    'hätte keinen Platz.</p><p class="muted">Der Speicher wird im '
                    'Manifest deklariert (<code>storage</code>). Eine Instanz aus '
                    'Version 0.1.0 kennt ihn noch nicht: Ein erneutes Ausrollen '
                    'über das Portal legt ihn an.</p></div>')
        sources = load_sources()
        if not sources:
            return page("Listen", f'<h1>Listen</h1>{warn}{LISTS_EMPTY}',
                        user, roles, "lists", flash)
        rows = []
        for i, src in enumerate(sources):
            url = src["url"]
            doc, work, err = current(src)
            draft = ('<span class="badge test">Entwurf</span> ' if work else "")
            if err:
                rows.append(f'<tr><td>{draft}<a class="rowaction" href="/liste/{i}">'
                            f'{esc(url)}</a></td>'
                            f'<td colspan="2"><span class="err">{esc(err)}</span></td>'
                            f'<td><a class="rowaction" href="/liste/{i}">Ansehen</a></td></tr>')
                continue
            rep = self.checked(src, doc)
            name = str((doc or {}).get("name") or url)
            rows.append(
                f'<tr class="rowlink"><td>{draft}<a class="rowaction" href="/liste/{i}">'
                f'{esc(name)}</a><br><span class="muted">{esc(url)}</span></td>'
                f'<td>{rep["entries"]} Einträge<br><span class="muted">'
                f'{rep["checked"]} geprüft, {rep["unreachable"]} ungeprüft</span></td>'
                f'<td>{summary_badges(rep)}</td>'
                f'<td><a class="rowaction" href="/liste/{i}">Öffnen</a></td></tr>')
        body = f'''<h1>Listen</h1>{warn}
<div class="card" style="overflow-x:auto">
  <table>
    <tr><th>Liste</th><th>Umfang</th><th>Ergebnis</th><th></th></tr>
    {"".join(rows)}
  </table>
</div>
<p class="muted">Ein <span class="badge test">Entwurf</span> ist eine
   Arbeitskopie auf dieser Instanz. Geprüft wird dann der Entwurf und nicht
   mehr die Veröffentlichung — der Wächter darf für das, was gerade
   entsteht, nicht blind sein. Veröffentlicht wird in diesem Stand nichts;
   das ist Bauschritt 3 aus RFC-0013.</p>'''
        return page("Listen", body, user, roles, "lists", flash)

    def list_page(self, idx, user, roles, flash=""):
        src = self.source_at(idx)
        if not src:
            return self.not_found(user, roles, "Diese Liste ist nicht eingetragen.")
        url = src["url"]
        doc, work, err = current(src)
        if err:
            return page(url, f'<a class="back" href="/">← Zurück</a>'
                             f'<div class="card danger"><p class="err">{esc(err)}</p>'
                             f'<p class="muted">Adresse: <code>{esc(url)}</code></p></div>',
                        user, roles, "lists", flash)
        rep = self.checked(src, doc)
        title = str(doc.get("name") or url)
        per_app = {}
        for f in rep["findings"]:
            per_app.setdefault(f["app"], []).append(f["level"])

        rows = []
        for e in doc.get("apps") or []:
            if not isinstance(e, dict):
                continue
            app_id = str(e.get("id") or "")
            marks = overrides_of(work, app_id) if work else set()
            lv = per_app.get(app_id, [])
            badges = " ".join(
                f'<span class="badge {LEVEL_BADGE[l]}">{lv.count(l)} '
                f'{esc(LEVEL_LABEL[l] if lv.count(l) == 1 else LEVEL_PLURAL[l])}</span>'
                for l in ck.LEVELS if lv.count(l)) or '<span class="badge ok">in Ordnung</span>'
            mark = (f'<br><span class="muted">{len(marks)} übersteuert</span>'
                    if marks else "")
            rows.append(
                f'<tr class="rowlink"><td><a class="rowaction" '
                f'href="/liste/{esc(idx)}/eintrag/{quote(app_id)}">{esc(e.get("name") or app_id)}'
                f'</a><br><span class="muted"><code>{esc(app_id)}</code></span>{mark}</td>'
                f'<td>{esc(e.get("version"))}<br><span class="muted">'
                f'{esc(e.get("app_class") or "—")}</span></td>'
                f'<td>{esc(e.get("maturity") or "—")}<br><span class="muted">'
                f'{esc(e.get("status") or "—")}</span></td>'
                f'<td>{badges}</td>'
                f'<td><a class="rowaction" href="/liste/{esc(idx)}/eintrag/'
                f'{quote(app_id)}">Bearbeiten</a><br>'
                f'<form method="post" action="/liste/{esc(idx)}/abgleich" '
                f'style="margin:0;display:inline">'
                f'<input type="hidden" name="id" value="{esc(app_id)}">'
                f'<button class="linkish">Abgleichen</button></form><br>'
                f'<a class="rowaction" href="/liste/{esc(idx)}/eintrag/'
                f'{quote(app_id)}/bericht">Bericht</a></td></tr>')

        changes = ed.diff_documents(work["published"], doc) if work else []
        draft_card = ""
        if work:
            c = ed.count_kinds(changes)
            draft_card = f'''<div class="card">
  <h2>Entwurf <span class="badge test">nicht veröffentlicht</span></h2>
  <p class="muted">Begonnen am {esc(work.get("begonnen"))}, zuletzt gespeichert
     {esc(work.get("saved") or "—")}. {len(changes)} Änderung(en) gegenüber dem
     veröffentlichten Stand: {c[ed.STRUKTUR]} strukturell, {c[ed.REDAKTIONELL]}
     redaktionell, {c[ed.ERZEUGT]} aus den Manifesten übernommen.</p>
  <div class="actions">
    <a class="btn ghost" href="/liste/{esc(idx)}/aenderungen">Änderungen ansehen</a>
    <a class="btn" href="/liste/{esc(idx)}/datei">Als Datei herunterladen</a>
    <form method="post" action="/liste/{esc(idx)}/verwerfen" style="margin:0"
          onsubmit="return confirm('Den Entwurf verwerfen? Alle Änderungen daran gehen verloren.')">
      <button class="danger">Entwurf verwerfen</button></form>
  </div>
</div>'''
        else:
            draft_card = ('<div class="card"><p class="muted">Es gibt noch keinen '
                          'Entwurf. Die erste Änderung an einem Eintrag legt eine '
                          'Arbeitskopie an; der veröffentlichte Stand bleibt daneben '
                          'stehen und dient als Vergleich.</p></div>')

        take = f'''<div class="card">
  <h2>Mit den Manifesten abgleichen</h2>
  <p class="muted">Holt für jeden Eintrag Name, Verpackungsart, Version, Art der
     App und Rollen aus dem Manifest — das ist die 80-%-Regel aus RFC-0012 §1.3.
     <strong>Übersteuerte Felder bleiben unberührt</strong>, sonst nähme jede
     Neuerzeugung eine bewusste Entscheidung stillschweigend zurück.
     Für eine einzelne App steht <em>Abgleichen</em> in ihrer Zeile.</p>
  <form method="post" action="/liste/{esc(idx)}/uebernehmen" style="margin:0">
    <button class="ghost">Alle Einträge abgleichen</button></form>
</div>
<div class="card">
  <h2>Nachpflege-Bericht</h2>
  <p class="muted">Der umgekehrte Weg: Statt die Liste ans Manifest anzupassen,
     sagt der Bericht, <strong>was dem Manifest fehlt</strong> — als Auftrag zum
     Weiterreichen an die KI, die die App betreut, mit einem einsetzbaren
     YAML-Block. Denn das Manifest gehört dem, der die App gebaut hat; der
     Katalog ist nur ein Verzeichnis.</p>
  <p class="muted">Was der Katalog führt und das Manifest-Format noch nicht
     kennt, steht getrennt und <strong>ohne Auftrag</strong> — das ist ein
     offener Punkt an der Spezifikation und kein Versäumnis der App.</p>
  <div class="actions">
    <a class="btn ghost" href="/liste/{esc(idx)}/bericht">Bericht für die ganze
       Liste</a></div>
</div>'''

        body = f'''<a class="back" href="/">← Zu den Listen</a>
<div class="pagehead"><h1>{esc(title)}</h1><div>{summary_badges(rep)}</div></div>
{draft_card}
<div class="card" style="overflow-x:auto">
  <h2>Einträge</h2>
  <table>
    <tr><th>App</th><th>Version / Art</th><th>Reifegrad / Stand</th>
        <th>Prüfung</th><th></th></tr>
    {"".join(rows) or '<tr><td colspan="5" class="muted">Diese Liste hat noch keinen Eintrag.</td></tr>'}
  </table>
</div>
{take}
{self.new_entry_card(idx)}
{findings_table(rep)}
<div class="card"><h2>Herkunft</h2><dl class="facts">
  <dt>Quelle</dt><dd><code>{esc(url)}</code></dd>
  <dt>Einträge</dt><dd>{rep["entries"]}</dd>
  <dt>Gegen das Manifest geprüft</dt><dd>{rep["checked"]}</dd>
  <dt>Ungeprüft geblieben</dt><dd>{rep["unreachable"]}</dd>
</dl></div>'''
        return page(title, body, user, roles, "lists", flash)

    def new_entry_card(self, idx, error="", values=None):
        v = values or {}
        msg = f'<p class="err">{esc(error)}</p>' if error else ""
        return f'''<div class="card">
  <h2>Eintrag aufnehmen</h2>
  <p class="muted">Nur Kennung und Zeiger auf das Paket — alles Weitere holt
     „Aus den Manifesten übernehmen". Ein Eintrag darf entstehen,
     <strong>bevor sein Manifest abrufbar ist</strong> (RFC-0013, Frage 4);
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
    <div class="actions"><button class="ghost">Aufnehmen</button></div>
  </form>
</div>'''

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
            body = f'''<a class="back" href="{back}/eintrag/{quote(app_id)}">← Zurück</a>
<h1>{esc(entry.get("name") or app_id)} entfernen?</h1>
<div class="card danger">
  <p>Der Eintrag <code>{esc(app_id)}</code> verschwindet aus der Arbeitskopie.
     Die veröffentlichte Liste bleibt unberührt — entfernt ist er dort erst,
     wenn die neue Datei veröffentlicht wird.</p>
  <form method="post" action="{back}/eintrag/{quote(app_id)}">
    <input type="hidden" name="tun" value="entfernen">
    <div class="actions"><button class="danger">Ja, entfernen</button>
      <a class="btn ghost" href="{back}/eintrag/{quote(app_id)}">Abbrechen</a></div>
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
        fbox = ('<div class="card"><p class="ok">Dieser Eintrag deckt sich mit '
                'seinem Manifest.</p></div>' if not findings else
                f'<div class="card" style="overflow-x:auto"><h2>Prüfung</h2><table>'
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
            mark = (' <span class="badge test">übersteuert</span>' if unlocked else "")
            checked = " checked" if unlocked else ""
            gen_rows.append(f'''<label>{esc(ed.FIELD_LABEL[field])}{mark}
  <input name="gen_{field}" value="{esc(shown)}"{"" if unlocked else " disabled"}></label>
<div class="lockline">
  <label style="margin:0"><input type="checkbox" name="entriegelt" value="{field}"{checked}> abweichend pflegen</label>
  <span>{side}</span></div>''')

        body = f'''<a class="back" href="{back}">← Zur Liste</a>
<div class="pagehead"><h1>{esc(entry.get("name") or app_id)}</h1>
  <div><span class="badge">{esc(app_id)}</span></div></div>
{f'<div class="card danger"><p class="err">{esc(error)}</p></div>' if error else ""}
{fbox}
<form method="post" action="{back}/eintrag/{quote(app_id)}">
<div class="card">
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

<div class="card">
  <h2>Verweise und Bilder</h2>
  <div class="grid2">{link_fields}</div>
  <label>Weitere Verweise — je Zeile <code>Beziehung | Adresse | Beschriftung</code>
    <textarea name="links_rest" class="small">{esc(rest_links)}</textarea></label>
  <label>Bildschirmfotos — je Zeile <code>Pfad | Bildunterschrift</code>
    <textarea name="screenshots" class="small">{esc(ed.format_pairs(entry.get("screenshots"), ("src", "caption")))}</textarea></label>
  <p class="hint">Bildpfade gelten <strong>relativ zur Liste</strong>, nicht als
     Adresse eines fremden Servers (RFC-0012 §1.1): Sonst ruft jeder Knoten, der
     die Store-Seite öffnet, einen Rechner auf, den niemand ausgewählt hat — und
     verrät ihm damit seine Existenz.</p>
</div>

<div class="card locked">
  <h2>Aus dem Manifest erzeugt</h2>
  <p class="hint">Diese fünf Felder gehören dem Paket, nicht dem Katalog. Wer sie
     abweichend pflegen will, hakt das an — dann bleibt die Abweichung bei der
     nächsten Neuerzeugung stehen, statt stillschweigend zurückgenommen zu
     werden (RFC-0012 §1.3).</p>
  {"".join(gen_rows)}
</div>

<div class="card">
  <h2>Von Hand, obwohl §1.3 sie „erzeugt" nennt</h2>
  <p class="hint">RFC-0012 §1.3 führt diese Felder unter „aus dem Manifest
     erzeugt". Eine Neuerzeugung kann das heute nicht einlösen:
     <code>profiles</code> kennt das Manifest-Schema nicht, das Freigabedatum
     wäre das eines Git-Tags, und beim Bild liegt es anders — <code>app.icon</code>
     gibt es sehr wohl, aber im Katalog gilt ein Bildpfad relativ zur Liste und
     im Manifest relativ zum Paket; eine Neuerzeugung müsste die Datei
     kopieren. Sie hier verriegelt darzustellen wäre eine Unwahrheit: Es gäbe
     nichts, woraus sie je erzeugt würden. Offener Punkt am Papier —
     der <a href="{back}/eintrag/{quote(app_id)}/bericht">Nachpflege-Bericht</a>
     hält ihn fest, statt ihn zu verschweigen.</p>
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
     was der Hauptzweig gerade enthält — nicht eine bestimmte Version
     (RFC-0012 §1.1).</p>
</div>

<div class="actions">
  <button name="tun" value="speichern">Speichern</button>
  <button name="tun" value="uebernehmen" class="ghost">Speichern und mit dem Manifest abgleichen</button>
  <a class="btn ghost" href="{back}/eintrag/{quote(app_id)}/bericht">Nachpflege-Bericht</a>
  <a class="btn ghost" href="{back}">Abbrechen</a>
  <a class="btn ghost" href="{back}/eintrag/{quote(app_id)}?entfernen=1"
     style="margin-left:auto;color:var(--err);border-color:var(--err)">Eintrag entfernen</a>
</div>
</form>'''
        return page(entry.get("name") or app_id, body, user, roles, "lists", flash)

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
            f'<tr><td><span class="badge {KIND_BADGE[ch["kind"]]}">'
            f'{esc(KIND_LABEL[ch["kind"]])}</span></td>'
            f'<td><code>{esc(ch["app"])}</code></td>'
            f'<td>{esc(ed.FIELD_LABEL.get(ch["field"], ch["field"]))}</td>'
            f'<td class="muted">{esc(ch["before"]) or "—"}</td>'
            f'<td>{esc(ch["after"]) or "—"}</td></tr>' for ch in changes)
        table = (f'<div class="card" style="overflow-x:auto"><table>'
                 f'<tr><th>Art</th><th>App</th><th>Feld</th><th>Vorher</th>'
                 f'<th>Nachher</th></tr>{rows}</table></div>' if changes else
                 '<div class="card"><p class="muted">Der Entwurf ist mit dem '
                 'veröffentlichten Stand identisch.</p></div>')
        body = f'''<a class="back" href="/liste/{esc(idx)}">← Zur Liste</a>
<h1>Was sich ändern würde</h1>
<div class="card">
  <p>Gegenüber dem Stand, der beim Beginn des Entwurfs veröffentlicht war
     ({esc(work.get("begonnen"))}).</p>
  <dl class="facts">
    <dt>Strukturell</dt><dd>{c[ed.STRUKTUR]} — Einträge aufgenommen, entfernt,
       oder ein Paket, das umzieht</dd>
    <dt>Redaktionell</dt><dd>{c[ed.REDAKTIONELL]} — Texte, Einordnung, Verweise</dd>
    <dt>Aus den Manifesten</dt><dd>{c[ed.ERZEUGT]} — vom Prüfer erzeugt, nicht
       von Hand geschrieben</dd>
  </dl>
  <p class="muted">Die Trennung ist keine Kosmetik: In Bauschritt 3 zählt die
     Mengenbremse <strong>nur</strong> die ersten beiden. Was der Prüfer selbst
     aus den Manifesten gebildet hat, ist maschinell und nachvollziehbar
     entstanden — zählte es mit, käme die Rückfrage bei jedem Lauf und würde
     weggeklickt (RFC-0013, Frage 5).</p>
</div>
{table}
<div class="actions"><a class="btn" href="/liste/{esc(idx)}/datei">Als Datei
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
            body = (f'<a class="back" href="/liste/{esc(idx)}">← Zur Liste</a>'
                    f'<h1>Diese Liste wäre nicht benutzbar</h1>'
                    f'<div class="card danger"><p>Ein Knoten würde daran scheitern '
                    f'oder etwas Falsches tun. Der Prüfer ist der Wächter — deshalb '
                    f'gibt es die Datei erst, wenn das behoben ist.</p></div>'
                    f'<div class="card" style="overflow-x:auto"><table>'
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
                zugang = (f'<span class="badge ok">Platz {slot}</span><br>'
                          f'<span class="muted">Schlüssel ist hinterlegt</span>')
            else:
                zugang = (f'<span class="badge err">Platz {slot} leer</span><br>'
                          f'<span class="muted">im Portal eintragen</span>')
            entfernen = ("" if not darf else
                         f'<form method="post" action="/quellen/entfernen" '
                         f'style="margin:0" onsubmit="return confirm('
                         f'\'Diese Liste aus dem Editor nehmen? Ein Entwurf dazu '
                         f'geht verloren.\')">'
                         f'<input type="hidden" name="i" value="{i}">'
                         f'<button class="linkish" style="color:var(--err)">'
                         f'Entfernen</button></form>')
            rows.append(
                f'<tr><td><a class="rowaction" href="/liste/{i}">'
                f'{esc(src.get("name") or src["url"])}</a><br>'
                f'<span class="muted">{esc(src["url"])}</span></td>'
                f'<td>{zugang}</td><td>{entfernen}</td></tr>')

        slots = "".join(
            f'<option value="{n}"{" selected" if str(v.get("token")) == str(n) else ""}>'
            f'Platz {n} — {"Schlüssel hinterlegt" if TOKENS[n - 1] else "noch leer"}'
            f'</option>' for n in range(1, len(TOKENS) + 1))
        form = "" if not darf else f'''<div class="card">
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
    <div class="actions"><button>Aufnehmen</button></div>
  </form>
</div>'''

        hinweis = "" if darf else (
            '<div class="card"><p class="muted">Listen aufzunehmen oder zu '
            'entfernen ist Einrichtung und braucht die Rolle <code>keyuser</code> '
            'oder <code>admin</code>. Bearbeiten darf jeder, der hierher '
            'kommt.</p></div>')

        body = f'''<h1>Listen und Zugangsdaten</h1>
<div class="card" style="overflow-x:auto">
  <table>
    <tr><th>Liste</th><th>Zugang</th><th></th></tr>
    {"".join(rows) or '<tr><td colspan="3" class="muted">Noch keine Liste eingetragen.</td></tr>'}
  </table>
</div>
{form}{hinweis}
<div class="card">
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
     der Beleg, dass die Plattform eine allgemeine Lösung bekommen sollte
     (RFC-0013).</p>
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
            body = (f'<a class="back" href="/liste/{esc(idx)}">← Zur Liste</a>'
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
                                f'<h1>Liste einfügen</h1><div class="card danger">'
                                f'<p class="err">Das ist keine gültige JSON-Datei: '
                                f'{esc(e)}</p></div>{PASTE_BODY}', user, roles, "paste"))
            return
        rep = self.checked(src, doc)
        title = str((doc or {}).get("name") or "Eingefügte Liste")
        body = (f'<a class="back" href="/pruefen">← Zurück</a>'
                f'<div class="pagehead"><h1>{esc(title)}</h1>'
                f'<div>{summary_badges(rep)}</div></div>'
                f'<div class="card"><dl class="facts">'
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


LISTS_EMPTY = '''<div class="card">
  <h2>Noch keine Liste eingetragen</h2>
  <p class="muted">Unter <a href="/quellen">Listen und Zugang</a> nimmst Du
     eine auf — auch eine aus einem privaten Repository. Die Voreinstellung
     kommt aus der Instanz-Konfiguration (<code>STORE_EDITOR_LISTS</code>).</p>
  <p class="muted">Eine Liste, die noch nirgends veröffentlicht ist, lässt
     sich unter <a href="/pruefen">Liste einfügen</a> trotzdem prüfen.</p>
</div>'''

PASTE_BODY = '''<h1>Liste einfügen</h1>
<div class="card">
  <p class="muted">Für eine Liste, die noch nicht veröffentlicht ist —
     etwa eine, die gerade entsteht. Der Inhalt wird geprüft und nirgends
     gespeichert. Die Manifeste holt der Prüfer aus den Repositories, auf
     die die Einträge zeigen.</p>
  <form method="post" action="/pruefen">
    <label>Inhalt der Liste (JSON)
      <textarea name="doc" rows="14" required
                placeholder='{"store": "0.2", "name": "…", "apps": [ … ]}'></textarea></label>
    <div class="actions"><button>Prüfen</button></div>
  </form>
</div>'''

HELP_BODY = f'''<h1>Hilfe</h1>
<div class="card">
  <h2>Wozu dieses Werkzeug</h2>
  <p>Eine Store-Liste ist kein Dokument. Sie ist eine Anweisung, die auf
     fremden Rechnern Software installiert. Deshalb prüft dieses Werkzeug
     eine Liste nicht nur gegen das Format, sondern <strong>gegen die
     Manifeste, auf die sie zeigt</strong>.</p>
  <p class="muted">Der Anlass steht in RFC-0013: Ollama stand am 09.08.2026
     in unserer Liste als Hintergrunddienst, sein Manifest sagte davon
     nichts. Der Widerspruch war wochenlang unsichtbar — beim Lesen von
     JSON findet so etwas kein Mensch.</p>
</div>
<div class="card">
  <h2>Die drei Arten von Befund</h2>
  <dl class="facts">
    <dt><span class="badge err">Fehler</span></dt><dd>{esc(LEVEL_HELP[ck.FEHLER])}</dd>
    <dt><span class="badge test">Befund</span></dt><dd>{esc(LEVEL_HELP[ck.BEFUND])}</dd>
    <dt><span class="badge off">Hinweis</span></dt><dd>{esc(LEVEL_HELP[ck.HINWEIS])}</dd>
  </dl>
  <p class="muted">Unbekannte Werte im Vokabular sind bewusst nur ein
     Hinweis: Ein Knoten toleriert sie (RFC-0012 §8.1), also darf der
     Editor nicht so tun, als wäre die Liste kaputt.</p>
</div>
<div class="card">
  <h2>Wie bearbeitet wird</h2>
  <p>Die erste Änderung legt einen <strong>Entwurf</strong> an — eine
     Arbeitskopie auf dieser Instanz. Der veröffentlichte Stand bleibt
     daneben stehen; <em>Änderungen ansehen</em> zeigt jederzeit, was sich
     unterscheidet. Solange ein Entwurf besteht, prüft der Prüfer ihn und
     nicht mehr die Veröffentlichung.</p>
  <p><strong>Fünf Felder gehören dem Paket:</strong> Name, Verpackungsart,
     Version, Art der App und die Rollen. Sie stehen verriegelt da und
     werden aus dem Manifest übernommen. Wer eines davon abweichend pflegen
     will, hakt „abweichend pflegen" an — die Abweichung wird dann
     <strong>markiert</strong> und überlebt die nächste Neuerzeugung. Ohne
     diese Markierung nähme jede Neuerzeugung eine bewusste Entscheidung
     stillschweigend zurück (RFC-0012 §1.3).</p>
  <p class="muted">Die Markierungen liegen im Editor, nicht in der Liste:
     Eine Liste ist ein Dokument nach dem Schema, die Buchführung des
     Editors gehört nicht hinein. Wer dieselbe Liste in einem anderen
     Editor öffnet, sieht sie deshalb nicht.</p>
</div>
<div class="card">
  <h2>Was verglichen wird — und was nicht</h2>
  <p>Verglichen wird, was in Liste <em>und</em> Manifest steht: Name,
     Verpackungsart, Version, Art der App und die Rollen (als Menge —
     die Reihenfolge bedeutet nichts).</p>
  <p class="muted"><strong>Nicht</strong> verglichen werden
     <code>description</code> (in der Liste steht absichtlich der längere,
     redaktionelle Text), <code>icon</code>, <code>released</code> und
     <code>profiles</code>. RFC-0012 §1.3 führt sie als „erzeugt", was eine
     Neuerzeugung nicht einlösen kann: <code>released</code> und
     <code>profiles</code> kennt das Manifest-Schema gar nicht, und beim Bild
     gilt im Katalog ein Pfad relativ zur Liste, im Manifest einer relativ zum
     Paket. Das ist ein offener Punkt am Papier, kein Versäumnis dieses
     Werkzeugs — deshalb sind sie hier frei bearbeitbar statt verriegelt.</p>
</div>
<div class="card">
  <h2>Der Nachpflege-Bericht</h2>
  <p>Der Abgleich geht in eine Richtung: Liste folgt Manifest. Der Bericht
     geht in die andere — er sagt, <strong>was dem Manifest fehlt</strong>,
     und ist zum Weiterreichen an die KI gedacht, die die App betreut. Mit
     einem YAML-Block, den man einsetzen kann.</p>
  <p class="muted">Drei Dinge tut er bewusst <em>nicht</em>. Er verlangt nichts
     nachzupflegen, wo Liste und Manifest sich <strong>widersprechen</strong> —
     dort ist der Katalog schuld, und eine fremde KI anzuweisen, unsere
     veraltete Version zu übernehmen, wäre schlimmer als gar kein Bericht. Er
     schlägt für das Bild <strong>keinen Pfad</strong> vor, weil der im Katalog
     einen anderen Bezugspunkt hat. Und was das Manifest-Format noch nicht
     kennt, führt er getrennt und <strong>ohne Auftrag</strong>.</p>
</div>
<div class="card">
  <h2>Was dieser Stand nicht kann</h2>
  <p>Er <strong>veröffentlicht nichts</strong>: kein Zurückschreiben ins
     Repository, keine Zugangsdaten. Das Ergebnis ist eine Datei zum
     Herunterladen. Das Zurückschreiben ist Bauschritt 3 aus RFC-0013,
     dann mit den drei Betriebsarten — allein gepflegt, Vier-Augen,
     Vorschlag einreichen.</p>
  <p class="muted">Der Prüfer ist dabei der Wächter, nicht der Mensch: Was
     strukturell kaputt ist, gibt es auch jetzt schon nicht als Datei.</p>
</div>'''


def main():
    print(f"OAAP Store Editor {VERSION} auf Port {PORT}; "
          f"{len(load_sources())} Liste(n); {sum(1 for t in TOKENS if t)} von "
          f"{len(TOKENS)} Zugangsdaten-Plätzen belegt; Speicher {DATA_DIR} "
          f"{'beschreibbar' if storage_ready() else 'NICHT beschreibbar'}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
