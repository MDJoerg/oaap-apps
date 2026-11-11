#!/usr/bin/env python3
"""help 0.1.0 (RFC-0078 Stufe 1) ohne Knoten.

Eine nachgebaute Destination liefert Archive aus; die App läuft echt (HTTP)
in einem Wegwerf-Verzeichnis. Geprüft werden die Messpunkte, die ohne
Knoten gehen: M3 (geänderte Doku nach „jetzt holen“), M4 (kaputtes Archiv
lässt den alten Index antworten), M5 (local überdeckt), M6 (Linkvorlage
wirkt sofort), M7 (rohes HTML escaped) -- dazu jede Tür: Seite, API, MCP.

Aufruf: python3 test_help.py
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


def archive(files, top="oaap-docs-main", extra=None):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, text in files.items():
            data = text.encode("utf-8")
            ti = tarfile.TarInfo(f"{top}/{name}")
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        for ti, data in (extra or []):
            tf.addfile(ti, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


BACKUP = """---
title: "Backup auf der CLI einrichten"
audience: [server_admin, support]
checked_against: "0.1.247"
checked_on: 2026-11-10
lang: de
tags: [backup, cli]
---
# Backup auf der CLI einrichten

> Geprüft gegen Referenz **0.1.247**.

## Voraussetzungen

Ein Ziel außerhalb des Datenverzeichnisses, siehe [Server aufsetzen](server-aufsetzen.md).

## Schritt 1: Zeitplan setzen

```sh
sudo oaap backup schedule --to /mnt/sicherung
```

**Prüfen:** `oaap backup status` nennt den nächsten Lauf.
"""

SERVER = """# Einen OAAP-Server aufsetzen

## Installieren

Mit install.sh aus dem Git-Checkout.
"""

PROMOTE = """---
id: cli/app/promote
title: "oaap app promote"
audience: [server_admin]
checked_against: "0.1.248"
checked_on: 2026-11-11
lang: en
tags: [cli, app]
---
# `oaap app promote`

take over a tested package into production.

**Root:** needs `sudo`.
"""

EVIL = """---
title: "Böse"
audience: [public]
---
# Böse

<script>alert(1)</script> und <img src=x onerror=alert(2)>

[klick](javascript:alert(3)) und [gut](https://example.org/)
"""

DOCS = {"szenarien/backup-cli.md": BACKUP, "szenarien/server-aufsetzen.md": SERVER,
        "cli/generated/app-promote.md": PROMOTE, "faq/boese.md": EVIL,
        "README.md": "# OAAP-Doku\n\nÜberblick.\n", "bild.png": "kein md"}


class Origin(BaseHTTPRequestHandler):
    """Die nachgebaute Destination: liefert, was `served` gerade enthält."""
    served = {"body": archive(DOCS), "code": 200, "location": ""}
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Origin.hits.append(self.path)
        s = Origin.served
        self.send_response(s["code"])
        if s["location"]:
            self.send_header("Location", s["location"])
        self.send_header("Content-Length", str(len(s["body"])))
        self.end_headers()
        self.wfile.write(s["body"])


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


origin = serve(Origin)
data = tempfile.mkdtemp(prefix="help-test-")
env = {"OAAP_DESTINATION_DOCS_ORIGIN_URL": f"http://127.0.0.1:{origin.server_port}/destinations/docs-origin/",
       "HELP_WEB_TEMPLATE": "https://example.org/oaap-docs/blob/main/{path}"}
lib = Library(data, env)
web = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
web.lib = lib
threading.Thread(target=web.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{web.server_port}"


def http(method, path, body=None, headers=None):
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=headers or {})

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8"), dict(e.headers)


def mcp(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if id_ is not None:
        msg["id"] = id_
    st, body, _ = http("POST", "/mcp", json.dumps(msg).encode(), {"Content-Type": "application/json"})
    return st, (json.loads(body) if body else None)


def tool(name, args):
    st, r = mcp("tools/call", {"name": name, "arguments": args})
    c = r["result"]["content"][0]["text"]
    return r["result"]["isError"], (json.loads(c) if c.startswith(("{", "[")) else c)


try:
    # --- Abruf und Index ---------------------------------------------------
    st = lib.refresh()
    ok("Abruf über die Destination, Pfad angehängt",
       st["oaap-docs"]["ok"] and Origin.hits[-1] == "/destinations/docs-origin/tar.gz/refs/heads/main",
       (st, Origin.hits))
    ok("nur Markdown, erster Ordner abgeschnitten", st["oaap-docs"]["files"] == 5, st)
    ok("O2: FTS5 vorhanden (lokal)", lib.index.fts5())
    d = lib.read("szenarien/backup-cli")
    ok("Kopf gelesen: Titel, Prüfstand, Zielgruppe, Tags",
       d and d["title"] == "Backup auf der CLI einrichten" and d["checked_against"] == "0.1.247"
       and d["audience"] == ["server_admin", "support"] and d["tags"] == ["backup", "cli"], d)
    ok("ohne Kopf: id = Pfad, Zielgruppe Betreiber",
       lib.read("szenarien/server-aufsetzen")["audience"] == ix.OPERATOR_ROLES)
    ok("explizite id gilt", lib.read("cli/app/promote") is not None
       and lib.read("cli/generated/app-promote") is None)

    # --- Suche --------------------------------------------------------------
    hits = lib.search("Backup CLI einrichten")
    ok("Suche findet die Anleitung zuerst", hits and hits[0]["id"] == "szenarien/backup-cli", hits[:2])
    h = hits[0]
    ok("Treffer trägt Abschnitt, Ausschnitt, Herkunft, Prüfstand, Link",
       h["source"] == "oaap-docs" and h["trust"] == "platform" and h["checked_on"] == "2026-11-10"
       and h["url"].startswith("https://example.org/oaap-docs/blob/main/szenarien/backup-cli.md"), h)
    ok("Präfix: 'Backups' findet 'Backup'", any(x["id"] == "szenarien/backup-cli" for x in lib.search("Backups")))
    ok("Umlaute: 'Prufen' findet 'Prüfen'", any(x["id"] == "szenarien/backup-cli" for x in lib.search("Prufen")))
    ok("ODER als Rückfall: ein unbekanntes Wort stört nicht",
       any(x["id"] == "szenarien/backup-cli" for x in lib.search("backup quatschwort")))
    ok("FTS-Syntax in der Frage wirft nicht", isinstance(lib.search('"NEAR( AND *'), list))
    ok("Abschnittstreffer: Schritt 1", any(x["anchor"] == "schritt-1-zeitplan-setzen" for x in lib.search("Zeitplan")))
    vh = lib.search("promote", version="0.1.100")
    ok("Version ohne applies_from: nicht markiert", vh and "applies_to_version" in vh[0] and vh[0]["applies_to_version"] is True, vh)

    # --- Lesen ----------------------------------------------------------------
    sec = lib.read("szenarien/backup-cli", section="Schritt 1: Zeitplan setzen")
    ok("Abschnitt lesen (per Überschrift)", sec and "oaap backup schedule" in sec["markdown"]
       and "Voraussetzungen" not in sec["markdown"], sec)
    ok("Abschnitt lesen (per Anker)", lib.read("szenarien/backup-cli", section="voraussetzungen") is not None)
    ok("Abschnittsliste", [s["anchor"] for s in d["sections"]] == ["voraussetzungen", "schritt-1-zeitplan-setzen"])
    ok("fehlt → None", lib.read("gibt/es-nicht") is None)
    t = lib.topics()
    ok("Themen je Quelle", "oaap-docs" in t and len(t["oaap-docs"]) == 5)
    ok("Themen mit Präfix", [x["id"] for x in lib.topics("szenarien/")["oaap-docs"]]
       == ["szenarien/backup-cli", "szenarien/server-aufsetzen"])

    # --- API ------------------------------------------------------------------
    s, b, _ = http("GET", "/api/v1/search?q=backup")
    ok("API search", s == 200 and json.loads(b)["hits"][0]["id"] == "szenarien/backup-cli")
    s, b, _ = http("GET", "/api/v1/docs/szenarien/backup-cli?section=voraussetzungen")
    ok("API docs mit Abschnitt", s == 200 and json.loads(b)["section"] == "Voraussetzungen")
    s, b, _ = http("GET", "/api/v1/docs/nichts")
    ok("API docs 404", s == 404)
    s, b, _ = http("GET", "/api/v1/topics?prefix=cli/")
    ok("API topics", s == 200 and json.loads(b)["oaap-docs"][0]["id"] == "cli/app/promote")
    s, b, _ = http("GET", "/healthz")
    ok("healthz", s == 200 and json.loads(b)["documents"] == 5)
    s, b, _ = http("GET", "/api/v1/status")
    ok("status nennt Quelle und Destination", s == 200 and json.loads(b)["destination"] is True
       and json.loads(b)["sources"]["oaap-docs"]["ok"])

    # --- MCP ------------------------------------------------------------------
    s, r = mcp("initialize", {"protocolVersion": "2025-06-18"})
    ok("MCP initialize mit tools, resources, prompts", s == 200
       and set(r["result"]["capabilities"]) == {"tools", "resources", "prompts"}
       and r["result"]["protocolVersion"] == "2025-06-18")
    s, r = mcp("tools/list")
    names = [x["name"] for x in r["result"]["tools"]]
    ok("drei Werkzeuge, alle lesend", names == ["help_search", "help_read", "help_topics"]
       and all(x["annotations"]["readOnlyHint"] for x in r["result"]["tools"]))
    ok("Werkzeugtexte: Doku zum Zitieren, nicht ausführen",
       all("nicht selbst aus" in x["description"] for x in r["result"]["tools"][:2]))
    e, res = tool("help_search", {"query": "backup einrichten", "limit": 3})
    ok("help_search", not e and res["hits"][0]["id"] == "szenarien/backup-cli" and res["count"] <= 3)
    e, res = tool("help_read", {"id": "szenarien/backup-cli"})
    ok("help_read", not e and "sudo oaap backup schedule" in res["markdown"] and res["url"])
    e, res = tool("help_read", {"id": "nichts"})
    ok("help_read fehlt → isError", e)
    e, res = tool("help_topics", {"prefix": "faq/"})
    ok("help_topics", not e and res["oaap-docs"][0]["id"] == "faq/boese")
    e, res = tool("help_search", {"query": ""})
    ok("help_search ohne query → isError", e)
    s, r = mcp("tools/call", {"name": "rm_rf", "arguments": {}})
    ok("unbekanntes Werkzeug → Fehler", "error" in r)
    s, r = mcp("resources/list")
    ok("Resources help://quelle/id", any(x["uri"] == "help://oaap-docs/szenarien/backup-cli"
                                         for x in r["result"]["resources"]))
    s, r = mcp("resources/read", {"uri": "help://oaap-docs/szenarien/backup-cli"})
    ok("Resource lesen", "Zeitplan" in r["result"]["contents"][0]["text"])
    s, r = mcp("resources/read", {"uri": "help://oaap-docs/nichts"})
    ok("Resource fehlt → -32002", r["error"]["code"] == -32002)
    s, r = mcp("resources/templates/list")
    ok("Resource-Vorlage", r["result"]["resourceTemplates"][0]["uriTemplate"] == "help://{source}/{id}")
    s, r = mcp("prompts/list")
    ok("Prompts guide und cli", [p["name"] for p in r["result"]["prompts"]] == ["guide", "cli"])
    s, r = mcp("prompts/get", {"name": "guide", "arguments": {"goal": "Backup einrichten"}})
    txt = r["result"]["messages"][0]["content"]["text"]
    ok("guide: Ziel, ein Schritt, Prüfung, Original", "Backup einrichten" in txt and "GENAU EINEN" in txt
       and "Link zum Original" in txt)
    s, r = mcp("prompts/get", {"name": "cli", "arguments": {"task": "Test nach Produktiv"}})
    ok("cli-Prompt nennt sudo", "sudo" in r["result"]["messages"][0]["content"]["text"])
    s, r = mcp("notifications/initialized", id_=None)
    ok("Benachrichtigung → 202", s == 202)
    s, _, _ = http("GET", "/mcp")
    ok("GET /mcp → 405", s == 405)
    s, _, _ = http("POST", "/mcp", b"x=1", {"Content-Type": "application/x-www-form-urlencoded"})
    ok("Formular an /mcp → 415", s == 415)

    # --- Seite ------------------------------------------------------------------
    s, b, hd = http("GET", "/")
    ok("Startseite listet Themen", s == 200 and "Backup auf der CLI einrichten" in b
       and "default-src 'none'" in hd.get("Content-Security-Policy", ""))
    s, b, _ = http("GET", "/?q=backup")
    ok("Suchseite", s == 200 and "/doc/oaap-docs/szenarien/backup-cli#" in b)
    s, b, _ = http("GET", "/doc/oaap-docs/szenarien/backup-cli")
    ok("Leseseite mit Prüfstand und Original", s == 200 and "geprüft gegen 0.1.247" in b
       and "Original öffnen" in b and "<pre><code" in b)
    ok("relativer Link zeigt auf /p/", 'href="/p/oaap-docs/szenarien/server-aufsetzen.md"' in b, b[:2000])
    s, b, hd = http("GET", "/p/oaap-docs/szenarien/server-aufsetzen.md")
    ok("/p/ leitet auf die Leseseite", s == 303 and hd.get("Location") == "/doc/oaap-docs/szenarien/server-aufsetzen")
    # M7
    s, b, _ = http("GET", "/doc/oaap-docs/faq/boese")
    ok("M7: <script> und onerror escaped", "<script>" not in b and "&lt;script&gt;" in b
       and "<img" not in b)
    ok("M7: javascript:-Link wird Text, https bleibt Link",
       "javascript:" not in b.split("<main>")[1] and 'href="https://example.org/"' in b)
    s, b, _ = http("GET", "/doc/oaap-docs/nichts")
    ok("Leseseite 404", s == 404)

    # --- M3: geänderte Doku nach „jetzt holen“ -------------------------------
    docs2 = dict(DOCS)
    docs2["szenarien/backup-cli.md"] = BACKUP.replace("Zeitplan setzen", "Zeitplan festlegen Wolkenkuckucksheim")
    Origin.served = {"body": archive(docs2), "code": 200, "location": ""}
    lib.refresh()
    ok("M3: neuer Text gefunden, ohne Neuausrollen", lib.search("Wolkenkuckucksheim"))
    good_sha = lib.state()["last_good"]["sha256"]

    # --- M4: kaputte Archive lassen den alten Index antworten ---------------
    def refused(body, label, code=200, location="", max_bytes=None):
        Origin.served = {"body": body, "code": code, "location": location}
        if max_bytes:
            lib.env["HELP_MAX_BYTES"] = str(max_bytes)
        st = lib.refresh()
        lib.env.pop("HELP_MAX_BYTES", None)
        e = st["oaap-docs"]
        still = bool(lib.search("Wolkenkuckucksheim"))
        ok(f"M4 {label}: abgelehnt, alter Index antwortet, Fehler steht da",
           not e["ok"] and e["error"] and still and st["last_good"]["sha256"] == good_sha, e)

    full = archive(docs2)
    refused(full[: len(full) // 2], "abgeschnitten")
    ti = tarfile.TarInfo("oaap-docs-main/../../etc/passwd.md")
    ti.size = 1
    refused(archive(DOCS, extra=[(ti, b"x")]), "Pfad mit ..")
    ln = tarfile.TarInfo("oaap-docs-main/link.md")
    ln.type, ln.linkname = tarfile.SYMTYPE, "/etc/passwd"
    refused(archive(DOCS, extra=[(ln, None)]), "Link")
    refused(full, "zu groß", max_bytes=100)
    refused(b"", "Weiterleitung", code=302, location="https://elsewhere.example/x.tar.gz")
    refused(b"nope", "HTTP 404", code=404)
    refused(archive({"bild.png": "x"}), "ohne Markdown")
    saved = lib.env.pop("OAAP_DESTINATION_DOCS_ORIGIN_URL")
    st = lib.refresh()
    ok("M4 ohne Destination: Fehler nennt den Weg", "Destination" in st["oaap-docs"]["error"]
       and lib.search("Wolkenkuckucksheim"))
    lib.env["OAAP_DESTINATION_DOCS_ORIGIN_URL"] = saved
    Origin.served = {"body": full, "code": 200, "location": ""}
    lib.refresh()

    # --- M6: die Linkvorlage wirkt sofort -----------------------------------
    lib.env["HELP_WEB_TEMPLATE"] = "https://forgejo.example/oaap/oaap-docs/src/branch/main/{path}"
    ok("M6: neue Vorlage, jeder Link neu, ohne Abruf",
       lib.search("backup")[0]["url"].startswith("https://forgejo.example/oaap/oaap-docs/src/branch/main/szenarien/backup-cli.md"))
    lib.env["HELP_WEB_TEMPLATE"] = ""
    ok("leere Vorlage: kein Link", lib.read("szenarien/backup-cli")["url"] == "")

    # --- M5 + Verwaltung: local überdeckt ------------------------------------
    note = "---\nid: szenarien/backup-cli\ntitle: \"Backup bei uns\"\n---\n# Backup bei uns\n\nBei uns sichert das NAS Zitronenfalter.\n"
    form = f"name=notizen/backup.md&text={urllib.request.quote(note)}".encode()
    s, _, _ = http("POST", "/admin/local", form, {"Content-Type": "application/x-www-form-urlencoded"})
    ok("Verwaltung ohne admin → 403", s == 403)
    s, _, _ = http("POST", "/admin/local", form, {"X-OAAP-Roles": "admin,user",
                                                  "Content-Type": "application/x-www-form-urlencoded"})
    ok("Verwaltung ohne Origin → 403", s == 403)
    adm = {"X-OAAP-Roles": "admin", "Origin": BASE, "Content-Type": "application/x-www-form-urlencoded"}
    s, _, hd = http("POST", "/admin/local", form, adm)
    ok("Notiz ablegen", s == 303 and hd["Location"].startswith("/admin?m="), (s, hd))
    d = lib.read("szenarien/backup-cli")
    ok("M5: local antwortet unter der id", d["source"] == "local" and "Zitronenfalter" in d["markdown"]
       and d["other_versions"] == ["oaap-docs:szenarien/backup-cli"], d)
    o = lib.read("oaap-docs:szenarien/backup-cli")
    ok("M5: Original bleibt lesbar, als überdeckt markiert", o and o["shadowed_by"] == "local"
       and "Wolkenkuckucksheim" in o["markdown"])
    ok("M5: Suche zeigt nur die antwortende Fassung",
       not lib.search("Wolkenkuckucksheim") and lib.search("Zitronenfalter")[0]["source"] == "local")
    s, b, _ = http("GET", "/doc/oaap-docs/szenarien/backup-cli")
    ok("Leseseite des Originals nennt die Überdeckung", "überdeckt von" in b)
    s, b, _ = http("GET", "/admin", headers={"X-OAAP-Roles": "admin"})
    ok("Verwaltungsseite zeigt Quelle und Notiz", s == 200 and "notizen/backup.md" in b and "Jetzt holen" in b)
    s, _, _ = http("GET", "/admin")
    ok("Verwaltungsseite ohne admin → 403", s == 403)
    s, _, _ = http("POST", "/admin/local", b"name=../x.md&text=a", adm)
    ok("Dateiname mit .. abgelehnt", s == 400)
    s, _, hd = http("POST", "/admin/fetch", b"", adm)
    ok("„Jetzt holen“ über die Seite", s == 303 and "geholt" in hd["Location"])
    ok("Notiz übersteht den Abruf", lib.read("szenarien/backup-cli")["source"] == "local")
    s, _, _ = http("POST", "/admin/local/delete", b"name=notizen/backup.md", adm)
    ok("Notiz entfernen: Original antwortet wieder", s == 303
       and lib.read("szenarien/backup-cli")["source"] == "oaap-docs")

    # --- Sichtbarkeit (Grundlage Stufe 2): eine Funktion hinter jeder Tür ----
    lib.env["HELP_AUDIENCE_FILTER"] = "on"
    user = {"user"}
    ok("Filter an: user sieht kein Betreiber-Dokument (search/read/topics/resources)",
       not lib.search("backup", roles=user) and lib.read("szenarien/backup-cli", roles=user) is None
       and all(x["id"] != "szenarien/backup-cli" for x in lib.topics(roles=user).get("oaap-docs", []))
       and all("backup-cli" not in x["uri"] for x in lib.resources(roles=user)))
    ok("Filter an: public bleibt sichtbar", lib.read("faq/boese", roles=user) is not None)
    ok("Filter an: support sieht es", lib.read("szenarien/backup-cli", roles={"support"}) is not None)
    lib.env["HELP_AUDIENCE_FILTER"] = "off"

    # --- O2-Rückfall ohne FTS5 --------------------------------------------------
    real = ix.fts5_available
    ix.fts5_available = lambda: False
    lib.rebuild_local()
    ix.fts5_available = real
    ok("ohne FTS5: einfache Suche findet", not lib.index.fts5()
       and lib.search("Wolkenkuckucksheim")[0]["id"] == "szenarien/backup-cli")
    lib.rebuild_local()
    ok("mit FTS5 zurück", lib.index.fts5())

    # --- Kopf-Leser ----------------------------------------------------------
    fm, body = ix.front_matter('---\ntitle: "A # B"\ntags: [x, y] # Kommentar\nunbekannt: 1\n---\n# T\n')
    ok("Kopf: # in Anführungszeichen bleibt, Kommentar fällt, Unbekanntes fällt",
       fm == {"title": "A # B", "tags": ["x", "y"]} and body == "# T\n", fm)
finally:
    web.shutdown()
    origin.shutdown()
    shutil.rmtree(data, ignore_errors=True)

print("")
print(f"{'ALLE PRUEFUNGEN BESTANDEN' if not fails else str(fails) + ' FEHLGESCHLAGEN'}")
sys.exit(1 if fails else 0)
