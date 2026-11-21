"""lib/appframe und lib/mcpdoor (RFC-0118 P2/P7) für sich.

    py -3 lib/test_appframe.py
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import appframe as af  # noqa: E402
import mcpdoor  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"\n      {str(detail)[:400]}"))


print("1. Der Rahmen")
os.environ["OAAP_PORTAL_URL"] = "https://bec.node.example/instances/wegweiser"
h = af.page("Übersicht", "<p>x</p>", app="Wegweiser", version="0.5.0", product="wegweiser",
            tabs=[("./", "Meine Links"), ("areas", "Bereiche", 3)], active="./",
            obj="Link:abc", obj_title="Mein <Link>")
ok("Kit und Rahmenskript", '/platform/theme.css' in h and '/platform/app.js' in h)
ok("die Marke führt zum Portal des Mandanten", 'href="https://bec.node.example/"' in h, h[:600])
ok("App-Name und Version in Kopf und Fuß", h.count("Wegweiser") >= 3 and "0.5.0" in h)
ok("Seitentitel: Seite · App", "<title>Übersicht · Wegweiser</title>" in h)
ok("eine Reiterleiste, der aktive markiert, mit Zahl",
   'class="oaap-tabs"' in h and 'aria-current="page">Meine Links' in h and 'oaap-count">3' in h)
ok("Objekt für Fragen und Feedback, maskiert",
   'data-oaap-object="Link:abc"' in h and 'data-oaap-object-title="Mein &lt;Link&gt;"' in h)
ok("kein OAAP im Kopf, keine Rollen", "OAAP" not in h.split("<header")[1].split("</header>")[0])
ok("ein Reiter allein ist keine Leiste", af.tabs([("./", "A")], "./") == "")

print("2. Der Listenbericht")
rows = [{"n": f"Link {i:03d}", "a": "x" if i % 2 else "y"} for i in range(120)]
cols = [("Name", "n", lambda r: r["n"], ""), ("Bereich", "a", lambda r: r["a"], "oaap-minor")]
q = af.Query({"sort": ["-n"]}, filters=("a",))
out = af.list_report(q, rows, cols, lambda r: f"<tr><td>{r['n']}</td></tr>")
ok("Zählzeile: 1–50 von 120", "1–50 von 120" in out, out[:500])
ok("absteigend sortiert, der Kopf sagt es",
   out.index("Link 119") < out.index("Link 118") and 'aria-sort="descending"' in out)
ok("Blättern: Weitere 50 mit offset", "offset=50" in out and "Weitere 50" in out)
q = af.Query({"q": ["link 00"]})
out = af.list_report(q, rows, cols, lambda r: f"<tr><td>{r['n']}</td></tr>")
ok("Suche: alle Wörter in einer Spalte (Link 000–009 und Link 100)", "11 Einträge" in out and "Link 010" not in out, out[:400])
q = af.Query({"q": ["gibtsnicht"]})
out = af.list_report(q, rows, cols, lambda r: "", empty_text="Noch keine Links.")
ok("keine Treffer sagt es, nicht 'Noch keine'", "Keine Treffer" in out and "Noch keine" not in out)
out = af.list_report(af.Query({}), [], cols, lambda r: "", empty_text="Noch keine Links.")
ok("leere Liste sagt, was zu tun ist", "Noch keine Links." in out)
q = af.Query({"q": ["a"], "a": ["x"]}, filters=("a",))
ok("die Adresse trägt den Filter", "a=x" in q.href() and "q=a" in q.href())
ok("Badge-Arten", 'oaap-badge ok' in af.badge("aktiv", "ok") and 'oaap-badge"' in af.badge("?", "quatsch"))

ok("versteckte Felder (etwa ?tab=) gehen mit dem Filter mit",
   '<input type="hidden" name="tab" value="spur">' in af.filter_bar(af.Query({}), hidden={"tab": "spur"}))
ok("ohne Suchfeld filtert ein q in der Adresse nicht",
   "120 Einträge" in af.list_report(af.Query({"q": ["zzz"]}), rows, cols, lambda r: "<tr></tr>", search=False))
ok("ein Eintrag heißt Eintrag", "1 Eintrag<" in af.countline(1, 1, 1))

print("3. Die MCP-Tür")
door = mcpdoor.Door("oaap-test", "1.0.0", "Testtür.")


@door.tool("t_read", "Lesen", "Liest etwas.", {"id": {"type": "string"}}, required=["id"])
def _read(args, who):
    if "admin" not in who["roles"]:
        raise mcpdoor.Refused("Nur für Admins.")
    return {"id": args.get("id"), "by": who["user"]}


@door.tool("t_hidden", "Versteckt", "Nie gelistet.", when=lambda: False)
def _hidden(args, who):
    return "geheim"


door.prompt("t_prompt", "Vorlage", "Eine Vorlage.", "Lies {id}.", arguments=["id"])
admin = {"user": "anna", "roles": ["admin"], "user_id": "", "display_name": ""}
user = dict(admin, roles=["user"])
r = door.answer({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, admin)
ok("initialize: Version, Name, Prompts angekündigt",
   r["result"]["protocolVersion"] == "2025-06-18" and r["result"]["serverInfo"]["name"] == "oaap-test"
   and "prompts" in r["result"]["capabilities"])
r = door.answer({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, admin)
ok("tools/list ohne das versteckte", [t["name"] for t in r["result"]["tools"]] == ["t_read"])
ok("lesend markiert", r["result"]["tools"][0]["annotations"]["readOnlyHint"] is True)
r = door.answer({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "t_read", "arguments": {"id": "7"}}}, admin)
ok("tools/call: JSON-Text", json.loads(r["result"]["content"][0]["text"]) == {"id": "7", "by": "anna"})
r = door.answer({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "t_read", "arguments": {}}}, user)
ok("dieselbe Rollenprüfung wie die Seite", r["result"]["isError"] and "Nur für Admins" in r["result"]["content"][0]["text"])
r = door.answer({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "t_hidden"}}, admin)
ok("ein nicht gelistetes Werkzeug läuft nicht", r.get("error", {}).get("code") == -32602)
r = door.answer({"jsonrpc": "2.0", "id": 6, "method": "prompts/get", "params": {"name": "t_prompt", "arguments": {"id": "X"}}}, admin)
ok("prompts/get setzt ein", r["result"]["messages"][0]["content"]["text"] == "Lies X.")
ok("Benachrichtigung: keine Antwort", door.answer({"jsonrpc": "2.0", "method": "notifications/initialized"}, admin) is None)


class FakeHandler:
    def __init__(self, method, body, headers):
        self.command = method
        self.headers = headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent = {}
        self.close_connection = False

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.sent[k] = v

    def end_headers(self):
        pass


body = json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "t_read", "arguments": {"id": "1"}}}).encode()
fh = FakeHandler("POST", body, {"Content-Type": "application/json", "Content-Length": str(len(body)),
                                "X-OAAP-User": "bert", "X-OAAP-Roles": "user, admin"})
door.serve(fh)
res = json.loads(fh.wfile.getvalue())
ok("HTTP: die Person aus den Kopfzeilen", fh.status == 200 and json.loads(res["result"]["content"][0]["text"])["by"] == "bert")
fh = FakeHandler("GET", b"", {})
door.serve(fh)
ok("GET /mcp: 405 mit Allow: POST", fh.status == 405 and fh.sent.get("Allow") == "POST")
fh = FakeHandler("POST", b"x", {"Content-Type": "text/plain", "Content-Length": "1"})
door.serve(fh)
ok("kein JSON: 415", fh.status == 415)

print("")
print("ALLE PRUEFUNGEN BESTANDEN" if not fails else f"{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
