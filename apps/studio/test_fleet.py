#!/usr/bin/env python3
"""Der Blick des Studios auf den Zielknoten — ohne Netz und ohne Knoten.

Geprüft wird, was entscheidet: die Konfiguration der Flotten-Schlüssel
lesen, die Wurzel eines Knotens aus einer beliebigen seiner Adressen
gewinnen, eine Antwort kurz vorhalten (und auf Wunsch nicht), Instanzen
und Auffälligkeiten aus dem Dokument holen — und dass der Schlüssel
nirgendwo auftaucht, wo ihn jemand lesen könnte: nicht in einer Sicht,
nicht in einem Fehlertext.

Run: python3 apps/studio/test_fleet.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fleet  # noqa: E402

ok = fail = 0
SECRET = "geheim-flotten-schluessel-xyz"


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label}{(' — ' + str(detail)) if detail else ''}")


T0 = datetime(2026, 8, 24, 12, 0, 0, tzinfo=timezone.utc)

DOC = {
    "schema": "oaap.fleet.status/0.2",
    "node": "oaap.joomp.de",
    "platform_version": "0.1.45",
    "core": [{"name": "gateway", "state": "ok"}],
    "instances": [
        {"instance": "bdt-app-test", "app": "bdt-app", "version": "0.339.0",
         "channel": "test", "state": "ok", "origin": "artifact",
         "address": "bdt-app-test.oaap.joomp.de"},
        {"instance": "bdt-app", "app": "bdt-app", "version": "0.339.0",
         "channel": "production", "state": "warn", "origin": "promoted",
         "address": "bdt-app.oaap.joomp.de"},
    ],
    "names": [
        {"name": "oaap.joomp.de", "kind": "node", "state": "ok"},
        {"name": "bdt-app.oaap.joomp.de", "kind": "instance",
         "instance": "bdt-app", "state": "ok"},
        {"name": "hub.bdt.joomp.de", "kind": "alias",
         "instance": "bdt-app", "state": "warn"},
        {"name": "fremd.example", "kind": "instance",
         "instance": "etwas-anderes", "state": "ok"},
    ],
    "attention": [
        {"kind": "instance_unhealthy", "instance": "bdt-app"},
        {"kind": "confirmation_pending", "instance": "etwas-anderes"},
        {"kind": "core_service_down", "detail": "identity"},
        {"kind": "brandneue_art", "instance": "bdt-app", "detail": "?"},
    ],
}

print("\n=== Schlüssel aus der Konfiguration ===")
keys = fleet.parse_keys(
    f"oaap.joomp.de={SECRET}; oaap-demo.local:8443=zweiter\n"
    "# ein Kommentar\n"
    f"https://oaapx01.example/ = dritter\n"
    "kaputt-ohne-wert=\n"
    "=ohne-namen\n")
check("Hostname mit Punkten wird angenommen", keys.get("oaap.joomp.de") == SECRET)
check("Hostname mit Port wird angenommen",
      keys.get("oaap-demo.local:8443") == "zweiter")
check("ganze Adresse wird auf den Hostnamen zurückgeschnitten",
      keys.get("oaapx01.example") == "dritter", keys)
check("Kommentare und unvollständige Zeilen fallen weg", len(keys) == 3, keys)
check("die Oberfläche erfährt nur die Namen",
      fleet.key_hosts(keys) == ["oaap-demo.local:8443", "oaap.joomp.de",
                                "oaapx01.example"])
check("und niemals Schlüsselmaterial",
      SECRET not in " ".join(fleet.key_hosts(keys)))

print("\n=== Die Wurzel eines Knotens ===")
check("aus dem Deploy-Hook",
      fleet.node_base("https://oaap.joomp.de/deploy/bdt-app-test")
      == "https://oaap.joomp.de")
check("aus der Portal-Adresse mit Schrägstrich",
      fleet.node_base("https://oaap.joomp.de/") == "https://oaap.joomp.de")
check("mit Port und http", fleet.node_base("http://10.10.10.96:8107/x")
      == "http://10.10.10.96:8107")
check("ohne Schema ist keine Adresse", fleet.node_base("oaap.joomp.de") == "")
check("leer bleibt leer", fleet.node_base("") == "" and fleet.node_base(None) == "")
check("ein fremdes Schema wird nicht übernommen",
      fleet.node_base("file:///etc/passwd") == "")
check("Hostname klein geschrieben",
      fleet.host_of("https://OAAP.Joomp.De/x") == "oaap.joomp.de")

print("\n=== Fragen — oder eben nicht ===")
calls = []


def spy(base, key, timeout=fleet.TIMEOUT):
    calls.append((base, key))
    return DOC, ""


fleet.forget()
st = fleet.status("https://oaap.joomp.de", {}, fetch=spy, now=T0)
check("ohne Schlüssel wird nicht gefragt", not calls)
check("und das ist kein Fehler, sondern 'nicht eingerichtet'",
      st["configured"] is False and st["error"] == "" and st["doc"] is None)

st = fleet.status("", keys, fetch=spy, now=T0)
check("ohne Zielknoten wird nicht gefragt", not calls and not st["configured"])

st = fleet.status("https://oaap.joomp.de", keys, fetch=spy, now=T0)
check("mit Schlüssel wird gefragt", len(calls) == 1)
check("und zwar mit genau diesem Schlüssel", calls[0] == ("https://oaap.joomp.de", SECRET))
check("das Dokument kommt an", st["doc"] is DOC and st["configured"])

st = fleet.status("https://oaap.joomp.de", keys, fetch=spy, now=T0 + timedelta(seconds=5))
check("kurz danach wird die Antwort vorgehalten", len(calls) == 1)
check("und ihr Alter benannt", st["age"] == 5)

st = fleet.status("https://oaap.joomp.de", keys, fresh=True, fetch=spy,
                  now=T0 + timedelta(seconds=6))
check("'neu abfragen' umgeht das Vorhalten", len(calls) == 2)

st = fleet.status("https://oaap.joomp.de", keys, fetch=spy,
                  now=T0 + timedelta(seconds=120))
check("nach Ablauf wird wieder gefragt", len(calls) == 3)

print("\n=== Ein Fehlschlag ist ein Zustand ===")
fleet.forget()
tries = []


def broken(base, key, timeout=fleet.TIMEOUT):
    tries.append(base)
    return None, "Nicht erreichbar (TimeoutError)"


st = fleet.status("https://oaap.joomp.de", keys, fetch=broken, now=T0)
check("kein Dokument, aber ein Grund",
      st["doc"] is None and st["error"].startswith("Nicht erreichbar"))
check("eingerichtet ist es trotzdem", st["configured"] is True)
check("der Grund verrät den Schlüssel nicht", SECRET not in st["error"])
fleet.status("https://oaap.joomp.de", keys, fetch=broken,
             now=T0 + timedelta(seconds=5))
check("auch ein Fehlschlag wird kurz vorgehalten", len(tries) == 1)
fleet.status("https://oaap.joomp.de", keys, fetch=broken,
             now=T0 + timedelta(seconds=15))
check("aber kürzer als ein Erfolg — ein Neustart soll nicht 30 s tot sein",
      len(tries) == 2)
fleet.forget("https://oaap.joomp.de")
fleet.status("https://oaap.joomp.de", keys, fetch=broken,
             now=T0 + timedelta(seconds=16))
check("vergessen wirkt sofort", len(tries) == 3)

print("\n=== Was im Dokument steht ===")
check("die Test-Instanz wird gefunden",
      (fleet.instance(DOC, "bdt-app-test") or {}).get("version") == "0.339.0")
check("die Produktiv-Instanz auch",
      (fleet.instance(DOC, "bdt-app") or {}).get("channel") == "production")
check("eine unbekannte Instanz ist None — eine Aussage, keine Lücke",
      fleet.instance(DOC, "gibt-es-nicht") is None)
check("ohne Dokument gibt es keine Instanz",
      fleet.instance(None, "bdt-app") is None)
check("ohne Namen auch nicht", fleet.instance(DOC, "") is None)

att = fleet.attention_for(DOC, ["bdt-app-test", "bdt-app"])
labels = [a["label"] for a in att]
check("Auffälligkeiten der eigenen Instanzen kommen mit",
      "Instanz ungesund" in labels, labels)
check("die einer fremden Instanz nicht", "Bestätigung offen" not in labels)
check("was keiner Instanz gehört, trifft uns auch",
      "Kerndienst ausgefallen" in labels)
check("eine unbekannte Art wird roh mitgenommen, nicht verschluckt",
      "brandneue_art" in labels, labels)
check("ohne Dokument ist die Liste leer",
      fleet.attention_for(None, ["bdt-app"]) == [])

names = [n["name"] for n in fleet.names_for(DOC, "bdt-app")]
check("veröffentlichte Namen der Instanz inkl. Alias",
      names == ["bdt-app.oaap.joomp.de", "hub.bdt.joomp.de"], names)
check("der Knotenname gehört keiner Instanz",
      "oaap.joomp.de" not in names)
check("fremde Instanznamen bleiben draußen",
      fleet.names_for(DOC, "bdt-app-test") == [])
check("ein Knoten mit Schema 0.1 liefert keine Namen — leer ist leer",
      fleet.names_for({"schema": "oaap.fleet.status/0.1"}, "bdt-app") == [])

print("\n=== Nur echte Status-Dokumente ===")
check("ein Dokument ohne Schema wäre keins",
      not str({}.get("schema", "")).startswith(fleet.SCHEMA_PREFIX))
check("Zustands-Beschriftungen sind vollständig",
      set(fleet.STATE_LABELS) == set(fleet.STATE_BADGE)
      == {"ok", "warn", "error", "unknown"})

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
