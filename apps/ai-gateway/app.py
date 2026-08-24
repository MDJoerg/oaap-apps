"""OAAP KI-Gateway 0.1 — eine Bezugsquelle für alle, ein Schlüssel je Verbraucher.

Umsetzung der Capability `oaap.ai.gateway` 0.2 (RFC-0023). Das Gateway
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
  Schlüssel ausstellen und widerrufen, Verbrauch. Die Plattform
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import relay
import store
import supply

VERSION = "0.2.1"
PORT = 8000

DATA_DIR = os.environ.get("AIGW_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "gateway.db")

MAX_BODY = 8 * 1024 * 1024          # großzügig: Kontexte sind heute groß
DEFAULT_RATE = 120                  # Anfragen je Minute und Schlüssel

esc = html.escape

# ------------------------------------------------------------- Konfiguration

SUPPLIERS, SUPPLIER_ERRORS = supply.parse_suppliers(
    os.environ.get("AIGW_SUPPLIERS", ""), os.environ.get("AIGW_SUPPLIER_KEYS", ""))
ALIASES, ALIAS_ERRORS = supply.parse_aliases(os.environ.get("AIGW_ALIASES", ""), SUPPLIERS)
CONFIG_ERRORS = SUPPLIER_ERRORS + ALIAS_ERRORS

try:
    TIMEOUT = max(10, min(3600, int(os.environ.get("AIGW_TIMEOUT_SECONDS") or relay.READ_TIMEOUT)))
except ValueError:
    TIMEOUT = relay.READ_TIMEOUT

DB = store.connect(DB_PATH)
LIMITER = store.RateLimiter()

# Eine zweite, kleine Bremse gegen das Raten von Schlüsseln. Die große
# steht in der Plattform (RFC-0010) und gilt je Client-Adresse für alle
# Anfragen; diese hier zählt ausschließlich **Fehlversuche** und macht
# das Durchprobieren teuer, ohne einen ehrlichen Verbraucher zu stören.
_FAILS = {}
_FAILS_LOCK = threading.Lock()
FAIL_WINDOW = 300
FAIL_LIMIT = 5

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
  @media (max-width:640px){ .userbox .who{display:none} }
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

    if CONFIG_ERRORS:
        items = "".join(f"<li>{esc(e)}</li>" for e in CONFIG_ERRORS)
        parts.append(f"""<div class="card attention">
  <h2>Die Konfiguration hat Stellen, die ich nicht verstanden habe</h2>
  <p class="hint">Alles andere läuft weiter — was hier steht, ist wirkungslos,
    nicht kaputt.</p>
  <ul>{items}</ul>
</div>""")

    # --- Aliasse
    if ALIASES:
        rows = []
        for name, alias in sorted(ALIASES.items()):
            targets = []
            for target in alias["targets"]:
                src = SUPPLIERS[target["supplier"]]
                targets.append(f'{light_badge(src["light"], short=True)} '
                               f'<code>{esc(src["name"])} · {esc(target["model"])}</code>')
            # Die Farbe eines Alias ist die schlechteste seiner Ziele — einer,
            # der auf einen roten Anbieter ausweichen kann, ist nicht grün,
            # auch wenn sein erstes Ziel es ist.
            worst = supply.alias_light(alias, SUPPLIERS, "red")
            order = ("wie aufgeführt" if alias["order_listed"] else "grün vor gelb vor rot")
            rows.append(f'<tr><td><code>{esc(name)}</code></td>'
                        f'<td>{light_badge(worst)}</td>'
                        f'<td>{"<br>".join(targets)}</td>'
                        f'<td class="muted">{esc(order)}</td></tr>')
        parts.append(f"""<div class="card">
  <h2>Aliasse</h2>
  <p class="hint">Ein Verbraucher fragt nach dem Zweck, nie nach dem Modell.
    Ausgewichen wird nur innerhalb der hier erklärten Gruppe — und die Ampel
    eines Alias ist die <b>schlechteste</b> seiner Ziele.</p>
  <table><tr><th>Alias</th><th>Ampel</th><th>Bezugsquellen</th><th>Reihenfolge</th></tr>
  {"".join(rows)}</table>
</div>""")
    else:
        parts.append("""<div class="card attention">
  <h2>Noch kein Alias eingerichtet</h2>
  <p class="hint">Ohne Alias kann kein Verbraucher etwas anfragen. Einzutragen
    als Konfiguration <code>AIGW_SUPPLIERS</code> und <code>AIGW_ALIASES</code>
    bei dieser Instanz (Portal → Instanz → Konfiguration).</p>
</div>""")

    # --- Bezugsquellen
    if SUPPLIERS:
        rows = []
        for name, src in sorted(SUPPLIERS.items()):
            cred = ("hinterlegt" if src["credential"]
                    else '<span class="muted">keine (z. B. lokal)</span>')
            rows.append(f'<tr><td><code>{esc(name)}</code></td>'
                        f'<td>{light_badge(src["light"])}<br>'
                        f'<span class="muted">{esc(supply.LIGHT_RULES[src["light"]])}</span></td>'
                        f'<td><code>{esc(src["url"])}</code></td>'
                        f'<td>{cred}</td></tr>')
        parts.append(f"""<div class="card">
  <h2>Bezugsquellen</h2>
  <p class="hint">Zugangsdaten stehen ausschließlich in der geheimen
    Konfiguration und erscheinen weder hier noch in einer Messzeile oder
    Fehlermeldung.</p>
  <table><tr><th>Name</th><th>Ampel</th><th>Adresse</th><th>Zugangsdaten</th></tr>
  {"".join(rows)}</table>
</div>""")

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
                      f'<button class="quiet" type="submit">widerrufen</button></form>')
        rows.append(
            f'<tr><td><b>{esc(row["label"])}</b><br>'
            f'<span class="muted">{esc(row["owner"] or "ohne Verantwortlichen")}'
            f'{(" · " + esc(row["cost_center"])) if row["cost_center"] else ""}'
            f'{(" · " + esc(row["project"])) if row["project"] else ""}</span></td>'
            f'<td>{state}</td><td><code>{aliases}</code></td><td>{light}</td>'
            f'<td>{budget}</td>'
            f'<td>{t["calls"] if t else 0}<br><span class="muted">'
            f'{esc(t["last"]) if t else "noch nie"}</span></td>'
            f'<td>{action}</td></tr>')
    parts.append(f"""<div class="card">
  <h2>Schlüssel</h2>
  <p class="hint">Wer ein Gateway betreibt, gibt die Erlaubnis — ein Recht wird
    gegeben, nicht gehalten. Jedes Ausstellen und jedes Widerrufen steht in der
    Prüfspur; einzelne Anfragen stehen dort nicht.</p>
  <table><tr><th>Etikett</th><th>Zustand</th><th>Aliasse</th><th>Ampel</th>
    <th>Token</th><th>Aufrufe</th><th></th></tr>{"".join(rows) or
    '<tr><td colspan="7" class="muted">Noch kein Schlüssel ausgestellt.</td></tr>'}</table>
</div>""")

    alias_hint = ", ".join(sorted(ALIASES)) or "—"
    parts.append(f"""<div class="card">
  <h2>Schlüssel ausstellen</h2>
  <form class="grid" method="post" action="issue">
    <div><label for="label">Etikett (eindeutig, benennt den Verbraucher)</label>
      <input id="label" name="label" type="text" required placeholder="laptop@joerg"></div>
    <div><label for="owner">Verantwortlich</label>
      <input id="owner" name="owner" type="text" placeholder="Jörg"></div>
    <div><label for="cost_center">Kostenstelle</label>
      <input id="cost_center" name="cost_center" type="text"></div>
    <div><label for="project">Projekt</label>
      <input id="project" name="project" type="text"></div>
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
            f'<td>{row["ms"]} ms</td><td>{esc(row["outcome"])}</td></tr>')
    parts.append(f"""<div class="card">
  <h2>Letzte Anfragen</h2>
  <p class="hint">Gezählt wird, nicht mitgeschrieben: Zeit, Schlüssel, Alias,
    <b>die tatsächlich benutzte Quelle</b>, Token-Zahlen, Dauer, Ausgang.
    Prompts und Antworten stehen in keiner Zeile und in keiner Datei.</p>
  <table><tr><th>Zeit</th><th>Schlüssel</th><th>Alias</th><th>Quelle</th>
    <th>Token ein/aus</th><th>Dauer</th><th>Ausgang</th></tr>{"".join(rows) or
    '<tr><td colspan="7" class="muted">Noch keine Anfrage.</td></tr>'}</table>
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
        self.send_json(status, {"error": {"message": message,
                                          "type": "invalid_request_error",
                                          "code": code}}, extra_headers)

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
                light = supply.alias_light(ALIASES[name], SUPPLIERS, ceiling, pbd)
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
                "budget_tokens": int(row["budget_tokens"]),
                "ceiling": ceiling_of(row),
                "personal_data_released": pbd_of(row),
                "recent": [{"time": r["time"], "alias": r["alias"],
                            "supplier": r["supplier"], "model": r["model"],
                            "light": SUPPLIERS.get(r["supplier"], {}).get("light", ""),
                            "input_tokens": r["in_tokens"], "output_tokens": r["out_tokens"],
                            "ms": r["ms"], "outcome": r["outcome"]}
                           for r in store.recent(DB, row["id"], 20)],
            })
            return
        if path.startswith("/v1"):
            self.send_error_doc(404, "Diesen Pfad kennt das Gateway nicht.", "unknown_path")
            return

        # Betreiber-Sicht
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff",
                                     '<div class="card"><p>Diese Seite ist der '
                                     'Serververwaltung vorbehalten.</p></div>', "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        self.send_html(200, admin_page(user, roles, host))

    # ---- POST

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/v1/chat/completions", "/v1/embeddings"):
            self.handle_inference(path)
            return
        if path in ("/issue", "/revoke"):
            self.handle_admin_post(path)
            return
        self.send_error_doc(404, "Diesen Pfad kennt das Gateway nicht.", "unknown_path")

    # ---- Betreiber-Handlungen

    def handle_admin_post(self, path):
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Nicht erlaubt.</p>'
                                     '</div>', "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        raw = self.read_body() or b""
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
                        owner=form.get("owner", ""), cost_center=form.get("cost_center", ""),
                        project=form.get("project", ""))
                    secret = {"label": form.get("label", ""), "value": value}
                except ValueError as exc:
                    notice = str(exc)
        else:
            label = form.get("label", "")
            notice = (f"Schlüssel „{label}“ widerrufen — ab sofort abgelehnt."
                      if store.revoke(DB, label, user)
                      else f"Nichts zu widerrufen: „{label}“ ist unbekannt oder schon widerrufen.")
        self.send_html(200, admin_page(user, roles, host, notice=notice, secret=secret))

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
                self.send_error_doc(
                    429, f"Budget erschöpft ({used} von {row['budget_tokens']} Token). "
                         "Der Betreiber kann es erhöhen.", "budget_exhausted")
                store.record(DB, row["id"], alias_name, "", "", 0, 0, 0, "budget")
                return

        alias = ALIASES[alias_name]
        ceiling, pbd = ceiling_of(row), pbd_of(row)
        targets = supply.candidates(alias, SUPPLIERS, ceiling, pbd)
        if not targets:
            reasons = supply.blocked_reasons(alias, SUPPLIERS, ceiling, pbd)
            if reasons:
                self.send_error_doc(
                    503, f"Für „{alias_name}“ bleibt jede Quelle gesperrt: "
                         f"{'; '.join(reasons)}. Der Betreiber kann die Obergrenze "
                         "heraufsetzen oder eine andere Quelle eintragen.",
                    "light_not_permitted")
            else:
                self.send_error_doc(503, f"Für „{alias_name}“ ist keine Bezugsquelle erreichbar.",
                                    "no_supplier")
            store.record(DB, row["id"], alias_name, "", "", None, None, 0, "no_supplier")
            return

        stream = bool(payload.get("stream"))
        last_error = ""
        for index, target in enumerate(targets):
            src, model = target["supplier"], target["model"]
            body = dict(payload)
            body["model"] = model                      # Alias → Modell der Quelle
            started = time.monotonic()
            if stream:
                done = self.relay_stream(row, alias_name, src, model, path, body, started)
            else:
                done = self.relay_once(row, alias_name, src, model, path, body, started)
            if done is True:
                return
            last_error = done or last_error
            # Ausgewichen wird nur innerhalb der erklärten Gruppe, und der
            # Wechsel steht anschließend in der Messzeile.
            if index + 1 < len(targets):
                sys.stdout.write(f"ausweichen: {src['name']} -> "
                                 f"{targets[index + 1]['supplier']['name']} ({last_error})\n")

        store.record(DB, row["id"], alias_name, targets[-1]["supplier"]["name"],
                     targets[-1]["model"], None, None,
                     int((time.monotonic() - started) * 1000), "upstream_failed")
        self.send_error_doc(502, f"Keine Bezugsquelle für „{alias_name}“ hat geantwortet "
                                 f"({last_error}).", "upstream_failed")

    def relay_once(self, row, alias_name, src, model, path, body, started):
        up, counts = relay.call_json(src, path, body, timeout=TIMEOUT)
        ms = int((time.monotonic() - started) * 1000)
        if up.status == 0 or up.status >= 500:
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            # Ein 4xx ist die Antwort auf **diese** Anfrage — Ausweichen
            # würde denselben Fehler noch einmal erzeugen.
            store.record(DB, row["id"], alias_name, src["name"], model,
                         counts[0], counts[1], ms, f"upstream_{up.status}")
            self.send_response(up.status)
            self.send_header("Content-Type", up.content_type or "application/json")
            self.send_header("Content-Length", str(len(up.body)))
            self.end_headers()
            self.wfile.write(up.body)
            return True
        store.record(DB, row["id"], alias_name, src["name"], model,
                     counts[0], counts[1], ms,
                     "ok" if counts[0] is not None else "ok_no_counts")
        self.send_response(200)
        self.send_header("Content-Type", up.content_type or "application/json")
        self.send_header("Content-Length", str(len(up.body)))
        self.end_headers()
        self.wfile.write(up.body)
        return True

    def relay_stream(self, row, alias_name, src, model, path, body, started):
        up, asked_usage = relay.open_stream(src, path, body, timeout=TIMEOUT)
        if up.status == 0 or up.status >= 500:
            up.close()
            return up.error or f"HTTP {up.status}"
        if up.status >= 400:
            payload = up.body or b'{"error":{"message":"upstream error"}}'
            up.close()
            store.record(DB, row["id"], alias_name, src["name"], model, None, None,
                         int((time.monotonic() - started) * 1000), f"upstream_{up.status}")
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

        def write(chunk):
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()

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
        store.record(DB, row["id"], alias_name, src["name"], model, in_tokens, out_tokens,
                     int((time.monotonic() - started) * 1000), outcome)
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
    if len(argv) < 2 or argv[0] != "key":
        print("Aufruf: app.py key issue <etikett> [--aliases a,b] "
              "[--ceiling green|yellow|red] [--personal-data ja] [--budget N] "
              "[--rate N] [--owner X] | key list | key revoke <etikett>")
        return 2
    action = argv[1]
    if action == "list":
        for row in store.keys(DB):
            print(f'{row["label"]:24} {row["created"]} '
                  f'{"WIDERRUFEN" if row["revoked"] else "gültig":10} '
                  f'aliasse={row["aliases"] or "alle"} bis={row["ceiling"]} '
                  f'pbD={"freigegeben" if row["personal_data"] else "nein"}')
        return 0
    if action == "revoke" and len(argv) > 2:
        print("widerrufen" if store.revoke(DB, argv[2], "cli") else "unbekannt oder schon widerrufen")
        return 0
    if action == "issue" and len(argv) > 2:
        opts, i = {}, 3
        while i + 1 < len(argv):
            if argv[i].startswith("--"):
                opts[argv[i][2:]] = argv[i + 1]
            i += 2
        value, _ = store.issue(
            DB, argv[2], "cli",
            aliases=[a for a in opts.get("aliases", "").split(",") if a],
            ceiling=(opts.get("ceiling") if opts.get("ceiling") in supply.LIGHT_RANK
                     else supply.DEFAULT_CEILING),
            personal_data=opts.get("personal-data", "").lower() in ("ja", "yes", "1"),
            budget_tokens=_int(opts.get("budget")), rate_per_min=_int(opts.get("rate")),
            owner=opts.get("owner", ""))
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
