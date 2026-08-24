#!/usr/bin/env python3
"""Bezugsquellen und Aliasse — Konfiguration lesen, Reihenfolge bilden.

Geprüft wird, was Entscheidungen trifft: Fehler werden **benannt statt
verschluckt** (eine App, die wegen eines Tippfehlers nicht startet, ist
schlimmer als eine, die sagt, was sie nicht verstanden hat), und die
Reihenfolge innerhalb einer Ausweich-Gruppe bevorzugt souveräne Quellen,
ohne dass jemand etwas einstellen muss.

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
ollama=http://ollama:11434/v1 class=internal
tsystems=https://hub.example.eu/v1 class=eu
groq=https://api.groq.com/openai/v1 class=external
"""

print("-- Bezugsquellen lesen")
src, errs = supply.parse_suppliers(SUPPLIER_TEXT, f"groq={SECRET}")
check("drei Quellen erkannt", len(src) == 3, list(src))
check("keine Fehler", errs == [], errs)
check("Klasse übernommen", src["tsystems"]["class"] == "eu")
check("Adresse ohne Schrägstrich am Ende", src["ollama"]["url"].endswith("/v1"))
check("Zugangsdaten zugeordnet", src["groq"]["credential"] == SECRET)
check("Quelle ohne Zugangsdaten ist erlaubt", src["ollama"]["credential"] == "")

print("\n-- Fehler werden benannt, nicht verschluckt")
_, errs = supply.parse_suppliers("kaputt", "")
check("Zeile ohne name=adresse", any("name=adresse" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=ftp://x", "")
check("fremdes Schema abgelehnt", any("http" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x class=mond", "")
check("unbekannte Klasse benannt", any("mond" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x; a=https://y", "")
check("Doppelung benannt", any("doppelt" in e for e in errs), errs)
_, errs = supply.parse_suppliers("a=https://x", "b=geheim")
check("Zugangsdaten ohne Quelle benannt", any("unbekannte Bezugsquelle" in e for e in errs), errs)
check("Schlüsselwert steht in keinem Fehlertext",
      not any("geheim" in e for e in errs), errs)

print("\n-- Voreinstellung: unbekannte Herkunft ist nicht souverän")
src2, _ = supply.parse_suppliers("x=https://irgendwo/v1", "")
check("ohne Klassenangabe gilt external", src2["x"]["class"] == "external")

print("\n-- Aliasse lesen")
aliases, errs = supply.parse_aliases(
    "chat-default = groq:llama-70b, tsystems:llama-3.3\n"
    "embedding-default = ollama:nomic-embed-text\n"
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

print("\n-- Reihenfolge: souverän zuerst, ohne dass jemand etwas einstellt")
alle = ["internal", "eu", "external"]
order = [c["supplier"]["name"] for c in supply.candidates(aliases["chat-default"], src, alle)]
check("eu vor external, obwohl external zuerst aufgeführt war",
      order == ["tsystems", "groq"], order)
order = [c["supplier"]["name"] for c in supply.candidates(aliases["code"], src, alle)]
check("order=listed behält die erklärte Reihenfolge", order == ["tsystems", "groq"], order)

print("\n-- Klassen begrenzen die Gruppe, nicht die Reihenfolge")
order = [c["supplier"]["name"] for c in supply.candidates(
    aliases["chat-default"], src, ["internal", "eu"])]
check("external fällt heraus", order == ["tsystems"], order)
order = supply.candidates(aliases["embedding-default"], src, ["eu"])
check("kein erlaubtes Ziel = leere Liste", order == [], order)
check("und die Ursache ist benennbar",
      supply.blocked_classes(aliases["embedding-default"], src, ["eu"]) == ["internal"])
check("Voreinstellung eines Schlüssels ist internal+eu",
      supply.DEFAULT_CLASSES == ("internal", "eu"))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
