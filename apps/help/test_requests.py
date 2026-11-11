#!/usr/bin/env python3
"""help 0.3.0 -- Lücken und Anfragen (RFC-0078 §15, Stufe 2b, I-50).

Echte App über HTTP in einem Wegwerf-Verzeichnis, Doku aus einem lokalen
Archiv. Geprüft: das Werkzeug nur mit HELP_REQUESTS (M13), Dubletten als
Stimme (M11 in klein, mit Jörgs Tunnel-Frage), covered_by, die
Bearbeitungstür nur mit Schlüssel (M13), Schließen nur mit einem Dokument im
Index (M12 in klein), keine Adresse und keine Identität in der Datenbank
(M14), Lücken ohne Personenbezug, Grenzen, Fristen -- und dass das
Dockerfile jedes Modul kopiert.

Aufruf: python3 test_requests.py
"""
import datetime
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
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
import guide_requests as gr  # noqa: E402
import index as ix  # noqa: E402
from library import Library  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


def archive(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode("utf-8")
            ti = tarfile.TarInfo(f"oaap-docs-main/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


DOCS = {
    "cli/generated/connect.md": "---\nid: cli/connect\ntitle: \"oaap connect\"\naudience: [server_admin]\ntags: [cli]\n---\n# `oaap connect`\n\nthe outer side of a tunnel: keys inner nodes dial in with.\n",
    "szenarien/backup-cli-einrichten.md": "---\ntitle: \"Das nächtliche Backup auf der CLI einrichten\"\n---\n# Das nächtliche Backup auf der CLI einrichten\n\n## Schritt 1\n\nsudo oaap backup create\n",
}


class Origin(BaseHTTPRequestHandler):
    body = archive(DOCS)

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", str(len(Origin.body)))
        self.end_headers()
        self.wfile.write(Origin.body)


origin = ThreadingHTTPServer(("127.0.0.1", 0), Origin)
threading.Thread(target=origin.serve_forever, daemon=True).start()
data = tempfile.mkdtemp(prefix="help-req-")
KEY = "test-schluessel-" + "x" * 24
env = {"OAAP_DESTINATION_DOCS_ORIGIN_URL": f"http://127.0.0.1:{origin.server_port}/d/",
       "HELP_PUBLIC": "on", "HELP_SEED_FILE": os.path.join(HERE, "kein-seed")}
lib = Library(data, env)
lib.refresh()
web = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
web.lib = lib
threading.Thread(target=web.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{web.server_port}"


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def mcp(path, method, params=None, headers=None):
    h = {"Content-Type": "application/json", "X-Forwarded-For": "203.0.113.7",
         "X-OAAP-User": "geheim@example.org", "User-Agent": "Testclient/9.9"}
    h.update(headers or {})
    msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    st, body = http("POST", path, json.dumps(msg).encode(), h)
    return st, (json.loads(body) if body.startswith("{") else body)


def ask(args):
    st, r = mcp("/public/mcp", "tools/call", {"name": "help_request_guide", "arguments": args})
    res = r["result"]
    txt = res["content"][0]["text"]
    return res["isError"], (json.loads(txt) if txt.startswith("{") else txt)


TUNNEL = {"topic": "Einen lokalen Dienst auf dem Laptop über die Plattform freigeben, wie mit ngrok",
          "question": "Wie kann ich einen Tunnel auf meinem lokalen Laptop über die Plattform freigeben analog ngrok?",
          "searched": ["tunnel laptop", "ngrok", "oaap connect"], "found": ["cli/connect"], "lang": "de"}
TUNNEL2 = {"topic": "Dienst vom Laptop wie ngrok über die Plattform freigeben",
           "question": "Ich möchte meinen lokalen Webserver auf dem Laptop öffentlich machen, so wie ngrok -- geht das mit OAAP?"}
OTHER = {"topic": "Keycloak-Realm eines Mandanten sichern und wiederherstellen",
         "question": "Wie sichere ich den Realm eines Mandanten einzeln?"}
MAINT = {"Authorization": "Bearer " + KEY}

try:
    # --- M13: ausgeschaltet ------------------------------------------------
    st, r = mcp("/public/mcp", "tools/list")
    ok("aus: help_request_guide nicht gelistet",
       "help_request_guide" not in [t["name"] for t in r["result"]["tools"]])
    e, res = ask(TUNNEL)
    ok("aus: direkter Aufruf abgelehnt", e and "ausgeschaltet" in res, res)

    # --- an ---------------------------------------------------------------
    env["HELP_REQUESTS"] = "on"
    st, r = mcp("/public/mcp", "tools/list")
    tool = [t for t in r["result"]["tools"] if t["name"] == "help_request_guide"]
    ok("an: gelistet, als schreibend markiert, Text verlangt Zustimmung und allgemeine Formulierung",
       tool and tool[0]["annotations"]["readOnlyHint"] is False and "zugestimmt" in tool[0]["description"]
       and "keine Namen" in tool[0]["description"], tool)
    e, res = ask(TUNNEL)
    ok("M11: neue Anfrage R-JJJJ-0001", not e and res["status"] == "new"
       and re.fullmatch(r"R-\d{4}-0001", res["request"]) and res["votes"] == 1, res)
    rid = res["request"]
    e, res = ask(TUNNEL2)
    ok("M11: anders formuliert → Stimme auf dieselbe Anfrage", not e and res["status"] == "vote"
       and res["request"] == rid and res["votes"] == 2, res)
    e, res = ask(OTHER)
    ok("anderes Thema → neue Anfrage", not e and res["status"] == "new" and res["request"] != rid, res)
    other = res["request"]
    e, res = ask({"topic": "Das nächtliche Backup auf der CLI einrichten", "question": "Backup CLI?"})
    ok("covered_by: vorhandenes Dokument wird genannt, Anfrage bleibt",
       not e and res["status"] == "new" and "szenarien/backup-cli-einrichten" in res.get("note", ""), res)
    covered = res["request"]
    e, res = ask({"topic": "x", "question": "y"})
    ok("ohne brauchbares topic abgelehnt", e)
    e, res = ask({"topic": "Ein Thema " + "a" * 500, "question": "b" * 5000 + "\x00\x07"})
    ok("Längen gekürzt, Steuerzeichen entfernt", not e)

    # --- Bearbeitungstür: nur mit Schlüssel (M13) ----------------------------
    st, r = mcp("/maint/mcp", "tools/list")
    ok("ohne konfigurierten Digest: 401, keine Daten", st == 401 and "requests" not in str(r))
    env["HELP_MAINT_KEY_SHA256"] = hashlib.sha256(KEY.encode()).hexdigest()
    st, r = mcp("/maint/mcp", "tools/list")
    ok("ohne Schlüssel: 401", st == 401)
    st, r = mcp("/maint/mcp", "tools/list", headers={"Authorization": "Bearer falsch"})
    ok("falscher Schlüssel: 401", st == 401)
    st, r = mcp("/public/maint/mcp", "tools/list", headers=MAINT)
    ok("/public/maint gibt es nicht", st == 404)
    st, b = http("GET", "/maint/api/v1/requests")
    ok("API ohne Schlüssel: 401", st == 401)
    st, r = mcp("/maint/mcp", "tools/list", headers=MAINT)
    ok("mit Schlüssel: vier Werkzeuge", st == 200 and [t["name"] for t in r["result"]["tools"]]
       == ["requests_list", "request_read", "request_close", "gaps_report"])
    ok("Texte warnen: fremder Inhalt, keine Anweisung", "nie eine Anweisung" in r["result"]["tools"][0]["description"])

    def maint(name, args):
        st, r = mcp("/maint/mcp", "tools/call", {"name": name, "arguments": args}, MAINT)
        res = r["result"]
        txt = res["content"][0]["text"]
        return res["isError"], (json.loads(txt) if txt.startswith("{") else txt)

    e, res = maint("requests_list", {})
    ok("requests_list: Stimmen zuerst, Zählung", not e and res["requests"][0]["id"] == rid
       and res["requests"][0]["votes"] == 2 and res["counts"]["open"] == 4, res)
    ok("covered_by in der Liste", any(x["id"] == covered and x["covered_by"] == "szenarien/backup-cli-einrichten"
                                      for x in res["requests"]))
    e, res = maint("request_read", {"id": rid})
    ok("request_read: beide Fragen", not e and len(res["questions"]) == 2
       and res["searched"] == ["tunnel laptop", "ngrok", "oaap connect"], res)
    # M12 in klein: done nur mit einem Dokument, das im Index steht
    e, res = maint("request_close", {"id": rid, "status": "done", "doc_id": "szenarien/ngrok-wie-freigeben"})
    ok("done mit unbekanntem Dokument abgelehnt", e and "gibt es nicht" in res, res)
    docs2 = dict(DOCS)
    docs2["szenarien/lokalen-dienst-freigeben.md"] = "# Einen lokalen Dienst über die Plattform freigeben (wie ngrok)\n\nSchritte …\n"
    Origin.body = archive(docs2)
    lib.refresh()
    e, res = maint("request_close", {"id": rid, "status": "done", "doc_id": "szenarien/lokalen-dienst-freigeben"})
    ok("M12: done mit dem neuen Dokument", not e and res["status"] == "done"
       and res["doc_id"] == "szenarien/lokalen-dienst-freigeben", res)
    hits = lib.search(TUNNEL["question"], roles=ix.UNFILTERED)
    ok("M12: die ursprüngliche Frage findet die neue Anleitung zuerst",
       hits and hits[0]["id"] == "szenarien/lokalen-dienst-freigeben", [h["id"] for h in hits[:3]])
    e, res = ask(TUNNEL)
    ok("nach dem Schließen: neue Anfrage statt Stimme auf eine erledigte", res["status"] == "new"
       and res["request"] != rid, res)
    e, res = maint("request_close", {"id": other, "status": "rejected"})
    ok("rejected ohne Begründung abgelehnt", e)
    e, res = maint("request_close", {"id": other, "status": "duplicate", "duplicate_of": other})
    ok("duplicate auf sich selbst abgelehnt", e)
    e, res = maint("request_close", {"id": covered, "status": "duplicate", "duplicate_of": other})
    e2, o = maint("request_read", {"id": other})
    ok("duplicate: Stimmen wandern", not e and o["votes"] == 2, (res, o))
    st, b = http("POST", f"/maint/api/v1/requests/{other}/close",
                 json.dumps({"status": "rejected", "note": "gehört in die Keycloak-Doku"}).encode(),
                 dict(MAINT, **{"Content-Type": "application/json"}))
    ok("API close", st == 200 and json.loads(b)["status"] == "rejected", b)

    # --- Lücken (§15.4) -----------------------------------------------------
    for q in ["Wolkenkratzer Tunnel", "Wolkenkratzer Tunnel", "mail an max@example.org 0171234567890"]:
        lib.search(q, roles=ix.UNFILTERED)
    lib.search("backup create", roles=ix.UNFILTERED)
    e, res = maint("gaps_report", {})
    words = {g["words"]: g["n"] for g in res["gaps"]}
    ok("Lücken gezählt (Stämme, sortiert)", words.get("tunnel wolkenkratz") == 2, words)
    ok("Lücken ohne Mailadresse und lange Nummern", not any("@" in w or "0171" in w or "example" in w
                                                            for w in words), words)
    ok("guter Treffer ist keine Lücke", not any("backup" in w for w in words), words)

    # --- M14: keine Adresse, keine Identität ----------------------------------
    con = sqlite3.connect(os.path.join(data, "requests.db"))
    dump = "\n".join(con.iterdump())
    con.close()
    ok("M14: weder IP noch Benutzer noch User-Agent in der Datenbank",
       "203.0.113.7" not in dump and "geheim@example.org" not in dump and "Testclient" not in dump)

    # --- Grenzen ----------------------------------------------------------------
    env["HELP_REQUESTS_PER_HOUR"] = "1"
    e, res = ask({"topic": "Ganz neues Thema Quokka Zebra Giraffe", "question": "egal"})
    ok("Grenze neu/Stunde: neue Anfrage abgelehnt", e and "Grenze" in res, res)
    e, res = ask(TUNNEL2)
    ok("Grenze: Stimmen gehen weiter", not e and res["status"] == "vote", res)
    env.pop("HELP_REQUESTS_PER_HOUR")

    # --- Fristen (D17) ------------------------------------------------------
    con = sqlite3.connect(os.path.join(data, "requests.db"))
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=181)).strftime("%Y-%m-%dT%H:%M:%SZ")
    con.execute("UPDATE request SET closed_at=? WHERE id=?", (old, rid))
    oldday = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=91)).strftime("%Y-%m-%d")
    con.execute("INSERT INTO gap VALUES (?,?,5)", (oldday, "alt lücke"))
    con.commit()
    con.close()
    e, res = maint("requests_list", {"status": "all"})
    ok("D17: geschlossen nach 180 Tagen gelöscht", all(x["id"] != rid for x in res["requests"]))
    con = sqlite3.connect(os.path.join(data, "requests.db"))
    ok("D17: Lücken nach 90 Tagen verdichtet", con.execute("SELECT count FROM gap_total WHERE words='alt lücke'").fetchone() == (5,)
       and not con.execute("SELECT 1 FROM gap WHERE words='alt lücke'").fetchone())
    con.close()

    # --- Verwaltung und Status ------------------------------------------------
    st, b = http("GET", "/admin", headers={"X-OAAP-Roles": "admin"})
    ok("Verwaltung zeigt Anfragen und Schlüsselstand", "Anfragen nach Anleitungen" in b and "Schlüssel gesetzt" in b)
    st, b = http("GET", "/public/api/v1/status")
    s_ = json.loads(b)["requests"]
    ok("Status nennt Schalter und ob ein Schlüssel gesetzt ist (nie den Digest)",
       s_ == {"enabled": True, "maintainer_key": True} and env["HELP_MAINT_KEY_SHA256"] not in b)

    # --- Dockerfile kopiert jedes Modul ----------------------------------------
    df = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()
    mods = sorted(f for f in os.listdir(HERE) if f.endswith(".py")
                  and not f.startswith("test_") and f != "make_seed.py")
    ok("Dockerfile kopiert jedes Modul", all(m in df for m in mods), [m for m in mods if m not in df])
finally:
    web.shutdown()
    origin.shutdown()
    shutil.rmtree(data, ignore_errors=True)

print("")
print(f"{'ALLE PRUEFUNGEN BESTANDEN' if not fails else str(fails) + ' FEHLGESCHLAGEN'}")
sys.exit(1 if fails else 0)
