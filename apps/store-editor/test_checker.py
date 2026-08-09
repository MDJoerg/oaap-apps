#!/usr/bin/env python3
"""Die Prüfregeln des Store Editors (RFC-0013 Bauschritt 1).

Läuft ohne Netz, ohne Docker, ohne Knoten: Das Abrufen wird
hereingereicht. Was hier festgehalten wird, sind Entscheidungen aus
RFC-0012 und RFC-0013 — wer eine davon ändern will, ändert zuerst den
RFC.

    python3 test_checker.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import checker as c  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail}")


def levels(findings, field=None, app=None):
    return [f["level"] for f in findings
            if (field is None or f["field"] == field)
            and (app is None or f["app"] == app)]


MANIFEST = {
    "oaap_manifest": "0.2",
    "app": {"id": "ollama", "name": "Ollama", "version": "0.9.1",
            "type": "wrapped", "class": "service"},
    "routes": [{"path": "/", "roles": ["keyuser", "admin"]}],
}
ENTRY = {"id": "ollama", "name": "Ollama", "version": "0.9.1",
         "type": "wrapped", "app_class": "service",
         "roles": ["keyuser", "admin"],
         "package": {"git": "https://github.com/MDJoerg/oaap-store",
                     "path": "apps/ollama"}}


print("=== der Fall, für den es das Werkzeug gibt ===")
# Ollama stand am 09.08.2026 als Hintergrunddienst in der Liste, sein
# Manifest sagte davon nichts. Wochenlang unsichtbar.
stumm = {**MANIFEST, "app": {k: v for k, v in MANIFEST["app"].items() if k != "class"}}
f = c.compare_entry(ENTRY, stumm)
ok("eine Behauptung ohne Deckung im Manifest wird gemeldet",
   c.HINWEIS in levels(f, "app_class"), str(f))
ok("...aber als Hinweis, nicht als Widerspruch",
   c.BEFUND not in levels(f, "app_class"),
   "das Manifest sagt nichts — das ist etwas anderes als 'sagt etwas anderes'")

widerspruch = {**MANIFEST, "app": {**MANIFEST["app"], "class": "frontend"}}
f = c.compare_entry(ENTRY, widerspruch)
ok("ein echter Widerspruch ist ein Befund", c.BEFUND in levels(f, "app_class"), str(f))

print("\n=== Versionsdrift, der zweite Alltagsfall ===")
alt = {**MANIFEST, "app": {**MANIFEST["app"], "version": "0.9.2"}}
f = c.compare_entry(ENTRY, alt)
ok("eine Liste, die hinter dem Paket herhinkt, wird gemeldet",
   c.BEFUND in levels(f, "version"), str(f))
ok("und die Meldung nennt beide Seiten",
   [x for x in f if x["field"] == "version"][0]["manifest"] == "0.9.2"
   and [x for x in f if x["field"] == "version"][0]["list"] == "0.9.1")

print("\n=== Rollen sind eine Menge, keine Reihenfolge ===")
# Beim Ableiten der Regeln an den echten Listen war genau das die erste
# falsche Meldung: ['keyuser','admin'] gegen sortiert ['admin','keyuser'].
gedreht = {**ENTRY, "roles": ["admin", "keyuser"]}
ok("andere Reihenfolge ist kein Unterschied",
   not levels(c.compare_entry(gedreht, MANIFEST), "roles"),
   str(c.compare_entry(gedreht, MANIFEST)))
fehlt = {**ENTRY, "roles": ["keyuser"]}
ok("eine fehlende Rolle dagegen schon",
   c.BEFUND in levels(c.compare_entry(fehlt, MANIFEST), "roles"))

print("\n=== was NICHT verglichen wird, und warum ===")
# Die Liste trägt absichtlich den langen redaktionellen Text, das
# Manifest den kurzen. Ein Vergleich wäre bei allen acht echten Apps
# eine Falschmeldung.
lang = {**ENTRY, "description": "Ein langer, redaktionell gepflegter Text."}
m_kurz = {**MANIFEST, "app": {**MANIFEST["app"], "description": "kurz"}}
ok("die Beschreibung ist Saatgut, kein Vergleichsfeld",
   not levels(c.compare_entry(lang, m_kurz), "description"))
mit_profil = {**ENTRY, "profiles": ["dev"], "released": "2026-08-09"}
ok("profiles und released werden nicht verglichen (das Manifest kennt sie nicht)",
   not levels(c.compare_entry(mit_profil, MANIFEST), "profiles")
   and not levels(c.compare_entry(mit_profil, MANIFEST), "released"))

print("\n=== ein Paket, das nicht zum Eintrag gehört ===")
fremd = {**MANIFEST, "app": {**MANIFEST["app"], "id": "etwas-anderes"}}
ok("abweichende Kennung im Manifest ist ein Fehler, kein Hinweis",
   c.FEHLER in levels(c.compare_entry(ENTRY, fremd), "id"))

print("\n=== woher das Manifest geholt wird ===")
u, why = c.raw_manifest_url({"git": "https://github.com/MDJoerg/oaap-store",
                             "path": "apps/ollama"})
ok("GitHub ohne ref zeigt auf main",
   u == "https://raw.githubusercontent.com/MDJoerg/oaap-store/main/apps/ollama/oaap-app.yaml", u)
u, _ = c.raw_manifest_url({"git": "https://github.com/MDJoerg/oaap-store.git",
                           "path": "apps/ollama", "ref": "v1.2.3"})
ok("ein angehefteter Stand wird geehrt, .git abgeschnitten",
   u.endswith("/oaap-store/v1.2.3/apps/ollama/oaap-app.yaml"), u)
u, _ = c.raw_manifest_url({"git": "https://git.joomp.de/kuk/crm"})
ok("Forgejo/Gitea wird bedient", "/kuk/crm/raw/branch/main/oaap-app.yaml" in u, u)
u, why = c.raw_manifest_url({"git": "git@github.com:MDJoerg/oaap-store"})
ok("eine Adressform, die der Prüfer nicht kennt, wird gemeldet statt geraten",
   not u and why, f"{u!r} / {why!r}")
u, why = c.raw_manifest_url({})
ok("kein Paket, keine Adresse", not u and "kein Git-Repository" in why)

print("\n=== Struktur: die Regeln des Schemas ===")
GUT = {"store": "0.2", "name": "Test", "apps": [ENTRY]}
ok("eine saubere Liste ergibt keine Strukturmeldung", not c.check_structure(GUT),
   str(c.check_structure(GUT)))
ok("ohne Formatangabe ist es ein Fehler",
   c.FEHLER in levels(c.check_structure({"apps": []}), "store"))
ok("eine fremde Hauptversion wird abgelehnt",
   c.FEHLER in levels(c.check_structure({"store": "1.0", "apps": []}), "store"))
ok("ohne 'apps' ist es ein Fehler",
   c.FEHLER in levels(c.check_structure({"store": "0.2"}), "apps"))
doppelt = {"store": "0.2", "apps": [ENTRY, dict(ENTRY)]}
ok("dieselbe Kennung zweimal ist ein Fehler, kein Hinweis",
   c.FEHLER in levels(c.check_structure(doppelt), "id"),
   "welcher Eintrag gewinnt, wäre sonst Zufall")
ohne_paket = {"store": "0.2", "apps": [{**ENTRY, "package": {}}]}
ok("ein Eintrag ohne Paket ist nicht installierbar",
   c.FEHLER in levels(c.check_structure(ohne_paket), "package"))
http = {"store": "0.2", "apps": [{**ENTRY, "package": {"git": "http://example.invalid/x"}}]}
ok("http:// wird abgelehnt", c.FEHLER in levels(c.check_structure(http), "package"))
raus = {"store": "0.2", "apps": [{**ENTRY, "package": {**ENTRY["package"], "path": "../../etc"}}]}
ok("ein Pfad, der aus dem Repository führt, wird abgelehnt",
   c.FEHLER in levels(c.check_structure(raus), "package"))

print("\n=== Bilder nur aus dem Repository der Liste (§6) ===")
fremd_bild = {"store": "0.2", "apps": [{**ENTRY, "icon": "https://fremd.invalid/i.svg"}]}
ok("ein Bild von einem fremden Server wird abgelehnt",
   c.FEHLER in levels(c.check_structure(fremd_bild), "icon"),
   "sonst ruft jeder Knoten, der die Store-Seite öffnet, einen Server auf, "
   "den niemand ausgewählt hat")
shot = {"store": "0.2", "apps": [{**ENTRY, "screenshots": [{"src": "../geheim.png"}]}]}
ok("und ein Bildpfad, der aus dem Repository führt, ebenso",
   c.FEHLER in levels(c.check_structure(shot), "screenshots"))
gut_bild = {"store": "0.2", "apps": [{**ENTRY, "icon": "icons/ollama.svg"}]}
ok("ein relativer Pfad ist in Ordnung", not c.check_structure(gut_bild))

print("\n=== unbekanntes Vokabular ist ein Hinweis, kein Fehler ===")
# Ein Knoten toleriert es (RFC-0012 §8.1) — der Editor darf nicht so
# tun, als wäre die Liste kaputt.
exotisch = {"store": "0.2", "apps": [{**ENTRY, "app_class": "kuehlschrank",
                                      "categories": ["quantenphysik"]}]}
lv = [f["level"] for f in c.check_structure(exotisch)]
ok("unbekannte Klasse und Kategorie werden gemeldet", lv.count(c.HINWEIS) == 2, str(lv))
ok("aber nicht als Fehler", c.FEHLER not in lv)

print("\n=== die ganze Prüfung, mit hereingereichtem Abruf ===")
import json  # noqa: E402


def fake_fetch(url):
    if "oaap-store/main/apps/ollama" in url:
        return "yaml:ollama"
    raise OSError("nicht erreichbar")


def fake_yaml(text):
    return MANIFEST if text == "yaml:ollama" else None


rep = c.check_document(GUT, fetch=fake_fetch, load_yaml=fake_yaml)
ok("eine stimmige Liste ist in Ordnung", rep["ok"], str(rep["counts"]))
ok("und zählt, was sie tatsächlich geprüft hat",
   rep["entries"] == 1 and rep["checked"] == 1 and rep["unreachable"] == 0, str(rep))

tot = {"store": "0.2", "apps": [{**ENTRY, "id": "weg",
                                 "package": {"git": "https://github.com/x/tot"}}]}
rep = c.check_document(tot, fetch=fake_fetch, load_yaml=fake_yaml)
ok("ein unerreichbares Paket wird als ungeprüft gezählt",
   rep["unreachable"] == 1 and rep["checked"] == 0, str(rep))
ok("und die Meldung sagt, dass die Behauptung ohne Beleg dasteht",
   any("ohne" in f["text"] and "Beleg" in f["text"] for f in rep["findings"]),
   str(rep["findings"]))
ok("ein unerreichbares Paket allein macht die Liste nicht ungültig",
   rep["counts"][c.FEHLER] == 0,
   "RFC-0013 Entscheidung 4: ein Eintrag darf vor seinem Manifest entstehen")

print("\n=== ohne Abruf prüft es nur die Struktur ===")
rep = c.check_document(GUT)
ok("kein Netz, keine Manifest-Prüfung, aber ein Ergebnis",
   rep["checked"] == 0 and rep["entries"] == 1 and rep["ok"])

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
