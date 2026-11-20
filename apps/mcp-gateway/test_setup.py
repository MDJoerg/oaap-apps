"""Die Einrichtungstür des MCP-Gateways (RFC-0104 Stufe 2, mcp-gateway 0.27.0).

    py -3 apps/mcp-gateway/test_setup.py      (auf dem Knoten: python3 …)

Das Gateway läuft echt, per HTTP; daneben eine Papier-Plattform, die die
Quellenliste nennt (eine Hilfe-Instanz mit Tür). Den Katalog liest ein
Papier-`read_catalog`, wie in test_sources.py.

Gehalten wird (§3.1, §3.3, §8 Köder 2 und 3):
  * ohne Schlüssel, mit falschem Schlüssel, mit den Kopfzeilen eines tenant_admin
    allein und ohne OAAP_SETUP_KEY in der Umgebung: 403, nichts angelegt;
  * `dry` ändert nichts und sagt would_make;
  * dieselbe Liste zweimal: made, dann present; die Prüfung danach: present;
  * eine von Hand geänderte Sicht: conflict, die Sicht bleibt, wie der Mensch sie ließ,
    und die Einrichtung hält dort an;
  * eine Ampel, die ein Mensch gesetzt hat: conflict;
  * ein Feld mit Geheimnis-Namen: refused;
  * remove nur, solange das Angelegte unverändert ist; Menschenwerk bleibt;
  * wer die Einrichtung startete, steht in der Prüfspur.
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
DATA = tempfile.mkdtemp(prefix="mcpgw-setup-")
SETUP_KEY = "oaap-setup-0123456789abcdef-diese-instanz"
os.environ.update({"MCPGW_DATA_DIR": DATA, "MCPGW_MASTER_KEY": "x" * 64,
                   "MCPGW_CATALOG_MINUTES": "0", "OAAP_SETUP_KEY": SETUP_KEY})
sys.path.insert(0, HERE)

fails = passes = 0


def ok(label, cond, detail=""):
    global fails, passes
    fails += not cond
    passes += bool(cond)
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:700]}")


# ------------------------------------------------------- Papier-Plattform (Quellen)
HELP = {"name": "ws-hilfe", "app": "help", "version": "0.4.0", "channel": "production",
        "path": "/mcp", "address": "http://oaap-gateway-1:8140/mcp",
        "door": "http://oaap-gateway-1:8128/platform/mcp/to/ws-hilfe"}


class Plattform(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"v": 1, "sources": [HELP]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


plat = ThreadingHTTPServer(("127.0.0.1", 0), Plattform)
threading.Thread(target=plat.serve_forever, daemon=True).start()
os.environ["OAAP_MCP_SOURCES_URL"] = f"http://127.0.0.1:{plat.server_port}/platform/mcp/sources"
os.environ["OAAP_MCP_SOURCES_KEY"] = "oaapk_quellen"

import app  # noqa: E402
import books  # noqa: E402

CAT = {"tool": [
    {"name": "help_search", "description": "suchen", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "help_products", "description": "Produkte", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "help_request_guide", "description": "Anfrage", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": False}}],
    "prompt": [{"name": "guide", "description": "fuehren"}]}
READS = []


def fake_read(name, person=None):
    READS.append((name, person))
    books.store_catalog(app.DB, name, json.loads(json.dumps(CAT)))
    return True, f"Katalog von „{name}“ gelesen."


app.read_catalog = fake_read

gw = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=gw.serve_forever, daemon=True).start()
GW = f"http://127.0.0.1:{gw.server_address[1]}"
ACTOR = "jörg (Aufbau ws)"


def call(method, path="/oaap/setup", body=None, key=SETUP_KEY, headers=None):
    h = dict(headers or {})
    if key is not None:
        h["Authorization"] = "Bearer " + key
    h.setdefault("X-OAAP-Setup-Actor", ACTOR.encode("utf-8").decode("latin-1"))
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(GW + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw or b"null")
        except ValueError:
            return exc.code, raw.decode("utf-8", "replace")


def results(doc):
    return [r["result"] for r in (doc or {}).get("results", [])]


def counts():
    q = lambda sql: app.DB.execute(sql).fetchone()[0]
    return (q("SELECT COUNT(*) FROM servers"), q("SELECT COUNT(*) FROM views"),
            q("SELECT COUNT(*) FROM items"), q("SELECT COUNT(*) FROM keys"),
            q("SELECT COUNT(*) FROM assistants"), q("SELECT COUNT(*) FROM setup_made"))


ITEMS = [
    {"kind": "refresh", "id": "ws-hilfe"},
    {"kind": "server_light", "id": "ws-hilfe", "light": "green"},
    {"kind": "view", "id": "hilfe", "accesses": ["platform"],
     "tools": {"server": "ws-hilfe", "effect": "read"}},
    {"kind": "profile", "id": "ws-chat", "group": "@ws-chat", "views": ["hilfe"], "ceiling": "yellow"},
    {"kind": "assistant", "id": "hilfe", "title": "Hilfe", "view": "hilfe", "gateway": "ws-werkzeuge",
     "model": "standard", "greeting": "Frag mich zur Plattform.", "visibility": "tenant"},
]
TENANT_ADMIN = {"X-OAAP-User": "anna", "X-OAAP-Roles": "tenant_admin,admin,user",
                "X-OAAP-User-Id": "u-anna"}

print("--- §8 Köder 2: nur der eigene Einrichtungsschlüssel")
st, doc = call("GET", key=None)
ok("GET ohne Schlüssel: 403", st == 403, (st, doc))
st, doc = call("POST", body={"v": 1, "items": ITEMS}, key="oaap-setup-einer-anderen-instanz")
ok("POST mit dem Schlüssel einer anderen Instanz: 403", st == 403, (st, doc))
st, doc = call("POST", body={"v": 1, "items": ITEMS}, key=None, headers=TENANT_ADMIN)
ok("KÖDER: POST mit den Kopfzeilen eines tenant_admin, ohne Schlüssel: 403", st == 403, (st, doc))
st, doc = call("GET", key="", headers=TENANT_ADMIN)
ok("… auch GET mit leerem Bearer und tenant_admin: 403", st == 403, (st, doc))
st, doc = call("POST", body={"v": 1, "items": ITEMS}, key=None,
               headers=dict(TENANT_ADMIN, Authorization="Basic " + SETUP_KEY))
ok("… und der richtige Wert, aber nicht als Bearer: 403", st == 403, (st, doc))
ok("… nichts angelegt", counts() == (0, 0, 0, 0, 0, 0), counts())
del os.environ["OAAP_SETUP_KEY"]
st, doc = call("GET")
st2, _ = call("GET", key="")
ok("ohne OAAP_SETUP_KEY in der Umgebung: jeder Aufruf 403", st == 403 and st2 == 403, (st, st2))
os.environ["OAAP_SETUP_KEY"] = SETUP_KEY

print("--- Form")
st, doc = call("GET")
ok("GET: die Arten, die das Gateway einrichten kann",
   st == 200 and doc == {"v": 1, "kinds": ["refresh", "server_light", "view", "profile", "assistant"]},
   (st, doc))
st, doc = call("POST", body=b"kein json")
ok("kaputter Körper: 400", st == 400, (st, doc))
st, doc = call("POST", body={"items": ITEMS})
ok("ohne v: 400", st == 400, (st, doc))
st, doc = call("POST", body={"v": 1, "items": [{"kind": "view"}]})
ok("Eintrag ohne id: 400", st == 400, (st, doc))
st, doc = call("GET", "/oaap/setup?kind=quatsch&id=x")
ok("GET mit unbekannter Art: 400", st == 400, (st, doc))

print("--- dry ändert nichts")
before = counts()
st, doc = call("POST", body={"v": 1, "dry": True, "items": ITEMS})
ok("dry: would_make für alle fünf, auch hinter einem noch nicht gelesenen Katalog",
   st == 200 and results(doc) == ["would_make"] * 5, doc)
ok("… nichts angelegt, nichts gelesen", counts() == before and READS == [], (counts(), READS))

print("--- anlegen: made, dann present")
st, doc = call("POST", body={"v": 1, "dry": False, "items": ITEMS})
ok("erster POST: made für alle fünf", st == 200 and results(doc) == ["made"] * 5, doc)
ok("… Antwort in Reihenfolge, je kind und id",
   [(r["kind"], r["id"]) for r in doc["results"]] == [(i["kind"], i["id"]) for i in ITEMS], doc)
srow = books.server_row(app.DB, "ws-hilfe")
ok("refresh: Server aus der Quellenliste, Katalog über die Erkundungs-Tür (ohne Person)",
   srow is not None and srow["auth_kind"] == "platform" and READS and READS[0] == ("ws-hilfe", None),
   (dict(srow) if srow else None, READS))
ok("server_light: red → green", srow["light"] == "green", dict(srow))
vrow = books.view_row(app.DB, "hilfe")
tools = sorted((i["kind"], i["source"], i["effect"]) for i in books.items(app.DB, "hilfe"))
ok("view: Zugang platform, genau die lesenden Tools der Hilfe (kein Prompt, kein schreibendes)",
   vrow["ways"] == "platform" and vrow["max_effect"] == "read"
   and tools == [("tool", "help_products", "read"), ("tool", "help_search", "read")], (dict(vrow), tools))
prof = app.DB.execute("SELECT * FROM keys WHERE label='ws-chat'").fetchone()
ok("profile: Zugangsprofil @ws-chat → hilfe, Decke gelb",
   prof["kind"] == "profile" and prof["groups"] == "@ws-chat" and prof["views"] == "hilfe"
   and prof["ceiling"] == "yellow" and prof["max_effect"] == "read" and not prof["revoked"], dict(prof))
arow = books.assistant_row(app.DB, "hilfe")
ok("assistant: erste Revision @1, Sichtbarkeit Mandant, Modellname standard, mit Systemtext",
   arow["rev"] == 1 and arow["state"] == "active" and arow["doc"]["visibility"] == {"scope": "tenant"}
   and arow["doc"]["model"] == "standard" and arow["doc"]["gateway"] == "ws-werkzeuge"
   and arow["doc"]["system"], arow)
ok("… eine Person aus dem Chat findet das Profil für die Sicht",
   books.match_profile(app.DB, ["@ws-chat"], "hilfe", "@*")["label"] == "ws-chat")
st, doc = call("POST", body={"v": 1, "items": ITEMS})
ok("zweiter POST: present für alle fünf", st == 200 and results(doc) == ["present"] * 5, doc)
ok("… keine zweite Revision", books.assistant_row(app.DB, "hilfe")["rev"] == 1)
st, doc = call("POST", body={"v": 1, "dry": True, "items": ITEMS})
ok("die Prüfung (dry) danach: present für alle fünf", results(doc) == ["present"] * 5, doc)
audit = [(r["actor"], r["action"], r["subject"]) for r in app.DB.execute(
    "SELECT actor,action,subject FROM audit WHERE action LIKE 'setup.%' ORDER BY id")]
ok("Prüfspur: wer die Einrichtung startete (X-OAAP-Setup-Actor), je angelegtem Eintrag",
   len(audit) == 5 and all(a == "setup:" + ACTOR and act == "setup.made" for a, act, _s in audit), audit)
rec = books.setup_record(app.DB, "view", "hilfe")
ok("gemerkt je angelegtem Eintrag: wer, wann, Digest des Wunsches",
   rec and rec["actor"] == ACTOR and rec["at"] and len(rec["wish"]) == 64, dict(rec) if rec else None)

print("--- GET ?kind=&id=")
st, doc = call("GET", "/oaap/setup?kind=view&id=hilfe")
ok("view hilfe: present, ready (Einträge freigegeben und aktiv)",
   st == 200 and doc["state"] == "present" and doc["ready"] is True and doc["kind"] == "view", doc)
st, doc = call("GET", "/oaap/setup?kind=refresh&id=ws-hilfe")
ok("refresh ws-hilfe: present, ready (Katalog hat Tools)", doc["state"] == "present" and doc["ready"], doc)
st, doc = call("GET", "/oaap/setup?kind=assistant&id=hilfe")
ok("assistant hilfe: present, ready", doc["state"] == "present" and doc["ready"] is True, doc)
st, doc = call("GET", "/oaap/setup?kind=profile&id=gibts-nicht")
ok("unbekanntes Profil: absent, nicht ready", doc["state"] == "absent" and doc["ready"] is False, doc)

print("--- 0.30.0 (RFC-0109 §4): GET ?kind= ohne id, die Liste zum Auswählen")
srow = app.DB.execute("SELECT address, credential FROM servers WHERE name='ws-hilfe'").fetchone()
app.DB.execute("UPDATE servers SET credential='geheim-zugang-4711' WHERE name='ws-hilfe'")
app.DB.commit()
lists = {}
for kind in ("refresh", "server_light", "view", "profile", "assistant"):
    st, lists[kind] = call("GET", "/oaap/setup?kind=" + kind)
ok("jede Art antwortet mit einer Liste",
   all(d.get("kind") == k and isinstance(d.get("items"), list) for k, d in lists.items()), lists)
ok("Server: Name und Ampel", lists["server_light"]["items"] == [
    {"id": "ws-hilfe", "title": "ws-hilfe", "light": "green"}], lists["server_light"])
ok("Sicht, Profil (Obergrenze als Ampel), Assistent (Titel, Revision)",
   [i["id"] for i in lists["view"]["items"]] == ["hilfe"]
   and [(i["id"], i["light"]) for i in lists["profile"]["items"]] == [("ws-chat", "yellow")]
   and lists["assistant"]["items"][0]["id"] == "hilfe"
   and lists["assistant"]["items"][0]["title"].startswith("Hilfe (@1"), lists)
raw = json.dumps(lists)
key_digests = [r["digest"] for r in app.DB.execute("SELECT * FROM keys") if r["digest"]]
ok("S4: kein Zugang, keine Adresse, kein Schlüssel-Digest in den Listen",
   "geheim-zugang-4711" not in raw and (not srow["address"] or srow["address"] not in raw)
   and not any(d_ in raw for d_ in key_digests), raw[:300])
app.DB.execute("UPDATE servers SET credential=? WHERE name='ws-hilfe'", (srow["credential"],))
app.DB.commit()
st, doc = call("GET", "/oaap/setup?kind=nix")
ok("unbekannte Art ohne id: 400", st == 400, doc)
st, doc = call("GET", "/oaap/setup?kind=view", key="fremd")
ok("Köder: die Liste mit fremdem Schlüssel: 403", st == 403, doc)

print("--- keine Geheimnisse, keine fremden Felder")
st, doc = call("POST", body={"v": 1, "items": [
    dict(ITEMS[2], id="mit-geheimnis", token="abc"),
    {"kind": "view", "id": "tief", "accesses": ["platform"],
     "tools": [{"server": "ws-hilfe", "tool": "help_search", "password": "x"}]},
    {"kind": "profile", "id": "p2", "group": "@ws-chat", "views": ["hilfe"], "credentials": {}}]})
ok("token, password (verschachtelt), credentials: refused (die Einrichtung hält am ersten)",
   results(doc) == ["refused", "refused", "refused"] and "token" in doc["results"][0]["note"]
   and books.view_row(app.DB, "mit-geheimnis") is None and books.view_row(app.DB, "tief") is None, doc)
st, doc = call("POST", body={"v": 1, "dry": True, "items": [
    {"kind": "view", "id": "x", "accesses": ["platform"], "tools": {"server": "ws-hilfe", "effect": "read"},
     "secret": "s"}]})
ok("… auch bei dry", results(doc) == ["refused"], doc)
st, doc = call("POST", body={"v": 1, "items": [{"kind": "supplier", "id": "anthropic"}]})
ok("eine Art, die das Gateway nicht kennt: refused", results(doc) == ["refused"], doc)
st, doc = call("POST", body={"v": 1, "items": [dict(ITEMS[3], id="p3", budget=5)]})
ok("ein unbekanntes Feld: refused", results(doc) == ["refused"] and "budget" in doc["results"][0]["note"], doc)
st, doc = call("POST", body={"v": 1, "items": [dict(ITEMS[4], id="privat", visibility="private")]})
ok("ein privater Assistent: refused (die Einrichtung legt nur Mandanten-Assistenten an)",
   results(doc) == ["refused"] and books.assistant_row(app.DB, "privat") is None, doc)

print("--- §8 Köder 3: was ein Mensch geändert hat, bleibt")
name = next(i["name"] for i in books.items(app.DB, "hilfe") if i["source"] == "help_products")
books.remove_item(app.DB, "anna", "hilfe", "tool", name)          # wie /item-remove
hand = [dict(i) for i in books.items(app.DB, "hilfe")]
st, doc = call("POST", body={"v": 1, "items": ITEMS})
ok("KÖDER: Sicht hilfe von Hand geändert, Einrichtung fortgesetzt → conflict, mit Notiz",
   results(doc)[:3] == ["present", "present", "conflict"] and "help_products" in doc["results"][2]["note"], doc)
ok("… die Sicht ist, wie der Mensch sie ließ", [dict(i) for i in books.items(app.DB, "hilfe")] == hand)
ok("… und die Einrichtung hält dort an (was dahinter steht: nicht ausgeführt)",
   results(doc)[3:] == ["refused", "refused"] and "hält bei view/hilfe" in doc["results"][3]["note"], doc)
st, doc = call("POST", body={"v": 1, "dry": True, "items": ITEMS})
ok("… die Prüfung (dry) sagt es auch und prüft weiter",
   results(doc) == ["present", "present", "conflict", "present", "present"], doc)

books.save_server(app.DB, "anna", "handserver", "http://10.0.0.5:3900/mcp", "none", "yellow",
                  app.guard.clean(dict(app.guard.DEFAULTS)))
st, doc = call("POST", body={"v": 1, "items": [{"kind": "server_light", "id": "handserver", "light": "green"}]})
ok("eine Ampel, die ein Mensch gesetzt hat (gelb): conflict, bleibt gelb",
   results(doc) == ["conflict"] and books.server_row(app.DB, "handserver")["light"] == "yellow", doc)
books.save_view(app.DB, "anna", "handarbeit", "read", ways="platform")
st, doc = call("POST", body={"v": 1, "items": [
    {"kind": "view", "id": "handarbeit", "accesses": ["platform"],
     "tools": {"server": "ws-hilfe", "effect": "read"}}]})
ok("eine Sicht gleichen Namens, von Hand angelegt: conflict, bleibt leer",
   results(doc) == ["conflict"] and not books.items(app.DB, "handarbeit"), doc)

print("--- remove: nur Unverändertes")
st, doc = call("POST", body={"v": 1, "remove": [
    {"kind": "view", "id": "hilfe"}, {"kind": "view", "id": "handarbeit"},
    {"kind": "server_light", "id": "handserver"}]})
ok("die von Hand geänderte Sicht, eine fremde Sicht, eine fremde Ampel: conflict, alles bleibt",
   results(doc) == ["conflict", "conflict", "conflict"] and books.view_row(app.DB, "hilfe")
   and books.view_row(app.DB, "handarbeit")
   and books.server_row(app.DB, "handserver")["light"] == "yellow", doc)
st, doc = call("POST", body={"v": 1, "remove": [
    {"kind": "assistant", "id": "hilfe"}, {"kind": "profile", "id": "ws-chat"},
    {"kind": "server_light", "id": "ws-hilfe"}, {"kind": "refresh", "id": "ws-hilfe"}]})
ok("unverändert Angelegtes: removed; refresh: gone",
   results(doc) == ["removed", "removed", "removed", "gone"], doc)
ok("… der Assistent gelöscht, das Profil widerrufen, die Ampel wieder rot",
   books.assistant_row(app.DB, "hilfe")["state"] == "deleted"
   and app.DB.execute("SELECT revoked FROM keys WHERE label='ws-chat'").fetchone()["revoked"]
   and books.server_row(app.DB, "ws-hilfe")["light"] == "red")
st, doc = call("POST", body={"v": 1, "remove": [{"kind": "profile", "id": "ws-chat"},
                                                {"kind": "assistant", "id": "nie-da"}]})
ok("noch einmal: gone", results(doc) == ["gone", "gone"], doc)

# Eine Sicht, die die Einrichtung angelegt hat und die keiner anfasste, geht.
st, doc = call("POST", body={"v": 1, "items": [
    {"kind": "view", "id": "neu", "accesses": ["platform"],
     "tools": [{"server": "ws-hilfe", "tool": "help_search"}]}]})
st2, doc2 = call("POST", body={"v": 1, "remove": [{"kind": "view", "id": "neu"}]})
ok("view mit Liste von Tools: made, danach unverändert entfernt",
   results(doc) == ["made"] and results(doc2) == ["removed"] and books.view_row(app.DB, "neu") is None,
   (doc, doc2))
ok("Prüfspur: auch removed und conflict mit Name der Einrichtung",
   app.DB.execute("SELECT COUNT(*) FROM audit WHERE action='setup.removed' AND actor=?",
                  ("setup:" + ACTOR,)).fetchone()[0] == 4
   and app.DB.execute("SELECT COUNT(*) FROM audit WHERE action='setup.conflict'").fetchone()[0] >= 3)

print("--- 0.29.0 (RFC-0109 D12): zurückholen, was die Tür selbst entfernt hat")
BACK = [ITEMS[3], ITEMS[4]]                      # Profil ws-chat, Assistent hilfe -- oben entfernt
st, doc = call("POST", body={"v": 1, "dry": True, "items": BACK})
ok("dry: would_make für Profil und Assistent, mit dem Satz „Holt zurück“",
   results(doc) == ["would_make", "would_make"]
   and all("zurück" in r["note"] for r in doc["results"]), doc)
ok("… dry holt nichts zurück",
   app.DB.execute("SELECT revoked FROM keys WHERE label='ws-chat'").fetchone()["revoked"]
   and books.assistant_row(app.DB, "hilfe")["state"] == "deleted")
st, doc = call("POST", body={"v": 1, "items": BACK})
ok("Lauf nach dem Rückgängig: made, made", results(doc) == ["made", "made"], doc)
prof = app.DB.execute("SELECT * FROM keys WHERE label='ws-chat'").fetchone()
ok("das Profil gilt wieder, wie gewünscht (Gruppe, Sicht, Decke)",
   not prof["revoked"] and prof["kind"] == "profile" and prof["groups"] == "@ws-chat"
   and prof["views"] == "hilfe" and prof["ceiling"] == "yellow", dict(prof))
ok("… eine Person aus dem Chat findet es wieder",
   (books.match_profile(app.DB, ["@ws-chat"], "hilfe", "@*") or {"label": ""})["label"] == "ws-chat")
arow = books.assistant_row(app.DB, "hilfe")
ok("der Assistent ist wieder aktiv, mit einer neuen Revision @2 (die @1 bleibt)",
   arow["state"] == "active" and arow["rev"] == 2
   and app.DB.execute("SELECT COUNT(*) FROM assistant_revisions WHERE id='hilfe'").fetchone()[0] == 2,
   arow)
ok("Prüfspur: profile.revive und assistant.revive mit dem Namen der Einrichtung",
   app.DB.execute("SELECT COUNT(*) FROM audit WHERE action IN ('profile.revive','assistant.revive') "
                  "AND actor=?", ("setup:" + ACTOR,)).fetchone()[0] == 2)
st, doc = call("POST", body={"v": 1, "items": BACK})
ok("noch einmal: present, present (kein zweites Zurückholen)",
   results(doc) == ["present", "present"] and books.assistant_row(app.DB, "hilfe")["rev"] == 2, doc)
st, doc = call("POST", body={"v": 1, "remove": [{"kind": "assistant", "id": "hilfe"},
                                                {"kind": "profile", "id": "ws-chat"}]})
ok("das Zurückgeholte lässt sich wieder entfernen (removed, removed)",
   results(doc) == ["removed", "removed"], doc)
st, doc = call("POST", body={"v": 1, "items": BACK})
ok("… und wieder zurückholen (Rückgängig und Lauf beliebig oft)",
   results(doc) == ["made", "made"], doc)

print("--- KÖDER: was ein Mensch widerrufen oder gelöscht hat, kommt nicht zurück")
books.revoke(app.DB, "ws-chat", "anna")
books.assistant_set_state(app.DB, "hilfe", "deleted", "anna")
st, doc = call("POST", body={"v": 1, "dry": True, "items": BACK})
ok("dry: conflict, conflict, die Notiz sagt „nicht von der Einrichtung“",
   results(doc) == ["conflict", "conflict"]
   and all("nicht von der Einrichtung" in r["note"] for r in doc["results"]), doc)
st, doc = call("POST", body={"v": 1, "items": BACK})
ok("… auch ohne dry: das Profil bleibt widerrufen, der Assistent gelöscht",
   results(doc)[0] == "conflict"
   and app.DB.execute("SELECT revoked FROM keys WHERE label='ws-chat'").fetchone()["revoked"]
   and books.assistant_row(app.DB, "hilfe")["state"] == "deleted", doc)
books.issue(app.DB, "ws-schluessel", "anna", views=["hilfe"])
books.revoke(app.DB, "ws-schluessel", "setup:jemand")
st, doc = call("POST", body={"v": 1, "items": [dict(ITEMS[3], id="ws-schluessel")]})
ok("ein SCHLÜSSEL kommt nie zurück, auch wenn „setup:“ ihn widerrief",
   results(doc) == ["conflict"]
   and app.DB.execute("SELECT revoked FROM keys WHERE label='ws-schluessel'").fetchone()["revoked"], doc)

gw.shutdown()
plat.shutdown()
print("")
print(f"{passes} passed, {fails} failed")
sys.exit(1 if fails else 0)
