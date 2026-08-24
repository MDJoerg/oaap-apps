#!/usr/bin/env python3
"""Das Gateway von außen — die Konformitätsliste der Spec, Punkt für Punkt.

Gespielt wird gegen eine **Papier-Bezugsquelle**: ein kleiner Server,
der sich OpenAI-kompatibel verhält und auf Ansage ausfällt. Kein Netz
nach draußen, kein Docker, kein Modell.

Abgedeckt sind die zehn Prüfungen aus `oaap.ai.gateway` §9 — die
wichtigste davon ist Nummer 7: **in keiner Datei steht ein Prompt.**
Der Test schreibt dafür einen unverwechselbaren Satz durch das Gateway
und durchsucht anschließend alles, was auf der Platte gelandet ist.

Run: python3 apps/ai-gateway/test_gateway.py
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


# --------------------------------------------------------- Papier-Bezugsquelle

GEHEIM = "upstream-zugangsdaten-streng-geheim"
PROMPT = "Zitronenfalter-Kennsatz-4711 bitte nicht protokollieren"

class Fake:
    """Zählt Aufrufe, sieht die Kopfzeilen, kann auf Ansage scheitern."""
    calls = []
    fail_times = 0
    support_usage = True


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        Fake.calls.append({"path": self.path, "body": body,
                           "auth": self.headers.get("Authorization", "")})
        if Fake.fail_times > 0:
            Fake.fail_times -= 1
            self.send_response(503)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if body.get("stream_options") and not Fake.support_usage:
            payload = json.dumps({"error": {"message": "stream_options unbekannt"}}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            lines = [b'data: {"choices":[{"delta":{"content":"Hal"}}]}\n\n',
                     b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n']
            if Fake.support_usage and body.get("stream_options"):
                lines.append(b'data: {"usage":{"prompt_tokens":11,"completion_tokens":5}}\n\n')
            lines.append(b"data: [DONE]\n\n")
            for line in lines:
                self.wfile.write(b"%x\r\n" % len(line) + line + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        doc = {"id": "x", "choices": [{"message": {"role": "assistant", "content": "Hallo"}}],
               "usage": {"prompt_tokens": 11, "completion_tokens": 5}}
        payload = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


fake = start(FakeHandler)
FAKE_URL = f"http://127.0.0.1:{fake.server_address[1]}/v1"

# --------------------------------------------------------------- Gateway starten

TMP = tempfile.mkdtemp(prefix="aigw-test-")
os.environ["AIGW_DATA_DIR"] = TMP
os.environ["AIGW_SUPPLIERS"] = (
    f"lokal={FAKE_URL} class=internal\n"
    f"eusrc={FAKE_URL} class=eu\n"
    f"fern={FAKE_URL} class=external")
os.environ["AIGW_SUPPLIER_KEYS"] = f"fern={GEHEIM}"
os.environ["AIGW_ALIASES"] = ("chat-default = fern:fern-modell, eusrc:eu-modell\n"
                              "nur-lokal = lokal:klein\n"
                              "nur-fern = fern:fern-modell")
os.environ["AIGW_TIMEOUT_SECONDS"] = "20"

import app  # noqa: E402  (liest die Konfiguration beim Import)
import store  # noqa: E402

gw = start(app.Handler)
BASE = f"http://127.0.0.1:{gw.server_address[1]}"


def call(path, method="GET", key=None, body=None, admin=False, form=None):
    data, headers = None, {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = form.encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if key:
        headers["Authorization"] = "Bearer " + key
    if admin:
        headers["X-OAAP-User"] = "joerg"
        headers["X-OAAP-Roles"] = "admin,user"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


# ------------------------------------------------------------------- Prüfungen

print("-- Gesundheit und Grundgerüst")
status, body, _ = call("/healthz")
check("Gesundheitspfad ohne Schlüssel", status == 200 and json.loads(body)["status"] == "ok")

print("\n-- §9.1 dieselbe Antwort für fehlend, unbekannt und widerrufen")
a = call("/v1/models")
b = call("/v1/models", key="sk-oaap-erfunden")
key_weg, _ = store.issue(app.DB, "gleich-weg", "test")
store.revoke(app.DB, "gleich-weg", "test")
c = call("/v1/models", key=key_weg)
check("dreimal 403", (a[0], b[0], c[0]) == (403, 403, 403), (a[0], b[0], c[0]))
check("dreimal derselbe Wortlaut", a[1] == b[1] == c[1], (a[1], c[1]))
check("die Antwort verrät nichts über den Grund",
      b"widerrufen" not in a[1] and b"unbekannt" not in a[1], a[1])

print("\n-- Schlüssel ausstellen (Betreiber-Sicht)")
status, body, _ = call("/issue", "POST", admin=True,
                       form="label=laptop&owner=J%C3%B6rg&classes=internal&classes=eu"
                            "&aliases=chat-default%2Cnur-lokal&budget=0&rate=0")
check("Ausstellen antwortet mit einer Seite", status == 200)
text = body.decode("utf-8")
check("der Wert wird genau einmal gezeigt", store.KEY_PREFIX in text)
KEY = text.split(store.KEY_PREFIX)[1].split("<")[0].strip()
KEY = store.KEY_PREFIX + KEY
check("Etikett steht in der Prüfspur",
      any(r["action"] == "key.issue" and r["subject"] == "laptop" for r in store.audit(app.DB)))
status, body, _ = call("/", admin=True)
check("die Seite zeigt den Wert danach nicht mehr", KEY not in body.decode("utf-8"))
check("und auch nicht die Zugangsdaten der Quelle", GEHEIM not in body.decode("utf-8"))

print("\n-- §9.3 /v1/models nennt Aliasse, keine Modelle und keine Quellen")
status, body, _ = call("/v1/models", key=KEY)
doc = json.loads(body)
names = sorted(m["id"] for m in doc["data"])
check("nur die erlaubten Aliasse", names == ["chat-default", "nur-lokal"], names)
check("kein Modellname der Quelle", "fern-modell" not in body.decode())
check("kein Quellenname", "eusrc" not in body.decode() and "fern" not in body.decode())

print("\n-- §9.2 ein nicht erlaubter Alias wird mit der Liste abgelehnt")
status, body, _ = call("/v1/chat/completions", "POST", key=KEY,
                       body={"model": "nur-fern", "messages": []})
check("400 statt stiller Ersetzung", status == 400, status)
check("die Antwort nennt, was erlaubt ist",
      "chat-default" in body.decode() and "nur-lokal" in body.decode(), body)

print("\n-- Der Normalfall, und die Klassen-Ordnung dahinter")
Fake.calls.clear()
status, body, _ = call("/v1/chat/completions", "POST", key=KEY,
                       body={"model": "chat-default",
                             "messages": [{"role": "user", "content": PROMPT}]})
check("200 vom Gateway", status == 200, status)
check("eine Anfrage an die Quelle", len(Fake.calls) == 1, Fake.calls)
check("eu wurde gewählt, obwohl fern zuerst aufgeführt war",
      Fake.calls[0]["body"]["model"] == "eu-modell", Fake.calls[0]["body"]["model"])
check("der Alias wurde durch das Modell der Quelle ersetzt",
      "chat-default" not in json.dumps(Fake.calls[0]["body"]))
rows = store.recent(app.DB, limit=1)
check("die Messzeile nennt die tatsächlich benutzte Quelle",
      rows[0]["supplier"] == "eusrc" and rows[0]["model"] == "eu-modell", dict(rows[0]))
check("Token-Zahlen übernommen", (rows[0]["in_tokens"], rows[0]["out_tokens"]) == (11, 5))

print("\n-- §9.10 Zugangsdaten fließen nur nach oben, nie nach unten")
Fake.calls.clear()
key_fern, _ = store.issue(app.DB, "mit-fern", "test", classes=["external"])
status, body, headers = call("/v1/chat/completions", "POST", key=key_fern,
                             body={"model": "nur-fern", "messages": []})
check("die Quelle sieht ihre eigenen Zugangsdaten",
      Fake.calls[0]["auth"] == "Bearer " + GEHEIM, Fake.calls[0]["auth"])
check("der Verbraucher sieht sie nicht", GEHEIM not in body.decode())
check("und auch in keiner Kopfzeile", GEHEIM not in json.dumps(headers))

print("\n-- Ausweichen nur innerhalb der Gruppe, und es steht in der Messzeile")
# Braucht einen Schlüssel, der beide Klassen der Gruppe darf — sonst gibt
# es gar keine zweite Quelle, zu der ausgewichen werden könnte.
key_alle, _ = store.issue(app.DB, "alle-klassen", "test",
                          classes=["internal", "eu", "external"])
Fake.calls.clear()
Fake.fail_times = 1                      # die erste Quelle fällt aus
status, body, _ = call("/v1/chat/completions", "POST", key=key_alle,
                       body={"model": "chat-default", "messages": []})
check("die Antwort kommt trotzdem", status == 200, status)
check("zwei Versuche", len(Fake.calls) == 2, len(Fake.calls))
rows = store.recent(app.DB, limit=1)
check("die zweite Quelle steht in der Messzeile", rows[0]["supplier"] == "fern", dict(rows[0]))

print("\n-- §9.6 eine verbotene Klasse wird nicht heimlich benutzt")
Fake.calls.clear()
key_eu, _ = store.issue(app.DB, "nur-eu", "test", classes=["eu"])
status, body, _ = call("/v1/chat/completions", "POST", key=key_eu,
                       body={"model": "nur-lokal", "messages": []})
check("503 statt Ausweichen über die Klassengrenze", status == 503, status)
check("keine Anfrage an die Quelle", Fake.calls == [], Fake.calls)
check("die Antwort erklärt, was der Betreiber tun kann",
      "internal" in body.decode() and "eu" in body.decode(), body)

print("\n-- §9.5 Budget: 429, und die Quelle wird nicht angefasst")
Fake.calls.clear()
key_arm, _ = store.issue(app.DB, "knapp", "test", classes=["eu"], budget_tokens=10)
call("/v1/chat/completions", "POST", key=key_arm,
     body={"model": "chat-default", "messages": []})          # verbraucht 16
status, body, headers = call("/v1/chat/completions", "POST", key=key_arm,
                             body={"model": "chat-default", "messages": []})
check("die zweite Anfrage wird abgewiesen", status == 429, status)
check("die Quelle wurde nur einmal gefragt", len(Fake.calls) == 1, len(Fake.calls))
check("kein Retry-After, weil Warten nicht hilft", "Retry-After" not in headers, headers)

print("\n-- Rate-Limit: das ist die andere Art von 429")
key_lang, _ = store.issue(app.DB, "langsam", "test", classes=["eu"], rate_per_min=1)
call("/v1/chat/completions", "POST", key=key_lang, body={"model": "chat-default", "messages": []})
status, body, headers = call("/v1/chat/completions", "POST", key=key_lang,
                             body={"model": "chat-default", "messages": []})
check("zweite Anfrage in derselben Minute: 429", status == 429, status)
check("hier hilft Warten, also steht Retry-After dabei", "Retry-After" in headers, headers)

print("\n-- Strom: durchgereicht, gezählt, nicht gepuffert")
Fake.calls.clear()
status, body, headers = call("/v1/chat/completions", "POST", key=KEY,
                             body={"model": "chat-default", "stream": True,
                                   "messages": [{"role": "user", "content": PROMPT}]})
check("200 mit Ereignisstrom", status == 200 and "event-stream" in headers.get("Content-Type", ""),
      headers)
check("der Inhalt kommt an", b"Hal" in body and b"[DONE]" in body, body[:80])
check("nach Token-Zahlen wurde gefragt", Fake.calls[0]["body"].get("stream_options"), Fake.calls[0]["body"])
rows = store.recent(app.DB, limit=1)
check("Token-Zahlen aus dem letzten Stück", (rows[0]["in_tokens"], rows[0]["out_tokens"]) == (11, 5),
      dict(rows[0]))

print("\n-- Strom bei einer Quelle ohne stream_options: einmal ohne, ehrlich vermerkt")
Fake.support_usage = False
Fake.calls.clear()
status, body, _ = call("/v1/chat/completions", "POST", key=KEY,
                       body={"model": "chat-default", "stream": True, "messages": []})
check("die Antwort kommt trotzdem", status == 200 and b"[DONE]" in body, status)
check("zweiter Versuch ohne stream_options",
      len(Fake.calls) == 2 and "stream_options" not in Fake.calls[1]["body"], Fake.calls)
rows = store.recent(app.DB, limit=1)
check("keine Zahlen geschätzt, sondern vermerkt",
      rows[0]["in_tokens"] is None and rows[0]["outcome"].startswith("ok_no"), dict(rows[0]))
Fake.support_usage = True

print("\n-- §9 Verbrauchssicht: jeder sieht seinen eigenen")
status, body, _ = call("/v1/usage", key=KEY)
mine = json.loads(body)
check("die eigene Summe", mine["key"] == "laptop" and mine["calls"] >= 3, mine["calls"])
check("Account und Mandant stehen dabei (heute default)",
      mine["account"] == "default" and mine["tenant"] == "default")
check("kein fremder Schlüssel in der Antwort", "knapp" not in body.decode())

print("\n-- Betreiber-Sicht bleibt der Serververwaltung vorbehalten")
status, _, _ = call("/", admin=False)
check("ohne Rolle keine Seite", status == 403, status)
status, _, _ = call("/issue", "POST", admin=False, form="label=heimlich")
check("und auch kein Ausstellen", status == 403, status)
check("kein Schlüssel entstanden",
      not any(r["label"] == "heimlich" for r in store.keys(app.DB)))

print("\n-- §9.7 die härteste Regel: nirgends ein Prompt")
found = []
for root, _dirs, files in os.walk(TMP):
    for name in files:
        path = os.path.join(root, name)
        with open(path, "rb") as fh:
            blob = fh.read()
        if PROMPT.encode() in blob:
            found.append(path)
        if GEHEIM.encode() in blob:
            found.append(path + " (Zugangsdaten!)")
check("keine Datei enthält den Prompt oder die Zugangsdaten", found == [], found)
check("und die Tabelle hat gar keine Spalte dafür",
      not any(col in store.SCHEMA for col in ("prompt", "content", "message", "response")))

print("\n-- §9.9 Prüfspur: Ausstellen und Widerrufen, keine Anfragen")
actions = [r["action"] for r in store.audit(app.DB)]
check("Ausstellen steht drin", "key.issue" in actions)
check("Widerrufen steht drin", "key.revoke" in actions)
check("einzelne Anfragen stehen nicht drin",
      not any(a.startswith("request") or a == "call" for a in actions), actions)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
