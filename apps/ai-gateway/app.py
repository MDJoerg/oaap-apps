"""KI-Gateway — eine Bezugsquelle für alle, ein Schlüssel je Verbraucher.

Umsetzung der Capability `oaap.ai.gateway` 0.5 (RFC-0023, RFC-0064). Das Gateway
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
- `/` ist `admin` — die Betreiber-Sicht in Reitern (0.6): Dashboard,
  Letzte Anfragen, Auswertung (`/auswertung`), Schlüssel, Kohorten
  (Schlüssel auf Zeit für Schulungen), Modelle, Bezugsquellen mit
  Verbindungstest und die Schnittstelle für externe Berichte
  (`/report/v1/…`, RFC-0064 §6) — die eine Maschine mit einem
  Plattform-API-Schlüssel abfragt, nicht mit einem Gateway-Schlüssel. Die Plattform
  liefert die geprüfte Identität als Kopfzeile (App Deployment
  Contract); einen eigenen Login gibt es hier so wenig wie in jeder
  anderen OAAP-App.
- Seit 0.10.0 (RFC-0104): `/oaap/setup`, die Einrichtungstür für den
  Aufbau aus einem Profil (setupdoor.py, nur mit OAAP_SETUP_KEY), und auf
  `/v1` die Freigabe für einen Chat, der durch die via-Tür der Plattform
  ohne API-Schlüssel kommt (`platform_client`, `authorize`).

Die Regel, die alles trägt (Spec §6): **In keinem Protokoll stehen
jemals Prompts oder Antworten.** Gezählt wird, nicht mitgeschrieben —
es gibt nicht einmal eine Spalte, in die ein Prompt passen würde.

Gebaut als gewöhnliche OAAP-App: ein HTTP-Port, Persistenz nur unter
dem deklarierten Mount, Konfiguration über deklarierte Variablen, Logs
nach stdout, Gesundheitspfad, nur Standardbibliothek.
"""
import base64
import html
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode

import assets
import catalog
import charts
import cohort
import dash
import props
import relay
import report
import setupdoor
import store
import templates
import supply
import vault

import appframe as af
import mcpdoor

VERSION = "0.13.0"
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
    learn_cached()


# RFC-0106 §2.3: was eine Quelle in ihrer Modellliste zuletzt über ihre
# Modelle sagte, {quelle: {modell: {prop: wert}}}. Nur im Speicher -- in die
# Bücher kommt davon, was ein Ziel eines Modellnamens ist (learn).
LISTED = {}
OLLAMA_SHOW_MAX = 20


def learn(name, entries):
    """Die Angaben der Modellliste für die Ziele, die Modellnamen benutzen."""
    LISTED[name] = {e["id"]: e["props"] for e in entries if e.get("props")}
    used = {t["model"] for a in ALIASES.values() for t in a["targets"] if t["supplier"] == name}
    for model in sorted(used & set(LISTED[name])):
        props.note(DB, name, model, LISTED[name][model], "supplier")
    # Ollama nennt in /v1/models nur Kennungen (`owned_by: library`), sagt aber
    # auf /api/show mehr. Gefragt wird nur für Ziele, über die noch gar nichts
    # in den Büchern steht -- nicht bei jedem minütlichen Test aufs Neue.
    src = SUPPLIERS.get(name)
    owners = {e["id"]: e.get("owner", "") for e in entries}
    if src is None:
        return
    tab = props.table(DB)
    for model in sorted(used - set(LISTED[name]))[:OLLAMA_SHOW_MAX]:
        if owners.get(model) == "library" and (name, model) not in tab:
            found = relay.ollama_show(src, model)
            if found:
                props.note(DB, name, model, found, "supplier")


def learn_cached():
    """Nach jeder Änderung der Modellnamen: ein neues Ziel bekommt sofort,
    was die Quelle beim letzten Abruf über es sagte (etwa beim Anlegen aus
    der Modellliste)."""
    if MODE != "app":
        return
    for name, listed in list(LISTED.items()):
        for a in ALIASES.values():
            for t in a["targets"]:
                if t["supplier"] == name and t["model"] in listed:
                    props.note(DB, name, t["model"], listed[t["model"]], "supplier")


reload_supply()

# RFC-0087 §8.2: der letzte Versuch je Bezugsquelle, für die Tür /oaap/assets
SEEN = assets.Seen()

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


LIGHT_DE = {"green": "grün", "yellow": "gelb", "red": "rot"}

# 0.9.0 (RFC-0101 §2.2): die Ampel eines Gesprächs als zusätzliche Decke DIESER
# Anfrage. Sie kann die Decke des Schlüssels nur verschärfen, nie lockern: ein
# Chat, dessen Person „sensible Daten“ gewählt hat, schickt `green`, und dann
# bleibt ein gelber Weg zu, auch wenn der Schlüssel bis gelb darf.
LIGHT_HEADER = "X-OAAP-Light"


def asked_light(value):
    """(Farbe oder '', Fehlersatz oder ''). Ohne Kopfzeile: keine zusätzliche Decke.

    Ein unbekannter Wert wird abgelehnt, nicht übergangen -- wer eine Decke
    verlangt, soll nicht stumm ohne sie weiterarbeiten."""
    v = (value or "").strip().lower()
    if not v:
        return "", ""
    if v not in supply.LIGHT_RANK:
        return "", f"{LIGHT_HEADER} kennt nur green, yellow oder red, nicht „{v[:20]}“."
    return v, ""


def request_ceiling(row, asked):
    """Die Decke dieser Anfrage: die strengere aus Schlüssel und Gespräch."""
    ceiling = ceiling_of(row)
    if asked and supply.LIGHT_RANK[asked] < supply.LIGHT_RANK.get(ceiling, 9):
        return asked
    return ceiling


def unreachable_aliases(names, ceiling, pbd):
    """L14 (0.8.1): die ausdrücklich genannten Modellnamen, die ein Schlüssel
    mit dieser Obergrenze nie erreicht -- je Name die Gründe. Vorher wurde so
    ein Schlüssel stumm ausgestellt; /v1/models bot die Namen nur nicht an."""
    out = {}
    for n in names:
        alias = ALIASES.get(n)
        if alias and not supply.alias_light(alias, SUPPLIERS, ceiling, pbd):
            out[n] = supply.blocked_reasons(alias, SUPPLIERS, ceiling, pbd) or ["kein Ziel erreichbar"]
    return out


def unreachable_note(label, unreachable):
    """Ein Satz für die Seite, oder ''."""
    if not unreachable:
        return ""
    parts = []
    for n, reasons in unreachable.items():
        words = "; ".join(re.sub(r"\b(green|yellow|red)\b", lambda m: LIGHT_DE[m.group(1)], r)
                          for r in reasons)
        parts.append(f"{n} ({words})")
    return (f"Achtung: „{label}“ erreicht diese Modellnamen nie: " + ", ".join(parts)
            + ". Die Obergrenze des Schlüssels heben oder die Modellnamen weglassen -- "
            "/v1/models bietet sie ihm nicht an, eine Anfrage darauf wird abgelehnt.")


def key_models(row, ceiling=None):
    """[(name, alias, ampel)] -- was dieser Schlüssel wirklich benutzen kann.

    Die eine Regel hinter /v1/models und den Vorlagen (RFC-0106 D8): Die
    Farbe ist die schlechteste unter den Zielen, die dieser Schlüssel
    erreichen darf -- ein Alias, der auf einen roten Anbieter ausweichen
    kann, ist nicht grün. Ein Name ohne erreichbares Ziel fehlt: ihn
    anzubieten hieße, dem Client eine Wahl hinzulegen, die beim ersten
    Klick scheitert.
    """
    ceiling = ceiling_of(row) if ceiling is None else ceiling
    pbd = pbd_of(row)
    out = []
    for name in sorted(allowed_aliases(row)):
        alias = ALIASES.get(name)
        light = supply.alias_light(alias, SUPPLIERS, ceiling, pbd) if alias else ""
        if light:
            out.append((name, alias, light))
    return out


def allowed_aliases(row):
    """Leere Angabe heißt: alle konfigurierten Aliasse."""
    raw = [a for a in (row["aliases"] or "").split(",") if a]
    if not raw:
        return list(ALIASES)
    return [a for a in raw if a in ALIASES]


# ------------------------------------------------------------------- Optik

APP_NAME = "KI-Gateway"
PRODUCT = "ai-gateway"

# Nur, was dieser App eigen ist (Kacheln, Balken, Testergebnisse, Formular-
# Raster). Rahmen, Reiter, Tabellen, Badges und Farben kommen aus dem Kit der
# Plattform (/platform/theme.css); Farben hier nur über dessen Variablen.
STYLE = """
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-color-surface);border:1px solid var(--oaap-color-border);
       border-radius:var(--oaap-radius);padding:1.2rem 1.4rem;margin-bottom:1.2rem}
  .card.attention{border-left:4px solid var(--oaap-color-warning)}
  .card.secretbox{border-left:4px solid var(--oaap-color-success)}
  .card.danger{border-left:4px solid var(--oaap-color-danger)}
  .badge{display:inline-block;font-size:.72rem;font-weight:600;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-badge-info-bg);color:var(--oaap-badge-info);white-space:nowrap}
  .badge.green,.badge.st-good{background:var(--oaap-badge-ok-bg);color:var(--oaap-badge-ok)}
  .badge.yellow,.badge.st-warning,.badge.st-serious{background:var(--oaap-badge-warn-bg);color:var(--oaap-badge-warn)}
  .badge.red,.badge.err,.badge.st-critical{background:var(--oaap-badge-err-bg);color:var(--oaap-badge-err)}
  .badge.pbd{background:var(--oaap-badge-info-bg);color:var(--oaap-badge-info)}
  .badge.off{background:var(--oaap-badge-neutral-bg);color:var(--oaap-badge-neutral)}
  a.btn{display:inline-block;padding:.45rem 1rem;border-radius:var(--oaap-radius);
       background:var(--oaap-color-primary);color:var(--oaap-color-primary-text);text-decoration:none}
  .hint{font-size:.82rem;color:var(--oaap-color-text-muted);margin:0 0 .6rem}
  .err{color:var(--oaap-color-danger)}.muted{color:var(--oaap-color-text-muted);font-size:.9rem}
  .card table{width:100%;border-collapse:collapse}
  .card th,.card td{text-align:left;padding:.5rem .5rem;border-bottom:1px solid var(--oaap-color-border);
       vertical-align:middle;font-size:.92rem}
  .card th{font-size:.8rem;color:var(--oaap-color-text-muted)}
  code,kbd{background:var(--oaap-badge-neutral-bg);color:var(--oaap-badge-neutral);border-radius:.25rem;
       padding:.1rem .35rem;font-size:.86rem;word-break:break-all}
  pre{background:var(--oaap-color-bg);color:var(--oaap-color-text);border:1px solid var(--oaap-color-border);
       padding:.9rem 1rem;border-radius:var(--oaap-radius);overflow-x:auto;font-size:.82rem}
  form.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
       gap:.8rem 1rem;align-items:end}
  form.grid label,.card label{display:block;font-size:.82rem;color:var(--oaap-color-text-muted);margin-bottom:.25rem}
  .card input[type=text],.card input[type=number],.card input[type=password],.card input[type=date],
  .card select{width:100%;box-sizing:border-box;min-height:40px}
  fieldset{border:1px solid var(--oaap-color-border);border-radius:var(--oaap-radius);padding:.5rem .8rem}
  fieldset legend{font-size:.78rem;color:var(--oaap-color-text-muted);padding:0 .3rem}
  fieldset label,.card fieldset label{display:inline-flex;align-items:center;gap:.35rem;margin-right:.9rem;
       font-size:.9rem;color:var(--oaap-color-text)}
  .legend{display:flex;flex-wrap:wrap;gap:.3rem 1rem;margin:0 0 .5rem;font-size:.85rem}
  .lg{display:inline-flex;align-items:center;gap:.35rem}
  .lg i{display:inline-block;width:.8rem;height:.8rem;border-radius:2px}
  svg.chart{width:100%;height:auto;display:block}
  svg.chart .ax{font-size:11px;fill:var(--oaap-color-text-muted)}
  .hbar{display:grid;grid-template-columns:minmax(8rem,2fr) 3fr minmax(6rem,auto);gap:.6rem;
       align-items:center;padding:.3rem 0;font-size:.9rem}
  .hl{overflow:hidden;text-overflow:ellipsis}
  .hl code{white-space:nowrap;word-break:normal;font-size:.78rem}
  .ht{background:var(--oaap-badge-neutral-bg);border-radius:4px;height:.8rem;overflow:hidden}
  .ht i{display:block;height:100%;border-radius:0 4px 4px 0}
  .hv{text-align:right;white-space:nowrap}.hv small{color:var(--oaap-color-text-muted)}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(9rem,1fr));gap:.8rem}
  .tile b{display:block;font-size:1.35rem}.tile span{font-size:.8rem;color:var(--oaap-color-text-muted)}
  .scroll{overflow-x:auto}td.num{text-align:right;white-space:nowrap}
  .hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(12.5rem,1fr));gap:1rem;
       margin-bottom:1.2rem}
  .kpi{position:relative;overflow:hidden;background:var(--oaap-color-surface);
       border:1px solid var(--oaap-color-border);border-radius:var(--oaap-radius);padding:1rem 1.1rem 0;
       display:flex;flex-direction:column;min-height:9.2rem}
  .kpi::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--oaap-color-primary)}
  .kpi .kl{font-size:.74rem;text-transform:uppercase;letter-spacing:.06em;color:var(--oaap-color-text-muted)}
  .kpi b{font-size:1.9rem;line-height:1.15;margin:.25rem 0 .1rem;color:var(--oaap-color-text);
       font-variant-numeric:tabular-nums}
  .kpi b.small{font-size:1.35rem}
  .kpi .ks{font-size:.8rem;color:var(--oaap-color-text-muted)}
  .kpi svg.spark{display:block;width:calc(100% + 2.2rem);height:2.6rem;margin:auto -1.1rem 0}
  .kpi.ringed{flex-direction:row;align-items:center;justify-content:space-between;
       padding-bottom:1rem;gap:.6rem}
  .kpi.ringed>div{display:flex;flex-direction:column}
  .kpi .chips{display:flex;flex-wrap:wrap;gap:.3rem;margin:auto 0 1rem;text-decoration:none;
       padding-top:.5rem}
  .delta{font-weight:600}.delta.up{color:var(--oaap-color-success)}.delta.down{color:var(--oaap-color-warning)}
  .cardhead{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;
       gap:.4rem 1rem;margin-bottom:.6rem}
  .cardhead h2{margin:0}
  .pills{display:inline-flex;background:var(--oaap-badge-neutral-bg);border-radius:2rem;padding:.2rem}
  .pill{padding:.3rem .8rem;border-radius:2rem;text-decoration:none;color:var(--oaap-color-text-muted);
       font-size:.85rem;font-variant-numeric:tabular-nums}
  .pill.on{background:var(--oaap-color-surface);color:var(--oaap-color-primary);font-weight:600}
  .summary{margin:.9rem 0 0;padding:.6rem .8rem;background:var(--oaap-color-bg);border-radius:var(--oaap-radius);
       font-size:.9rem}
  .grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 1.2rem}
  td:first-child>code{word-break:normal;white-space:nowrap}
  .rank{display:inline-block;width:1.3rem;color:var(--oaap-color-text-muted);font-size:.78rem;
       font-variant-numeric:tabular-nums}
  .empty{padding:.8rem 0}
  .probe{display:flex;gap:.7rem;align-items:flex-start;padding:.6rem .7rem;border-radius:var(--oaap-radius);
       margin-bottom:.45rem;border:1px solid var(--oaap-color-border)}
  .probe .pm{font-weight:700;width:1.2rem;text-align:center}
  .probe.good{background:var(--oaap-badge-ok-bg);color:var(--oaap-badge-ok)}
  .probe.warning,.probe.serious{background:var(--oaap-badge-warn-bg);color:var(--oaap-badge-warn)}
  .probe.critical{background:var(--oaap-badge-err-bg);color:var(--oaap-badge-err)}
  .probe a{color:inherit}
  .rowactions{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center}
  .rowactions form{margin:0}
  th.num{text-align:right}.nowrap{white-space:nowrap}
  tr.dim td{opacity:.6}
  textarea{width:100%;box-sizing:border-box;font:.86rem/1.45 ui-monospace,SFMono-Regular,Consolas,monospace;
       resize:vertical}
  textarea[readonly]{background:var(--oaap-color-bg)}
  code.key{white-space:nowrap;word-break:normal;font-size:.8rem}
  form.inline{display:inline-flex;gap:.4rem;align-items:center;flex-wrap:wrap}
  form.inline input[type=date]{width:auto}
  form.inline label{display:inline-flex;gap:.3rem;align-items:center;margin:0;font-size:.85rem}
  @media (max-width:640px){ .hbar{grid-template-columns:1fr 1fr}
       .grid2{grid-template-columns:1fr} .kpi b{font-size:1.6rem} }
"""


def page(title, body, user="", roles="", tab=None, refresh=0, obj=None):
    """Eine Seite im Rahmen der Plattform. `user`/`roles` bleiben in der
    Unterschrift, stehen aber nicht mehr auf der Seite: das Benutzermenü des
    Rahmens zeigt, wer angemeldet ist."""
    head = f'<meta http-equiv="refresh" content="{int(refresh)}">' if refresh else ""
    href = dict((k, h) for k, h, _t in TABS).get(tab)
    return af.page(title, body, app=APP_NAME, version=VERSION, product=PRODUCT,
                   tabs=tab_items() if tab else None, active=href, obj=obj,
                   style=STYLE, head=head)


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


def context_inputs(row=None, prefix="", keep=None):
    """Eingabefelder für den Kontext — leer beim Ausstellen, gefüllt beim Ändern.

    `keep` sind Formularwerte, die nach einer Ablehnung oder beim Neu-Ausstellen
    wieder eingesetzt werden. Jedes Feld außer den Etiketten schlägt die schon
    benutzten Werte vor (`context_datalists`) — gegen „Projekt X“ neben „projekt x“.
    """
    out = []
    for name in store.CONTEXT_FIELDS:
        value = ""
        if row is not None:
            value = store.labels_text(row[name]) if name == "labels" else row[name]
        elif keep:
            value = keep.get(name, "")
        hint = " (name=wert, Komma-getrennt, höchstens 5)" if name == "labels" else ""
        suggest = "" if name == "labels" else f' list="ctx-{name}" autocomplete="off"'
        out.append(f'<div><label for="{prefix}{name}">{CONTEXT_LABELS[name]}{hint}</label>'
                   f'<input id="{prefix}{name}" name="{name}" type="text"{suggest} '
                   f'maxlength="{400 if name == "labels" else store.MAX_TEXT}" '
                   f'value="{esc(value or "")}"></div>')
    return "".join(out)


def context_datalists(rows):
    """Je Kontextfeld die schon benutzten Werte als <datalist> — einmal je Seite.

    Häufige Werte zuerst; Schreibweisen, die sich nur in Groß/Klein oder
    Leerzeichen unterscheiden, erscheinen trotzdem beide, damit man den
    Wildwuchs sieht, statt ihn zu verstecken.
    """
    out = []
    for name in store.CONTEXT_FIELDS:
        if name == "labels":
            continue
        counts = {}
        for row in rows:
            value = (row[name] or "").strip()
            if value and not (name == "account" and value == "default"):
                counts[value] = counts.get(value, 0) + 1
        ranked = sorted(counts, key=lambda v: (-counts[v], v.lower()))
        out.append(f'<datalist id="ctx-{name}">'
                   + "".join(f'<option value="{esc(v)}">' for v in ranked) + "</datalist>")
    return "".join(out)


def next_label(rows, label):
    """Das nächste freie Etikett nach `label`: `laptop@joerg` → `laptop@joerg-2`,
    `laptop@joerg-2` → `laptop@joerg-3`. Ein Etikett bleibt seinem Schlüssel für
    immer (auch widerrufen oder abgelaufen), damit die Prüfspur eindeutig bleibt."""
    taken = {row["label"] for row in rows}
    stem, n = label, 2
    m = re.fullmatch(r"(.+)-(\d{1,4})", label)
    if m:
        stem, n = m.group(1), int(m.group(2)) + 1
    while f"{stem}-{n}" in taken:
        n += 1
    return f"{stem}-{n}"


def renew_values(row, rows):
    """Formularwerte für „neu ausstellen“: alles vom alten Schlüssel, ein freies
    Etikett und — wenn er befristet war — dieselbe Laufzeit ab heute."""
    keep = {name: (store.labels_text(row[name]) if name == "labels" else row[name] or "")
            for name in store.CONTEXT_FIELDS}
    keep.update(label=next_label(rows, row["label"]), aliases=row["aliases"] or "",
                budget=str(row["budget_tokens"] or 0), rate=str(row["rate_per_min"] or 0),
                ceiling=row["ceiling"] or supply.DEFAULT_CEILING,
                personal_data="1" if row["personal_data"] else "")
    start, end = dash.parse(row["created"]), dash.parse(row["expires"])
    if start and end and end > start:
        keep["bis"] = _today_plus(max(1, round((end - start).total_seconds() / 86400)))
    return keep


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


def props_summary(values):
    """Werkzeuge ✓ · Bilder ? · 128k ein · 8k aus"""
    return (f'Werkzeuge {props.short("tools", values["tools"])} · '
            f'Bilder {props.short("vision", values["vision"])} · '
            f'{props.short("context", values["context"])} ein · '
            f'{props.short("output", values["output"])} aus')


def props_forms(alias, tab, alias_name=""):
    """Je Ziel ein Formular mit den vier Angaben, Herkunft und Meldung (RFC-0106 §2.3)."""
    out = []
    for target in alias["targets"]:
        key = (target["supplier"], target["model"])
        have = tab.get(key, {})
        fields, notes = [], []
        for prop in props.PROPS:
            cell = have.get(prop, {})
            value, origin = cell.get("value", ""), cell.get("origin", "")
            if prop in props.BOOLS:
                opts = "".join(f'<option value="{v}"{" selected" if v == value else ""}>{t}</option>'
                               for v, t in (("", "unbekannt"), ("true", "ja"), ("false", "nein")))
                inp = f'<select name="{prop}">{opts}</select>'
            else:
                inp = (f'<input name="{prop}" type="text" inputmode="numeric" value="{esc(value)}" '
                       f'placeholder="unbekannt" style="max-width:9rem">')
            where = (f' <span class="muted" style="font-size:.8rem">{props.ORIGIN_DE.get(origin, "")}'
                     f'</span>' if origin else "")
            fields.append(f'<div><label>{props.LABEL_DE[prop]}</label>{inp}{where}</div>')
            seen = cell.get("seen", "")
            back = f'<input type="hidden" name="alias" value="{esc(alias_name)}">' if alias_name else ""
            if origin == "operator" and seen and seen != value:
                notes.append(
                    f'<form method="post" action="model-props-take" style="margin:.2rem 0">{back}'
                    f'<input type="hidden" name="supplier" value="{esc(key[0])}">'
                    f'<input type="hidden" name="model" value="{esc(key[1])}">'
                    f'<input type="hidden" name="prop" value="{prop}">'
                    f'<span class="muted">{props.LABEL_DE[prop]}: '
                    f'{props.ORIGIN_DE.get(cell.get("seen_origin"), "")} gemeldet '
                    f'<code>{esc(props.short(prop, seen))}</code>, gesetzt '
                    f'<code>{esc(props.short(prop, value))}</code></span> '
                    f'<button type="submit">übernehmen</button></form>')
        out.append(
            '<form class="grid" method="post" action="model-props" style="margin-top:.6rem">'
            + (f'<input type="hidden" name="alias" value="{esc(alias_name)}">' if alias_name else "") +
            f'<input type="hidden" name="supplier" value="{esc(key[0])}">'
            f'<input type="hidden" name="model" value="{esc(key[1])}">'
            f'<div style="grid-column:1/-1"><b><code>{esc(key[0])} · {esc(key[1])}</code></b></div>'
            f'{"".join(fields)}<div><button class="quiet" type="submit">Eigenschaften speichern'
            f'</button></div></form>{"".join(notes)}')
    return "".join(out)


def alias_href(name):
    return "modelle?" + urlencode({"name": name})


def alias_info(name, tab, stored):
    """Was Liste und Objektseite über einen Modellnamen sagen."""
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
        light = supply.alias_light(alias, SUPPLIERS, "red")
        worst = light_badge(light)
        order_listed = alias["order_listed"]
        text = supply.targets_text(alias)
        traits = props_summary(props.of_alias(alias, tab))
    else:
        light = ""
        traits = ""
        targets = [f'<code>{esc(stored[name]["targets"])}</code>']
        worst = '<span class="badge err">unbenutzbar</span>'
        order_listed = bool(stored[name]["order_listed"])
        text = stored[name]["targets"]
    return {"alias": alias, "targets": targets, "light": light, "worst": worst,
            "order_listed": order_listed, "text": text, "traits": traits,
            "order": "wie aufgeführt" if order_listed else "grün vor gelb vor rot"}


def aliases_card(params=None):
    editable = MODE == "app"
    tab = props.table(DB)
    stored = {r["name"]: r for r in catalog.alias_rows(DB)} if editable else {}
    names = sorted(set(ALIASES) | set(stored))
    info = {n: alias_info(n, tab, stored) for n in names}
    q = af.Query(params or {}, filters=("ampel",), default_sort="name")
    rows = [n for n in names if not q.filters["ampel"] or info[n]["light"] == q.filters["ampel"]
            or (q.filters["ampel"] == "kaputt" and not info[n]["alias"])]
    columns = [
        ("Modellname", "name", lambda n: n, ""),
        ("Ampel", "light", lambda n: supply.LIGHT_RANK.get(info[n]["light"], 9), ""),
        ("Bezugsquellen", "targets", lambda n: info[n]["text"], ""),
        ("Reihenfolge", "", None, "oaap-minor"),
        ("Eigenschaften", "", None, "oaap-minor"),
    ]

    def row_html(n):
        i = info[n]
        return (f'<tr><td><a href="{esc(alias_href(n))}"><code>{esc(n)}</code></a></td><td>{i["worst"]}</td>'
                f'<td>{"<br>".join(i["targets"])}</td><td class="oaap-minor muted">{esc(i["order"])}</td>'
                f'<td class="oaap-minor muted">{esc(i["traits"])}</td></tr>')
    table = af.list_report(
        q, rows, columns, row_html, base="modelle?", noun="Modellnamen",
        empty_text="Noch kein Modellname — ohne ihn kann kein Verbraucher etwas anfragen."
                   + (" <b>Modellname anlegen</b> unten legt den ersten an." if editable else ""),
        choices=[("ampel", "Ampel", [("green", "grün"), ("yellow", "gelb"), ("red", "rot"),
                                     ("kaputt", "unbenutzbar")])],
        placeholder="Modellname oder Quelle")
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
    <div><button class="primary" type="submit">Anlegen</button></div>
  </form>"""
    return f"""<div class="card">
  <h2>Modellnamen</h2>
  <p class="hint">Ein Verbraucher fragt nach einem Namen von uns, nie nach dem Modell des
    Anbieters. Ausgewichen wird nur innerhalb der hier erklärten Gruppe — und die Ampel
    eines Modellnamens ist die <b>schlechteste</b> seiner Ziele. Ein Klick auf den Namen
    öffnet ihn: testen, Eigenschaften, ändern, löschen.</p>
  {table}{form}
</div>"""


def alias_page(user, roles, name, notice="", tests=None):
    """Die Objektseite eines Modellnamens (RFC-0118 §7.5)."""
    editable = MODE == "app"
    tab = props.table(DB)
    stored = {r["name"]: r for r in catalog.alias_rows(DB)} if editable else {}
    back = af.back("modelle", "Zurück zu den Modellnamen")
    if name not in ALIASES and name not in stored:
        return page("Unbekannter Modellname", back + "".join(top_cards(notice))
                    + af.empty(f"Einen Modellnamen „{esc(name)}“ gibt es nicht. "
                               '<a href="modelle">Zur Liste</a>'), user, roles, tab="modelle")
    i = alias_info(name, tab, stored)
    alias = i["alias"]
    facts = af.facts([("Ampel", i["worst"]), ("Bezugsquellen", "<br>".join(i["targets"])),
                      ("Reihenfolge", esc(i["order"])), ("Eigenschaften", esc(i["traits"]))])
    parts = [back] + top_cards(notice) + [f'<div class="oaap-objhead"><h1><code>{esc(name)}</code></h1>{facts}</div>']
    if tests:
        parts.append(tests)
    if alias:
        parts.append(f"""<div class="card"><h2>Testen</h2>
  <p class="hint">„Testen“ schickt jedem Ziel eine Mini-Anfrage. „Werkzeugaufruf probieren“
    schickt je Ziel eine kleine Anfrage mit einem harmlosen Werkzeug — kostet bei der Quelle
    ein paar Token.</p>
  <div class="rowactions">
    <form method="post" action="alias-test"><input type="hidden" name="name" value="{esc(name)}">
      <button type="submit">Testen</button></form>
    <form method="post" action="alias-tooltest"><input type="hidden" name="name" value="{esc(name)}">
      <button type="submit">Werkzeugaufruf probieren</button></form>
  </div></div>""")
    if editable and alias:
        parts.append(f"""<div class="card"><h2>Eigenschaften</h2>
  <p class="hint">Je Ziel; der Modellname bekommt je Angabe das schwächste seiner Ziele. Leer
    heißt unbekannt — geraten wird nicht; meldet die Quelle etwas, gilt dann wieder ihr Wert.</p>
  {props_forms(alias, tab, name)}</div>""")
    if editable:
        parts.append(f"""<div class="card"><h2>Ändern</h2>
  <form class="grid" method="post" action="alias">
    <input type="hidden" name="name" value="{esc(name)}">
    <div><label>Ziele (quelle:modell, …)</label>
      <input name="targets" type="text" value="{esc(i["text"])}"></div>
    <div><fieldset><legend>Reihenfolge</legend><label>
      <input type="checkbox" name="order_listed" value="1"{" checked" if i["order_listed"] else ""}> wie aufgeführt</label></fieldset></div>
    <div><button type="submit">Speichern</button></div></form></div>
<div class="card danger"><h2>Löschen</h2>
  <p class="hint">Schlüssel, die auf diesen Namen beschränkt sind, erreichen ihn danach nicht mehr.</p>
  <form method="post" action="alias-delete" onsubmit="return confirm('{esc(name)} wirklich löschen?')">
    <input type="hidden" name="name" value="{esc(name)}">
    <button class="danger" type="submit">Modellname löschen</button></form></div>""")
    return page(name, "".join(parts), user, roles, tab="modelle", obj=f"Modellname:{name}")


DEST_ENV = re.compile(r"^OAAP_DESTINATION_([A-Z0-9_]+)_URL$")


def bound_destinations(env=None):
    """[(name, url)] -- the destinations the platform bound to this instance
    (oaap.net.destinations; manifest `any: true`, 0.5.5). The platform adds
    the credential on the way, so a supplier made from one needs none here."""
    out = []
    for k, v in sorted((env if env is not None else os.environ).items()):
        m = DEST_ENV.match(k)
        if m and v.startswith(("http://", "https://")):
            out.append((m.group(1).lower().replace("_", "-"), v if v.endswith("/") else v + "/"))
    return out


def destination_of(url, dests):
    """The name of the bound destination a supplier's address leads through, or ""."""
    for name, base in dests:
        if (url.rstrip("/") + "/").startswith(base):
            return name
    return ""


def destinations_form(rows, dests):
    """One small form per bound destination not yet used by a supplier."""
    used = {destination_of(r[1], dests) for r in rows}
    forms = []
    for name, base in dests:
        if name in used:
            continue
        forms.append(
            f'<form class="grid" method="post" action="supplier" style="margin-top:.6rem">'
            f'<div><label>Name</label><input name="name" type="text" value="{esc(name)}" required></div>'
            f'<div><label>Adresse (Destination <code>{esc(name)}</code>, bis /v1)</label>'
            f'<input name="url" type="text" value="{esc(base)}v1" required></div>'
            f'{light_radios("yellow", "dest-" + name)}'
            f'<div><button type="submit">Als Bezugsquelle anlegen</button></div></form>')
    if not dests:
        return ""
    head = ('<h2 style="margin-top:1.2rem">Aus Destinationen der Plattform</h2>'
            '<p class="hint">Diese Ziele hat der Betreiber dieser Instanz zugeordnet (Portal → '
            'Instanz → Netz → Destinationen). Die Plattform meldet sich dort an — hier sind '
            '<b>keine Zugangsdaten</b> nötig. Die Ampel wählst du: Sie sagt, was mit den Daten '
            'hinter dem Ziel geschehen kann.</p>')
    return head + ("".join(forms) or '<p class="muted">Jede zugeordnete Destination ist '
                   'schon Bezugsquelle.</p>')


def supplier_entries():
    """[(name, url, light, has_cred, usable, drop_sampling)] -- aus der Datenbank,
    wenn die App pflegt, sonst aus der Konfiguration."""
    if MODE == "app":
        return [(r["name"], r["url"], r["light"], bool(r["credential"]),
                 r["name"] in SUPPLIERS, bool(r["drop_sampling"])) for r in catalog.supplier_rows(DB)]
    return [(n, s["url"], s["light"], bool(s["credential"]), True, False)
            for n, s in sorted(SUPPLIERS.items())]


def supplier_cred(entry, dests):
    name, url, light, has_cred, usable, sampling = entry
    via = destination_of(url, dests)
    cred = ("hinterlegt" if has_cred else
            f'<span class="muted">meldet die Plattform an (Destination {esc(via)})</span>' if via else
            '<span class="muted">keine (z. B. lokal)</span>')
    forced = relay.is_anthropic(url)
    if forced or sampling:
        cred += ('<br><span class="badge off" title="temperature, top_p und top_k werden '
                 'nicht weitergegeben">ohne temperature/top_p</span>')
    if not usable:
        cred = '<span class="badge err">nicht lesbar</span>'
    return cred


def supplier_href(name):
    return "quellen?" + urlencode({"name": name})


def suppliers_card():
    editable = MODE == "app"
    dests = bound_destinations()
    entries = supplier_entries()
    rows = []
    for e in entries:
        name, url, light = e[0], e[1], e[2]
        rows.append(f'<tr><td><a href="{esc(supplier_href(name))}"><code>{esc(name)}</code></a></td>'
                    f'<td>{light_badge(light)}<br>'
                    f'<span class="muted">{esc(supply.LIGHT_RULES.get(light, ""))}</span></td>'
                    f'<td class="oaap-minor"><code>{esc(url)}</code></td><td>{supplier_cred(e, dests)}</td></tr>')
    table = af.table("<th>Name</th><th>Ampel</th><th class=\"oaap-minor\">Adresse</th><th>Zugangsdaten</th>",
                     rows, "Noch keine Bezugsquelle."
                     + (" <b>Bezugsquelle anlegen</b> unten legt die erste an." if editable else ""))
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
    <div><fieldset><legend>temperature/top_p</legend><label>
      <input type="checkbox" name="drop_sampling" value="1"> nicht weitergeben</label></fieldset></div>
    <div><button class="primary" type="submit">Anlegen</button></div>
  </form>
  <p class="hint" style="margin-top:.8rem">Anthropic: Adresse <code>https://api.anthropic.com/v1</code>,
    der API-Key von Anthropic als Zugangsdaten, Modelle wie <code>claude-sonnet-5-5</code>.
    temperature/top_p gibt das Gateway dort nie weiter (die neuen Modelle lehnen sie ab).
    Ohne Angabe gilt <b>rot</b> — unbekannte Herkunft
    ist nicht souverän. Eine Quelle auf demselben Knoten wird über eine App-zu-App-Verbindung
    erreichbar (<code>oaap app link add</code>).</p>
  {destinations_form([(e[0], e[1]) for e in entries], dests)}"""
    test_all = ('<form method="post" action="supplier-test" style="margin:0">'
                '<input type="hidden" name="name" value="*">'
                '<button type="submit">Alle testen</button></form>'
                if len(entries) > 1 else "")
    return f"""<div class="card">
  <div class="cardhead"><h2>Bezugsquellen</h2>{test_all}</div>
  <p class="hint">Zugangsdaten werden nur verschlüsselt gespeichert, lassen sich setzen
    oder ersetzen, aber <b>nie wieder anzeigen</b> — weder hier noch in einer Messzeile,
    Fehlermeldung oder der Prüfspur. Ein Klick auf den Namen öffnet die Quelle: testen,
    ändern, löschen.</p>
  {table}{form}
</div>"""


def supplier_page(user, roles, name, notice="", tests=None):
    """Die Objektseite einer Bezugsquelle (RFC-0118 §7.5)."""
    editable = MODE == "app"
    dests = bound_destinations()
    entry = next((e for e in supplier_entries() if e[0] == name), None)
    if entry is None and name in SUPPLIERS:      # gelesen, aber (noch) nicht gepflegt
        s = SUPPLIERS[name]
        entry = (name, s["url"], s["light"], bool(s["credential"]), True, False)
    back = af.back("quellen", "Zurück zu den Bezugsquellen")
    if entry is None:
        return page("Unbekannte Bezugsquelle", back + "".join(top_cards(notice))
                    + af.empty(f"Eine Bezugsquelle „{esc(name)}“ gibt es nicht. "
                               '<a href="quellen">Zur Liste</a>'), user, roles, tab="quellen")
    _n, url, light, _has, usable, sampling = entry
    used_by = sorted(a for a, alias in ALIASES.items()
                     if any(t["supplier"] == name for t in alias["targets"]))
    facts = af.facts([
        ("Ampel", light_badge(light) + f'<br><span class="muted">{esc(supply.LIGHT_RULES.get(light, ""))}</span>'),
        ("Adresse", f"<code>{esc(url)}</code>"),
        ("Zugangsdaten", supplier_cred(entry, dests)),
        ("Benutzt von", ", ".join(f'<a href="{esc(alias_href(a))}"><code>{esc(a)}</code></a>' for a in used_by)
         or '<span class="muted">keinem Modellnamen</span>'),
    ])
    parts = [back] + top_cards(notice) + [f'<div class="oaap-objhead"><h1><code>{esc(name)}</code></h1>{facts}</div>']
    if tests:
        parts.append(tests)
    if usable:
        parts.append(f"""<div class="card"><h2>Testen</h2>
  <p class="hint">Fragt die Modellliste ab: erreichbar? Anmeldung gültig? Lässt nichts rechnen.</p>
  <div class="rowactions">
    <form method="post" action="supplier-test"><input type="hidden" name="name" value="{esc(name)}">
      <button type="submit">Testen</button></form>
    <a class="oaap-btn" href="modelle?{esc(urlencode({"quelle": name}))}#import">Modellnamen daraus anlegen</a>
  </div></div>""")
    if editable:
        forced = relay.is_anthropic(url)
        drops = forced or sampling
        parts.append(f"""<div class="card"><h2>Ändern</h2>
  <form class="grid" method="post" action="supplier">
    <input type="hidden" name="name" value="{esc(name)}">
    <div><label>Adresse</label><input name="url" type="text" value="{esc(url)}"></div>
    {light_radios(light, name)}
    <div><label>Neue Zugangsdaten (leer = unverändert)</label>
      <input name="credential" type="password" autocomplete="new-password"></div>
    <div><fieldset><legend>Zugangsdaten</legend><label>
      <input type="checkbox" name="drop_credential" value="1"> entfernen</label></fieldset></div>
    <div><fieldset><legend>temperature/top_p</legend><label>
      <input type="checkbox" name="drop_sampling" value="1"{" checked" if drops else ""}{" disabled" if forced else ""}>
      nicht weitergeben{" (Anthropic: immer)" if forced else ""}</label></fieldset></div>
    <div><button type="submit">Speichern</button></div></form></div>
<div class="card danger"><h2>Löschen</h2>
  <form method="post" action="supplier-delete" onsubmit="return confirm('{esc(name)} wirklich löschen?')">
    <input type="hidden" name="name" value="{esc(name)}">
    <button class="danger" type="submit">Bezugsquelle löschen</button></form></div>""")
    return page(name, "".join(parts), user, roles, tab="quellen", obj=f"Bezugsquelle:{name}")


# ------------------------------------------------------------ Betreiber-Seiten

# Die Reiter, in der Reihenfolge, in der ein Betreiber fragt: Was passiert
# gerade? Was kam zuletzt? Wie viel war es? Wer darf? — Einrichten
# (Modellnamen, Bezugsquellen) steht hinten, Beobachten vorn (RFC-0118 §6).
TABS = (("dashboard", "./", "Übersicht"), ("anfragen", "anfragen", "Letzte Anfragen"),
        ("auswertung", "auswertung", "Auswertung"), ("schluessel", "schluessel", "Schlüssel"),
        ("kohorten", "kohorten", "Kohorten"), ("vorlagen", "vorlagen", "Vorlagen"),
        ("modelle", "modelle", "Modelle"), ("quellen", "quellen", "Bezugsquellen"))


def tab_counts():
    try:
        rows = store.keys(DB)
        keys = sum(1 for r in rows if store.usable(r) and not r["cohort"])
        cohorts_live = len({r["cohort"] for r in rows if r["cohort"] and store.usable(r)})
    except Exception:
        keys = cohorts_live = 0
    return {"schluessel": keys, "kohorten": cohorts_live, "modelle": len(ALIASES),
            "quellen": len(SUPPLIERS)}


def tab_items():
    counts = tab_counts()
    return [(href, text, counts.get(key)) for key, href, text in TABS]


def top_cards(notice="", secret=None, host=""):
    """Was über jedem Reiter stehen kann: der einmalige Schlüssel, ein Hinweis."""
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
    return parts


def config_cards():
    parts = list(supply_notes())
    if CONFIG_ERRORS:
        items = "".join(f"<li>{esc(e)}</li>" for e in CONFIG_ERRORS)
        parts.append(f"""<div class="card attention">
  <h2>Stellen, die ich nicht verwenden kann</h2>
  <p class="hint">Alles andere läuft weiter — was hier steht, ist wirkungslos,
    nicht kaputt.</p>
  <ul>{items}</ul>
</div>""")
    return parts


def outcome_badge(outcome):
    status = charts.outcome_status(outcome or "")
    mark = charts.STATUS[status][1]
    return (f'<span class="badge st-{status}" title="{esc(outcome)}">{mark} '
            f'{esc(charts.outcome_word(outcome or ""))}</span>')


def _delta(now_v, before_v):
    if not before_v:
        return '<span class="muted">vorher 0</span>' if now_v else '<span class="muted">ruhig</span>'
    pct = round(100 * (now_v - before_v) / before_v)
    cls = "up" if pct > 0 else "down" if pct < 0 else ""
    arrow = "▲" if pct > 0 else "▼" if pct < 0 else "="
    return f'<span class="delta {cls}">{arrow} {abs(pct)} %</span> <span class="muted">zu den 24 h davor</span>'


def _qs(params, **change):
    merged = {k: v for k, v in params.items() if v not in ("", "*")}
    merged.update({k: v for k, v in change.items()})
    return urlencode({k: v for k, v in merged.items() if v not in ("", "*", None)})


def dashboard_page(user, roles, host, params, now=None):
    now = now or datetime.now(timezone.utc)
    day_ago, two_days = now - timedelta(hours=24), now - timedelta(hours=48)
    cur = dash.totals(DB, dash.stamp(day_ago))
    prev = dash.totals(DB, dash.stamp(two_days), dash.stamp(day_ago))
    hours = dash.hourly(DB, 24, now)
    ever = dash.totals(DB, "")
    share = cur["answered"] / cur["calls"] if cur["calls"] else 1.0
    good, bad = charts.STATUS["good"][0], charts.STATUS["critical"][0]
    ring_color = good if share >= 0.95 else charts.STATUS["warning"][0] if share >= 0.8 else bad

    hero = f"""<section class="hero">
  <div class="kpi"><span class="kl">Anfragen · 24 h</span>
    <b>{charts.fmt_num(cur['calls'])}</b>
    <span class="ks">{_delta(cur['calls'], prev['calls'])}</span>
    {dash.sparkline([h['calls'] for h in hours])}</div>
  <div class="kpi"><span class="kl">Token · 24 h</span>
    <b>{charts.fmt_num(cur['tokens'])}</b>
    <span class="ks">{charts.fmt_num(cur['in_tokens'])} ein · {charts.fmt_num(cur['out_tokens'])} aus</span>
    {dash.sparkline([h['tokens'] for h in hours], charts.SERIES[1])}</div>
  <div class="kpi ringed"><div><span class="kl">Erfolg · 24 h</span>
    <b>{'—' if not cur['calls'] else f"{100 * share:.0f} %".replace('.', ',')}</b>
    <span class="ks">{charts.fmt_num(cur['failed'])} nicht beantwortet</span></div>
    {dash.ring(share if cur['calls'] else 0, ring_color)}</div>
  <div class="kpi"><span class="kl">Antwortzeit Ø · 24 h</span>
    <b>{charts.fmt_num(cur['avg_ms'])} ms</b>
    <span class="ks">nur beantwortete Anfragen</span>
    {dash.sparkline([h['ms'] for h in hours], charts.SERIES[2])}</div>
  <div class="kpi"><span class="kl">Zuletzt</span>
    <b class="small">{esc(dash.ago(ever['last'], now))}</b>
    <span class="ks">{cur['keys']} Schlüssel aktiv in 24 h · {tab_counts()['schluessel']} gültig
      {f"· {tab_counts()['kohorten']} Kohorte(n)" if tab_counts()['kohorten'] else ""}</span>
    {_supply_chips()}</div>
</section>"""

    chart = f"""<div class="card">
  <div class="cardhead"><h2>Verlauf — letzte 24 Stunden</h2>
    <span class="legend"><span class="lg"><i style="background:{good}"></i>beantwortet</span>
    <span class="lg"><i style="background:{bad}"></i>nicht beantwortet</span>
    <span class="muted">Stunden in {esc(dash.LOCAL_NAME)}</span></span></div>
  {dash.hour_bars(hours)}
</div>"""

    # --- die aktuelle Auswertung mit Filtern
    win = params.get("fenster", dash.DEFAULT_WINDOW)
    if win not in dash.WINDOWS:
        win = dash.DEFAULT_WINDOW
    metric = params.get("mass", dash.DEFAULT_METRIC)
    if metric not in dash.METRICS:
        metric = dash.DEFAULT_METRIC
    since = dash.stamp(now - timedelta(hours=dash.WINDOWS[win][0]))
    filters = {d: params[d] for d in ("model", "key", "supplier") if params.get(d) not in (None, "", "*")}
    keep = {"mass": metric, **filters}
    pills = "".join(
        f'<a class="pill{" on" if w == win else ""}" href="./?{esc(_qs(keep, fenster=w))}">'
        f'{esc(w)}</a>' for w in dash.WINDOWS)
    selects = []
    for dim in ("model", "key", "supplier"):
        vals = dash.values(DB, dim, since)
        current = filters.get(dim, "*")
        if current != "*" and current not in vals:
            vals.append(current)
        selects.append(f'<div><label for="d-{dim}">{esc(dash.DIMS[dim][1])}</label>'
                       f'<select id="d-{dim}" name="{dim}"><option value="*">alle</option>'
                       f'{options(vals, current, {v: v for v in vals})}</select></div>')
    w_tot = dash.totals(DB, since, None, filters)

    def fmt(v):
        return f"{charts.fmt_num(v)} ms" if metric == "ms" else charts.fmt_num(v)

    def block(dim, title, color):
        rows, more = dash.top(DB, since, dim, metric, filters)
        return (f'<div class="card"><h2>{esc(title)}</h2>'
                f'{dash.bar_list(rows, more, fmt, color, share=metric != "ms")}</div>')

    outcome_rows, _ = dash.top(DB, since, "outcome", "calls", filters, n=20)
    o_total = sum(v for _, v in outcome_rows) or 1
    o_html = "".join(
        f'<div class="hbar"><span class="hl">{outcome_badge(name)}</span>'
        f'<span class="ht"><i style="width:{100 * v / o_total:.1f}%;'
        f'background:{charts.STATUS[charts.outcome_status(name)][0]}"></i></span>'
        f'<span class="hv">{charts.fmt_num(v)} <small>{100 * v / o_total:.0f} %</small></span></div>'
        for name, v in outcome_rows) or '<p class="muted empty">Keine Anfragen in diesem Fenster.</p>'

    analysis = f"""<div class="card">
  <div class="cardhead"><h2>Aktuelle Auswertung</h2>
    <span class="pills" role="group" aria-label="Zeitfenster">{pills}</span></div>
  <form class="grid" method="get" action="./">
    <input type="hidden" name="fenster" value="{esc(win)}">
    <div><label for="d-mass">Rangliste nach</label><select id="d-mass" name="mass">
      {options(list(dash.METRICS), metric, {k: v[1] for k, v in dash.METRICS.items()})}</select></div>
    {"".join(selects)}
    <div><button type="submit">Filtern</button></div>
  </form>
  <p class="summary"><b>{esc(dash.WINDOWS[win][1])}</b>{' · gefiltert' if filters else ''}:
    {charts.fmt_num(w_tot['calls'])} Anfragen · {charts.fmt_num(w_tot['tokens'])} Token ·
    {charts.fmt_num(w_tot['failed'])} nicht beantwortet · Ø {charts.fmt_num(w_tot['avg_ms'])} ms
    {f'· <a href="./?{esc(_qs({}, fenster=win))}">Filter zurücksetzen</a>' if filters else ''}</p>
</div>
<div class="grid2">
  {block("model", "Top 10 Modellnamen", charts.SERIES[0])}
  {block("key", "Top 10 Schlüssel", charts.SERIES[6])}
  {block("supplier", "Top 10 Bezugsquellen", charts.SERIES[2])}
  <div class="card"><h2>Ausgänge</h2>{o_html}</div>
</div>"""

    body = "".join(config_cards()) + hero + chart + analysis + (
        f'<p class="hint">Aktualisiert sich jede Minute · Stand {esc(now.astimezone(dash.LOCAL).strftime("%H:%M:%S"))} '
        f'{esc(dash.LOCAL_NAME)} · Rechnungen und lange Zeiträume: <a href="auswertung">Auswertung</a></p>')
    return page("Übersicht", body, user, roles, tab="dashboard", refresh=60)


def _supply_chips():
    by = {}
    for s in SUPPLIERS.values():
        by[s["light"]] = by.get(s["light"], 0) + 1
    if not SUPPLIERS:
        return '<a class="chips" href="quellen"><span class="badge off">keine Bezugsquelle</span></a>'
    chips = "".join(f'<span class="badge {esc(l)}">{n} {esc(supply.LIGHT_LABELS.get(l, l).split(" — ")[0])}</span>'
                    for l, n in sorted(by.items(), key=lambda kv: supply.LIGHT_RANK.get(kv[0], 9)))
    return f'<a class="chips" href="quellen" title="Bezugsquellen ansehen und testen">{chips}</a>'


REQUEST_SORT = {"time": "u.id", "key": "key_label", "model": "u.alias", "supplier": "u.supplier",
                "tokens": "COALESCE(u.in_tokens,0)+COALESCE(u.out_tokens,0)", "ms": "u.ms",
                "outcome": "u.outcome"}


def requests_page(user, roles, params):
    """Die letzten Anfragen als Listenbericht (RFC-0118 §7): gefiltert,
    sortiert und geblättert in der Datenbank -- nie eine stille Grenze."""
    q = af.Query(params, filters=("key", "model", "supplier", "ausgang"), default_sort="-time")
    filters = {d: q.filters[d] for d in ("model", "key", "supplier") if q.filters[d] not in ("", "*")}
    where, args = dash._where("", None, filters)
    state = q.filters["ausgang"]
    if state == "ok":
        where += f" AND {dash.ANSWERED}"
    elif state == "fehler":
        where += f" AND NOT ({dash.ANSWERED})"
    for word in q.q.lower().split():
        where += (" AND (LOWER(u.alias) LIKE ? OR LOWER(COALESCE(k.label, u.key_id)) LIKE ?"
                  " OR LOWER(COALESCE(u.supplier,'')) LIKE ? OR LOWER(COALESCE(u.model,'')) LIKE ?)")
        args += [f"%{word}%"] * 4
    total = DB.execute(f"SELECT COUNT(*) AS n {dash.FROM} WHERE {where}", args).fetchone()["n"]
    if q.offset >= total:
        q.offset = 0
    col = q.sort.lstrip("-")
    order = REQUEST_SORT.get(col, "u.id")
    direction = "DESC" if q.sort.startswith("-") or col not in REQUEST_SORT else "ASC"
    rows = list(DB.execute(
        f"SELECT u.*, COALESCE(k.label, u.key_id) AS key_label {dash.FROM} WHERE {where} "
        f"ORDER BY {order} {direction}, u.id DESC LIMIT ? OFFSET ?", args + [q.size, q.offset]))
    choices = [(dim, dash.DIMS[dim][1], [(v, v) for v in dash.values(DB, dim, "")])
               for dim in ("key", "model", "supplier")]
    choices.append(("ausgang", "Ausgang", [("ok", "beantwortet"), ("fehler", "nicht beantwortet")]))
    lines = []
    for row in rows:
        lines.append(
            f'<tr><td class="muted nowrap" title="{esc(row["time"])} (UTC)">{esc(dash.local(row["time"]))}</td>'
            f'<td>{esc(row["key_label"])}</td>'
            f'<td><code>{esc(row["alias"])}</code></td>'
            f'<td>{esc(row["supplier"] or "—")}<br>'
            f'<span class="muted">{esc(row["model"] or "")}</span></td>'
            f'<td class="oaap-num">{fmt_tokens(row["in_tokens"])} / {fmt_tokens(row["out_tokens"])}</td>'
            f'<td class="oaap-num oaap-minor">{fmt_bytes(row["bytes_up"])} / {fmt_bytes(row["bytes_down"])}</td>'
            f'<td class="oaap-num">{charts.fmt_num(row["ms"])} ms</td><td>{outcome_badge(row["outcome"])}</td></tr>')
    head = "".join([
        af.th("Zeit", "time", q, "anfragen?"), af.th("Schlüssel", "key", q, "anfragen?"),
        af.th("Modellname", "model", q, "anfragen?"), af.th("Quelle", "supplier", q, "anfragen?"),
        af.th("Token ein/aus", "tokens", q, "anfragen?", "oaap-num"),
        af.th("Bytes ↑/↓", "", q, cls="oaap-num oaap-minor"),
        af.th("Dauer", "ms", q, "anfragen?", "oaap-num"), af.th("Ausgang", "outcome", q, "anfragen?")])
    nothing = ("Keine Anfrage passt zu dieser Auswahl." if (q.q or any(q.filters.values()))
               else "Noch keine Anfrage. Sobald ein Verbraucher mit einem Schlüssel fragt, steht sie hier.")
    body = (af.pagehead("Letzte Anfragen")
            + f'<p class="hint">Gezählt wird, nicht mitgeschrieben: Zeit, Schlüssel, Modellname, '
              f'<b>die tatsächlich benutzte Quelle</b>, Token-Zahlen, Bytes am Verbraucher, '
              f'Dauer und Ausgang. Prompts und Antworten stehen in keiner Zeile und in keiner Datei. '
              f'Zeiten in {esc(dash.LOCAL_NAME)}.</p>'
            + af.filter_bar(q, choices, "anfragen", placeholder="Schlüssel, Modellname, Quelle")
            + af.countline(q.offset + 1, q.offset + len(rows), total, "Anfragen")
            + af.table(head, lines, nothing) + af.paging(q, total, "anfragen?"))
    return page("Letzte Anfragen", body, user, roles, tab="anfragen")


def change_key_aliases(label, names, user):
    names = [n.strip() for n in names if n.strip()]
    unknown = [n for n in names if n not in ALIASES]
    if unknown:
        return ("Diese Modellnamen gibt es nicht: " + ", ".join(unknown) +
                f". Modellnamen von „{label}“ nicht geändert.")
    try:
        old, new = store.set_aliases(DB, label, user, names)
    except ValueError as exc:
        return str(exc)
    if old == new:
        return f"Modellnamen von „{label}“ unverändert."
    row = next((r for r in store.keys(DB) if r["label"] == label), None)
    warn = unreachable_note(label, unreachable_aliases(names, ceiling_of(row), pbd_of(row))) if row else ""
    return (f"Modellnamen von „{label}“ geändert: {old or 'alle'} → {new or 'alle'} "
            "-- gilt ab der nächsten Anfrage, das Geheimnis bleibt." + (" " + warn if warn else ""))


def renew_link(row):
    return (f'<a class="oaap-btn" href="schluessel?{urlencode({"neu_wie": row["label"]})}#ausstellen">'
            f'Neu ausstellen</a> <span class="muted">mit denselben Angaben</span>')


def key_href(label):
    return "schluessel?" + urlencode({"etikett": label})


def aliases_form(row):
    """Modellnamen eines Schlüssels ändern (0.8.0) -- ein Ankreuzfeld je
    Modellname; keins angekreuzt heißt wie beim Ausstellen: alle."""
    have = {a for a in (row["aliases"] or "").split(",") if a}
    boxes = "".join(
        f'<label style="display:block"><input type="checkbox" name="alias" value="{esc(n)}"'
        f'{" checked" if n in have else ""}> <code>{esc(n)}</code></label>'
        for n in sorted(set(ALIASES) | have))
    gone = sorted(have - set(ALIASES))
    return (f'<div class="card"><h2>Modellnamen ändern</h2>'
            f'<form method="post" action="key-aliases">'
            f'<input type="hidden" name="label" value="{esc(row["label"])}">'
            f'<p class="hint">Gilt ab der nächsten Anfrage, das Geheimnis bleibt. Ampel und '
            f'Freigabe bleiben, wie beim Ausstellen entschieden. Nichts angekreuzt = alle.</p>'
            f'{boxes}'
            + (f'<p class="hint">Gibt es nicht mehr: {esc(", ".join(gone))} — Haken entfernen.</p>'
               if gone else "")
            + '<div><button type="submit">Speichern</button></div></form></div>')


def key_state(row):
    """(Wort, Badge-Art) des Zustands eines Schlüssels."""
    if row["revoked"]:
        return "widerrufen", "err"
    if store.expired(row):
        return "abgelaufen", ""
    return "gültig", "ok"


def key_view(row, totals):
    """Was Liste und Objektseite über einen Schlüssel sagen."""
    t = totals.get(row["id"])
    used = (int(t["in_tokens"]) + int(t["out_tokens"])) if t else 0
    budget = (f'{fmt_tokens(used)} / {fmt_tokens(row["budget_tokens"])}'
              if row["budget_tokens"] else f'{fmt_tokens(used)} <span class="muted">/ frei</span>')
    aliases = esc(row["aliases"]) if row["aliases"] else "alle"
    never = unreachable_aliases([a for a in (row["aliases"] or "").split(",") if a],
                                ceiling_of(row), pbd_of(row))
    warn = ""
    if never:
        warn = (' <span class="badge yellow" title="die Obergrenze des Schlüssels lässt '
                'kein Ziel dieser Modellnamen zu">nie erreichbar: ' + esc(", ".join(never)) + '</span>')
    light = light_badge(ceiling_of(row), short=True) + " und besser"
    if pbd_of(row):
        light += ' <span class="badge pbd">pbD freigegeben → nie rot</span>'
    word, kind = key_state(row)
    until = (f' <span class="muted">bis {esc(dash.until(row["expires"]))}</span>'
             if row["expires"] and word != "widerrufen" else "")
    last = t["last"] if t else ""
    return {"t": t, "used": used, "budget": budget, "aliases": aliases, "warn": warn,
            "light": light, "state": af.badge(word, kind) + until, "word": word,
            "calls": int(t["calls"]) if t else 0, "last": last,
            "is_grant": row["kind"] == "client"}


def keys_page(user, roles, host, notice="", secret=None, keep=None, renew_from="", params=None):
    """`keep` füllt das Ausstell-Formular wieder (nach einer Ablehnung oder aus
    `renew_values`); `renew_from` nennt den Schlüssel, aus dem es stammt."""
    keep = keep or {}
    parts = top_cards(notice, secret, host)
    totals = store.totals(DB)
    all_keys = store.keys(DB)
    own = [r for r in all_keys if not r["cohort"]]
    in_cohorts = len(all_keys) - len(own)
    views = {r["id"]: key_view(r, totals) for r in own}
    q = af.Query(params or {}, filters=("zustand", "art", "ampel"), default_sort="label")
    rows = [r for r in own
            if (not q.filters["zustand"] or views[r["id"]]["word"] == q.filters["zustand"])
            and (not q.filters["art"] or (r["kind"] == "client") == (q.filters["art"] == "freigabe"))
            and (not q.filters["ampel"] or ceiling_of(r) == q.filters["ampel"])]
    columns = [
        ("Etikett · Kontext", "label", lambda r: f'{r["label"]} {context_line(r)}', ""),
        ("Zustand", "state", lambda r: views[r["id"]]["word"], ""),
        ("Modellnamen", "aliases", lambda r: r["aliases"] or "alle", "oaap-minor"),
        ("Ampel", "", None, "oaap-minor"),
        ("Token", "tokens", lambda r: views[r["id"]]["used"], "oaap-num"),
        ("Aufrufe", "calls", lambda r: views[r["id"]]["calls"], "oaap-num"),
        ("zuletzt", "last", lambda r: views[r["id"]]["last"] or "", "oaap-minor"),
    ]

    def row_html(r):
        v = views[r["id"]]
        grant = ' ' + af.badge("Freigabe für Chat", "info") if v["is_grant"] else ""
        return (f'<tr{" class=dim" if not store.usable(r) else ""}>'
                f'<td><a href="{esc(key_href(r["label"]))}"><b>{esc(r["label"])}</b></a>{grant}<br>'
                f'<span class="muted">{esc(context_line(r))}</span></td>'
                f'<td>{v["state"]}</td><td class="oaap-minor"><code>{v["aliases"]}</code>{v["warn"]}</td>'
                f'<td class="oaap-minor">{v["light"]}</td>'
                f'<td class="oaap-num">{v["budget"]}</td><td class="oaap-num">{v["calls"]}</td>'
                f'<td class="oaap-minor muted" title="{esc(v["last"] or "")}">'
                f'{esc(dash.ago(v["last"])) if v["last"] else "noch nie"}</td></tr>')
    choices = [("zustand", "Zustand", [("gültig", "gültig"), ("abgelaufen", "abgelaufen"),
                                       ("widerrufen", "widerrufen")]),
               ("art", "Art", [("schluessel", "Schlüssel"), ("freigabe", "Freigabe für Chat")]),
               ("ampel", "Höchstens", [("green", "grün"), ("yellow", "gelb"), ("red", "rot")])]
    report_html = af.list_report(
        q, rows, columns, row_html, base="schluessel?", noun="Schlüssel",
        empty_text='Noch kein Schlüssel ausgestellt. <a href="#ausstellen">Schlüssel ausstellen</a> '
                   'legt den ersten an.', choices=choices, placeholder="Etikett oder Kontext")
    parts.append(f"""<div class="card">
  <div class="cardhead"><h2>Schlüssel</h2><a class="oaap-btn primary" href="#ausstellen">Schlüssel ausstellen</a></div>
  <p class="hint">Wer ein Gateway betreibt, gibt die Erlaubnis — ein Recht wird
    gegeben, nicht gehalten. Ausstellen, Widerrufen und jede Änderung am Kontext
    stehen in der Prüfspur; einzelne Anfragen stehen dort nicht. Ein Klick auf das
    Etikett öffnet den Schlüssel: dort Kontext und Modellnamen ändern, widerrufen
    oder neu ausstellen.</p>
  <p class="hint">Eine <b>Freigabe für Chat</b> legt der Aufbau eines Profils an:
    Sie hat keinen Schlüsselwert und gilt nur für Aufrufe dieses Chats über die
    Plattform. Widerrufen, Modellnamen und Kontext wie bei einem Schlüssel; gezählt
    wird der Chat, nie die Person.</p>
  {report_html}
  {f'<p class="hint" style="margin:.8rem 0 0">Dazu {in_cohorts} Schlüssel aus Kohorten — '
   f'die stehen im Reiter <a href="kohorten">Kohorten</a>.</p>' if in_cohorts else ""}
</div>""")

    alias_hint = ", ".join(sorted(ALIASES)) or "—"
    ceiling = keep.get("ceiling") if keep.get("ceiling") in supply.LIGHT_RANK else supply.DEFAULT_CEILING
    radios = "".join(
        f'<label><input type="radio" name="ceiling" value="{c}"'
        f'{" checked" if c == ceiling else ""}> {word}</label>'
        for c, word in (("green", "grün"), ("yellow", "gelb"), ("red", "rot")))
    origin = (f'<p class="hint">Übernommen von „<b>{esc(renew_from)}</b>“: Kontext, Aliasse, '
              f'Grenzen, Ampel und Freigabe. Etikett und Ablauf sind Vorschläge — bitte prüfen. '
              f'Der alte Schlüssel bleibt, wie er ist.</p>' if renew_from else "")
    parts.append(f"""<div class="card" id="ausstellen">
  <h2>Schlüssel ausstellen</h2>
  {origin}
  {context_datalists(all_keys)}
  <form class="grid" method="post" action="issue">
    <div><label for="label">Etikett (eindeutig, benennt den Verbraucher)</label>
      <input id="label" name="label" type="text" required placeholder="laptop@joerg"
        value="{esc(keep.get('label', ''))}"></div>
    {context_inputs(keep=keep)}
    <div><label for="aliases">Aliasse (leer = alle: {esc(alias_hint)})</label>
      <input id="aliases" name="aliases" type="text" placeholder="chat-default,code"
        value="{esc(keep.get('aliases', ''))}"></div>
    <div><label for="budget">Budget in Token (0 = ohne)</label>
      <input id="budget" name="budget" type="number" min="0" value="{esc(keep.get('budget') or '0')}"></div>
    <div><label for="rate">Anfragen je Minute (0 = Standard {DEFAULT_RATE})</label>
      <input id="rate" name="rate" type="number" min="0" value="{esc(keep.get('rate') or '0')}"></div>
    <div><label for="bis">gültig bis einschließlich (leer = unbefristet)</label>
      <input id="bis" name="bis" type="date" value="{esc(keep.get('bis', ''))}"></div>
    <div><fieldset><legend>Höchstens diese Ampelfarbe</legend>
      {radios}
    </fieldset></div>
    <div><fieldset><legend>Personenbezogene Daten</legend>
      <label><input type="checkbox" name="personal_data" value="1"{" checked" if keep.get("personal_data") == "1" else ""}> freigegeben</label>
    </fieldset></div>
    <div><button class="primary" type="submit">Ausstellen</button></div>
  </form>
  <p class="hint" style="margin-top:.8rem">Die Kontextfelder schlagen vor, was schon
    benutzt wird — einmal angetippt, steht der Wert genau so da wie bei den anderen
    Schlüsseln, und die Auswertung zählt sie zusammen.</p>
  <p class="hint" style="margin-top:.8rem">Ohne Angabe gilt <b>gelb</b> —
    souverän ist das, was passiert, wenn niemand etwas einstellt; <b>rot</b> ist
    eine bewusste Zusatzerlaubnis. Die Freigabe für personenbezogene Daten
    <b>schließt rot aus</b>: Das Gateway darf nicht in die Anfrage sehen und kann
    eine Anfrage deshalb nicht von der anderen unterscheiden — eine Regel, die
    das voraussetzte, wäre ein Versprechen, das wir nicht halten können.</p>
</div>""")

    parts.append(f"""<div class="card">
  <h2>So verbindet sich ein Client</h2>
  <p class="hint">Jeder OpenAI-kompatible Client — LM Studio, ein IDE-Plugin,
    eine Bibliothek, ein anderes KI-Gateway. Die Modellliste zeigt <b>die Aliasse</b>;
    welche Quelle antwortet, entscheidet dieses Gateway.</p>
  <pre>Basis-Adresse : https://{esc(host)}/v1
API-Key       : der ausgestellte Schlüssel
Modell        : {esc(alias_hint)}

curl https://{esc(host)}/v1/chat/completions \\
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \\
  -d '{{"model":"{esc(sorted(ALIASES)[0] if ALIASES else "chat-default")}",
       "messages":[{{"role":"user","content":"Hallo"}}]}}'</pre>
</div>""")
    return page("Schlüssel", "".join(parts), user, roles, tab="schluessel")


def key_page(user, roles, label, notice="", error=False):
    """Die Objektseite eines Schlüssels: Kennzahlen und alle Handlungen, die
    früher in der Tabellenzeile standen (RFC-0118 §7.5)."""
    row = next((r for r in store.keys(DB) if r["label"] == label), None)
    if row is None:
        return page("Unbekannter Schlüssel", af.back("schluessel", "Zurück zu den Schlüsseln")
                    + af.empty(f"Einen Schlüssel „{esc(label)}“ gibt es nicht. "
                               '<a href="schluessel">Zur Liste</a>'), user, roles, tab="schluessel")
    v = key_view(row, store.totals(DB))
    back = (af.back("kohorten?" + urlencode({"kohorte": row["cohort"]}), "Zurück zur Kohorte")
            if row["cohort"] else af.back("schluessel", "Zurück zu den Schlüsseln"))
    t = v["t"]
    facts = af.facts([
        ("Zustand", v["state"]),
        ("Art", "Freigabe für Chat (kein Schlüsselwert)" if v["is_grant"] else "Schlüssel"),
        ("Kontext", esc(context_line(row))),
        ("Modellnamen", f'<code>{v["aliases"]}</code>{v["warn"]}'),
        ("Ampel", v["light"]),
        ("Token", v["budget"]),
        ("Anfragen je Minute", esc(str(row["rate_per_min"] or f"Standard {DEFAULT_RATE}"))),
        ("Aufrufe", f'{v["calls"]} · {esc(dash.ago(v["last"])) if v["last"] else "noch nie"}'),
        ("Daten ↑/↓", f'{fmt_bytes(t["bytes_up"] if t else 0)} / {fmt_bytes(t["bytes_down"] if t else 0)}'),
    ])
    parts = [back, af.msg(notice, error) if notice else "",
             f'<div class="oaap-objhead"><h1>{esc(row["label"])}</h1>{facts}</div>']
    if store.usable(row):
        parts.append(f"""<div class="card"><h2>Kontext ändern</h2>
  <p class="hint">Ein geänderter Kontext gilt <b>ab der nächsten Anfrage</b> — gezählte Anfragen
    behalten den, mit dem sie kamen.</p>
  {context_datalists(store.keys(DB))}
  <form class="grid" method="post" action="context">
    <input type="hidden" name="label" value="{esc(row["label"])}">
    {context_inputs(row, prefix=row["id"] + "-")}
    <div><button type="submit">Speichern</button></div>
  </form></div>""")
        parts.append(aliases_form(row))
        parts.append(f"""<div class="card danger"><h2>Widerrufen</h2>
  <p class="hint">Ab sofort abgelehnt. Gezählte Anfragen bleiben in der Auswertung.</p>
  <form method="post" action="revoke" onsubmit="return confirm('{esc(row["label"])} wirklich widerrufen?')">
    <input type="hidden" name="label" value="{esc(row["label"])}">
    {'<input type="hidden" name="back" value="kohorten">' if row["cohort"] else ""}
    <button class="danger" type="submit">Widerrufen</button></form></div>""")
    elif not v["is_grant"] and not row["cohort"]:
        parts.append(f'<div class="card"><h2>Neu ausstellen</h2><p class="hint">Ein widerrufener oder '
                     f'abgelaufener Schlüssel gilt nicht wieder. Ein neuer übernimmt Kontext, Aliasse, '
                     f'Grenzen, Ampel und Freigabe.</p><p>{renew_link(row)}</p></div>')
    return page(row["label"], "".join(parts), user, roles, tab="kohorten" if row["cohort"] else "schluessel",
                obj=f"Schlüssel:{row['label']}")


def models_page(user, roles, notice="", tests=None, params=None):
    parts = top_cards(notice) + config_cards()
    if tests:
        parts.append(tests)
    # Wer gerade Modelle abruft, sieht die Auswahl oben; sonst steht die Liste vorn.
    if (params or {}).get("quelle"):
        parts += [import_card(params), aliases_card(params)]
    else:
        parts += [aliases_card(params), import_card({})]
    return page("Modelle", "".join(parts), user, roles, tab="modelle")


def sources_page(user, roles, notice="", tests=None):
    parts = top_cards(notice) + config_cards()
    if tests:
        parts.append(tests)
    parts.append(suppliers_card())
    return page("Bezugsquellen", "".join(parts), user, roles, tab="quellen")


# ---- Kohorten (0.6): zeitlich begrenzte Schlüssel für Schulungen

def end_of_day(text):
    """`2026-11-20` → Ende dieses Tages in Ortszeit, als UTC-Zeitstempel; leer bleibt leer."""
    text = (text or "").strip()
    if not text:
        return ""
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"„{text}“ ist kein Datum.")
    return dash.stamp(day.replace(hour=23, minute=59, second=59, tzinfo=dash.LOCAL))


def _today_plus(days):
    return (datetime.now(timezone.utc).astimezone(dash.LOCAL) + timedelta(days=days)).strftime("%Y-%m-%d")


def issue_cohort(form, multi, user):
    """Liest die eingefügte Tabelle, stellt je Zeile einen Schlüssel aus, gibt die Tabelle
    mit der Schlüsselspalte zurück. Die Werte stehen nur in dieser einen Antwort."""
    name = form.get("kohorte", "").strip().lower()
    if not cohort.COHORT_NAME.match(name):
        return {"error": f"Name der Kohorte: {cohort.COHORT_RULE}."}
    try:
        expires = end_of_day(form.get("bis", ""))
    except ValueError as exc:
        return {"error": str(exc)}
    if not expires:
        return {"error": "Eine Kohorte braucht ein Ende — „gültig bis“ angeben."}
    if expires <= store.now():
        return {"error": "„gültig bis“ liegt in der Vergangenheit."}
    sep, header, rows = cohort.parse(form.get("teilnehmer", ""))
    if not rows:
        return {"error": "Keine Teilnehmer — mindestens eine Zeile mit einem Namen einfügen."}
    first = cohort.normalize_kennung(form.get("erste", "01")) or "01"
    taken = {r["label"] for r in store.keys(DB)}
    planned, generated = cohort.plan(rows, name, taken, first=int(first[:2]),
                                     auto_free=form.get("frei") == "1")
    aliases = [a for a in multi.get("alias", []) if a in ALIASES]
    ceiling = form.get("ceiling") if form.get("ceiling") in supply.LIGHT_RANK else supply.DEFAULT_CEILING
    keys = {}
    for p in planned:
        if p["error"]:
            continue
        try:
            keys[p["label"]], _ = store.issue(
                DB, p["label"], user, aliases=aliases, ceiling=ceiling,
                budget_tokens=_int(form.get("budget")), rate_per_min=_int(form.get("rate")),
                owner=p["name"], project=form.get("project", "").strip() or name,
                cost_center=form.get("cost_center", ""), application=form.get("application", ""),
                expires=expires, cohort=name)
        except ValueError as exc:
            p["error"] = str(exc)
    text = cohort.output(sep, header, planned, keys, generated)
    download = cohort.output(";", header, planned, keys, generated)
    return {"name": name, "planned": planned, "keys": keys, "header": header,
            "generated": generated, "text": text, "sep": sep or "\t", "expires": expires,
            "download": download}


def cohort_result_card(r):
    if r.get("error"):
        return f'<div class="card attention"><p style="margin:0">{esc(r["error"])}</p></div>'
    ok = len(r["keys"])
    bad = [p for p in r["planned"] if p["error"]]
    head = r["header"] or ["Name"]
    width = max([len(head)] + [len(p["cells"]) for p in r["planned"]])
    head = list(head) + [f"Spalte {i + 1}" for i in range(len(head), width)]
    ths = "".join(f"<th>{esc(h)}</th>" for h in head)
    ths += ("<th>Kennung</th>" if r["generated"] else "") + "<th>Schlüssel</th>"
    trs = []
    for p in r["planned"]:
        cells = list(p["cells"]) + [""] * (width - len(p["cells"]))
        tds = "".join(f"<td>{esc(c)}</td>" for c in cells)
        if r["generated"]:
            tds += f'<td><code>{esc(p["kennung"])}</code></td>'
        value = r["keys"].get(p["label"])
        tds += (f'<td><code class="key">{esc(value)}</code></td>' if value else
                f'<td><span class="badge err">nicht ausgestellt</span> '
                f'<span class="muted">{esc(p["error"])}</span></td>')
        trs.append(f'<tr{"" if value else " class=dim"}>{tds}</tr>')
    blob = base64.b64encode(("﻿" + r["download"]).encode("utf-8")).decode("ascii")
    sep_word = {"\t": "Tabulator", ";": "Semikolon", ",": "Komma"}[r["sep"]]
    return f"""<div class="card secretbox">
  <div class="cardhead"><h2>Kohorte „{esc(r['name'])}“ — {ok} Schlüssel, jetzt sichern</h2>
    <a class="btn" download="{esc(r['name'])}-schluessel.csv"
       href="data:text/csv;charset=utf-8;base64,{blob}">Als CSV herunterladen</a></div>
  <p class="hint">Die Schlüssel werden <b>genau einmal</b> angezeigt — gespeichert ist nur ihre
    Prüfsumme. Gültig bis <b>{esc(dash.until(r['expires']))}</b> ({esc(dash.LOCAL_NAME)}).
    {f"<b>{len(bad)} Zeile(n) ohne Schlüssel</b> — Grund in der Tabelle." if bad else ""}</p>
  <div class="scroll"><table><tr>{ths}</tr>{"".join(trs)}</table></div>
  <label for="kohorte-text" style="margin-top:1rem">Dieselbe Tabelle zum Kopieren
    (getrennt mit {sep_word}, wie eingefügt — zurück nach Excel: markieren, kopieren, einfügen)</label>
  <textarea id="kohorte-text" readonly rows="{min(12, len(r['planned']) + 2)}">{esc(r['text'])}</textarea>
</div>"""


def cohorts_page(user, roles, notice="", result=None, keep=None):
    keep = keep if (result and result.get("error")) else {}
    parts = top_cards(notice)
    if result:
        parts.append(cohort_result_card(result))
    existing = store.cohorts(DB)
    alias_boxes = "".join(
        f'<label><input type="checkbox" name="alias" value="{esc(a)}"> {esc(a)}</label>'
        for a in sorted(ALIASES)) or '<span class="muted">noch keine Modellnamen</span>'
    datalist = "".join(f'<option value="{esc(c["cohort"])}">' for c in existing)
    parts.append(f"""<div class="card">
  <h2>Kohorte anlegen oder ergänzen</h2>
  <p class="hint">Für Schulungen: je Teilnehmer ein Schlüssel, alle mit demselben Ende. Die
    Teilnehmer aus Excel oder einer Liste einfügen — eine Zeile je Person, Spalten getrennt
    mit Tab, Semikolon oder Komma. <b>Erste Spalte: Name</b> (mehr braucht es nicht).
    Steht in der zweiten Spalte eine Nummer (<code>07</code>) oder eine abweichende Kennung
    (<code>07b</code>), gilt sie; sonst wird ab der ersten Nummer fortlaufend gezählt.
    Weitere Spalten werden durchgereicht. Zurück kommt dieselbe Tabelle mit einer Spalte für
    den Schlüssel.</p>
  <form method="post" action="cohort-issue">
    <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem 1rem;align-items:end">
      <div><label for="k-name">Kohorte</label>
        <input id="k-name" name="kohorte" type="text" required list="k-known"
          placeholder="kurs-2026-11" value="{esc(keep.get('kohorte', ''))}">
        <datalist id="k-known">{datalist}</datalist></div>
      <div><label for="k-bis">gültig bis einschließlich</label>
        <input id="k-bis" name="bis" type="date" required value="{esc(keep.get('bis') or _today_plus(4))}"></div>
      <div><label for="k-erste">erste Nummer</label>
        <input id="k-erste" name="erste" type="text" value="{esc(keep.get('erste', '01'))}"
          pattern="[0-9]{{1,2}}" maxlength="2"></div>
      <div><fieldset><legend>Kennung schon vergeben</legend><label>
        <input type="checkbox" name="frei" value="1"{" checked" if keep.get("frei") == "1" else ""}>
        nächste freie nehmen (07 → 07b)</label></fieldset></div>
    </div>
    <label for="k-tn" style="margin-top:.9rem">Teilnehmer</label>
    <textarea id="k-tn" name="teilnehmer" rows="8" required
      placeholder="Anna Beispiel&#10;Bernd Muster;07&#10;Carla Probe;12;carla@example.org">{esc(keep.get('teilnehmer', ''))}</textarea>
    <details style="margin-top:.8rem" open><summary class="muted">Für die Auswertung und Grenzen</summary>
      <div class="grid" style="display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem 1rem;align-items:end;margin-top:.6rem">
        <div><label for="k-proj">Projekt (leer = Name der Kohorte)</label>
          <input id="k-proj" name="project" type="text" maxlength="{store.MAX_TEXT}" value="{esc(keep.get('project', ''))}"></div>
        <div><label for="k-cc">Kostenstelle</label>
          <input id="k-cc" name="cost_center" type="text" maxlength="{store.MAX_TEXT}" value="{esc(keep.get('cost_center', ''))}"></div>
        <div><label for="k-app">Anwendung</label>
          <input id="k-app" name="application" type="text" maxlength="{store.MAX_TEXT}" value="{esc(keep.get('application', ''))}"></div>
        <div><label for="k-budget">Budget je Teilnehmer in Token (0 = ohne)</label>
          <input id="k-budget" name="budget" type="number" min="0" value="{esc(keep.get('budget', '0'))}"></div>
        <div><label for="k-rate">Anfragen je Minute (0 = Standard {DEFAULT_RATE})</label>
          <input id="k-rate" name="rate" type="number" min="0" value="{esc(keep.get('rate', '0'))}"></div>
        <div><fieldset><legend>Höchstens diese Ampelfarbe</legend>
          <label><input type="radio" name="ceiling" value="green"> grün</label>
          <label><input type="radio" name="ceiling" value="yellow" checked> gelb</label>
          <label><input type="radio" name="ceiling" value="red"> rot</label></fieldset></div>
      </div>
      <fieldset style="margin-top:.8rem"><legend>Nur diese Modellnamen (keiner angehakt = alle)</legend>
        {alias_boxes}</fieldset>
    </details>
    <p style="margin:1rem 0 0"><button type="submit">Schlüssel erzeugen</button></p>
  </form>
  <p class="hint" style="margin-top:.8rem">Der Name jeder Zeile wird zum „Verantwortlichen“ des
    Schlüssels und steht damit in Auswertung und Prüfspur. Wer das nicht möchte, fügt Kürzel
    oder nur Nummern ein. Ein widerrufener Schlüssel behält seine Kennung — zum Nachgenerieren
    eine abweichende angeben (<code>07b</code>) oder „nächste freie nehmen“ wählen.</p>
</div>""")

    totals = store.totals(DB)
    rows = []
    for c in existing:
        s = cohort_summary(c, totals)
        rows.append(f'<tr><td><a href="kohorten?{esc(urlencode({"kohorte": c["cohort"]}))}">'
                    f'<b>{esc(c["cohort"])}</b></a></td><td>{s["state"]}</td>'
                    f'<td class="muted">{esc(dash.until(c["expires"])) if c["expires"] else "unbefristet"}</td>'
                    f'<td class="oaap-minor">{esc(c["project"] or "—")}</td>'
                    f'<td class="oaap-num">{charts.fmt_num(s["calls"])}</td>'
                    f'<td class="oaap-num oaap-minor">{charts.fmt_num(s["tokens"])}</td></tr>')
    parts.insert(len(parts) - 1, f"""<div class="card">
  <h2>Kohorten</h2>
  <p class="hint">Ein Klick auf eine Kohorte zeigt ihre Teilnehmer; dort wird verlängert oder
    widerrufen. Verlängern gilt für alle nicht widerrufenen Schlüssel der Kohorte — auch
    abgelaufene gelten danach wieder.</p>
  {af.table('<th>Kohorte</th><th>Zustand</th><th>bis</th><th class="oaap-minor">Projekt</th>'
            '<th class="oaap-num">Aufrufe</th><th class="oaap-num oaap-minor">Token</th>', rows,
            "Noch keine Kohorte. <b>Kohorte anlegen</b> unten erzeugt die ersten Schlüssel.")}
</div>""")
    return page("Kohorten", "".join(parts), user, roles, tab="kohorten")


def cohort_summary(c, totals):
    rows = store.cohort_keys(DB, c["cohort"])
    calls = sum(int(totals[r["id"]]["calls"]) for r in rows if r["id"] in totals)
    tokens = sum(int(totals[r["id"]]["in_tokens"]) + int(totals[r["id"]]["out_tokens"])
                 for r in rows if r["id"] in totals)
    live = int(c["live"] or 0)
    ended = bool(c["expires"]) and c["expires"] <= store.now()
    state = (af.badge(f'{live} von {c["seats"]} gültig', "ok") if live else
             af.badge("abgelaufen") if ended else af.badge("widerrufen", "err"))
    return {"rows": rows, "calls": calls, "tokens": tokens, "live": live, "state": state}


def cohort_page(user, roles, name, notice=""):
    """Die Objektseite einer Kohorte: Teilnehmer, verlängern, alle widerrufen."""
    c = next((x for x in store.cohorts(DB) if x["cohort"] == name), None)
    back = af.back("kohorten", "Zurück zu den Kohorten")
    if c is None:
        return page("Unbekannte Kohorte", back + "".join(top_cards(notice))
                    + af.empty(f"Eine Kohorte „{esc(name)}“ gibt es nicht. "
                               '<a href="kohorten">Zur Liste</a>'), user, roles, tab="kohorten")
    totals = store.totals(DB)
    s = cohort_summary(c, totals)
    seats = []
    for r in s["rows"]:
        t = totals.get(r["id"])
        kennung = r["label"][len(c["cohort"]) + len(cohort.SEAT) + 2:]
        word, kind = key_state(r)
        seats.append(f'<tr{"" if store.usable(r) else " class=dim"}>'
                     f'<td><a href="{esc(key_href(r["label"]))}"><code>{esc(kennung)}</code></a></td>'
                     f'<td>{esc(r["owner"])}</td><td>{af.badge(word, kind)}</td>'
                     f'<td class="oaap-num">{t["calls"] if t else 0}</td>'
                     f'<td class="oaap-num">{fmt_tokens((int(t["in_tokens"]) + int(t["out_tokens"])) if t else 0)}</td>'
                     f'<td class="muted oaap-minor">{esc(dash.ago(t["last"])) if t else "noch nie"}</td></tr>')
    report_link = f'auswertung?{urlencode({"project": c["project"], "nach": "key", "top": "key"})}'
    facts = af.facts([
        ("Zustand", s["state"]),
        ("gültig bis", esc(dash.until(c["expires"])) if c["expires"] else "unbefristet"),
        ("Projekt", esc(c["project"] or "—")),
        ("Aufrufe", charts.fmt_num(s["calls"])), ("Token", charts.fmt_num(s["tokens"])),
        ("Auswertung", f'<a href="{esc(report_link)}">ansehen</a>'),
    ])
    parts = [back] + top_cards(notice) + [f'<div class="oaap-objhead"><h1>{esc(c["cohort"])}</h1>{facts}</div>']
    parts.append(f"""<div class="card"><h2>Verlängern</h2>
  <p class="hint">Gilt für alle nicht widerrufenen Schlüssel der Kohorte — auch abgelaufene gelten
    danach wieder. Ein abgelaufener Schlüssel bekommt dieselbe Antwort wie ein widerrufener.</p>
  <form method="post" action="cohort-extend" class="inline">
    <input type="hidden" name="kohorte" value="{esc(c['cohort'])}">
    <input type="date" name="bis" required value="{esc(_today_plus(7))}" aria-label="neues Ende">
    <button type="submit">Verlängern</button></form></div>""")
    if s["live"]:
        parts.append(f"""<div class="card danger"><h2>Alle widerrufen</h2>
  <form method="post" action="cohort-revoke" class="inline">
    <input type="hidden" name="kohorte" value="{esc(c['cohort'])}">
    <label><input type="checkbox" name="confirm" value="1"> wirklich</label>
    <button class="danger" type="submit">Alle widerrufen</button></form></div>""")
    parts.append(f"""<div class="card"><h2>{c['seats']} Teilnehmer</h2>
  <p class="hint">Ein Klick auf die Kennung öffnet den Schlüssel — dort lässt sich ein einzelner
    widerrufen.</p>
  {af.table('<th>Kennung</th><th>Name</th><th>Zustand</th><th class="oaap-num">Aufrufe</th>'
            '<th class="oaap-num">Token</th><th class="oaap-minor">zuletzt</th>', seats, "Keine Teilnehmer.")}
</div>""")
    return page(c["cohort"], "".join(parts), user, roles, tab="kohorten", obj=f"Kohorte:{c['cohort']}")


# ---- Modellnamen aus einer Bezugsquelle (0.6)

IMPORT_SHOW = 300


def suggest_alias_name(model):
    """`meta-llama/llama-3.3-70b:free` → `llama-3.3-70b-free` — erlaubt sind A-Z a-z 0-9 _ . -"""
    tail = model.rsplit("/", 1)[-1].lower()
    name = re.sub(r"[^a-z0-9_.-]+", "-", tail).strip("-.")
    name = re.sub(r"-{2,}", "-", name)
    return name[:40].strip("-.") or "modell"


def import_card(params):
    names = sorted(SUPPLIERS)
    src = params.get("quelle", "")
    q = params.get("suche", "").strip().lower()
    pick = f"""<form class="grid" method="get" action="modelle#import">
    <div><label for="im-src">Bezugsquelle</label><select id="im-src" name="quelle">
      {options(names, src, {n: n for n in names})}</select></div>
    <div><label for="im-q">Suche (optional)</label>
      <input id="im-q" name="suche" type="text" value="{esc(params.get('suche', ''))}" placeholder="llama"></div>
    <div><button type="submit">Modelle abrufen</button></div>
  </form>"""
    body = ""
    if src and src in SUPPLIERS:
        r = probe_supplier(src)
        cls, text, advice = probe_verdict(r)
        if cls != "good" or not r["ids"]:
            body = (f'<div class="probe {cls}"><span class="pm">{charts.STATUS[cls][1]}</span><div>'
                    f'<b>{esc(src)}</b> — {text}{f"<br><span class=muted>{advice}</span>" if advice else ""}'
                    f'</div></div>')
        else:
            have = {}
            for aname, alias in ALIASES.items():
                for t in alias["targets"]:
                    if t["supplier"] == src:
                        have.setdefault(t["model"], []).append(aname)
            ids = sorted(set(r["ids"]))
            shown = [m for m in ids if q in m.lower()] if q else ids
            rows = []
            for i, model in enumerate(shown[:IMPORT_SHOW]):
                used = have.get(model)
                suggestion = suggest_alias_name(model)
                taken = suggestion in ALIASES
                rows.append(
                    f'<tr{" class=dim" if used else ""}><td><input type="checkbox" name="pick" value="{i}" '
                    f'id="pk{i}" aria-label="{esc(model)} übernehmen"></td>'
                    f'<td><label for="pk{i}" style="margin:0;color:inherit;font-size:.92rem">'
                    f'<code>{esc(model)}</code></label>'
                    f'<input type="hidden" name="id_{i}" value="{esc(model)}"></td>'
                    f'<td><input type="text" name="name_{i}" value="{esc(suggestion)}" maxlength="40" '
                    f'aria-label="Modellname für {esc(model)}"></td>'
                    f'<td class="muted">{("schon als " + ", ".join(used)) if used else ("Name vergeben" if taken else "")}</td></tr>')
            more = (f'<p class="hint">Gezeigt sind {IMPORT_SHOW} von {len(shown)} — mit der Suche '
                    f'eingrenzen.</p>' if len(shown) > IMPORT_SHOW else "")
            body = f"""<p class="summary"><b>{esc(src)}</b> nennt {len(ids)} Modelle
    {f"· {len(shown)} passen zu „{esc(q)}“" if q else ""} · abgerufen in {charts.fmt_num(r['ms'])} ms</p>
  <form method="post" action="alias-import">
    <input type="hidden" name="quelle" value="{esc(src)}">
    <div class="scroll"><table><tr><th></th><th>Modell der Quelle</th><th>Modellname im Gateway</th><th></th></tr>
      {"".join(rows) or '<tr><td colspan="4" class="muted">Kein Modell passt.</td></tr>'}</table></div>
    {more}
    <p class="rowactions" style="margin-top:.8rem">
      <button type="submit">Angehakte anlegen</button>
      <button class="quiet" type="submit" name="all" value="1">alle gezeigten anlegen ({min(len(shown), IMPORT_SHOW)})</button></p>
  </form>"""
    elif src:
        body = f'<p class="muted">Eine Bezugsquelle „{esc(src)}“ gibt es nicht.</p>'
    if MODE != "app":
        return ""
    return f"""<div class="card" id="import">
  <h2>Modellnamen aus einer Bezugsquelle anlegen</h2>
  <p class="hint">Das Gateway fragt die Quelle nach ihren Modellen. Anhaken, Namen bei Bedarf
    ändern, anlegen — je Modell ein Modellname mit genau diesem Ziel. Ausweichen auf eine zweite
    Quelle trägst du danach unter „ändern“ ein. Vorhandene Namen werden nie überschrieben.</p>
  {pick}{body}
</div>"""


def import_aliases(form, multi, user):
    if MODE != "app":
        return "Gesperrt, solange die Übernahme aus der Konfiguration wartet (AIGW_MASTER_KEY fehlt)."
    src = form.get("quelle", "")
    if src not in SUPPLIERS:
        return f"Eine Bezugsquelle „{src}“ gibt es nicht."
    if form.get("all") == "1":
        picks = sorted({k[3:] for k in form if k.startswith("id_")}, key=lambda x: int(x) if x.isdigit() else 0)
    else:
        picks = multi.get("pick", [])
    if not picks:
        return "Nichts angehakt — nichts angelegt."
    made, skipped, failed = [], [], []
    for i in picks:
        model = form.get(f"id_{i}", "")
        name = (form.get(f"name_{i}", "") or suggest_alias_name(model)).strip()
        if not model:
            continue
        if "," in model:
            failed.append(f"{model} (Komma im Namen der Quelle)")
            continue
        if name in ALIASES or name in made:
            skipped.append(name)
            continue
        try:
            catalog.set_alias(DB, user, name, f"{src}:{model}", False)
            made.append(name)
        except ValueError as exc:
            failed.append(f"{name}: {exc}")
    reload_supply()
    out = f"{len(made)} Modellname(n) angelegt" + (f": {', '.join(made[:12])}{' …' if len(made) > 12 else ''}" if made else "") + "."
    if skipped:
        out += f" Schon vorhanden, nicht geändert: {', '.join(skipped[:12])}."
    if failed:
        out += f" Nicht angelegt: {'; '.join(failed[:6])}."
    return out


# ---- Verbindungstests (0.6)

PROBE_TIMEOUT = 15
ALIAS_TEST_TIMEOUT = 60


def probe_supplier(name):
    """Ein Ping an eine Bezugsquelle: `GET …/models` mit ihren Zugangsdaten."""
    src = SUPPLIERS.get(name)
    via = destination_of(src["url"], bound_destinations()) if src else ""
    if src is None:
        return {"name": name, "status": None, "ms": 0, "ids": [], "via": via,
                "error": "nicht nutzbar (Zugangsdaten nicht lesbar oder Eintrag fehlerhaft)"}
    status, ms, entries, error = relay.probe_list(src, timeout=PROBE_TIMEOUT)
    SEEN.note(name, assets.reached(status))
    if entries and MODE == "app":
        try:
            learn(name, entries)
        except Exception as exc:                        # noqa: BLE001
            sys.stdout.write(f"Eigenschaften aus der Modellliste: {type(exc).__name__}\n")
    return {"name": name, "status": status, "ms": ms, "ids": [e["id"] for e in entries],
            "listed": {e["id"]: e["props"] for e in entries if e["props"]},
            "error": error, "via": via}


# 0.10.0 (RFC-0104 §3.1): die Einrichtungstür. Von außen unter `/` und damit
# hinter `admin`; hinein kommt nur, wer OAAP_SETUP_KEY kennt (setupdoor.py).
SETUP_PATH = "/oaap/setup"
DOOR = setupdoor.Door(DB, mode=lambda: MODE, master=lambda: MASTER, reload=reload_supply,
                      probe=lambda name: probe_supplier(name),
                      loaded=lambda: (SUPPLIERS, ALIASES))


def asset_answer():
    """Die Antwort der Tür /oaap/assets (Manifest 0.15, RFC-0087 §8.2)."""
    suppliers, aliases = SUPPLIERS, ALIASES
    dests = bound_destinations()
    via = {n: destination_of(src["url"], dests) for n, src in suppliers.items()}
    SEEN.forget(set(suppliers))
    tab = props.table(DB)
    return assets.answer(suppliers, aliases, SEEN, {n: d for n, d in via.items() if d},
                         props={n: props.api_fields(props.of_alias(a, tab))
                                for n, a in aliases.items()})


def check_destination_sources():
    """Je Minute: die Quellen hinter einer Destination der Plattform testen
    (eigene Wege, kostet nichts). Fremde Anbieter nicht -- siehe assets.py."""
    while True:
        time.sleep(assets.CHECK_EVERY)
        try:
            dests = bound_destinations()
            names = sorted(n for n, src in SUPPLIERS.items() if destination_of(src["url"], dests))
            if names:
                probe_all(names)
        except Exception as exc:                        # noqa: BLE001
            sys.stdout.write(f"Prüfung der Quellen: {type(exc).__name__}\n")


def probe_all(names):
    results = [None] * len(names)

    def run(i, n):
        results[i] = probe_supplier(n)

    threads = [threading.Thread(target=run, args=(i, n), daemon=True) for i, n in enumerate(names)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(PROBE_TIMEOUT + 5)
    return [r or {"name": names[i], "status": 0, "ms": PROBE_TIMEOUT * 1000, "ids": [],
                  "error": "keine Antwort", "via": ""} for i, r in enumerate(results)]


def probe_verdict(r):
    """(Klasse, Satz, Rat) für ein Testergebnis — in Worten, nicht nur Zahlen."""
    s, via = r["status"], r["via"]
    said = r.get("error") or ""
    # Was die Plattform auf dem Weg durch einen Tunnel sagt
    # (services/connect, gemessen 13.11. auf oaap-test): fehlendes Angebot = 404
    # "The inner node does not offer ... on this tunnel", Tunnel weg = 502.
    if s == 404 and ("does not offer" in said or "is not offered by the inner node" in said):
        return ("critical", f"der Tunnel steht, aber der innere Knoten bietet das Ziel nicht an "
                            f"(HTTP 404, {charts.fmt_num(r['ms'])} ms)",
                "Auf dem inneren Knoten im Portal unter Connector das Angebot anlegen — "
                "Name wie im <code>via:&lt;tunnel&gt;/&lt;angebot&gt;</code> der Destination, "
                "Ziel ohne <code>/v1</code>.")
    if s == 502 and ("is not connected" in said or "There is no tunnel" in said):
        return ("critical", f"der Tunnel ist nicht verbunden (HTTP 502, {charts.fmt_num(r['ms'])} ms)",
                "Auf dem inneren Knoten im Portal unter Connector nachsehen: angelegt, nicht "
                "pausiert, Schlüssel gültig? Auf dem äußeren Knoten: Schlüssel für diesen "
                "Mandanten ausgestellt?")
    where = (f" Die Quelle läuft über die Destination <code>{esc(via)}</code>: im Portal unter "
             f"Destinationen die Anmeldung und unter Connector Tunnel und Angebot prüfen."
             if via else "")
    if s is None:
        return "critical", esc(said), ""
    if s == 0:
        return ("critical", f"nicht erreichbar — {esc(said)}",
                "Adresse und Netz prüfen; eine Quelle auf demselben Knoten braucht eine "
                "App-zu-App-Verbindung oder eine Destination." + where)
    if 200 <= s < 300:
        n = len(r["ids"])
        shown = ", ".join(r["ids"][:8]) + (" …" if n > 8 else "")
        return ("good", f"erreichbar — HTTP {s} in {charts.fmt_num(r['ms'])} ms"
                        + (f" · {n} Modell{'e' if n != 1 else ''}: <code>{esc(shown)}</code>" if n else
                           " · die Quelle nennt keine Modelle"), "")
    if s in (401, 403):
        return ("serious", f"erreichbar, aber die Anmeldung wurde abgelehnt (HTTP {s}, "
                           f"{charts.fmt_num(r['ms'])} ms)",
                "Der Weg steht. Zugangsdaten prüfen — bei einem anderen KI-Gateway: dessen "
                "API-Schlüssel." + where)
    if s == 404:
        return ("warning", f"erreichbar, aber <code>…/models</code> ist unbekannt (HTTP 404)",
                "Adresse bis <code>/v1</code> prüfen — und kein doppeltes <code>/v1</code>: "
                "Endet ein Connector-Angebot schon auf <code>/v1</code>, wird daraus "
                "<code>/v1/v1</code>. Manche Quellen haben keine Modellliste — dann zeigt der "
                "Test eines Modellnamens (Reiter Modelle), ob sie antwortet." + where)
    if s in (502, 503, 504):
        return ("critical", f"die Gegenstelle antwortet nicht (HTTP {s}, {charts.fmt_num(r['ms'])} ms)",
                ("Die Plattform erreicht das Ziel hinter der Destination nicht — ist der Tunnel "
                 "verbunden und das Angebot angelegt?" + where) if via else
                "Die Quelle oder ein Proxy davor ist gestört.")
    return ("warning", f"antwortet mit HTTP {s} ({charts.fmt_num(r['ms'])} ms)", where)


def probe_card(results, title="Verbindungstest"):
    rows = []
    for r in results:
        cls, text, advice = probe_verdict(r)
        mark = charts.STATUS[cls][1]
        if cls == "good" and r["ids"]:
            text += (f' · <a href="modelle?quelle={esc(r["name"])}#import">'
                     f'Modellnamen daraus anlegen →</a>')
        rows.append(f'<div class="probe {cls}"><span class="pm">{mark}</span><div>'
                    f'<b>{esc(r["name"])}</b> — {text}'
                    f'{f"<br><span class=muted>{advice}</span>" if advice else ""}</div></div>')
    stamp_ = datetime.now(timezone.utc).astimezone(dash.LOCAL).strftime("%H:%M:%S")
    return (f'<div class="card"><div class="cardhead"><h2>{esc(title)}</h2>'
            f'<span class="muted">{stamp_}</span></div>{"".join(rows)}'
            f'<p class="hint" style="margin:.6rem 0 0">Gefragt wurde nach der Modellliste — '
            f'ein Ping, der auch die Anmeldung prüft, aber nichts rechnen lässt.</p></div>')


def test_alias(name):
    """Ein Modellname, Ziel für Ziel: eine Mini-Anfrage („ping“, höchstens 1 Token).

    Nicht in den Verbrauch gezählt — es gibt keinen Schlüssel, dem er gehört.
    Die Quelle selbst zählt ihn (wenige Token).
    """
    alias = ALIASES.get(name)
    if alias is None:
        return [{"target": name, "cls": "critical", "text": "gibt es nicht (mehr)"}]
    out = []
    for target in alias["targets"]:
        src = SUPPLIERS.get(target["supplier"])
        label = f'{target["supplier"]} · {target["model"]}'
        if src is None:
            out.append({"target": label, "cls": "critical", "text": "Quelle nicht nutzbar"})
            continue
        started = time.monotonic()
        up, counts = relay.call_json(src, "/v1/chat/completions",
                                     {"model": target["model"], "max_tokens": 1, "stream": False,
                                      "messages": [{"role": "user", "content": "ping"}]},
                                     timeout=ALIAS_TEST_TIMEOUT)
        ms = int((time.monotonic() - started) * 1000)
        if 200 <= up.status < 300:
            tok = (f" · {counts[0]} + {counts[1]} Token" if counts[0] is not None else "")
            out.append({"target": label, "cls": "good",
                        "text": f"antwortet — HTTP {up.status} in {charts.fmt_num(ms)} ms{tok}"})
        elif up.status == 0:
            out.append({"target": label, "cls": "critical",
                        "text": f"nicht erreichbar — {esc(up.error[:200])}"})
        else:
            hint = {401: "Anmeldung abgelehnt", 403: "Anmeldung abgelehnt",
                    404: "Modell oder Pfad unbekannt", 400: "Anfrage abgelehnt — "
                    "ist es ein Chat-Modell? Ein Embedding-Modell antwortet hier nicht"}.get(up.status, "")
            out.append({"target": label, "cls": "serious" if up.status < 500 else "critical",
                        "text": f"HTTP {up.status} in {charts.fmt_num(ms)} ms"
                                + (f" — {hint}" if hint else "")})
    return out


TOOL_TEST_TOOL = {"type": "function", "function": {
    "name": "now", "description": "Returns the current time. Takes no arguments.",
    "parameters": {"type": "object", "properties": {}}}}
TOOL_TEST_MAX_TOKENS = 60


def tool_test(name):
    """„Werkzeugaufruf probieren“ (RFC-0106 §2.4): je Ziel eine kleine Anfrage
    mit einem harmlosen Werkzeug. Nur auf Klick, nicht in den Verbrauch
    gezählt; die Quelle zählt die paar Token.

    Ein Werkzeugaufruf in der Antwort → `tools` ja; eine Antwort ohne → nein;
    eine Ablehnung, die das Feld `tools` nennt → nein; alles andere setzt
    nichts (eine Störung ist keine Auskunft über das Modell).
    """
    alias = ALIASES.get(name)
    if alias is None:
        return [{"target": name, "cls": "critical", "text": "gibt es nicht (mehr)"}]
    out = []
    for target in alias["targets"]:
        src = SUPPLIERS.get(target["supplier"])
        label = f'{target["supplier"]} · {target["model"]}'
        if src is None:
            out.append({"target": label, "cls": "critical", "text": "Quelle nicht nutzbar"})
            continue
        started = time.monotonic()
        up, _counts = relay.call_json(src, "/v1/chat/completions", {
            "model": target["model"], "max_tokens": TOOL_TEST_MAX_TOKENS, "stream": False,
            "tools": [TOOL_TEST_TOOL], "tool_choice": "auto",
            "messages": [{"role": "user", "content": "What time is it? Use the tool now; "
                                                     "do not answer in text."}]},
            timeout=ALIAS_TEST_TIMEOUT)
        ms = int((time.monotonic() - started) * 1000)
        verdict = None
        if 200 <= up.status < 300:
            doc = relay._safe_json(up.body) or {}
            msg = ((doc.get("choices") or [{}])[0] or {}).get("message") or {}
            calls = msg.get("tool_calls") if isinstance(msg, dict) else None
            verdict = "true" if isinstance(calls, list) and calls else "false"
            text = (f"ruft das Werkzeug auf — HTTP {up.status} in {charts.fmt_num(ms)} ms"
                    if verdict == "true" else
                    f"antwortet, ruft aber kein Werkzeug auf — HTTP {up.status} in "
                    f"{charts.fmt_num(ms)} ms")
            cls = "good" if verdict == "true" else "warning"
        elif up.status == 400 and b"tool" in (up.body or b"").lower():
            verdict, cls = "false", "warning"
            text = f"lehnt Werkzeuge ab — HTTP 400 in {charts.fmt_num(ms)} ms"
        elif up.status == 0:
            cls, text = "critical", f"nicht erreichbar — {esc(up.error[:200])}"
        else:
            cls = "serious" if up.status < 500 else "critical"
            text = f"HTTP {up.status} in {charts.fmt_num(ms)} ms — keine Auskunft, nichts gesetzt"
        if verdict is not None and MODE == "app":
            props.note(DB, target["supplier"], target["model"], {"tools": verdict}, "test")
            text += f" · gemerkt: Werkzeuge {'ja' if verdict == 'true' else 'nein'} (aus dem Test)"
        out.append({"target": label, "cls": cls, "text": text})
    return out


def tool_test_card(name, results):
    rows = "".join(
        f'<div class="probe {r["cls"]}"><span class="pm">{charts.STATUS[r["cls"]][1]}</span>'
        f'<div><code>{esc(r["target"])}</code> — {r["text"]}</div></div>' for r in results)
    return (f'<div class="card"><h2>Werkzeugaufruf „{esc(name)}“</h2>{rows}'
            f'<p class="hint" style="margin:.6rem 0 0">Jedes Ziel bekam eine kleine Anfrage mit '
            f'einem harmlosen Werkzeug (<code>now</code>, höchstens {TOOL_TEST_MAX_TOKENS} Token). '
            f'Sie zählt nicht in den Verbrauch dieses Gateways; die Quelle zählt sie. Ein Wert vom '
            f'Betreiber bleibt — die Meldung steht dann daneben.</p></div>')


def change_props(form, user):
    """POST /model-props und /model-props-take — gibt den Satz für die Seite zurück."""
    if MODE != "app":
        return "Gesperrt, solange die Übernahme aus der Konfiguration wartet (AIGW_MASTER_KEY fehlt)."
    supplier, model = form.get("supplier", ""), form.get("model", "")
    used = any(t["supplier"] == supplier and t["model"] == model
               for a in ALIASES.values() for t in a["targets"])
    if not used:
        return f"„{supplier} · {model}“ ist kein Ziel eines Modellnamens."
    try:
        if form.get("prop"):
            props.take_seen(DB, user, supplier, model, form["prop"])
            return f"Übernommen für {supplier} · {model}."
        changed = props.set_operator(DB, user, supplier, model,
                                     {p: form.get(p, "") for p in props.PROPS})
    except ValueError as exc:
        return str(exc)
    return (f"Eigenschaften von {supplier} · {model} gespeichert ({', '.join(changed)})."
            if changed else f"Eigenschaften von {supplier} · {model} unverändert.")


# ---- Vorlagen für Clients (RFC-0106 Stufe 2)

TEMPLATES = templates.load()


def client_file(tpl, names, host):
    """(text, fehlende) für diese Modellnamen; die Adresse ist die dieses Gateways."""
    tab = props.table(DB)
    models = [{"id": n, "props": props.of_alias(ALIASES[n], tab)} for n in names if n in ALIASES]
    return templates.render(tpl, models, f"https://{host}/v1", f"{APP_NAME} {host}")


def templates_result(params, picked, host):
    """(text, vorlage, fehlende, namen, satz) oder None, solange nichts gewählt ist."""
    tpl = TEMPLATES.get(params.get("ziel", ""))
    if tpl is None:
        return None
    if params.get("quelle") == "schluessel":
        label = params.get("schluessel", "")
        row = next((r for r in store.keys(DB, include_revoked=False)
                    if r["label"] == label and r["kind"] == "key"), None)
        if row is None:
            return None
        names = [n for n, _a, _l in key_models(row)]
        said = (f"Die Modellnamen, die der Schlüssel „{label}“ benutzen kann ({len(names)}) — "
                f"genau die Liste, die er unter /v1/models bekommt.")
    else:
        names = [n for n in picked if n in ALIASES]
        said = f"{len(names)} von Hand gewählte Modellnamen."
    if not names:
        return None
    text, missing = client_file(tpl, names, host)
    return text, tpl, missing, names, said


def templates_page(user, roles, host, params, picked, built):
    target = params.get("ziel", "") or (sorted(TEMPLATES)[0] if TEMPLATES else "")
    mode = params.get("quelle", "schluessel")
    keys = [r for r in store.keys(DB, include_revoked=False) if r["kind"] == "key"
            and store.usable(r)]
    key_opts = "".join(f'<option value="{esc(r["label"])}"'
                       f'{" selected" if r["label"] == params.get("schluessel") else ""}>'
                       f'{esc(r["label"])}</option>' for r in keys)
    boxes = "".join(f'<label style="display:inline-block;margin-right:1rem">'
                    f'<input type="checkbox" name="m" value="{esc(n)}"'
                    f'{" checked" if n in picked else ""}> <code>{esc(n)}</code></label>'
                    for n in sorted(ALIASES))
    form = f"""<form method="get" action="vorlagen" class="grid">
    <div><label for="tp-ziel">Werkzeug</label><select id="tp-ziel" name="ziel">
      {options(sorted(TEMPLATES), target, {k: v["title"] for k, v in TEMPLATES.items()})}</select></div>
    <div><fieldset><legend>Modellnamen</legend>
      <label><input type="radio" name="quelle" value="schluessel"{" checked" if mode == "schluessel" else ""}>
        aus einem Schlüssel</label>
      <label><input type="radio" name="quelle" value="hand"{" checked" if mode == "hand" else ""}>
        von Hand</label></fieldset></div>
    <div><label for="tp-key">Schlüssel (Etikett)</label><select id="tp-key" name="schluessel">
      <option value="">—</option>{key_opts}</select></div>
    <div style="grid-column:1/-1"><label>Von Hand (wenn gewählt)</label>{boxes or
      '<span class="muted">Noch kein Modellname.</span>'}</div>
    <div><button type="submit">Datei erzeugen</button></div>
  </form>"""
    parts = [f"""<div class="card">
  <h2>Vorlagen für Clients</h2>
  <p class="hint">Erzeugt die Konfiguration eines Werkzeugs für die Modellnamen dieses Gateways —
    mit den Eigenschaften, die das Gateway kennt (Reiter Modelle). Der Schlüssel steht nie in der
    Datei, nur ein Platzhalter. Wer einen Schlüssel hat, holt sich die Datei auch selbst:
    <code>GET https://{esc(host)}/v1/oaap/client-config?target=&lt;werkzeug&gt;</code>
    mit dem Schlüssel.</p>
  {form}
</div>"""]
    if params.get("ziel") and not built:
        parts.append('<div class="card"><p class="muted">Nichts zu erzeugen — kein Schlüssel '
                     'gewählt, oder er kann keinen Modellnamen erreichen, oder keiner '
                     'angehakt.</p></div>')
    if built:
        text, tpl, missing, names, said = built
        gone = "".join(f'<li><code>{esc(n)}</code>: ' +
                       ", ".join(props.LABEL_DE[p] for p in ps) + '</li>'
                       for n, ps in missing.items())
        query = urlencode([(k, v) for k, v in params.items() if k != "laden"]
                          + [("m", n) for n in picked] + [("laden", "1")])
        parts.append(f"""<div class="card">
  <div class="cardhead"><h2>{esc(tpl["title"])}: <code>{esc(tpl["file"])}</code></h2>
    <a class="button quiet" href="vorlagen?{esc(query)}">herunterladen</a></div>
  <p class="summary">{esc(said)}</p>
  <pre style="white-space:pre-wrap">{esc(text)}</pre>
  <p class="hint"><b>Wohin:</b> {esc(tpl["where"])}</p>
  <p class="hint"><b>Schlüssel:</b> {esc(tpl["key_hint"])}</p>
  {f'<p class="hint"><b>Unbekannt und deshalb weggelassen</b> (das Werkzeug nimmt seinen '
   f'eigenen Vorgabewert; nachtragen im Reiter Modelle):</p><ul class="hint">{gone}</ul>'
   if gone else ''}
  <p class="hint">Geprüft gegen: {esc(tpl["format"])} (am {esc(tpl["checked"])}). Formate von
    Werkzeugen ändern sich — lädt das Werkzeug die Datei nicht, bitte melden.</p>
</div>""")
    return page("Vorlagen", "".join(parts), user, roles, tab="vorlagen")


def alias_test_card(name, results):
    rows = "".join(
        f'<div class="probe {r["cls"]}"><span class="pm">{charts.STATUS[r["cls"]][1]}</span>'
        f'<div><code>{esc(r["target"])}</code> — {r["text"]}</div></div>' for r in results)
    return (f'<div class="card"><h2>Test „{esc(name)}“</h2>{rows}'
            f'<p class="hint" style="margin:.6rem 0 0">Jedes Ziel bekam eine Mini-Anfrage '
            f'(„ping“, höchstens 1 Token). Sie zählt nicht in den Verbrauch dieses Gateways; '
            f'die Quelle zählt sie.</p></div>')


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
    body = f"""<p class="hint">Nur intern. Volumen, keine Preise. Gruppiert wird nach dem Kontext, den ein
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
    return page("Auswertung", body, user, roles, tab="auswertung")


# --------------------------------------------------------------- HTTP

# ------------------------------------------------------------ MCP-Tür (RFC-0118 §9)

# Nur lesen, und nur, was die Betreiberseite derselben Person zeigt: Verbrauch
# und Modellnamen. Nie ein Schlüsselwert, nie Zugangsdaten einer Quelle, nie
# eine Adresse mit Geheimnis, nie Inhalte (es gibt keine).
door = mcpdoor.Door(
    "oaap-ai-gateway", VERSION,
    "Liest den Verbrauch des KI-Gateways (Anfragen, Token, Ausgänge, Ranglisten) und die "
    "angebotenen Modellnamen mit Ampel und Eigenschaften. Nur für die Serververwaltung; "
    "Schlüssel und Zugangsdaten gibt diese Tür nie heraus.")

USAGE_WINDOWS = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}


def _admin_only(who):
    # Dieselbe Regel wie die Seite: nur die Rolle admin.
    if "admin" not in who.get("roles", []):
        raise mcpdoor.Refused("Das KI-Gateway zeigt seinen Verbrauch nur der Serververwaltung.")


@door.tool("ai_gateway_usage", "Verbrauch",
           "Fasst den Verbrauch eines Zeitfensters zusammen: Anfragen, Token ein und aus, nicht "
           "beantwortete Anfragen, mittlere Dauer, und die Ranglisten nach Modellname, "
           "Bezugsquelle, Schlüssel-Etikett und Ausgang. Ohne Inhalte.",
           {"window": {"type": "string", "enum": list(USAGE_WINDOWS),
                       "description": "Zeitfenster: 24h, 7d oder 30d (Standard 24h)"},
            "model": {"type": "string", "description": "nur dieser Modellname"},
            "supplier": {"type": "string", "description": "nur diese Bezugsquelle"}})
def _tool_usage(args, who):
    _admin_only(who)
    win = str(args.get("window") or "24h")
    if win not in USAGE_WINDOWS:
        raise mcpdoor.Refused("window kennt 24h, 7d und 30d.")
    since = dash.stamp(datetime.now(timezone.utc) - timedelta(hours=USAGE_WINDOWS[win]))
    filters = {d: str(args[d]).strip() for d in ("model", "supplier") if str(args.get(d) or "").strip()}
    tot = dash.totals(DB, since, None, filters)

    def ranking(dim, metric="calls"):
        rows, more = dash.top(DB, since, dim, metric, filters)
        return {"top": [{"name": n, metric: v} for n, v in rows], "more": more}
    return {"window": win, "filters": filters,
            "calls": int(tot["calls"]), "answered": int(tot["answered"]),
            "failed": int(tot["failed"]), "input_tokens": int(tot["in_tokens"]),
            "output_tokens": int(tot["out_tokens"]), "avg_ms": int(tot["avg_ms"]),
            "active_keys": int(tot["keys"]),
            "by_model": ranking("model", "tokens"), "by_supplier": ranking("supplier", "tokens"),
            "by_key": ranking("key", "tokens"), "by_outcome": ranking("outcome")}


@door.tool("ai_gateway_models", "Modellnamen",
           "Listet die Modellnamen, die das Gateway anbietet: Ampel (die schlechteste ihrer "
           "Ziele), die Bezugsquellen mit deren Ampel, Reihenfolge und die bekannten "
           "Eigenschaften (Werkzeuge, Bilder, Kontext, Ausgabe).",
           {"q": {"type": "string", "description": "Suchwort im Modellnamen oder Ziel"}})
def _tool_models(args, who):
    _admin_only(who)
    tab = props.table(DB)
    words = str(args.get("q") or "").lower().split()
    out = []
    for name, alias in sorted(ALIASES.items()):
        text = supply.targets_text(alias)
        if not all(w in f"{name} {text}".lower() for w in words):
            continue
        out.append({"name": name, "light": supply.alias_light(alias, SUPPLIERS, "red"),
                    "order": "listed" if alias["order_listed"] else "green-first",
                    "targets": [{"supplier": t["supplier"], "model": t["model"],
                                 "light": SUPPLIERS.get(t["supplier"], {}).get("light", "")}
                                for t in alias["targets"]],
                    "properties": props_summary(props.of_alias(alias, tab))})
    return {"models": out, "count": len(out)}


@door.tool("ai_gateway_suppliers", "Bezugsquellen",
           "Listet die Bezugsquellen mit Namen und Ampel und welche Modellnamen sie benutzen. "
           "Ohne Adressen und ohne Zugangsdaten.")
def _tool_suppliers(args, who):
    _admin_only(who)
    out = []
    for name, src in sorted(SUPPLIERS.items()):
        out.append({"name": name, "light": src["light"],
                    "light_meaning": supply.LIGHT_LABELS.get(src["light"], ""),
                    "used_by": sorted(a for a, alias in ALIASES.items()
                                      if any(t["supplier"] == name for t in alias["targets"]))})
    return {"suppliers": out, "count": len(out)}


door.prompt("ai_gateway_verbrauch_berichten", "Verbrauch berichten",
            "Ein kurzer Bericht über den Verbrauch des KI-Gateways.",
            "Fasse den Verbrauch des KI-Gateways im Zeitfenster {fenster} zusammen (Standard 7d): "
            "Nutze ai_gateway_usage. Nenne Anfragen, Token, die größten Verbraucher nach "
            "Modellname und Schlüssel-Etikett und auffällige Fehler. Kurz, in einfachen Worten.",
            arguments=("fenster",))
door.prompt("ai_gateway_modell_empfehlen", "Modellnamen empfehlen",
            "Empfiehlt einen Modellnamen für eine Aufgabe und eine Datenlage.",
            "Welcher Modellname des KI-Gateways passt für diese Aufgabe: {aufgabe}? Lies "
            "ai_gateway_models. Achte auf die Ampel (grün: Daten bleiben im Unternehmen) und "
            "auf die Eigenschaften, zum Beispiel Werkzeuge oder Bilder.",
            arguments=("aufgabe",))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-ai-gateway/" + VERSION
    asked = ""          # die Ampel des Gesprächs dieser Anfrage (RFC-0101), sonst ''

    def record(self, row, *args):
        """Eine Messzeile mit der Ampel, die diese Anfrage verlangte (ein Wort, kein Inhalt)."""
        store.record(DB, row, *args, light=self.asked)

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
        # Ausgestellte Schlüssel stehen genau einmal auf einer Seite — nie in einem Cache.
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, text, filename):
        """Eine erzeugte Datei zum Herunterladen (RFC-0106 §3.1); nie im Cache."""
        body = text.encode("utf-8")
        safe = "".join(c for c in filename if c.isalnum() or c in "._-") or "datei.json"
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{safe}"')
        self.send_header("Cache-Control", "no-store")
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
        self.record(row, alias, supplier, model, None, None, ms, outcome,
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

    def platform_client(self):
        """Der Chat, für den die via-Tür der Plattform ruft (RFC-0104 §4), oder ''.

        Nur wenn alle drei da sind: `X-OAAP-AI-Client` (von der Tür gesetzt),
        die Kennung der Person und ihre Zusicherung — wie Verfahren A beim
        MCP-Gateway. Auf dem öffentlichen /v1 entfernt die Plattform die
        beiden Identitäts-Kopfzeilen; wer `X-OAAP-AI-Client` allein schickt,
        landet deshalb auf dem gewöhnlichen Weg über den API-Schlüssel. Die
        Person wird nur auf Vorhandensein geprüft und nirgends aufgeschrieben
        (§9 O1): gezählt wird die Freigabe, nicht der Mensch."""
        client = (self.headers.get("X-OAAP-AI-Client") or "").strip()
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        assertion = (self.headers.get("X-OAAP-User-Assertion") or "").strip()
        return client if (client and uid and assertion) else ""

    def authorize(self):
        """Gibt die Schlüsselzeile zurück oder beantwortet die Anfrage selbst.

        Seit 0.10.0 ist die Zeile bei einem Aufruf durch die via-Tür die
        Freigabe des Chats (Art `client`); alles danach — Modellnamen, Ampel,
        pbD, Budget, Rate, Messzeile — läuft für sie genauso wie für einen
        Schlüssel."""
        addr = self.client_addr()
        if failing_hard(addr):
            self.send_error_doc(429, "Zu viele Fehlversuche. Später erneut.",
                                "too_many_requests", [("Retry-After", "60")])
            return None
        client = self.platform_client()
        if client:
            row = store.grant_of(DB, client)
            if not row or not store.usable(row):
                # Dieselbe Antwort wie für einen unbekannten Schlüssel (§8 Köder 6).
                # Kein Fehlversuch: die Plattform rät nicht, und hinter ihrer
                # Adresse sitzen alle Chats des Knotens.
                self.send_json(403, DENIED)
                return None
            return row
        header = self.headers.get("Authorization", "")
        value = header[7:].strip() if header[:7].lower() == "bearer " else ""
        row = store.find(DB, value)
        if not row or row["revoked"]:
            note_failure(addr)
            self.send_json(403, DENIED)
            return None
        if store.expired(row):
            # Dieselbe Antwort wie für einen widerrufenen Schlüssel (Spec §2,
            # §8) — aber kein Fehlversuch: Wer einen echten, abgelaufenen
            # Schlüssel schickt, rät nicht. Sonst bremste eine Schulung, die
            # hinter einer Adresse sitzt, nach Kursende sich selbst aus.
            self.send_json(403, DENIED)
            return None
        return row

    # ---- GET

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION})
            return
        if path == SETUP_PATH:
            self.handle_setup_get()
            return
        if path == "/mcp":
            door.serve(self)
            return
        if path == "/oaap/assets":
            # RFC-0087 §8.2: der Knoten fragt über seinen internen Endpunkt;
            # von außen liegt der Pfad unter `/` und damit hinter `admin`.
            self.send_json(200, asset_answer())
            return
        if path == "/v1/models":
            row = self.authorize()
            if row is None:
                return
            asked, bad = asked_light(self.headers.get(LIGHT_HEADER))
            if bad:
                self.send_error_doc(400, bad, "bad_light")
                return
            data = []
            tab = props.table(DB)
            for name, alias, light in key_models(row, request_ceiling(row, asked)):
                data.append({"id": name, "object": "model", "created": 0,
                             "owned_by": "oaap", "oaap_light": light,
                             "oaap_light_meaning": supply.LIGHT_LABELS[light],
                             "oaap_data_rule": supply.LIGHT_RULES[light],
                             # RFC-0106 §2.5: nur gesetzte Eigenschaften.
                             **props.api_fields(props.of_alias(alias, tab))})
            self.send_json(200, {"object": "list", "data": data})
            return
        if path == "/v1/oaap/client-config":
            # RFC-0106 §3.4: die Datei für die Modellnamen DIESES Schlüssels.
            # Der Schlüssel, mit dem gefragt wurde, steht nicht darin (D9).
            row = self.authorize()
            if row is None:
                return
            query = dict(parse_qsl(self.path.partition("?")[2]))
            tpl = TEMPLATES.get(query.get("target", ""))
            if tpl is None:
                self.send_error_doc(400, "target kennt: " + ", ".join(sorted(TEMPLATES)),
                                    "unknown_target")
                return
            names = [n for n, _a, _l in key_models(row)]
            text, _missing = client_file(tpl, names, self.headers.get("Host", "<gateway>"))
            self.send_file(text, tpl["file"])
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
                                     af.empty('Diese Seite ist der Serververwaltung vorbehalten.')))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        params = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        if path == "/auswertung":
            self.send_html(200, charts_page(user, roles, host, params))
        elif path == "/anfragen":
            self.send_html(200, requests_page(user, roles, params))
        elif path == "/schluessel":
            source = params.get("neu_wie", "")
            if params.get("etikett"):
                self.send_html(200, key_page(user, roles, params["etikett"]))
            elif source:
                all_keys = store.keys(DB)
                row = next((r for r in all_keys if r["label"] == source), None)
                if row is None:
                    self.send_html(200, keys_page(user, roles, host,
                                                  notice=f"Einen Schlüssel „{source}“ gibt es nicht."))
                else:
                    self.send_html(200, keys_page(user, roles, host, renew_from=source,
                                                  keep=renew_values(row, all_keys)))
            else:
                self.send_html(200, keys_page(user, roles, host, params=params))
        elif path == "/kohorten":
            if params.get("kohorte"):
                self.send_html(200, cohort_page(user, roles, params["kohorte"]))
            else:
                self.send_html(200, cohorts_page(user, roles))
        elif path == "/modelle":
            if params.get("name"):
                self.send_html(200, alias_page(user, roles, params["name"]))
            else:
                self.send_html(200, models_page(user, roles, params=params))
        elif path == "/quellen":
            if params.get("name"):
                self.send_html(200, supplier_page(user, roles, params["name"]))
            else:
                self.send_html(200, sources_page(user, roles))
        elif path == "/vorlagen":
            picked = [v for k, v in parse_qsl(self.path.partition("?")[2]) if k == "m"]
            built = templates_result(params, picked, host)
            if built and params.get("laden") == "1":
                self.send_file(built[0], built[1]["file"])
            else:
                self.send_html(200, templates_page(user, roles, host, params, picked, built))
        elif path == "/":
            self.send_html(200, dashboard_page(user, roles, host, params))
        else:
            self.send_html(404, page("Unbekannt", af.empty('Diese Seite gibt es nicht. '
                                                           '<a href="./">Zur Übersicht</a>'),
                                     user, roles, tab="dashboard"))

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

    # ---- Einrichtungstür (RFC-0104 §3.1)

    SETUP_DENIED = {"v": 1, "error": "Nur mit dem Einrichtungsschlüssel dieser Instanz."}

    def handle_setup_get(self):
        # Keine Identitäts-Kopfzeile zählt hier, auch nicht admin — nur der Schlüssel.
        if not setupdoor.allowed(self.headers):
            self.send_json(403, self.SETUP_DENIED)
            return
        params = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        if "kind" not in params and "id" not in params:
            self.send_json(200, DOOR.kinds())
            return
        try:
            if not params.get("id", "").strip() and "id" not in params:
                self.send_json(200, DOOR.items(params.get("kind", "")))
                return
            self.send_json(200, DOOR.state(params.get("kind", ""), params.get("id", "").strip()))
        except setupdoor.Malformed as exc:
            self.send_json(400, {"v": 1, "error": str(exc)})

    def handle_setup_post(self):
        raw = self.read_body()            # erst lesen, dann ablehnen (Keep-Alive)
        if not setupdoor.allowed(self.headers):
            self.send_json(403, self.SETUP_DENIED)
            return
        try:
            what, items, dry = setupdoor.parse(raw)
        except setupdoor.Malformed as exc:
            self.send_json(400, {"v": 1, "error": str(exc)})
            return
        actor = setupdoor.actor_of(self.headers)
        results = (DOOR.apply(items, dry, actor) if what == "apply"
                   else DOOR.remove(items, actor))
        self.send_json(200, {"v": 1, "results": results})

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == SETUP_PATH:
            self.handle_setup_post()
            return
        if path == "/mcp":
            door.serve(self)
            return
        if path in ("/v1/chat/completions", "/v1/embeddings"):
            self.handle_inference(path)
            return
        if path in ("/issue", "/revoke", "/context", "/key-aliases", "/supplier", "/supplier-delete",
                    "/alias", "/alias-delete", "/supplier-test", "/alias-test",
                    "/cohort-issue", "/cohort-extend", "/cohort-revoke", "/alias-import",
                    "/alias-tooltest", "/model-props", "/model-props-take"):
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
            self.send_html(403, page("Kein Zugriff", af.empty("Nicht erlaubt.")))
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
        if path == "/supplier-test":
            name = form.get("name", "")
            names = sorted(SUPPLIERS) if name == "*" else [name]
            if not names:
                self.send_html(200, sources_page(user, roles,
                                                 notice="Noch keine Bezugsquelle zum Testen."))
                return
            title = "Verbindungstest" + ("" if name == "*" else f" „{name}“")
            tests = probe_card(probe_all(names), title)
            if name != "*" and name in SUPPLIERS:
                self.send_html(200, supplier_page(user, roles, name, tests=tests))
            else:
                self.send_html(200, sources_page(user, roles, tests=tests))
            return
        if path.startswith("/cohort-"):
            self.send_html(200, self.handle_cohort(path, form, multi, user, roles))
            return
        if path == "/alias-import":
            self.send_html(200, models_page(user, roles, notice=import_aliases(form, multi, user)))
            return
        if path == "/alias-tooltest":
            name = form.get("name", "")
            self.send_html(200, alias_page(user, roles, name,
                                           tests=tool_test_card(name, tool_test(name))))
            return
        if path in ("/model-props", "/model-props-take"):
            notice = change_props(form, user)
            if form.get("alias"):
                self.send_html(200, alias_page(user, roles, form["alias"], notice=notice))
            else:
                self.send_html(200, models_page(user, roles, notice=notice))
            return
        if path == "/alias-test":
            name = form.get("name", "")
            self.send_html(200, alias_page(user, roles, name,
                                           tests=alias_test_card(name, test_alias(name))))
            return
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
                        expires=end_of_day(form.get("bis", "")),
                        **{name: form.get(name, "") for name in store.CONTEXT_FIELDS})
                    secret = {"label": form.get("label", ""), "value": value}
                    notice = unreachable_note(form.get("label", "").strip(),
                                              unreachable_aliases(aliases, ceiling, personal_data))
                except ValueError as exc:
                    notice = str(exc)
        elif path in ("/supplier", "/supplier-delete", "/alias", "/alias-delete"):
            notice = self.change_supply(path, form, user)
        elif path == "/key-aliases":
            notice = change_key_aliases(form.get("label", ""), multi.get("alias", []), user)
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
        if path in ("/supplier", "/supplier-delete"):
            self.send_html(200, sources_page(user, roles, notice=notice))
        elif path in ("/alias", "/alias-delete"):
            self.send_html(200, models_page(user, roles, notice=notice))
        elif path == "/revoke" and form.get("back") == "kohorten":
            row = next((r for r in store.keys(DB) if r["label"] == form.get("label", "")), None)
            if row is not None and row["cohort"]:
                self.send_html(200, cohort_page(user, roles, row["cohort"], notice=notice))
            else:
                self.send_html(200, cohorts_page(user, roles, notice=notice))
        elif path in ("/context", "/key-aliases"):
            self.send_html(200, key_page(user, roles, form.get("label", ""), notice=notice))
        elif path == "/issue" and not secret:
            # abgelehnt: das Formular kommt mit allem zurück, was eingetippt war
            self.send_html(200, keys_page(user, roles, host, notice=notice, keep=form))
        else:
            self.send_html(200, keys_page(user, roles, host, notice=notice, secret=secret))

    def handle_cohort(self, path, form, multi, user, roles):
        name = form.get("kohorte", "").strip().lower()
        if path == "/cohort-extend":
            try:
                n = store.extend_cohort(DB, name, end_of_day(form.get("bis", "")), user)
                return cohort_page(user, roles, name, notice=f"Kohorte „{name}“: {n} Schlüssel gelten "
                                                             f"jetzt bis {dash.until(end_of_day(form.get('bis', '')))}.")
            except ValueError as exc:
                return cohort_page(user, roles, name, notice=str(exc))
        if path == "/cohort-revoke":
            if form.get("confirm") != "1":
                return cohort_page(user, roles, name, notice="Nicht widerrufen: Für eine ganze Kohorte "
                                                             "bitte „wirklich“ anhaken.")
            labels = store.revoke_cohort(DB, name, user)
            return cohort_page(user, roles, name, notice=f"Kohorte „{name}“: {len(labels)} Schlüssel "
                                                         "widerrufen — ab sofort abgelehnt.")
        return cohorts_page(user, roles, result=issue_cohort(form, multi, user), keep=form)

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
                    drop_credential=form.get("drop_credential") == "1",
                    drop_sampling=form.get("drop_sampling") == "1")
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

        self.asked, bad = asked_light(self.headers.get(LIGHT_HEADER))
        if bad:
            self.send_error_doc(400, bad, "bad_light")
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
        ceiling, pbd = request_ceiling(row, self.asked), pbd_of(row)
        targets = supply.candidates(alias, SUPPLIERS, ceiling, pbd)
        if not targets:
            reasons = supply.blocked_reasons(alias, SUPPLIERS, ceiling, pbd)
            if ceiling != ceiling_of(row) and supply.candidates(alias, SUPPLIERS, ceiling_of(row), pbd):
                # Nur die Ampel des Gesprächs sperrt (RFC-0101): der Schlüssel dürfte.
                worst = supply.alias_light(alias, SUPPLIERS, ceiling_of(row), pbd)
                message = (f"Das Gespräch steht auf {LIGHT_DE[ceiling]}, „{alias_name}“ führt nur "
                           f"über {LIGHT_DE.get(worst, worst)}. Im Chat die Ampel des Gesprächs "
                           "bewusst ändern oder einen Modellnamen mit grünerer Quelle wählen.")
                code = "conversation_light"
            elif reasons:
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
        SEEN.note(src["name"], assets.reached(up.status))
        ms = int((time.monotonic() - started) * 1000)
        if up.status == 0 or up.status >= 500:
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            # Ein 4xx ist die Antwort auf **diese** Anfrage — Ausweichen
            # würde denselben Fehler noch einmal erzeugen.
            self.record(row, alias_name, src["name"], model,
                         counts[0], counts[1], ms, f"upstream_{up.status}",
                         up_bytes, len(up.body))
            self.send_response(up.status)
            self.send_header("Content-Type", up.content_type or "application/json")
            self.send_header("Content-Length", str(len(up.body)))
            self.end_headers()
            self.wfile.write(up.body)
            return True
        self.record(row, alias_name, src["name"], model,
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
        SEEN.note(src["name"], assets.reached(up.status))
        if up.status == 0 or up.status >= 500:
            up.close()
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            payload = up.body or b'{"error":{"message":"upstream error"}}'
            up.close()
            self.record(row, alias_name, src["name"], model, None, None,
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
        self.record(row, alias_name, src["name"], model, in_tokens, out_tokens,
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
              f"[--rate N] {context_opts} | key list | key revoke <etikett> | key aliases <etikett> [a,b] "
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
                  + ("art=freigabe-für-chat " if row["kind"] == "client" else "")
                  + f'| {context_line(row)}')
        return 0
    if action == "revoke" and len(argv) > 2:
        print("widerrufen" if store.revoke(DB, argv[2], "cli") else "unbekannt oder schon widerrufen")
        return 0
    if action == "aliases" and len(argv) > 2:
        print(change_key_aliases(argv[2], (argv[3] if len(argv) > 3 else "").split(","), "cli"))
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
        f"{APP_NAME} {VERSION} auf Port {PORT} — "
        f"{len(SUPPLIERS)} Bezugsquelle(n), {len(ALIASES)} Alias(se)\n")
    sys.stdout.flush()
    threading.Thread(target=check_destination_sources, daemon=True).start()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
