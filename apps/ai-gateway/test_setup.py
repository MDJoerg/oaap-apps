#!/usr/bin/env python3
"""Einrichtungstür und Freigabe für einen Chat (RFC-0104 Stufe 2, §3.1, §3.3, §4).

Ein eigener Start, weil die Tür einen Einrichtungsschlüssel in der
Umgebung braucht und die Bücher leer beginnen sollen. Gespielt wird
gegen eine Papier-Bezugsquelle, die ihre Modellliste nur mit den
richtigen Zugangsdaten herausgibt — so lässt sich `ready` messen.

Die Köder aus §8: 2 (nur der eigene Schlüssel öffnet die Tür),
3 (nie überschreiben, was ein Mensch gemacht hat), 5 (X-OAAP-AI-Client
allein öffnet nichts), 6 (ein Chat ohne Freigabe bekommt dieselbe 403
wie ein unbekannter Schlüssel), 7 (die Obergrenze der Freigabe hält).

Run: py -3 apps/ai-gateway/test_setup.py
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
from urllib.parse import quote

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


RICHTIG = "zugang-der-bezugsquelle-richtig-77"
PERSON = "uuid-person-0f0f-erkennbar-4242"


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
        if self.headers.get("Authorization") != "Bearer " + RICHTIG:
            self.answer(401, {"error": {"message": "nein"}})
            return
        self.answer(200, {"object": "list", "data": [{"id": "modell-a"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.answer(200, {"id": "x", "choices": [{"message": {"role": "assistant",
                                                               "content": "Hallo"}}],
                          "usage": {"prompt_tokens": 7, "completion_tokens": 3}})


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


fake = start(FakeHandler)
FAKE_URL = f"http://127.0.0.1:{fake.server_address[1]}/v1"

TMP = tempfile.mkdtemp(prefix="aigw-setup-")
os.environ["AIGW_DATA_DIR"] = TMP
for name in ("AIGW_SUPPLIERS", "AIGW_SUPPLIER_KEYS", "AIGW_ALIASES"):
    os.environ.pop(name, None)
os.environ["AIGW_MASTER_KEY"] = "test-hauptschluessel-0123456789abcdef"
SETUP_KEY = "einrichtungsschluessel-dieser-instanz-A1"
FREMD_KEY = "einrichtungsschluessel-einer-anderen-B2"
os.environ["OAAP_SETUP_KEY"] = SETUP_KEY

import app  # noqa: E402
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


def door(body=None, key=SETUP_KEY, query="", extra=None, raw=None):
    headers = {"X-OAAP-Setup-Actor": "anna"}
    if key:
        headers["Authorization"] = "Bearer " + key
    headers.update(extra or {})
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    if data is not None:
        headers["Content-Type"] = "application/json"
    status, payload = http("/oaap/setup" + query, "POST" if data is not None else "GET",
                           data, headers)
    try:
        return status, json.loads(payload)
    except ValueError:
        return status, payload


def results(doc):
    return [r["result"] for r in doc["results"]]


def admin(path, form):
    return http(path, "POST", form.encode(),
                {"Content-Type": "application/x-www-form-urlencoded",
                 "X-OAAP-User": "joerg", "X-OAAP-Roles": "admin,user"})


def via(path, client="kit-chat", body=None, uid=PERSON, assertion="zusicherung-xyz",
        key=None, light=None):
    # Die Köder zählen als Fehlversuche derselben Adresse (FAIL_LIMIT); jede
    # Prüfung soll für sich stehen, nicht an der Bremse gegen Raten scheitern.
    with app._FAILS_LOCK:
        app._FAILS.clear()
    headers = {}
    if client:
        headers["X-OAAP-AI-Client"] = client
    if uid:
        headers["X-OAAP-User-Id"] = uid
    if assertion:
        headers["X-OAAP-User-Assertion"] = assertion
    if key:
        headers["Authorization"] = "Bearer " + key
    if light:
        headers["X-OAAP-Light"] = light
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
    return http(path, "POST" if body is not None else "GET", data, headers)


def last_usage_id():
    rows = store.recent(app.DB, limit=1)
    return rows[0]["id"] if rows else 0


def wait_new_usage(before, timeout=5.0):
    """Die Messzeile erscheint nach der Antwort — auf die NEUE Zeile warten, nicht die letzte lesen."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        rows = store.recent(app.DB, limit=1)
        if rows and rows[0]["id"] > before:
            return rows[0]
        time.sleep(0.05)
    return None


def disk_bytes():
    blob = b""
    for root, _dirs, files in os.walk(TMP):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                blob += fh.read()
    return blob


SUPPLIER = {"kind": "supplier", "id": "anbieter", "url": FAKE_URL, "light": "yellow"}
ALIAS = {"kind": "alias", "id": "standard", "supplier": "anbieter", "model": "modell-a"}
CLIENT = {"kind": "client", "id": "kit-chat", "aliases": ["standard"], "ceiling": "yellow",
          "personal_data": False}

# ------------------------------------------------------------------- Prüfungen

print("-- Grundgerüst")
status, body = http("/healthz")
check("Version 0.10.0", json.loads(body)["version"] == "0.10.0", body)

print("\n-- §8 Köder 2: nur der eigene Einrichtungsschlüssel öffnet die Tür")
status, _ = door(key=None)
check("GET ohne Schlüssel: 403", status == 403, status)
status, _ = door({"v": 1, "items": [SUPPLIER]}, key=None,
                 extra={"X-OAAP-User": "chef", "X-OAAP-Roles": "admin,tenant_admin,user"})
check("POST mit Identität einer Mandantenverwaltung, ohne Schlüssel: 403", status == 403, status)
status, _ = door({"v": 1, "items": [SUPPLIER]}, key=FREMD_KEY)
check("POST mit dem Schlüssel einer anderen Instanz: 403", status == 403, status)
check("... und nichts angelegt", not app.DB.execute("SELECT 1 FROM suppliers").fetchone())
status, _ = http("/oaap/setup", headers={"X-OAAP-User": "chef", "X-OAAP-Roles": "admin"})
check("admin über die Route `/`, ohne Schlüssel: 403", status == 403, status)
os.environ.pop("OAAP_SETUP_KEY")
status, _ = door()
check("ohne OAAP_SETUP_KEY in der Umgebung verweigert die Tür jeden Aufruf", status == 403, status)
status, _ = door(key="")
check("... auch mit leerem Bearer", status == 403, status)
os.environ["OAAP_SETUP_KEY"] = SETUP_KEY
status, doc = door()
check("mit Schlüssel: GET nennt die Arten",
      status == 200 and doc == {"v": 1, "kinds": ["supplier", "alias", "client"]}, doc)

print("\n-- Form des Rumpfs (400)")
for label, raw in (("kein JSON", b"{nein"),
                   ("ohne v", json.dumps({"items": []}).encode()),
                   ("items und remove zugleich", json.dumps({"v": 1, "items": [], "remove": []}).encode()),
                   ("Eintrag ohne id", json.dumps({"v": 1, "items": [{"kind": "alias"}]}).encode()),
                   ("dry kein Wahrheitswert", json.dumps({"v": 1, "dry": "ja", "items": []}).encode())):
    status, _ = door(raw=raw)
    check(f"{label}: 400", status == 400, status)
status, _ = door(query="?kind=unbekannt&id=x")
check("GET mit unbekannter Art: 400", status == 400, status)

print("\n-- Keine Geheimnisse")
GEHEIM = "sk-anbieter-darf-nie-durch-die-tuer-99"
for field in ("key", "token", "secret", "password", "credentials", "credential"):
    status, doc = door({"v": 1, "items": [dict(SUPPLIER, **{field: GEHEIM})]})
    check(f"Feld „{field}“: refused", status == 200 and results(doc) == ["refused"], doc)
check("nichts angelegt", not app.DB.execute("SELECT 1 FROM suppliers").fetchone())
check("der Wert steht nirgends auf der Platte", GEHEIM.encode() not in disk_bytes())
status, doc = door({"v": 1, "items": [dict(SUPPLIER, farbe="blau")]})
check("unbekanntes Feld: refused, mit Grund", results(doc) == ["refused"]
      and "farbe" in doc["results"][0]["note"], doc)

print("\n-- Art supplier: dry, made, present; ready erst mit Zugangsdaten und Antwort")
status, doc = door({"v": 1, "dry": True, "items": [SUPPLIER]})
check("dry: would_make", results(doc) == ["would_make"], doc)
check("dry legt nichts an", not app.DB.execute("SELECT 1 FROM suppliers").fetchone())
status, doc = door({"v": 1, "dry": False, "items": [SUPPLIER]})
check("echt: made", status == 200 and results(doc) == ["made"], doc)
check("Ergebnis trägt kind, id, result, note",
      set(doc["results"][0]) == {"kind", "id", "result", "note"}, doc)
status, doc = door({"v": 1, "items": [SUPPLIER]})
check("dasselbe noch einmal: present (idempotent)", results(doc) == ["present"], doc)
status, doc = door({"v": 1, "dry": True, "items": [SUPPLIER]})
check("die Prüfung (dry) sagt present", results(doc) == ["present"], doc)
check("das Gateway routet die neue Quelle sofort", "anbieter" in app.SUPPLIERS)
mem = app.DB.execute("SELECT * FROM setup_items WHERE kind='supplier' AND id='anbieter'").fetchone()
check("gemerkt: wer, wann, Digest", mem and mem["actor"] == "setup:anna" and mem["time"]
      and len(mem["digest"]) == 64, dict(mem) if mem else None)
spur = [(r["actor"], r["action"]) for r in store.audit(app.DB, 50)]
check("Prüfspur: setup.make und supplier.set mit dem Auslöser",
      ("setup:anna", "setup.make") in spur and ("setup:anna", "supplier.set") in spur, spur[:6])
status, doc = door(query="?kind=supplier&id=anbieter")
check("GET: present, nicht ready, Note sagt Zugangsdaten fehlen",
      doc["state"] == "present" and doc["ready"] is False and "Zugangsdaten fehlen" in doc["note"],
      doc)
admin("/supplier", f"name=anbieter&url={quote(FAKE_URL, safe='')}&light=yellow&credential=falsch")
status, doc = door(query="?kind=supplier&id=anbieter")
check("falsche Zugangsdaten: nicht ready, der Test hat abgelehnt (401)",
      doc["ready"] is False and "401" in doc["note"], doc)
admin("/supplier", f"name=anbieter&url={quote(FAKE_URL, safe='')}&light=yellow&credential={RICHTIG}")
status, doc = door(query="?kind=supplier&id=anbieter")
check("richtige Zugangsdaten und Antwort: ready", doc["ready"] is True and "200" in doc["note"], doc)
check("die Antwort nennt die Zugangsdaten nie", RICHTIG not in json.dumps(doc))
status, doc = door({"v": 1, "items": [SUPPLIER]})
check("Zugangsdaten durch einen Menschen sind kein Konflikt: present", results(doc) == ["present"], doc)
status, doc = door({"v": 1, "items": [dict(SUPPLIER, url="https://anders.example/v1")]})
check("andere Adresse: conflict, die Note nennt sie",
      results(doc) == ["conflict"] and "Adresse" in doc["results"][0]["note"], doc)
row = app.DB.execute("SELECT url FROM suppliers WHERE name='anbieter'").fetchone()
check("... und die Quelle bleibt, wie sie war", row["url"] == FAKE_URL, row["url"])
status, doc = door(query="?kind=supplier&id=gibtsnicht")
check("GET eines fehlenden Eintrags: absent, nicht ready",
      doc["state"] == "absent" and doc["ready"] is False, doc)

print("\n-- Arten alias und client in einem Schritt (wie im Profil, §6 Schritt 7)")
status, doc = door({"v": 1, "dry": True, "items": [ALIAS, CLIENT]})
check("dry: would_make für beide (die Freigabe sieht den geplanten Modellnamen)",
      results(doc) == ["would_make", "would_make"], doc)
check("dry legt nichts an", not app.DB.execute("SELECT 1 FROM aliases").fetchone()
      and not store.grant_of(app.DB, "kit-chat"))
status, doc = door({"v": 1, "items": [ALIAS, CLIENT]})
check("echt: made, made", results(doc) == ["made", "made"], doc)
status, doc = door({"v": 1, "items": [ALIAS, CLIENT]})
check("noch einmal: present, present", results(doc) == ["present", "present"], doc)
for kind, ident in (("alias", "standard"), ("client", "kit-chat")):
    status, doc = door(query=f"?kind={kind}&id={ident}")
    check(f"GET {kind}: present und ready", doc["state"] == "present" and doc["ready"] is True, doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "x", "supplier": "fehlt",
                                       "model": "m"}]})
check("Modellname auf eine unbekannte Quelle: refused", results(doc) == ["refused"], doc)
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "c2", "aliases": ["fehlt"]}]})
check("Freigabe mit unbekanntem Modellnamen: refused", results(doc) == ["refused"], doc)
store.issue(app.DB, "laptop", "test")
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "laptop", "aliases": []}]})
check("Freigabe auf das Etikett eines API-Schlüssels: conflict", results(doc) == ["conflict"], doc)
status, doc = door({"v": 1, "items": [dict(CLIENT, ceiling="red")]})
check("Freigabe mit anderer Obergrenze: conflict", results(doc) == ["conflict"]
      and "Obergrenze" in doc["results"][0]["note"], doc)

print("\n-- §8 Köder 3: nie überschreiben, was ein Mensch gemacht hat")
door({"v": 1, "items": [{"kind": "alias", "id": "hand", "supplier": "anbieter",
                         "model": "modell-a"}]})
admin("/alias", "name=hand&targets=anbieter%3Amodell-b")
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "hand", "supplier": "anbieter",
                                       "model": "modell-a"}]})
check("von Hand geänderter Modellname, Aufbau läuft weiter: conflict",
      results(doc) == ["conflict"] and "modell-b" in doc["results"][0]["note"], doc)
row = app.DB.execute("SELECT targets FROM aliases WHERE name='hand'").fetchone()
check("... und er bleibt, wie der Mensch ihn wollte", row["targets"] == "anbieter:modell-b",
      row["targets"])
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "hand"}]})
check("remove eines von Hand geänderten Eintrags: conflict, er bleibt",
      results(doc) == ["conflict"] and app.DB.execute(
          "SELECT 1 FROM aliases WHERE name='hand'").fetchone(), doc)
admin("/alias", "name=vonhand&targets=anbieter%3Amodell-a")
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "vonhand"}]})
check("remove eines Eintrags, den die Einrichtung nie gemacht hat: conflict",
      results(doc) == ["conflict"] and "Nicht von der Einrichtung" in doc["results"][0]["note"],
      doc)
status, doc = door({"v": 1, "items": [{"kind": "alias", "id": "vonhand", "supplier": "anbieter",
                                       "model": "modell-a"}]})
check("ein gleicher Eintrag von Hand ist present (nicht übernommen)",
      results(doc) == ["present"] and not app.DB.execute(
          "SELECT 1 FROM setup_items WHERE id='vonhand'").fetchone(), doc)

print("\n-- Eine Freigabe hat keinen Wert, der passt")
g = store.grant_of(app.DB, "kit-chat")
check("Zeile der Art client, Digest ohne SHA-256", g["kind"] == "client"
      and not all(c in "0123456789abcdef" for c in g["digest"]), g["digest"])
check("store.find: leerer Wert findet nichts", store.find(app.DB, "") is None)
check("store.find: der gespeicherte Digest als Wert findet nichts",
      store.find(app.DB, g["digest"]) is None)
check("store.find: auch nicht das Etikett", store.find(app.DB, "kit-chat") is None)
app.DB.execute("UPDATE keys SET digest=? WHERE id=?", (store.digest("trick-wert"), g["id"]))
check("store.find: selbst mit passendem Digest nie eine Freigabe",
      store.find(app.DB, "trick-wert") is None)
app.DB.execute("UPDATE keys SET digest=? WHERE id=?", (g["digest"], g["id"]))
app.DB.commit()
status, _ = http("/v1/models", headers={"Authorization": "Bearer " + g["digest"]})
check("der Digest als Bearer auf /v1: 403", status == 403, status)

print("\n-- §4: ein Aufruf durch die via-Tür, berechtigt durch die Freigabe")
before = last_usage_id()
status, body = via("/v1/chat/completions", body={"model": "standard", "messages": []})
check("alle drei Kopfzeilen, kein Schlüssel: 200", status == 200, (status, body[:200]))
row = wait_new_usage(before)
check("Messzeile mit der key_id der Freigabe", row is not None and row["key_id"] == g["id"],
      dict(row) if row else None)
check("§9 O1: die Person steht nirgends auf der Platte", PERSON.encode() not in disk_bytes())
status, body = via("/v1/models")
check("/v1/models über die Tür: genau die Modellnamen der Freigabe",
      status == 200 and [m["id"] for m in json.loads(body)["data"]] == ["standard"], body[:200])
status, body = via("/v1/usage")
check("/v1/usage über die Tür: die Zahlen der Freigabe",
      status == 200 and json.loads(body)["key"] == "kit-chat" and json.loads(body)["calls"] >= 1,
      body[:200])
status, body = via("/v1/chat/completions", body={"model": "hand", "messages": []})
check("ein Modellname außerhalb der Freigabe: 400 unknown_model",
      status == 400 and b"unknown_model" in body, (status, body[:200]))

print("\n-- §8 Köder 5: X-OAAP-AI-Client allein öffnet nichts")
st0, unbekannt = http("/v1/models", headers={"Authorization": "Bearer sk-oaap-erfunden"})
check("Vergleichsantwort: 403 für einen unbekannten Schlüssel", st0 == 403, st0)
status, body = via("/v1/models", uid=None, assertion=None)
check("nur X-OAAP-AI-Client, kein Schlüssel: 403, dieselbe Antwort wie unbekannt",
      status == 403 and body == unbekannt, (status, body))
status, body = via("/v1/chat/completions", uid=None, assertion=None,
                   body={"model": "standard", "messages": []})
check("... auch für eine Anfrage", status == 403 and body == unbekannt, status)
status, body = via("/v1/models", assertion=None)
check("zwei von drei (ohne Zusicherung): 403", status == 403 and body == unbekannt, status)
status, body = via("/v1/models", uid=None)
check("zwei von drei (ohne Person): 403", status == 403 and body == unbekannt, status)

print("\n-- §8 Köder 6: ein Chat ohne Freigabe bekommt nichts")
status, body = via("/v1/models", client="kit-chat2")
check("zweiter Chat, alle drei Kopfzeilen, keine Freigabe: 403 wie unbekannt",
      status == 403 and body == unbekannt, (status, body))
status, body = via("/v1/chat/completions", client="laptop",
                   body={"model": "standard", "messages": []})
check("das Etikett eines API-Schlüssels als Chat-Name: 403 wie unbekannt",
      status == 403 and body == unbekannt, (status, body))

print("\n-- §8 Köder 7: die Obergrenze der Freigabe hält wie die eines Schlüssels")
door({"v": 1, "items": [
    {"kind": "supplier", "id": "fernquelle", "url": FAKE_URL, "light": "red"},
    {"kind": "alias", "id": "nur-rot", "supplier": "fernquelle", "model": "modell-a"},
    {"kind": "client", "id": "kit-chat3", "aliases": ["nur-rot", "standard"],
     "ceiling": "yellow"}]})
status, body = via("/v1/chat/completions", client="kit-chat3",
                   body={"model": "nur-rot", "messages": []})
check("rot verlangt von einer gelben Freigabe: abgelehnt (503 light_not_permitted)",
      status == 503 and b"light_not_permitted" in body, (status, body[:200]))
status, body = via("/v1/chat/completions", client="kit-chat3", light="red",
                   body={"model": "nur-rot", "messages": []})
check("X-OAAP-Light: red lockert die Decke der Freigabe nicht",
      status == 503 and b"light_not_permitted" in body, (status, body[:200]))
status, body = via("/v1/models", client="kit-chat3")
check("/v1/models bietet den roten Namen nicht an",
      [m["id"] for m in json.loads(body)["data"]] == ["standard"], body[:200])
status, body = via("/v1/chat/completions", client="kit-chat3", light="green",
                   body={"model": "standard", "messages": []})
check("ein grünes Gespräch sperrt den gelben Weg auch für die Freigabe (RFC-0101)",
      status == 503 and b"conversation_light" in body, (status, body[:200]))

print("\n-- Budget und Rate wie bei einem Schlüssel")
g3 = store.grant_of(app.DB, "kit-chat3")
app.DB.execute("UPDATE keys SET budget_tokens=5 WHERE id=?", (g3["id"],))
app.DB.commit()
via("/v1/chat/completions", client="kit-chat3", body={"model": "standard", "messages": []})
status, body = via("/v1/chat/completions", client="kit-chat3",
                   body={"model": "standard", "messages": []})
check("Budget erschöpft: 429 budget_exhausted", status == 429 and b"budget_exhausted" in body,
      (status, body[:200]))
app.DB.execute("UPDATE keys SET budget_tokens=0, rate_per_min=1 WHERE id=?", (g3["id"],))
app.DB.commit()
app.LIMITER._seen.pop(g3["id"], None)
via("/v1/chat/completions", client="kit-chat3", body={"model": "standard", "messages": []})
status, body = via("/v1/chat/completions", client="kit-chat3",
                   body={"model": "standard", "messages": []})
check("Rate 1 je Minute: die zweite Anfrage 429 rate_limit",
      status == 429 and b"rate_limit" in body, (status, body[:200]))

print("\n-- Reiter Schlüssel: Freigaben sichtbar und widerrufbar")
status, body = http("/schluessel", headers={"X-OAAP-User": "joerg", "X-OAAP-Roles": "admin"})
text = body.decode("utf-8")
check("die Freigabe steht mit ihrer Art auf der Seite",
      "kit-chat" in text and "Freigabe für Chat" in text, text[:200])
check("für eine Freigabe gibt es kein „neu ausstellen“",
      "neu_wie=kit-chat" not in text)
status, body = admin("/revoke", "label=kit-chat3")
check("widerrufen über die Seite", "widerrufen" in body.decode("utf-8"))
status, body = via("/v1/models", client="kit-chat3")
check("widerrufene Freigabe: 403 wie unbekannt", status == 403 and body == unbekannt, status)
status, doc = door(query="?kind=client&id=kit-chat3")
check("GET: present, nicht ready, widerrufen", doc["state"] == "present"
      and doc["ready"] is False and "widerrufen" in doc["note"], doc)
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat3",
                                       "aliases": ["nur-rot", "standard"], "ceiling": "yellow"}]})
check("eine von Hand widerrufene Freigabe stellt die Einrichtung nicht wieder her: conflict",
      results(doc) == ["conflict"], doc)

print("\n-- remove (die Rücknahme eines Aufbaus)")
door({"v": 1, "items": [{"kind": "supplier", "id": "leer", "url": FAKE_URL, "light": "green"},
                        {"kind": "client", "id": "kit-chat4", "aliases": ["standard"]}]})
status, doc = door({"v": 1, "remove": [{"kind": "client", "id": "kit-chat4"},
                                       {"kind": "supplier", "id": "leer"},
                                       {"kind": "alias", "id": "gibtsnicht"}]})
check("unveränderte Einträge: removed, removed; ein fehlender: gone",
      results(doc) == ["removed", "removed", "gone"], doc)
check("Freigabe ohne Verbrauch ist gelöscht, Quelle auch",
      store.grant_of(app.DB, "kit-chat4") is None
      and not app.DB.execute("SELECT 1 FROM suppliers WHERE name='leer'").fetchone()
      and "leer" not in app.SUPPLIERS)
status, doc = door({"v": 1, "remove": [{"kind": "client", "id": "kit-chat"}]})
check("Freigabe mit Verbrauch: removed, aber widerrufen statt gelöscht",
      results(doc) == ["removed"] and "widerrufen" in doc["results"][0]["note"]
      and store.grant_of(app.DB, "kit-chat")["revoked"], doc)
status, body = via("/v1/models")
check("... und sie öffnet nichts mehr", status == 403 and body == unbekannt, status)
status, doc = door({"v": 1, "remove": [{"kind": "client", "id": "kit-chat"}]})
check("noch einmal: gone", results(doc) == ["gone"], doc)
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "nur-rot"}]})
check("Modellname, auf den nur eine widerrufene Freigabe zeigt: removed",
      results(doc) == ["removed"], doc)
status, doc = door({"v": 1, "remove": [{"kind": "supplier", "id": "anbieter"}]})
check("Quelle mit Zugangsdaten eines Menschen: conflict, bleibt",
      results(doc) == ["conflict"] and app.DB.execute(
          "SELECT 1 FROM suppliers WHERE name='anbieter'").fetchone(), doc)
door({"v": 1, "items": [{"kind": "client", "id": "kit-chat5", "aliases": ["standard"]}]})
status, doc = door({"v": 1, "remove": [{"kind": "alias", "id": "standard"}]})
check("Modellname, auf den eine gültige Freigabe beschränkt ist: conflict",
      results(doc) == ["conflict"] and "kit-chat5" in doc["results"][0]["note"], doc)
spur = [(r["actor"], r["action"]) for r in store.audit(app.DB, 200)]
check("Prüfspur: setup.remove mit dem Auslöser", ("setup:anna", "setup.remove") in spur)

print("\n-- 0.10.1: der Mandant der Freigabe steht in jeder Messzeile")
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat8",
                                       "aliases": ["standard"]}]})
check("ohne tenant bleibt die Freigabe `default` wie vor 0.10.1",
      results(doc) == ["made"] and app.DB.execute(
          "SELECT tenant FROM keys WHERE label='kit-chat8'").fetchone()["tenant"] == "default",
      doc)
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat8",
                                       "aliases": ["standard"]}]})
check("dieselbe Freigabe ohne tenant noch einmal: present", results(doc) == ["present"], doc)
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat6",
                                       "aliases": ["standard"], "tenant": "kit1"}]})
check("Freigabe mit tenant: made", results(doc) == ["made"], doc)
g6 = app.DB.execute("SELECT * FROM keys WHERE label='kit-chat6'").fetchone()
check("tenant steht an der Freigabe", g6 is not None and g6["tenant"] == "kit1",
      dict(g6) if g6 else None)
before = last_usage_id()
status, _body = via("/v1/chat/completions", client="kit-chat6",
                    body={"model": "standard", "messages": []})
row = wait_new_usage(before)
check("Messzeile über die Freigabe trägt ctx_tenant kit1",
      status == 200 and row is not None and row["ctx_tenant"] == "kit1",
      (status, dict(row) if row else None))
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat6",
                                       "aliases": ["standard"], "tenant": "kit1"}]})
check("gleicher Wunsch noch einmal: present", results(doc) == ["present"], doc)
status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat6",
                                       "aliases": ["standard"], "tenant": "anders"}]})
check("anderer Mandant: conflict mit Feldnamen",
      results(doc) == ["conflict"] and "Mandant" in doc["results"][0]["note"], doc)
for bad in ("", "x" * 500, 7):
    status, doc = door({"v": 1, "items": [{"kind": "client", "id": "kit-chat7",
                                           "aliases": ["standard"], "tenant": bad}]})
    check(f"tenant {str(bad)[:8]!r}: refused", results(doc) == ["refused"], doc)
status, doc = door({"v": 1, "remove": [{"kind": "client", "id": "kit-chat6"}]})
check("eine Freigabe mit Mandant lässt sich zurückbauen", results(doc) == ["removed"], doc)

print("\n-- Identitäts-Kopfzeilen zählen auf der Tür nicht")
status, doc = door({"v": 1, "items": [SUPPLIER]},
                   extra={"X-OAAP-User": "eva", "X-OAAP-Roles": "user"})
check("mit Schlüssel und einer Person ohne admin: die Tür antwortet trotzdem",
      status == 200 and results(doc) == ["present"], doc)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
