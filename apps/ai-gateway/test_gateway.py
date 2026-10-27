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
    f"lokal={FAKE_URL} light=green\n"
    f"eusrc={FAKE_URL} light=yellow\n"
    f"fern={FAKE_URL} light=red")
os.environ["AIGW_SUPPLIER_KEYS"] = f"fern={GEHEIM}"
os.environ["AIGW_ALIASES"] = ("chat-default = fern:fern-modell, eusrc:eu-modell\n"
                              "nur-lokal = lokal:klein\n"
                              "nur-fern = fern:fern-modell")
os.environ["AIGW_TIMEOUT_SECONDS"] = "20"
# Mit Hauptschlüssel: Der Lauf spielt den Normalfall nach dem Update auf
# 0.4 — die Konfiguration wird beim Start einmal übernommen, danach gilt
# die Datenbank. Den gesperrten Fall prüft test_catalog.py.
os.environ["AIGW_MASTER_KEY"] = "test-hauptschluessel-0123456789abcdef"
GEHEIM2 = "zweites-geheimnis-aus-dem-formular"

import app  # noqa: E402  (liest die Konfiguration beim Import)
import catalog  # noqa: E402
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
                       form="label=laptop&owner=J%C3%B6rg&ceiling=yellow"
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

print("\n-- §9.6a die Ampel ist die schlechteste erreichbare Farbe")
by_id = {m["id"]: m for m in doc["data"]}
check("nur-lokal ist grün", by_id["nur-lokal"]["oaap_light"] == "green", by_id["nur-lokal"])
check("chat-default ist für diesen Schlüssel gelb (die Obergrenze schneidet rot ab)",
      by_id["chat-default"]["oaap_light"] == "yellow", by_id["chat-default"])
check("die Datenregel steht dabei",
      "personenbezogen" in by_id["chat-default"]["oaap_data_rule"].lower(),
      by_id["chat-default"]["oaap_data_rule"])

key_rot, _ = store.issue(app.DB, "darf-rot", "test", ceiling="red")
_, body_rot, _ = call("/v1/models", key=key_rot)
rot = {m["id"]: m for m in json.loads(body_rot)["data"]}
check("derselbe Alias ist ROT, sobald das Ausweichen nach rot möglich ist",
      rot["chat-default"]["oaap_light"] == "red", rot["chat-default"])
check("und ein rein grüner Alias bleibt grün",
      rot["nur-lokal"]["oaap_light"] == "green", rot["nur-lokal"])

print("\n-- §9.6b Freigabe für personenbezogene Daten heißt: nie rot")
Fake.calls.clear()
key_pbd, _ = store.issue(app.DB, "mit-pbd", "test", ceiling="red", personal_data=True)
status, body, _ = call("/v1/chat/completions", "POST", key=key_pbd,
                       body={"model": "nur-fern", "messages": []})
check("rot wird verweigert, obwohl die Obergrenze rot ist", status == 503, status)
check("die Quelle wurde nicht angefasst", Fake.calls == [], Fake.calls)
check("und die Antwort sagt, warum", "personenbezogen" in body.decode(), body)
_, body_pbd, _ = call("/v1/models", key=key_pbd)
namen = [m["id"] for m in json.loads(body_pbd)["data"]]
check("ein rein roter Alias wird so einem Schlüssel gar nicht erst angeboten",
      "nur-fern" not in namen, namen)
check("die erreichbaren bleiben in der Liste",
      "chat-default" in namen and "nur-lokal" in namen, namen)
status, _, _ = call("/v1/chat/completions", "POST", key=key_pbd,
                    body={"model": "chat-default", "messages": []})
check("gelb geht weiterhin", status == 200, status)

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
key_fern, _ = store.issue(app.DB, "mit-fern", "test", ceiling="red")
status, body, headers = call("/v1/chat/completions", "POST", key=key_fern,
                             body={"model": "nur-fern", "messages": []})
check("die Quelle sieht ihre eigenen Zugangsdaten",
      Fake.calls[0]["auth"] == "Bearer " + GEHEIM, Fake.calls[0]["auth"])
check("der Verbraucher sieht sie nicht", GEHEIM not in body.decode())
check("und auch in keiner Kopfzeile", GEHEIM not in json.dumps(headers))

print("\n-- Ausweichen nur innerhalb der Gruppe, und es steht in der Messzeile")
# Braucht einen Schlüssel, der beide Klassen der Gruppe darf — sonst gibt
# es gar keine zweite Quelle, zu der ausgewichen werden könnte.
key_alle, _ = store.issue(app.DB, "alle-farben", "test", ceiling="red")
Fake.calls.clear()
Fake.fail_times = 1                      # die erste Quelle fällt aus
status, body, _ = call("/v1/chat/completions", "POST", key=key_alle,
                       body={"model": "chat-default", "messages": []})
check("die Antwort kommt trotzdem", status == 200, status)
check("zwei Versuche", len(Fake.calls) == 2, len(Fake.calls))
rows = store.recent(app.DB, limit=1)
check("die zweite Quelle steht in der Messzeile", rows[0]["supplier"] == "fern", dict(rows[0]))

print("\n-- §9.6 eine verbotene Farbe wird nicht heimlich benutzt")
Fake.calls.clear()
key_gruen, _ = store.issue(app.DB, "nur-gruen", "test", ceiling="green")
status, body, _ = call("/v1/chat/completions", "POST", key=key_gruen,
                       body={"model": "chat-default", "messages": []})
check("503 statt Ausweichen über die Ampelgrenze", status == 503, status)
check("keine Anfrage an die Quelle", Fake.calls == [], Fake.calls)
check("die Antwort nennt die Obergrenze", "Obergrenze" in body.decode(), body)

print("\n-- §9.5 Budget: 429, und die Quelle wird nicht angefasst")
Fake.calls.clear()
key_arm, _ = store.issue(app.DB, "knapp", "test", ceiling="yellow", budget_tokens=10)
call("/v1/chat/completions", "POST", key=key_arm,
     body={"model": "chat-default", "messages": []})          # verbraucht 16
status, body, headers = call("/v1/chat/completions", "POST", key=key_arm,
                             body={"model": "chat-default", "messages": []})
check("die zweite Anfrage wird abgewiesen", status == 429, status)
check("die Quelle wurde nur einmal gefragt", len(Fake.calls) == 1, len(Fake.calls))
check("kein Retry-After, weil Warten nicht hilft", "Retry-After" not in headers, headers)

print("\n-- Rate-Limit: das ist die andere Art von 429")
key_lang, _ = store.issue(app.DB, "langsam", "test", ceiling="yellow", rate_per_min=1)
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
check("die Obergrenze steht dabei", mine["ceiling"] == "yellow", mine.get("ceiling"))
check("jede Zeile nennt die Farbe, die tatsächlich geantwortet hat",
      all(r["light"] in ("green", "yellow", "red", "") for r in mine["recent"])
      and any(r["light"] == "yellow" for r in mine["recent"]),
      [r["light"] for r in mine["recent"]])

print("\n-- Betreiber-Sicht bleibt der Serververwaltung vorbehalten")
status, _, _ = call("/", admin=False)
check("ohne Rolle keine Seite", status == 403, status)
status, _, _ = call("/issue", "POST", admin=False, form="label=heimlich")
check("und auch kein Ausstellen", status == 403, status)
check("kein Schlüssel entstanden",
      not any(r["label"] == "heimlich" for r in store.keys(app.DB)))

print("\n-- RFC-0064 Kontext am Schlüssel: ausstellen mit allen Feldern")
status, body, _ = call("/issue", "POST", admin=True,
                       form="label=kurs-app&owner=Lars&account=acme&project=ADT-Kurs"
                            "&cost_center=KS-4711&application=webtop"
                            "&labels=kurs%3Dadt-2026-11%2C+kunde%3Dacme&ceiling=yellow")
text = body.decode("utf-8")
KEY_KURS = store.KEY_PREFIX + text.split(store.KEY_PREFIX)[1].split("<")[0].strip()
kurs = [r for r in store.keys(app.DB) if r["label"] == "kurs-app"][0]
check("alle vier Dimensionen und der Verantwortliche stehen am Schlüssel",
      (kurs["account"], kurs["project"], kurs["cost_center"], kurs["application"], kurs["owner"])
      == ("acme", "ADT-Kurs", "KS-4711", "webtop", "Lars"), dict(kurs))
check("die freien Etiketten sind gespeichert",
      json.loads(kurs["labels"]) == {"kurs": "adt-2026-11", "kunde": "acme"}, kurs["labels"])

print("\n-- RFC-0064 Bytes: genau das, was der Verbraucher schickt und bekommt")
anfrage = {"model": "chat-default", "messages": [{"role": "user", "content": "Bytes zählen"}]}
status, body, _ = call("/v1/chat/completions", "POST", key=KEY_KURS, body=anfrage)
rows = store.recent(app.DB, limit=1)
check("bytes_up = Länge des Anfragerumpfs",
      rows[0]["bytes_up"] == len(json.dumps(anfrage).encode()), (rows[0]["bytes_up"]))
check("bytes_down = Länge der Antwort", rows[0]["bytes_down"] == len(body),
      (rows[0]["bytes_down"], len(body)))
check("die Zeile trägt die Kopie des Kontexts",
      (rows[0]["ctx_cost_center"], rows[0]["ctx_application"], rows[0]["ctx_account"],
       rows[0]["ctx_source"]) == ("KS-4711", "webtop", "acme", "snapshot"), dict(rows[0]))
ZEILE_VORHER = rows[0]["id"]

stromanfrage = dict(anfrage, stream=True)
status, body, _ = call("/v1/chat/completions", "POST", key=KEY_KURS, body=stromanfrage)
rows = store.recent(app.DB, limit=1)
check("im Strom: bytes_down = was ankam, ohne Chunk-Rahmung",
      rows[0]["bytes_down"] == len(body) and len(body) > 0, (rows[0]["bytes_down"], len(body)))
check("im Strom: bytes_up = Anfragerumpf",
      rows[0]["bytes_up"] == len(json.dumps(stromanfrage).encode()), rows[0]["bytes_up"])

print("\n-- RFC-0064 §3 die Kontext-Kopie: eine Änderung verschiebt keine alten Zeilen")
status, body, _ = call("/context", "POST", admin=True,
                       form="label=kurs-app&owner=Lars&account=acme&project=ADT-Kurs"
                            "&cost_center=KS-9000&application=webtop"
                            "&labels=kurs%3Dadt-2026-11%2C+kunde%3Dacme")
check("die Seite bestätigt die Änderung", "geändert" in body.decode("utf-8"), status)
call("/v1/chat/completions", "POST", key=KEY_KURS, body=anfrage)
alt = app.DB.execute("SELECT * FROM usage WHERE id=?", (ZEILE_VORHER,)).fetchone()
neu = store.recent(app.DB, limit=1)[0]
check("die alte Zeile behält KS-4711", alt["ctx_cost_center"] == "KS-4711", alt["ctx_cost_center"])
check("die neue Zeile trägt KS-9000", neu["ctx_cost_center"] == "KS-9000", neu["ctx_cost_center"])
eintrag = [r for r in store.audit(app.DB) if r["action"] == "key.context"]
check("die Prüfspur nennt alten und neuen Wert",
      eintrag and "KS-4711" in eintrag[0]["detail"] and "KS-9000" in eintrag[0]["detail"]
      and eintrag[0]["actor"] == "joerg", [dict(r) for r in eintrag])
check("und nur das geänderte Feld", eintrag and "application" not in eintrag[0]["detail"],
      eintrag[0]["detail"] if eintrag else "")

print("\n-- RFC-0064 Etiketten: höchstens fünf, in der Form name=wert")
zuviel = "%2C".join(f"e{i}%3Dx" for i in range(6))
status, body, _ = call("/context", "POST", admin=True, form=f"label=kurs-app&labels={zuviel}")
check("sechs Etiketten werden abgelehnt, mit Grund", "Höchstens 5" in body.decode("utf-8"))
check("und nichts geändert",
      json.loads([r for r in store.keys(app.DB) if r["label"] == "kurs-app"][0]["labels"])
      == {"kurs": "adt-2026-11", "kunde": "acme"})
try:
    store.parse_labels("ohne-gleichheitszeichen")
    check("ein Etikett ohne = wird abgelehnt", False)
except ValueError:
    check("ein Etikett ohne = wird abgelehnt", True)
status, body, _ = call("/context", "POST", admin=True, form="label=gleich-weg&project=x")
check("ein widerrufener Schlüssel bekommt keinen neuen Kontext",
      "widerrufen" in body.decode("utf-8"))
status, _, _ = call("/context", "POST", admin=False, form="label=kurs-app&project=heimlich")
check("Kontext ändern bleibt der Serververwaltung vorbehalten", status == 403, status)

print("\n-- RFC-0064 auch Ablehnungen tragen Bytes")
key_eng, _ = store.issue(app.DB, "eng", "test", budget_tokens=1)
call("/v1/chat/completions", "POST", key=key_eng, body=anfrage)            # verbraucht 16
status, body, _ = call("/v1/chat/completions", "POST", key=key_eng, body=anfrage)
rows = store.recent(app.DB, limit=1)
check("die Budget-Ablehnung zählt ihre Bytes",
      status == 429 and rows[0]["outcome"] == "budget"
      and rows[0]["bytes_down"] == len(body) and rows[0]["bytes_up"] > 0, dict(rows[0]))

print("\n-- RFC-0064 Kommandozeile: Kontext setzen und ändern")
import io, contextlib  # noqa: E401,E402
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    rc1 = app.cli(["key", "issue", "kopflos", "--cost-center", "KS-1", "--application", "n8n",
                   "--labels", "team=ops"])
    rc2 = app.cli(["key", "context", "kopflos", "--cost-center", "KS-2"])
kopflos = [r for r in store.keys(app.DB) if r["label"] == "kopflos"][0]
check("issue und context über die Kommandozeile",
      (rc1, rc2, kopflos["cost_center"], kopflos["application"], kopflos["labels"])
      == (0, 0, "KS-2", "n8n", '{"team": "ops"}'), (rc1, rc2, dict(kopflos)))

print("\n-- RFC-0064 Verbrauchssicht nennt Bytes")
_, body, _ = call("/v1/usage", key=KEY_KURS)
mine = json.loads(body)
check("Summen in Bytes", mine["bytes_up"] > 0 and mine["bytes_down"] > 0, mine)

print("\n-- RFC-0064 Migration: eine 0.2-Datenbank wird nachgezogen")
import sqlite3  # noqa: E402
ALT = os.path.join(tempfile.mkdtemp(prefix="aigw-alt-"), "gateway.db")
roh = sqlite3.connect(ALT)
roh.executescript("""
CREATE TABLE keys (id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, digest TEXT NOT NULL UNIQUE,
  created TEXT NOT NULL, revoked TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '',
  ceiling TEXT NOT NULL DEFAULT 'yellow', personal_data INTEGER NOT NULL DEFAULT 0,
  budget_tokens INTEGER NOT NULL DEFAULT 0, rate_per_min INTEGER NOT NULL DEFAULT 0,
  owner TEXT NOT NULL DEFAULT '', cost_center TEXT NOT NULL DEFAULT '',
  project TEXT NOT NULL DEFAULT '', account TEXT NOT NULL DEFAULT 'default',
  tenant TEXT NOT NULL DEFAULT 'default');
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL,
  key_id TEXT NOT NULL, alias TEXT NOT NULL, supplier TEXT NOT NULL DEFAULT '',
  model TEXT NOT NULL DEFAULT '', in_tokens INTEGER, out_tokens INTEGER,
  ms INTEGER NOT NULL DEFAULT 0, outcome TEXT NOT NULL DEFAULT 'ok');
INSERT INTO keys (id,label,digest,created,owner,cost_center,project)
  VALUES ('k1','laptop@joerg','d1','2026-08-24T10:00:00Z','Jörg','KS-ALT','privat');
INSERT INTO usage (time,key_id,alias,in_tokens,out_tokens) VALUES
  ('2026-09-01T10:00:00Z','k1','chat-default',100,20),
  ('2026-09-02T10:00:00Z','k1','chat-default',50,10);
""")
roh.commit()
roh.close()
db_alt = store.connect(ALT)
zeilen = list(db_alt.execute("SELECT * FROM usage ORDER BY id"))
check("alte Zeilen bekommen den Kontext ihres Schlüssels",
      all(z["ctx_cost_center"] == "KS-ALT" and z["ctx_project"] == "privat" for z in zeilen),
      [dict(z) for z in zeilen])
check("und sagen, dass er nachgetragen ist",
      all(z["ctx_source"] == "backfilled" for z in zeilen))
check("ihre Bytes bleiben unbekannt (NULL), nicht null",
      all(z["bytes_up"] is None and z["bytes_down"] is None for z in zeilen))
check("die Token-Zahlen sind unberührt", [z["in_tokens"] for z in zeilen] == [100, 50])
check("die Prüfspur hält die Nachtragung fest",
      [r["subject"] for r in store.audit(db_alt) if r["action"] == "usage.backfill"] == ["2 Zeilen"])
db_alt.close()
db_alt = store.connect(ALT)
check("ein zweiter Start trägt nichts noch einmal nach",
      len([r for r in store.audit(db_alt) if r["action"] == "usage.backfill"]) == 1)
db_alt.close()
shutil.rmtree(os.path.dirname(ALT), ignore_errors=True)

print("\n-- RFC-0064 Stufe 2: die Konfiguration wurde einmal übernommen")
check("die App pflegt selbst", (app.MODE, app.IMPORT_STATE) == ("app", "imported"),
      (app.MODE, app.IMPORT_STATE))
rows = {r["name"]: r for r in catalog.supplier_rows(app.DB)}
check("drei Quellen und drei Modellnamen in der Datenbank",
      sorted(rows) == ["eusrc", "fern", "lokal"] and len(catalog.alias_rows(app.DB)) == 3,
      (sorted(rows), len(catalog.alias_rows(app.DB))))
check("die Zugangsdaten liegen verschlüsselt, nicht im Klartext",
      rows["fern"]["credential"].startswith("v1:") and GEHEIM not in rows["fern"]["credential"],
      rows["fern"]["credential"][:12])
check("die Übernahme steht in der Prüfspur",
      any(r["action"] == "config.import" for r in store.audit(app.DB)))
_, body, _ = call("/", admin=True)
check("die Seite sagt, dass die Variablen nicht mehr gelesen werden",
      "Aus der Konfiguration übernommen" in body.decode("utf-8"))

print("\n-- RFC-0064 Stufe 2: Quelle und Modellname im Formular, wirksam ohne Neustart")
status, body, _ = call("/supplier", "POST", admin=True,
                       form=f"name=neu2&url={FAKE_URL}&light=green&credential={GEHEIM2}")
check("Quelle angelegt", "„neu2“ angelegt" in body.decode("utf-8"), body.decode("utf-8")[:0])
check("die Seite zeigt die neuen Zugangsdaten nicht", GEHEIM2 not in body.decode("utf-8"))
status, body, _ = call("/alias", "POST", admin=True,
                       form="name=eigener-name&targets=neu2%3Aklein-2")
check("Modellname angelegt", "„eigener-name“ angelegt" in body.decode("utf-8"))
key_neu, _ = store.issue(app.DB, "neu-nutzer", "test", aliases=["eigener-name"])
_, body, _ = call("/v1/models", key=key_neu)
check("der neue Name steht sofort in /v1/models",
      [m["id"] for m in json.loads(body)["data"]] == ["eigener-name"], body)
Fake.calls.clear()
status, body, _ = call("/v1/chat/completions", "POST", key=key_neu,
                       body={"model": "eigener-name", "messages": []})
check("und antwortet über die neue Quelle", status == 200 and
      Fake.calls[-1]["body"]["model"] == "klein-2", (status, Fake.calls[-1:]))
check("mit den im Formular gesetzten Zugangsdaten",
      Fake.calls[-1]["auth"] == "Bearer " + GEHEIM2, Fake.calls[-1]["auth"])

print("\n-- RFC-0064 Stufe 2: leeres Feld heißt unverändert, Entfernen ist ein Häkchen")
call("/supplier", "POST", admin=True, form=f"name=neu2&url={FAKE_URL}&light=green&credential=")
Fake.calls.clear()
call("/v1/chat/completions", "POST", key=key_neu, body={"model": "eigener-name", "messages": []})
check("ohne neue Eingabe bleiben die Zugangsdaten", Fake.calls[-1]["auth"] == "Bearer " + GEHEIM2,
      Fake.calls[-1]["auth"])
call("/supplier", "POST", admin=True,
     form=f"name=neu2&url={FAKE_URL}&light=green&drop_credential=1")
Fake.calls.clear()
call("/v1/chat/completions", "POST", key=key_neu, body={"model": "eigener-name", "messages": []})
check("mit Häkchen sind sie weg", Fake.calls[-1]["auth"] == "", Fake.calls[-1]["auth"])
call("/supplier", "POST", admin=True,
     form=f"name=neu2&url={FAKE_URL}&light=green&credential={GEHEIM2}")
eintraege = [dict(r) for r in store.audit(app.DB) if r["action"] == "supplier.set"]
check("die Prüfspur sagt, was geschah, nie den Wert",
      [e["detail"].split("zugangsdaten=")[1] for e in eintraege[:4]]
      == ["neu gesetzt", "entfernt", "unverändert", "neu gesetzt"]
      and not any(GEHEIM2 in e["detail"] for e in eintraege), eintraege[:4])

print("\n-- RFC-0064 Stufe 2: was abgelehnt wird")
status, body, _ = call("/supplier-delete", "POST", admin=True, form="name=neu2")
check("eine benutzte Quelle lässt sich nicht löschen, mit Namen des Nutzers",
      "eigener-name" in body.decode("utf-8") and "neu2" in {r["name"] for r in catalog.supplier_rows(app.DB)})
status, body, _ = call("/alias", "POST", admin=True, form="name=kaputt&targets=gibtsnicht%3Ax")
check("ein Modellname mit unbekannter Quelle wird nicht gespeichert",
      "nicht konfiguriert" in body.decode("utf-8")
      and "kaputt" not in {r["name"] for r in catalog.alias_rows(app.DB)})
status, body, _ = call("/supplier", "POST", admin=True, form="name=mit+leerzeichen&url=https%3A%2F%2Fx")
check("ein Name mit Leerzeichen wird abgelehnt", "höchstens 40 Zeichen" in body.decode("utf-8"))
status, body, _ = call("/supplier", "POST", admin=True, form="name=ftp&url=ftp%3A%2F%2Fx&light=green")
check("eine Adresse ohne http(s) wird abgelehnt", "http://" in body.decode("utf-8")
      and "ftp" not in {r["name"] for r in catalog.supplier_rows(app.DB)})
status, _, _ = call("/supplier", "POST", admin=False, form="name=heimlich&url=https%3A%2F%2Fx")
check("Quellen pflegen bleibt der Serververwaltung vorbehalten",
      status == 403 and "heimlich" not in {r["name"] for r in catalog.supplier_rows(app.DB)})

print("\n-- RFC-0064 Stufe 2: Löschen eines Modellnamens nennt die betroffenen Schlüssel")
status, body, _ = call("/alias-delete", "POST", admin=True, form="name=eigener-name")
check("die Seite nennt den beschränkten Schlüssel", "neu-nutzer" in body.decode("utf-8"))
status, body, _ = call("/v1/chat/completions", "POST", key=key_neu,
                       body={"model": "eigener-name", "messages": []})
check("und der Name ist sofort unbekannt", status == 400, status)
status, body, _ = call("/supplier-delete", "POST", admin=True, form="name=neu2")
check("jetzt ist die Quelle frei und gelöscht",
      "neu2" not in {r["name"] for r in catalog.supplier_rows(app.DB)}, body.decode("utf-8")[:0])

print("\n-- §9.7 die härteste Regel: nirgends ein Prompt")
found = []
for root, _dirs, files in os.walk(TMP):
    for name in files:
        path = os.path.join(root, name)
        with open(path, "rb") as fh:
            blob = fh.read()
        if PROMPT.encode() in blob:
            found.append(path)
        if GEHEIM.encode() in blob or GEHEIM2.encode() in blob:
            found.append(path + " (Zugangsdaten!)")
check("keine Datei enthält den Prompt oder die Zugangsdaten", found == [], found)
check("und die Tabelle hat gar keine Spalte dafür — auch keine nachgezogene",
      not any(col in store.SCHEMA or any(col in c for _t, c, _d in store.ADDED_COLUMNS)
              for col in ("prompt", "content", "message", "response")))

print("\n-- §9.9 Prüfspur: Ausstellen und Widerrufen, keine Anfragen")
actions = [r["action"] for r in store.audit(app.DB)]
check("Ausstellen steht drin", "key.issue" in actions)
check("Widerrufen steht drin", "key.revoke" in actions)
check("einzelne Anfragen stehen nicht drin",
      not any(a.startswith("request") or a == "call" for a in actions), actions)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
