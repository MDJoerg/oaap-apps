"""OAAP Ollama-Modelle 0.1 — die Betriebsoberfläche zu einem Dienst ohne eigene.

Ollama ist in unserem KI-Stack ein tragendes Teil geworden, bringt aber
**keine Oberfläche** mit: Modelle holt man auf der Kommandozeile, und
was auf der Platte liegt, sieht man gar nicht. Diese App schließt genau
diese Lücke — nicht mehr:

- welche Modelle installiert sind, wie groß sie sind, wann zuletzt
  angefasst;
- was **gerade im Speicher liegt** (`/api/ps`) — auf einer Maschine ohne
  Grafikkarte die interessanteste Zahl, weil sie erklärt, warum die
  erste Antwort nach einer Pause so lange braucht;
- Modelle holen (mit Fortschritt) und löschen;
- **die Zeilen, die man ins KI-Gateway einträgt** — der Teil, den keine
  fremde Oberfläche liefert und der aus zwei Apps einen Stack macht.

Ausdrücklich **nicht** hier: Chatten (dafür gibt es Open WebUI im
Store) und Modell-Verwaltung im KI-Gateway (dessen Spec nennt das als
Nicht-Ziel — `/api/pull` ist Ollama-spezifisch und wäre der erste
Adapter, den RFC-0023 A5 bewusst vertagt hat).

Erreicht wird Ollama über eine **App-zu-App-Verbindung** (RFC-0016):
Standard ist Isolation, die Verbindung ist eine ausdrückliche
Betreiber-Entscheidung und jederzeit widerrufbar. Ollamas API bleibt
dabei unveröffentlicht — sie kennt keine Authentifizierung, und was
nicht geroutet ist, kann auch niemand aufrufen.

Gebaut als gewöhnliche OAAP-App: kein eigener Login (geprüfte Identität
als Gateway-Kopfzeile), ein HTTP-Port, Persistenz nur unter dem
deklarierten Mount, Konfiguration über deklarierte Variablen, Logs nach
stdout, Gesundheitspfad, nur Standardbibliothek.
"""
import html
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote_plus

import ollama

VERSION = "0.1.0"
PORT = 8000

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://oaap-app-ollama:11434").rstrip("/")
DATA_DIR = os.environ.get("OLLAMA_MODELS_DATA_DIR", "/data")
LOG_PATH = os.path.join(DATA_DIR, "actions.jsonl")

esc = html.escape

# Vorschläge, keine Liste. Auf einem Knoten ohne Grafikkarte entscheidet
# die Größe darüber, ob ein Modell benutzbar ist oder nur vorhanden —
# deshalb stehen hier kleine Modelle und eine ehrliche Einordnung.
SUGGESTIONS = [
    ("qwen2.5:3b", "~2 GB", "Allzweck-Chat, auf CPU noch flüssig genug zum Ausprobieren"),
    ("llama3.2:3b", "~2 GB", "Allzweck-Chat, kleiner Kontext, schnell geladen"),
    ("nomic-embed-text", "~275 MB", "Embeddings — auf CPU wirklich brauchbar"),
    ("qwen2.5-coder:7b", "~4,7 GB", "Code; auf CPU spürbar langsam, aber nützlich"),
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def note(actor, action, model, detail=""):
    """Wer hat was geholt oder gelöscht. Ein Modell zu holen belegt Platz
    und Bandbreite; das gehört nachvollziehbar festgehalten."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"time": now(), "actor": actor, "action": action,
                                 "model": model, "detail": detail},
                                ensure_ascii=False) + "\n")
    except OSError as exc:
        sys.stdout.write(f"Protokoll nicht schreibbar: {exc}\n")


def recent(limit=20):
    try:
        with open(LOG_PATH, encoding="utf-8") as fh:
            lines = fh.readlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# ------------------------------------------------------- laufende Vorgänge

_lock = threading.Lock()
_pulls = {}     # modell -> {status, completed, total, done, error, started}


def pull_state():
    with _lock:
        return {k: dict(v) for k, v in _pulls.items()}


def start_pull(name, actor):
    with _lock:
        if name in _pulls and not _pulls[name]["done"]:
            return False
        _pulls[name] = {"status": "angefragt", "completed": 0, "total": 0,
                        "done": False, "error": "", "started": time.monotonic()}

    def progress(status, completed, total):
        with _lock:
            row = _pulls.get(name)
            if row is None:
                return
            row["status"] = status
            # Nur übernehmen, was auch Zahlen trägt: Ollama meldet zum
            # Schluss ein bloßes 'success' ohne completed/total, und das
            # würde einen vollen Balken auf null zurücksetzen.
            if total:
                row["completed"], row["total"] = completed, total

    def run():
        okay, err = ollama.pull(OLLAMA_URL, name, progress)
        with _lock:
            row = _pulls.get(name)
            if row is not None:
                row.update(done=True, error=err,
                           status="fertig" if okay else "abgebrochen")
        note(actor, "pull" if okay else "pull-fehlgeschlagen", name, err)

    threading.Thread(target=run, daemon=True).start()
    note(actor, "pull-gestartet", name)
    return True


def forget_finished():
    """Abgeschlossene Vorgänge verschwinden nach fünf Minuten von selbst —
    sonst steht die Seite in einer Woche voller alter Erfolge."""
    with _lock:
        for name in [n for n, r in _pulls.items()
                     if r["done"] and time.monotonic() - r["started"] > 300]:
            _pulls.pop(name, None)


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
  main{max-width:62rem;margin:1.6rem auto;padding:0 1.2rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .card.busy{border-color:#93c5fd;background:#eff6ff}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  .badge.off{background:#f3f4f6;color:#6b7280}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  button.quiet{background:#f3f4f6;color:#374151;min-height:36px;padding:.35rem .9rem;
       font-size:.85rem}
  button.quiet:hover{background:#e5e7eb}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  code{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
  pre{background:#0f172a;color:#e2e8f0;padding:.9rem 1rem;border-radius:.5rem;
       overflow-x:auto;font-size:.82rem}
  .bar{height:.55rem;border-radius:.3rem;background:#e5e7eb;overflow:hidden;margin:.35rem 0}
  .bar span{display:block;height:100%;background:var(--oaap-blue-600)}
  form.inline{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:0}
  input[type=text]{flex:1 1 16rem;padding:.5rem .6rem;border-radius:.4rem;
       border:1px solid var(--oaap-border);font-size:.95rem;min-height:44px}
  footer.oaap{max-width:62rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
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


def page(body, user, roles, refresh=False):
    # Läuft ein Vorgang, aktualisiert die Seite sich selbst — ohne
    # JavaScript, weil ein Fortschrittsbalken kein Grund ist, eine
    # Oberfläche von Skripten abhängig zu machen.
    meta = '<meta http-equiv="refresh" content="3">' if refresh else ""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{meta}
<link rel="icon" href="{FAVICON}">
<title>Modelle — OAAP Ollama</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OLLAMA-MODELLE</b><small>Was liegt da, was läuft, was fehlt</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP Ollama-Modelle {VERSION} — verwaltet Modelle; gechattet wird woanders
</footer>
</html>"""


def render(user, roles, may_change, notice=""):
    forget_finished()
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p style="margin:0">{esc(notice)}</p></div>')

    version, verr = ollama.version(OLLAMA_URL)
    installed, ierr = ollama.models(OLLAMA_URL)
    loaded, _ = ollama.running(OLLAMA_URL)
    err = verr or ierr

    if err:
        parts.append(f"""<div class="card attention">
  <h2>Ollama antwortet nicht</h2>
  <p class="hint">{esc(err)} — versucht wurde <code>{esc(OLLAMA_URL)}</code>.</p>
  <p class="hint">Das ist fast immer die fehlende <b>App-zu-App-Verbindung</b>:
    Standard ist Isolation (RFC-0016), eine Verbindung ist eine ausdrückliche
    Entscheidung des Betreibers. An der Maschine:</p>
  <pre>sudo oaap app link add &lt;diese-instanz&gt; {esc(ollama.instance_of(OLLAMA_URL))}</pre>
  <p class="hint">Danach erreichen sich beide über das eigene Netz des Paares —
    Ollamas API bleibt dabei unveröffentlicht, was gut ist: Sie kennt keine
    Authentifizierung.</p>
</div>""")
        return page("".join(parts), user, roles)

    # --- laufende Vorgänge
    pulls = pull_state()
    busy = any(not r["done"] for r in pulls.values())
    if pulls:
        rows = []
        for name, row in sorted(pulls.items()):
            if row["error"]:
                state = f'<span class="badge err">{esc(row["error"])}</span>'
            elif row["done"]:
                state = '<span class="badge ok">fertig</span>'
            else:
                state = f'<span class="badge">{esc(row["status"] or "läuft")}</span>'
            bar = ""
            if row["total"]:
                pct = min(100, int(row["completed"] * 100 / row["total"]))
                bar = (f'<div class="bar"><span style="width:{pct}%"></span></div>'
                       f'<span class="muted">{ollama.human_size(row["completed"])} von '
                       f'{ollama.human_size(row["total"])} ({pct} %)</span>')
            rows.append(f'<tr><td><code>{esc(name)}</code></td><td>{state}{bar}</td></tr>')
        parts.append(f"""<div class="card busy">
  <h2>Wird geholt</h2>
  <p class="hint">Die Seite aktualisiert sich selbst, solange etwas läuft.
    Wegnavigieren ist ungefährlich — geholt wird auf dem Knoten, nicht im
    Browser.</p>
  <table>{"".join(rows)}</table>
</div>""")

    # --- im Speicher
    if loaded:
        rows = []
        for row in loaded:
            where = "Grafikspeicher" if row["vram"] else "Arbeitsspeicher"
            rows.append(f'<tr><td><code>{esc(row["name"])}</code></td>'
                        f'<td>{esc(ollama.human_size(row["size"]))}</td>'
                        f'<td>{esc(where)}</td>'
                        f'<td class="muted">bis {esc(row["until"] or "?")}</td></tr>')
        parts.append(f"""<div class="card">
  <h2>Gerade geladen</h2>
  <p class="hint">Ollama hält ein benutztes Modell eine Weile im Speicher.
    Ist die Liste leer, muss das nächste Modell erst wieder eingelesen werden —
    das ist die Wartezeit, die nach einer Pause wie ein Fehler aussieht und
    keiner ist.</p>
  <table><tr><th>Modell</th><th>Belegt</th><th>Wo</th><th>Vorgehalten</th></tr>
  {"".join(rows)}</table>
</div>""")

    # --- installiert
    total = sum(r["size"] for r in installed)
    if installed:
        rows = []
        for row in installed:
            info = " · ".join(x for x in (row["parameters"], row["quantization"],
                                          row["family"]) if x)
            action = ""
            if may_change:
                action = (f'<form method="post" action="delete" style="margin:0" '
                          f'onsubmit="return confirm(\'{esc(row["name"])} wirklich löschen?\')">'
                          f'<input type="hidden" name="model" value="{esc(row["name"])}">'
                          f'<button class="quiet" type="submit">löschen</button></form>')
            rows.append(f'<tr><td><code>{esc(row["name"])}</code><br>'
                        f'<span class="muted">{esc(info)}</span></td>'
                        f'<td>{esc(ollama.human_size(row["size"]))}</td>'
                        f'<td class="muted">{esc(row["modified"])}</td>'
                        f'<td>{action}</td></tr>')
        parts.append(f"""<div class="card">
  <h2>Installierte Modelle</h2>
  <p class="hint">Zusammen <b>{esc(ollama.human_size(total))}</b> auf dem Mount der
    Ollama-Instanz. Wie viel dort noch frei ist, kann diese App nicht sehen —
    sie schaut in einen fremden Container hinein, nicht auf dessen Platte; das
    weiß der Knoten.</p>
  <table><tr><th>Modell</th><th>Größe</th><th>Geändert</th><th></th></tr>
  {"".join(rows)}</table>
</div>""")
    else:
        parts.append("""<div class="card attention">
  <h2>Noch kein Modell installiert</h2>
  <p class="hint">Ollama läuft, hat aber nichts zu antworten. Unten ein paar
    Startpunkte.</p>
</div>""")

    # --- holen
    if may_change:
        chips = "".join(
            f'<tr><td><code>{esc(name)}</code></td><td>{esc(size)}</td>'
            f'<td class="muted">{esc(why)}</td>'
            f'<td><form method="post" action="pull" style="margin:0">'
            f'<input type="hidden" name="model" value="{esc(name)}">'
            f'<button class="quiet" type="submit">holen</button></form></td></tr>'
            for name, size, why in SUGGESTIONS)
        parts.append(f"""<div class="card">
  <h2>Modell holen</h2>
  <form class="inline" method="post" action="pull">
    <input type="text" name="model" placeholder="z. B. qwen2.5:3b" required>
    <button type="submit">Holen</button>
  </form>
  <p class="hint" style="margin-top:.8rem">Der Name ist der von
    <code>ollama.com</code>; ohne Doppelpunkt nimmt Ollama <code>:latest</code>.</p>
  <h2 style="margin-top:1.2rem">Bewährte Startpunkte</h2>
  <p class="hint">Vorschläge, kein Katalog. Auf einem Knoten <b>ohne
    Grafikkarte</b> entscheidet die Größe darüber, ob ein Modell benutzbar ist
    oder nur vorhanden — kleine Modelle antworten in Sekunden, ein 70B-Modell
    rechnet Minuten an einem Satz.</p>
  <table><tr><th>Modell</th><th>Größe</th><th>Wofür</th><th></th></tr>{chips}</table>
</div>""")
    else:
        parts.append("""<div class="card">
  <p class="hint" style="margin:0">Modelle zu holen oder zu löschen belegt Platz
    und Bandbreite auf dem Knoten und ist deshalb der Serververwaltung
    vorbehalten. Sehen darf diese Seite jeder Schlüsselanwender.</p>
</div>""")

    # --- Anschluss ans Gateway
    inst = ollama.instance_of(OLLAMA_URL)
    first = installed[0]["name"] if installed else "qwen2.5:3b"
    parts.append(f"""<div class="card">
  <h2>Anschluss ans KI-Gateway</h2>
  <p class="hint">Damit das Gateway diese Modelle anbieten kann, braucht es
    einmal eine Verbindung und zwei Zeilen Konfiguration. Die Ampelfarbe ist
    <b>grün</b>: Die Daten verlassen das Unternehmen nicht.</p>
  <pre>sudo oaap app link add &lt;gateway-instanz&gt; {esc(inst)}

AIGW_SUPPLIERS   ollama={esc(OLLAMA_URL)}/v1 light=green
AIGW_ALIASES     chat-default = ollama:{esc(first)}</pre>
  <p class="hint">Ollamas eigene API wird dabei <b>nicht veröffentlicht</b> —
    sie kennt keine Authentifizierung, und was nicht geroutet ist, kann
    niemand aufrufen.</p>
</div>""")

    # --- Protokoll
    log = recent()
    if log:
        rows = "".join(
            f'<tr><td class="muted">{esc(r.get("time", ""))}</td>'
            f'<td>{esc(r.get("actor", "?"))}</td><td>{esc(r.get("action", ""))}</td>'
            f'<td><code>{esc(r.get("model", ""))}</code></td>'
            f'<td class="muted">{esc(r.get("detail", ""))}</td></tr>' for r in log)
        parts.append(f"""<div class="card">
  <h2>Was hier geschah</h2>
  <p class="hint">Holen und Löschen belegen Platz und Bandbreite — das gehört
    nachvollziehbar festgehalten. Ollama-Version: {esc(version or "unbekannt")}.</p>
  <table><tr><th>Zeit</th><th>Wer</th><th>Was</th><th>Modell</th><th></th></tr>
  {rows}</table>
</div>""")

    return page("".join(parts), user, roles, refresh=busy)


# --------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-ollama-models/" + VERSION

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def identity(self):
        return (self.headers.get("X-OAAP-User", ""), self.headers.get("X-OAAP-Roles", ""))

    def roles(self):
        return [r.strip() for r in self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()]

    def send_html(self, status, text):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, doc):
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION})
            return
        user, roles = self.identity()
        if not self.roles():
            # Ohne Kopfzeile kommt niemand hier an, außer die Route ist
            # falsch gesetzt. Dann lieber schweigen als raten.
            self.send_html(403, page('<div class="card"><p>Diese Seite braucht eine '
                                     'angemeldete Sitzung.</p></div>', "", ""))
            return
        self.send_html(200, render(user, roles, "admin" in self.roles()))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        user, roles = self.identity()
        if "admin" not in self.roles():
            self.send_html(403, page('<div class="card"><p>Modelle zu holen oder zu '
                                     'löschen ist der Serververwaltung vorbehalten.</p>'
                                     '</div>', user, roles))
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        form = {}
        for pair in self.rfile.read(max(0, min(length, 64 * 1024))).decode(
                "utf-8", "replace").split("&"):
            name, _, value = pair.partition("=")
            if name:
                form[unquote_plus(name)] = unquote_plus(value)
        model = (form.get("model") or "").strip()

        notice = ""
        if not model:
            notice = "Ohne Modellnamen kann ich nichts tun."
        elif path == "/pull":
            notice = ("" if start_pull(model, user)
                      else f"„{model}“ wird bereits geholt.")
        elif path == "/delete":
            okay, err = ollama.delete(OLLAMA_URL, model)
            note(user, "delete" if okay else "delete-fehlgeschlagen", model, err)
            notice = (f"„{model}“ gelöscht — der Platz ist auf dem Knoten wieder frei."
                      if okay else f"Löschen fehlgeschlagen: {err}")
        else:
            self.send_html(404, page('<div class="card"><p>Unbekannter Pfad.</p></div>',
                                     user, roles))
            return
        self.send_html(200, render(user, roles, True, notice))


def main():
    sys.stdout.write(f"OAAP Ollama-Modelle {VERSION} auf Port {PORT} — "
                     f"Ollama unter {OLLAMA_URL}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
