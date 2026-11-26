"""Der Agent in der Messzeile (mcp-gateway 0.53.1, Befund RFC-0134 M2).

    python3 apps/mcp-gateway/test_agent_sub.py

Gemessen in bec am 26.11.2026: ein Agent las a4h über Verfahren P (Zusicherung der Plattform für
den Agenten des Server-Eintrags), die Messzeile hatte aber kein `user_sub` -- wer gelesen hatte,
stand nur im Schlüssel. Gehalten wird jetzt:
  * die Benutzerkennung kommt aus der Zusicherung, die die Plattform gab (gelesen, nicht geprüft --
    prüfen tut die Destination);
  * Verfahren P mit dem Agenten trägt sie mit, eine Person bleibt, wie sie war;
  * die Messzeile schreibt sie als user_sub mit Aussteller `platform`, wie bei einer Person aus
    dem Chat; eine unlesbare Zusicherung schreibt nichts Falsches.
"""
import base64
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = tempfile.mkdtemp(prefix="mcpgw-agentsub-")
os.environ["MCPGW_DATA_DIR"] = DATA
os.environ["MCPGW_MASTER_KEY"] = "x" * 64
sys.path.insert(0, HERE)

import books  # noqa: E402
import app  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


TOK = b64({"alg": "HS256", "typ": "oaap-assertion"}) + "." + b64(
    {"v": 1, "sub": "9f1c-agent-uuid", "usr": "spz-lead", "aud": "bec-tools"}) + ".sig"
ok("die Kennung aus der Zusicherung", app.assertion_subject(TOK) == "9f1c-agent-uuid")
ok("unlesbar: leer, nichts Falsches", app.assertion_subject("kaputt") == ""
   and app.assertion_subject("a.!!!.b") == "")

app.agent_assertion = lambda key: TOK
acc = app.propagate_access(None, "oaapk_agent")
ok("Verfahren P mit dem Agenten trägt seine Kennung mit", acc["agent_sub"] == "9f1c-agent-uuid"
   and acc["headers"][app.USER_ASSERTION] == TOK, acc)
pers = app.propagate_access({"iss": "platform", "sub": "p1", "assertion": "x.y.z"}, "oaapk_agent")
ok("eine Person bleibt, wie sie war (ihre eigene Zusicherung, keine Agenten-Kennung)",
   "agent_sub" not in pers and pers["headers"][app.USER_ASSERTION] == "x.y.z", pers)

db = books.connect(os.path.join(DATA, "t.db"))
from gwcore import context  # noqa: E402
key = dict({src: "" for _, src in context.SNAPSHOT}, id="k1")
books.record(db, key, "spz-lead", "tool", "get_statistics", "erc", "get_statistics", "ok", "", 12, 10, 20,
             person={"iss": "platform", "sub": "9f1c-agent-uuid"}, procedure="P")
row = db.execute("SELECT user_sub, user_iss, procedure FROM usage ORDER BY rowid DESC LIMIT 1").fetchone()
ok("die Messzeile nennt den Agenten wie eine Person aus dem Chat",
   tuple(row) == ("9f1c-agent-uuid", "platform", "P"), tuple(row))
src = open(os.path.join(HERE, "app.py"), encoding="utf-8").read()
ok("die Messzeile nimmt die Person, sonst den Agenten des Aufrufs",
   'person=self.person or ({"iss": "platform", "sub": state["agent_sub"]}' in src
   and 'state["agent_sub"] = server["agent_sub"]' in src)

print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
