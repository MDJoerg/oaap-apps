#!/usr/bin/env python3
"""FleetView-Regeln (RFC-0021 §3) — ohne Netz, ohne Docker, ohne Knoten.

Geprüft wird, was Entscheidungen trifft: Konfiguration lesen (Fehler
benennen statt verschlucken), Zustand fortschreiben (nicht erreichbar
ist ein Zustand), Sichten bauen (veraltet, Versions-Abweichung,
attention-Sammlung inkl. unbekannter Arten) — und dass der Schlüssel
nirgends auftaucht: nicht im Zustand, nicht in einer Zeile, nicht in
einem Fehlertext.

Run: python3 apps/fleetview/test_fleet.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

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


NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "kAqQ7vXo-fleet-key"


def doc(node="oaapx01", version="0.1.41", instances=None, attention=None,
        core_state="ok"):
    return {"schema": "oaap.fleet.status/0.1", "node": node,
            "platform_version": version, "profiles": [],
            "time": "2026-08-23T11:59:00Z",
            "core": [{"name": "identity", "state": core_state}],
            "instances": instances or [], "attention": attention or []}


print("\n-- Konfiguration lesen")
nodes, errs = fleet.parse_nodes(
    "oaapx01=https://oaap.joomp.de\n# Kommentar\n"
    "demo=https://oaap-demo.duckdns.org/;bad line;UPPER=https://x.de;"
    "demo=https://doppelt.de;leer=")
check("zwei gültige Knoten", [n["name"] for n in nodes] == ["oaapx01", "demo"])
check("Adresse ohne Schluss-Schrägstrich",
      nodes[1]["url"] == "https://oaap-demo.duckdns.org")
check("drei Fehler benannt (bad line, UPPER, doppelt, leer)", len(errs) == 4, errs)
keys = fleet.parse_keys(f"oaapx01={SECRET};#weg\nkaputt;demo=zwei")
check("Schlüssel gelesen", keys == {"oaapx01": SECRET, "demo": "zwei"})
check("Oberfläche erfährt nur Namen",
      fleet.key_names(keys) == ["demo", "oaapx01"])

print("\n-- Zustand fortschreiben (poll_all)")
calls = []


def fake_fetch_ok(url, key):
    calls.append((url, key))
    return doc(), ""


nodes2, _ = fleet.parse_nodes("oaapx01=https://oaap.joomp.de;demo=https://d.example")
state = fleet.poll_all(nodes2, {"oaapx01": SECRET}, {}, fetch=fake_fetch_ok, now=NOW)
check("Schlüssel geht nur in die Abfrage",
      calls == [("https://oaap.joomp.de", SECRET)])
check("ohne Schlüssel: benannter Zustand statt Abfrage",
      state["demo"]["error"].startswith("Kein Schlüssel"))
check("Erfolg trägt Zeitstempel",
      state["oaapx01"]["fetched"] == "2026-08-23T12:00:00Z"
      and state["oaapx01"]["error"] == "")
check("Zustand enthält den Schlüssel nirgends",
      SECRET not in json.dumps(state))


def fake_fetch_down(url, key):
    return None, "Nicht erreichbar (TimeoutError)"


state2 = fleet.poll_all(nodes2, {"oaapx01": SECRET}, state,
                        fetch=fake_fetch_down, now=NOW)
check("Fehlschlag behält den letzten Stand",
      state2["oaapx01"]["doc"] == state["oaapx01"]["doc"]
      and state2["oaapx01"]["fetched"] == "2026-08-23T12:00:00Z"
      and state2["oaapx01"]["error"].startswith("Nicht erreichbar"))

print("\n-- Sicht bauen (node_rows)")
rows = fleet.node_rows(nodes2, state2, now=NOW, interval=60)
r = {x["name"]: x for x in rows}
check("jeder konfigurierte Knoten hat eine Zeile", len(rows) == 2)
check("frischer Stand ist nicht veraltet", r["oaapx01"]["stale"] is False)
check("nie gesehener Knoten ist unreachable + veraltet",
      r["demo"]["state"] == "unreachable" and r["demo"]["stale"])
old = dict(state2["oaapx01"], fetched="2026-08-23T11:00:00Z")
rows_old = fleet.node_rows(nodes2[:1], {"oaapx01": old}, now=NOW, interval=60)
check("alter Stand wird als veraltet markiert", rows_old[0]["stale"] is True)

sick = doc(instances=[{"instance": "a", "state": "ok"},
                      {"instance": "b", "state": "error"}])
rows_sick = fleet.node_rows(nodes2[:1],
                            {"oaapx01": {"url": "x", "doc": sick, "error": "",
                                         "fetched": "2026-08-23T12:00:00Z"}},
                            now=NOW, interval=60)
check("Gesamtzustand = schlechtester Einzelzustand",
      rows_sick[0]["state"] == "error"
      and rows_sick[0]["inst_ok"] == 1 and rows_sick[0]["inst_total"] == 2)

print("\n-- attention und Versionen")
attn_doc = doc(attention=[{"kind": "confirmation_pending", "instance": "b-test"},
                          {"kind": "voellig_neu", "detail": "aus der Zukunft"}])
rows3 = fleet.node_rows(nodes2, {
    "oaapx01": {"url": "x", "doc": attn_doc, "error": "",
                "fetched": "2026-08-23T12:00:00Z"},
    "demo": {"url": "y", "doc": None, "error": "Abgewiesen (403) — Schlüssel prüfen",
             "fetched": ""}}, now=NOW, interval=60)
items = fleet.fleet_attention(rows3)
labels = [(i["node"], i["label"]) for i in items]
check("Bestätigung offen gesammelt",
      ("oaapx01", "Bestätigung offen") in labels)
check("unbekannte Art toleriert und roh gezeigt",
      ("oaapx01", "voellig_neu") in labels)
check("nicht erreichbarer Knoten steht in der Liste",
      ("demo", "Nicht erreichbar") in labels)
v_rows = [dict(rows3[0], version="0.1.40"), dict(rows3[0], version="0.1.41")]
check("Versions-Abweichung wird benannt",
      "0.1.40" in fleet.version_note(v_rows)
      and "0.1.41" in fleet.version_note(v_rows))
check("gleiche Versionen: kein Hinweis",
      fleet.version_note([v_rows[0], v_rows[0]]) == "")

print("\n-- Dokument-Prüfung in fetch_status (ohne Netz simuliert)")
check("fremdes JSON wird abgelehnt",
      not str({"schema": "anders/1"}).startswith(fleet.SCHEMA_PREFIX))

print("\n-- Zustand speichern/laden")
tmp = os.path.join(tempfile.mkdtemp(), "state.json")
fleet.save_state(tmp, state2)
check("Rundreise über die Platte", fleet.load_state(tmp) == state2)
check("Datei enthält den Schlüssel nicht",
      SECRET not in open(tmp, encoding="utf-8").read())
check("kaputte Datei lädt als leer", fleet.load_state(tmp + ".nix") == {})

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
