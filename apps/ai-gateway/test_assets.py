"""Die Tür /oaap/assets (0.7.0, Manifest 0.15, RFC-0087 §8.2).

Gespielt wird gegen eine Papier-Quelle hinter einer „Destination“
(OAAP_DESTINATION_GAMING_URL, wie die Plattform sie setzt), eine Quelle,
die nie antwortet, und einen „fremden Anbieter“, der für die Flotte nie
angefragt werden darf. Geprüft wird:

* was die Tür nennt -- und was nie (Adressen, Modellkennungen, Schlüssel);
* der Zustand aus echten Versuchen, auch vor dem Ausweichen;
* die Prüfung je Minute fasst nur Quellen hinter einer Destination an;
* `idle` nach 15 Minuten ohne Versuch, die Regel für Modellnamen;
* der Knoten liest die Antwort mit seinem eigenen Leser genauso
  (oaap-reference, falls daneben ausgecheckt).

Run: python3 apps/ai-gateway/test_assets.py
"""
import json
import os
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


FERN_KEY = "fremder-anbieter-schluessel-nie-zeigen"


class Fake:
    calls = []


class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, doc):
        payload = json.dumps(doc).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        Fake.calls.append(("GET", self.path, self.headers.get("Authorization", "")))
        self._send({"object": "list", "data": [{"id": "klein"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        Fake.calls.append(("POST", self.path, self.headers.get("Authorization", "")))
        self._send({"choices": [{"message": {"content": "hallo"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1}})


def start(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


fake = start(FakeHandler)
FAKE_BASE = f"http://127.0.0.1:{fake.server_address[1]}"
DEAD = "http://127.0.0.1:1/v1"           # niemand hört hier

TMP = tempfile.mkdtemp(prefix="aigw-assets-")
os.environ["AIGW_DATA_DIR"] = TMP
# Wie die Plattform eine gebundene Destination übergibt (manifest any: true)
os.environ["OAAP_DESTINATION_GAMING_URL"] = FAKE_BASE + "/destinations/gaming/"
os.environ["AIGW_SUPPLIERS"] = (
    f"gaming={FAKE_BASE}/destinations/gaming/v1 light=green\n"
    f"tot={DEAD} light=green\n"
    f"fern={FAKE_BASE}/fern/v1 light=red")
os.environ["AIGW_SUPPLIER_KEYS"] = f"fern={FERN_KEY}"
os.environ["AIGW_ALIASES"] = ("chat-gaming = gaming:klein\n"
                              "chat-mix = tot:klein, gaming:klein order=listed\n"
                              "nur-tot = tot:klein")
os.environ["AIGW_MASTER_KEY"] = "test-hauptschluessel-0123456789abcdef"

import app  # noqa: E402
import assets  # noqa: E402
import store  # noqa: E402

gw = start(app.Handler)
BASE = f"http://127.0.0.1:{gw.server_address[1]}"


def get(path):
    try:
        with urllib.request.urlopen(BASE + path, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def post(path, key, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def door():
    status, body = get("/oaap/assets")
    return status, body, json.loads(body)


def by(doc):
    return {(a["kind"], a["name"]): a for a in doc["assets"]}


print("-- die Tür nach dem Start")
status, body, doc = door()
a = by(doc)
check("200 und v 1", status == 200 and doc.get("v") == 1, (status, doc))
check("drei Quellen, drei Modellnamen",
      sorted(a) == [("model", "chat-gaming"), ("model", "chat-mix"), ("model", "nur-tot"),
                    ("source", "fern"), ("source", "gaming"), ("source", "tot")], sorted(a))
check("die Quelle hinter der Destination nennt sie",
      a[("source", "gaming")].get("destination") == "gaming", a[("source", "gaming")])
check("die anderen nennen keine",
      "destination" not in a[("source", "tot")] and "destination" not in a[("source", "fern")])
check("chat-mix nutzt tot und gaming", a[("model", "chat-mix")]["uses"] == ["gaming", "tot"],
      a[("model", "chat-mix")])
check("nach dem Start ist alles idle",
      all(x["state"] == "idle" for x in doc["assets"]), doc)
text = body.decode()
check("keine Adresse, kein Port, kein Schlüssel, keine Modellkennung",
      "127.0.0.1" not in text and "http" not in text and FERN_KEY not in text
      and "klein" not in text, text)
check("nur die erlaubten Felder",
      all(set(x) <= {"kind", "name", "state", "uses", "destination"} for x in doc["assets"]))

print("\n-- Zustand aus echten Versuchen (auch vor dem Ausweichen)")
key, _ = store.issue(app.DB, "assets-test", "test", aliases=("chat-mix", "nur-tot"),
                     ceiling="green")
Fake.calls.clear()
status, _b = post("/v1/chat/completions", key, {"model": "chat-mix", "messages": []})
check("chat-mix antwortet über das Ausweichen", status == 200, status)
_s, _b, doc = door()
a = by(doc)
check("tot ist down (der Versuch vor dem Ausweichen zählt)",
      a[("source", "tot")]["state"] == "down", a[("source", "tot")])
check("gaming ist ok", a[("source", "gaming")]["state"] == "ok", a[("source", "gaming")])
check("chat-mix ist ok (eine Quelle reicht)", a[("model", "chat-mix")]["state"] == "ok")
check("nur-tot ist down (alle Quellen down)", a[("model", "nur-tot")]["state"] == "down")
check("fern bleibt idle", a[("source", "fern")]["state"] == "idle")

print("\n-- die Prüfung je Minute fasst nur Quellen hinter einer Destination an")
Fake.calls.clear()
dests = app.bound_destinations()
names = sorted(n for n, src in app.SUPPLIERS.items() if app.destination_of(src["url"], dests))
check("nur gaming wird geprüft", names == ["gaming"], names)
app.probe_all(names)
check("ein GET …/models an gaming", [c[:2] for c in Fake.calls]
      == [("GET", "/destinations/gaming/v1/models")], Fake.calls)
check("der fremde Anbieter wurde nicht angefragt",
      not any(FERN_KEY in c[2] or "/fern/" in c[1] for c in Fake.calls), Fake.calls)

print("\n-- Seen, Modellregel, Vergessen (rein)")
s = assets.Seen()
s.note("q", True, at=1000)
check("frisch: ok", s.state("q", now=1000 + 60) == "ok")
check("nach 15 Minuten: idle", s.state("q", now=1000 + assets.RECENT + 1) == "idle")
s.note("q", False, at=2000)
check("nicht erreicht: down", s.state("q", now=2001) == "down")
check("unbekannt: idle", s.state("x", now=2001) == "idle")
s.forget({"y"})
check("vergessen, was es nicht mehr gibt", s.state("q", now=2001) == "idle")
check("reached: 200/401/404 ja, 0/502/503 nein",
      [assets.reached(x) for x in (200, 401, 404, 0, 502, 503, None)]
      == [True, True, True, False, False, False, False])
check("Modell: ok vor down vor idle",
      (assets.model_state(["down", "ok"]), assets.model_state(["down", "down"]),
       assets.model_state(["down", "idle"]), assets.model_state([]))
      == ("ok", "down", "idle", "idle"))
many = {f"q{i}": {"url": "x"} for i in range(250)}
check("höchstens 200 Assets", len(assets.answer(many, {}, assets.Seen(), {})["assets"]) == 200)

print("\n-- der Leser des Knotens liest dieselbe Antwort")
ref = os.path.normpath(os.path.join(HERE, "..", "..", "..", "oaap-reference", "platform",
                                    "services", "portal"))
if os.path.exists(os.path.join(ref, "fleet_links.py")):
    sys.path.insert(0, ref)
    import fleet_links  # noqa: E402
    got = fleet_links.read_assets_answer(doc)
    check("read_assets_answer nimmt alle sechs", got is not None and len(got) == 6, got)
    g = {(x["kind"], x["name"]): x for x in got or []}
    check("mit uses, destination und Zuständen",
          g[("model", "chat-mix")].get("uses") == ["gaming", "tot"]
          and g[("source", "gaming")].get("destination") == "gaming"
          and g[("source", "tot")].get("state") == "down", g)
else:
    print("  (oaap-reference nicht daneben -- übersprungen)")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
