#!/usr/bin/env python3
"""Zuschnitt einer Sicht (compose.py) und der Umzug einer Datenbank aus 0.1.

Run: python3 apps/mcp-gateway/test_compose.py
"""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import argcheck  # noqa: E402
import compose  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {str(detail)[:300]}")


def refused(fn, *args):
    try:
        fn(*args)
        return None
    except (argcheck.Refused, ValueError) as exc:
        return str(exc)


print("-- Muster mit * und ?")
for pat, text, want in (("Z*", "ZCL_A", True), ("Z*", "CL_A", False), ("Z*", "Z", True),
                        ("?CL_*", "ZCL_X", True), ("?CL_*", "CL_X", False), ("*", "", True),
                        ("a*b*c", "aXXbYYc", True), ("a*b*c", "aXXbYY", False),
                        ("ZDEMO", "ZDEMO", True), ("ZDEMO", "zdemo", False),
                        ("a.b", "aXb", False), ("[x]", "[x]", True)):
    check(f"{pat!r} ~ {text!r} = {want}", compose.glob_match(pat, text) == want)
t0 = time.monotonic()
compose.glob_match("*a" * 50 + "b", "a" * 5000)
check("kein exponentielles Zurückgehen (bösartiges Muster, 5000 Zeichen)", time.monotonic() - t0 < 2,
      time.monotonic() - t0)
check("Muster für die Anzeige", compose.glob_regex("Z*.?") == r"^Z.*\..$", compose.glob_regex("Z*.?"))

print("\n-- Namen")
check("frei: Name des Servers", compose.propose("tool", "sap", "read_class", set()) == "read_class")
check("belegt: <server>_<name>", compose.propose("tool", "sap", "read_class", {"read_class"})
      == "sap_read_class")
check("unzulässiges Zeichen wird ersetzt", compose.propose("tool", "s", "a b", set()) == "s_a_b")
check("Resource behält die URI", compose.propose("resource", "s", "doc://x y", set()) == "doc://x y")
check("Name mit Leerzeichen abgewiesen", refused(compose.valid_exposed, "tool", "a b") is not None)
check("129 Zeichen abgewiesen", refused(compose.valid_exposed, "tool", "a" * 129) is not None)

print("\n-- feste Argumente und Grenzen")
DOC = {"name": "lookup", "inputSchema": {
    "type": "object", "required": ["package", "name"],
    "properties": {"package": {"type": "string", "maxLength": 30},
                   "name": {"$ref": "#/$defs/obj"},
                   "mode": {"type": "string", "enum": ["kurz", "lang", "roh"]},
                   "n": {"type": "integer", "minimum": 1}},
    "$defs": {"obj": {"type": "string", "minLength": 1}}}}
fixed, bounds = compose.tailor(DOC, {"package": "ZDEMO"},
                               {"name": {"glob": "Z*"}, "mode": {"enum": ["kurz", "lang"]}})
check("gültiger Zuschnitt angenommen", fixed == {"package": "ZDEMO"} and set(bounds) == {"name", "mode"})
check("fester Wert mit falschem Typ abgewiesen",
      "package" in (refused(compose.tailor, DOC, {"package": 5}, {}) or ""))
check("fester Wert gegen lokales $ref geprüft",
      refused(compose.tailor, DOC, {"name": ""}, {}) is not None)
check("festes Argument, das es nicht gibt, abgewiesen",
      refused(compose.tailor, DOC, {"erfunden": 1}, {}) is not None)
check("Grenze auf ein festes Argument abgewiesen",
      "zweite Wahrheit" in (refused(compose.tailor, DOC, {"package": "Z"}, {"package": {"glob": "Z*"}}) or ""))
check("erlaubter Wert außerhalb des Schemas abgewiesen",
      refused(compose.tailor, DOC, {}, {"mode": {"enum": ["kurz", "quer"]}}) is not None)
check("Grenze ohne enum/glob abgewiesen",
      refused(compose.tailor, DOC, {}, {"mode": {"regex": ".*"}}) is not None)

shown = compose.shown_schema(DOC["inputSchema"], fixed, bounds)
check("gezeigtes Schema ohne festes Argument", "package" not in shown["properties"]
      and shown["required"] == ["name"], shown)
check("Grenzen im gezeigten Schema", shown["properties"]["mode"]["enum"] == ["kurz", "lang"]
      and shown["properties"]["name"]["pattern"] == "^Z.*$", shown["properties"])
check("das freigegebene Schema bleibt unberührt", "package" in DOC["inputSchema"]["properties"])

merged = compose.prepare_call(DOC, fixed, bounds, {"name": "ZCL_A", "mode": "kurz"})
check("fester Wert wird eingesetzt", merged == {"name": "ZCL_A", "mode": "kurz", "package": "ZDEMO"})
r = refused(compose.prepare_call, DOC, fixed, bounds, {"name": "ZCL_A", "package": "ZDEMO"})
check("festes Argument vom Client abgewiesen, auch mit gleichem Wert", r and "fest" in r, r)
r = refused(compose.prepare_call, DOC, fixed, bounds, {"name": "CL_GEHEIM"})
check("Muster verletzt: Pfad genannt, Wert nicht", r and "name" in r and "GEHEIM" not in r, r)
r = refused(compose.prepare_call, DOC, fixed, bounds, {"name": "ZA", "mode": "roh"})
check("Wert außerhalb der Liste abgewiesen, obwohl das Schema ihn kennt", r and "mode" in r, r)
check("Schema gilt weiter (Pflicht fehlt)", refused(compose.prepare_call, DOC, fixed, bounds, {}))

print("\n-- Resource-Vorlagen")
check("{x} passt auf ein Stück", compose.template_match("sap://class/{name}", "sap://class/ZCL_A"))
check("{x} nicht über /", not compose.template_match("sap://class/{name}", "sap://class/a/b"))
check("{x} nicht leer", not compose.template_match("sap://class/{name}", "sap://class/"))
check("{+x} über /", compose.template_match("file:///{+path}", "file:///a/b/c"))
check("zwei Variablen", compose.template_match("db://{schema}/{table}", "db://s/t"))
check("anderer Anfang passt nicht", not compose.template_match("sap://class/{name}", "sap://prog/X"))
check("Operatoren außer + nicht freigebbar", not compose.template_supported("x://{?q}")
      and not compose.template_supported("x://{a,b}") and compose.template_supported("x://{+a}/{b}"))

print("\n-- Prompts")
PDOC = {"name": "review", "arguments": [{"name": "code", "required": True}, {"name": "ton"}]}
check("gültig", compose.check_prompt_arguments(PDOC, {"code": "x"}) == {"code": "x"})
check("Pflicht fehlt", refused(compose.check_prompt_arguments, PDOC, {"ton": "x"}))
check("unbekannt", refused(compose.check_prompt_arguments, PDOC, {"code": "x", "boese": "y"}))
check("kein Text", refused(compose.check_prompt_arguments, PDOC, {"code": 5}))

print("\n-- Umzug einer Datenbank aus 0.1")
TMP = tempfile.mkdtemp(prefix="mcpgw-mig-")
path = os.path.join(TMP, "gateway.db")
old = sqlite3.connect(path)
old.executescript("""
CREATE TABLE views (name TEXT PRIMARY KEY, server TEXT NOT NULL, max_effect TEXT NOT NULL DEFAULT 'read',
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE TABLE catalog (server TEXT NOT NULL, name TEXT NOT NULL, fingerprint TEXT NOT NULL, doc TEXT NOT NULL,
  present INTEGER NOT NULL DEFAULT 1, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  PRIMARY KEY (server, name));
CREATE TABLE items (view TEXT NOT NULL, tool TEXT NOT NULL, effect TEXT NOT NULL, approved_fp TEXT NOT NULL,
  approved_doc TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL, PRIMARY KEY (view, tool));
CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, key_id TEXT NOT NULL,
  view TEXT NOT NULL, tool TEXT NOT NULL, server TEXT NOT NULL DEFAULT '', server_tool TEXT NOT NULL DEFAULT '',
  procedure TEXT NOT NULL DEFAULT 'T', outcome TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
  ms INTEGER NOT NULL DEFAULT 0, bytes_up INTEGER NOT NULL DEFAULT 0, bytes_down INTEGER NOT NULL DEFAULT 0);
""")
doc = {"name": "read_class", "description": "Liest eine Klasse."}
canon = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
import hashlib  # noqa: E402
fp = hashlib.sha256(canon.encode()).hexdigest()
old.execute("INSERT INTO views VALUES ('sap-lesen','a4h','read','t','t')")
old.execute("INSERT INTO catalog VALUES ('a4h','read_class',?,?,1,'t','t')", (fp, json.dumps(doc)))
old.execute("INSERT INTO items VALUES ('sap-lesen','read_class','read',?,?,'betreiber','t')",
            (fp, json.dumps(doc)))
old.execute("INSERT INTO usage (time,key_id,view,tool,outcome) VALUES ('t','k','sap-lesen','read_class','ok')")
old.commit()
old.close()

import books  # noqa: E402
db = books.connect(path)
items = books.items(db, "sap-lesen")
check("ein Eintrag, Art tool, Server aus der Sicht",
      len(items) == 1 and items[0]["kind"] == "tool" and items[0]["server"] == "a4h"
      and items[0]["source"] == "read_class" and items[0]["name"] == "read_class", [dict(i) for i in items])
check("Fingerabdruck unverändert → weiter freigegeben (nicht gesperrt)",
      books.item_state(db, items[0]) == "active")
check("alte Sicht: Rückfragen weiter abgelehnt",
      books.view_requests(books.view_row(db, "sap-lesen")) == dict.fromkeys(books.REQUESTS, "refuse"))
check("alte Messzeile: Art tool", db.execute("SELECT kind FROM usage").fetchone()["kind"] == "tool")
check("Prüfspur nennt den Umzug", any(r["action"] == "items.0.2" for r in books.audit(db)))
db.close()
db = books.connect(path)
check("zweiter Start: nichts doppelt", len(books.items(db, "sap-lesen")) == 1
      and sum(1 for r in books.audit(db) if r["action"] == "items.0.2") == 1)
books.save_view(db, "t", "neu", "read")
check("neue Sicht: Vorgabe des RFC (elicitation, roots durch; sampling nicht)",
      books.view_requests(books.view_row(db, "neu")) == books.REQUESTS_NEW)
db.close()
shutil.rmtree(TMP, ignore_errors=True)

print("\n-- Dockerfile kopiert jedes Modul")
# Lokal läuft alles aus dem Ordner; im Container nur, was COPY nennt.
copied = set()
for line in open(os.path.join(HERE, "Dockerfile"), encoding="utf-8"):
    if line.startswith("COPY "):
        copied.update(line.split()[1:-1])
modules = {f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_")}
check("jedes Modul steht in einer COPY-Zeile", modules <= copied, sorted(modules - copied))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
