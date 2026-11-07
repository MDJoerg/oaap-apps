#!/usr/bin/env python3
"""Einstellungen von der Plattform (Spec §3.8, RFC-0073 Stufe 3).

Jeder Fall in einem eigenen Prozess, weil app.py die Einstellungen beim
Laden liest:
    - nur OAAP_MCP_IDP_*: alle fünf kommen von der Plattform;
    - MCPGW_* von Hand gesetzt: gewinnt, einzeln je Einstellung;
    - MCPGW_* leer gesetzt (so legt die Plattform Config-Tasten an): zählt als leer;
    - nichts: Anmeldung aus;
    - die Tabelle der Betreiberseite zeigt das Geheimnis nie.

Aufruf: python3 test_platform_settings.py
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


PROBE = r"""
import json, app
print(json.dumps({"from": app.SETTING_FROM, "issuer": app.ISSUER,
                  "aud": app.AUDIENCE, "signin": app.SIGNIN_CLIENT,
                  "secret": app.CLIENT_SECRET, "platform": app.PLATFORM_AUDIENCE,
                  "table": app.settings_table()}))
"""

PLAT = {"OAAP_MCP_IDP_ISSUER": "http://kc.invalid/realms/r",
        "OAAP_MCP_IDP_AUDIENCE": "oaap-mcp-gw",
        "OAAP_MCP_IDP_SECRET": "plattform-geheimnis-xyz",
        "OAAP_MCP_IDP_SIGNIN": "oaap-mcp-gw-login",
        "OAAP_MCP_IDP_PLATFORM_AUDIENCE": "oaap-node"}


def run(extra):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("MCPGW_", "OAAP_MCP_"))}
    env.update({"MCPGW_DATA_DIR": tempfile.mkdtemp(prefix="mcpgw-ps-"),
                "MCPGW_MASTER_KEY": "x" * 64})
    env.update(extra)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=HERE, env=env,
                         capture_output=True, text=True, timeout=60)
    if out.returncode:
        raise SystemExit(out.stderr)
    return json.loads(out.stdout.strip().splitlines()[-1])


print("Nur die Plattform")
r = run(PLAT)
ok("alle fünf kommen von der Plattform", set(r["from"].values()) == {"platform"}, r["from"])
ok("Werte durchgereicht", r["issuer"] == PLAT["OAAP_MCP_IDP_ISSUER"]
   and r["aud"] == "oaap-mcp-gw" and r["signin"] == "oaap-mcp-gw-login"
   and r["secret"] == PLAT["OAAP_MCP_IDP_SECRET"] and r["platform"] == "oaap-node", r)
ok("die Tabelle nennt die Herkunft", "von der Plattform" in r["table"])
ok("KÖDER: das Geheimnis steht nicht in der Tabelle",
   PLAT["OAAP_MCP_IDP_SECRET"] not in r["table"] and "gesetzt" in r["table"], r["table"])

print("Von Hand gewinnt, einzeln")
r = run(dict(PLAT, MCPGW_AUDIENCE="von-hand-gw", MCPGW_CLIENT_SECRET="hand-geheim-abc"))
ok("Audience und Geheimnis von Hand", r["aud"] == "von-hand-gw"
   and r["secret"] == "hand-geheim-abc"
   and r["from"]["MCPGW_AUDIENCE"] == "hand" and r["from"]["MCPGW_CLIENT_SECRET"] == "hand", r)
ok("die anderen drei weiter von der Plattform",
   [r["from"][k] for k in ("MCPGW_ISSUER", "MCPGW_SIGNIN_CLIENT", "MCPGW_PLATFORM_AUDIENCE")]
   == ["platform"] * 3, r["from"])
ok("KÖDER: auch das Geheimnis von Hand steht nicht in der Tabelle",
   "hand-geheim-abc" not in r["table"], r["table"])

print("Leer gesetzte Config-Tasten zählen als leer")
r = run(dict(PLAT, MCPGW_ISSUER="", MCPGW_AUDIENCE="  ", MCPGW_SIGNIN_CLIENT="",
             MCPGW_CLIENT_SECRET="", MCPGW_PLATFORM_AUDIENCE=""))
ok("alle fünf von der Plattform", set(r["from"].values()) == {"platform"}, r["from"])

print("Nichts gesetzt")
r = run({})
ok("Anmeldung aus, alles leer", r["issuer"] == "" and set(r["from"].values()) == {""}, r)
ok("die Tabelle sagt leer", "leer" in r["table"])

print("Vor 0.6 von Hand eingerichtet")
hand = {"MCPGW_ISSUER": "http://kc.invalid/realms/alt", "MCPGW_AUDIENCE": "alt-gw",
        "MCPGW_SIGNIN_CLIENT": "alt-login", "MCPGW_CLIENT_SECRET": "alt-geheim",
        "MCPGW_PLATFORM_AUDIENCE": "alt-node"}
r = run(dict(PLAT, **hand))
ok("bleibt genau wie es war", r["issuer"] == hand["MCPGW_ISSUER"] and r["aud"] == "alt-gw"
   and r["signin"] == "alt-login" and r["secret"] == "alt-geheim"
   and r["platform"] == "alt-node" and set(r["from"].values()) == {"hand"}, r)

print("")
print("ALLE PRUEFUNGEN BESTANDEN" if not fails else f"{fails} FEHLER")
sys.exit(1 if fails else 0)
