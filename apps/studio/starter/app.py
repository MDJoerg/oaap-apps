"""OAAP Starter — die kleinste App, die alle Plattformregeln einhält.

Sie ist als Vorlage gedacht, nicht als Produkt: Wer eine eigene App
baut, ersetzt den Inhalt und behält den Rahmen. Der Rahmen zeigt genau
die sechs Dinge, an denen fremde Apps sonst scheitern:

1. **Kein eigener Login.** Die geprüfte Identität kommt vom Gateway in
   den Kopfzeilen `X-OAAP-User-Id` (die Person: eine UUID, die sich nie
   ändert — **daran hängen die Daten**), `X-OAAP-User` (der Anmeldename:
   anzeigen, nicht darauf bauen, er ist änderbar), `X-OAAP-Roles`,
   `X-OAAP-Display-Name` und `X-OAAP-Email`. Ein Anmeldeformular hier
   wäre ein Fehler — die Plattform hat die Anmeldung schon erledigt.
2. **Ein HTTP-Port, kein TLS.** Das Gateway terminiert TLS.
3. **Persistenz nur unter einem deklarierten Mount** (`/data`).
4. **Konfiguration nur über deklarierte Umgebungsvariablen.**
5. **Logs nach stdout**, dazu ein Gesundheitspfad ohne Anmeldung.
6. **Keine externen Ressourcen** — keine Webfonts, keine CDNs; die App
   muss in einem Netz ohne Internet vollständig funktionieren.

Dazu zeigt die Startseite, welche **Plattformtüren** diese Instanz
bekommen hat (Zwilling, Berechtigungen, Destinationen, Vorgänge …) —
nur ob die Variable da ist, nie ihren Wert. So sieht man nach dem
Einkommentieren eines Manifest-Abschnitts sofort, ob er angekommen ist.

Nur Standardbibliothek, damit das Paket ohne jede Abhängigkeit baut.
"""

import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "0.2.0"
PORT = 8000

# Regel 3: nur dieser Pfad überlebt ein Deployment und wird gesichert.
DATA_DIR = "/data"
VISITS = os.path.join(DATA_DIR, "besuche.json")

# Regel 4: Konfiguration kommt aus deklarierten Variablen, mit Vorgabe.
GREETING = os.environ.get("STARTER_GREETING", "Willkommen")

STYLE = """<style>
  :root{color-scheme:light}
  body{margin:0;font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
       background:#f8fafc;color:#0f172a}
  header{background:#1e3a8a;color:#fff;padding:1rem 1.4rem}
  header b{font-size:1.1rem;letter-spacing:.04em}
  main{max-width:46rem;margin:0 auto;padding:1.4rem}
  .card{background:#fff;border:1px solid #e2e8f0;border-radius:.6rem;
        padding:1.1rem 1.4rem;margin-bottom:1rem}
  h1{font-size:1.4rem;margin:.2rem 0 1rem}
  h2{font-size:1.05rem;margin:0 0 .6rem}
  table{border-collapse:collapse;width:100%}
  td,th{text-align:left;padding:.45rem .3rem;border-bottom:1px solid #e2e8f0}
  code{background:#f1f5f9;padding:.1rem .3rem;border-radius:.25rem}
  .muted{color:#64748b}
  a.btn{display:inline-block;min-height:44px;line-height:44px;padding:0 1.1rem;
        background:#2563eb;color:#fff;border-radius:.4rem;text-decoration:none}
</style>"""


def read_visits():
    """Zustand lesen — und ein fehlendes oder kaputtes File überleben.

    Ein leerer Mount ist der Normalfall beim allerersten Start, kein
    Fehler. Eine App, die daran stirbt, kommt nie hoch.
    """
    try:
        with open(VISITS, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def note_visit(user_id, name):
    """Besuche je PERSON zählen — Schlüssel ist die UUID, nicht der Name.

    Wird jemand umbenannt, bleibt die UUID; ein Zähler am Namen finge
    dann von vorn an (und ein neu vergebener alter Name erbte fremde
    Daten). Der Name wird nur zur Anzeige mitgeschrieben.
    """
    visits = read_visits()
    entry = visits.get(user_id)
    if not isinstance(entry, dict):
        entry = {"n": 0}
    entry["n"] = entry.get("n", 0) + 1
    entry["name"] = name
    visits[user_id] = entry
    tmp = VISITS + ".tmp"
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(visits, fh, ensure_ascii=False)
        os.replace(tmp, VISITS)          # atomar: nie eine halbe Datei
    except OSError as exc:
        print(f"besuch nicht gespeichert: {exc}", file=sys.stdout, flush=True)
    return entry["n"]


# Plattformtüren: Variablen, die die Plattform setzt, wenn das Manifest
# den Abschnitt trägt (und der Knoten ihn kann). Gezeigt wird nur, OB
# sie da sind — Schlüssel gehören nie auf eine Seite und nie ins Log.
DOORS = [
    ("Digitaler Zwilling", "data_model / contributes / consumes",
     ("OAAP_TWIN_URL", "OAAP_PLATFORM_KEY")),
    ("Fachliche Berechtigungen", "authorization", ("OAAP_AUTHZ_URL", "OAAP_AUTHZ_KEY")),
    ("Vorgänge öffnen", "cases.submit", ("OAAP_CASES_URL", "OAAP_CASES_KEY")),
    ("Eigenes Geheimnis der Instanz", "(immer)", ("OAAP_APP_SECRET",)),
]


def doors():
    rows = [(title, section, all(os.environ.get(v) for v in names))
            for title, section, names in DOORS]
    dests = sorted(k for k in os.environ
                   if k.startswith("OAAP_DESTINATION_") and k.endswith("_URL"))
    rows.append(("Destinationen", "destinations",
                 ", ".join(d[len("OAAP_DESTINATION_"):-len("_URL")] for d in dests)
                 or False))
    return rows


def esc(value):
    return html.escape(str(value), quote=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "oaap-starter/" + VERSION
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):        # Regel 5: stdout, sonst nichts
        sys.stdout.write("%s - %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()

    def send(self, body, status=200, content_type="text/html; charset=utf-8"):
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/") or "/"

        # Regel 5: Gesundheit zuerst — ohne Identität, ohne Seiteneffekt.
        if path == "/healthz":
            return self.send(json.dumps({"status": "ok", "version": VERSION}),
                             content_type="application/json")

        # Regel 1: Die Identität kommt vom Gateway. Fehlt sie, läuft die
        # App an der Plattform vorbei — dann wird nichts ausgeliefert.
        user_id = self.headers.get("X-OAAP-User-Id", "").strip()
        user = self.headers.get("X-OAAP-User", "").strip()
        shown = self.headers.get("X-OAAP-Display-Name", "").strip() or user
        roles = [r.strip() for r in
                 self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()]
        if not user:
            return self.send(
                "<p>Diese App wird über das OAAP-Gateway aufgerufen. "
                "Direkt erreicht sie niemand.</p>", status=403)

        if path != "/":
            return self.send("<p>Seite nicht gefunden.</p>", status=404)

        # Ein API-Schlüssel (Maschine) trägt einen Namen, aber keine UUID
        # einer Person — dann zählt der Name. Für Personen immer die UUID.
        count = note_visit(user_id or "name:" + user, user)
        rows = "".join(
            f"<tr><td>{esc(v.get('name', '?'))}</td><td>{v.get('n', 0)}</td></tr>"
            for v in sorted((v for v in read_visits().values() if isinstance(v, dict)),
                            key=lambda v: str(v.get("name", ""))))
        door_rows = "".join(
            f"<tr><td>{esc(t)}</td><td><code>{esc(sec)}</code></td><td>"
            + (esc(ok) if isinstance(ok, str) else ("ja" if ok else '<span class="muted">nein</span>'))
            + "</td></tr>" for t, sec, ok in doors())
        self.send(f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OAAP Starter</title>{STYLE}
<header><b>OAAP STARTER</b></header>
<main>
  <h1>{esc(GREETING)}, {esc(shown)}!</h1>
  <div class="card">
    <h2>Was die Plattform über Dich sagt</h2>
    <table>
      <tr><th>Person (<code>X-OAAP-User-Id</code>)</th>
          <td>{esc(user_id) or '<span class="muted">keine — Maschine</span>'}</td></tr>
      <tr><th>Anmeldename (<code>X-OAAP-User</code>)</th><td>{esc(user)}</td></tr>
      <tr><th>Rollen (<code>X-OAAP-Roles</code>)</th>
          <td>{esc(", ".join(roles)) or '<span class="muted">keine</span>'}</td></tr>
    </table>
    <p class="muted">Diese Kopfzeilen setzt das Gateway. Die App hat kein
    Anmeldeformular und wird auch keines bekommen. Eigene Daten hängen an
    der <code>X-OAAP-User-Id</code>; der Anmeldename ist änderbar.</p>
  </div>
  <div class="card">
    <h2>Welche Plattformtüren diese Instanz hat</h2>
    <table><tr><th>Fähigkeit</th><th>Manifest</th><th>Da?</th></tr>{door_rows}</table>
    <p class="muted">Gezeigt wird nur, ob die Variable gesetzt ist — nie ihr
    Wert. „nein“ heißt: Abschnitt nicht im Manifest, oder der Knoten kann
    ihn nicht (der Zwilling braucht das Knotenprofil <code>store</code>).</p>
  </div>
  <div class="card">
    <h2>Was den Neustart überlebt</h2>
    <p>Dein {count}. Besuch. Gezählt wird in <code>/data</code> — dem
    einzigen Pfad, den das Manifest als Speicher deklariert und den die
    Plattform sichert.</p>
    <table><tr><th>Benutzer</th><th>Besuche</th></tr>{rows}</table>
  </div>
  <div class="card">
    <h2>Und jetzt?</h2>
    <p>Ersetze den Inhalt dieser App durch Deinen — behalte den Rahmen:
    Manifest, ein Port, <code>/data</code>, <code>/healthz</code>,
    Identität aus den Kopfzeilen. Was das Paket erfüllen muss, steht im
    Plattform-Briefing, das Du zusammen mit diesem Paket bekommen hast.</p>
  </div>
</main></html>""")


def main():
    print(f"oaap-starter {VERSION} auf Port {PORT}, Speicher {DATA_DIR}",
          flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
