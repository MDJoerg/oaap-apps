#!/usr/bin/env python3
"""Die Doku der installierten Apps in der Hilfe (RFC-0094 Stufe 1).

    python3 test_apps_docs.py

Ein nachgebauter Ordner /oaap/docs, wie der Knoten ihn schreibt: zwei
Fassungen eines Produkts (Produktiv und Test), ein Produkt nur in Test, ein
Eintrag mit einem Pfad, der den Ordner verlassen will. Jede Zeile hat einen
Köder.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apps_source  # noqa: E402
import index as ix  # noqa: E402
import mcp_door  # noqa: E402
from library import Library  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:600]}")


ROOT = tempfile.mkdtemp(prefix="help-docs-")
DATA = tempfile.mkdtemp(prefix="help-data-")


def write(rel, text):
    path = os.path.join(ROOT, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


BDT = {"product": "bdt", "title": "BDT – Business Daily Tools", "lang": "de",
       "audience": ["user", "keyuser", "admin"], "readers": ["end-user"],
       "kinds": {"faq.md": "faq", "kacheln/": "function", "quickstart/": "tutorial"},
       "color": "#0a6ed1", "entry": "index.md"}
write("iid-prod/0.753.0/bdt/index.md", "# BDT\n\nDie Startseite mit Kacheln.\n")
write("iid-prod/0.753.0/bdt/faq.md", "# Häufige Fragen\n\n**Ist BDT kostenlos?**\nJa, Produktivfassung.\n")
write("iid-prod/0.753.0/bdt/kacheln/ai-chat.md", "# KI-Chat\n\n## Voraussetzungen\n\nEin Anbieter.\n")
write("iid-prod/0.753.0/bdt/quickstart/erste.md",
      "---\nkind: howto\naudience: [admin]\n---\n# Erste Schritte\n\nNur für Admins.\n")
write("iid-test/0.757.0/bdt/faq.md", "# Häufige Fragen\n\n**Ist BDT kostenlos?**\nJa, Testfassung.\n")
write("iid-hub/0.27.1/hub/index.md", "# Hub\n\nDer Hub vermittelt Sitzungen.\n")
write("index.json", json.dumps({"v": 1, "tenant": "bdt", "updated": "2026-11-18T12:00:00Z", "sets": [
    {"dir": "iid-test/0.757.0", "instance": "bdt-app-test", "name": "app-test", "app_id": "bdt",
     "app_name": "BDT", "version": "0.757.0", "channel": "test", "products": [BDT]},
    {"dir": "iid-prod/0.753.0", "instance": "bdt-app", "name": "app", "app_id": "bdt",
     "app_name": "BDT", "version": "0.753.0", "channel": "production", "products": [BDT]},
    {"dir": "iid-hub/0.27.1", "instance": "bdt-hub-test", "name": "hub-test", "app_id": "bdt-hub",
     "app_name": "BDT Hub", "version": "0.27.1", "channel": "test",
     "products": [{"product": "hub", "title": "BDT Hub", "audience": ["user", "admin"]}]},
    {"dir": "../../etc", "instance": "x", "channel": "production",
     "products": [{"product": "boese", "title": "böse"}]},
]}))

print("=== apps_source.read ===")
docs, products, state = apps_source.read(ROOT)
ok("gelesen, zwei Produkte (der Ausbruch fehlt)", state["ok"] and
   sorted(p["product"] for p in products) == ["bdt", "hub"], (state, products))
ok("KÖDER: bdt kommt aus der Produktiv-Instanz, nicht aus der Test-Instanz",
   any(d["path"] == "bdt/faq.md" and "Produktivfassung" in d["text"] for d in docs)
   and not any("Testfassung" in d["text"] for d in docs))
ok("hub gibt es nur in Test: Vorschau", next(p for p in products if p["product"] == "hub")
   ["channel"] == "test")
kinds = {d["path"]: d["kind"] for d in docs}
ok("Typ aus der Zuordnung: Datei, Ordner, sonst unspecified",
   kinds["bdt/faq.md"] == "faq" and kinds["bdt/kacheln/ai-chat.md"] == "function"
   and kinds["bdt/index.md"] == "unspecified", kinds)
ok("Einstieg und Version am Produkt", next(p for p in products if p["product"] == "bdt")
   ["entry"] == "bdt/index" and next(p for p in products if p["product"] == "bdt")["version"] == "0.753.0")
ok("ohne Ordner: leer mit Satz, kein Fehler",
   apps_source.read(os.path.join(ROOT, "fehlt"))[2]["error"] == "keine Doku der Apps eingehängt")

print("\n=== die Hilfe ===")
env = {"HELP_DOCS_DIR": ROOT, "HELP_OAAP_DOCS": "off", "HELP_SEED_FILE": "/nicht/da"}
lib = Library(DATA, env=env)
lib.refresh()
USER, ADMIN = {"user"}, {"admin", "user"}
ok("HELP_OAAP_DOCS=off: kein Plattform-Dokument, kein Abruf", not any(
    d["source"] == "oaap-docs" for d in lib.index.docs())
   and lib.state()["oaap-docs"].get("skipped") == "HELP_OAAP_DOCS=off")
prods = {p["product"]: p for p in lib.products(roles=USER)}
ok("ein user sieht bdt, aber nicht die Vorschau hub", set(prods) == {"bdt"}, prods)
ok("... mit Zahlen je Typ", prods["bdt"]["kinds"].get("faq") == 1
   and prods["bdt"]["kinds"].get("function") == 1, prods["bdt"])
ok("ein admin sieht auch die Vorschau", {p["product"] for p in lib.products(roles=ADMIN)} == {"bdt", "hub"})
hits = lib.search("kostenlos", roles=USER)
ok("Suche findet die FAQ mit Produkt, Typ und Version", hits and hits[0]["product"] == "bdt"
   and hits[0]["kind"] == "faq" and hits[0]["app_version"] == "0.753.0", hits)
ok("Filter kind=function: keine FAQ", not lib.search("kostenlos", roles=USER, kind="function"))
ok("Filter product=hub: für user nichts (Vorschau)", not lib.search("Hub Sitzungen", roles=USER, product="hub"))
ok("... für admin der Hub", lib.search("Hub Sitzungen", roles=ADMIN, product="hub"))
ok("KÖDER: Kopf gewinnt -- 'Erste Schritte' ist howto und nur für admin",
   not lib.search("Erste Schritte Admins", roles=USER)
   and lib.search("Erste Schritte Admins", roles=ADMIN)[0]["kind"] == "howto")
doc = lib.read("bdt/faq", roles=USER)
ok("lesen über die id aus Produkt und Pfad", doc and doc["product"] == "bdt" and "Produktivfassung" in doc["markdown"])
ok("öffentlicher Pfad (ungefiltert) zeigt keine Vorschau", not any(
    d["channel"] == "test" for d in lib.index.docs() if ix.visible(d, ix.UNFILTERED)))

print("\n=== MCP ===")
r = mcp_door.answer(lib, USER, "t", {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                       "params": {"name": "help_products", "arguments": {}}})
body = json.loads(r["result"]["content"][0]["text"])
ok("help_products nennt bdt mit Version", [p["product"] for p in body["products"]] == ["bdt"]
   and body["products"][0]["version"] == "0.753.0", body)
r = mcp_door.answer(lib, USER, "t", {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                       "params": {"name": "help_search",
                                                  "arguments": {"query": "kostenlos", "kind": "faq",
                                                                "product": "bdt"}}})
body = json.loads(r["result"]["content"][0]["text"])
ok("help_search mit product und kind", body["count"] == 1 and body["hits"][0]["id"] == "bdt/faq", body)
tools = mcp_door.answer(lib, USER, "t", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
ok("alle Werkzeuge lesend", all(t["annotations"]["readOnlyHint"] for t in tools["result"]["tools"]))

print("\n=== der Knoten ändert etwas ===")
ok("unverändert: nichts neu zu bauen", not lib.apps_changed())
idx = json.load(open(os.path.join(ROOT, "index.json"), encoding="utf-8"))
idx["sets"] = [s for s in idx["sets"] if s["instance"] != "bdt-app"]
write("index.json", json.dumps(idx))
st = os.stat(os.path.join(ROOT, "index.json"))
os.utime(os.path.join(ROOT, "index.json"), ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
ok("index.json geändert: der Wächter merkt es", lib.apps_changed())
lib.rebuild()
hits = lib.search("kostenlos", roles=ADMIN)
ok("Produktiv-Instanz entfernt: jetzt antwortet die Testfassung als Vorschau",
   hits and "Testfassung" in lib.read(hits[0]["id"], roles=ADMIN)["markdown"]
   and hits[0]["channel"] == "test", hits)
ok("... und ein user sieht sie nicht", not lib.search("kostenlos", roles=USER))

print("\n" + ("ALLE PRUEFUNGEN BESTANDEN" if not fails else f"{fails} FEHLGESCHLAGEN"))
sys.exit(1 if fails else 0)
