#!/usr/bin/env python3
"""Die Bearbeitungsregeln des Store Editors (RFC-0013 Bauschritt 2).

Läuft ohne Netz, ohne Docker, ohne Knoten. Was hier festgehalten wird,
sind Entscheidungen aus RFC-0012 §1.3 und RFC-0013 — wer eine davon
ändern will, ändert zuerst den RFC.

    python3 test_editor.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import editor as ed  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail}")


MANIFEST = {
    "oaap_manifest": "0.2",
    "app": {"id": "ollama", "name": "Ollama", "version": "0.9.2",
            "type": "wrapped", "class": "service"},
    "routes": [{"path": "/", "roles": ["keyuser", "admin"]}],
}
ENTRY = {"id": "ollama", "name": "Ollama", "version": "0.9.1",
         "type": "wrapped", "app_class": "service",
         "roles": ["admin", "keyuser"], "summary": "Sprachmodelle lokal.",
         "package": {"git": "https://github.com/MDJoerg/oaap-store",
                     "path": "apps/ollama"}}


print("=== Neuerzeugung: die 80-%-Regel in Code ===")
e = dict(ENTRY)
ch = ed.regenerate_entry(e, MANIFEST)
ok("eine veraltete Version wird nachgezogen", e["version"] == "0.9.2", str(ch))
ok("und die Änderung nennt beide Stände",
   ch and ch[0]["before"] == "0.9.1" and ch[0]["after"] == "0.9.2", str(ch))
ok("was schon stimmt, erzeugt keine Änderung",
   not [c for c in ch if c["field"] in ("name", "type", "app_class")], str(ch))

e = dict(ENTRY)
ok("gedrehte Rollen sind kein Unterschied",
   not [c for c in ed.regenerate_entry(e, MANIFEST) if c["field"] == "roles"],
   str(e.get("roles")))

print("\n=== die markierte Übersteuerung (RFC-0012 §1.3) ===")
# Ohne sie nähme jede Neuerzeugung eine bewusste redaktionelle
# Entscheidung stillschweigend zurück. Das ist der ganze Grund,
# warum es die Markierung gibt.
e = {**ENTRY, "name": "Ollama (bei uns)"}
ch = ed.regenerate_entry(e, MANIFEST, overridden=("name",))
ok("ein markiertes Feld wird NICHT überschrieben",
   e["name"] == "Ollama (bei uns)", str(e))
ok("...aber die Neuerzeugung sagt, dass sie es gelassen hat",
   any(c["field"] == "name" and c["held"] for c in ch), str(ch))
e2 = {**ENTRY, "name": "Ollama (bei uns)"}
ed.regenerate_entry(e2, MANIFEST)
ok("ohne Markierung wird dieselbe Änderung überschrieben",
   e2["name"] == "Ollama", str(e2))

print("\n=== nur diese fünf Felder kann das Manifest belegen ===")
# `released`, `profiles`, `icon` und `package` stehen in RFC-0012 §1.3
# unter „erzeugt", ohne dass es eine Quelle dafür gäbe. Sie hier als
# verriegelt darzustellen wäre eine Unwahrheit in der Oberfläche.
ok("released/profiles/icon/package sind nicht neuerzeugbar",
   not set(ed.REGENERABLE) & set(ed.UNGENERATED), str(ed.REGENERABLE))
e = {**ENTRY, "released": "2026-01-01", "icon": "icons/o.svg", "profiles": ["dev"]}
before = (e["released"], e["icon"], list(e["profiles"]))
ed.regenerate_entry(e, MANIFEST)
ok("und eine Neuerzeugung fasst sie nicht an",
   (e["released"], e["icon"], e["profiles"]) == before, str(e))
ok("die Beschreibung ist Saatgut und damit redaktionell",
   "description" in ed.EDITORIAL and "description" not in ed.REGENERABLE)

print("\n=== leer heißt weglassen, nicht 'leer behaupten' ===")
e = {**ENTRY}
ed.apply_values(e, {"summary": ""})
ok("ein geleertes Feld verschwindet aus dem Eintrag", "summary" not in e, str(e))
e = {**ENTRY}
ch = ed.apply_values(e, {"summary": ENTRY["summary"]})
ok("ein unveränderter Wert ist keine Änderung", not ch, str(ch))
e = {}
ok("ein Feld, das es nie gab, wird durch Leeren nicht zur Änderung",
   not ed.apply_values(e, {"summary": ""}) and e == {})

print("\n=== Unterschied zur veröffentlichten Liste ===")
PUB = {"store": "0.2", "apps": [ENTRY]}
work = {"store": "0.2", "apps": [{**ENTRY, "version": "0.9.2",
                                 "summary": "Neu getextet."}]}
d = ed.diff_documents(PUB, work)
kinds = {c["field"]: c["kind"] for c in d}
ok("eine nachgezogene Version zählt als erzeugt",
   kinds.get("version") == ed.ERZEUGT, str(d))
ok("ein neuer Text zählt als redaktionell",
   kinds.get("summary") == ed.REDAKTIONELL, str(d))
# RFC-0013 Entscheidung 5: die Mengenbremse zählt Neuerzeugung NICHT
# mit — sonst feuert sie bei jedem Lauf und wird weggeklickt.
c = ed.count_kinds(d)
ok("und die Zählung trennt beides",
   c[ed.ERZEUGT] == 1 and c[ed.REDAKTIONELL] == 1 and c[ed.STRUKTUR] == 0, str(c))

umzug = {"store": "0.2", "apps": [{**ENTRY,
                                   "package": {"git": "https://github.com/fremd/x"}}]}
d = ed.diff_documents(PUB, umzug)
ok("ein Paket, das umzieht, ist strukturell — nicht redaktionell",
   d and d[0]["kind"] == ed.STRUKTUR and d[0]["field"] == "package",
   "die Kennung bleibt, der Zeiger wandert: die Form, die ein Versehen "
   "und eine Übernahme gemeinsam haben")

d = ed.diff_documents(PUB, {"store": "0.2", "apps": []})
ok("ein entfernter Eintrag wird gemeldet",
   len(d) == 1 and d[0]["kind"] == ed.STRUKTUR and d[0]["app"] == "ollama", str(d))
d = ed.diff_documents({"store": "0.2", "apps": []}, PUB)
ok("ein neuer Eintrag ebenso", len(d) == 1 and d[0]["kind"] == ed.STRUKTUR, str(d))
ok("eine unveränderte Liste ergibt keinen Unterschied",
   not ed.diff_documents(PUB, {"store": "0.2", "apps": [dict(ENTRY)]}))

print("\n=== Verweise: nichts darf verlorengehen ===")
LINKS = [{"rel": "source", "url": "https://a"},
         {"rel": "sponsor", "url": "https://b", "label": "Spenden"}]
known, rest = ed.split_links(LINKS)
ok("eine bekannte Beziehung bekommt ihr eigenes Feld",
   known["source"]["url"] == "https://a", str(known))
ok("eine unbekannte landet im Freitext statt im Papierkorb",
   "sponsor | https://b | Spenden" in rest, rest)
back = ed.merge_links({k: v["url"] for k, v in known.items()}, rest)
ok("und beides kommt unverändert zurück", back == LINKS, str(back))
ok("ein leeres Feld erzeugt keinen Verweis",
   ed.merge_links({"homepage": "  "}, "") == [])

print("\n=== zeilenweise Felder ===")
ok("Bildschirmfotos: Pfad und Bildunterschrift",
   ed.parse_pairs("a.png | Übersicht", ("src", "caption"))
   == [{"src": "a.png", "caption": "Übersicht"}])
ok("eine Zeile ohne Trenner ist nur der erste Wert",
   ed.parse_pairs("a.png", ("src", "caption")) == [{"src": "a.png"}])
ok("leere Zeilen fallen weg", ed.parse_pairs("\n\n a.png \n\n", ("src",))
   == [{"src": "a.png"}])
ok("Schlagwörter: Komma oder Zeile, ohne Doppelte",
   ed.parse_words("ki, lokal\nki\n llm ") == ["ki", "lokal", "llm"])

print("\n=== ein Eintrag vor seinem Manifest (Entscheidung 4) ===")
e = ed.new_entry("bdt-hub", "https://github.com/x/bdt", "apps/hub", "v1.0.0")
ok("er trägt nur Kennung und Zeiger aufs Paket",
   e["id"] == "bdt-hub" and e["package"]["ref"] == "v1.0.0"
   and e["package"]["path"] == "apps/hub", str(e))
ok("er ist strukturell gültig — nur ohne Beleg",
   not [f for f in __import__("checker").check_structure(
        {"store": "0.2", "apps": [e]}) if f["level"] == "fehler"],
   "sonst würde die Entscheidung 'ein Eintrag darf vor seinem Manifest "
   "entstehen' vom eigenen Prüfer kassiert")
ok("eine unzulässige Kennung wird abgelehnt",
   ed.check_new_id(PUB, "Ollama Groß"), "")
ok("eine schon vergebene ebenso",
   "schon" in ed.check_new_id(PUB, "ollama"))
ok("eine saubere neue Kennung geht durch", not ed.check_new_id(PUB, "uptime-kuma"))

print("\n=== Werte lesbar machen ===")
ok("Listen werden aufgezählt", ed.as_text(["a", "b"]) == "a, b")
ok("Objekte werden nicht als JSON hingeworfen",
   "github" in ed.as_text({"git": "https://github.com/x", "path": "y"}))
ok("nichts bleibt nichts", ed.as_text(None) == "" and ed.as_text([]) == "")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
