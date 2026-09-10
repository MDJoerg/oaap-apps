#!/usr/bin/env python3
"""This manifest validates as a data_models artefact (oaap.data.model
0.1 §2.8) -- no Docker, no Postgres, no node. That an install on a
real node actually registers under 'model:kundenzufriedenheit' and
leaves no instance belongs on `oaap-test`.

Run: python3 data_models/kundenzufriedenheit/test_manifest.py
"""
import os
import sys

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PLATFORM = os.path.join(HERE, "..", "..", "..", "oaap-reference", "platform")
sys.path.insert(0, PLATFORM)

import appctl as m  # noqa: E402

ok_n = fail_n = 0


def ok(label, cond, detail=""):
    global ok_n, fail_n
    if cond:
        ok_n += 1
        print(f"PASS  {label}")
    else:
        fail_n += 1
        print(f"FAIL  {label} {detail}")


with open(os.path.join(HERE, "oaap-app.yaml"), encoding="utf-8") as fh:
    doc = yaml.safe_load(fh)

print("=== structural shape: no services, no route, no health -- an artefact ===")
ok("no 'services' key at all", "services" not in doc)
ok("no 'routes' key at all", "routes" not in doc)
ok("no 'health' key at all", "health" not in doc)
ok("no 'app.type' -- an artefact never builds a container", "type" not in doc["app"])
ok("carries a 'data_model' section (the only content)", bool(doc.get("data_model")))

print("\n=== validate_data_model_sections: clean ===")
ok("no structural errors", m.validate_data_model_sections(doc) == [])

print("\n=== validate_manifest: accepted as an artefact, not refused for "
      "missing services (oaap.data.model 0.1 §2.8) ===")
try:
    m.validate_manifest(doc)
    ok("validate_manifest accepts it", True)
except SystemExit:
    ok("validate_manifest accepts it", False)

print(f"\n{ok_n} bestanden, {fail_n} fehlgeschlagen")
print("ALLE PRUEFUNGEN BESTANDEN" if not fail_n else "FEHLGESCHLAGEN")
sys.exit(1 if fail_n else 0)
