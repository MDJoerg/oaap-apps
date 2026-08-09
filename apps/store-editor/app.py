#!/usr/bin/env python3
"""OAAP Store Editor 0.1 — Bauschritt 1 aus RFC-0013: prüfen, nicht schreiben.

Eine Store-Liste ist kein Dokument, sondern eine Anweisung, die auf
fremden Rechnern Software installiert. Deshalb ist der **Prüfer** der
Kern dieses Werkzeugs und nicht das Formular: Er hält jede Liste gegen
das Schema UND gegen die Manifeste, auf die sie zeigt.

Dieser Bauschritt **schreibt nichts** — kein Git, keine Zugangsdaten,
keine Ablage. Bearbeiten (Bauschritt 2) und Zurückschreiben
(Bauschritt 3) kommen später; die Betriebsarten dafür stehen in
RFC-0013 §3.

Gebaut als gewöhnliche OAAP-App nach dem App Deployment Contract:
kein eigener Login (die Anmeldung kommt als Gateway-Kopfzeile), ein
HTTP-Port, Konfiguration über deklarierte Umgebungsvariablen, Logs
nach stdout, Gesundheitspfad, offline-fähig.

Abhängigkeit: **PyYAML**, und sonst nichts. Das Studio kommt mit der
Standardbibliothek aus; hier geht das nicht, weil fremde Manifeste
gelesen werden. Ein selbstgebauter YAML-Leser, der eine Schreibweise
missversteht, würde genau die Art stiller Falschaussage erzeugen, gegen
die dieses Werkzeug antritt. PyYAML installiert sich ohne Übersetzer
(reines Python als Rückfallebene), der Build auf arm64 bleibt also
abhängigkeitsarm im Sinne von ADR-0005.

Optik nach oaap-design/docs/design-guidelines.md v0.1 — Rahmen und
Stil sind bewusst aus dem Studio übernommen, damit beide wie ein
Produkt aussehen.
"""

import html
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import yaml

import checker as ck

VERSION = "0.1.0"
PORT = 8000
FETCH_TIMEOUT = 15

# Deklarierte Konfiguration (Manifest `config`). Mehrere Listen je
# Instanz ist RFC-0013 Entscheidung 3; in diesem Bauschritt stehen sie
# in einer Umgebungsvariablen, weil noch nichts geschrieben wird und
# eine Ablage dafür ein Konzept wäre, das erst Bauschritt 2 braucht.
LISTS = [u.strip() for u in
         os.environ.get("STORE_EDITOR_LISTS", "").replace(chr(10), ",").split(",")
         if u.strip()]

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
    """Gemeinsamer Rahmen: Kopf mit Marke, Navigation, Benutzer, Fuß."""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — OAAP Store Editor</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP STORE EDITOR</b><small>App-Listen prüfen und pflegen</small></span>
  </a>
  <nav class="main">
    <a href="./" class="{'active' if active == 'lists' else ''}">Listen</a>
    <a href="./pruefen" class="{'active' if active == 'paste' else ''}">Liste einfügen</a>
    <a href="./hilfe" class="{'active' if active == 'help' else ''}">Hilfe</a>
  </nav>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP Store Editor {VERSION} — Bauschritt 1: prüfen. Dieser Stand schreibt nichts.
</footer>
</html>"""


# ------------------------------------------------------------------ Abrufen

def fetch(url):
    """Eine Datei holen. Bewusst ohne Umleitung auf andere Hosts."""
    req = urllib.request.Request(url, headers={"User-Agent": f"oaap-store-editor/{VERSION}"})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def check_url(url):
    """Liste abrufen und prüfen. Gibt (Bericht, Dokument, Fehlertext)."""
    try:
        doc = json.loads(fetch(url))
    except urllib.error.HTTPError as e:
        return None, None, f"Der Server antwortete mit {e.code}."
    except (urllib.error.URLError, OSError) as e:
        return None, None, f"Nicht erreichbar: {getattr(e, 'reason', e)}"
    except ValueError as e:
        return None, None, f"Das ist keine gültige JSON-Datei: {e}"
    return ck.check_document(doc, fetch=fetch, load_yaml=yaml.safe_load), doc, ""


# -------------------------------------------------------------- Darstellung

def summary_badges(rep):
    out = []
    for lvl in ck.LEVELS:
        n = rep["counts"][lvl]
        if n:
            word = LEVEL_LABEL[lvl] if n == 1 else LEVEL_PLURAL[lvl]
            out.append(f'<span class="badge {LEVEL_BADGE[lvl]}">{n} '
                       f'{esc(word)}</span>')
    if not out:
        out.append('<span class="badge ok">ohne Beanstandung</span>')
    return " ".join(out)


def findings_table(rep):
    if not rep["findings"]:
        return ('<div class="card"><p class="ok">Keine Beanstandung. Jeder '
                'Eintrag deckt sich mit dem Manifest, auf das er zeigt.</p></div>')
    rows = []
    for f in sorted(rep["findings"], key=lambda x: (ck.LEVELS.index(x["level"]), x["app"])):
        rows.append(
            f'<tr><td><span class="badge {LEVEL_BADGE[f["level"]]}">'
            f'{esc(LEVEL_LABEL[f["level"]])}</span></td>'
            f'<td><code>{esc(f["app"]) or "—"}</code></td>'
            f'<td><code>{esc(f["field"])}</code></td>'
            f'<td>{esc(f["text"])}</td>'
            f'<td>{esc(f["list"]) or "—"}</td>'
            f'<td>{esc(f["manifest"]) or "—"}</td></tr>')
    legend = "".join(
        f'<p class="muted"><span class="badge {LEVEL_BADGE[l]}">{esc(LEVEL_LABEL[l])}</span> '
        f'{esc(LEVEL_HELP[l])}</p>' for l in ck.LEVELS if rep["counts"][l])
    return f'''<div class="card" style="overflow-x:auto">
  <h2>Was geprüft wurde</h2>
  <table>
    <tr><th>Art</th><th>App</th><th>Feld</th><th>Befund</th>
        <th>In der Liste</th><th>Im Manifest</th></tr>
    {"".join(rows)}
  </table>
</div>
<div class="card">{legend}</div>'''


def report_page(title, url, rep, err, user, roles, active="lists", back=""):
    back_html = f'<a class="back" href="{esc(back)}">← Zurück</a>' if back else ""
    if err:
        body = (f'{back_html}<h1>{esc(title)}</h1>'
                f'<div class="card danger"><p class="err">{esc(err)}</p>'
                f'<p class="muted">Adresse: <code>{esc(url)}</code></p></div>')
        return page(title, body, user, roles, active)
    facts = f'''<div class="card">
  <h2>Überblick</h2>
  <dl class="facts">
    <dt>Einträge</dt><dd>{rep["entries"]}</dd>
    <dt>Gegen das Manifest geprüft</dt><dd>{rep["checked"]}</dd>
    <dt>Ungeprüft geblieben</dt><dd>{rep["unreachable"]}
       {"<br><span class='muted'>Solange ein Manifest nicht abrufbar ist, "
        "steht die Behauptung des Eintrags ohne Beleg da.</span>"
        if rep["unreachable"] else ""}</dd>
    {f"<dt>Quelle</dt><dd><code>{esc(url)}</code></dd>" if url else ""}
  </dl>
</div>'''
    body = (f'{back_html}<div class="pagehead"><h1>{esc(title)}</h1>'
            f'<div>{summary_badges(rep)}</div></div>{facts}{findings_table(rep)}')
    return page(title, body, user, roles, active)


LISTS_EMPTY = '''<div class="card">
  <h2>Noch keine Liste eingetragen</h2>
  <p class="muted">Der Editor prüft die Listen, die in der Konfiguration
     dieser Instanz stehen — <code>STORE_EDITOR_LISTS</code>, mehrere durch
     Komma getrennt. Die Konfiguration ändert ein <code>server_admin</code>
     im Portal auf der Instanzseite.</p>
  <p class="muted">Eine Liste, die noch nirgends veröffentlicht ist, lässt
     sich unter <a href="./pruefen">Liste einfügen</a> trotzdem prüfen.</p>
</div>'''

PASTE_BODY = '''<h1>Liste einfügen</h1>
<div class="card">
  <p class="muted">Für eine Liste, die noch nicht veröffentlicht ist —
     etwa eine, die gerade entsteht. Der Inhalt wird geprüft und nirgends
     gespeichert. Die Manifeste holt der Prüfer aus den Repositories, auf
     die die Einträge zeigen.</p>
  <form method="post" action="./pruefen">
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
  <h2>Was verglichen wird — und was nicht</h2>
  <p>Verglichen wird, was in Liste <em>und</em> Manifest steht: Name,
     Verpackungsart, Version, Art der App und die Rollen (als Menge —
     die Reihenfolge bedeutet nichts).</p>
  <p class="muted"><strong>Nicht</strong> verglichen werden
     <code>description</code> (in der Liste steht absichtlich der längere,
     redaktionelle Text), <code>icon</code>, <code>released</code> und
     <code>profiles</code> — die kennt das Manifest-Schema heute gar nicht.
     RFC-0012 §1.3 führt sie als „erzeugt", was das Manifest nicht
     einlösen kann; das ist ein offener Punkt am Papier, kein Versäumnis
     dieses Werkzeugs.</p>
</div>
<div class="card">
  <h2>Was dieser Stand nicht kann</h2>
  <p>Er <strong>schreibt nichts</strong>: kein Bearbeiten, kein
     Zurückschreiben, keine Zugangsdaten. Das ist Absicht — Bauschritt 1
     aus RFC-0013. Bearbeiten kommt in Bauschritt 2, das Zurückschreiben
     in Bauschritt 3, dann mit den drei Betriebsarten (allein gepflegt,
     Vier-Augen, Vorschlag einreichen).</p>
</div>'''


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
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles", "") or "").split(",")
                 if r.strip()}
        return user, roles

    def send_html(self, body, status=200):
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok\n")
            return
        user, roles = self.who()
        rolestr = ", ".join(sorted(roles))
        if path == "/":
            self.send_html(self.lists_page(user, rolestr))
        elif path.startswith("/liste/"):
            self.send_html(self.list_page(path.rsplit("/", 1)[-1], user, rolestr))
        elif path == "/pruefen":
            self.send_html(page("Liste einfügen", PASTE_BODY, user, rolestr, "paste"))
        elif path == "/hilfe":
            self.send_html(page("Hilfe", HELP_BODY, user, rolestr, "help"))
        else:
            self.send_html(page("Nicht gefunden",
                                '<div class="card"><p class="err">Diese Seite gibt es '
                                'nicht.</p><p><a class="back" href="./">← Zu den '
                                'Listen</a></p></div>', user, rolestr), 404)

    def do_POST(self):
        if urlparse(self.path).path.rstrip("/") != "/pruefen":
            self.send_html("", 404)
            return
        user, roles = self.who()
        rolestr = ", ".join(sorted(roles))
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        text = (parse_qs(raw).get("doc") or [""])[0]
        try:
            doc = json.loads(text)
        except ValueError as e:
            self.send_html(page("Liste einfügen",
                                f'<h1>Liste einfügen</h1><div class="card danger">'
                                f'<p class="err">Das ist keine gültige JSON-Datei: '
                                f'{esc(e)}</p></div>{PASTE_BODY}', user, rolestr, "paste"))
            return
        rep = ck.check_document(doc, fetch=fetch, load_yaml=yaml.safe_load)
        title = str((doc or {}).get("name") or "Eingefügte Liste")
        self.send_html(report_page(title, "", rep, "", user, rolestr, "paste", "./pruefen"))

    # ---------------------------------------------------------------- Seiten

    def lists_page(self, user, rolestr):
        if not LISTS:
            return page("Listen", f'<h1>Listen</h1>{LISTS_EMPTY}', user, rolestr, "lists")
        rows = []
        for i, url in enumerate(LISTS):
            rep, doc, err = check_url(url)
            if err:
                rows.append(f'<tr class="rowlink"><td><a class="rowaction" '
                            f'href="./liste/{i}">{esc(url)}</a></td>'
                            f'<td colspan="2"><span class="err">{esc(err)}</span></td>'
                            f'<td><a class="rowaction" href="./liste/{i}">Ansehen</a></td></tr>')
                continue
            name = str((doc or {}).get("name") or url)
            rows.append(
                f'<tr class="rowlink"><td><a class="rowaction" href="./liste/{i}">'
                f'{esc(name)}</a><br><span class="muted">{esc(url)}</span></td>'
                f'<td>{rep["entries"]} Einträge<br><span class="muted">'
                f'{rep["checked"]} geprüft, {rep["unreachable"]} ungeprüft</span></td>'
                f'<td>{summary_badges(rep)}</td>'
                f'<td><a class="rowaction" href="./liste/{i}">Ansehen</a></td></tr>')
        body = f'''<h1>Listen</h1>
<div class="card" style="overflow-x:auto">
  <table>
    <tr><th>Liste</th><th>Umfang</th><th>Ergebnis</th><th></th></tr>
    {"".join(rows)}
  </table>
</div>
<p class="muted">Dieser Stand prüft nur. Bearbeiten und Zurückschreiben
   sind Bauschritt 2 und 3 aus RFC-0013.</p>'''
        return page("Listen", body, user, rolestr, "lists")

    def list_page(self, idx, user, rolestr):
        try:
            url = LISTS[int(idx)]
        except (ValueError, IndexError):
            return page("Nicht gefunden",
                        '<div class="card"><p class="err">Diese Liste ist nicht '
                        'eingetragen.</p></div>', user, rolestr, "lists")
        rep, doc, err = check_url(url)
        title = str((doc or {}).get("name") or url) if doc else url
        return report_page(title, url, rep, err, user, rolestr, "lists", "./")


def main():
    print(f"OAAP Store Editor {VERSION} auf Port {PORT}; "
          f"{len(LISTS)} Liste(n) konfiguriert", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
