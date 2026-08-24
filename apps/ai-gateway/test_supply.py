#!/usr/bin/env python3
"""Bezugsquellen, Aliasse und die Ampel — Konfiguration und Reihenfolge.

Geprüft wird, was Entscheidungen trifft: Fehler werden **benannt statt
verschluckt** (eine App, die wegen eines Tippfehlers nicht startet, ist
schlimmer als eine, die sagt, was sie nicht verstanden hat), die
Reihenfolge bevorzugt die ungefährlichere Quelle, ohne dass jemand
etwas einstellen muss — und die Ampel eines Alias ist die
**schlechteste** seiner erreichbaren Ziele, nicht die freundlichste.

Run: python3 apps/ai-gateway/test_supply.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import supply  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


SECRET = "gsk-geheim-nie-sichtbar"

SUPPLIER_TEXT = """
ollama=http://ollama:11434/v1 light=green
tsystems=https://hub.example.eu/v1 light=yellow
groq=https://api.groq.com/openai/v1 light=red
"""

print("-- Bezugsquellen lesen")
src, errs = supply.parse_suppliers(SUPPLIER_TEXT, f"groq={SECRET}")
check("drei Quellen erkannt", len(src) == 3, list(src))
check("keine Fehler", errs == [], errs)
check("Ampelfarbe übernommen", src["tsystems"]["light"] == "yellow")
check("Adresse ohne Schrägstrich am Ende", src["ollama"]["url"].endswith("/v1"))
check("Zugangsdaten zugeordnet", src["groq"]["credential"] == SECRET)
check("Quelle ohne Zugangsdaten ist erlaubt", src["ollama"]["credential"] == "")

print("\n-- Fehler werden benannt, nicht verschluckt")
_, errs = supply.parse_suppliers("kaputt", "")
check("Zeile ohne name=adresse", any("name=adresse" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=ftp://x", "")
check("fremdes Schema abgelehnt", any("http" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x light=blau", "")
check("unbekannte Farbe benannt", any("blau" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x; a=https://y", "")
check("Doppelung benannt", any("doppelt" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x", "b=geheim")
check("Zugangsdaten ohne Quelle benannt", any("unbekannte Bezugsquelle" in e for e in errs), errs)
check("Schlüsselwert steht in keinem Fehlertext",
      not any("geheim" in e for e in errs), errs)

print("\n-- Voreinstellungen: der sichere Zustand ist der Standardzustand")
src2, _ = supply.parse_suppliers("x=https://irgendwo/v1", "")
check("Quelle ohne Angabe gilt als rot", src2["x"]["light"] == "red")
check("ein neuer Schlüssel darf bis gelb", supply.DEFAULT_CEILING == "yellow")

print("\n-- Die schlechtere Farbe gewinnt (die Regel, die Ketten ehrlich hält)")
check("grün vs. rot ist rot", supply.worse("green", "red") == "red")
check("gelb vs. grün ist gelb", supply.worse("yellow", "green") == "yellow")
check("gleiche Farbe bleibt", supply.worse("yellow", "yellow") == "yellow")
check("Obergrenze gelb erlaubt grün", supply.allows("yellow", "green"))
check("Obergrenze gelb erlaubt gelb", supply.allows("yellow", "yellow"))
check("Obergrenze gelb verbietet rot", not supply.allows("yellow", "red"))

print("\n-- Aliasse lesen")
aliases, errs = supply.parse_aliases(
    "chat-default = groq:llama-70b, tsystems:llama-3.3\n"
    "nur-lokal = ollama:nomic-embed-text\n"
    "code = tsystems:qwen, groq:qwen order=listed", src)
check("drei Aliasse", len(aliases) == 3, list(aliases))
check("keine Fehler", errs == [], errs)
check("order=listed erkannt", aliases["code"]["order_listed"] is True)
check("ohne Angabe nicht listed", aliases["chat-default"]["order_listed"] is False)

_, errs = supply.parse_aliases("a = nichtda:modell", src)
check("unbekannte Quelle benannt", any("nicht konfiguriert" in e for e in errs), errs)
_, errs = supply.parse_aliases("a = ollama", src)
check("fehlendes Modell benannt", any("quelle:modell" in e for e in errs), errs)
_, errs = supply.parse_aliases("a =", src)
check("leerer Alias benannt", errs != [], errs)

print("\n-- Reihenfolge: das Ungefährlichere zuerst, ohne Einstellung")
order = [c["supplier"]["name"] for c in supply.candidates(aliases["chat-default"], src, "red")]
check("gelb vor rot, obwohl rot zuerst aufgeführt war",
      order == ["tsystems", "groq"], order)
order = [c["supplier"]["name"] for c in supply.candidates(aliases["code"], src, "red")]
check("order=listed behält die erklärte Reihenfolge", order == ["tsystems", "groq"], order)

print("\n-- Die Obergrenze begrenzt die Gruppe")
order = [c["supplier"]["name"] for c in supply.candidates(aliases["chat-default"], src, "yellow")]
check("rot fällt heraus", order == ["tsystems"], order)
check("kein erreichbares Ziel = leere Liste",
      supply.candidates(aliases["nur-lokal"], src, "green") != [], "grün erreicht grün")
check("Obergrenze unter der Quelle sperrt alles",
      supply.candidates(aliases["chat-default"], src, "green") == [])
check("und die Ursache ist benennbar",
      any("Obergrenze" in r for r in
          supply.blocked_reasons(aliases["chat-default"], src, "green")),
      supply.blocked_reasons(aliases["chat-default"], src, "green"))

print("\n-- Freigabe für personenbezogene Daten schließt rot aus")
order = [c["supplier"]["name"] for c in
         supply.candidates(aliases["chat-default"], src, "red", personal_data=True)]
check("rot entfällt trotz Obergrenze rot", order == ["tsystems"], order)
only_red, _ = supply.parse_aliases("nur-rot = groq:x", src)
check("ein rein roter Alias ist für so einen Schlüssel leer",
      supply.candidates(only_red["nur-rot"], src, "red", personal_data=True) == [])
check("und sagt auch warum",
      any("personenbezogen" in r for r in
          supply.blocked_reasons(only_red["nur-rot"], src, "red", personal_data=True)),
      supply.blocked_reasons(only_red["nur-rot"], src, "red", personal_data=True))

print("\n-- Die Ampel eines Alias ist die schlechteste seiner Ziele")
check("grün + rot ergibt rot",
      supply.alias_light(aliases["chat-default"], src, "red") == "red")
check("dieselbe Gruppe mit Obergrenze gelb ist gelb",
      supply.alias_light(aliases["chat-default"], src, "yellow") == "yellow")
check("ein rein grüner Alias bleibt grün",
      supply.alias_light(aliases["nur-lokal"], src, "red") == "green")
check("ohne erreichbares Ziel gibt es keine Farbe",
      supply.alias_light(aliases["chat-default"], src, "green") == "")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
