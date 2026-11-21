#!/usr/bin/env python3
"""Das Studio im Rahmen der Plattform (RFC-0118) -- ohne Knoten.

Rahmen und Name, Reiter, der Listenbericht der Vorhaben (Suche, Filter,
Sortierung, Blättern), die Hilfe aus docs/, die MCP-Tür mit den Rollen
der Seite und der Starter, den das Studio an Entwickler ausgibt.

    py -3 test_frame.py
"""
import http.client
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import zipfile
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="studio-frame-")
os.environ["STUDIO_DATA_DIR"] = DATA

import app  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
KEY = {"X-OAAP-User": "kim", "X-OAAP-Roles": "keyuser", "X-OAAP-User-Id": "u-1"}


def call(method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=10)
    h = dict(headers or {})
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=body, headers=h)
    r = c.getresponse()
    out = r.status, r.read().decode("utf-8", "replace")
    c.close()
    return out


def rpc(method, params=None, headers=KEY):
    st, b = call("POST", "/mcp", headers, {"jsonrpc": "2.0", "id": 1, "method": method,
                                          "params": params or {}})
    return json.loads(b)


try:
    con = app.db()
    for i in range(55):
        con.execute(
            "INSERT INTO projects (id, name, context, status, app_type, agent, goal,"
            " created_at, created_by, updated_at, updated_by)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"v-{i:02d}", f"Vorhaben {i:02d}", "bec" if i % 2 else "kuk",
             "test" if i < 5 else "idee", "native", "Claude Code", f"Ziel {i}",
             "2026-11-21", "kim", "2026-11-21", "kim"))
    con.commit()
    con.close()

    print("=== Rahmen und Name ===")
    st, b = call("GET", "/", KEY)
    ok("Seite antwortet", st == 200, b[:200])
    ok("Kit der Plattform", '<link rel="stylesheet" href="/platform/theme.css">' in b)
    ok("Rahmenskript der Plattform", '<script src="/platform/app.js" defer></script>' in b)
    ok("App-Name ohne OAAP", '<span class="oaap-appname">Studio</span>' in b
       and "OAAP Studio" not in b and "OAAP STUDIO" not in b)
    ok("Hilfeprodukt am Kopf", 'data-oaap-product="studio"' in b)
    ok("keine Rollen und kein Benutzername auf der Seite", "keyuser" not in b and ">kim<" not in b)
    ok("keine eigene Palette", "--oaap-blue-950" not in b and "#1e3a8a" not in b)
    ok("Reiter: Übersicht zuerst, Onboarding danach, kein Hilfe-Reiter",
       0 < b.find(">Übersicht<") < b.find(">Onboarding<") and 'href="/hilfe"' not in b)

    print("\n=== Listenbericht der Vorhaben ===")
    ok("Filterleiste mit Suche, Status und App-Typ",
       'class="oaap-filter"' in b and 'name="status"' in b and 'name="typ"' in b)
    ok("Zählzeile 1–50 von 55", "1–50 von 55 Vorhaben" in b, b[b.find("oaap-countline"):][:100])
    ok("Weitere 50", "Weitere 50" in b and "offset=50" in b)
    ok("keine Formulare in Tabellenzeilen", "<form" not in b[b.find("<tbody>"):b.find("</tbody>")])
    st, b = call("GET", "/?offset=50", KEY)
    ok("zweite Seite 51–55", "51–55 von 55 Vorhaben" in b)
    st, b = call("GET", "/?status=test", KEY)
    ok("Filter Status=Im Test: fünf Vorhaben", "5 Vorhaben" in b and "Vorhaben 07" not in b,
       b[b.find("oaap-countline"):][:100])
    st, b = call("GET", "/?q=Vorhaben+07", KEY)
    ok("Suche findet genau eins", "Vorhaben 07" in b and "Vorhaben 08" not in b)
    st, b = call("GET", "/?sort=-name", KEY)
    ok("absteigend sortiert", 0 < b.find("Vorhaben 54") < b.find("Vorhaben 53")
       and 'aria-sort="descending"' in b)

    print("\n=== Seite eines Vorhabens ===")
    st, b = call("GET", "/vorhaben/v-03", KEY)
    ok("Zurück zur Liste über dem Titel", 'class="oaap-back" href="/"' in b)
    ok("Reiter des Vorhabens", all(x in b for x in ('href="/vorhaben/v-03/paket"',
                                                   'href="/vorhaben/v-03/briefing"',
                                                   'href="/vorhaben/v-03/zettel"')))
    ok("das Objekt steht am Kopf (für Fragen und Feedback)", 'data-oaap-object="Vorhaben:v-03"' in b)
    st, b = call("GET", "/vorhaben/v-03/paket", KEY)
    ok("Paket-Reiter ist aktiv", '<a href="/vorhaben/v-03/paket" aria-current="page">Paket</a>' in b)
    st, b = call("GET", "/vorhaben/gibtsnicht", KEY)
    ok("unbekanntes Vorhaben: 404 im Rahmen", st == 404 and "oaap-header" in b)

    print("\n=== Hilfe aus docs/ ===")
    st, b = call("GET", "/hilfe", KEY)
    ok("/hilfe zeigt docs/index.md", st == 200 and "Studio – Überblick" in b)
    ok("Verweise bleiben in /hilfe", 'href="/hilfe/faq"' in b)
    st, b = call("GET", "/hilfe/faq", KEY)
    ok("/hilfe/faq", st == 200 and "häufige Fragen" in b)
    st, b = call("GET", "/hilfe/..%2Fapp", KEY)
    ok("kein Weg aus docs/ hinaus", st == 404)

    print("\n=== MCP-Tür ===")
    r = rpc("tools/list")
    names = sorted(t["name"] for t in r["result"]["tools"])
    ok("tools/list", names == ["studio_briefing", "studio_project_read", "studio_projects"], names)
    ok("alle nur lesend", all(t["annotations"]["readOnlyHint"] for t in r["result"]["tools"]))
    ok("prompts/list: zwei Vorlagen", len(rpc("prompts/list")["result"]["prompts"]) == 2)
    st, _b = call("GET", "/mcp", KEY)
    ok("GET /mcp → 405", st == 405)

    def tool(name, args, headers=KEY):
        res = rpc("tools/call", {"name": name, "arguments": args}, headers)["result"]
        return res["isError"], res["content"][0]["text"]

    err, text = tool("studio_projects", {"q": "Vorhaben 0", "status": "test"})
    data = json.loads(text)
    ok("studio_projects mit Suche und Status", not err and data["anzahl"] == 5, text[:200])
    err, text = tool("studio_project_read", {"id": "v-02"})
    ok("studio_project_read", not err and json.loads(text)["ziel"] == "Ziel 2", text[:200])
    err, text = tool("studio_briefing", {"id": "v-02"})
    ok("studio_briefing zu einem Vorhaben", not err and "Vorhaben 02" in text)
    err, text = tool("studio_briefing", {})
    ok("studio_briefing ohne Kennung: das Plattform-Briefing", not err and "/platform/theme.css" in text)
    user = {"X-OAAP-User": "gast", "X-OAAP-Roles": "user"}
    for name, args in (("studio_projects", {}), ("studio_project_read", {"id": "v-02"}),
                       ("studio_briefing", {})):
        err, text = tool(name, args, user)
        ok(f"{name}: Rolle user wird abgelehnt (wie die Seite)", err and "keyuser" in text, text)
    err, text = tool("studio_project_read", {"id": "nix"})
    ok("unbekanntes Vorhaben: ein Satz", err and "gibt es nicht" in text)

    print("\n=== Briefing nennt die Regeln von RFC-0118 ===")
    st, b = call("GET", "/briefing.md", KEY)
    ok("Rahmen, docs:, mcp:, Zwilling",
       all(x in b for x in ("/platform/app.js", "ohne „OAAP“", "**Hilfe (`docs:`) ist Pflicht.**",
                            "**MCP (`mcp:`) für jede App, die Daten hält.**", "**Zwilling.**",
                            "Listenbericht")))

    print("\n=== Der Starter folgt dem Rahmen ===")
    zf = zipfile.ZipFile(io.BytesIO(app.starter_zip()))
    folder = app.starter_name()
    src = zf.read(f"{folder}/app.py").decode("utf-8")
    man = zf.read(f"{folder}/oaap-app.yaml").decode("utf-8")
    ok("Starter enthält seine Hilfe", f"{folder}/docs/index.md" in zf.namelist())
    ok("Starter-Manifest: Name ohne OAAP, docs:", "name: Starter" in man and "\ndocs:\n" in man)
    ok("Starter ohne eigene Palette", "#1e3a8a" not in src and "#2563eb" not in src)
    sys.path.insert(0, os.path.join(HERE, "starter"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("starter_app", os.path.join(HERE, "starter", "app.py"))
    starter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(starter)
    starter.DATA_DIR = DATA
    starter.VISITS = os.path.join(DATA, "besuche.json")
    s2 = ThreadingHTTPServer(("127.0.0.1", 0), starter.Handler)
    threading.Thread(target=s2.serve_forever, daemon=True).start()
    c = http.client.HTTPConnection("127.0.0.1", s2.server_port, timeout=10)
    c.request("GET", "/", headers={"X-OAAP-User": "kim", "X-OAAP-User-Id": "u-1",
                                   "X-OAAP-Roles": "keyuser"})
    r = c.getresponse()
    page = r.read().decode("utf-8")
    ok("Starter-Seite lädt Kit und Rahmenskript",
       r.status == 200 and "/platform/theme.css" in page and "/platform/app.js" in page)
    ok("Starter-Kopfleiste nach app.js-Muster",
       '<header class="oaap-header" data-oaap-product="starter">' in page
       and '<span class="oaap-appname">Starter</span>' in page and 'class="oaap-footer"' in page)
    ok("Starter zeigt keine Rollen", "keyuser" not in page)
    s2.shutdown()
finally:
    srv.shutdown()
    shutil.rmtree(DATA, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
