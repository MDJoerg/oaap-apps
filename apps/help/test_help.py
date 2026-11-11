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
import make_seed  # noqa: E402
from library import Library  # noqa: E402

U = ix.UNFILTERED

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
       "HELP_WEB_TEMPLATE": "https://example.org/oaap-docs/blob/main/{path}",
       "HELP_PUBLIC": "on", "HELP_SEED_FILE": os.path.join(HERE, "kein-seed.tar.gz")}
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
    st, body, _ = http("POST", "/public/mcp", json.dumps(msg).encode(), {"Content-Type": "application/json"})
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
    d = lib.read(roles=U, ref="szenarien/backup-cli")
    ok("Kopf gelesen: Titel, Prüfstand, Zielgruppe, Tags",
       d and d["title"] == "Backup auf der CLI einrichten" and d["checked_against"] == "0.1.247"
       and d["audience"] == ["server_admin", "support"] and d["tags"] == ["backup", "cli"], d)
    ok("ohne Kopf: id = Pfad, Zielgruppe Betreiber",
       lib.read(roles=U, ref="szenarien/server-aufsetzen")["audience"] == ix.OPERATOR_ROLES)
    ok("explizite id gilt", lib.read(roles=U, ref="cli/app/promote") is not None
       and lib.read(roles=U, ref="cli/generated/app-promote") is None)

    # --- Suche --------------------------------------------------------------
    hits = lib.search(roles=U, query="Backup CLI einrichten")
    ok("Suche findet die Anleitung zuerst", hits and hits[0]["id"] == "szenarien/backup-cli", hits[:2])
    h = hits[0]
    ok("Treffer trägt Abschnitt, Ausschnitt, Herkunft, Prüfstand, Link",
       h["source"] == "oaap-docs" and h["trust"] == "platform" and h["checked_on"] == "2026-11-10"
       and h["url"].startswith("https://example.org/oaap-docs/blob/main/szenarien/backup-cli.md"), h)
    ok("Präfix: 'Backups' findet 'Backup'", any(x["id"] == "szenarien/backup-cli" for x in lib.search(roles=U, query="Backups")))
    ok("Umlaute: 'Prufen' findet 'Prüfen'", any(x["id"] == "szenarien/backup-cli" for x in lib.search(roles=U, query="Prufen")))
    ok("ODER als Rückfall: ein unbekanntes Wort stört nicht",
       any(x["id"] == "szenarien/backup-cli" for x in lib.search(roles=U, query="backup quatschwort")))
    ok("FTS-Syntax in der Frage wirft nicht", isinstance(lib.search(roles=U, query='"NEAR( AND *'), list))
    ok("Abschnittstreffer: Schritt 1", any(x["anchor"] == "schritt-1-zeitplan-setzen" for x in lib.search(roles=U, query="Zeitplan")))
    vh = lib.search(roles=U, query="promote", version="0.1.100")
    ok("Version ohne applies_from: nicht markiert", vh and "applies_to_version" in vh[0] and vh[0]["applies_to_version"] is True, vh)

    # --- Lesen ----------------------------------------------------------------
    sec = lib.read(roles=U, ref="szenarien/backup-cli", section="Schritt 1: Zeitplan setzen")
    ok("Abschnitt lesen (per Überschrift)", sec and "oaap backup schedule" in sec["markdown"]
       and "Voraussetzungen" not in sec["markdown"], sec)
    ok("Abschnitt lesen (per Anker)", lib.read(roles=U, ref="szenarien/backup-cli", section="voraussetzungen") is not None)
    ok("Abschnittsliste", [s["anchor"] for s in d["sections"]] == ["voraussetzungen", "schritt-1-zeitplan-setzen"])
    ok("fehlt → None", lib.read(roles=U, ref="gibt/es-nicht") is None)
    t = lib.topics(roles=U)
    ok("Themen je Quelle", "oaap-docs" in t and len(t["oaap-docs"]) == 5)
    ok("Themen mit Präfix", [x["id"] for x in lib.topics(roles=U, prefix="szenarien/")["oaap-docs"]]
       == ["szenarien/backup-cli", "szenarien/server-aufsetzen"])

    # --- API ------------------------------------------------------------------
    s, b, _ = http("GET", "/public/api/v1/search?q=backup")
    ok("API search", s == 200 and json.loads(b)["hits"][0]["id"] == "szenarien/backup-cli")
    s, b, _ = http("GET", "/public/api/v1/docs/szenarien/backup-cli?section=voraussetzungen")
    ok("API docs mit Abschnitt", s == 200 and json.loads(b)["section"] == "Voraussetzungen")
    s, b, _ = http("GET", "/public/api/v1/docs/nichts")
    ok("API docs 404", s == 404)
    s, b, _ = http("GET", "/public/api/v1/topics?prefix=cli/")
    ok("API topics", s == 200 and json.loads(b)["oaap-docs"][0]["id"] == "cli/app/promote")
    s, b, _ = http("GET", "/healthz")
    ok("healthz", s == 200 and json.loads(b)["documents"] == 5)
    s, b, _ = http("GET", "/public/api/v1/status")
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
    s, _, _ = http("GET", "/public/mcp")
    ok("GET /mcp → 405", s == 405)
    s, _, _ = http("POST", "/public/mcp", b"x=1", {"Content-Type": "application/x-www-form-urlencoded"})
    ok("Formular an /mcp → 415", s == 415)

    # --- Seite ------------------------------------------------------------------
    s, b, hd = http("GET", "/public/")
    ok("Startseite listet Themen", s == 200 and "Backup auf der CLI einrichten" in b
       and "default-src 'none'" in hd.get("Content-Security-Policy", ""))
    s, b, _ = http("GET", "/public/?q=backup")
    ok("Suchseite", s == 200 and "/public/doc/oaap-docs/szenarien/backup-cli#" in b)
    s, b, _ = http("GET", "/public/doc/oaap-docs/szenarien/backup-cli")
    ok("Leseseite mit Prüfstand und Original", s == 200 and "geprüft gegen 0.1.247" in b
       and "Original öffnen" in b and "<pre><code" in b)
    ok("relativer Link zeigt auf /p/", 'href="/public/p/oaap-docs/szenarien/server-aufsetzen.md"' in b, b[:2000])
    s, b, hd = http("GET", "/public/p/oaap-docs/szenarien/server-aufsetzen.md")
    ok("/p/ leitet auf die Leseseite", s == 303 and hd.get("Location") == "/public/doc/oaap-docs/szenarien/server-aufsetzen")
    # M7
    s, b, _ = http("GET", "/public/doc/oaap-docs/faq/boese")
    ok("M7: <script> und onerror escaped", "<script>" not in b and "&lt;script&gt;" in b
       and "<img" not in b)
    ok("M7: javascript:-Link wird Text, https bleibt Link",
       "javascript:" not in b.split("<main>")[1] and 'href="https://example.org/"' in b)
    s, b, _ = http("GET", "/public/doc/oaap-docs/nichts")
    ok("Leseseite 404", s == 404)

    # --- M3: geänderte Doku nach „jetzt holen“ -------------------------------
    docs2 = dict(DOCS)
    docs2["szenarien/backup-cli.md"] = BACKUP.replace("Zeitplan setzen", "Zeitplan festlegen Wolkenkuckucksheim")
    Origin.served = {"body": archive(docs2), "code": 200, "location": ""}
    lib.refresh()
    ok("M3: neuer Text gefunden, ohne Neuausrollen", lib.search(roles=U, query="Wolkenkuckucksheim"))
    good_sha = lib.state()["last_good"]["sha256"]

    # --- M4: kaputte Archive lassen den alten Index antworten ---------------
    def refused(body, label, code=200, location="", max_bytes=None):
        Origin.served = {"body": body, "code": code, "location": location}
        if max_bytes:
            lib.env["HELP_MAX_BYTES"] = str(max_bytes)
        st = lib.refresh()
        lib.env.pop("HELP_MAX_BYTES", None)
        e = st["oaap-docs"]
        still = bool(lib.search(roles=U, query="Wolkenkuckucksheim"))
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
       and lib.search(roles=U, query="Wolkenkuckucksheim"))
    lib.env["OAAP_DESTINATION_DOCS_ORIGIN_URL"] = saved
    Origin.served = {"body": full, "code": 200, "location": ""}
    lib.refresh()

    # --- M6: die Linkvorlage wirkt sofort -----------------------------------
    lib.env["HELP_WEB_TEMPLATE"] = "https://forgejo.example/oaap/oaap-docs/src/branch/main/{path}"
    ok("M6: neue Vorlage, jeder Link neu, ohne Abruf",
       lib.search(roles=U, query="backup")[0]["url"].startswith("https://forgejo.example/oaap/oaap-docs/src/branch/main/szenarien/backup-cli.md"))
    lib.env["HELP_WEB_TEMPLATE"] = ""
    ok("leere Vorlage: kein Link", lib.read(roles=U, ref="szenarien/backup-cli")["url"] == "")

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
    d = lib.read(roles=U, ref="szenarien/backup-cli")
    ok("M5: local antwortet unter der id", d["source"] == "local" and "Zitronenfalter" in d["markdown"]
       and d["other_versions"] == ["oaap-docs:szenarien/backup-cli"], d)
    o = lib.read(roles=U, ref="oaap-docs:szenarien/backup-cli")
    ok("M5: Original bleibt lesbar, als überdeckt markiert", o and o["shadowed_by"] == "local"
       and "Wolkenkuckucksheim" in o["markdown"])
    ok("M5: Suche zeigt nur die antwortende Fassung",
       not lib.search(roles=U, query="Wolkenkuckucksheim") and lib.search(roles=U, query="Zitronenfalter")[0]["source"] == "local")
    s, b, _ = http("GET", "/public/doc/oaap-docs/szenarien/backup-cli")
    ok("Leseseite des Originals nennt die Überdeckung", "überdeckt von" in b)
    s, b, _ = http("GET", "/admin", headers={"X-OAAP-Roles": "admin"})
    ok("Verwaltungsseite zeigt Quelle und Notiz", s == 200 and "notizen/backup.md" in b and "Jetzt holen" in b)
    s, _, _ = http("GET", "/admin")
    ok("Verwaltungsseite ohne admin → 403", s == 403)
    s, _, _ = http("POST", "/admin/local", b"name=../x.md&text=a", adm)
    ok("Dateiname mit .. abgelehnt", s == 400)
    s, _, hd = http("POST", "/admin/fetch", b"", adm)
    ok("„Jetzt holen“ über die Seite", s == 303 and "geholt" in hd["Location"])
    ok("Notiz übersteht den Abruf", lib.read(roles=U, ref="szenarien/backup-cli")["source"] == "local")
    s, _, _ = http("POST", "/admin/local/delete", b"name=notizen/backup.md", adm)
    ok("Notiz entfernen: Original antwortet wieder", s == 303
       and lib.read(roles=U, ref="szenarien/backup-cli")["source"] == "oaap-docs")

    # --- O3 + M9: angemeldet gilt der Zielgruppenfilter, an jeder Tür -------
    def api(path, roles):
        return http("GET", path, headers={"X-OAAP-Roles": roles} if roles else {})

    def mcp_as(roles, method, params=None):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        h = {"Content-Type": "application/json"}
        if roles:
            h["X-OAAP-Roles"] = roles
        st, body, _ = http("POST", "/mcp", json.dumps(msg).encode(), h)
        return json.loads(body)

    # Köder: das Betreiber-Dokument szenarien/backup-cli (audience server_admin, support)
    s, b, _ = api("/api/v1/search?q=backup", "user")
    ok("M9 API search: user bekommt das Betreiber-Dokument nicht",
       s == 200 and all(h["id"] != "szenarien/backup-cli" for h in json.loads(b)["hits"]), b[:300])
    s, b, _ = api("/api/v1/docs/szenarien/backup-cli", "user")
    ok("M9 API docs: 404 wie ein fehlendes", s == 404)
    s, b, _ = api("/api/v1/topics", "user")
    ok("M9 API topics: nicht gelistet", "szenarien/backup-cli" not in b)
    s, b, _ = api("/doc/oaap-docs/szenarien/backup-cli", "user")
    ok("M9 Leseseite: 404", s == 404)
    s, b, _ = api("/?q=backup", "user")
    ok("M9 Suchseite: kein Treffer auf das Dokument", "szenarien/backup-cli" not in b)
    r = mcp_as("user", "tools/call", {"name": "help_search", "arguments": {"query": "backup"}})
    ok("M9 MCP help_search", "szenarien/backup-cli" not in r["result"]["content"][0]["text"])
    r = mcp_as("user", "tools/call", {"name": "help_read", "arguments": {"id": "szenarien/backup-cli"}})
    ok("M9 MCP help_read: nicht gefunden", r["result"]["isError"])
    r = mcp_as("user", "resources/list")
    ok("M9 MCP resources/list", all("backup-cli" not in x["uri"] for x in r["result"]["resources"]))
    r = mcp_as("user", "resources/read", {"uri": "help://oaap-docs/szenarien/backup-cli"})
    ok("M9 MCP resources/read: -32002", r.get("error", {}).get("code") == -32002)
    r = mcp_as("user", "tools/call", {"name": "help_topics", "arguments": {}})
    ok("M9 MCP help_topics", "szenarien/backup-cli" not in r["result"]["content"][0]["text"])
    s, b, _ = api("/api/v1/search?q=backup", "")
    ok("angemeldet ohne Rollen: nur public", all("public" in h["audience"] for h in json.loads(b)["hits"]))
    s, b, _ = api("/api/v1/docs/faq/boese", "user")
    ok("public bleibt für user sichtbar", s == 200)
    s, b, _ = api("/api/v1/docs/szenarien/backup-cli", "support,user")
    ok("support sieht das Betreiber-Dokument", s == 200)
    s, b, _ = api("/api/v1/docs/szenarien/server-aufsetzen", "tenant_admin")
    ok("ohne Kopf: tenant_admin sieht es (Betreiberrolle)", s == 200)
    s, b, _ = api("/api/v1/docs/szenarien/server-aufsetzen", "admin")
    ok("ohne Kopf: Mandanten-admin nicht (keine Betreiberrolle)", s == 404)
    s, b, _ = http("GET", "/public/api/v1/docs/szenarien/backup-cli", headers={"X-OAAP-Roles": "user"})
    ok("/public/ bleibt ungefiltert, auch mit Kopfzeile", s == 200)

    # Der öffentliche Pfad nur mit HELP_PUBLIC=on
    lib.env["HELP_PUBLIC"] = "off"
    s, _, _ = http("GET", "/public/api/v1/search?q=backup")
    s2, _ = mcp("ping")
    ok("HELP_PUBLIC=off: /public/ und /public/mcp antworten 404", s == 404 and s2 == 404)
    lib.env["HELP_PUBLIC"] = "on"
    s, _, _ = http("GET", "/public/admin", headers={"X-OAAP-Roles": "admin"})
    ok("/public/admin gibt es nicht", s == 404)
    s, b, _ = http("GET", "/public/")
    ok("öffentliche Seite: kein Link zur Verwaltung, Links mit /public", 'href="/admin"' not in b
       and 'href="/public/doc/' in b)

    # --- M10: der seed antwortet ohne Destination ----------------------------
    seeddir = tempfile.mkdtemp(prefix="help-seed-")
    docsdir = os.path.join(seeddir, "docs")
    os.makedirs(os.path.join(docsdir, "szenarien"))
    with open(os.path.join(docsdir, "szenarien", "aus-dem-paket.md"), "w", encoding="utf-8") as f:
        f.write("# Aus dem Paket\n\nDer Saatgutspeicher antwortet.\n")
    with open(os.path.join(docsdir, "AGENTS.md"), "w", encoding="utf-8") as f:
        f.write("# Agent\n")
    seed_bytes, n = make_seed.build(docsdir)
    ok("make_seed: ohne AGENTS.md, byte-gleich bei gleichem Inhalt",
       n == 1 and make_seed.build(docsdir)[0] == seed_bytes)
    with open(os.path.join(seeddir, "seed.tar.gz"), "wb") as f:
        f.write(seed_bytes)
    lib2 = Library(os.path.join(seeddir, "data"), {"HELP_SEED_FILE": os.path.join(seeddir, "seed.tar.gz")})
    st = lib2.refresh()
    ok("M10: ohne Destination scheitert der Abruf, der seed antwortet",
       not st["oaap-docs"]["ok"] and st.get("answering") == "seed"
       and lib2.search("Saatgutspeicher", roles=U)[0]["id"] == "szenarien/aus-dem-paket", st)
    st = lib2.refresh()
    ok("M10: zweiter Fehlschlag lässt den seed stehen", lib2.search("Saatgutspeicher", roles=U))
    lib2.rebuild_local()
    ok("M10: Notizen neu bauen behält den seed", lib2.search("Saatgutspeicher", roles=U)
       and lib2.state()["answering"] == "seed")
    lib2.env["OAAP_DESTINATION_DOCS_ORIGIN_URL"] = env["OAAP_DESTINATION_DOCS_ORIGIN_URL"]
    st = lib2.refresh()
    ok("M10: erster gelungener Abruf löst den seed ab", st["oaap-docs"]["ok"] and st["answering"] == "fetch"
       and not lib2.search("Saatgutspeicher", roles=U) and lib2.search("Wolkenkuckucksheim", roles=U))
    shutil.rmtree(seeddir, ignore_errors=True)

    # --- O2-Rückfall ohne FTS5 --------------------------------------------------
    real = ix.fts5_available
    ix.fts5_available = lambda: False
    lib.rebuild_local()
    ix.fts5_available = real
    ok("ohne FTS5: einfache Suche findet", not lib.index.fts5()
       and lib.search(roles=U, query="Wolkenkuckucksheim")[0]["id"] == "szenarien/backup-cli")
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
