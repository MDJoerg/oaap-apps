#!/usr/bin/env python3
"""Tresor, Übernahme und Laden der Bezugsquellen (RFC-0064 Stufe 2).

Die Fälle, die test_gateway.py nicht spielen kann, weil sie einen
anderen Start brauchen: kein Hauptschlüssel, ein falscher, ein
manipulierter Tresor, eine App, die schon selbst pflegt. Ohne HTTP,
ohne Netz — jeder Fall mit einer eigenen SQLite-Datei.

Run: python3 apps/ai-gateway/test_catalog.py
"""
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import catalog  # noqa: E402
import store  # noqa: E402
import vault  # noqa: E402

ok = fail = 0
TMP = tempfile.mkdtemp(prefix="aigw-catalog-")
MASTER = "hauptschluessel-fuer-den-test-0123456789"
ANDERER = "ein-ganz-anderer-hauptschluessel-98765"
ENV = {
    "AIGW_SUPPLIERS": "lokal=http://ollama:11434/v1 light=green\n"
                      "fern=https://api.example.com/v1 light=red\n"
                      "kaputt=ftp://nein",
    "AIGW_SUPPLIER_KEYS": "fern=sk-anbieter-geheim",
    "AIGW_ALIASES": "chat = fern:groß, lokal:klein\nnur-lokal = lokal:klein",
}


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


def fresh(name):
    return store.connect(os.path.join(TMP, name, "gateway.db"))


print("-- Tresor")
token = vault.seal(MASTER, "sk-anbieter-geheim")
check("hin und zurück", vault.open_(MASTER, token) == "sk-anbieter-geheim")
check("zweimal derselbe Text ergibt verschiedene Tresorinhalte",
      vault.seal(MASTER, "x") != vault.seal(MASTER, "x"))
check("der Klartext steht nicht im Tresorinhalt", "geheim" not in token)
for label, args in (("falscher Hauptschlüssel", (ANDERER, token)),
                    ("ein verändertes Zeichen", (MASTER, token[:-6] + ("A" if token[-6] != "A" else "B") + token[-5:])),
                    ("zu kurzer Hauptschlüssel", ("kurz", token)),
                    ("fremdes Format", (MASTER, "v9:abc"))):
    try:
        vault.open_(*args)
        check(f"{label} wird abgelehnt", False)
    except vault.VaultError:
        check(f"{label} wird abgelehnt, nicht falsch entschlüsselt", True)
try:
    vault.seal("", "x")
    check("ohne Hauptschlüssel wird nichts versiegelt", False)
except vault.VaultError:
    check("ohne Hauptschlüssel wird nichts versiegelt", True)

print("\n-- Übernahme: Zugangsdaten da, Hauptschlüssel nicht → warten")
db = fresh("gesperrt")
state, notes = catalog.import_config(db, ENV, "")
check("Zustand blocked", state == "blocked", state)
check("der Grund nennt AIGW_MASTER_KEY", notes and "AIGW_MASTER_KEY" in notes[0], notes)
check("nichts übernommen, nichts als erledigt markiert",
      catalog.empty(db) and not catalog.meta(db, "imported_at"))
state, _ = catalog.import_config(db, ENV, MASTER)
check("mit Hauptschlüssel klappt es beim nächsten Start", state == "imported", state)

print("\n-- Übernahme: ohne Zugangsdaten braucht es keinen Hauptschlüssel")
db = fresh("ohne-geheimnis")
state, _ = catalog.import_config(
    db, {"AIGW_SUPPLIERS": "lokal=http://ollama:11434/v1 light=green",
         "AIGW_ALIASES": "nur-lokal = lokal:klein"}, "")
check("übernommen", state == "imported", state)

print("\n-- Übernahme: der Normalfall")
db = fresh("normal")
state, notes = catalog.import_config(db, ENV, MASTER)
check("Zustand imported", state == "imported", state)
check("die unverständliche Zeile wird genannt, nicht still verschluckt",
      any("kaputt" in n for n in notes), notes)
suppliers, aliases, errors = catalog.load(db, MASTER)
check("geladen wie aus der Konfiguration",
      sorted(suppliers) == ["fern", "lokal"] and sorted(aliases) == ["chat", "nur-lokal"]
      and suppliers["fern"]["credential"] == "sk-anbieter-geheim"
      and suppliers["fern"]["light"] == "red", (suppliers, aliases, errors))
check("die Reihenfolge der Ziele bleibt",
      [t["supplier"] for t in aliases["chat"]["targets"]] == ["fern", "lokal"])
state, _ = catalog.import_config(db, ENV, MASTER)
check("ein zweiter Start übernimmt nicht noch einmal", state == "already", state)
with open(os.path.join(TMP, "normal", "gateway.db"), "rb") as fh:
    check("der Klartext steht nicht in der Datenbankdatei",
          b"sk-anbieter-geheim" not in fh.read())

print("\n-- Laden mit falschem Hauptschlüssel: benannt, nicht geraten")
suppliers, aliases, errors = catalog.load(db, ANDERER)
check("die Quelle mit unlesbaren Zugangsdaten fällt heraus",
      "fern" not in suppliers and "lokal" in suppliers, sorted(suppliers))
check("und wird benannt", any("fern" in e and "nicht lesbar" in e for e in errors), errors)
check("ein Modellname, der sie braucht, wird nicht stillschweigend verkürzt",
      "chat" not in aliases and "nur-lokal" in aliases, sorted(aliases))

print("\n-- Übernahme, wenn die App schon selbst pflegt")
db = fresh("schon-gepflegt")
catalog.set_supplier(db, "test", MASTER, "eigen", "http://x:1/v1", "green")
state, _ = catalog.import_config(db, ENV, MASTER)
check("die Variablen bleiben liegen", state == "skipped", state)
check("und überschreiben nichts", [r["name"] for r in catalog.supplier_rows(db)] == ["eigen"])

print("\n-- Schreiben ohne Hauptschlüssel")
db = fresh("ohne-schluessel")
try:
    catalog.set_supplier(db, "test", "", "fern", "https://x/v1", "red", credential="sk-1")
    check("Zugangsdaten ohne Hauptschlüssel werden abgelehnt", False)
except ValueError as exc:
    check("Zugangsdaten ohne Hauptschlüssel werden abgelehnt, mit Abhilfe",
          "AIGW_MASTER_KEY" in str(exc), str(exc))
check("und nichts gespeichert", catalog.empty(db))
catalog.set_supplier(db, "test", "", "lokal", "http://ollama:11434/v1", "green")
check("eine Quelle ohne Zugangsdaten geht trotzdem",
      [r["name"] for r in catalog.supplier_rows(db)] == ["lokal"])

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
