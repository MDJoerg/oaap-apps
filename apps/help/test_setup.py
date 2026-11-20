#!/usr/bin/env python3
"""help 0.7.0: die Einrichtungstür /oaap/setup (RFC-0104 §3.1, §3.3) ohne Knoten.

Eine nachgebaute Destination liefert ein Archiv; die App läuft echt (HTTP)
in Wegwerf-Verzeichnissen. Geprüft: nur der Einrichtungsschlüssel öffnet
(Köder: tenant_admin ohne Schlüssel, fremder Schlüssel, kein Schlüssel im
Environment), GET kinds und Zustand, POST dry/echt, idempotent (made, dann
present), remove → gone, Geheimnisfelder → refused, kaputter Rumpf → 400,
der mitgelieferte Stand zählt nicht als present, nie unter /public/.

Aufruf: python3 test_setup.py
"""
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app  # noqa: E402
import index as ix  # noqa: E402
from library import Library  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


def archive(files, top="oaap-docs-main"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode("utf-8")
            ti = tarfile.TarInfo(f"{top}/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


DOCS = {"szenarien/backup.md": "# Backup\n\nSo geht es.\n", "README.md": "# OAAP-Doku\n\nÜberblick.\n"}


class Origin(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Origin.hits.append(self.path)
        body = archive(DOCS)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler, lib=None):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if lib is not None:
        srv.lib = lib
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


KEY = "einrichtung-geheim-123"
origin = serve(Origin)
dirs = [tempfile.mkdtemp(prefix="help-setup-") for _ in range(4)]
DEST = f"http://127.0.0.1:{origin.server_port}/destinations/docs-origin/"
NO_SEED = os.path.join(HERE, "kein-seed.tar.gz")


def web_for(data, **env):
    e = {"HELP_SEED_FILE": NO_SEED, "HELP_PUBLIC": "on"}
    e.update(env)
    lib = Library(data, e)
    srv = serve(app.Handler, lib)
    return lib, srv, f"http://127.0.0.1:{srv.server_port}"


def http(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


AUTH = {"Authorization": "Bearer " + KEY, "X-OAAP-Setup-Actor": "jörg@bec"}
JSON = {"Content-Type": "application/json"}


def post(base, doc, headers=None):
    body = doc if isinstance(doc, bytes) else json.dumps(doc).encode()
    return http(base, "POST", "/oaap/setup", body, dict(JSON, **(headers if headers is not None else AUTH)))


FETCH = {"kind": "fetch", "id": "docs"}
servers = [origin]
try:
    lib, srv, B = web_for(dirs[0], OAAP_SETUP_KEY=KEY, OAAP_DESTINATION_DOCS_ORIGIN_URL=DEST)
    servers.append(srv)

    # --- Schlüssel ------------------------------------------------------------
    s, _ = http(B, "GET", "/oaap/setup")
    ok("ohne Schlüssel → 403", s == 403)
    s, _ = http(B, "GET", "/oaap/setup", headers={"X-OAAP-Roles": "tenant_admin,admin",
                                                 "X-OAAP-User": "chef"})
    ok("Köder: tenant_admin mit Identitäts-Kopfzeilen, ohne Schlüssel → 403", s == 403)
    s, _ = http(B, "GET", "/oaap/setup", headers={"Authorization": "Bearer fremder-schluessel"})
    ok("Köder: Schlüssel einer anderen Instanz → 403", s == 403)
    s, _ = post(B, {"v": 1, "items": [FETCH]}, {"X-OAAP-Roles": "tenant_admin"})
    ok("POST ohne Schlüssel → 403, nichts geholt", s == 403 and not Origin.hits, Origin.hits)

    # --- GET ------------------------------------------------------------------
    s, d = http(B, "GET", "/oaap/setup", headers=AUTH)
    ok("GET: kinds", s == 200 and d == {"v": 1, "kinds": ["fetch"]}, d)
    s, d = http(B, "GET", "/oaap/setup?kind=fetch&id=docs", headers=AUTH)
    ok("GET fetch/docs vor dem Abruf: absent, nicht ready",
       s == 200 and d["state"] == "absent" and d["ready"] is False and d["kind"] == "fetch"
       and d["id"] == "docs" and d["v"] == 1 and d["note"], d)
    s, d = http(B, "GET", "/oaap/setup?kind=fetch", headers=AUTH)
    ok("GET ?kind=fetch ohne id (RFC-0109): die Liste zum Auswählen, eine Kennung",
       s == 200 and d["kind"] == "fetch" and [i["id"] for i in d["items"]] == ["docs"]
       and "noch nicht geholt" in d["items"][0]["title"], d)
    s, d = http(B, "GET", "/oaap/setup?kind=fetch")
    ok("Köder: die Liste ohne Schlüssel → 403", s == 403)

    # --- POST dry ---------------------------------------------------------------
    s, d = post(B, {"v": 1, "dry": True, "items": [FETCH]})
    ok("dry → would_make, nichts geholt",
       s == 200 and d["results"] == [{"kind": "fetch", "id": "docs", "result": "would_make",
                                      "note": d["results"][0]["note"]}] and not Origin.hits, (d, Origin.hits))

    # --- Rumpf --------------------------------------------------------------------
    for label, bad in (("kein JSON", b"{kaputt"), ("ohne v", {"items": [FETCH]}),
                       ("items und remove zugleich", {"v": 1, "items": [FETCH], "remove": [FETCH]}),
                       ("Eintrag ohne id", {"v": 1, "items": [{"kind": "fetch"}]}),
                       ("dry kein Wahrheitswert", {"v": 1, "dry": "ja", "items": [FETCH]})):
        s, _ = post(B, bad)
        ok(f"400 bei kaputtem Rumpf: {label}", s == 400, s)

    # --- Geheimnisse und Unbekanntes ---------------------------------------------
    s, d = post(B, {"v": 1, "items": [dict(FETCH, token="abc"), {"kind": "fetch", "id": "docs", "Password": "x"},
                                      {"kind": "supplier", "id": "x"}, {"kind": "fetch", "id": "anderes"}]})
    r = [x["result"] for x in d["results"]]
    ok("Geheimnisfelder, unbekannte Art und id → refused, je Eintrag in Reihenfolge",
       s == 200 and r == ["refused"] * 4 and "token" in d["results"][0]["note"]
       and d["results"][2]["kind"] == "supplier" and not Origin.hits, (d, Origin.hits))

    # --- POST echt: made, dann present ---------------------------------------------
    s, d = post(B, {"v": 1, "dry": False, "items": [FETCH]})
    ok("echt → made, über docs-origin geholt",
       s == 200 and d["results"][0]["result"] == "made" and len(Origin.hits) == 1
       and Origin.hits[0].startswith("/destinations/docs-origin/"), (d, Origin.hits))
    ok("geholte Doku ist im Index", lib.read(roles=ix.UNFILTERED, ref="szenarien/backup") is not None)
    mem = json.load(open(os.path.join(dirs[0], "setup.json"), encoding="utf-8"))
    rec = mem.get("made", {}).get("fetch:docs", {})
    ok("Erinnerung: wer, wann, Digest des Eintrags",
       rec.get("actor") == "jörg@bec" and rec.get("at") and len(rec.get("digest", "")) == 64, mem)
    s, d = post(B, {"v": 1, "items": [FETCH]})
    ok("derselbe POST noch einmal → present, kein neuer Abruf",
       d["results"][0]["result"] == "present" and len(Origin.hits) == 1, (d, Origin.hits))
    s, d = post(B, {"v": 1, "dry": True, "items": [FETCH]})
    ok("dry danach → present (das ist die Prüfung von app.setup)", d["results"][0]["result"] == "present", d)
    s, d = http(B, "GET", "/oaap/setup?kind=fetch&id=docs", headers=AUTH)
    ok("GET danach: present, ready", d["state"] == "present" and d["ready"] is True, d)

    # --- remove -------------------------------------------------------------------
    s, d = post(B, {"v": 1, "remove": [FETCH]})
    ok("remove → gone, nichts zu entfernen", s == 200 and d["results"][0]["result"] == "gone", d)
    ok("nach remove bleibt die Doku", lib.read(roles=ix.UNFILTERED, ref="szenarien/backup") is not None)

    # --- nie unter /public/ ----------------------------------------------------------
    s, _ = http(B, "GET", "/public/oaap/setup", headers=AUTH)
    s2, _ = http(B, "POST", "/public/oaap/setup", json.dumps({"v": 1, "items": [FETCH]}).encode(),
                 dict(JSON, **AUTH))
    ok("/public/oaap/setup gibt es nicht (404)", s == 404 and s2 == 404, (s, s2))

    # --- ohne OAAP_SETUP_KEY im Environment: jeder Ruf abgelehnt ------------------------
    lib2, srv2, B2 = web_for(dirs[1], OAAP_DESTINATION_DOCS_ORIGIN_URL=DEST)
    servers.append(srv2)
    s, _ = http(B2, "GET", "/oaap/setup", headers=AUTH)
    s2, _ = http(B2, "GET", "/oaap/setup", headers={"Authorization": "Bearer "})
    ok("ohne Schlüssel im Environment: 403, auch mit leerem Bearer", s == 403 and s2 == 403, (s, s2))

    # --- Destination nicht zugeordnet ----------------------------------------------------
    lib3, srv3, B3 = web_for(dirs[2], OAAP_SETUP_KEY=KEY)
    servers.append(srv3)
    s, d = post(B3, {"v": 1, "dry": True, "items": [FETCH]})
    ok("ohne docs-origin: dry → would_make mit Hinweis",
       d["results"][0]["result"] == "would_make" and "docs-origin" in d["results"][0]["note"], d)
    s, d = post(B3, {"v": 1, "items": [FETCH]})
    ok("ohne docs-origin: echt → refused, Hinweis nennt die Destination",
       d["results"][0]["result"] == "refused" and "docs-origin" in d["results"][0]["note"], d)

    # --- mitgelieferter Stand zählt nicht; HELP_OAAP_DOCS=off ---------------------------
    lib4, srv4, B4 = web_for(dirs[3], OAAP_SETUP_KEY=KEY, HELP_SEED_FILE=os.path.join(HERE, "seed", "oaap-docs.tar.gz"))
    servers.append(srv4)
    lib4.refresh()
    s, d = http(B4, "GET", "/oaap/setup?kind=fetch&id=docs", headers=AUTH)
    ok("nur der seed antwortet → absent", lib4.state().get("answering") == "seed" and d["state"] == "absent", d)
    lib4.env["HELP_OAAP_DOCS"] = "off"
    s, d = post(B4, {"v": 1, "items": [FETCH]})
    ok("HELP_OAAP_DOCS=off → refused mit Grund",
       d["results"][0]["result"] == "refused" and "HELP_OAAP_DOCS" in d["results"][0]["note"], d)
finally:
    for s_ in servers:
        s_.shutdown()
    for d_ in dirs:
        shutil.rmtree(d_, ignore_errors=True)

print("")
print(f"{'ALLE PRUEFUNGEN BESTANDEN' if not fails else str(fails) + ' FEHLGESCHLAGEN'}")
sys.exit(1 if fails else 0)
