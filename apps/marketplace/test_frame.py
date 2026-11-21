#!/usr/bin/env python3
"""Marktplatz 0.4: Rahmen, Listen, Objektseiten und MCP-Tür (RFC-0118).

Ein echter Server im Prozess, wie test_marketplace.py. Prüft:
- die Verwaltung trägt Kit und Rahmen der Plattform und den Namen ohne „OAAP“,
  das Schaufenster behält sein eigenes Gesicht,
- Listenbericht: Suche, Filter, Sortierung, Blättern; keine Formulare in Zeilen,
- Objektseiten für Anfragen, Berechtigungen, Fälle; Partner sehen nur Eigenes,
- jedes MCP-Werkzeug: tools/list, tools/call, dieselben Rechte wie die Seite.

Aufruf: py -3 test_frame.py
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from urllib.parse import urlencode

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app as mk                                              # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


DATA = tempfile.mkdtemp(prefix="market-frame-")
srv = mk.make_server(DATA, 0)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "server_admin,admin,user"}
CI = {"X-OAAP-User": "ci", "X-OAAP-Roles": "user"}
ANNA = {"X-OAAP-User": "anna", "X-OAAP-Roles": "user"}
OTHER = {"X-OAAP-User": "fremd", "X-OAAP-Roles": "user"}


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedir)


def call(method, path, headers=None, body=None, ctype=None):
    h = dict(headers or {})
    h.setdefault("Accept-Language", "de")
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with OPENER.open(req, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def form(path, fields, headers=ADMIN):
    return call("POST", path, headers, urlencode(fields).encode(), "application/x-www-form-urlencoded")


def publish(headers, app_id, version, channels="stable"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("oaap-app.yaml", f'oaap_manifest: "0.10"\napp:\n  id: {app_id}\n  name: Probe {app_id}\n'
                                    f'  version: "{version}"\n  type: native\n  description: "Eine Probe."\n')
        z.writestr("Dockerfile", "FROM scratch\n")
    bd = "----t" + uuid.uuid4().hex
    body = (f'--{bd}\r\nContent-Disposition: form-data; name="channels"\r\n\r\n{channels}\r\n'
            f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="p.zip"\r\n'
            'Content-Type: application/zip\r\n\r\n').encode() + buf.getvalue() + f"\r\n--{bd}--\r\n".encode()
    return call("POST", "/api/v1/releases", headers, body, f"multipart/form-data; boundary={bd}")


def mcp(method, params=None, headers=None, rid=1):
    st, b, _ = call("POST", "/mcp", headers or {}, json.dumps(
        {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}).encode(), "application/json")
    return st, (json.loads(b) if b else None)


def tool(name, args, headers):
    st, doc = mcp("tools/call", {"name": name, "arguments": args}, headers)
    res = doc["result"]
    text = res["content"][0]["text"]
    try:
        return res["isError"], json.loads(text)
    except ValueError:
        return res["isError"], text


# ------------------------------------------------------------------ Daten ---
form("/admin/publisher", {"pattern": "probe-*", "kind": "user", "name": "ci", "channels": ""})
for i in range(57):
    publish(CI, f"probe-{i:02d}", "1.0.0")
form("/admin/asset", {"asset": "probe-00", "mode": "principals", "free": ""})     # verborgen
form("/admin/asset", {"asset": "probe-01", "mode": "anyone", "free": "stable"})   # frei
call("POST", "/my/assets/probe-02/request", ANNA, urlencode({"channel": "stable", "note": "Bitte für den Verein"}).encode(),
     "application/x-www-form-urlencoded")
rid = mk.M.requests("open", "probe-02")[0]["id"]

# ------------------------------------------------------------------ Rahmen ---
print("Rahmen und Name")
for path in ("/admin", "/admin/assets", "/admin/requests", "/admin/rights", "/admin/reviews",
             "/admin/taxonomy", "/admin/settings", "/admin/assets/probe-01", f"/admin/requests/{rid}"):
    st, b, h = call("GET", path, ADMIN)
    ok(f"{path}: Kit, Rahmen, Name ohne OAAP", st == 200 and "/platform/theme.css" in b and "/platform/app.js" in b
       and 'class="oaap-header"' in b and '<span class="oaap-appname">Marktplatz</span>' in b
       and "OAAP Marktplatz" not in b and "X-OAAP" not in b, (st, b[:300]))
    ok(f"{path}: CSP lässt Kit und Rahmen zu", "style-src 'self'" in h.get("Content-Security-Policy", "")
       and "connect-src 'self'" in h.get("Content-Security-Policy", ""), h.get("Content-Security-Policy"))
b = call("GET", "/admin", ADMIN)[1]
ok("eine Reiterleiste, Übersicht zuerst, Einstellungen zuletzt",
   b.count('class="oaap-tabs"') == 1 and b.index(">Übersicht<") < b.index(">Einstellungen<"))
ok("die Übersicht zeigt die offene Anfrage", "Bitte für den Verein" in b)
ok("keine Formulare in Tabellenzeilen der Verwaltung",
   not re.search(r"<td>[^<]*<form", "".join(call("GET", p, ADMIN)[1] for p in
                                            ("/admin", "/admin/assets", "/admin/requests", "/admin/rights",
                                             "/admin/reviews", "/admin/taxonomy"))))
pub = call("GET", "/", {})[1]
ok("das Schaufenster behält sein Gesicht (kein Rahmen)", "oaap-header" not in pub and "<header>" in pub)
ok("…und heißt auf Deutsch Marktplatz", "<span>Marktplatz</span>" in pub)
ok("…auf Englisch Marketplace", "<span>Marketplace</span>" in call("GET", "/?lang=en", {})[1])
ok("der Feed nennt den Namen der App", mk.feed_doc(mk.access.ANON, "stable")["name"] == "Marktplatz")

# ------------------------------------------------------------ Listenbericht ---
print("\nListenbericht")
b = call("GET", "/admin/assets", ADMIN)[1]
ok("Blättern ab 50: 1–50 von 57", "1–50 von 57" in b and "Weitere 50" in b, re.findall(r"oaap-countline[^<]*<", b))
b2 = call("GET", "/admin/assets?offset=50", ADMIN)[1]
ok("zweite Seite: 51–57", "51–57 von 57" in b2)
b = call("GET", "/admin/assets?q=probe-05", ADMIN)[1]
ok("Suche findet die Kennung", "probe-05" in b and "probe-06" not in b)
b = call("GET", "/admin/assets?mode=principals", ADMIN)[1]
ok("Filter Sichtbarkeit", "probe-00" in b and "probe-01" not in b)
b = call("GET", "/admin/assets?sort=-id", ADMIN)[1]
ok("Sortierung absteigend, aria-sort", b.index("probe-56") < b.index("probe-10") and 'aria-sort="descending"' in b)
b = call("GET", "/admin/assets?q=gibtsnicht", ADMIN)[1]
ok("leere Auswahl sagt es", "Keine Treffer" in b)

# ----------------------------------------------------------- Objektseiten ---
print("\nObjektseiten")
b = call("GET", f"/admin/requests/{rid}", ADMIN)[1]
ok("Anfrage: Rückweg und Entscheidung", "oaap-back" in b and 'action="/admin/request-grant"' in b
   and 'action="/admin/request-reject"' in b)
st, _, h = form("/admin/request-grant", {"id": rid, "channels": "stable", "answer": "", "back": "/admin/requests"})
ok("Freischalten führt zur Liste zurück", st == 303 and h["Location"].startswith("/admin/requests?m="), h.get("Location"))
eid = next(e["id"] for e in mk.M.entitlements("probe-02"))
b = call("GET", f"/admin/grants/{eid}", ADMIN)[1]
ok("Berechtigung: Entziehen auf der Objektseite", 'action="/admin/revoke"' in b and "anna" in b)
b = call("GET", "/admin/assets/probe-02?tab=zugang", ADMIN)[1]
ok("Asset-Reiter Zugang zeigt Berechtigung und Sichtbarkeit",
   f"/admin/grants/{eid}" in b and 'action="/admin/asset"' in b)
st, _, h = form("/admin/asset", {"asset": "probe-02", "mode": "anyone", "free": "",
                                 "back": "/admin/assets/probe-02?tab=zugang"})
ok("Sichtbarkeit setzen kehrt zum Reiter zurück", h.get("Location", "").startswith("/admin/assets/probe-02?tab=zugang&m="),
   h.get("Location"))
st, _, h = form("/admin/asset", {"asset": "probe-02", "mode": "anyone", "free": "", "back": "https://evil.example/"})
ok("ein fremdes back wird nicht befolgt", h.get("Location", "").startswith("/admin/assets/probe-02"), h.get("Location"))
ok("unbekannte Objekte: 404", call("GET", "/admin/grants/9999", ADMIN)[0] == 404
   and call("GET", "/admin/assets/nix", ADMIN)[0] == 404)
ok("Verwaltung für Nicht-Admins: 404", call("GET", "/admin/assets", CI)[0] == 404
   and call("GET", f"/admin/requests/{rid}", CI)[0] == 404)
old_texts = call("GET", "/admin/assets/probe-02?lang=de", ADMIN)[1]
ok("alte Lesezeichen ?lang= öffnen den Reiter Texte", 'name="lang" value="de"' in old_texts)

print("\nMeine Assets (Partner)")
call("POST", "/my/assets/probe-03/request", ANNA, urlencode({"channel": "stable", "note": "Zweite Bitte"}).encode(),
     "application/x-www-form-urlencoded")
rid2 = mk.M.requests("open", "probe-03")[0]["id"]
b = call("GET", "/my/manage", CI)[1]
ok("Partner: Rahmen und offene Anfrage", "/platform/app.js" in b and "Zweite Bitte" in b
   and f"/my/manage/requests/{rid2}" in b)
ok("Partner: Anfrage als Objektseite", 'name="do" value="grant"' in call("GET", f"/my/manage/requests/{rid2}", CI)[1])
ok("Fremde: Anfrage 404", call("GET", f"/my/manage/requests/{rid2}", OTHER)[0] == 404)
ok("Fremde: Asset 404", call("GET", "/my/manage/assets/probe-03", OTHER)[0] == 404)
ok("Partner: eigenes Asset", call("GET", "/my/manage/assets/probe-03", CI)[0] == 200)
st, _, h = form("/my/manage/request", {"id": rid2, "do": "grant", "channels": "stable", "answer": "",
                                       "back": "/my/manage"}, CI)
ok("Partner schaltet frei", st == 303 and "Freigeschaltet" in urllib.parse.unquote(h.get("Location", "")))

# ------------------------------------------------------------------- MCP ---
print("\nMCP-Tür")
st, doc = mcp("initialize", {"protocolVersion": "2025-06-18"}, ANNA)
ok("initialize", st == 200 and doc["result"]["serverInfo"]["name"] == "oaap-marketplace"
   and "prompts" in doc["result"]["capabilities"])
st, doc = mcp("tools/list", {}, ANNA)
names = {t["name"] for t in doc["result"]["tools"]}
ok("tools/list: drei Lesewerkzeuge", names == {"marketplace_search", "marketplace_read", "marketplace_requests"}, names)
ok("alle lesend", all(t["annotations"]["readOnlyHint"] for t in doc["result"]["tools"]))
st, doc = mcp("prompts/list", {}, ANNA)
ok("prompts/list: zwei Vorlagen", len(doc["result"]["prompts"]) == 2)
ok("GET /mcp: 405", call("GET", "/mcp", ANNA)[0] == 405)

err, res = tool("marketplace_search", {"q": "probe"}, ANNA)
ids = {a["id"] for a in res["assets"]}
ok("search: Anna sieht kein verborgenes Asset", not err and "probe-00" not in ids and res["count"] == 56, res.get("count"))
err, res = tool("marketplace_search", {"q": "probe"}, ADMIN)
ok("search: der Betreiber sieht alle 57", res["count"] == 57 and res["shown"] == 50, res.get("count"))
err, res = tool("marketplace_search", {"q": "probe"}, {})
ok("search: ohne Person nur Öffentliches", "probe-00" not in {a["id"] for a in res["assets"]})
err, res = tool("marketplace_read", {"id": "probe-00"}, ANNA)
ok("read: verborgen = abgelehnt wie 404", err and "gibt es für dich nicht" in res)
err, res = tool("marketplace_read", {"id": "probe-00"}, ADMIN)
ok("read: der Betreiber liest es", not err and res["id"] == "probe-00" and "releases" in res)
err, res = tool("marketplace_read", {"id": "probe-01"}, ANNA)
ok("read: frei und ladbar", not err and res["channels"]["stable"]["obtainable"] is True)
ok("read: keine Schlüssel im Ergebnis", not re.search(r"oaapk_|password|token", json.dumps(res), re.I))
call("POST", "/my/assets/probe-04/request", ANNA, urlencode({"channel": "stable", "note": "Dritte"}).encode(),
     "application/x-www-form-urlencoded")
err, res = tool("marketplace_requests", {}, ADMIN)
ok("requests: der Betreiber sieht die offene Anfrage", not err and any(r["note"] == "Dritte" for r in res["requests"]))
err, res = tool("marketplace_requests", {}, CI)
ok("requests: der Partner sieht die seiner Assets", not err and any(r["asset"] == "probe-04" for r in res["requests"]))
err, res = tool("marketplace_requests", {}, ANNA)
ok("requests: sonst abgelehnt", err)
err, res = tool("marketplace_requests", {}, {})
ok("requests: ohne Person abgelehnt", err)
st, doc = mcp("prompts/get", {"name": "marketplace_find", "arguments": {"frage": "Vereinsvorlage"}}, ANNA)
ok("prompts/get setzt die Frage ein", "Vereinsvorlage" in doc["result"]["messages"][0]["content"]["text"])

print(f"\n{'ALLES GRÜN' if not fails else f'{fails} FEHLER'}")
srv.shutdown()
sys.exit(1 if fails else 0)
