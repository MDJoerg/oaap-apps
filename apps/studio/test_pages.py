#!/usr/bin/env python3
"""Das Studio am Stück — echte Anfragen gegen den echten Handler.

Kein Docker, kein Knoten, kein Gateway: Die Identität kommt als
Kopfzeile herein (genau wie im Betrieb), die Datenbank liegt in einem
Wegwerf-Verzeichnis, und die Gegenstelle des Deploy-Hooks ist ein
Papierknoten in einem zweiten Thread — der aber **denselben** Ablauf
spricht wie die Plattform (RFC-0019 §2).

Damit läuft der ganze Weg durch echten Code: Formular → Upload im Fluss
→ Paketprüfung → drei Phasen → Ergebnisseite.

    python3 test_pages.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA = tempfile.mkdtemp(prefix="studio-test-")
os.environ["STUDIO_DATA_DIR"] = DATA
os.environ["STUDIO_TMP_DIR"] = DATA

import urllib.error  # noqa: E402
import urllib.parse  # noqa: E402
import urllib.request  # noqa: E402

import app  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail[:400]}")


# ------------------------------------------------------------ Papierknoten

NODE = {"announced": None, "grant": None, "uploads": [], "refuse": None,
        # Die lesende Flotten-Auskunft desselben Knotens (RFC-0021).
        # `None` = der Knoten antwortet darauf nicht.
        "fleet": None}

# Der Flotten-Schlüssel des Papierknotens. Er darf in keiner Seite und
# in keiner Datei auftauchen — das wird unten geprüft.
FLEET_KEY = "flotten-schluessel-nur-fuer-den-test"

FLEET_DOC = {
    "schema": "oaap.fleet.status/0.2",
    "node": "papierknoten.example",
    "platform_version": "0.1.45",
    "core": [{"name": "gateway", "state": "ok"}],
    "instances": [
        {"instance": "bdt-app-test", "app": "bdt-app", "version": "0.305.0",
         "channel": "test", "state": "ok", "origin": "artifact",
         "address": "bdt-app-test.papierknoten.example"},
        {"instance": "bdt-app", "app": "bdt-app", "version": "0.304.0",
         "channel": "production", "state": "warn", "origin": "promoted",
         "address": "bdt-app.papierknoten.example"},
    ],
    "names": [
        {"name": "hub.bdt.papierknoten.example", "kind": "alias",
         "instance": "bdt-app", "state": "warn"},
    ],
    "attention": [
        {"kind": "instance_unhealthy", "instance": "bdt-app"},
        {"kind": "confirmation_pending", "instance": "ganz-andere-app"},
    ],
}


class NodeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        """Nur die Flotten-Auskunft — und nur mit ihrem Schlüssel."""
        if urlparse(self.path).path != "/fleet/status":
            return self._json(404, {"error": "unknown"})
        if self.headers.get("Authorization") != f"Bearer {FLEET_KEY}":
            return self._json(403, {})
        if NODE["fleet"] is None:
            return self._json(404, {"error": "no such route"})
        self._json(200, NODE["fleet"])

    def _json(self, status, obj):
        raw = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = json.loads(self.rfile.read(n) or b"{}")
        if self.headers.get("Authorization") != "Bearer richtiger-token":
            return self._json(403, {"error": "denied"})
        if NODE["refuse"]:
            return self._json(422, NODE["refuse"])
        NODE["announced"] = data
        NODE["grant"] = "einmal-" + data["artifact_sha256"][:8]
        host = self.headers.get("Host")
        self._json(200, {"ok": True, "upload_token": NODE["grant"],
                         "upload_url": f"http://{host}/deploy/app-test/artifact",
                         "expires_in": 900})

    def do_PUT(self):
        if self.headers.get("Authorization") != f"Bearer {NODE['grant']}":
            return self._json(403, {"error": "denied"})
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        NODE["uploads"].append(body)
        NODE["grant"] = None                      # einmalig
        self._json(200, {"ok": True, "version": NODE["announced"]["version"],
                         "revision": "r1", "message": "deployed",
                         "url": "http://app-test.example/"})


node = ThreadingHTTPServer(("127.0.0.1", 0), NodeHandler)
threading.Thread(target=node.serve_forever, daemon=True).start()
HOOK = f"http://127.0.0.1:{node.server_port}/deploy/app-test"
NODE_HOST = f"127.0.0.1:{node.server_port}"
NODE_BASE = f"http://{NODE_HOST}"

# Der Schlüssel kommt im Betrieb als geheimer Konfigurationswert und
# wird beim Start gelesen; hier ist der Port erst jetzt bekannt.
app.FLEET_KEYS = app.fleet.parse_keys(f"{NODE_HOST}={FLEET_KEY}")

# ------------------------------------------------------------ Studio starten

studio = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=studio.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{studio.server_port}"

USER = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "keyuser,admin"}


def call(method, path, headers=None, data=None, content_type=None):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a):
        return None


opener = urllib.request.build_opener(NoRedirect)


def call_noredirect(method, path, headers=None, data=None, content_type=None):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if content_type:
        req.add_header("Content-Type", content_type)
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def form(fields):
    from urllib.parse import urlencode
    return (urlencode(fields).encode(),
            "application/x-www-form-urlencoded")


BOUND = "----studiotest"


def multipart_body(fields, filename=None, content=b""):
    out = b""
    for k, v in fields.items():
        out += f"--{BOUND}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    if filename:
        out += (f"--{BOUND}\r\nContent-Disposition: form-data; name=\"paket\"; "
                f"filename=\"{filename}\"\r\n"
                f"Content-Type: application/zip\r\n\r\n").encode()
        out += content + b"\r\n"
    out += f"--{BOUND}--\r\n".encode()
    return out, f"multipart/form-data; boundary={BOUND}"


MANIFEST = """oaap_manifest: "0.2"

app:
  id: bdt-app
  name: BDT App
  version: {v}
  type: native

services:
  web:
    build: .
    port: 8000

routes:
  - path: /
    roles: {roles}

health:
  path: /healthz
"""


def package(version="0.305.0", roles="[keyuser, admin]"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("oaap-app.yaml", MANIFEST.format(v=version, roles=roles))
        z.writestr("app.py", "print('hi')")
    return buf.getvalue()


# ------------------------------------------------------------------ Prüfungen

print("=== Gesundheit und Anmeldung ===")
st, b, _ = call("GET", "/healthz")
ok("Gesundheit antwortet ohne Identität",
   st == 200 and json.loads(b)["version"] == app.VERSION, b)

st, b, _ = call("GET", "/")
ok("ohne Gateway-Kopfzeilen: 403 und kein Anmeldeformular",
   st == 403 and "nur hinter dem OAAP-Gateway" in b and "password" not in b)

st, b, _ = call("GET", "/", {"X-OAAP-User": "gast", "X-OAAP-Roles": "user"})
ok("Rolle user reicht für das Studio nicht", st == 403, b[:120])

st, b, _ = call("GET", "/", USER)
ok("keyuser sieht die Vorhaben", st == 200 and "Entwicklungsvorhaben" in b)

print("\n=== Vorhaben anlegen ===")
data, ct = form({"name": "BDT App", "instance": "", "hook_url": HOOK,
                 "app_type": "native", "status": "entwicklung",
                 "deploy_way": "artifact"})
st, b, h = call_noredirect("POST", "/vorhaben", USER, data, ct)
ok("Anlegen leitet weiter (kein erneutes Absenden beim Neuladen)",
   st == 303 and "vorhaben/bdt-app" in h.get("Location", ""), str(h))
PID = "bdt-app"

st, b, _ = call("GET", f"/vorhaben/{PID}", USER)
ok("die Objektseite trägt die Paket-Karte",
   "Paket und Deployment" in b and "Paket prüfen und ausrollen" in b)

print("\n=== Paket prüfen ===")
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("die Paketseite nennt beide Adressen des Ablaufs",
   st == 200 and "/announce" in b and "/artifact" in b)
ok("das Token-Feld ist ein Passwortfeld und nie vorbelegt",
   'type="password" name="token"' in b and 'value="' not in b.split('name="token"')[1][:80])

data, ct = multipart_body({"action": "pruefen", "uebernehmen": "1"},
                          "bdt-0.305.0.zip", package())
st, b, h = call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("Prüfen leitet weiter", st == 303 and "paket?msg=" in h.get("Location", ""), str(h))

st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("der Bericht steht auf der Seite: Version, Prüfsumme, Manifest",
   "0.305.0" in b and "bdt-app" in b and "Prüfsumme" in b)
ok("„bereit“ — keine Beanstandung", "bereit</span>" in b)

st, b, _ = call("GET", f"/vorhaben/{PID}", USER)
ok("das Vorhaben hat die Angaben aus dem Manifest übernommen",
   "bdt-0.305.0.zip" in b and "0.305.0" in b)

print("\n=== Noch nichts ausgerollt: es gibt nichts zu vergleichen ===")
data, ct = multipart_body({"action": "pruefen"}, "nochmal.zip", package())
call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("dasselbe Paket ein zweites Mal PRÜFEN ist kein Widerspruch",
   "Noch kein Vergleich möglich" in b and "bereit</span>" in b)

print("\n=== Kein Ausrollen ohne Token ===")
data, ct = multipart_body({"action": "deployen", "token": ""},
                          "x.zip", package("0.305.1"))
st, b, h = call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("ohne Token wird nicht ausgerollt",
   st == 303 and "fehler=" in h.get("Location", "") and not NODE["uploads"], str(h))

print("\n=== Kein Ausrollen eines fehlerhaften Pakets ===")
bad = io.BytesIO()
with zipfile.ZipFile(bad, "w") as z:
    z.writestr("oaap-app.yaml", MANIFEST.format(v="0.305.2", roles="[chef]"))
data, ct = multipart_body({"action": "deployen", "token": "richtiger-token"},
                          "kaputt.zip", bad.getvalue())
st, b, h = call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("ein Paket mit Fehlern geht gar nicht erst zum Knoten",
   "fehler=" in h.get("Location", "") and not NODE["uploads"], str(h))

print("\n=== Der ganze Weg: prüfen, anmelden, hochladen ===")
zip_bytes = package("0.305.1")
data, ct = multipart_body({"action": "deployen", "token": "richtiger-token"},
                          "bdt-0.305.1.zip", zip_bytes)
st, b, _ = call("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("die Ergebnisseite zeigt beide Phasen",
   st == 200 and "1 · Anmelden" in b and "2 · Hochladen" in b, b[:300])
ok("der Knoten hat genau das Paket bekommen, das angemeldet war",
   NODE["uploads"] and NODE["uploads"][-1] == zip_bytes)
import hashlib  # noqa: E402
ok("Anmeldung trug Prüfsumme und Größe des echten Pakets",
   NODE["announced"]["artifact_sha256"] == hashlib.sha256(zip_bytes).hexdigest()
   and NODE["announced"]["artifact_bytes"] == len(zip_bytes))
ok("das angemeldete Manifest ist zeichengleich dem in der ZIP",
   NODE["announced"]["manifest"]
   == zipfile.ZipFile(io.BytesIO(zip_bytes)).read("oaap-app.yaml").decode())
ok("„Ausgerollt“ mit Version und Stand", "Ausgerollt" in b and "r1" in b)
ok("der Token steht nirgends auf der Ergebnisseite", "richtiger-token" not in b)

st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("das Deployment steht im Verzeichnis des Studios",
   "Vom Studio ausgerollt" in b and "0.305.1" in b and "angenommen" in b)

print("\n=== Und JETZT dieselbe Version noch einmal ===")
before = len(NODE["uploads"])
data, ct = multipart_body({"action": "deployen", "token": "richtiger-token"},
                          "nochmal.zip", zip_bytes)
st, b, h = call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("das Studio hält es zurück, bevor der Knoten es tut",
   st == 303 and "fehler=" in h.get("Location", "")
   and len(NODE["uploads"]) == before, str(h))
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("und sagt warum: die Version ist unverändert",
   "unverändert" in b and "nicht bereit" in b)

print("\n=== Der Knoten lehnt ab ===")
NODE["refuse"] = {"refused": "envelope_widened",
                  "details": ["route '/' becomes public"],
                  "message": "needs an administrator's confirmation"}
data, ct = multipart_body({"action": "deployen", "token": "richtiger-token"},
                          "bdt-0.305.3.zip", package("0.305.3"))
st, b, _ = call("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("die Ablehnung des Knotens steht wörtlich da (HTML-sicher gesetzt)",
   "needs an administrator" in b and "becomes public" in b
   and "route &#x27;/&#x27;" in b, b[:400])
ok("dazu der Satz, was zu tun ist", "server_admin" in b)
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("auch die Ablehnung steht im Verzeichnis", "abgelehnt" in b)
NODE["refuse"] = None

print("\n=== Falscher Token ===")
data, ct = multipart_body({"action": "deployen", "token": "falscher-token"},
                          "x.zip", package("0.305.4"))
st, b, _ = call("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("403 wird erklärt, ohne über die Instanz zu plaudern",
   "Token wurde nicht angenommen" in b and "sagt bewusst nicht" in b,
   b[-600:])

print("\n=== Der Knoten antwortet gar nicht ===")
# Auf einem kleinen Knoten kann der erste Bau eines Images laenger
# dauern, als das Studio wartet — der Knoten rollt derweil weiter aus.
# Keine Antwort ist deshalb KEINE Ablehnung; alles andere waere eine
# Falschaussage ueber eine Instanz, die es hinterher gibt.
STUMM = "http://127.0.0.1:9/deploy/app-test"      # discard-Port: nimmt an, sagt nichts


def set_hook(url):
    data, ct = form({"name": "BDT App", "instance": "", "hook_url": url,
                     "app_type": "native", "status": "entwicklung",
                     "deploy_way": "artifact"})
    call_noredirect("POST", f"/vorhaben/{PID}", USER, data, ct)


set_hook(STUMM)
data, ct = multipart_body({"action": "deployen", "token": "richtiger-token"},
                          "stumm.zip", package("0.305.9"))
st, b, h = call_noredirect("POST", f"/vorhaben/{PID}/paket", USER, data, ct)
ok("das Studio meldet den Ausfall, statt eine Ablehnung zu erfinden",
   st == 303 and "nicht erreichbar"
   in urllib.parse.unquote(h.get("Location", "")), str(h))
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("im Verzeichnis steht „Ausgang unklar“, nicht „abgelehnt“",
   "Ausgang unklar" in b, b[b.find("Vom Studio ausgerollt"):][:700])
ok("und der Hinweis, wo das verbindliche Protokoll steht",
   "im Portal unter der Instanz nachsehen" in b
   or "Portal unter der Instanz" in b, b[-800:])
set_hook(HOOK)

print("\n=== Zielknoten: wo das Vorhaben wirklich läuft (0.3) ===")


def save_project(**over):
    """Das Vorhaben speichern — mit allen Feldern, wie das Formular es tut."""
    fields = {"name": "BDT App", "app_type": "native", "status": "entwicklung",
              "deploy_way": "artifact", "hook_url": HOOK,
              "instance": "bdt-app-test", "prod_instance": "bdt-app",
              "node_url": ""}
    fields.update(over)
    data, ct = form(fields)
    return call_noredirect("POST", f"/vorhaben/{PID}", USER, data, ct)


save_project()
st, b, _ = call("GET", f"/vorhaben/{PID}", USER)
ok("die Objektseite benennt den Zielknoten",
   "Zielknoten und Instanzen" in b and NODE_HOST in b)
ok("und sagt, woher sie ihn weiß", "laut Deploy-Hook" in b)
ok("beide Instanzen stehen da, auch ohne Auskunft vom Knoten",
   f"{NODE_BASE}/instances/bdt-app-test" in b
   and f"{NODE_BASE}/instances/bdt-app" in b)
ok("antwortet der Knoten nicht, steht der Grund da statt einer Vermutung",
   "keine Auskunft" in b, b[b.find("Zielknoten und Instanzen"):][:900])

print("\n=== Zustand beider Instanzen über die Flotten-Auskunft ===")
NODE["fleet"] = FLEET_DOC
st, b, _ = call("GET", f"/vorhaben/{PID}?frisch=1", USER)
karte = b[b.find("Zielknoten und Instanzen"):]
karte = karte[:karte.find("Paket und Deployment")]
ok("die Test-Instanz mit Version und Ampel",
   "0.305.0" in karte and "Gesund" in karte, karte[:900])
ok("die Produktiv-Instanz mit ihrer eigenen Version und Ampel",
   "0.304.0" in karte and "Auffällig" in karte, karte[:900])
ok("die Plattformversion des Zielknotens", "0.1.45" in karte)
ok("Auffälligkeiten der eigenen Instanzen kommen mit",
   "Instanz ungesund" in karte)
ok("die einer fremden Instanz nicht", "Bestätigung offen" not in karte)
ok("die veröffentlichte Adresse mit dem DNS-Urteil des Knotens",
   "hub.bdt.papierknoten.example" in karte)
ok("der Flotten-Schlüssel steht auf keiner Seite", FLEET_KEY not in b)

save_project(prod_instance="gibt-es-nicht")
st, b, _ = call("GET", f"/vorhaben/{PID}?frisch=1", USER)
ok("eine Instanz, die der Knoten nicht kennt, wird benannt",
   "auf dem Knoten nicht vorhanden" in b)

save_project(prod_instance="")
st, b, _ = call("GET", f"/vorhaben/{PID}?frisch=1", USER)
ok("ohne Produktiv-Instanz erklärt die Seite den Weg dorthin",
   "Portal des" in b and "RFC-0020" in b and "üblicher Name wäre" in b)

save_project(node_url="https://ganz-anderer-knoten.example")
st, b, _ = call("GET", f"/vorhaben/{PID}?frisch=1", USER)
ok("Widerspruch zwischen Feld und Hook wird benannt, nicht verschluckt",
   "Es gilt der Hook" in b and "ganz-anderer-knoten.example" in b)

print("\n=== Ohne Flotten-Schlüssel läuft alles Übrige weiter ===")
merken, app.FLEET_KEYS = app.FLEET_KEYS, {}
save_project()
st, b, _ = call("GET", f"/vorhaben/{PID}", USER)
ok("die Seite steht", st == 200)
ok("und sagt, wie man die Anzeige einrichtet",
   "oaap fleet key issue" in b and "STUDIO_FLEET_KEYS" in b)
ok("mit der Begründung, warum das kein Recht ist",
   "kein Schreibweg" in b)
ok("die Instanzen und ihre Verweise stehen trotzdem da",
   f"{NODE_BASE}/instances/bdt-app-test" in b)
app.FLEET_KEYS = merken

print("\n=== Ohne Hook und ohne Feld: der eigene Knoten ist eine Vermutung ===")
save_project(hook_url="", node_url="")
st, b, _ = call("GET", f"/vorhaben/{PID}", USER)
ok("sie steht als Vermutung da, nicht als Tatsache", "angenommen" in b)
save_project()

print("\n=== Der Zielknoten in Paketseite, Zettel und Briefing ===")
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", USER)
ok("die Paketseite verweist auf das Portal des ZIELKNOTENS",
   f"{NODE_BASE}/instances/bdt-app-test" in b and "Zielknoten" in b)
st, b, _ = call("GET", f"/vorhaben/{PID}/zettel.md", USER)
ok("der Zettel für die Projekt-KI nennt den Zielknoten",
   "## Zielknoten" in b and f"{NODE_BASE}/instances/bdt-app-test" in b)
ok("und wo die Produktivsetzung stattfindet",
   "Portal des" in b and "RFC-0020" in b)
st, b, _ = call("GET", f"/vorhaben/{PID}/briefing.md", USER)
ok("das Briefing nennt den Knoten der Instanz", NODE_HOST in b)

print("\n=== Deployment-Zettel ===")
st, b, _ = call("GET", f"/vorhaben/{PID}/zettel", USER)
ok("das Blatt steht ohne Token da",
   st == 200 and "dein Deploy-Token" in b and "Anmelden" in b)
st, b, h = call("GET", f"/vorhaben/{PID}/zettel.md", USER)
ok("als Datei zum Herunterladen, Markdown",
   "text/markdown" in h.get("Content-Type", "")
   and "attachment" in h.get("Content-Disposition", ""))
ok("die Datei enthält nie einen Token", "TOKEN=<dein Deploy-Token>" in b)

data, ct = form({"token": "richtiger-token"})
st, b, _ = call("POST", f"/vorhaben/{PID}/zettel", USER, data, ct)
ok("mit Eingabe steht der Token genau einmal im Blatt",
   "richtiger-token" in b and "einmalig" in b)

st, b, _ = call("GET", f"/vorhaben/{PID}/zettel", USER)
ok("und ist danach wieder weg — nichts gespeichert",
   "richtiger-token" not in b)

print("\n=== Rollen ===")
KEY = {"X-OAAP-User": "kim", "X-OAAP-Roles": "keyuser"}
st, b, _ = call("GET", f"/vorhaben/{PID}/loeschen", KEY)
ok("Löschen bleibt admin vorbehalten", st == 403)
st, b, _ = call("GET", f"/vorhaben/{PID}/paket", KEY)
ok("keyuser darf Pakete prüfen und ausrollen", st == 200)

print("\n=== Fremde Formulare ===")
data, ct = form({"name": "böse"})
st, b, _ = call("POST", "/vorhaben", dict(USER, **{"Sec-Fetch-Site": "cross-site"}),
                data, ct)
ok("ein Formular von fremder Seite wird abgewiesen", st == 403)

print("\n=== Was in den Protokollen landen darf ===")
roh = open(os.path.join(DATA, "studio.db"), "rb").read()
ok("kein Token in der Datenbank", b"richtiger-token" not in roh)
ok("und kein Flotten-Schlüssel — das Studio schreibt ihn nie",
   FLEET_KEY.encode() not in roh)

node.shutdown()
studio.shutdown()
shutil.rmtree(DATA, ignore_errors=True)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
