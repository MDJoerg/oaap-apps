#!/usr/bin/env python3
"""Die Ollama-Oberfläche gegen ein **Papier-Ollama**.

Ein kleiner Server, der sich wie Ollama verhält: Modelle auflisten, was
geladen ist, löschen, und ein Holen, das seinen Fortschritt zeilenweise
meldet. Kein Netz nach draußen, kein Docker, kein Modell.

Geprüft wird, was Entscheidungen trifft: dass die Rollentrennung hält
(sehen darf jeder Schlüsselanwender, holen und löschen nur die
Serververwaltung), dass ein nicht erreichbares Ollama ein **Zustand mit
Anleitung** ist statt einer Fehlerseite, dass der Fortschritt ankommt —
und dass jede Handlung mit Urheber im Protokoll steht.

Run: python3 apps/ollama-models/test_models.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


# ------------------------------------------------------------- Papier-Ollama

class Fake:
    models = [
        {"name": "qwen2.5:3b", "size": 2_000_000_000, "modified_at": "2026-08-20T10:00:00Z",
         "details": {"parameter_size": "3B", "quantization_level": "Q4_K_M", "family": "qwen2"}},
        {"name": "nomic-embed-text:latest", "size": 274_000_000,
         "modified_at": "2026-08-19T09:00:00Z", "details": {"parameter_size": "137M"}},
    ]
    loaded = [{"name": "qwen2.5:3b", "size": 2_400_000_000, "size_vram": 0,
               "expires_at": "2026-08-24T18:05:00Z"}]
    deleted = []
    pulled = []
    pull_fails = ""
    pull_delay = 0.0


class FakeOllama(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _json(self, status, doc):
        body = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            return self._json(200, {"version": "0.9.1"})
        if self.path == "/api/tags":
            return self._json(200, {"models": Fake.models})
        if self.path == "/api/ps":
            return self._json(200, {"models": Fake.loaded})
        self._json(404, {"error": "nope"})

    def do_DELETE(self):
        length = int(self.headers.get("Content-Length") or 0)
        doc = json.loads(self.rfile.read(length) or b"{}")
        Fake.deleted.append(doc.get("model"))
        self._json(200, {})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        doc = json.loads(self.rfile.read(length) or b"{}")
        if self.path != "/api/pull":
            return self._json(404, {"error": "nope"})
        Fake.pulled.append(doc.get("model"))
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        lines = [{"status": "pulling manifest"},
                 {"status": "pulling", "completed": 500, "total": 1000}]
        if Fake.pull_fails:
            lines.append({"error": Fake.pull_fails})
        else:
            lines.append({"status": "pulling", "completed": 1000, "total": 1000})
            lines.append({"status": "success"})
        for line in lines:
            if Fake.pull_delay:
                time.sleep(Fake.pull_delay)
            chunk = (json.dumps(line) + "\n").encode()
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


fake = start(FakeOllama)
FAKE_URL = f"http://127.0.0.1:{fake.server_address[1]}"

TMP = tempfile.mkdtemp(prefix="ollama-models-test-")
os.environ["OLLAMA_URL"] = FAKE_URL
os.environ["OLLAMA_MODELS_DATA_DIR"] = TMP

import app      # noqa: E402  (liest die Konfiguration beim Import)
import ollama   # noqa: E402

gw = start(app.Handler)
BASE = f"http://127.0.0.1:{gw.server_address[1]}"


def call(path, method="GET", form=None, roles="admin,user", user="joerg"):
    data, headers = None, {}
    if form is not None:
        data = form.encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if roles is not None:
        headers["X-OAAP-User"] = user
        headers["X-OAAP-Roles"] = roles
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


# ------------------------------------------------------------------ Prüfungen

print("-- Der Draht zu Ollama")
ver, err = ollama.version(FAKE_URL)
check("Version gelesen", ver == "0.9.1" and not err, (ver, err))
rows, err = ollama.models(FAKE_URL)
check("zwei Modelle", len(rows) == 2 and not err, rows)
check("größtes zuerst", rows[0]["name"] == "qwen2.5:3b", [r["name"] for r in rows])
check("Details übernommen", rows[0]["quantization"] == "Q4_K_M", rows[0])
loaded, _ = ollama.running(FAKE_URL)
check("geladenes Modell erkannt", loaded and loaded[0]["name"] == "qwen2.5:3b", loaded)
check("ohne Grafikspeicher = 0", loaded[0]["vram"] == 0)

print("\n-- Größen lesbar, und ohne Erfindung")
check("2 GB", ollama.human_size(2_000_000_000).endswith("GB"), ollama.human_size(2_000_000_000))
check("nichts ist kein Strich ins Blaue", ollama.human_size(0) == "—")

print("\n-- Instanzname aus der Adresse (für die Gateway-Zeilen)")
check("Containername wird abgeschält",
      ollama.instance_of("http://oaap-app-ollama:11434") == "ollama")
check("fremde Adresse bleibt, wie sie ist",
      ollama.instance_of("https://ki.example.de/") == "ki.example.de")

print("\n-- Nicht erreichbar ist ein Zustand mit Anleitung, keine Fehlerseite")
merk = app.OLLAMA_URL
app.OLLAMA_URL = "http://127.0.0.1:1"
seite = app.render("joerg", "admin", True)
app.OLLAMA_URL = merk
check("die Seite kommt trotzdem", "<html" in seite)
check("sie nennt die Ursache beim Namen", "App-zu-App-Verbindung" in seite)
check("und den Befehl, der sie behebt", "oaap app link add" in seite, seite[:200])

print("\n-- Rollentrennung: sehen darf mehr Leute als ändern")
status, seite = call("/", roles="keyuser,user")
check("Schlüsselanwender sieht die Seite", status == 200, status)
check("aber keinen Holen-Knopf", "Modell holen" not in seite)
check("und die Seite sagt auch, warum", "Serververwaltung" in seite, seite[-400:])
status, _ = call("/pull", "POST", form="model=qwen2.5%3A3b", roles="keyuser,user")
check("Holen wird ihm verweigert", status == 403, status)
check("und ist auch nicht passiert", Fake.pulled == [], Fake.pulled)
status, _ = call("/", roles=None)
check("ohne Rollen-Kopfzeile gar nichts", status == 403, status)

print("\n-- Die Verwaltung darf, und der Fortschritt kommt an")
status, seite = call("/", roles="admin,user")
check("Verwaltung sieht den Holen-Bereich", "Modell holen" in seite and status == 200)
check("Startpunkte mit ehrlicher Einordnung", "ohne\n    Grafikkarte" in seite
      or "ohne <b>Grafikkarte</b>" in seite or "Grafikkarte" in seite)

Fake.pull_delay = 0.25          # damit der Vorgang beim Rendern noch läuft
status, seite = call("/pull", "POST", form="model=qwen2.5%3A3b")
check("Antwortseite kommt sofort", status == 200)
check("der laufende Vorgang steht drauf", "Wird geholt" in seite, seite[:300])
check("mit Balken und Zahlen", "class=\"bar\"" in seite or "angefragt" in seite)
check("und die Seite aktualisiert sich selbst", "http-equiv=\"refresh\"" in seite)

for _ in range(80):
    if app.pull_state().get("qwen2.5:3b", {}).get("done"):
        break
    time.sleep(0.1)
state = app.pull_state()["qwen2.5:3b"]
check("der Vorgang läuft durch", state["done"] and not state["error"], state)
check("Fortschritt wurde mitgeschrieben", state["total"] == 1000, state)
check("Ollama wurde genau einmal gefragt", Fake.pulled == ["qwen2.5:3b"], Fake.pulled)

print("\n-- Ein zweites Holen desselben Modells prallt ab")
Fake.pull_delay = 0.4
app._pulls.clear()
call("/pull", "POST", form="model=zweimal")
status, seite = call("/pull", "POST", form="model=zweimal")
check("die Seite sagt es, statt doppelt zu laden", "wird bereits geholt" in seite, seite[:300])
check("und Ollama sah nur einen Auftrag", Fake.pulled.count("zweimal") == 1, Fake.pulled)
Fake.pull_delay = 0.0

print("\n-- Ein Fehler der Quelle wird berichtet, nicht verschluckt")
app._pulls.clear()
Fake.pull_fails = "manifest nicht gefunden"
call("/pull", "POST", form="model=gibtsnicht")
for _ in range(80):
    if app.pull_state().get("gibtsnicht", {}).get("done"):
        break
    time.sleep(0.1)
state = app.pull_state()["gibtsnicht"]
check("als abgebrochen vermerkt", state["done"] and state["error"], state)
check("mit dem Wortlaut der Quelle", "manifest" in state["error"], state["error"])
Fake.pull_fails = ""

print("\n-- Löschen")
status, seite = call("/delete", "POST", form="model=nomic-embed-text%3Alatest")
check("Ollama wurde beauftragt", Fake.deleted == ["nomic-embed-text:latest"], Fake.deleted)
check("die Seite bestätigt es", "gelöscht" in seite, seite[:300])

print("\n-- Protokoll: jede Handlung mit Urheber")
log = app.recent(50)
arten = {r["action"] for r in log}
check("Holen steht drin", "pull" in arten or "pull-gestartet" in arten, arten)
check("Löschen steht drin", "delete" in arten, arten)
check("mit Urheber", all(r.get("actor") for r in log), log[:2])
check("die Datei liegt unter dem deklarierten Mount",
      os.path.exists(os.path.join(TMP, "actions.jsonl")))

print("\n-- Die Zeilen für das Gateway stehen auf ihrem Reiter")
status, seite = call("/anschluss")
check("die Verbindung wird genannt", "oaap app link add" in seite)
check("die Bezugsquelle mit grüner Ampel", "light=green" in seite, "")
check("und ein Alias-Vorschlag", "chat-default = ollama:" in seite)

print("\n-- Der Rahmen der Plattform (RFC-0118)")
status, seite = call("/", roles="keyuser,user")
check("Kit der Plattform geladen", "/platform/theme.css" in seite)
check("Rahmenskript der Plattform geladen", "/platform/app.js" in seite)
check("App-Name ohne OAAP", "Ollama-Modelle" in seite and "OAAP Ollama" not in seite)
check("keine Rollen auf der Seite", "keyuser,user" not in seite)
check("keine eigene Palette", "#1e3a8a" not in seite and "--oaap-blue-900:" not in seite)
check("Reiter: Übersicht zuerst", seite.find(">Übersicht<") < seite.find(">Protokoll<")
      and 'aria-current="page">Übersicht' in seite)
check("Fußzeile mit Name und Version", f"Ollama-Modelle {app.VERSION}" in seite)
check("kein Löschknopf in einer Tabellenzeile", 'action="delete"' not in seite)

print("\n-- Listenbericht der installierten Modelle")


def installed_part(seite):
    return seite.split("Installierte Modelle")[1].split("Modell holen")[0]


status, seite = call("/?q=nomic")
part = installed_part(seite)
check("Suche filtert", "nomic-embed-text" in part and "qwen2.5:3b" not in part, part[-600:])
status, seite = call("/?sort=name")
part = installed_part(seite)
check("Sortieren nach Name", part.find("nomic-embed-text") < part.find("qwen2.5:3b"))
check("Spaltenkopf markiert", 'aria-sort="ascending"' in seite)
status, seite = call("/?geladen=ja")
check("Filter: nur geladene", "nomic-embed-text" not in installed_part(seite))
check("Zählzeile", "Modelle</p>" in seite)
alt = Fake.models
Fake.models = [{"name": f"m{i:03d}:1b", "size": 1000 + i, "modified_at": "2026-08-20T10:00:00Z",
                "details": {}} for i in range(60)]
status, seite = call("/")
check("ab 50 Zeilen wird geblättert", "Weitere 50" in seite and "1–50 von 60" in seite)
status, seite = call("/?offset=50")
check("zweite Seite", "51–60 von 60" in seite)
Fake.models = alt

print("\n-- Objektseite eines Modells")
status, seite = call("/modell?name=qwen2.5%3A3b")
check("Objektseite kommt", status == 200 and "Zurück zur Übersicht" in seite and "Q4_K_M" in seite)
check("Verwaltung sieht Löschen", 'action="delete"' in seite)
status, seite = call("/modell?name=qwen2.5%3A3b", roles="keyuser")
check("Schlüsselanwender sieht kein Löschen", status == 200 and 'action="delete"' not in seite)
status, seite = call("/modell?name=gibtsnicht")
check("unbekanntes Modell: Hinweis statt Absturz", status == 200 and "nicht (mehr) installiert" in seite)
status, seite = call("/protokoll")
check("Protokoll als Liste", status == 200 and "oaap-table" in seite and "joerg" in seite)


def mcp(method, params=None, roles="keyuser", user="anna"):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    headers = {"Content-Type": "application/json"}
    if roles is not None:
        headers.update({"X-OAAP-User": user, "X-OAAP-Roles": roles})
    req = urllib.request.Request(BASE + "/mcp", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def text(r):
    return r["result"]["content"][0]["text"]


print("\n-- MCP-Tür (nur lesen)")
r = mcp("initialize", {"protocolVersion": "2025-06-18"})
check("initialize", r["result"]["serverInfo"]["name"] == "oaap-ollama-models")
tools = mcp("tools/list")["result"]["tools"]
names = {t["name"] for t in tools}
check("drei Lesewerkzeuge", names == {"ollama_models_list", "ollama_models_loaded", "ollama_models_read"}, names)
check("alle nur lesend", all(t["annotations"]["readOnlyHint"] for t in tools))
r = mcp("tools/call", {"name": "ollama_models_list", "arguments": {}})
doc = json.loads(text(r))
check("list: Modelle mit Größe", not r["result"]["isError"] and doc["count"] == 2 and doc["models"][0]["size"], doc)
r = mcp("tools/call", {"name": "ollama_models_list", "arguments": {"q": "nomic"}})
check("list: Suchwort filtert", json.loads(text(r))["count"] == 1)
r = mcp("tools/call", {"name": "ollama_models_loaded", "arguments": {}})
doc = json.loads(text(r))
check("loaded: das geladene Modell", doc["loaded"][0]["name"] == "qwen2.5:3b"
      and doc["loaded"][0]["where"] == "Arbeitsspeicher", doc)
r = mcp("tools/call", {"name": "ollama_models_read", "arguments": {"name": "qwen2.5:3b"}})
check("read: ein Modell", json.loads(text(r))["loaded"] is True)
r = mcp("tools/call", {"name": "ollama_models_read", "arguments": {"name": "nix"}})
check("read: unbekannt als Ablehnung", r["result"]["isError"] and "nicht installiert" in text(r))
r = mcp("tools/call", {"name": "ollama_models_list", "arguments": {}}, roles=None)
check("ohne Rolle: abgelehnt wie die Seite", r["result"]["isError"] and "angemeldete" in text(r))
check("Vorlagen für die Hauptaufgaben", len(mcp("prompts/list")["result"]["prompts"]) == 2)
check("GET /mcp: 405", call("/mcp", roles=None)[0] == 405)

print("\n-- Gesundheit")
status, body = call("/healthz", roles=None)
check("ohne Sitzung erreichbar", status == 200 and json.loads(body)["status"] == "ok")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
