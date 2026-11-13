#!/usr/bin/env python3
"""Kohorten (0.6): eingefügte Tabelle lesen, Kennungen vergeben, Tabelle ergänzen.

Nur die Rechnung in `cohort.py` — ohne Gateway, ohne Netz. Das Ausstellen
selbst prüft test_gateway.py.

Run: python3 apps/ai-gateway/test_cohort.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cohort  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


print("-- Trennzeichen und Überschrift")
sep, head, rows = cohort.parse("Anna\nBernd\n\nCarla\n")
check("nur Namen: eine Spalte, Leerzeilen weg", sep is None and head is None
      and rows == [["Anna"], ["Bernd"], ["Carla"]], rows)
sep, head, rows = cohort.parse("Name\tNr\tE-Mail\r\nAnna Beispiel\t03\ta@x.de\r\nBernd\t\tb@x.de\r\n")
check("aus Excel: Tab, Überschrift erkannt", sep == "\t" and head == ["Name", "Nr", "E-Mail"]
      and rows[0] == ["Anna Beispiel", "03", "a@x.de"], (sep, head, rows))
sep, head, rows = cohort.parse('Name;Firma\n"Muster; Bernd";ACME\n')
check("Semikolon mit Anführungszeichen", sep == ";" and rows == [["Muster; Bernd", "ACME"]], rows)
sep, head, rows = cohort.parse("Anna,07\nBernd,08\n")
check("Komma, keine Überschrift", sep == "," and head is None and len(rows) == 2)
sep, head, rows = cohort.parse("Name,07\n")
check("„Name,07“ ist eine Person mit Kennung, keine Überschrift", head is None and rows == [["Name", "07"]])

print("\n-- Kennungen")
check("7 → 07, 07B → 07b, Unsinn → None",
      cohort.normalize_kennung("7") == "07" and cohort.normalize_kennung("07B") == "07b"
      and cohort.normalize_kennung("x7") is None and cohort.normalize_kennung("123") is None)
plan, gen = cohort.plan([["Anna"], ["Bernd"], ["Carla"]], "kurs-2026-11", set())
check("fortlaufend ab 01, Etikett wie bei den Schulungsservern",
      [p["label"] for p in plan] == ["kurs-2026-11-tn-01", "kurs-2026-11-tn-02", "kurs-2026-11-tn-03"]
      and gen, plan)
plan, gen = cohort.plan([["Anna"], ["Bernd"]], "k", {"k-tn-00"}, first=0)
check("erste Nummer 00, vergebene wird übersprungen",
      [p["kennung"] for p in plan] == ["01", "02"], plan)
plan, gen = cohort.plan([["Anna", "07"], ["Bernd", "8"]], "k", set())
check("angegebene Kennungen gelten, keine eigene Spalte nötig",
      [p["kennung"] for p in plan] == ["07", "08"] and not gen, (plan, gen))
plan, gen = cohort.plan([["Anna", "07"]], "k", {"k-tn-07"})
check("vergebene Kennung ohne „nächste freie“: Fehler mit Vorschlag",
      plan[0]["error"] and "07b" in plan[0]["error"] and not plan[0]["label"], plan)
plan, gen = cohort.plan([["Anna", "07"]], "k", {"k-tn-07", "k-tn-07b"}, auto_free=True)
check("mit „nächste freie“: 07 → 07c, und die Ausgabe zeigt die geänderte Kennung",
      plan[0]["kennung"] == "07c" and gen, plan)
plan, gen = cohort.plan([["Anna", "07b"]], "k", set())
check("abweichende Kennung 07b direkt angegeben", plan[0]["label"] == "k-tn-07b")
plan, gen = cohort.plan([["Anna"], ["Bernd", "02"], ["Carla"]], "k", set())
check("gemischt: angegebene 02 wird beim Zählen nicht doppelt vergeben",
      [p["kennung"] for p in plan] == ["01", "02", "03"], plan)
plan, gen = cohort.plan([[""], ["Bernd"]], "k", set())
check("Zeile ohne Namen: Fehler, Rest läuft", plan[0]["error"] and plan[1]["kennung"] == "01")
plan, gen = cohort.plan([[f"P{i}"] for i in range(101)], "k", set(), first=0)
check("mehr als 100 Zeilen: der Rest bekommt einen Grund statt still zu fehlen",
      sum(1 for p in plan if p["label"]) == 100 and plan[100]["error"])
check("Name der Kohorte: kurs-2026-11 ja, Kurs 1 nein",
      cohort.COHORT_NAME.match("kurs-2026-11") and not cohort.COHORT_NAME.match("Kurs 1"))

print("\n-- Ausgabe: dieselbe Tabelle plus Schlüssel")
sep, head, rows = cohort.parse("Name\tE-Mail\nAnna\ta@x.de\nBernd\n")
plan, gen = cohort.plan(rows, "k", set())
text = cohort.output(sep, head, plan, {"k-tn-01": "sk-1", "k-tn-02": "sk-2"}, gen)
check("Überschrift + Kennung + Schlüssel, Tab bleibt Tab, kurze Zeile aufgefüllt",
      text == "Name\tE-Mail\tKennung\tSchlüssel\nAnna\ta@x.de\t01\tsk-1\nBernd\t\t02\tsk-2\n", repr(text))
text = cohort.output(None, None, plan, {"k-tn-01": "sk-1"}, gen)
check("nur Namen: Tab als Trenner, fehlender Schlüssel mit Grund",
      text.splitlines()[1].endswith("(nicht ausgestellt: Fehler)") and "\t" in text, repr(text))
sep, head, rows = cohort.parse("Anna;07\n")
plan, gen = cohort.plan(rows, "k", set())
check("Semikolon bleibt Semikolon, ohne Kennungsspalte",
      cohort.output(sep, head, plan, {"k-tn-07": "sk-7"}, gen) == "Anna;07;sk-7\n")

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
