#!/usr/bin/env python3
"""Eigenschaften der Modelle (RFC-0106 Stufe 1, Spec `oaap.ai.gateway` §3.1).

Gespielt wird gegen eine Papier-Bezugsquelle, deren Modellliste für jedes
Modell etwas anderes sagt (Kontext als `context_length`, als
`context_window`, Fähigkeiten als Liste, als Objekt, gar nichts) und die
auf eine Anfrage mit Werkzeug je nach Modell einen Werkzeugaufruf, eine
Textantwort oder eine 400 gibt.

Run: py -3 apps/ai-gateway/test_props.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

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


ZUGANG = "zugang-der-papierquelle-55"
LISTE = [
    {"id": "gross", "context_length": 128000, "max_completion_tokens": 8192,
     "supported_parameters": ["tools", "temperature"],
     "architecture": {"input_modalities": ["text", "image"]}},
    {"id": "klein", "context_window": 32768, "capabilities": {"function_calling": False}},
    {"id": "stumm"},
    {"id": "redner"},
    {"id": "verweigerer"},
]
SEEN_REQUESTS = []


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def answer(self, status, doc):
        payload = json.dumps(doc).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + ZUGANG:
            self.answer(401, {"error": {"message": "nein"}})
            return
        self.answer(200, {"object": "list", "data": LISTE})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        SEEN_REQUESTS.append(body)
        model = body.get("model")
        if body.get("tools") and model == "verweigerer":
            self.answer(400, {"error": {"message": "tools is not supported for this model"}})
            return
        if body.get("tools") and model in ("gross", "stumm"):
            msg = {"role": "assistant", "content": None,
                   "tool_calls": [{"id": "c1", "type": "function",
                                   "function": {"name": "now", "arguments": "{}"}}]}
        else:
            msg = {"role": "assistant", "content": "Es ist spät."}
        self.answer(200, {"id": "x", "choices": [{"message": msg}],
                          "usage": {"prompt_tokens": 9, "completion_tokens": 4}})


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


fake = start(FakeHandler)
FAKE_URL = f"http://127.0.0.1:{fake.server_address[1]}/v1"

TMP = tempfile.mkdtemp(prefix="aigw-props-")
os.environ["AIGW_DATA_DIR"] = TMP
for name in ("AIGW_SUPPLIERS", "AIGW_SUPPLIER_KEYS", "AIGW_ALIASES"):
    os.environ.pop(name, None)
os.environ["AIGW_MASTER_KEY"] = "test-hauptschluessel-0123456789abcdef"
SETUP_KEY = "einrichtungsschluessel-props-A1"
os.environ["OAAP_SETUP_KEY"] = SETUP_KEY

import app  # noqa: E402
import catalog  # noqa: E402
import props  # noqa: E402
import store  # noqa: E402

gw = start(app.Handler)
BASE = f"http://127.0.0.1:{gw.server_address[1]}"


def http(path, method="GET", data=None, headers=None):
    req = urllib.request.Request(BASE + path, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "admin,user"}


def admin_post(path, form):
    return http(path, "POST", urlencode(form).encode(),
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))


def door(body):
    status, payload = http("/oaap/setup", "POST", json.dumps(body).encode(),
                           {"Authorization": "Bearer " + SETUP_KEY, "X-OAAP-Setup-Actor": "anna",
                            "Content-Type": "application/json"})
    return status, json.loads(payload)


def models(key):
    status, payload = http("/v1/models", headers={"Authorization": "Bearer " + key})
    return status, {m["id"]: m for m in json.loads(payload)["data"]}


def cell(supplier, model, prop):
    return props.table(app.DB).get((supplier, model), {}).get(prop, {})


print("-- Werte und Regeln (ohne HTTP)")
check("norm: 128k -> 128000", props.norm("context", "128k") == "128000")
check("norm: 131_072 -> 131072", props.norm("context", "131_072") == "131072")
check("norm: leer = unbekannt", props.norm("tools", "") == "" and props.norm("output", None) == "")
check("norm: ja/nein", props.norm("tools", "ja") == "true" and props.norm("vision", False) == "false")
for bad_prop, bad in (("context", "viel"), ("context", "0"), ("context", True),
                      ("tools", "vielleicht"), ("output", "-5"), ("nonsense", "1")):
    try:
        props.norm(bad_prop, bad)
        check(f"norm {bad_prop}={bad!r} abgelehnt", False)
    except ValueError:
        check(f"norm {bad_prop}={bad!r} abgelehnt", True)
check("schwächstes: nein entscheidet", props.weakest("tools", ["true", "false", ""]) == "false")
check("schwächstes: unbekannt macht unbekannt", props.weakest("tools", ["true", ""]) == "")
check("schwächstes: alle ja", props.weakest("vision", ["true", "true"]) == "true")
check("schwächstes: kleinste Zahl", props.weakest("context", ["128000", "32768"]) == "32768")
check("schwächstes: Zahl mit einem unbekannten -> unbekannt",
      props.weakest("context", ["128000", ""]) == "")
check("short: 128k, 128Ki, ?, ✓, –",
      [props.short("context", "128000"), props.short("context", "131072"),
       props.short("output", ""), props.short("tools", "true"), props.short("vision", "false")]
      == ["128k", "128Ki", "?", "✓", "–"])

print("\n-- Aus der Modellliste nur, was dasteht")
fl = props.from_listing
check("context_length + max_completion_tokens + tools + image",
      fl(LISTE[0]) == {"context": "128000", "output": "8192", "tools": "true", "vision": "true"},
      fl(LISTE[0]))
check("context_window + ausdrücklich keine Werkzeuge",
      fl(LISTE[1]) == {"context": "32768", "tools": "false"}, fl(LISTE[1]))
check("nichts gesagt -> nichts", fl({"id": "x"}) == {})
check("Fähigkeitenliste ohne tools heißt NICHT nein",
      "tools" not in fl({"id": "x", "capabilities": ["completion"]}))
check("Ollama-Liste capabilities [tools, vision]",
      fl({"name": "q", "capabilities": ["completion", "tools", "vision"]})
      == {"tools": "true", "vision": "true"})
check("LM Studio type vlm + max_context_length",
      fl({"id": "v", "type": "vlm", "max_context_length": 4096}) == {"vision": "true",
                                                                     "context": "4096"})
check("Unsinn wird übergangen (Text, negativ, bool)",
      fl({"id": "x", "context_length": "viel", "max_completion_tokens": -1,
          "context_window": True}) == {})

print("\n-- Quelle und Modellnamen anlegen, Modellliste abrufen")
catalog.set_supplier(app.DB, "joerg", app.MASTER, "papier", FAKE_URL, "green", credential=ZUGANG)
for alias, target in (("chat", "papier:gross"), ("mini", "papier:klein"),
                      ("leer", "papier:stumm"), ("gruppe", "papier:gross, papier:klein"),
                      ("redner", "papier:redner"), ("nein", "papier:verweigerer")):
    catalog.set_alias(app.DB, "joerg", alias, target, False)
app.reload_supply()
r = app.probe_supplier("papier")
check("Verbindungstest: erreichbar, fünf Modelle", r["status"] == 200 and len(r["ids"]) == 5, r)
check("gross: vier Angaben von der Quelle",
      {p: cell("papier", "gross", p).get("value") for p in props.PROPS}
      == {"tools": "true", "vision": "true", "context": "128000", "output": "8192"}
      and cell("papier", "gross", "context")["origin"] == "supplier")
check("klein: Kontext 32768 und Werkzeuge nein",
      cell("papier", "klein", "context").get("value") == "32768"
      and cell("papier", "klein", "tools").get("value") == "false")
check("stumm: nichts bekannt", props.table(app.DB).get(("papier", "stumm")) is None)
rows_before = app.DB.execute("SELECT COUNT(*) FROM model_props").fetchone()[0]
updated_before = app.DB.execute("SELECT MAX(updated) FROM model_props").fetchone()[0]
app.probe_supplier("papier")
check("zweiter Abruf schreibt nichts neu (gleiche Meldung)",
      app.DB.execute("SELECT COUNT(*), MAX(updated) FROM model_props").fetchone()[:]
      == (rows_before, updated_before))

print("\n-- Für einen Modellnamen: das schwächste Ziel")
tab = props.table(app.DB)
check("gruppe (gross + klein): Werkzeuge nein, Kontext 32768, Bilder/Ausgabe unbekannt",
      props.of_alias(app.ALIASES["gruppe"], tab)
      == {"tools": "false", "vision": "", "context": "32768", "output": ""},
      props.of_alias(app.ALIASES["gruppe"], tab))

print("\n-- /v1/models: nur gesetzte oaap_*-Felder")
value, _row = store.issue(app.DB, "probe-schluessel", "joerg"), None
key = value if isinstance(value, str) else value[0]
status, by = models(key)
check("200 und alle sechs Modellnamen", status == 200 and set(by) >= {"chat", "mini", "leer"}, by)
check("chat: oaap_tools true, oaap_vision true, oaap_context 128000, oaap_output 8192",
      (by["chat"].get("oaap_tools"), by["chat"].get("oaap_vision"),
       by["chat"].get("oaap_context"), by["chat"].get("oaap_output")) == (True, True, 128000, 8192),
      by["chat"])
check("leer: kein oaap_tools/vision/context/output",
      not any(k in by["leer"] for k in ("oaap_tools", "oaap_vision", "oaap_context", "oaap_output")),
      by["leer"])
check("die Ampel steht weiter daneben", by["chat"].get("oaap_light") == "green")

print("\n-- Der Betreiber setzt; ein Abruf überschreibt ihn nicht")
status, page = admin_post("/model-props", {"supplier": "papier", "model": "gross",
                                           "tools": "true", "vision": "true",
                                           "context": "100k", "output": "8192"})
check("Speichern: 200 und Satz", status == 200 and "gespeichert".encode() in page, page[-300:])
check("Kontext 100000 vom Betreiber",
      cell("papier", "gross", "context") .get("value") == "100000"
      and cell("papier", "gross", "context")["origin"] == "operator")
app.probe_supplier("papier")
c = cell("papier", "gross", "context")
check("nach dem Abruf: Wert bleibt 100000, Meldung 128000 daneben",
      c["value"] == "100000" and c["origin"] == "operator" and c["seen"] == "128000"
      and c["seen_origin"] == "supplier", c)
status, page = http("/modelle", headers=ADMIN)
text = page.decode("utf-8")
check("Seite zeigt die Meldung mit „übernehmen“", "übernehmen" in text and "128k" in text)
check("Seite zeigt die Spalte Eigenschaften", "<th>Eigenschaften</th>" in text)
status, page = admin_post("/model-props-take", {"supplier": "papier", "model": "gross",
                                                "prop": "context"})
c = cell("papier", "gross", "context")
check("übernehmen: 128000 von der Quelle", c["value"] == "128000" and c["origin"] == "supplier", c)
status, page = admin_post("/model-props", {"supplier": "papier", "model": "gross",
                                           "tools": "", "vision": "true",
                                           "context": "128000", "output": "8192"})
check("leer gesetzt: Werkzeuge wieder unbekannt", cell("papier", "gross", "tools") == {})
status, page = admin_post("/model-props", {"supplier": "papier", "model": "gross",
                                           "tools": "true", "vision": "true",
                                           "context": "zu viel", "output": "8192"})
check("ungültiger Wert: Satz, nichts geschrieben",
      "Kontext" in page.decode("utf-8") and cell("papier", "gross", "tools") == {})
status, page = admin_post("/model-props", {"supplier": "papier", "model": "fremd",
                                           "tools": "true"})
check("kein Ziel eines Modellnamens: abgelehnt", "kein Ziel".encode() in page)
status, page = http("/model-props", "POST", b"supplier=papier&model=gross&tools=true",
                    {"Content-Type": "application/x-www-form-urlencoded",
                     "X-OAAP-User": "eva", "X-OAAP-Roles": "user"})
check("ohne admin: kein Speichern", status in (403, 401) and cell("papier", "gross", "tools") == {},
      status)
audit = [r for r in app.DB.execute("SELECT * FROM audit WHERE action='model.props'")]
check("Prüfspur: model.props mit Betreiber", audit and all(r["actor"] == "joerg" for r in audit))

print("\n-- Werkzeugaufruf probieren")
SEEN_REQUESTS.clear()
status, page = admin_post("/alias-tooltest", {"name": "leer"})
check("stumm ruft das Werkzeug: ja (aus dem Test)",
      cell("papier", "stumm", "tools") .get("value") == "true"
      and cell("papier", "stumm", "tools")["origin"] == "test", cell("papier", "stumm", "tools"))
req = SEEN_REQUESTS[-1]
check("die Anfrage bot genau das harmlose Werkzeug now an",
      [t["function"]["name"] for t in req["tools"]] == ["now"] and req["max_tokens"] <= 60, req)
admin_post("/alias-tooltest", {"name": "redner"})
check("redner antwortet mit Text: nein (aus dem Test)",
      cell("papier", "redner", "tools").get("value") == "false")
admin_post("/alias-tooltest", {"name": "nein"})
check("verweigerer lehnt tools mit 400 ab: nein",
      cell("papier", "verweigerer", "tools").get("value") == "false")
admin_post("/model-props", {"supplier": "papier", "model": "redner", "tools": "true"})
admin_post("/alias-tooltest", {"name": "redner"})
c = cell("papier", "redner", "tools")
check("Test nach Betreiberwert: Wert bleibt, Meldung daneben",
      c["value"] == "true" and c["origin"] == "operator" and c["seen"] == "false"
      and c["seen_origin"] == "test", c)
used = app.DB.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
check("der Test zählt nicht in den Verbrauch", used == 0, used)

print("\n-- Einrichtungstür: alias mit Eigenschaften")
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil", "supplier": "papier",
                                       "model": "stumm", "context": 65536, "vision": False}]})
check("made", [r["result"] for r in doc["results"]] == ["made"], doc)
check("stumm: Kontext 65536 und Bilder nein vom Betreiber",
      cell("papier", "stumm", "context").get("value") == "65536"
      and cell("papier", "stumm", "vision").get("value") == "false"
      and cell("papier", "stumm", "context")["origin"] == "operator")
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil", "supplier": "papier",
                                       "model": "stumm", "context": 65536, "vision": False}]})
check("noch einmal: present", [r["result"] for r in doc["results"]] == ["present"], doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil", "supplier": "papier",
                                       "model": "stumm", "context": 4096}]})
check("anderer Wert als der des Betreibers: conflict, bleibt",
      [r["result"] for r in doc["results"]] == ["conflict"]
      and cell("papier", "stumm", "context")["value"] == "65536", doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil2", "supplier": "papier",
                                       "model": "stumm", "context": "65536"}]})
check("Kontext als Text: refused", [r["result"] for r in doc["results"]] == ["refused"], doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil2", "supplier": "papier",
                                       "model": "stumm", "tools": "ja"}]})
check("tools als Text: refused", [r["result"] for r in doc["results"]] == ["refused"], doc)
before = props.table(app.DB)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "profil3", "supplier": "papier",
                                       "model": "redner", "tools": False}], "dry": True})
check("dry: would_make, nichts gesetzt",
      [r["result"] for r in doc["results"]] == ["would_make"] and props.table(app.DB) == before,
      doc)
check("geleerter Wert: die Meldung der Quelle gilt beim nächsten Lernen wieder",
      cell("papier", "gross", "tools").get("origin") == "supplier")
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "profil"}]})
check("Rückbau eines Modellnamens mit Eigenschaften: removed (Eigenschaften zählen nicht zum Digest)",
      [r["result"] for r in doc["results"]] == ["removed"], doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "alt", "supplier": "papier",
                                       "model": "klein"}]})
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "alt"}]})
check("ein Modellname ohne Eigenschaften wie vor 0.11: made und removed",
      [r["result"] for r in doc["results"]] == ["removed"], doc)

print("\n-- Asset-Tür trägt die Eigenschaften")
ans = app.asset_answer()
chat = next(a for a in ans["assets"] if a["kind"] == "model" and a["name"] == "chat")
check("chat in /oaap/assets mit vision und context",
      chat.get("vision") is True and chat.get("context") == 128000, chat)

print("\n-- Ollama: /v1/models nennt nur Kennungen, /api/show mehr (gemessen 20.11.)")
fos = props.from_ollama_show
check("capabilities tools+vision, num_ctx gesetzt",
      fos({"capabilities": ["completion", "tools", "vision"], "parameters": "num_ctx 8192\nstop x",
           "model_info": {"qwen3.context_length": 262144}})
      == {"tools": "true", "vision": "true", "context": "8192"})
check("ohne num_ctx kein Kontext (model_info.context_length ist nicht, was Ollama bedient)",
      fos({"capabilities": ["completion", "tools"],
           "model_info": {"qwen2.context_length": 32768}}) == {"tools": "true"})
check("Unsinn: {}", fos("kein Objekt") == {} and fos({"parameters": "num_ctx viel"}) == {})

SHOWN = []


class OllamaHandler(FakeHandler):
    def do_GET(self):
        self.answer(200, {"object": "list", "data": [
            {"id": "qwen-klein", "object": "model", "owned_by": "library"},
            {"id": "qwen-gross", "object": "model", "owned_by": "library"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        if self.path == "/api/show":
            SHOWN.append(body.get("model"))
            if body.get("model") == "qwen-gross":
                self.answer(200, {"capabilities": ["completion", "tools", "vision"],
                                  "parameters": "num_ctx 16384",
                                  "model_info": {"qwen3.context_length": 262144}})
            else:
                self.answer(200, {"capabilities": ["completion"],
                                  "model_info": {"qwen2.context_length": 32768}})
            return
        self.answer(404, {"error": "nein"})


ollama = start(OllamaHandler)
catalog.set_supplier(app.DB, "joerg", app.MASTER, "lokal",
                     f"http://127.0.0.1:{ollama.server_address[1]}/v1", "green")
catalog.set_alias(app.DB, "joerg", "lokal-gross", "lokal:qwen-gross", False)
catalog.set_alias(app.DB, "joerg", "lokal-klein", "lokal:qwen-klein", False)
app.reload_supply()
app.probe_supplier("lokal")
check("/api/show für beide Ziele gefragt", sorted(SHOWN) == ["qwen-gross", "qwen-klein"], SHOWN)
check("qwen-gross: Werkzeuge, Bilder, Kontext 16384 von der Quelle",
      {p: cell("lokal", "qwen-gross", p).get("value") for p in ("tools", "vision", "context")}
      == {"tools": "true", "vision": "true", "context": "16384"}
      and cell("lokal", "qwen-gross", "context")["origin"] == "supplier")
check("qwen-klein: nichts gesetzt (completion allein sagt nichts, kein num_ctx)",
      props.table(app.DB).get(("lokal", "qwen-klein")) is None)
SHOWN.clear()
app.probe_supplier("lokal")
check("zweiter Test fragt qwen-gross nicht erneut (steht schon in den Büchern)",
      SHOWN == ["qwen-klein"], SHOWN)

print("\n-- Stufe 2: Vorlagen sind Daten und werden beim Start geprüft")
import templates  # noqa: E402
check("VS Code und opencode geladen", set(app.TEMPLATES) == {"vscode", "opencode"})
for bad_tpl, why in (({"target": "x"}, "Pflichtfelder"),
                     (dict(app.TEMPLATES["vscode"], document={"m": "{models} und mehr"}),
                      "{models} nicht als ganzer Wert"),
                     (dict(app.TEMPLATES["vscode"], model={"t": "x{tools}"}),
                      "{tools} nicht als ganzer Wert"),
                     (dict(app.TEMPLATES["vscode"], document={"a": 1}), "{models} fehlt")):
    try:
        templates._check(bad_tpl, "probe")
        check(f"fehlerhafte Vorlage abgelehnt ({why})", False)
    except templates.TemplateError:
        check(f"fehlerhafte Vorlage abgelehnt ({why})", True)

print("\n-- Reiter Vorlagen: aus einem Schlüssel genau die Liste von /v1/models")
schmal_value = store.issue(app.DB, "nur-chat", "joerg", aliases=("chat", "leer"))
schmal = schmal_value if isinstance(schmal_value, str) else schmal_value[0]
status, page = http("/vorlagen?ziel=vscode&quelle=schluessel&schluessel=nur-chat", headers=ADMIN)
text = page.decode("utf-8")
check("Seite 200 mit Reiter Vorlagen", status == 200 and ">Vorlagen<" in text)
status, raw = http("/vorlagen?ziel=vscode&quelle=schluessel&schluessel=nur-chat&laden=1",
                   headers=ADMIN)
doc = json.loads(raw)
ids = [m["id"] for m in doc[0]["models"]]
_s, by_schmal = models(schmal)
check("VS Code: genau die Modellnamen des Schlüssels", ids == sorted(by_schmal),
      (ids, sorted(by_schmal)))
chat = next(m for m in doc[0]["models"] if m["id"] == "chat")
check("VS Code: chat mit vision, contextWindow, maxOutputTokens, url bis /chat/completions",
      chat.get("vision") is True and chat.get("contextWindow") == 128000
      and chat.get("maxOutputTokens") == 8192 and chat["url"].endswith("/v1/chat/completions"),
      chat)
check("VS Code: Platzhalter statt Schlüssel", doc[0]["apiKey"] == "${input:oaapAiKey}")
leer = next(m for m in doc[0]["models"] if m["id"] == "leer")
check("VS Code: unbekannte Ausgabegröße fehlt ganz (nichts geraten)",
      "maxOutputTokens" not in leer and leer.get("contextWindow") == 65536, leer)
status, raw = http("/vorlagen?ziel=opencode&quelle=hand&m=chat&m=mini&laden=1", headers=ADMIN)
doc = json.loads(raw)
oc = doc["providers"]["oaap"]
check("opencode: von Hand gewählt, zwei Modelle", sorted(oc["models"]) == ["chat", "mini"])
check("opencode: Bilder als input-Liste, limit.context",
      oc["models"]["chat"]["capabilities"]["input"] == ["text", "image"]
      and oc["models"]["chat"]["limit"]["context"] == 128000, oc["models"]["chat"])
check("opencode: mini ohne Werkzeuge (ausdrücklich nein von der Quelle)",
      oc["models"]["mini"]["capabilities"]["tools"] is False, oc["models"]["mini"])
check("opencode: Schlüssel aus der Umgebung", oc["env"] == ["OAAP_AI_KEY"])
status, page = http("/vorlagen?ziel=vscode&quelle=schluessel&schluessel=gibtsnicht",
                    headers=ADMIN)
check("unbekannter Schlüssel: nichts erzeugt, Satz", "Nichts zu erzeugen".encode() in page)
status, page = http("/vorlagen?ziel=vscode", headers={"X-OAAP-User": "eva", "X-OAAP-Roles": "user"})
check("Reiter ohne admin: 403", status == 403, status)

print("\n-- Selbstbedienung mit dem eigenen Schlüssel")
status, raw = http("/v1/oaap/client-config?target=opencode",
                   headers={"Authorization": "Bearer " + schmal})
check("200 mit Datei", status == 200 and json.loads(raw)["providers"]["oaap"]["models"], raw[:200])
check("genau die Modellnamen dieses Schlüssels",
      sorted(json.loads(raw)["providers"]["oaap"]["models"]) == sorted(by_schmal))
status, raw = http("/v1/oaap/client-config?target=nix",
                   headers={"Authorization": "Bearer " + schmal})
check("unbekanntes Werkzeug: 400 mit Liste", status == 400 and b"vscode" in raw, raw)
status, raw = http("/v1/oaap/client-config?target=vscode")
check("ohne Schlüssel: abgelehnt wie /v1/models (403)", status == 403, status)
status, raw = http("/v1/oaap/client-config?target=vscode",
                   headers={"Authorization": "Bearer sk-oaap-falsch"})
check("falscher Schlüssel: abgelehnt (403)", status == 403, status)

print("\n-- D9: kein Schlüssel und keine Zugangsdaten in irgendeiner Ausgabe")
outputs = []
for tgt in ("vscode", "opencode"):
    outputs.append(http(f"/v1/oaap/client-config?target={tgt}",
                        headers={"Authorization": "Bearer " + schmal})[1])
    outputs.append(http(f"/v1/oaap/client-config?target={tgt}",
                        headers={"Authorization": "Bearer " + key})[1])
    outputs.append(http(f"/vorlagen?ziel={tgt}&quelle=schluessel&schluessel=nur-chat&laden=1",
                        headers=ADMIN)[1])
    outputs.append(http(f"/vorlagen?ziel={tgt}&quelle=schluessel&schluessel=nur-chat",
                        headers=ADMIN)[1])
blob = b"\n".join(outputs)
check("weder dieser noch der andere Schlüssel, nirgends",
      schmal.encode() not in blob and key.encode() not in blob)
check("kein sk-oaap- und keine Zugangsdaten der Quelle",
      b"sk-oaap-" not in blob and ZUGANG.encode() not in blob)
req = urllib.request.Request(BASE + "/v1/oaap/client-config?target=vscode",
                             headers={"Authorization": "Bearer " + schmal})
with urllib.request.urlopen(req, timeout=30) as resp:
    check("Datei mit no-store und als Anhang",
          resp.headers.get("Cache-Control") == "no-store"
          and "chatLanguageModels.json" in (resp.headers.get("Content-Disposition") or ""))

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
