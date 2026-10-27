"""OAAP KI-Gateway 0.5 — eine Bezugsquelle für alle, ein Schlüssel je Verbraucher.

Umsetzung der Capability `oaap.ai.gateway` 0.3 (RFC-0023, RFC-0064). Das Gateway
bietet **einen OpenAI-kompatiblen Endpunkt** an und bedient sich bei
einer oder mehreren Bezugsquellen: lokales Modell, externer Anbieter,
Kunden-Endpunkt oder ein anderes Gateway. Der Verbraucher fragt nach
einem **Zweck** (`chat-default`, `code`) und nie nach einem Hersteller.

Zwei Wege in dieselbe App, mit verschiedenen Rollen im Manifest:

- `/v1/...` ist `public` — die Plattform authentifiziert dort nichts,
  entfernt gefälschte Identitäts-Kopfzeilen und bremst je Client
  (RFC-0010, einmal je Anfrage, ein Strom also unbelastet). Geprüft
  wird hier: **Der API-Schlüssel ist die Identität** — keine Sitzung,
  keine Rollen, kein Kontakt zum Identitätsdienst.
- `/` ist `admin` — die Betreiber-Sicht: Aliasse, Bezugsquellen,
  Schlüssel ausstellen und widerrufen, Verbrauch, Auswertung
  (`/auswertung`) und die Schnittstelle für externe Berichte
  (`/report/v1/…`, RFC-0064 §6) — die eine Maschine mit einem
  Plattform-API-Schlüssel abfragt, nicht mit einem Gateway-Schlüssel. Die Plattform
  liefert die geprüfte Identität als Kopfzeile (App Deployment
  Contract); einen eigenen Login gibt es hier so wenig wie in jeder
  anderen OAAP-App.

Die Regel, die alles trägt (Spec §6): **In keinem Protokoll stehen
jemals Prompts oder Antworten.** Gezählt wird, nicht mitgeschrieben —
es gibt nicht einmal eine Spalte, in die ein Prompt passen würde.

Gebaut als gewöhnliche OAAP-App: ein HTTP-Port, Persistenz nur unter
dem deklarierten Mount, Konfiguration über deklarierte Variablen, Logs
nach stdout, Gesundheitspfad, nur Standardbibliothek.
"""
import html
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode

import catalog
import charts
import relay
import report
import store
import supply
import vault

VERSION = "0.5.0"
PORT = 8000

DATA_DIR = os.environ.get("AIGW_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "gateway.db")

MAX_BODY = 8 * 1024 * 1024          # großzügig: Kontexte sind heute groß
DEFAULT_RATE = 120                  # Anfragen je Minute und Schlüssel

esc = html.escape

# ------------------------------------------------------------- Konfiguration

try:
    TIMEOUT = max(10, min(3600, int(os.environ.get("AIGW_TIMEOUT_SECONDS") or relay.READ_TIMEOUT)))
except ValueError:
    TIMEOUT = relay.READ_TIMEOUT

DB = store.connect(DB_PATH)
LIMITER = store.RateLimiter()

# Bezugsquellen und Modellnamen (RFC-0064 Stufe 2): Die Datenbank ist die
# Wahrheit; die drei alten Variablen werden beim ersten Start einmal
# übernommen. Nur solange die Übernahme wartet (Zugangsdaten da, aber
# kein AIGW_MASTER_KEY), gilt die Konfiguration wie bisher — dann sind
# die Formulare gesperrt, damit es nicht zwei Wahrheiten gibt.
MASTER = os.environ.get("AIGW_MASTER_KEY", "")
IMPORT_STATE, IMPORT_NOTES = catalog.import_config(DB, os.environ, MASTER)
MODE = "config" if IMPORT_STATE == "blocked" else "app"
SUPPLIERS, ALIASES, CONFIG_ERRORS = {}, {}, []
_SUPPLY_LOCK = threading.Lock()


def reload_supply():
    """Baut SUPPLIERS/ALIASES neu — beim Start und nach jeder Änderung.

    Die drei Namen werden jeweils als Ganzes ersetzt, nie verändert: Eine
    Anfrage, die gerade läuft, behält die Fassung, mit der sie begann.
    """
    global SUPPLIERS, ALIASES, CONFIG_ERRORS
    with _SUPPLY_LOCK:
        if MODE == "config":
            suppliers, s_errors = supply.parse_suppliers(
                os.environ.get("AIGW_SUPPLIERS", ""), os.environ.get("AIGW_SUPPLIER_KEYS", ""))
            aliases, a_errors = supply.parse_aliases(os.environ.get("AIGW_ALIASES", ""), suppliers)
            errors = s_errors + a_errors
        else:
            suppliers, aliases, errors = catalog.load(DB, MASTER)
        SUPPLIERS, ALIASES, CONFIG_ERRORS = suppliers, aliases, errors


reload_supply()

# Eine zweite, kleine Bremse gegen das Raten von Schlüsseln. Die große
# steht in der Plattform (RFC-0010) und gilt je Client-Adresse für alle
# Anfragen; diese hier zählt ausschließlich **Fehlversuche** und macht
# das Durchprobieren teuer, ohne einen ehrlichen Verbraucher zu stören.
_FAILS = {}
_FAILS_LOCK = threading.Lock()
FAIL_WINDOW = 300
FAIL_LIMIT = 5

def error_bytes(message, code):
    return json.dumps({"error": {"message": message, "type": "invalid_request_error",
                                 "code": code}}, ensure_ascii=False).encode("utf-8")


# Dieselbe Antwort für „kein Schlüssel“, „unbekannt“ und „widerrufen“
# (Spec §8): Wer probiert, soll aus der Antwort nichts lernen.
DENIED = {"error": {"message": "Kein gültiger API-Schlüssel.",
                    "type": "invalid_request_error", "code": "invalid_api_key"}}


def note_failure(addr):
    with _FAILS_LOCK:
        cutoff = time.monotonic() - FAIL_WINDOW
        hits = [t for t in _FAILS.get(addr, ()) if t > cutoff]
        hits.append(time.monotonic())
        _FAILS[addr] = hits
        return len(hits)


def failing_hard(addr):
    with _FAILS_LOCK:
        cutoff = time.monotonic() - FAIL_WINDOW
        hits = [t for t in _FAILS.get(addr, ()) if t > cutoff]
        _FAILS[addr] = hits
        return len(hits) >= FAIL_LIMIT


def ceiling_of(row):
    """Die schlechteste Ampelfarbe, die dieser Schlüssel benutzen darf."""
    return row["ceiling"] or supply.DEFAULT_CEILING


def pbd_of(row):
    """Freigabe für personenbezogene Daten — eine Erklärung, kein Befund.

    Das Gateway darf nicht in die Anfrage sehen (Spec §6) und kann
    deshalb personenbezogene Daten nicht erkennen. Es kann nur prüfen,
    was beim Ausstellen des Schlüssels gesagt wurde — und daraus die eine
    Regel halten, die es halten kann: freigegeben heißt **nie rot**.
    """
    return bool(row["personal_data"])


def allowed_aliases(row):
    """Leere Angabe heißt: alle konfigurierten Aliasse."""
    raw = [a for a in (row["aliases"] or "").split(",") if a]
    if not raw:
        return list(ALIASES)
    return [a for a in raw if a in ALIASES]


# ------------------------------------------------------------------- Optik

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
  .userbox{display:flex;align-items:center;gap:.7rem;font-size:.9rem;margin-left:auto}
  .userbox .who{text-align:right;line-height:1.2}
  .userbox .who small{opacity:.75}
  main{max-width:64rem;margin:1.6rem auto;padding:0 1.2rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .card.secretbox{border-color:#86efac;background:#f0fdf4}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.green{background:#dcfce7;color:#166534}
  .badge.yellow{background:#fef3c7;color:#92400e}
  .badge.red{background:#fee2e2;color:#991b1b}
  .badge.pbd{background:#ede9fe;color:#5b21b6}
  .badge.off{background:#f3f4f6;color:#6b7280}
  .badge.err{background:#fee2e2;color:#991b1b}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  button.quiet{background:#f3f4f6;color:#374151;min-height:36px;padding:.35rem .9rem;
       font-size:.85rem}
  button.quiet:hover{background:#e5e7eb}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .err{color:var(--err)}.muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  code,kbd{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
  pre{background:#0f172a;color:#e2e8f0;padding:.9rem 1rem;border-radius:.5rem;
       overflow-x:auto;font-size:.82rem}
  form.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
       gap:.8rem 1rem;align-items:end}
  label{display:block;font-size:.82rem;color:var(--oaap-muted);margin-bottom:.25rem}
  input[type=text],input[type=number]{width:100%;padding:.5rem .6rem;border-radius:.4rem;
       border:1px solid var(--oaap-border);font-size:.95rem;min-height:44px}
  fieldset{border:1px solid var(--oaap-border);border-radius:.4rem;padding:.5rem .8rem}
  fieldset legend{font-size:.78rem;color:var(--oaap-muted);padding:0 .3rem}
  fieldset label{display:inline-flex;align-items:center;gap:.35rem;margin-right:.9rem;
       font-size:.9rem;color:var(--oaap-text)}
  footer.oaap{max-width:64rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
       color:var(--oaap-muted);font-size:.8rem;display:flex;gap:.5rem;align-items:center}
  .legend{display:flex;flex-wrap:wrap;gap:.3rem 1rem;margin:0 0 .5rem;font-size:.85rem}
  .lg{display:inline-flex;align-items:center;gap:.35rem}
  .lg i{display:inline-block;width:.8rem;height:.8rem;border-radius:2px}
  svg.chart{width:100%;height:auto;display:block}
  svg.chart .ax{font-size:11px;fill:var(--oaap-muted)}
  .hbar{display:grid;grid-template-columns:minmax(8rem,2fr) 3fr minmax(6rem,auto);gap:.6rem;
       align-items:center;padding:.3rem 0;font-size:.9rem}
  .hl{overflow:hidden;text-overflow:ellipsis}
  .hl code{white-space:nowrap;word-break:normal;font-size:.78rem}
  .ht{background:#f3f4f6;border-radius:4px;height:.8rem;overflow:hidden}
  .ht i{display:block;height:100%;border-radius:0 4px 4px 0}
  .hv{text-align:right;white-space:nowrap}.hv small{color:var(--oaap-muted)}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.8rem}
  .tile b{display:block;font-size:1.35rem}.tile span{font-size:.8rem;color:var(--oaap-muted)}
  .scroll{overflow-x:auto}td.num{text-align:right;white-space:nowrap}
  select{width:100%;padding:.5rem .6rem;border-radius:.4rem;border:1px solid var(--oaap-border);
       font-size:.95rem;min-height:44px;background:#fff}
  input[type=date]{width:100%;padding:.5rem .6rem;border-radius:.4rem;
       border:1px solid var(--oaap-border);font-size:.95rem;min-height:44px}
  @media (max-width:640px){ .userbox .who{display:none} .hbar{grid-template-columns:1fr 1fr} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

LOGO_SVG = ('<svg viewBox="0 0 100 100" width="34" height="34" aria-hidden="true">'
            '<polygon points="34,6 58,20 58,48 34,62 10,48 10,20" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round"/>'
            '<polygon points="72,30 92,41 92,64 72,76 52,64 52,41" fill="#fff" opacity=".85"/>'
            '<polygon points="42,58 66,72 66,94 42,96 22,84 22,70" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round" opacity=".6"/></svg>')


def page(title, body, user, roles):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — OAAP KI-Gateway</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP KI-GATEWAY</b><small>Ein Zweck, ein Schlüssel, eine Rechnung</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP KI-Gateway {VERSION} — zählt Verbräuche, speichert keine Inhalte
</footer>
</html>"""


def light_badge(light, short=False):
    if not light:
        return '<span class="badge off">keine erreichbare Quelle</span>'
    text = light if short else supply.LIGHT_LABELS.get(light, light)
    rule = supply.LIGHT_RULES.get(light, "")
    return f'<span class="badge {esc(light)}" title="{esc(rule)}">{esc(text)}</span>' 


def fmt_tokens(value):
    if value is None:
        return '<span class="muted">keine Zahlen</span>'
    return f"{value:,}".replace(",", ".")


def fmt_bytes(value):
    """Bytes lesbar. None heißt: vor 0.3 gezählt, Bytes unbekannt."""
    if value is None:
        return '<span class="muted">—</span>'
    value = int(value)
    for unit, size in (("GB", 1 << 30), ("MB", 1 << 20), ("kB", 1 << 10)):
        if value >= size:
            return f"{value / size:.1f} {unit}".replace(".", ",")
    return f"{value} B"


def context_line(row):
    """Der Kontext eines Schlüssels in einer Zeile, Leeres weggelassen."""
    bits = [row["owner"] or "ohne Verantwortlichen"]
    for name in ("account", "project", "cost_center", "application"):
        if row[name] and not (name == "account" and row[name] == "default"):
            bits.append(f"{CONTEXT_LABELS[name]} {row[name]}")
    if row["labels"]:
        bits.append(store.labels_text(row["labels"]))
    return " · ".join(bits)


CONTEXT_LABELS = {"owner": "Verantwortlich", "account": "Account", "project": "Projekt",
                  "cost_center": "Kostenstelle", "application": "Anwendung",
                  "labels": "Freie Etiketten"}


def context_inputs(row=None, prefix=""):
    """Eingabefelder für den Kontext — leer beim Ausstellen, gefüllt beim Ändern."""
    out = []
    for name in store.CONTEXT_FIELDS:
        value = ""
        if row is not None:
            value = store.labels_text(row[name]) if name == "labels" else row[name]
        hint = " (name=wert, Komma-getrennt, höchstens 5)" if name == "labels" else ""
        out.append(f'<div><label for="{prefix}{name}">{CONTEXT_LABELS[name]}{hint}</label>'
                   f'<input id="{prefix}{name}" name="{name}" type="text" '
                   f'maxlength="{400 if name == "labels" else store.MAX_TEXT}" '
                   f'value="{esc(value or "")}"></div>')
    return "".join(out)


def light_radios(current, prefix):
    out = []
    for light in ("green", "yellow", "red"):
        checked = " checked" if light == current else ""
        out.append(f'<label><input type="radio" name="light" value="{light}"{checked}> '
                   f'{esc(supply.LIGHT_LABELS[light].split(" — ")[0])}</label>')
    return (f'<div><fieldset><legend>Ampel — was mit den Daten geschehen kann</legend>'
            f'{"".join(out)}</fieldset></div>')


def supply_notes():
    """Woher die Bezugsquellen kommen — und was mit den alten Variablen ist."""
    old_set = any(os.environ.get(k, "").strip()
                  for k in ("AIGW_SUPPLIERS", "AIGW_SUPPLIER_KEYS", "AIGW_ALIASES"))
    if MODE == "config":
        reasons = "".join(f"<li>{esc(n)}</li>" for n in IMPORT_NOTES)
        return [f"""<div class="card attention">
  <h2>Noch nicht in die App übernommen</h2>
  <p class="hint">Bezugsquellen und Modellnamen kommen noch aus der Instanz-Konfiguration,
    und die Formulare unten sind gesperrt — sonst gäbe es zwei Wahrheiten.</p>
  <ul>{reasons}</ul>
  <p class="hint">Abhilfe: Portal → Instanz → Konfiguration → <code>AIGW_MASTER_KEY</code>
    erzeugen und speichern. Beim Neustart übernimmt die App alles einmal und
    verschlüsselt die Zugangsdaten.</p>
</div>"""]
    out = []
    if IMPORT_STATE == "imported":
        skipped = "".join(f"<li>{esc(n)}</li>" for n in IMPORT_NOTES)
        out.append(f"""<div class="card attention">
  <h2>Aus der Konfiguration übernommen</h2>
  <p class="hint">Bezugsquellen und Modellnamen werden ab jetzt <b>hier</b> gepflegt.
    Die Variablen <code>AIGW_SUPPLIERS</code>, <code>AIGW_SUPPLIER_KEYS</code> und
    <code>AIGW_ALIASES</code> werden nicht mehr gelesen und können geleert werden.</p>
  {f"<p class='hint'>Nicht übernommen, weil nicht verständlich:</p><ul>{skipped}</ul>" if skipped else ""}
</div>""")
    elif old_set:
        since = catalog.meta(DB, "imported_at")
        out.append(f"""<div class="card attention">
  <p style="margin:0">Die Variablen <code>AIGW_SUPPLIERS</code>, <code>AIGW_SUPPLIER_KEYS</code>
    und <code>AIGW_ALIASES</code> sind noch gesetzt, werden aber
    {f"seit {esc(since)} " if since else ""}nicht gelesen — gepflegt wird hier.
    Sie können geleert werden.</p>
</div>""")
    if not vault.usable(MASTER):
        out.append("""<div class="card attention">
  <p style="margin:0"><code>AIGW_MASTER_KEY</code> fehlt. Quellen <b>ohne</b> Zugangsdaten
    (z. B. ein lokales Ollama) lassen sich pflegen; Zugangsdaten speichert die App
    erst, wenn der Schlüssel gesetzt ist (Portal → Instanz → Konfiguration, „erzeugen“).</p>
</div>""")
    return out


def aliases_card():
    editable = MODE == "app"
    rows = []
    stored = {r["name"]: r for r in catalog.alias_rows(DB)} if editable else {}
    names = sorted(set(ALIASES) | set(stored))
    for name in names:
        alias = ALIASES.get(name)
        if alias:
            targets = []
            for target in alias["targets"]:
                src = SUPPLIERS.get(target["supplier"])
                if src is None:          # zwischen zwei Neuladungen gelesen
                    continue
                targets.append(f'{light_badge(src["light"], short=True)} '
                               f'<code>{esc(src["name"])} · {esc(target["model"])}</code>')
            # Die Farbe eines Alias ist die schlechteste seiner Ziele — einer,
            # der auf einen roten Anbieter ausweichen kann, ist nicht grün,
            # auch wenn sein erstes Ziel es ist.
            worst = light_badge(supply.alias_light(alias, SUPPLIERS, "red"))
            order_listed = alias["order_listed"]
            text = supply.targets_text(alias)
        else:
            targets = [f'<code>{esc(stored[name]["targets"])}</code>']
            worst = '<span class="badge err">unbenutzbar</span>'
            order_listed = bool(stored[name]["order_listed"])
            text = stored[name]["targets"]
        action = ""
        if editable:
            action = (f'<details><summary class="muted">ändern</summary>'
                      f'<form class="grid" method="post" action="alias" style="margin-top:.6rem">'
                      f'<input type="hidden" name="name" value="{esc(name)}">'
                      f'<div><label>Ziele (quelle:modell, …)</label>'
                      f'<input name="targets" type="text" value="{esc(text)}"></div>'
                      f'<div><fieldset><legend>Reihenfolge</legend><label>'
                      f'<input type="checkbox" name="order_listed" value="1"'
                      f'{" checked" if order_listed else ""}> wie aufgeführt</label></fieldset></div>'
                      f'<div><button class="quiet" type="submit">speichern</button></div></form>'
                      f'<form method="post" action="alias-delete" style="margin:.4rem 0 0">'
                      f'<input type="hidden" name="name" value="{esc(name)}">'
                      f'<button class="quiet" type="submit">löschen</button></form></details>')
        order = "wie aufgeführt" if order_listed else "grün vor gelb vor rot"
        rows.append(f'<tr><td><code>{esc(name)}</code></td><td>{worst}</td>'
                    f'<td>{"<br>".join(targets)}</td><td class="muted">{esc(order)}</td>'
                    f'<td>{action}</td></tr>')
    table = (f'<table><tr><th>Modellname</th><th>Ampel</th><th>Bezugsquellen</th>'
             f'<th>Reihenfolge</th><th></th></tr>{"".join(rows)}</table>' if rows else
             '<p class="muted">Noch kein Modellname — ohne ihn kann kein Verbraucher etwas anfragen.</p>')
    form = ""
    if editable:
        known = ", ".join(sorted(r["name"] for r in catalog.supplier_rows(DB))) or "—"
        form = f"""<h2 style="margin-top:1.2rem">Modellname anlegen</h2>
  <form class="grid" method="post" action="alias">
    <div><label for="alias-name">Modellname (das, was ein Client eintippt)</label>
      <input id="alias-name" name="name" type="text" required placeholder="chat-default"></div>
    <div><label for="alias-targets">Ziele: quelle:modell, … (Quellen: {esc(known)})</label>
      <input id="alias-targets" name="targets" type="text" required
        placeholder="tsystems:llama-3.3-70b, groq:llama-3.3-70b-versatile"></div>
    <div><fieldset><legend>Reihenfolge</legend><label>
      <input type="checkbox" name="order_listed" value="1"> wie aufgeführt</label></fieldset></div>
    <div><button type="submit">Anlegen</button></div>
  </form>"""
    return f"""<div class="card">
  <h2>Modellnamen</h2>
  <p class="hint">Ein Verbraucher fragt nach einem Namen von uns, nie nach dem Modell des
    Anbieters. Ausgewichen wird nur innerhalb der hier erklärten Gruppe — und die Ampel
    eines Modellnamens ist die <b>schlechteste</b> seiner Ziele.</p>
  {table}{form}
</div>"""


def suppliers_card():
    editable = MODE == "app"
    rows = []
    if editable:
        entries = [(r["name"], r["url"], r["light"], bool(r["credential"]),
                    r["name"] in SUPPLIERS) for r in catalog.supplier_rows(DB)]
    else:
        entries = [(n, s["url"], s["light"], bool(s["credential"]), True)
                   for n, s in sorted(SUPPLIERS.items())]
    for name, url, light, has_cred, usable in entries:
        cred = ("hinterlegt" if has_cred else '<span class="muted">keine (z. B. lokal)</span>')
        if not usable:
            cred = '<span class="badge err">nicht lesbar</span>'
        action = ""
        if editable:
            action = (f'<details><summary class="muted">ändern</summary>'
                      f'<form class="grid" method="post" action="supplier" style="margin-top:.6rem">'
                      f'<input type="hidden" name="name" value="{esc(name)}">'
                      f'<div><label>Adresse</label>'
                      f'<input name="url" type="text" value="{esc(url)}"></div>'
                      f'{light_radios(light, name)}'
                      f'<div><label>Neue Zugangsdaten (leer = unverändert)</label>'
                      f'<input name="credential" type="password" autocomplete="new-password"></div>'
                      f'<div><fieldset><legend>Zugangsdaten</legend><label>'
                      f'<input type="checkbox" name="drop_credential" value="1"> entfernen'
                      f'</label></fieldset></div>'
                      f'<div><button class="quiet" type="submit">speichern</button></div></form>'
                      f'<form method="post" action="supplier-delete" style="margin:.4rem 0 0">'
                      f'<input type="hidden" name="name" value="{esc(name)}">'
                      f'<button class="quiet" type="submit">löschen</button></form></details>')
        rows.append(f'<tr><td><code>{esc(name)}</code></td>'
                    f'<td>{light_badge(light)}<br>'
                    f'<span class="muted">{esc(supply.LIGHT_RULES.get(light, ""))}</span></td>'
                    f'<td><code>{esc(url)}</code></td><td>{cred}</td><td>{action}</td></tr>')
    table = (f'<table><tr><th>Name</th><th>Ampel</th><th>Adresse</th><th>Zugangsdaten</th>'
             f'<th></th></tr>{"".join(rows)}</table>' if rows else
             '<p class="muted">Noch keine Bezugsquelle.</p>')
    form = ""
    if editable:
        form = f"""<h2 style="margin-top:1.2rem">Bezugsquelle anlegen</h2>
  <form class="grid" method="post" action="supplier">
    <div><label for="src-name">Name</label>
      <input id="src-name" name="name" type="text" required placeholder="groq"></div>
    <div><label for="src-url">Adresse (OpenAI-kompatibel, bis /v1)</label>
      <input id="src-url" name="url" type="text" required
        placeholder="https://api.groq.com/openai/v1"></div>
    {light_radios("red", "neu")}
    <div><label for="src-cred">Zugangsdaten (API-Key des Anbieters)</label>
      <input id="src-cred" name="credential" type="password" autocomplete="new-password"></div>
    <div><button type="submit">Anlegen</button></div>
  </form>
  <p class="hint" style="margin-top:.8rem">Ohne Angabe gilt <b>rot</b> — unbekannte Herkunft
    ist nicht souverän. Eine Quelle auf demselben Knoten wird über eine App-zu-App-Verbindung
    erreichbar (<code>oaap app link add</code>).</p>"""
    return f"""<div class="card">
  <h2>Bezugsquellen</h2>
  <p class="hint">Zugangsdaten werden nur verschlüsselt gespeichert, lassen sich setzen
    oder ersetzen, aber <b>nie wieder anzeigen</b> — weder hier noch in einer Messzeile,
    Fehlermeldung oder der Prüfspur.</p>
  {table}{form}
</div>"""


# ------------------------------------------------------------ Betreiber-Seite

def admin_page(user, roles, host, notice="", secret=None):
    parts = []

    if secret:
        parts.append(f"""<div class="card secretbox">
  <h2>Schlüssel für „{esc(secret['label'])}“ — jetzt kopieren</h2>
  <p class="hint">Dieser Wert wird <b>genau einmal</b> angezeigt. Gespeichert ist
    nur seine Prüfsumme; wer ihn verliert, bekommt einen neuen — nachlesen
    lässt er sich nirgends.</p>
  <pre>{esc(secret['value'])}</pre>
  <p class="hint">Im Client einzutragen als API-Key, mit der Basis-Adresse
    <code>https://{esc(host)}/v1</code>.</p>
</div>""")

    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')

    parts.extend(supply_notes())

    if CONFIG_ERRORS:
        items = "".join(f"<li>{esc(e)}</li>" for e in CONFIG_ERRORS)
        parts.append(f"""<div class="card attention">
  <h2>Stellen, die ich nicht verwenden kann</h2>
  <p class="hint">Alles andere läuft weiter — was hier steht, ist wirkungslos,
    nicht kaputt.</p>
  <ul>{items}</ul>
</div>""")

    parts.append(aliases_card())
    parts.append(suppliers_card())

    # --- Schlüssel
    totals = store.totals(DB)
    rows = []
    for row in store.keys(DB):
        t = totals.get(row["id"])
        used = (int(t["in_tokens"]) + int(t["out_tokens"])) if t else 0
        budget = (f'{fmt_tokens(used)} / {fmt_tokens(row["budget_tokens"])}'
                  if row["budget_tokens"] else f'{fmt_tokens(used)} <span class="muted">/ frei</span>')
        aliases = esc(row["aliases"]) if row["aliases"] else "alle"
        light = light_badge(ceiling_of(row), short=True) + " und besser"
        if pbd_of(row):
            light += ' <span class="badge pbd">pbD freigegeben → nie rot</span>' 
        if row["revoked"]:
            state = '<span class="badge err">widerrufen</span>'
            action = ""
        else:
            state = '<span class="badge">gültig</span>'
            action = (f'<form method="post" action="revoke" style="margin:0">'
                      f'<input type="hidden" name="label" value="{esc(row["label"])}">'
                      f'<button class="quiet" type="submit">widerrufen</button></form>'
                      f'<details><summary class="muted">Kontext ändern</summary>'
                      f'<form class="grid" method="post" action="context" style="margin-top:.6rem">'
                      f'<input type="hidden" name="label" value="{esc(row["label"])}">'
                      f'{context_inputs(row, prefix=row["id"] + "-")}'
                      f'<div><button class="quiet" type="submit">speichern</button></div>'
                      f'</form></details>')
        rows.append(
            f'<tr><td><b>{esc(row["label"])}</b><br>'
            f'<span class="muted">{esc(context_line(row))}</span></td>'
            f'<td>{state}</td><td><code>{aliases}</code></td><td>{light}</td>'
            f'<td>{budget}</td>'
            f'<td>{t["calls"] if t else 0}<br><span class="muted">'
            f'{esc(t["last"]) if t else "noch nie"}</span></td>'
            f'<td>{fmt_bytes(t["bytes_up"] if t else 0)} / '
            f'{fmt_bytes(t["bytes_down"] if t else 0)}</td>'
            f'<td>{action}</td></tr>')
    parts.append(f"""<div class="card">
  <h2>Schlüssel</h2>
  <p class="hint">Wer ein Gateway betreibt, gibt die Erlaubnis — ein Recht wird
    gegeben, nicht gehalten. Ausstellen, Widerrufen und jede Änderung am Kontext
    stehen in der Prüfspur; einzelne Anfragen stehen dort nicht. Ein geänderter
    Kontext gilt <b>ab der nächsten Anfrage</b> — gezählte Anfragen behalten den,
    mit dem sie kamen.</p>
  <table><tr><th>Etikett · Kontext</th><th>Zustand</th><th>Aliasse</th><th>Ampel</th>
    <th>Token</th><th>Aufrufe</th><th>Daten ↑/↓</th><th></th></tr>{"".join(rows) or
    '<tr><td colspan="8" class="muted">Noch kein Schlüssel ausgestellt.</td></tr>'}</table>
</div>""")

    alias_hint = ", ".join(sorted(ALIASES)) or "—"
    parts.append(f"""<div class="card">
  <h2>Schlüssel ausstellen</h2>
  <form class="grid" method="post" action="issue">
    <div><label for="label">Etikett (eindeutig, benennt den Verbraucher)</label>
      <input id="label" name="label" type="text" required placeholder="laptop@joerg"></div>
    {context_inputs()}
    <div><label for="aliases">Aliasse (leer = alle: {esc(alias_hint)})</label>
      <input id="aliases" name="aliases" type="text" placeholder="chat-default,code"></div>
    <div><label for="budget">Budget in Token (0 = ohne)</label>
      <input id="budget" name="budget" type="number" min="0" value="0"></div>
    <div><label for="rate">Anfragen je Minute (0 = Standard {DEFAULT_RATE})</label>
      <input id="rate" name="rate" type="number" min="0" value="0"></div>
    <div><fieldset><legend>Höchstens diese Ampelfarbe</legend>
      <label><input type="radio" name="ceiling" value="green"> grün</label>
      <label><input type="radio" name="ceiling" value="yellow" checked> gelb</label>
      <label><input type="radio" name="ceiling" value="red"> rot</label>
    </fieldset></div>
    <div><fieldset><legend>Personenbezogene Daten</legend>
      <label><input type="checkbox" name="personal_data" value="1"> freigegeben</label>
    </fieldset></div>
    <div><button type="submit">Ausstellen</button></div>
  </form>
  <p class="hint" style="margin-top:.8rem">Ohne Angabe gilt <b>gelb</b> —
    souverän ist das, was passiert, wenn niemand etwas einstellt; <b>rot</b> ist
    eine bewusste Zusatzerlaubnis. Die Freigabe für personenbezogene Daten
    <b>schließt rot aus</b>: Das Gateway darf nicht in die Anfrage sehen und kann
    eine Anfrage deshalb nicht von der anderen unterscheiden — eine Regel, die
    das voraussetzte, wäre ein Versprechen, das wir nicht halten können.</p>
</div>""")

    # --- Verbrauch
    labels = {r["id"]: r["label"] for r in store.keys(DB)}
    rows = []
    for row in store.recent(DB, limit=40):
        rows.append(
            f'<tr><td class="muted">{esc(row["time"])}</td>'
            f'<td>{esc(labels.get(row["key_id"], row["key_id"]))}</td>'
            f'<td><code>{esc(row["alias"])}</code></td>'
            f'<td>{esc(row["supplier"] or "—")}<br>'
            f'<span class="muted">{esc(row["model"] or "")}</span></td>'
            f'<td>{fmt_tokens(row["in_tokens"])} / {fmt_tokens(row["out_tokens"])}</td>'
            f'<td>{fmt_bytes(row["bytes_up"])} / {fmt_bytes(row["bytes_down"])}</td>'
            f'<td>{row["ms"]} ms</td><td>{esc(row["outcome"])}</td></tr>')
    parts.append(f"""<div class="card">
  <h2>Letzte Anfragen</h2>
  <p class="hint">Gezählt wird, nicht mitgeschrieben: Zeit, Schlüssel, Alias,
    <b>die tatsächlich benutzte Quelle</b>, Token-Zahlen, Bytes am Verbraucher,
    Dauer, Ausgang und der Kontext des Schlüssels zu diesem Zeitpunkt.
    Prompts und Antworten stehen in keiner Zeile und in keiner Datei.</p>
  <p><a class="btn" href="auswertung">Auswertung: Grafiken und Schnittstelle</a></p>
  <table><tr><th>Zeit</th><th>Schlüssel</th><th>Alias</th><th>Quelle</th>
    <th>Token ein/aus</th><th>Bytes ↑/↓</th><th>Dauer</th><th>Ausgang</th></tr>{"".join(rows) or
    '<tr><td colspan="8" class="muted">Noch keine Anfrage.</td></tr>'}</table>
</div>""")

    parts.append(f"""<div class="card">
  <h2>So verbindet sich ein Client</h2>
  <p class="hint">Jeder OpenAI-kompatible Client — LM Studio, ein IDE-Plugin,
    eine Bibliothek. Die Modellliste zeigt <b>die Aliasse</b>; welche Quelle
    antwortet, entscheidet dieses Gateway.</p>
  <pre>Basis-Adresse : https://{esc(host)}/v1
API-Key       : der ausgestellte Schlüssel
Modell        : {esc(alias_hint)}

curl https://{esc(host)}/v1/chat/completions \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{{"model":"{esc(sorted(ALIASES)[0] if ALIASES else "chat-default")}",
       "messages":[{{"role":"user","content":"Hallo"}}]}}'</pre>
</div>""")

    return page("Übersicht", "".join(parts), user, roles)


# ------------------------------------------------------------ Auswertung

# Wonach gestapelt und gerankt werden kann. Tag und Monat sind die Zeitachse.
CHART_DIMS = {"cost_center": "Kostenstelle", "account": "Account", "project": "Projekt",
              "application": "Anwendung", "owner": "Verantwortlich", "key": "Schlüssel",
              "model": "Modellname", "supplier": "Bezugsquelle", "outcome": "Ausgang"}
MAX_DAYS = 400


def chart_query(params):
    """Die Auswahl der Seite → Zeitraum, Achsen, Filter; Unbrauchbares mit Hinweis ersetzt."""
    notes = []
    today = datetime.now(timezone.utc).date()

    def day(name, default):
        try:
            return datetime.strptime(params.get(name, ""), "%Y-%m-%d").date()
        except ValueError:
            if params.get(name):
                notes.append(f"„{params[name]}“ ist kein Datum; genommen: {default}.")
            return default

    first, last = day("von", today - timedelta(days=29)), day("bis", today)
    if last < first:
        notes.append("„bis“ lag vor „von“; vertauscht.")
        first, last = last, first

    def choice(name, allowed, default):
        value = params.get(name, default)
        if value not in allowed:
            notes.append(f"„{value}“ gibt es für {name} nicht; genommen: {default}.")
            return default
        return value

    step = choice("schritt", ("day", "month"), "day")
    if step == "day" and (last - first).days >= MAX_DAYS:
        step = "month"
        notes.append(f"Mehr als {MAX_DAYS} Tage — je Monat gezeichnet.")
    by = choice("nach", CHART_DIMS, "cost_center")
    top = choice("top", CHART_DIMS, "key")
    metric = choice("mass", charts.METRIC_LABELS, "calls")
    filters = {d: [params[d]] for d in CHART_DIMS if d in params and params[d] != "*"}
    frm = report.stamp(datetime(first.year, first.month, first.day))
    to = report.stamp(datetime(last.year, last.month, last.day) + timedelta(days=1))
    return dict(first=first, last=last, frm=frm, to=to, step=step, by=by, top=top,
                metric=metric, filters=filters, notes=notes)


def options(values, current, labels=None):
    out = []
    for value in values:
        text = (labels or {}).get(value, charts.label(value))
        out.append(f'<option value="{esc(value)}"{" selected" if value == current else ""}>'
                   f'{esc(text)}</option>')
    return "".join(out)


def charts_page(user, roles, host, params):
    q = chart_query(params)
    frm, to, step, by, top, metric, filters = (q[k] for k in (
        "frm", "to", "step", "by", "top", "metric", "filters"))
    # Drei Fragen, eine Abfragefunktion — dieselbe, die /report/v1/usage beantwortet.
    over_time = report.usage(DB, frm, to, [step, by], filters)
    ranked = report.usage(DB, frm, to, [top], filters)
    outcomes = report.usage(DB, frm, to, ["outcome"], filters)
    t = ranked["totals"]

    selects = []
    for dim, text in CHART_DIMS.items():
        current = filters.get(dim, ["*"])[0]
        values = report.values_of(DB, dim, frm, to)
        if current != "*" and current not in values:
            values.append(current)
        selects.append(f'<div><label for="f-{dim}">{esc(text)}</label>'
                       f'<select id="f-{dim}" name="{dim}"><option value="*">alle</option>'
                       f'{options(values, current)}</select></div>')
    form = f"""<div class="card">
  <form class="grid" method="get" action="auswertung">
    <div><label for="von">von (UTC)</label>
      <input id="von" name="von" type="date" value="{q['first']}"></div>
    <div><label for="bis">bis einschließlich</label>
      <input id="bis" name="bis" type="date" value="{q['last']}"></div>
    <div><label for="schritt">je</label><select id="schritt" name="schritt">
      {options(["day", "month"], step, {"day": "Tag", "month": "Monat"})}</select></div>
    <div><label for="mass">Maß</label><select id="mass" name="mass">
      {options(list(charts.METRIC_LABELS), metric, charts.METRIC_LABELS)}</select></div>
    <div><label for="nach">gestapelt nach</label><select id="nach" name="nach">
      {options(list(CHART_DIMS), by, CHART_DIMS)}</select></div>
    <div><label for="top">Rangliste nach</label><select id="top" name="top">
      {options(list(CHART_DIMS), top, CHART_DIMS)}</select></div>
    {"".join(selects)}
    <div><button type="submit">Anzeigen</button></div>
  </form>
</div>"""

    api = {"from": frm, "to": to, "group_by": f"{step},{by}"}
    api_qs = urlencode(list(api.items()) + [(d, v[0]) for d, v in sorted(filters.items())],
                       safe=":,")
    notes = "".join(f"<li>{esc(n)}</li>" for n in q["notes"])
    complete = ("endgültig" if over_time["complete"] else
                f"<b>noch nicht endgültig</b> — endgültig bis {esc(over_time['complete_until'])}")
    gaps = []
    if t["calls_without_token_counts"]:
        gaps.append(f"{charts.fmt_num(t['calls_without_token_counts'])} Aufrufe ohne Token-Zahlen "
                    "(Quelle nannte keine, oder abgewiesen) — sie zählen bei Token als 0")
    if t["calls_without_bytes"]:
        gaps.append(f"{charts.fmt_num(t['calls_without_bytes'])} Aufrufe von vor 0.3 ohne Bytes")
    tiles = "".join(f'<div class="tile"><b>{v}</b><span>{esc(k)}</span></div>' for k, v in (
        ("Aufrufe", charts.fmt_num(t["calls"])),
        ("Token ein", charts.fmt_num(t["in_tokens"])),
        ("Token aus", charts.fmt_num(t["out_tokens"])),
        ("Bytes hoch", charts.fmt_metric(t["bytes_up"], "bytes_up")),
        ("Bytes runter", charts.fmt_metric(t["bytes_down"], "bytes_down"))))
    body = f"""<p><a href="./">← Übersicht</a></p>
<h1>Auswertung</h1>
<p class="hint">Nur intern. Volumen, keine Preise. Gruppiert wird nach dem Kontext, den ein
  Schlüssel <b>zum Zeitpunkt der Anfrage</b> hatte; die Zeit einer Anfrage ist ihr Ende (UTC).</p>
{form}
{f'<div class="card attention"><ul>{notes}</ul></div>' if notes else ''}
<div class="card">
  <div class="tiles">{tiles}</div>
  <p class="hint" style="margin-top:.8rem">{q['first']} bis {q['last']} · {complete}
    {'· ' + esc('; '.join(gaps)) if gaps else ''}</p>
</div>
<div class="card">
  <h2>{esc(charts.METRIC_LABELS[metric])} je {"Tag" if step == "day" else "Monat"},
    gestapelt nach {esc(CHART_DIMS[by])}</h2>
  {charts.stacked_bars(over_time, step, by, metric)}
</div>
<div class="card">
  <h2>Rangliste: {esc(CHART_DIMS[top])} nach {esc(charts.METRIC_LABELS[metric])}</h2>
  {charts.top_list(ranked, top, metric)}
</div>
<div class="card">
  <h2>Ausgänge</h2>
  <p class="hint">Hier wird sichtbar, wie viel ohne Token-Zahlen beantwortet wurde — und
    wie viel gar nicht.</p>
  {charts.outcome_list(outcomes)}
</div>
<div class="card">
  <h2>Dieselben Zahlen für ein Abrechnungssystem</h2>
  <p class="hint">Die Grafiken oben und diese Schnittstelle lesen über dieselbe Abfrage.
    Abgefragt wird mit einem Plattform-API-Schlüssel für einen Maschinen-Benutzer mit der
    Rolle <code>admin</code> (Portal → Zugänge), nicht mit einem Gateway-Schlüssel.
    <code>to</code> ist ausschließlich; mit <code>format=csv</code> als CSV.</p>
  <pre>curl -H "Authorization: Bearer $OAAP_API_KEY" \\
  "https://{esc(host)}/report/v1/usage?{esc(api_qs)}"</pre>
</div>"""
    return page("Auswertung", body, user, roles)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-ai-gateway/" + VERSION

    # ---- Werkzeug

    def log_message(self, fmt, *args):
        # Nach stdout, wie der Contract es verlangt — und ohne Fragezeichen:
        # Pfade dieses Dienstes tragen keine Inhalte.
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def client_addr(self):
        # Hinter der Plattform ist der Peer das Gateway; die vom Gateway
        # gesetzte Kopfzeile ist dann die belastbare Angabe. Die
        # eigentliche Bremse steht ohnehin in der Plattform (RFC-0010).
        return (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def send_json(self, status, doc, extra_headers=()):
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_error_doc(self, status, message, code, extra_headers=()):
        self.send_raw_json(status, error_bytes(message, code), extra_headers)

    def send_raw_json(self, status, payload, extra_headers=()):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in extra_headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)

    def refuse_counted(self, row, alias, supplier, model, ms, outcome, up_bytes,
                       status, message, code, extra_headers=()):
        """Eine Ablehnung, die gezählt wird: erst die Messzeile, dann die Antwort.

        Die Fehlerantwort steht vorher als Bytes fest, damit `bytes_down`
        genau das ist, was der Verbraucher bekommt.
        """
        payload = error_bytes(message, code)
        store.record(DB, row, alias, supplier, model, None, None, ms, outcome,
                     up_bytes, len(payload))
        self.send_raw_json(status, payload, extra_headers)

    def read_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > MAX_BODY:
            return None
        return self.rfile.read(length)

    def identity(self):
        return (self.headers.get("X-OAAP-User", ""), self.headers.get("X-OAAP-Roles", ""))

    def is_admin(self):
        _, roles = self.identity()
        return "admin" in [r.strip() for r in roles.split(",")]

    # ---- Schlüsselprüfung

    def authorize(self):
        """Gibt die Schlüsselzeile zurück oder beantwortet die Anfrage selbst."""
        addr = self.client_addr()
        if failing_hard(addr):
            self.send_error_doc(429, "Zu viele Fehlversuche. Später erneut.",
                                "too_many_requests", [("Retry-After", "60")])
            return None
        header = self.headers.get("Authorization", "")
        value = header[7:].strip() if header[:7].lower() == "bearer " else ""
        row = store.find(DB, value)
        if not row or row["revoked"]:
            note_failure(addr)
            self.send_json(403, DENIED)
            return None
        return row

    # ---- GET

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION})
            return
        if path == "/v1/models":
            row = self.authorize()
            if row is None:
                return
            ceiling, pbd = ceiling_of(row), pbd_of(row)
            data = []
            for name in sorted(allowed_aliases(row)):
                # Die Farbe ist die schlechteste unter den Zielen, die
                # dieser Schlüssel erreichen darf. Ein Alias, der auf einen
                # roten Anbieter ausweichen kann, ist nicht grün — sonst
                # wäre die Ampel eine Beruhigung statt einer Auskunft.
                alias = ALIASES.get(name)
                light = supply.alias_light(alias, SUPPLIERS, ceiling, pbd) if alias else ""
                if not light:
                    # Für diesen Schlüssel gibt es kein erreichbares Ziel.
                    # Ihn trotzdem anzubieten hieße, dem Client eine Wahl
                    # hinzulegen, die beim ersten Klick scheitert.
                    continue
                data.append({"id": name, "object": "model", "created": 0,
                             "owned_by": "oaap", "oaap_light": light,
                             "oaap_light_meaning": supply.LIGHT_LABELS[light],
                             "oaap_data_rule": supply.LIGHT_RULES[light]})
            self.send_json(200, {"object": "list", "data": data})
            return
        if path == "/v1/usage":
            row = self.authorize()
            if row is None:
                return
            totals = store.totals(DB).get(row["id"])
            self.send_json(200, {
                "key": row["label"],
                "account": row["account"], "tenant": row["tenant"],
                "calls": int(totals["calls"]) if totals else 0,
                "input_tokens": int(totals["in_tokens"]) if totals else 0,
                "output_tokens": int(totals["out_tokens"]) if totals else 0,
                "bytes_up": int(totals["bytes_up"]) if totals else 0,
                "bytes_down": int(totals["bytes_down"]) if totals else 0,
                "budget_tokens": int(row["budget_tokens"]),
                "ceiling": ceiling_of(row),
                "personal_data_released": pbd_of(row),
                "recent": [{"time": r["time"], "alias": r["alias"],
                            "supplier": r["supplier"], "model": r["model"],
                            "light": SUPPLIERS.get(r["supplier"], {}).get("light", ""),
                            "input_tokens": r["in_tokens"], "output_tokens": r["out_tokens"],
                            "bytes_up": r["bytes_up"], "bytes_down": r["bytes_down"],
                            "ms": r["ms"], "outcome": r["outcome"]}
                           for r in store.recent(DB, row["id"], 20)],
            })
            return
        if path.startswith("/v1"):
            self.send_error_doc(404, "Diesen Pfad kennt das Gateway nicht.", "unknown_path")
            return

        if path.startswith("/report"):
            self.handle_report(path)
            return

        # Betreiber-Sicht
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff",
                                     '<div class="card"><p>Diese Seite ist der '
                                     'Serververwaltung vorbehalten.</p></div>', "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        if path == "/auswertung":
            query = self.path.partition("?")[2]
            self.send_html(200, charts_page(user, roles, host,
                                            dict(parse_qsl(query, keep_blank_values=True))))
            return
        self.send_html(200, admin_page(user, roles, host))

    def handle_report(self, path):
        """`/report/v1/…` — für ein Abrechnungssystem, nicht für einen Browser (RFC-0064 §6).

        Die Plattform hat schon geprüft (Route `/`, Rolle `admin`); eine
        Maschine kommt mit einem Plattform-API-Schlüssel und erscheint
        hier mit denselben Kopfzeilen wie ein Mensch (RFC-0027). Ein
        Gateway-Schlüssel (`sk-oaap-…`) öffnet diesen Weg nie: Er wird
        hier gar nicht gelesen.
        """
        if not self.is_admin():
            self.send_error_doc(403, "Nur für die Rolle admin — ein Abrechnungssystem fragt "
                                     "mit einem Plattform-API-Schlüssel (Portal → Zugänge).",
                                "forbidden")
            return
        if path == "/report/v1/keys":
            self.send_json(200, report.keys_doc(DB, lambda raw: json.loads(raw) if raw else {}))
            return
        if path != "/report/v1/usage":
            self.send_error_doc(404, "Bekannt sind /report/v1/usage und /report/v1/keys.",
                                "unknown_path")
            return
        try:
            frm, to, group_by, filters, fmt = report.parse_query(
                parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            doc = report.usage(DB, frm, to, group_by, filters)
        except ValueError as exc:
            self.send_error_doc(400, str(exc), "bad_query")
            return
        if fmt == "json":
            self.send_json(200, doc)
            return
        body = report.to_csv(doc).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # In CSV gibt es keinen Platz dafür — und ohne diese Angabe wüsste
        # ein Abrechnungslauf nicht, ob der Zeitraum noch wächst.
        self.send_header("X-OAAP-Complete-Until", doc["complete_until"])
        self.send_header("X-OAAP-Complete", "true" if doc["complete"] else "false")
        self.end_headers()
        self.wfile.write(body)

    # ---- POST

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/v1/chat/completions", "/v1/embeddings"):
            self.handle_inference(path)
            return
        if path in ("/issue", "/revoke", "/context", "/supplier", "/supplier-delete",
                    "/alias", "/alias-delete"):
            self.handle_admin_post(path)
            return
        self.send_error_doc(404, "Diesen Pfad kennt das Gateway nicht.", "unknown_path")

    # ---- Betreiber-Handlungen

    def handle_admin_post(self, path):
        # Der Rumpf wird gelesen, BEVOR abgelehnt wird: Ein ungelesener Rest
        # bliebe auf der Keep-Alive-Verbindung liegen und würde als nächste
        # Anfrage gelesen — oder die Verbindung endet mit einem Reset, und
        # der Aufrufer sieht statt der 403 einen Abbruch.
        raw = self.read_body() or b""
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Nicht erlaubt.</p>'
                                     '</div>', "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        form = {}
        multi = {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            if not pair:
                continue
            name, _, value = pair.partition("=")
            from urllib.parse import unquote_plus
            name, value = unquote_plus(name), unquote_plus(value)
            form[name] = value
            multi.setdefault(name, []).append(value)

        notice, secret = "", None
        if path == "/issue":
            ceiling = form.get("ceiling") or supply.DEFAULT_CEILING
            if ceiling not in supply.LIGHT_RANK:
                ceiling = supply.DEFAULT_CEILING
            personal_data = form.get("personal_data") == "1"
            aliases = [a.strip() for a in form.get("aliases", "").split(",") if a.strip()]
            unknown = [a for a in aliases if a not in ALIASES]
            if unknown:
                notice = ("Diese Aliasse gibt es nicht: " + ", ".join(unknown) +
                          ". Kein Schlüssel ausgestellt.")
            else:
                try:
                    value, _ = store.issue(
                        DB, form.get("label", ""), user, aliases=aliases,
                        ceiling=ceiling, personal_data=personal_data,
                        budget_tokens=_int(form.get("budget")),
                        rate_per_min=_int(form.get("rate")),
                        **{name: form.get(name, "") for name in store.CONTEXT_FIELDS})
                    secret = {"label": form.get("label", ""), "value": value}
                except ValueError as exc:
                    notice = str(exc)
        elif path in ("/supplier", "/supplier-delete", "/alias", "/alias-delete"):
            notice = self.change_supply(path, form, user)
        elif path == "/context":
            label = form.get("label", "")
            try:
                changed = store.update_context(
                    DB, label, user,
                    **{name: form[name] for name in store.CONTEXT_FIELDS if name in form})
                notice = (f"Kontext von „{label}“ geändert ({', '.join(CONTEXT_LABELS[n] for n in changed)}) "
                          "— gilt ab der nächsten Anfrage." if changed
                          else f"Kontext von „{label}“ unverändert.")
            except ValueError as exc:
                notice = str(exc)
        else:
            label = form.get("label", "")
            notice = (f"Schlüssel „{label}“ widerrufen — ab sofort abgelehnt."
                      if store.revoke(DB, label, user)
                      else f"Nichts zu widerrufen: „{label}“ ist unbekannt oder schon widerrufen.")
        self.send_html(200, admin_page(user, roles, host, notice=notice, secret=secret))

    def change_supply(self, path, form, user):
        """Bezugsquellen und Modellnamen ändern — wirksam ohne Neustart."""
        if MODE != "app":
            return ("Gesperrt, solange die Übernahme aus der Konfiguration wartet "
                    "(AIGW_MASTER_KEY fehlt).")
        name = form.get("name", "").strip()
        try:
            if path == "/supplier":
                done = catalog.set_supplier(
                    DB, user, MASTER, name, form.get("url", ""),
                    form.get("light") or supply.DEFAULT_LIGHT,
                    credential=form.get("credential", ""),
                    drop_credential=form.get("drop_credential") == "1")
                notice = f"Bezugsquelle „{name}“ {done}."
            elif path == "/supplier-delete":
                catalog.delete_supplier(DB, user, name)
                notice = f"Bezugsquelle „{name}“ gelöscht."
            elif path == "/alias":
                done = catalog.set_alias(DB, user, name, form.get("targets", ""),
                                         form.get("order_listed") == "1")
                notice = f"Modellname „{name}“ {done}."
            else:
                affected = catalog.delete_alias(DB, user, name)
                notice = f"Modellname „{name}“ gelöscht."
                if affected:
                    notice += (" Diese Schlüssel waren auf ihn beschränkt und erreichen ihn "
                               "nicht mehr: " + ", ".join(affected) + ".")
        except ValueError as exc:
            return str(exc)
        reload_supply()
        return notice

    # ---- Der eigentliche Dienst

    def handle_inference(self, path):
        row = self.authorize()
        if row is None:
            return
        raw = self.read_body()
        if raw is None:
            self.send_error_doc(400, "Anfrage ohne oder mit zu großem Rumpf.", "bad_body")
            return
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError
        except Exception:
            self.send_error_doc(400, "Der Rumpf ist kein JSON-Objekt.", "bad_body")
            return

        alias_name = str(payload.get("model") or "").strip()
        permitted = allowed_aliases(row)
        if alias_name not in permitted:
            # Ein falscher Modellname ist ein Konfigurationsfehler, und
            # Konfigurationsfehler verdienen eine Antwort, die sie behebt.
            self.send_error_doc(
                400,
                f"„{alias_name or '(kein Modell angegeben)'}“ ist für diesen Schlüssel "
                f"kein erlaubter Alias. Erlaubt: {', '.join(sorted(permitted)) or 'keiner'}.",
                "unknown_model")
            return

        rate = row["rate_per_min"] or DEFAULT_RATE
        ok, wait = LIMITER.allow(row["id"], rate)
        if not ok:
            self.send_error_doc(429, f"Mehr als {rate} Anfragen je Minute für diesen Schlüssel.",
                                "rate_limit", [("Retry-After", str(wait))])
            return

        if row["budget_tokens"]:
            used = store.spent(DB, row["id"])
            if used >= row["budget_tokens"]:
                # Kein Retry-After: Warten hilft hier nicht, nur ein
                # neues Budget. Eine Zeitangabe wäre eine Unwahrheit.
                self.refuse_counted(
                    row, alias_name, "", "", 0, "budget", len(raw),
                    429, f"Budget erschöpft ({used} von {row['budget_tokens']} Token). "
                         "Der Betreiber kann es erhöhen.", "budget_exhausted")
                return

        # Seit Stufe 2 kann ein Modellname zwischen Prüfung und Gebrauch
        # gelöscht werden; dann ist er so unbekannt wie jeder andere.
        alias = ALIASES.get(alias_name)
        if alias is None:
            self.send_error_doc(400, f"„{alias_name}“ gibt es nicht mehr.", "unknown_model")
            return
        ceiling, pbd = ceiling_of(row), pbd_of(row)
        targets = supply.candidates(alias, SUPPLIERS, ceiling, pbd)
        if not targets:
            reasons = supply.blocked_reasons(alias, SUPPLIERS, ceiling, pbd)
            if reasons:
                message = (f"Für „{alias_name}“ bleibt jede Quelle gesperrt: "
                           f"{'; '.join(reasons)}. Der Betreiber kann die Obergrenze "
                           "heraufsetzen oder eine andere Quelle eintragen.")
                code = "light_not_permitted"
            else:
                message = f"Für „{alias_name}“ ist keine Bezugsquelle erreichbar."
                code = "no_supplier"
            self.refuse_counted(row, alias_name, "", "", 0, "no_supplier", len(raw),
                                503, message, code)
            return

        stream = bool(payload.get("stream"))
        last_error = ""
        for index, target in enumerate(targets):
            src, model = target["supplier"], target["model"]
            body = dict(payload)
            body["model"] = model                      # Alias → Modell der Quelle
            started = time.monotonic()
            if stream:
                done = self.relay_stream(row, alias_name, src, model, path, body, started,
                                         len(raw))
            else:
                done = self.relay_once(row, alias_name, src, model, path, body, started,
                                       len(raw))
            if done is True:
                return
            last_error = done or last_error
            # Ausgewichen wird nur innerhalb der erklärten Gruppe, und der
            # Wechsel steht anschließend in der Messzeile.
            if index + 1 < len(targets):
                sys.stdout.write(f"ausweichen: {src['name']} -> "
                                 f"{targets[index + 1]['supplier']['name']} ({last_error})\n")

        self.refuse_counted(row, alias_name, targets[-1]["supplier"]["name"],
                            targets[-1]["model"], int((time.monotonic() - started) * 1000),
                            "upstream_failed", len(raw),
                            502, f"Keine Bezugsquelle für „{alias_name}“ hat geantwortet "
                                 f"({last_error}).", "upstream_failed")

    def relay_once(self, row, alias_name, src, model, path, body, started, up_bytes):
        up, counts = relay.call_json(src, path, body, timeout=TIMEOUT)
        ms = int((time.monotonic() - started) * 1000)
        if up.status == 0 or up.status >= 500:
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            # Ein 4xx ist die Antwort auf **diese** Anfrage — Ausweichen
            # würde denselben Fehler noch einmal erzeugen.
            store.record(DB, row, alias_name, src["name"], model,
                         counts[0], counts[1], ms, f"upstream_{up.status}",
                         up_bytes, len(up.body))
            self.send_response(up.status)
            self.send_header("Content-Type", up.content_type or "application/json")
            self.send_header("Content-Length", str(len(up.body)))
            self.end_headers()
            self.wfile.write(up.body)
            return True
        store.record(DB, row, alias_name, src["name"], model,
                     counts[0], counts[1], ms,
                     "ok" if counts[0] is not None else "ok_no_counts",
                     up_bytes, len(up.body))
        self.send_response(200)
        self.send_header("Content-Type", up.content_type or "application/json")
        self.send_header("Content-Length", str(len(up.body)))
        self.end_headers()
        self.wfile.write(up.body)
        return True

    def relay_stream(self, row, alias_name, src, model, path, body, started, up_bytes):
        up, asked_usage = relay.open_stream(src, path, body, timeout=TIMEOUT)
        if up.status == 0 or up.status >= 500:
            up.close()
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            payload = up.body or b'{"error":{"message":"upstream error"}}'
            up.close()
            store.record(DB, row, alias_name, src["name"], model, None, None,
                         int((time.monotonic() - started) * 1000), f"upstream_{up.status}",
                         up_bytes, len(payload))
            self.send_response(up.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return True

        self.send_response(200)
        self.send_header("Content-Type", up.content_type or "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Gezählt wird der Nutzinhalt, nicht die Chunk-Rahmung — dieselbe
        # Größe, die der Verbraucher ohne Strom als Rumpf bekommen hätte.
        sent = [0]

        def write(chunk):
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()
            sent[0] += len(chunk)

        outcome = "ok"
        in_tokens = out_tokens = None
        try:
            in_tokens, out_tokens = relay.pump(up, write)
        except Exception as exc:
            outcome = f"stream_broken:{type(exc).__name__}"
        finally:
            up.close()
        if outcome == "ok" and in_tokens is None:
            # Ehrlich statt geschätzt: Die Quelle hat keine Zahlen genannt.
            outcome = "ok_no_counts" if asked_usage else "ok_no_usage_support"
        # Erst die Bücher schließen, dann den Strom beenden. Andersherum
        # wäre die Messzeile ein Wettlauf gegen den Client: Er hätte die
        # Antwort schon vollständig, während der Verbrauch noch nirgends
        # steht — und genau in diesem Fenster fällt ein Prozess aus.
        store.record(DB, row, alias_name, src["name"], model, in_tokens, out_tokens,
                     int((time.monotonic() - started) * 1000), outcome, up_bytes, sent[0])
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        return True


def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------- Kommandozeile

def cli(argv):
    """Für kopflose Knoten: `docker exec <container> python3 /srv/app.py key ...`."""
    context_opts = ("[--owner X] [--account X] [--project X] [--cost-center X] "
                    "[--application X] [--labels name=wert,...]")
    if len(argv) < 2 or argv[0] != "key":
        print("Aufruf: app.py key issue <etikett> [--aliases a,b] "
              "[--ceiling green|yellow|red] [--personal-data ja] [--budget N] "
              f"[--rate N] {context_opts} | key list | key revoke <etikett> "
              f"| key context <etikett> {context_opts}")
        return 2
    action = argv[1]

    def options():
        opts, i = {}, 3
        while i + 1 < len(argv):
            if argv[i].startswith("--"):
                opts[argv[i][2:]] = argv[i + 1]
            i += 2
        return opts

    def context_of(opts):
        # --cost-center auf der Kommandozeile, cost_center in den Büchern.
        return {name: opts[name.replace("_", "-")] for name in store.CONTEXT_FIELDS
                if name.replace("_", "-") in opts}

    if action == "list":
        for row in store.keys(DB):
            print(f'{row["label"]:24} {row["created"]} '
                  f'{"WIDERRUFEN" if row["revoked"] else "gültig":10} '
                  f'aliasse={row["aliases"] or "alle"} bis={row["ceiling"]} '
                  f'pbD={"freigegeben" if row["personal_data"] else "nein"} '
                  f'| {context_line(row)}')
        return 0
    if action == "revoke" and len(argv) > 2:
        print("widerrufen" if store.revoke(DB, argv[2], "cli") else "unbekannt oder schon widerrufen")
        return 0
    if action == "context" and len(argv) > 2:
        try:
            changed = store.update_context(DB, argv[2], "cli", **context_of(options()))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print("geändert: " + ", ".join(changed) if changed else "unverändert")
        return 0
    if action == "issue" and len(argv) > 2:
        opts = options()
        try:
            value, _ = store.issue(
                DB, argv[2], "cli",
                aliases=[a for a in opts.get("aliases", "").split(",") if a],
                ceiling=(opts.get("ceiling") if opts.get("ceiling") in supply.LIGHT_RANK
                         else supply.DEFAULT_CEILING),
                personal_data=opts.get("personal-data", "").lower() in ("ja", "yes", "1"),
                budget_tokens=_int(opts.get("budget")), rate_per_min=_int(opts.get("rate")),
                **context_of(opts))
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(value)
        print("Dieser Wert wird nicht wieder angezeigt.", file=sys.stderr)
        return 0
    print("Unbekannter Aufruf.")
    return 2


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    for problem in CONFIG_ERRORS:
        sys.stdout.write(f"Konfiguration: {problem}\n")
    sys.stdout.write(
        f"OAAP KI-Gateway {VERSION} auf Port {PORT} — "
        f"{len(SUPPLIERS)} Bezugsquelle(n), {len(ALIASES)} Alias(se)\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
