#!/usr/bin/env python3
"""Argumentprüfung gegen das freigegebene Schema (Spec §7, A1).

Run: python3 apps/mcp-gateway/test_argcheck.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argcheck  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


def refused(schema, args):
    try:
        argcheck.check(schema, args)
        return None
    except argcheck.Refused as exc:
        return exc


S = {"type": "object",
     "properties": {"name": {"type": "string", "maxLength": 30},
                    "count": {"type": "integer", "minimum": 1, "maximum": 100},
                    "mode": {"enum": ["fast", "full"]},
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                    "opts": {"type": "object", "properties": {"deep": {"type": "boolean"}}},
                    "free": {"type": "object"},
                    "ref": {"$ref": "#/$defs/pkg"}},
     "required": ["name"],
     "$defs": {"pkg": {"type": "string", "minLength": 1, "maxLength": 30}}}

print("-- passt")
check("vollständig und richtig", refused(S, {"name": "ZCL", "count": 5, "mode": "fast",
                                              "tags": ["a"], "opts": {"deep": True},
                                              "free": {"was": "auch immer"}, "ref": "ZDEMO"}) is None)
check("nur das Pflichtfeld", refused(S, {"name": "x"}) is None)
check("1.0 zählt als ganze Zahl", refused(S, {"name": "x", "count": 1.0}) is None)

print("\n-- abgewiesen, mit Pfad und ohne Wert")
e = refused(S, {"name": "x", "erfunden": "GEHEIM-WERT"})
check("unbekanntes Argument", e is not None and e.path == "erfunden", e)
check("die Meldung nennt den Wert nicht", e is not None and "GEHEIM-WERT" not in str(e), e)
e = refused(S, {"count": 3})
check("Pflichtfeld fehlt", e is not None and e.path == "name", e)
e = refused(S, {"name": 42})
check("falscher Typ", e is not None and e.path == "name" and "Text" in str(e), e)
e = refused(S, {"name": "x", "count": True})
check("ein Wahrheitswert ist keine Zahl", e is not None and e.path == "count", e)
e = refused(S, {"name": "x", "count": 0})
check("unter dem Minimum", e is not None and e.path == "count", e)
e = refused(S, {"name": "x", "mode": "turbo"})
check("nicht in enum", e is not None and e.path == "mode", e)
e = refused(S, {"name": "x", "tags": ["a", 2]})
check("Listeneintrag mit Index", e is not None and e.path == "tags[1]", e)
e = refused(S, {"name": "x", "tags": ["a", "b", "c", "d"]})
check("zu viele Einträge", e is not None and e.path == "tags", e)
e = refused(S, {"name": "x", "opts": {"deep": True, "heimlich": 1}})
check("unbekannt auch in einem Objekt, das aufzählt", e is not None and e.path == "opts.heimlich", e)
e = refused(S, {"name": "x", "ref": ""})
check("lokales $ref wird verfolgt", e is not None and e.path == "ref", e)
e = refused(S, {"name": "x" * 31})
check("zu lang", e is not None and e.path == "name", e)
check("Argumente müssen ein Objekt sein", refused(S, ["name"]) is not None)

print("\n-- Formen")
U = {"type": "object", "properties": {"v": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                                      "w": {"oneOf": [{"type": "integer"}, {"type": "number"}]}}}
check("anyOf: eine passt", refused(U, {"v": 3}) is None)
check("anyOf: keine passt", refused(U, {"v": [1]}) is not None)
check("oneOf: 2.5 passt nur zu number", refused(U, {"w": 2.5}) is None)
check("oneOf: 2 passt zu beiden → abgewiesen", refused(U, {"w": 2}) is not None)
A = {"type": "object", "additionalProperties": True}
check("ausdrücklich offen: Unbekanntes erlaubt", refused(A, {"irgendwas": 1}) is None)
check("leeres Schema: keine Argumente erlaubt", refused({"type": "object"}, {"x": 1}) is not None)
check("leeres Schema, keine Argumente", refused({"type": "object"}, None) is None)

print("\n-- Was nicht geprüft wird, wird gesagt")
P = {"type": "object", "properties": {"p": {"type": "string", "pattern": "^Z", "format": "email"}},
     "patternProperties": {"^x-": {"type": "string"}}}
check("pattern wird nicht durchgesetzt", refused(P, {"p": "ABC"}) is None)
check("unchecked() nennt pattern, format und patternProperties",
      argcheck.unchecked(P) == ["format", "pattern", "patternProperties"], argcheck.unchecked(P))
check("ein Name, den nur ein Muster erlaubt, ist strenger: abgewiesen",
      refused(P, {"x-a": "1"}) is not None)
check("Eigenschaftsnamen sind keine Schlüsselwörter",
      argcheck.unchecked({"properties": {"pattern": {"type": "string"}}}) == [])

print("\n-- Verweise nach außen")
check("remote $ref gefunden",
      argcheck.remote_refs({"properties": {"a": {"$ref": "https://evil.example/s.json"}}})
      == ["https://evil.example/s.json"])
check("lokales $ref ist keins", argcheck.remote_refs(S) == [])

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
