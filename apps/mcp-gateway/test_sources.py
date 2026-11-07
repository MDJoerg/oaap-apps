"""Quellen der Plattform im Gateway (RFC-0073 Stufe 1, mcp-gateway 0.4.0).

    python3 apps/mcp-gateway/test_sources.py

Gehalten wird:
  * eine neue Quelle wird ein Server mit Instanznamen, Zugang none, Ampel red,
    und erscheint in KEINER Sicht;
  * ein von Hand angelegter Server gleichen Namens bleibt unberuehrt (Konflikt);
  * Zugang, Zugangsdaten und Ampel des Betreibers ueberleben den naechsten Abgleich,
    nur eine geaenderte Adresse wird nachgezogen;
  * eine verschwundene Quelle wird mit Zeitpunkt markiert, ihre Eintraege in den
    Sichten bleiben; kommt sie wieder, ist die Markierung weg;
  * unbrauchbare Zeilen (Name, Adresse) werden uebergangen;
  * die Abfrage der Liste schickt den Schluessel und wertet nur die Form v=1 aus.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = tempfile.mkdtemp(prefix="mcpgw-sources-")
os.environ["MCPGW_DATA_DIR"] = DATA
os.environ["MCPGW_MASTER_KEY"] = "x" * 64
sys.path.insert(0, HERE)

import books  # noqa: E402
import guard  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


db = books.connect(os.path.join(DATA, "g.db"))
LIM = guard.clean(dict(guard.DEFAULTS))
KP = {"name": "kp", "app": "kontext-probe", "version": "0.1.2", "channel": "production",
      "path": "/mcp", "address": "http://oaap-gateway-1:8125/mcp"}

rep = books.sync_sources(db, [KP], LIM)
row = books.server_row(db, "kp")
ok("neue Quelle: Server mit Instanznamen", rep["added"] == ["kp"] and row is not None, rep)
ok("… Zugang none, keine Zugangsdaten, Ampel red, als Quelle markiert",
   row["auth_kind"] == "none" and row["credential"] == "" and row["light"] == "red"
   and row["source"] == "kp" and row["gone"] == "", dict(row))
ok("… und in keiner Sicht ein Eintrag (Standard: alles verboten)",
   db.execute("SELECT COUNT(*) FROM items WHERE server='kp'").fetchone()[0] == 0)

# Betreiber setzt Ampel und Zugang (wie `server set … --auth bearer`)
db.execute("UPDATE servers SET light='green', auth_kind='bearer', credential='versiegelt' WHERE name='kp'")
db.commit()
rep = books.sync_sources(db, [dict(KP, version="0.1.3")], LIM)
row = books.server_row(db, "kp")
ok("zweiter Abgleich: Entscheidungen des Betreibers bleiben",
   row["light"] == "green" and row["auth_kind"] == "bearer" and row["credential"] == "versiegelt"
   and not any(rep.values()), (dict(row), rep))
rep = books.sync_sources(db, [dict(KP, address="http://oaap-gateway-1:8199/mcp")], LIM)
ok("geaenderte Adresse wird nachgezogen und gemeldet",
   books.server_row(db, "kp")["address"] == "http://oaap-gateway-1:8199/mcp" and rep["moved"] == ["kp"], rep)

books.save_server(db, "tester", "handarbeit", "http://10.0.0.5:3900/mcp", "none", "yellow", LIM)
rep = books.sync_sources(db, [dict(KP, address="http://oaap-gateway-1:8199/mcp"),
                              dict(KP, name="handarbeit", address="http://oaap-gateway-1:8200/mcp")], LIM)
h = books.server_row(db, "handarbeit")
ok("von Hand angelegter Server gleichen Namens bleibt unberuehrt (Konflikt gemeldet)",
   rep["conflict"] == ["handarbeit"] and h["address"] == "http://10.0.0.5:3900/mcp"
   and h["source"] == "" and h["light"] == "yellow", (rep, dict(h)))

db.execute("INSERT INTO views (name,max_effect,created,updated) VALUES ('v1','read','x','x')")
db.execute("INSERT INTO items (view,kind,name,server,source,effect,approved_fp,approved_doc,approved_by,"
           "approved_at) VALUES ('v1','tool','whoami','kp','whoami','read','fp','{}','t','x')")
db.commit()
rep = books.sync_sources(db, [], LIM)
row = books.server_row(db, "kp")
ok("verschwundene Quelle: mit Zeitpunkt markiert", rep["gone"] == ["kp"] and row["gone"] != "", rep)
ok("… ihr Eintrag in der Sicht bleibt stehen",
   db.execute("SELECT COUNT(*) FROM items WHERE server='kp'").fetchone()[0] == 1)
ok("… der Server von Hand ist nie 'verschwunden'", books.server_row(db, "handarbeit")["gone"] == "")
rep = books.sync_sources(db, [], LIM)
ok("… ein zweiter leerer Abgleich meldet nichts Neues", not any(rep.values()), rep)
rep = books.sync_sources(db, [dict(KP, address="http://oaap-gateway-1:8199/mcp")], LIM)
ok("kommt sie wieder: Markierung weg", rep["back"] == ["kp"] and books.server_row(db, "kp")["gone"] == "", rep)

rep = books.sync_sources(db, [dict(KP, name="Gross!"), dict(KP, name="ftp", address="ftp://x/mcp"),
                              dict(KP, name="ohne-adresse", address="")], LIM)
ok("unbrauchbare Zeilen werden uebergangen",
   all(books.server_row(db, n) is None for n in ("Gross!", "ftp", "ohne-adresse")), rep)
logs = [r["detail"] for r in db.execute("SELECT detail FROM audit WHERE action='server.source'")]
ok("jede Bewegung steht im Protokoll", len(logs) >= 5, logs)

# ------------------------------------------- Stufe 2: die Tuer (Verfahren I)
DOOR = "http://oaap-gateway-1:8128/platform/mcp/to/"
rep = books.sync_sources(db, [dict(KP, name="kp-neu", address="http://oaap-gateway-1:8201/mcp",
                                   door=DOOR + "kp-neu")], LIM)
row = books.server_row(db, "kp-neu")
ok("neue Quelle mit Tuer: Verfahren I (platform), Adresse = Tuer, rot, keine Zugangsdaten",
   rep["added"] == ["kp-neu"] and row["auth_kind"] == "platform" and row["address"] == DOOR + "kp-neu"
   and row["light"] == "red" and row["credential"] == "", dict(row))
books.sync_sources(db, [dict(KP, name="kp-alt", address="http://oaap-gateway-1:8202/mcp")], LIM)
rep = books.sync_sources(db, [dict(KP, name="kp-alt", address="http://oaap-gateway-1:8202/mcp",
                                   door=DOOR + "kp-alt")], LIM)
row = books.server_row(db, "kp-alt")
ok("eine Quelle aus Stufe 1, die nie einen Zugang bekam (none): wechselt auf I, Adresse = Tuer",
   row["auth_kind"] == "platform" and row["address"] == DOOR + "kp-alt" and rep["moved"] == ["kp-alt"],
   (dict(row), rep))
rep = books.sync_sources(db, [dict(KP, address="http://oaap-gateway-1:8199/mcp", door=DOOR + "kp")], LIM)
row = books.server_row(db, "kp")
ok("eine Quelle mit Zugang des Betreibers (bearer, Agent) bleibt T an ihrer Adresse",
   row["auth_kind"] == "bearer" and row["address"] == "http://oaap-gateway-1:8199/mcp"
   and row["credential"] == "versiegelt" and not rep["moved"], (dict(row), rep))
rep = books.sync_sources(db, [dict(KP, name="kp-neu", address="http://oaap-gateway-1:8201/mcp",
                                   door="ftp://evil/x")], LIM)
ok("eine Tuer, die nicht http:// ist, biegt nichts um",
   books.server_row(db, "kp-neu")["address"] == DOOR + "kp-neu", rep)
try:
    books.save_server(db, "tester", "handarbeit", "http://10.0.0.5:3900/mcp", "platform", "yellow", LIM)
    ok("Verfahren I fuer einen Server von Hand: abgewiesen", False)
except ValueError as exc:
    ok("Verfahren I fuer einen Server von Hand: abgewiesen", "Quellen der Plattform" in str(exc), exc)

# ------------------------------------------------------- die Abfrage der Liste
SEEN = []
ANSWER = {"body": json.dumps({"v": 1, "sources": [KP]}).encode(), "code": 200}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        SEEN.append(self.headers.get("Authorization"))
        self.send_response(ANSWER["code"])
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(ANSWER["body"])


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["OAAP_MCP_SOURCES_URL"] = f"http://127.0.0.1:{srv.server_port}/platform/mcp/sources"
os.environ["OAAP_MCP_SOURCES_KEY"] = "oaapk_k1_geheim"
import app  # noqa: E402

got = app.fetch_sources()
ok("die Abfrage schickt den Schluessel als Bearer", SEEN[-1] == "Bearer oaapk_k1_geheim", SEEN)
ok("… und liefert die Quellen", got == [KP], got)
for body, code, why in ((b'{"v": 2, "sources": []}', 200, "fremde Version"),
                        (b'[]', 200, "keine Abbildung"), (b'nicht json', 200, "kein JSON"),
                        (b'{}', 401, "401")):
    ANSWER.update(body=body, code=code)
    try:
        app.fetch_sources()
        ok(f"abgewiesen: {why}", False)
    except ValueError as exc:
        ok(f"abgewiesen: {why}", "geheim" not in str(exc), exc)
ANSWER.update(body=json.dumps({"v": 1, "sources": [KP]}).encode(), code=200)
okk, text = app.sync_sources()
ok("sync_sources meldet einen Satz ohne den Schluessel", okk and "geheim" not in text, text)
srv.shutdown()

print("")
print("FEHLER: %d" % fails if fails else "Alles gruen.")
sys.exit(1 if fails else 0)
