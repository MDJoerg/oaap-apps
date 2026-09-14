"""OAAP Starter — die kleinste App, die alle Plattformregeln einhält.

Sie ist als Vorlage gedacht, nicht als Produkt: Wer eine eigene App
baut, ersetzt den Inhalt und behält den Rahmen. Der Rahmen zeigt genau
die sechs Dinge, an denen fremde Apps sonst scheitern:

1. **Kein eigener Login.** Die geprüfte Identität kommt vom Gateway als
   `X-OAAP-User` und `X-OAAP-Roles`. Ein Anmeldeformular hier wäre ein
   Fehler — die Plattform hat die Anmeldung schon erledigt.
2. **Ein HTTP-Port, kein TLS.** Das Gateway terminiert TLS.
3. **Persistenz nur unter einem deklarierten Mount** (`/data`).
4. **Konfiguration nur über deklarierte Umgebungsvariablen.**
5. **Logs nach stdout**, dazu ein Gesundheitspfad ohne Anmeldung.
6. **Keine externen Ressourcen** — keine Webfonts, keine CDNs; die App
   muss in einem Netz ohne Internet vollständig funktionieren.

Nur Standardbibliothek, damit das Paket ohne jede Abhängigkeit baut.
"""

import html
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VERSION = "0.1.0"
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


def note_visit(user):
    visits = read_visits()
    visits[user] = visits.get(user, 0) + 1
    tmp = VISITS + ".tmp"
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(visits, fh, ensure_ascii=False)
        os.replace(tmp, VISITS)          # atomar: nie eine halbe Datei
    except OSError as exc:
        print(f"besuch nicht gespeichert: {exc}", file=sys.stdout, flush=True)
    return visits[user]


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
        user = self.headers.get("X-OAAP-User", "").strip()
        roles = [r.strip() for r in
                 self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()]
        if not user:
            return self.send(
                "<p>Diese App wird über das OAAP-Gateway aufgerufen. "
                "Direkt erreicht sie niemand.</p>", status=403)

        if path != "/":
            return self.send("<p>Seite nicht gefunden.</p>", status=404)

        count = note_visit(user)
        rows = "".join(
            f"<tr><td>{esc(name)}</td><td>{n}</td></tr>"
            for name, n in sorted(read_visits().items()))
        self.send(f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OAAP Starter</title>{STYLE}
<header><b>OAAP STARTER</b></header>
<main>
  <h1>{esc(GREETING)}, {esc(user)}!</h1>
  <div class="card">
    <h2>Was die Plattform über Dich sagt</h2>
    <table>
      <tr><th>Benutzer (<code>X-OAAP-User</code>)</th><td>{esc(user)}</td></tr>
      <tr><th>Rollen (<code>X-OAAP-Roles</code>)</th>
          <td>{esc(", ".join(roles)) or '<span class="muted">keine</span>'}</td></tr>
    </table>
    <p class="muted">Diese Kopfzeilen setzt das Gateway. Die App hat kein
    Anmeldeformular und wird auch keines bekommen.</p>
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
