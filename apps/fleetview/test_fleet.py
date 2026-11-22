#!/usr/bin/env python3
"""FleetView-Regeln (RFC-0085 §10) — ohne Netz, ohne Docker, ohne Zwilling.

Geprüft wird, was Entscheidungen trifft: die Antwort des Zwillings lesen
(die Form von oaap.data.twin §2.6/§2.6a), Knoten und Instanzen
zusammenführen, Alias vor Knotenname, Lebenszeichen vor Selbstauskunft,
der zuletzt gelesene Stand bei einem Fehlschlag — und dass der Schlüssel
nirgends auftaucht. Dazu ein echter Lesezugriff gegen einen kleinen
HTTP-Server, der wie der Zwilling antwortet.

Run: python3 apps/fleetview/test_fleet.py
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


NOW = datetime(2026, 11, 15, 22, 0, 0, tzinfo=timezone.utc)
SECRET = "oaapk_fleetview_secret"


def g(attrs, rels=None):
    return {"origin": "platform:fleet", "object_id": "x",
            "attributes": {("fleet" + k[0].upper() + k[1:]): {"value": v, "valid_from": None,
                                                             "valid_to": None}
                           for k, v in attrs.items()},
            "relations": [{"key": "fleet" + k[0].upper() + k[1:], "target": t,
                           "valid_from": None, "valid_to": None} for k, t in (rels or [])],
            "activities": []}


def node(name, alias="", state="up", version="0.1.292", via="", profiles="",
         address="", core_worst="ok", attention=None, oid=None):
    attention = attention or []
    return {"id": oid or f"urn:oaap:obj:{name}", "type": "FleetNode", "title": name,
            "owner": "platform:fleet", "groups": {
                "oaap.software": g({"platformVersion": version, "os": "Debian 13",
                                    "profiles": profiles}),
                "oaap.placement": g({"alias": alias or name, "place": "", "tags": "",
                                     "address": address, "via": via}),
                "oaap.services": g({"coreServices": "identity ok", "coreWorst": core_worst}),
                "oaap.attention": g({"attentionCount": len(attention),
                                     "attentionKinds": ", ".join(sorted({k for k, _ in attention})),
                                     "attentionDetail": ", ".join(f"{k}: {d}" for k, d in attention)}),
                "oaap.dns": g({"publicIp": "", "namesTotal": 2, "namesNotOk": 0,
                               "namesProblems": ""}),
                "oaap.liveness": g({"state": state, "since": "2026-11-15T21:02:20Z"}),
            }}


def inst(name, node_name, state="ok", app="chat", channel="production"):
    return {"id": f"urn:oaap:obj:{node_name}/{name}", "type": "FleetInstance", "title": name,
            "owner": "platform:fleet", "groups": {
                "oaap.appsoftware": g({"app": app, "appVersion": "0.2.0", "instanceName": name}),
                "oaap.runtime": g({"channel": channel, "runState": state, "origin": "store",
                                   "address": "", "autoAddress": f"{name}.x.de",
                                   "autoState": "ok"},
                                  [("isPartOf", f"urn:oaap:obj:{node_name}")]),
            }}


NODES = [
    node("oaap-demo", alias="oaapi01", profiles="broker, fleet-hub, store",
         address="oaap-demo.duckdns.org"),
    node("oaapx02", via="oaapx01", profiles="gateway-only", address="hsp.btocs.de"),
    node("raspberrypi", address="raspberrypi"),
    node("oaap-alt", state="removed", version="0.1.200"),
    node("oaap-bernd", state="silent", attention=[("dns_drift", "bernd.de")]),
]
INSTS = [inst("fleetview", "oaap-demo"), inst("chat", "oaap-demo", state="warn"),
         inst("chat", "oaapx02"), inst("alt", "oaapx02", state="removed"),
         inst("kuma", "raspberrypi", state="standby")]

print("\n-- Attribute lesen")
o = NODES[0]
check("Alias aus oaap.placement", fleet.attr(o, "oaap.placement", "alias") == "oaapi01")
check("fehlendes Attribut gibt den Vorgabewert",
      fleet.attr(o, "oaap.hostcare", "rebootRequired", None) is None)
merged = {"groups": {"oaap.placement+abcd1234": NODES[0]["groups"]["oaap.placement"]}}
check("Gruppe nach Zusammenführung (key+id) wird gefunden",
      fleet.attr(merged, "oaap.placement", "alias") == "oaapi01")
check("Beziehung isPartOf", fleet.rel_targets(INSTS[0], "oaap.runtime", "isPartOf")
      == ["urn:oaap:obj:oaap-demo"])

print("\n-- Portaladresse")
check("Name mit Punkt -> https", fleet.portal_url("hsp.btocs.de") == "https://hsp.btocs.de")
check("LAN-Name -> http", fleet.portal_url("raspberrypi") == "http://raspberrypi")
check("IP -> http", fleet.portal_url("10.0.0.5") == "http://10.0.0.5")
check("leer bleibt leer", fleet.portal_url("") == "")

print("\n-- Zeilen")
rows = fleet.node_rows(NODES, INSTS)
by = {r["name"]: r for r in rows}
check("alle fünf Knoten, auch ohne Liste und Schlüssel", len(rows) == 5)
check("oaap-demo heißt oaapi01, der feste Name bleibt daneben",
      by["oaap-demo"]["label"] == "oaapi01" and by["oaap-demo"]["renamed"])
check("ohne Umbenennung kein Zusatz", not by["oaapx02"]["renamed"])
check("oaapx02 meldet über oaapx01", by["oaapx02"]["via"] == "oaapx01")
check("Instanzen dem Knoten zugeordnet",
      [i["instance"] for i in by["oaap-demo"]["instances"]] == ["chat", "fleetview"])
check("entfernte Instanz zählt nicht, steht aber benannt",
      by["oaapx02"]["inst_total"] == 1
      and [i["instance"] for i in by["oaapx02"]["removed_instances"]] == ["alt"])
check("schlechteste Instanz bestimmt den lebenden Knoten", by["oaap-demo"]["state"] == "warn")
check("Standby ist kein Fehler", by["raspberrypi"]["state"] == "ok"
      and by["raspberrypi"]["inst_standby"] == 1)
check("stumm schlägt Selbstauskunft", by["oaap-bernd"]["state"] == "silent")
check("entfernt steht am Ende", rows[-1]["name"] == "oaap-alt"
      and rows[-1]["state"] == "removed")
check("sortiert nach angezeigtem Namen",
      [r["label"] for r in rows[:-1]] == ["oaap-bernd", "oaapi01", "oaapx02", "raspberrypi"])
check("find_row nach festem Namen und nach Alias",
      fleet.find_row(rows, "oaap-demo") is fleet.find_row(rows, "oaapi01") is by["oaap-demo"])
check("node_state: Dienst gestört", fleet.node_state("up", "error", []) == "error")
check("node_state: unbekannter Instanzzustand -> unbekannt",
      fleet.node_state("up", "ok", [{"state": "seltsam"}]) == "unknown")

print("\n-- Sichten")
items = fleet.fleet_attention(rows)
check("stummer Knoten oben benannt",
      any(i["node"] == "oaap-bernd" and i["label"] == "Stumm" for i in items))
check("Hinweis-Art übersetzt", any(i["label"] == "DNS zeigt woanders hin" for i in items))
check("Host-Pflege-Arten übersetzt (gefunden auf oaap-test, 15.11.)",
      all(k in fleet.ATTENTION_LABELS for k in
          ("reboot_required", "reboot_planned", "images_open", "advisories_open")))
check("entfernter Knoten erzeugt keinen Hinweis",
      not any(i["node"] == "oaap-alt" for i in items))
check("Versionsabweichung ohne entfernten Knoten", fleet.version_note(rows) == "")
dups = fleet.duplicate_instances(rows)
check("chat auf zwei Knoten benannt (mit Alias)",
      dups == [{"instance": "chat", "nodes": ["oaapi01", "oaapx02"]}], dups)
hubs = fleet.pairing_targets(rows)
check("Koppeln-Knopf zur Zentrale",
      hubs == [{"name": "oaapi01", "url": "https://oaap-demo.duckdns.org/flotte#zentrale"}], hubs)
check("Kopplungskarte des Knotens",
      fleet.pairing_link(by["oaapx02"]) == "https://hsp.btocs.de/flotte#kopplung")
check("ohne Adresse kein Kopplungslink", fleet.pairing_link(by["oaap-alt"]) == "")

print("\n-- Lesen mit Fehlschlag")


def good(url, key, t):
    return (NODES if t == "FleetNode" else INSTS), False


def bad(url, key, t):
    raise fleet.TwinError(403, "'fleetview' neither contributes nor consumes 'FleetNode'")


st = fleet.read_fleet("http://gw/twin", SECRET, None, fetch=good, now=NOW)
check("gelesen mit Zeitstempel", st["read"] == "2026-11-15T22:00:00Z" and not st["error"]
      and len(st["nodes"]) == 5)
st2 = fleet.read_fleet("http://gw/twin", SECRET, st, fetch=bad, now=NOW)
check("Fehlschlag behält den letzten Stand", len(st2["nodes"]) == 5
      and st2["read"] == st["read"] and "403" in st2["error"])
st3 = fleet.read_fleet("", "", None, now=NOW)
check("ohne Zugang: verständlicher Text, kein Absturz",
      "keinen Zugang zum Zwilling" in st3["error"] and st3["nodes"] == [])
check("Alter in Sekunden", fleet.age_seconds("2026-11-15T21:59:00Z", NOW) == 60)
check("Alter unlesbar -> None", fleet.age_seconds("", NOW) is None)

d = tempfile.mkdtemp()
path = os.path.join(d, "fleet-state.json")
fleet.save_state(path, st)
check("Zustand übersteht Speichern/Laden", fleet.load_state(path)["read"] == st["read"])
check("Schlüssel steht nicht im Zustand", SECRET not in open(path, encoding="utf-8").read())

print("\n-- Echter Lesezugriff gegen einen Schein-Zwilling")
seen = {}


class Twin(BaseHTTPRequestHandler):
    def do_GET(self):
        seen["path"] = self.path
        seen["auth"] = self.headers.get("Authorization", "")
        if self.headers.get("Authorization") != f"Bearer {SECRET}":
            body, code = b"no", 401
        elif self.path == "/twin/objects?type=FleetNode":
            body, code = json.dumps({"objects": NODES, "truncated": False}).encode(), 200
        elif self.path == "/twin/objects?type=FleetInstance":
            body, code = json.dumps({"objects": INSTS, "truncated": True}).encode(), 200
        else:
            body, code = b"'fleetview' neither contributes nor consumes", 403
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), Twin)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/twin"
st = fleet.read_fleet(url, SECRET, None)
check("liest Knoten und Instanzen über /objects?type=", len(st["nodes"]) == 5
      and len(st["instances"]) == 5 and not st["error"], st.get("error"))
check("gekürzte Liste wird gemeldet", st["truncated"] is True)
check("Schlüssel nur im Authorization-Header", seen["auth"] == f"Bearer {SECRET}")
try:
    fleet.fetch_type(url, SECRET, "Fremd")
    check("fremder Typ verweigert", False)
except fleet.TwinError as e:
    check("fremder Typ verweigert (403 mit Text des Zwillings)",
          e.status == 403 and "consumes" in e.detail)
st = fleet.read_fleet(url, "falsch", None)
check("falscher Schlüssel: Fehler ohne Schlüssel im Text",
      st["error"] and "falsch" not in st["error"] and SECRET not in st["error"])
srv.shutdown()

print("\n-- Manifest")
here = os.path.dirname(os.path.abspath(__file__))
man = open(os.path.join(here, "oaap-app.yaml"), encoding="utf-8").read()
check("bindet FleetNode und FleetInstance als consumes",
      "consumes:" in man and "type: FleetNode" in man and "type: FleetInstance" in man)
check("keine Knotenliste, keine Schlüssel mehr",
      "FLEETVIEW_NODES" not in man and "FLEETVIEW_KEYS" not in man)
appsrc = open(os.path.join(here, "app.py"), encoding="utf-8").read()
check("Version in app.py und Manifest gleich",
      'VERSION = "1.2.1"' in appsrc and "version: 1.2.1" in man)
check("Name im Manifest ohne OAAP: Flotte", "name: Flotte" in man and "OAAP FleetView" not in man)

print("\n-- Seiten im Rahmen, Listen, MCP-Tür (1.1.0)")
os.environ["FLEETVIEW_DATA_DIR"] = tempfile.mkdtemp()
import io  # noqa: E402
import app  # noqa: E402
app._state = fleet.read_fleet("http://gw/twin", SECRET, None, fetch=good, now=NOW)
html_ = app.overview({})
check("Übersicht trägt Kit und Rahmen der Plattform",
      "/platform/theme.css" in html_ and "/platform/app.js" in html_)
check("... den Namen Flotte, ohne OAAP und ohne eigene Palette",
      'class="oaap-appname">Flotte<' in html_ and "OAAP FLEETVIEW" not in html_
      and "--oaap-blue-900" not in html_ and "anna" not in html_)
check("Reiter: Übersicht zuerst, dann Instanzen",
      html_.index(">Übersicht<") < html_.index(">Instanzen<") and 'aria-current="page"' in html_)
check("Knotenliste als Listenbericht mit Zählzeile", "5 Knoten" in html_ and "oaap-filter" in html_)
h = app.overview({"state": ["silent"]})
check("Filter Zustand=Stumm zeigt nur oaap-bernd",
      "oaap-bernd" in h and "node/raspberrypi" not in h and "1 Knoten" in h)
h = app.overview({"q": ["raspberry"]})
check("Suche findet raspberrypi", "node/raspberrypi" in h and "node/oaapx02" not in h)
h = app.instances_page({"sort": ["-instance"]})
check("Instanzen sortiert absteigend (aria-sort)", 'aria-sort="descending"' in h
      and h.index(">kuma<") < h.index(">chat<"))
many = [inst(f"i{n:03d}", "oaap-demo") for n in range(120)]
app._state = fleet.read_fleet("http://gw/twin", SECRET, None,
                              fetch=lambda u, k, t: ((NODES if t == "FleetNode" else many), False),
                              now=NOW)
h = app.instances_page({})
check("Blättern ab 50: 1–50 von 120, Weitere 50", "1–50 von 120" in h and "offset=50" in h)
h2 = app.instances_page({"offset": ["100"]})
check("... dritte Seite zeigt i100 bis i119", ">i100<" in h2 and ">i119<" in h2 and ">i099<" not in h2)
app._state = fleet.read_fleet("http://gw/twin", SECRET, None, fetch=good, now=NOW)
h = app.node_page("oaap-demo", {})
check("Objektseite: zurück zur Liste, Objekt für Fragen", 'class="oaap-back"' in h
      and 'data-oaap-object="FleetNode:oaap-demo"' in h)


def rpc(msg, roles):
    who = {"user": "anna", "roles": roles}
    return app.DOOR.answer(msg, who)


r = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, ["admin"])
names = sorted(t["name"] for t in r["result"]["tools"])
check("MCP tools/list", names == ["fleetview_instances", "fleetview_node_read", "fleetview_nodes"], names)
r = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
         "params": {"name": "fleetview_nodes", "arguments": {}}}, ["support"])
txt = r["result"]["content"][0]["text"]
doc = json.loads(txt)
check("fleetview_nodes als support: fünf Knoten, Stumm benannt",
      not r["result"]["isError"] and len(doc["nodes"]) == 5
      and any(a["label"] == "Stumm" for a in doc["attention"]))
check("... ohne Schlüssel", SECRET not in txt)
r = rpc({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "fleetview_node_read", "arguments": {"name": "oaapi01"}}}, ["admin"])
doc = json.loads(r["result"]["content"][0]["text"])
check("fleetview_node_read nach Alias", doc["name"] == "oaap-demo" and len(doc["instances"]) == 2)
r = rpc({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "fleetview_instances", "arguments": {"q": "oaapx02"}}}, ["admin"])
doc = json.loads(r["result"]["content"][0]["text"])
check("fleetview_instances sucht (Knoten)", doc["total"] == 1
      and doc["instances"][0]["node"] == "oaapx02", doc)
for tool in ("fleetview_nodes", "fleetview_node_read", "fleetview_instances"):
    r = rpc({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": tool, "arguments": {"name": "oaapi01"}}}, ["user"])
    check(f"{tool} als user: abgelehnt wie die Seite", r["result"]["isError"] is True)
r = rpc({"jsonrpc": "2.0", "id": 6, "method": "prompts/list"}, ["admin"])
check("zwei Vorlagen", len(r["result"]["prompts"]) == 2)

print(f"\n{ok} bestanden, {fail} fehlgeschlagen")
sys.exit(1 if fail else 0)
