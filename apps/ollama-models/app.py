"""Ollama-Modelle — die Betriebsoberfläche zu einem Dienst ohne eigene.

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
from urllib.parse import parse_qs, unquote_plus, urlencode

import appframe as af
import mcpdoor
import ollama

VERSION = "0.2.0"
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

APP_NAME = "Ollama-Modelle"
PRODUCT = "ollama-models"

# Nur, was dieser App eigen ist; Farben kommen aus dem Kit der Plattform.
STYLE = """
.hint{font-size:.85rem;color:var(--oaap-color-text-muted);margin:0 0 .6rem}
.oaap-card.busy{border-left:4px solid var(--oaap-color-primary)}
code{background:var(--oaap-badge-neutral-bg);color:var(--oaap-badge-neutral);border-radius:.25rem;
     padding:.1rem .35rem;font-size:.86rem;word-break:break-all}
pre{background:var(--oaap-color-bg);color:var(--oaap-color-text);border:1px solid var(--oaap-color-border);
    padding:.9rem 1rem;border-radius:var(--oaap-radius);overflow-x:auto;font-size:.82rem}
.bar{height:.55rem;border-radius:.3rem;background:var(--oaap-color-border);overflow:hidden;margin:.35rem 0}
.bar span{display:block;height:100%;background:var(--oaap-color-primary)}
form.inline{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin:0}
form.inline input[type=text]{flex:1 1 16rem}
"""

TABS = [("./", "Übersicht"), ("anschluss", "Anschluss ans KI-Gateway"), ("protokoll", "Protokoll")]


def page(title, body, active="./", refresh=False, obj=None, status_tabs=True):
    # Läuft ein Vorgang, aktualisiert die Seite sich selbst — ohne
    # JavaScript, weil ein Fortschrittsbalken kein Grund ist, eine
    # Oberfläche von Skripten abhängig zu machen.
    head = '<meta http-equiv="refresh" content="3">' if refresh else ""
    return af.page(title, body, app=APP_NAME, version=VERSION, product=PRODUCT,
                   tabs=TABS if status_tabs else None, active=active, obj=obj,
                   style=STYLE, head=head)


def unreachable_card(err):
    return af.card("Ollama antwortet nicht", f"""
  <p class="hint">{esc(err)} — versucht wurde <code>{esc(OLLAMA_URL)}</code>.</p>
  <p class="hint">Das ist fast immer die fehlende <b>App-zu-App-Verbindung</b>:
    Standard ist Isolation, eine Verbindung ist eine ausdrückliche
    Entscheidung des Betreibers. An der Maschine:</p>
  <pre>sudo oaap app link add &lt;diese-instanz&gt; {esc(ollama.instance_of(OLLAMA_URL))}</pre>
  <p class="hint">Danach erreichen sich beide über das eigene Netz des Paares —
    Ollamas Schnittstelle bleibt dabei unveröffentlicht, was gut ist: Sie kennt keine
    Anmeldung.</p>""", "oaap-warn")


def pulls_card():
    pulls = pull_state()
    if not pulls:
        return "", False
    busy = any(not r["done"] for r in pulls.values())
    rows = []
    for name, row in sorted(pulls.items()):
        if row["error"]:
            state = af.badge(row["error"], "err")
        elif row["done"]:
            state = af.badge("fertig", "ok")
        else:
            state = af.badge(row["status"] or "läuft", "info")
        bar = ""
        if row["total"]:
            pct = min(100, int(row["completed"] * 100 / row["total"]))
            bar = (f'<div class="bar"><span style="width:{pct}%"></span></div>'
                   f'<span class="oaap-muted">{ollama.human_size(row["completed"])} von '
                   f'{ollama.human_size(row["total"])} ({pct} %)</span>')
        rows.append(f'<tr><td><code>{esc(name)}</code></td><td>{state}{bar}</td></tr>')
    return af.card("Wird geholt", f"""
  <p class="hint">Die Seite aktualisiert sich selbst, solange etwas läuft.
    Wegnavigieren ist ungefährlich — geholt wird auf dem Knoten, nicht im
    Browser.</p>
  {af.table("<th>Modell</th><th>Stand</th>", rows)}""", "busy"), busy


def installed_report(installed, loaded_names, params):
    families = sorted({r["family"] for r in installed if r["family"]})
    q = af.Query(params, filters=("familie", "geladen"), default_sort="-size")
    rows = [r for r in installed
            if (not q.filters["familie"] or r["family"] == q.filters["familie"])
            and (not q.filters["geladen"] or (r["name"] in loaded_names) == (q.filters["geladen"] == "ja"))]
    columns = [
        ("Modell", "name", lambda r: r["name"], ""),
        ("Größe", "size", lambda r: r["size"], "oaap-num"),
        ("Parameter · Quantisierung", "family",
         lambda r: " · ".join(x for x in (r["parameters"], r["quantization"], r["family"]) if x), "oaap-minor"),
        ("Geändert", "modified", lambda r: r["modified"], "oaap-minor"),
    ]

    def row_html(r):
        info = " · ".join(x for x in (r["parameters"], r["quantization"], r["family"]) if x)
        mark = " " + af.badge("geladen", "ok") if r["name"] in loaded_names else ""
        return (f'<tr><td><a href="modell?{urlencode({"name": r["name"]})}"><code>{esc(r["name"])}</code></a>{mark}</td>'
                f'<td class="oaap-num">{esc(ollama.human_size(r["size"]))}</td>'
                f'<td class="oaap-minor oaap-muted">{esc(info)}</td>'
                f'<td class="oaap-minor oaap-muted">{esc(r["modified"])}</td></tr>')
    choices = [("geladen", "Im Speicher", [("ja", "geladen"), ("nein", "nicht geladen")])]
    if families:
        choices.insert(0, ("familie", "Familie", [(f, f) for f in families]))
    return af.list_report(q, rows, columns, row_html, base="./?", noun="Modelle",
                          empty_text="Noch kein Modell installiert.", choices=choices,
                          placeholder="Modellname")


def render(user, roles, may_change, notice="", params=None, error=False):
    forget_finished()
    params = params or {}
    parts = [af.msg(notice, error=error)] if notice else []

    version, verr = ollama.version(OLLAMA_URL)
    installed, ierr = ollama.models(OLLAMA_URL)
    loaded, _ = ollama.running(OLLAMA_URL)
    err = verr or ierr

    if err:
        parts.append(unreachable_card(err))
        return page("Übersicht", af.pagehead("Modelle") + "".join(parts))

    pulls, busy = pulls_card()
    if pulls:
        parts.append(pulls)

    # --- im Speicher
    if loaded:
        rows = []
        for row in loaded:
            where = "Grafikspeicher" if row["vram"] else "Arbeitsspeicher"
            rows.append(f'<tr><td><a href="modell?{urlencode({"name": row["name"]})}"><code>{esc(row["name"])}</code></a></td>'
                        f'<td class="oaap-num">{esc(ollama.human_size(row["size"]))}</td>'
                        f'<td>{esc(where)}</td>'
                        f'<td class="oaap-muted">bis {esc(row["until"] or "?")}</td></tr>')
        parts.append(af.card("Gerade geladen", f"""
  <p class="hint">Ollama hält ein benutztes Modell eine Weile im Speicher.
    Ist die Liste leer, muss das nächste Modell erst wieder eingelesen werden —
    das ist die Wartezeit, die nach einer Pause wie ein Fehler aussieht und
    keiner ist.</p>
  {af.table('<th>Modell</th><th class="oaap-num">Belegt</th><th>Wo</th><th>Vorgehalten</th>', rows)}"""))

    # --- installiert
    total = sum(r["size"] for r in installed)
    if installed:
        parts.append(af.card("Installierte Modelle", f"""
  <p class="hint">Zusammen <b>{esc(ollama.human_size(total))}</b> auf dem Speicher der
    Ollama-Instanz. Wie viel dort noch frei ist, kann diese App nicht sehen —
    sie schaut in einen fremden Container hinein, nicht auf dessen Platte; das
    weiß der Knoten. Ein Klick auf ein Modell zeigt Einzelheiten{" und löschen" if may_change else ""}.</p>
  {installed_report(installed, {r["name"] for r in loaded}, params)}"""))
    else:
        parts.append(af.card("Noch kein Modell installiert", """
  <p class="hint">Ollama läuft, hat aber nichts zu antworten. Unten ein paar
    Startpunkte.</p>""", "oaap-warn"))

    # --- holen
    if may_change:
        pick = (params.get("vorschlag") or "").strip() if isinstance(params.get("vorschlag"), str) else ""
        sugg = [f'<tr><td><a href="./?{urlencode({"vorschlag": name})}#holen"><code>{esc(name)}</code></a></td>'
                f'<td>{esc(size)}</td><td class="oaap-muted">{esc(why)}</td></tr>'
                for name, size, why in SUGGESTIONS]
        parts.append(f"""<section class="oaap-card" id="holen"><h2>Modell holen</h2>
  <form class="inline" method="post" action="pull">
    <input type="text" name="model" placeholder="z. B. qwen2.5:3b" value="{esc(pick)}" required aria-label="Modellname">
    <button class="primary" type="submit">Holen</button>
  </form>
  <p class="hint" style="margin-top:.8rem">Der Name ist der von
    <code>ollama.com</code>; ohne Doppelpunkt nimmt Ollama <code>:latest</code>.</p>
  <h3>Bewährte Startpunkte</h3>
  <p class="hint">Vorschläge, kein Katalog. Ein Klick trägt den Namen oben ein. Auf einem
    Knoten <b>ohne Grafikkarte</b> entscheidet die Größe darüber, ob ein Modell benutzbar ist
    oder nur vorhanden — kleine Modelle antworten in Sekunden, ein 70B-Modell
    rechnet Minuten an einem Satz.</p>
  {af.table("<th>Modell</th><th>Größe</th><th>Wofür</th>", sugg)}
</section>""")
    else:
        parts.append(af.card("", """
  <p class="hint" style="margin:0">Modelle zu holen oder zu löschen belegt Platz
    und Bandbreite auf dem Knoten und ist deshalb der Serververwaltung
    vorbehalten. Sehen darf diese Seite jeder Schlüsselanwender.</p>"""))

    parts.append(f'<p class="hint">Ollama-Version: {esc(version or "unbekannt")}.</p>')
    return page("Übersicht", af.pagehead("Modelle") + "".join(parts), refresh=busy)


def model_page(name, may_change):
    """Die Objektseite eines Modells: Kennzahlen und, für die Verwaltung, Löschen."""
    installed, err = ollama.models(OLLAMA_URL)
    loaded, _ = ollama.running(OLLAMA_URL)
    back = af.back("./", "Zurück zur Übersicht")
    if err:
        return page(name or "Modell", back + unreachable_card(err), active="./")
    row = next((r for r in installed if r["name"] == name), None)
    if row is None:
        return page("Unbekanntes Modell", back + af.pagehead(name or "Modell")
                    + af.empty("Dieses Modell ist nicht (mehr) installiert. "
                               '<a href="./">Zur Übersicht</a>'), active="./")
    live = next((r for r in loaded if r["name"] == name), None)
    facts = af.facts([
        ("Größe", esc(ollama.human_size(row["size"]))),
        ("Parameter", esc(row["parameters"])),
        ("Quantisierung", esc(row["quantization"])),
        ("Familie", esc(row["family"])),
        ("Geändert", esc(row["modified"])),
        ("Im Speicher", af.badge("geladen", "ok") + (
            f' <span class="oaap-muted">bis {esc(live["until"] or "?")}</span>' if live["until"] else "")
         if live else af.badge("nicht geladen")),
    ])
    body = (back + f'<div class="oaap-objhead"><h1><code>{esc(name)}</code></h1>{facts}</div>')
    if may_change:
        body += af.card("Löschen", f"""
  <p class="hint">Gibt den Platz auf dem Knoten frei. Ein gelöschtes Modell lässt sich
    jederzeit wieder holen; im KI-Gateway antwortet es bis dahin nicht.</p>
  <form method="post" action="delete" onsubmit="return confirm('{esc(name)} wirklich löschen?')">
    <input type="hidden" name="model" value="{esc(name)}">
    <button class="danger" type="submit">Modell löschen</button></form>""", "oaap-danger")
    return page(name, body, active="./", obj=f"Modell:{name}")


def gateway_page():
    installed, _ = ollama.models(OLLAMA_URL)
    inst = ollama.instance_of(OLLAMA_URL)
    first = installed[0]["name"] if installed else "qwen2.5:3b"
    body = af.pagehead("Anschluss ans KI-Gateway") + af.card("", f"""
  <p class="hint">Damit das KI-Gateway diese Modelle anbieten kann, braucht es
    einmal eine Verbindung und zwei Zeilen Konfiguration. Die Ampelfarbe ist
    <b>grün</b>: Die Daten verlassen das Unternehmen nicht.</p>
  <pre>sudo oaap app link add &lt;gateway-instanz&gt; {esc(inst)}

AIGW_SUPPLIERS   ollama={esc(OLLAMA_URL)}/v1 light=green
AIGW_ALIASES     chat-default = ollama:{esc(first)}</pre>
  <p class="hint">Ollamas eigene Schnittstelle wird dabei <b>nicht veröffentlicht</b> —
    sie kennt keine Anmeldung, und was nicht geroutet ist, kann
    niemand aufrufen.</p>""")
    return page("Anschluss ans KI-Gateway", body, active="anschluss")


def log_page(params):
    log = recent(500)
    q = af.Query(params, filters=("was",), default_sort="-time")
    kinds = sorted({r.get("action", "") for r in log if r.get("action")})
    rows = [r for r in log if not q.filters["was"] or r.get("action") == q.filters["was"]]
    columns = [
        ("Zeit", "time", lambda r: r.get("time", ""), ""),
        ("Wer", "actor", lambda r: r.get("actor", ""), ""),
        ("Was", "action", lambda r: r.get("action", ""), ""),
        ("Modell", "model", lambda r: r.get("model", ""), ""),
        ("Einzelheiten", "", None, "oaap-minor"),
    ]

    def row_html(r):
        return (f'<tr><td class="oaap-muted">{esc(r.get("time", ""))}</td>'
                f'<td>{esc(r.get("actor", "?"))}</td><td>{esc(r.get("action", ""))}</td>'
                f'<td><code>{esc(r.get("model", ""))}</code></td>'
                f'<td class="oaap-minor oaap-muted">{esc(r.get("detail", ""))}</td></tr>')
    body = (af.pagehead("Protokoll")
            + '<p class="hint">Holen und Löschen belegen Platz und Bandbreite — das gehört '
              'nachvollziehbar festgehalten: wer, wann, welches Modell.</p>'
            + af.list_report(q, rows, columns, row_html, base="protokoll?", noun="Einträge",
                             empty_text="Noch nichts geholt oder gelöscht.",
                             choices=[("was", "Was", [(k, k) for k in kinds])],
                             placeholder="Wer oder welches Modell"))
    return page("Protokoll", body, active="protokoll")


# --------------------------------------------------------------- MCP

door = mcpdoor.Door(
    "oaap-ollama-models", VERSION,
    "Liest, welche Ollama-Modelle auf diesem Knoten installiert sind und welche gerade "
    "im Speicher liegen. Holen und löschen geht nur auf der Seite.")


def _seen(who):
    # Dieselbe Regel wie die Seite: ohne geprüfte Rolle nichts.
    if not who.get("roles"):
        raise mcpdoor.Refused("Diese Auskunft braucht eine angemeldete Sitzung.")


def _model_doc(r, loaded_names):
    return {"name": r["name"], "size": ollama.human_size(r["size"]), "bytes": r["size"],
            "parameters": r["parameters"], "quantization": r["quantization"],
            "family": r["family"], "modified": r["modified"], "loaded": r["name"] in loaded_names}


@door.tool("ollama_models_list", "Installierte Modelle",
           "Listet die auf diesem Knoten installierten Ollama-Modelle mit Größe, Parametern, "
           "Quantisierung und ob sie gerade geladen sind. Optional nach einem Wort filtern.",
           {"q": {"type": "string", "description": "Suchwort im Modellnamen oder in der Familie"}})
def _tool_list(args, who):
    _seen(who)
    installed, err = ollama.models(OLLAMA_URL)
    if err:
        raise mcpdoor.Refused(f"Ollama antwortet nicht: {err}")
    loaded, _ = ollama.running(OLLAMA_URL)
    names = {r["name"] for r in loaded}
    words = str(args.get("q") or "").lower().split()
    hits = [r for r in installed
            if all(w in f'{r["name"]} {r["family"]}'.lower() for w in words)]
    return {"models": [_model_doc(r, names) for r in hits], "count": len(hits),
            "total_size": ollama.human_size(sum(r["size"] for r in installed))}


@door.tool("ollama_models_loaded", "Geladene Modelle",
           "Zeigt, welche Modelle gerade im Speicher liegen, wie viel sie belegen, ob im "
           "Grafik- oder Arbeitsspeicher, und bis wann Ollama sie vorhält.")
def _tool_loaded(args, who):
    _seen(who)
    loaded, err = ollama.running(OLLAMA_URL)
    if err:
        raise mcpdoor.Refused(f"Ollama antwortet nicht: {err}")
    return {"loaded": [{"name": r["name"], "size": ollama.human_size(r["size"]),
                        "where": "Grafikspeicher" if r["vram"] else "Arbeitsspeicher",
                        "until": r["until"]} for r in loaded],
            "count": len(loaded)}


@door.tool("ollama_models_read", "Ein Modell",
           "Liest die Angaben zu einem installierten Modell: Größe, Parameter, "
           "Quantisierung, Familie, Änderungsdatum und ob es geladen ist.",
           {"name": {"type": "string", "description": "Modellname, z. B. qwen2.5:3b"}},
           required=("name",))
def _tool_read(args, who):
    _seen(who)
    name = str(args.get("name") or "").strip()
    installed, err = ollama.models(OLLAMA_URL)
    if err:
        raise mcpdoor.Refused(f"Ollama antwortet nicht: {err}")
    row = next((r for r in installed if r["name"] == name), None)
    if row is None:
        raise mcpdoor.Refused(f"Das Modell „{name}“ ist nicht installiert.")
    loaded, _ = ollama.running(OLLAMA_URL)
    return _model_doc(row, {r["name"] for r in loaded})


door.prompt("ollama_modell_waehlen", "Passendes Modell wählen",
            "Empfiehlt unter den installierten Modellen eines für eine Aufgabe.",
            "Welches der installierten Ollama-Modelle passt am besten für diese Aufgabe: "
            "{aufgabe}? Lies die Liste mit ollama_models_list, achte auf Größe und darauf, "
            "dass der Knoten womöglich keine Grafikkarte hat, und begründe kurz.",
            arguments=("aufgabe",))
door.prompt("ollama_wartezeit_erklaeren", "Wartezeit erklären",
            "Erklärt, warum die erste Antwort eines Modells gerade lange dauern kann.",
            "Die erste Antwort eines Modells dauert lange. Prüfe mit ollama_models_loaded, "
            "welche Modelle im Speicher liegen, und erkläre in einfachen Worten, ob das "
            "Laden die Wartezeit erklärt.")


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

    def params(self):
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        return {k: v[0] for k, v in parse_qs(qs).items() if v}

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION})
            return
        if path == "/mcp":
            door.serve(self)
            return
        user, roles = self.identity()
        if not self.roles():
            # Ohne Kopfzeile kommt niemand hier an, außer die Route ist
            # falsch gesetzt. Dann lieber schweigen als raten.
            self.send_html(403, page("Kein Zugriff", af.card("", '<p>Diese Seite braucht eine '
                                     'angemeldete Sitzung.</p>'), status_tabs=False))
            return
        admin = "admin" in self.roles()
        params = self.params()
        if path == "/modell":
            self.send_html(200, model_page(params.get("name", ""), admin))
        elif path == "/anschluss":
            self.send_html(200, gateway_page())
        elif path == "/protokoll":
            self.send_html(200, log_page(params))
        elif path == "/":
            self.send_html(200, render(user, roles, admin, params=params))
        else:
            self.send_html(404, page("Unbekannt", af.empty('Diese Seite gibt es nicht. '
                                                           '<a href="./">Zur Übersicht</a>')))

    def do_POST(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/mcp":
            door.serve(self)
            return
        user, roles = self.identity()
        if "admin" not in self.roles():
            self.send_html(403, page("Kein Zugriff", af.card("", '<p>Modelle zu holen oder zu '
                                     'löschen ist der Serververwaltung vorbehalten.</p>')))
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

        notice, error = "", False
        if not model:
            notice, error = "Ohne Modellnamen kann ich nichts tun.", True
        elif path == "/pull":
            if not start_pull(model, user):
                notice, error = f"„{model}“ wird bereits geholt.", True
        elif path == "/delete":
            okay, err = ollama.delete(OLLAMA_URL, model)
            note(user, "delete" if okay else "delete-fehlgeschlagen", model, err)
            notice, error = ((f"„{model}“ gelöscht — der Platz ist auf dem Knoten wieder frei.", False)
                             if okay else (f"Löschen fehlgeschlagen: {err}", True))
        else:
            self.send_html(404, page("Unbekannt", af.empty("Unbekannter Pfad.")))
            return
        self.send_html(200, render(user, roles, True, notice, error=error))


def main():
    sys.stdout.write(f"{APP_NAME} {VERSION} auf Port {PORT} — "
                     f"Ollama unter {OLLAMA_URL}\n")
    sys.stdout.flush()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
