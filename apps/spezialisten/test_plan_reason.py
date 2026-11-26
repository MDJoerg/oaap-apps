"""Die Begründung eines Plans (spezialisten 0.8.3, Befund bec 26.11.).

    python3 apps/spezialisten/test_plan_reason.py

Gemessen in bec: Sonnet schlug ändernde Aufrufe vor, ohne einen Satz dazu -- der Plan stand als
„(ohne Begründung des Modells)“ zur Freigabe. Gehalten wird jetzt:
  * schreibt das Modell nichts zum Aufruf, fragt der Lauf in einem Zug OHNE Werkzeuge nach;
  * dieser Zug enthält den offenen Aufruf nicht (ein Aufruf ohne Antwort ist für die
    Schnittstelle kein gültiger Verlauf) und nennt Werkzeug und Argumente;
  * kommt keine Antwort, steht dort, was aufgerufen werden soll -- nie nur „ohne Begründung“.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import runner  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


class Model:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.seen = answer, fail, []

    def turn(self, messages, tools):
        self.seen.append((messages, tools))
        if self.fail:
            raise runner.Unreachable("KI-Gateway nicht erreichbar")
        return {"content": self.answer or ""}


history = [{"role": "system", "content": "Rahmen"}, {"role": "user", "content": "Vorgang V-1"},
           {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {
               "name": "propose_solution", "arguments": "{}"}}]}]
m = Model("Der Dump zeigt einen fehlenden Eintrag; der Vorschlag legt ihn an.")
r = runner.Run({}, None, None, m, log=lambda *a: None)
said = r._reason(history, "propose_solution", {"incident": "100", "text": "Eintrag anlegen"})
msgs, tools = m.seen[0]
ok("ohne Text zum Aufruf: ein Zug nach der Begründung, ohne Werkzeuge", tools == [] and len(m.seen) == 1)
ok("... ohne den offenen Aufruf, mit Werkzeug und Argumenten in der Frage",
   not any(x.get("tool_calls") for x in msgs) and msgs[:2] == history[:2]
   and "propose_solution" in msgs[-1]["content"] and "Eintrag anlegen" in msgs[-1]["content"], msgs)
ok("... die Antwort des Modells ist die Begründung", said.startswith("Der Dump zeigt"), said)

r2 = runner.Run({}, None, None, Model(fail=True), log=lambda *a: None)
said2 = r2._reason(history, "propose_solution", {"incident": "100"})
ok("KI nicht erreichbar: dort steht, was aufgerufen werden soll",
   said2.startswith("Vorgeschlagen: propose_solution") and "100" in said2, said2)
said3 = runner.Run({}, None, None, Model(""), log=lambda *a: None)._reason(history, "add_analysis", {})
ok("leere Antwort: ebenso, nie nur „ohne Begründung“",
   "add_analysis" in said3 and "ohne Begründung des Modells)" not in said3, said3)
src = open(runner.__file__, encoding="utf-8").read()
ok("der Plan nimmt den Text des Modells, sonst die nachgefragte Begründung",
   'text or self._reason(messages, name, args)' in src and '"(ohne Begründung des Modells)"' not in src)

print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
