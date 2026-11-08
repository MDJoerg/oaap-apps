"""Die MCP-Tür der Vorgänge (0.4.0) gegen einen echten Server im Test.

    py -3 apps/vorgaenge/test_mcp.py

Die Kopfzeilen der Person setzt hier der Test, wie die Tür der Plattform sie
setzt. Geprüft wird vor allem: die Tür gibt niemandem mehr als die Seite.
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA = tempfile.mkdtemp(prefix="vorgaenge-mcp-")
os.environ["VORGAENGE_DATA_DIR"] = DATA

import app  # noqa: E402
import store  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


GRANTS = {}


class FakeAuthz:
    url = "http://fake"

    def grants(self, uid):
        return GRANTS.get(uid, [])


app.STORE = store.Store(os.path.join(DATA, "t.db"))
app.AUTHZ = FakeAuthz()
srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_port}"
app.STORE.add_queue("service", "Service") if hasattr(app.STORE, "add_queue") else None


def person(uid, roles="user"):
    return {"X-OAAP-User-Id": uid, "X-OAAP-Roles": roles, "X-OAAP-Display-Name": uid.title(),
            "X-OAAP-User": "vb." + uid}


def raw(method, headers, body=None, ctype="application/json"):
    h = dict(headers)
    data = None
    if body is not None:
        data = json.dumps(body).encode() if not isinstance(body, bytes) else body
        h["Content-Type"] = ctype
    req = urllib.request.Request(BASE + "/mcp", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


N = {"id": 0}


def rpc(who, method, params=None, roles="user"):
    N["id"] += 1
    st, b = raw("POST", person(who, roles), {"jsonrpc": "2.0", "id": N["id"], "method": method,
                                            "params": params or {}})
    return st, (json.loads(b) if b else {})


def tool(who, name, args=None, roles="user"):
    st, d = rpc(who, "tools/call", {"name": name, "arguments": args or {}}, roles)
    res = d.get("result") or {}
    text = (res.get("content") or [{}])[0].get("text", "")
    try:
        val = json.loads(text)
    except ValueError:
        val = text
    return res.get("isError"), val


# --- Tür
st, _ = raw("GET", person("anna"))
ok("GET /mcp: 405", st == 405, st)
st, _ = raw("POST", {}, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
ok("ohne Person: 401", st == 401, st)
st, _ = raw("POST", person("anna"), b"jsonrpc=2.0", "application/x-www-form-urlencoded")
ok("kein JSON (ein Formular aus dem Browser): 415", st == 415, st)
st, d = rpc("anna", "initialize", {"protocolVersion": "2025-11-25"})
ok("initialize: Version und Name", st == 200 and d["result"]["serverInfo"]["name"] == "vorgaenge"
   and d["result"]["protocolVersion"] == "2025-11-25", d)
st, d = rpc("anna", "tools/list")
tools = {t["name"]: t for t in d["result"]["tools"]}
ok("fünf Werkzeuge; lesende mit readOnlyHint, schreibende ohne",
   set(tools) == {"list_cases", "get_case", "list_queues", "create_case", "comment_case"}
   and all(tools[n]["annotations"]["readOnlyHint"] for n in ("list_cases", "get_case", "list_queues"))
   and not any(tools[n]["annotations"]["readOnlyHint"] for n in ("create_case", "comment_case")), tools)
st, _ = raw("POST", person("anna"), {"jsonrpc": "2.0", "method": "notifications/initialized"})
ok("Benachrichtigung: 202 ohne Rumpf", st == 202, st)

# --- anna meldet
err, made = tool("anna", "create_case", {"title": "Drucker im Büro druckt nicht", "body": "Seit heute früh.",
                                         "kind": "bug"})
ok("anna meldet über MCP einen Vorgang", not err and made.get("created") and made.get("number"), made)
num = made.get("number")
c = app.STORE.get(num)
ok("... als anna, Herkunft via mcp vermerkt", c["reporter"] == "anna" and c["reporter_name"] == "Anna"
   and (c["tech"].get("attested") or {}).get("via") == "mcp", c)
err, lst = tool("anna", "list_cases", {"scope": "mine"})
ok("anna sieht ihn in list_cases", not err and [x["number"] for x in lst["cases"]] == [num], lst)
err, g2 = tool("anna", "get_case", {"number": num.replace("V-", "")})
ok("0.5.1: get_case nimmt die Nummer auch ohne V- (wie ein Modell sie gern schreibt)",
   not err and g2.get("number") == num, g2)
err, g = tool("anna", "get_case", {"number": num})
ok("get_case: Beschreibung und Verlauf, keine technischen Angaben",
   not err and g["description"] == "Seit heute früh." and g["history"][0]["type"] == "created"
   and "tech" not in g and "browser" not in json.dumps(g), g)
err, r = tool("anna", "create_case", {"title": "x", "kind": "erfunden"})
ok("unbekannte Art: Fehler mit Satz, nichts angelegt", err and "gibt es nicht" in r, r)
err, r = tool("anna", "create_case", {"title": "x", "queue": "lager"})
ok("unbekannter Bereich: Fehler mit Satz", err and "Bereich" in r, r)
err, r = tool("anna", "create_case", {"title": ""})
ok("leerer Betreff: Fehler", err, r)

# --- bert sieht nichts von anna
err, lst = tool("bert", "list_cases")
ok("bert ohne Rechte: annas Vorgang nicht in seiner Liste", not err and lst["count"] == 0, lst)
err, r = tool("bert", "get_case", {"number": num})
ok("bert: get_case auf annas Vorgang -- „gibt es nicht“ (wie die Seite: 404)", err and "gibt es nicht" in r, r)
err, r = tool("bert", "comment_case", {"number": num, "text": "eingeschmuggelt"})
ok("bert kommentiert annas Vorgang nicht", err and not any(
    e["text"] == "eingeschmuggelt" for e in app.STORE.get(num)["entries"]), r)

# --- carl bearbeitet den Eingang
GRANTS["carl"] = [{"object": "case", "activities": ["read", "comment"], "fields": {"queue": ["eingang"]}}]
err, lst = tool("carl", "list_cases", {"scope": "queues"})
ok("carl (read im Eingang) sieht annas Vorgang über seinen Bereich",
   not err and num in [x["number"] for x in lst["cases"]], lst)
err, r = tool("carl", "comment_case", {"number": num, "text": "Toner ist bestellt."})
ok("carl kommentiert", not err and r.get("commented"), r)
err, g = tool("anna", "get_case", {"number": num})
ok("anna sieht carls Kommentar im Verlauf", any(h["text"] == "Toner ist bestellt." and h["by"] == "Carl"
                                                for h in g["history"]), g)
GRANTS["dora"] = [{"object": "case", "activities": ["read"], "fields": {"queue": ["eingang"]}}]
err, g = tool("dora", "get_case", {"number": num})
err2, r2 = tool("dora", "comment_case", {"number": num, "text": "von dora"})
ok("dora (nur read im Eingang) liest annas Vorgang, kommentiert ihn aber nicht (wie die Seite)",
   not err and err2 and "Kommentieren" in r2, (g, r2))
GRANTS["carl"] = []
err, r = tool("carl", "get_case", {"number": num})
ok("Recht entzogen: carl sieht ihn nicht mehr", err, r)

# --- Partner-Zugang bekommt die Tür nicht
app.STORE.save_partner("dev", "Entwicklung", "http://partner.invalid/handover", "k", "m-partner", "eingang")
st, b = raw("POST", {"X-OAAP-User-Id": "m1", "X-OAAP-Roles": "user", "X-OAAP-User": "m-partner"},
            {"jsonrpc": "2.0", "id": 99, "method": "tools/call",
             "params": {"name": "list_cases", "arguments": {}}})
ok("ein Partner-Zugang bekommt die Tür nicht (403, wie jede Seite außer /handover)", st == 403, (st, b))
st, d = rpc("anna", "resources/read", {"uri": "ui://vorgaenge/case"})
c = ((d.get("result") or {}).get("contents") or [{}])[0]
ok("0.5.0: get_case nennt die Ansicht ui://vorgaenge/case", tools["get_case"].get("_meta") ==
   {"ui": {"resourceUri": "ui://vorgaenge/case"}}, tools["get_case"])
ok("0.5.0: resources/read liefert sie als text/html;profile=mcp-app mit leerer CSP",
   c.get("mimeType") == "text/html;profile=mcp-app" and c.get("text", "").startswith("<!doctype html>")
   and c.get("_meta") == {"ui": {"csp": {}, "prefersBorder": True}}, c)
html = c.get("text", "")
ok("0.5.0: die Ansicht lädt nichts von außen und setzt Daten nie als HTML ein",
   "http://" not in html and "https://" not in html and "innerHTML" not in html
   and "<script src" not in html and "textContent" in html, None)
ok("0.5.0: sie spricht das Protokoll der Fassung 2026-01-26",
   all(x in html for x in ('"ui/initialize"', '"2026-01-26"', '"ui/notifications/initialized"',
                           '"ui/notifications/tool-result"', '"ui/notifications/size-changed"',
                           '"tools/call"')), None)
st, d = rpc("anna", "resources/templates/list")
ok("resources/templates/list antwortet (leer) -- gehört zur Fähigkeit resources",
   (d.get("result") or {}).get("resourceTemplates") == [], d)
st, d = rpc("anna", "resources/read", {"uri": "ui://vorgaenge/anderes"})
ok("eine andere ui://-URI: nicht gefunden", (d.get("error") or {}).get("code") == -32002, d)
st, d = rpc("anna", "frobnicate")
ok("unbekannte Methode: -32601", d.get("error", {}).get("code") == -32601, d)
err, r = tool("anna", "list_queues")
ok("list_queues nennt den Eingang", not err and {"id": "eingang", "title": "Eingang"} in r, r)

srv.shutdown()
print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
