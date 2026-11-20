#!/usr/bin/env python3
"""Vorlagen für Werkzeuge (RFC-0107, Spec `oaap.ai.mcp-gateway` §2.4) -- Ende zu Ende.

Das Gateway läuft echt per HTTP. Sichten, Schlüssel und Profile entstehen direkt in den
Büchern; Server braucht es dafür nicht. Geprüft wird vor allem, was nie passieren darf:
ein Schlüsselwert in einer Datei, auf irgendeinem Weg.

Run: python3 apps/mcp-gateway/test_templates.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
TMP = tempfile.mkdtemp(prefix="mcpgw-tpl-")
os.environ.update({"MCPGW_DATA_DIR": TMP, "MCPGW_MASTER_KEY": "test-tresor-schluessel-0123456789",
                   "MCPGW_CATALOG_MINUTES": "0",
                   "OAAP_IDENTITY_HEADERS": "X-OAAP-User,X-OAAP-Roles,X-OAAP-User-Id,X-OAAP-User-Idp",
                   "MCPGW_ISSUER": "https://auth.example/realms/t", "MCPGW_AUDIENCE": "mcp-gw-t",
                   "MCPGW_SIGNIN_CLIENT": "mcp-claude"})

import app  # noqa: E402
import books  # noqa: E402
import clientfiles  # noqa: E402
from gwcore.fill import TemplateError  # noqa: E402

ok = fail = 0
OUTPUTS = []          # alles, was ein Mensch oder Werkzeug zu sehen bekommt -- am Ende durchsucht


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {str(detail)[:400]}")


gw = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=gw.serve_forever, daemon=True).start()
GW = f"http://127.0.0.1:{gw.server_address[1]}"


def get(path, headers=None):
    req = urllib.request.Request(GW + path, headers=dict({"Host": "gw.example"}, **(headers or {})))
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as exc:
        resp = exc
    body = resp.read().decode()
    OUTPUTS.append(body)
    return resp.status, dict(resp.headers), body


def admin_get(path):
    return get(path, {"X-OAAP-User": "betreiber", "X-OAAP-Roles": "admin"})


def me_get(path, uid="u-anna"):
    return get(path, {"X-OAAP-User": "anna", "X-OAAP-Roles": "user", "X-OAAP-User-Id": uid})


DB = app.DB
for name, ways in (("lesen", "key"), ("beides", "key,signin"), ("nur-anmeldung", "signin"),
                   ("chat", "platform"), ("ohne-profil", "signin")):
    books.save_view(DB, "test", name, "read", ways=ways)
KEY, KID = books.issue(DB, "werkstatt", "test", views=["lesen", "beides"], ceiling="yellow")
OTHER, OTHER_ID = books.issue(DB, "zweiter", "test", views=["chat"], ceiling="yellow")
books.issue(DB, "alle", "test", views=["beides"], kind="profile", groups="*")
books.issue(DB, "team", "test", views=["nur-anmeldung"], kind="profile", groups="/team")

print("-- Vorlagen laden")
T = app.TEMPLATES
check("drei Vorlagen: Claude Code, opencode, VS Code", sorted(T) == ["claude-code", "opencode", "vscode"],
      sorted(T))
check("VS Code bietet nur den Schlüssel an (D5)", T["vscode"]["ways"] == ["key"])
check("jede Vorlage hat Prüfdatum und Format", all(t["checked"] and t["format"] for t in T.values()))
bad_dir = tempfile.mkdtemp(prefix="tpl-bad-")


def refused(tpl):
    for fn in os.listdir(bad_dir):
        os.remove(os.path.join(bad_dir, fn))
    with open(os.path.join(bad_dir, "x.json"), "w", encoding="utf-8") as fh:
        json.dump(tpl, fh)
    try:
        clientfiles.load(bad_dir)
        return ""
    except TemplateError as exc:
        return str(exc)


base = json.loads(json.dumps(T["claude-code"]))
r = refused(dict(base, entry=dict(base["entry"], key={"url": "{url}", "x": "{schluessel}"})))
check("unbekannter Platzhalter: abgewiesen, mit Namen", "{schluessel}" in r, r)
r = refused(dict(base, entry=dict(base["entry"], key={"url": "{url}", "p": "Port {callback_port}"})))
check("ganzer Wert mitten im Text: abgewiesen", "nur als ganzer Wert" in r, r)
r = refused(dict(base, entry=dict(base["entry"], key={"url": "{url}", "h": "Bearer mcp-oaap-abc"})))
check("ein Schlüsselwert in der Vorlage: abgewiesen", "nichts zu suchen" in r, r)
r = refused(dict(base, ways=["key", "signin"], entry={"key": base["entry"]["key"]}))
check("Weg ohne Form: abgewiesen", "signin" in r, r)
r = refused(dict(base, document={"a": "{servers}", "b": "{servers}"}))
check("{servers} zweimal: abgewiesen", "genau einmal" in r, r)
shutil.rmtree(bad_dir, ignore_errors=True)

print("\n-- Reiter Vorlagen, aus einem Schlüssel")
st, _h, html = admin_get(f"/vorlagen?target=claude-code&key={KID}")
check("Seite 200, Reiter aktiv", st == 200 and 'href="vorlagen" class=on' in html, st)
check("Datei mit beiden Sichten des Schlüssels", "https://gw.example/mcp/lesen" in html
      and "https://gw.example/mcp/beides" in html)
check("Platzhalter statt Wert", "${OAAP_MCP_KEY}" in html and KEY not in html)
check("Format und Prüfdatum stehen da", "geprüft am 2026-11-20" in html)
st, h, raw = admin_get(f"/vorlagen?target=claude-code&key={KID}&laden=1")
doc = json.loads(raw)
check("Herunterladen: Anhang .mcp.json, no-store",
      st == 200 and 'filename=".mcp.json"' in h.get("Content-Disposition", "")
      and h.get("Cache-Control") == "no-store", h)
check("Claude Code, Schlüssel: type http, url, Authorization mit ${OAAP_MCP_KEY}",
      doc == {"mcpServers": {v: {"type": "http", "url": f"https://gw.example/mcp/{v}",
                                 "headers": {"Authorization": "Bearer ${OAAP_MCP_KEY}"}}
                             for v in ("lesen", "beides")}}, doc)
st, _h, raw = admin_get(f"/vorlagen?target=vscode&key={KID}&laden=1")
doc = json.loads(raw)
check("VS Code: mcpServers, tools [*], derselbe Platzhalter",
      doc["mcpServers"]["lesen"] == {"type": "http", "url": "https://gw.example/mcp/lesen",
                                     "headers": {"Authorization": "Bearer ${OAAP_MCP_KEY}"},
                                     "tools": ["*"]}, doc)
st, h, raw = admin_get(f"/vorlagen?target=opencode&key={KID}&laden=1")
doc = json.loads(raw)
check("opencode, Schlüssel: remote, oauth false, {env:OAAP_MCP_KEY}",
      doc["mcp"]["servers"]["beides"] == {"type": "remote", "url": "https://gw.example/mcp/beides",
                                          "oauth": False,
                                          "headers": {"Authorization": "Bearer {env:OAAP_MCP_KEY}"}}
      and 'filename="opencode.jsonc"' in h.get("Content-Disposition", ""), doc)

print("\n-- von Hand, Anmeldung")
st, _h, raw = admin_get("/vorlagen?target=claude-code&pick=beides:signin&pick=lesen:key&laden=1")
doc = json.loads(raw)["mcpServers"]
check("Claude Code, Anmeldung: clientId und callbackPort als Zahl",
      doc["beides"] == {"type": "http", "url": "https://gw.example/mcp/beides",
                        "oauth": {"clientId": "mcp-claude", "callbackPort": 33418}}, doc)
check("... und daneben eine Sicht mit Schlüssel", "headers" in doc["lesen"], doc)
st, _h, raw = admin_get("/vorlagen?target=opencode&pick=beides:signin&laden=1")
doc = json.loads(raw)["mcp"]["servers"]["beides"]
check("opencode, Anmeldung: client_id, callback_port, redirect_uri",
      doc["oauth"] == {"client_id": "mcp-claude", "callback_port": 33418,
                       "redirect_uri": "http://localhost:33418/callback"}, doc)
st, _h, html = admin_get("/vorlagen?target=vscode&pick=beides:signin")
check("VS Code mit Anmeldung: keine Datei, Grund genannt",
      "Keine Datei" in html and "noch nicht an" in html, html[-600:])
st, _h, html = admin_get("/vorlagen?target=claude-code&pick=lesen:signin")
check("Sicht ohne den Zugang: keine Datei", "hat den Zugang" in html and "Keine Datei" in html)
st, _h, html = admin_get("/vorlagen?target=claude-code&pick=lesen:key&pick=lesen:key")
check("dieselbe Sicht zweimal: keine Datei", "zweimal gewählt" in html)
st, _h, html = admin_get("/vorlagen")
check("Sicht nur mit Plattform: nicht wählbar, Grund steht da",
      'value="chat:' not in html and "Ohne Datei: chat" in html)
check("Schlüssel in der Auswahl mit Namen, nie mit Wert", "werkstatt (lesen, beides)" in html
      and KEY not in html)
old = app.SIGNIN_CLIENT
app.SIGNIN_CLIENT = ""
st, _h, html = admin_get("/vorlagen?target=claude-code&pick=beides:signin")
check("Anmeldung ohne Client für Personen: keine Datei, die Lücke genannt",
      "Keine Datei" in html and "Client für Personen fehlt" in html)
app.SIGNIN_CLIENT = old
st, _h, html = admin_get("/vorlagen?target=claude-code&key=gibt-es-nicht")
check("unbekannter Schlüssel: keine Datei", "gibt es nicht (mehr)" in html)
st, _h, html = admin_get("/vorlagen?target=unbekannt&pick=lesen:key")
check("unbekanntes Werkzeug: keine Datei", "kennt das Gateway nicht" in html)

print("\n-- Ausgelassen nach der Regel eines Aufrufs")
real_light = books.view_light
books.view_light = lambda db, v: "red" if v == "beides" else "green"
st, _h, html = admin_get(f"/vorlagen?target=claude-code&key={KID}")
books.view_light = real_light
check("rote Sicht über der Ampel des Schlüssels: ausgelassen, mit Grund",
      "Sicht beides ausgelassen" in html and "https://gw.example/mcp/lesen" in html, html[-900:])

print("\n-- Schlüsselkasten und Anmeldung")
page = app.admin_page("betreiber", "admin", "gw.example",
                      secret={"label": "neu", "value": "mcp-oaap-NURJETZT", "views": ["lesen"],
                              "id": KID})
check("Kasten nach dem Ausstellen verweist auf die Vorlagen dieses Schlüssels",
      f"vorlagen?key={KID}" in page)
card = app.signin_card("gw.example")
OUTPUTS.append(card)
check("Reiter Anmeldung: .mcp.json aus der Vorlage, Verweis auf Vorlagen",
      '&quot;clientId&quot;: &quot;mcp-claude&quot;' in card and "vorlagen?pick=beides%3Asignin" in card, card[-800:])

print("\n-- Selbstbedienung mit dem Schlüssel (§3.1)")
st, h, raw = get("/mcp/_client-config?target=opencode", {"Authorization": "Bearer " + KEY})
doc = json.loads(raw) if st == 200 else {}
check("200, die Sichten des Schlüssels, als Anhang, no-store",
      st == 200 and sorted(doc["mcp"]["servers"]) == ["beides", "lesen"]
      and h.get("Cache-Control") == "no-store" and "attachment" in h.get("Content-Disposition", ""),
      (st, raw[:300]))
check("... ohne den Wert", KEY not in raw)
audit = [r for r in books.audit(DB, 20) if r["action"] == "client.config"]
check("Prüfspur: Schlüssel und Werkzeug", audit and audit[0]["actor"] == "key:werkstatt"
      and audit[0]["subject"] == "opencode", [dict(a) for a in audit])
check("keine Messzeile", DB.execute("SELECT COUNT(*) n FROM usage").fetchone()["n"] == 0)
st, h, _raw = get("/mcp/_client-config?target=opencode", {"Authorization": "Bearer mcp-oaap-falsch"})
check("falscher Schlüssel: 401 wie überall", st == 401 and "resource_metadata" in h.get("WWW-Authenticate", ""), st)
st, _h, _raw = get("/mcp/_client-config?target=opencode")
check("ohne Schlüssel: 401", st == 401, st)
st, _h, raw = get("/mcp/_client-config?target=goose", {"Authorization": "Bearer " + KEY})
check("unbekanntes Werkzeug: 400 mit der Liste", st == 400 and "claude-code" in raw, (st, raw))
st, _h, raw = get("/mcp/_client-config?target=vscode", {"Authorization": "Bearer " + OTHER})
check("Schlüssel nur für eine Plattform-Sicht: 409, ausgelassen mit Grund",
      st == 409 and '"view": "chat"' in raw, (st, raw))
books.revoke(DB, "werkstatt", "test")
st, _h, _raw = get("/mcp/_client-config?target=opencode", {"Authorization": "Bearer " + KEY})
check("widerrufener Schlüssel: 401", st == 401, st)
st, _h, html = admin_get(f"/vorlagen?target=claude-code&key={KID}")
check("widerrufener Schlüssel im Reiter: keine Datei", "gibt es nicht (mehr)" in html)

print("\n-- /me: Anmelde-Sichten (§3.2)")
st, _h, html = me_get("/me")
check("Karte „In meinem Werkzeug einrichten“", st == 200 and "In meinem Werkzeug einrichten" in html, st)
check("Sicht mit Profil * : für alle", "beides</td><td>alle mit Anmeldung" in html, html[-1500:])
check("Sicht mit Gruppenprofil: die Anmeldung entscheidet", "nur-anmeldung</td><td>bestimmte Gruppen" in html)
check("Sicht ohne Profil, Schlüssel- und Plattform-Sichten: nicht da",
      "ohne-profil</td>" not in html and ">lesen</td>" not in html and ">chat</td>" not in html)
check("Download nur für Werkzeuge mit Anmeldung (nicht VS Code)",
      "me/client-config?target=claude-code" in html and "target=vscode" not in html)
st, h, raw = me_get("/me/client-config?target=claude-code")
doc = json.loads(raw) if st == 200 else {}
check("Datei: beide Anmelde-Sichten mit oauth",
      st == 200 and sorted(doc["mcpServers"]) == ["beides", "nur-anmeldung"]
      and all("oauth" in e for e in doc["mcpServers"].values()), (st, raw[:300]))
check("Prüfspur nennt die Person mit Kennung",
      [r for r in books.audit(DB, 5) if r["actor"] == "person:u-anna" and r["action"] == "client.config"])
st, _h, _raw = me_get("/me/client-config?target=vscode")
check("VS Code auf /me: 400", st == 400, st)
st, _h, _raw = me_get("/me/client-config?target=claude-code", uid="")
check("ohne Kennung der Person: 403", st == 403, st)
real = app.personal_servers_of
app.personal_servers_of = lambda v: ["jira"] if v == "beides" else []
st, _h, html = me_get("/me")
check("Verfahren U ohne eigenen Zugang: „erst Zugang hinterlegen“", "erst Zugang hinterlegen: jira" in html)
text, _p, hints = app.client_file("gw.example", "claude-code", [{"name": "beides", "way": "signin"}])
check("... und in den Hinweisen zur Datei", any("jira" in x and "Meine Werkzeuge" in x for x in hints), hints)
app.personal_servers_of = real
old_iss = app.ISSUER
app.ISSUER = ""
st, _h, html = me_get("/me")
check("ohne Anmeldung am Gateway: keine Karte", "In meinem Werkzeug einrichten" not in html)
app.ISSUER = old_iss

print("\n-- nie ein Schlüsselwert")
leaks = [o[:120] for o in OUTPUTS if KEY in o or OTHER in o]
check("kein ausgestellter Wert in irgendeiner Ausgabe", not leaks, leaks)
files = [o for o in OUTPUTS if o.lstrip().startswith("{")]
check("keine Datei enthält mcp-oaap-", files and not [f for f in files if "mcp-oaap-" in f], len(files))

gw.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
