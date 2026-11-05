"""Der Desk liest die Tuer des Knotens (RFC-0069 Stufe 3) -- ohne Desk.

    python3 apps/webtop/test_care_watch.py

Ein kleiner HTTP-Server spielt die Tuer /platform/care.json und aendert
seine Antwort ueber die Zeit; notify-send ist ein Skript, das mitschreibt.
Gehalten wird: ohne Plan nichts; ein Plan -> eine Meldung und die Datei
auf dem Schreibtisch; 30/10 Minuten vorher dringend, je einmal; 'startet
jetzt' dringend; Plan weg -> Datei weg, eine Meldung; 404 heisst 'kein Plan'.
"""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "init", "oaap-care-watch")
TMP = tempfile.mkdtemp(prefix="care-watch-")
LOG = os.path.join(TMP, "notify.log")
NOTE = os.path.join(TMP, "Desktop", "NEUSTART-GEPLANT.txt")
fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


FAKE = os.path.join(TMP, "fake-notify.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write("import sys\nopen(sys.argv[0] + '.log', 'a', encoding='utf-8').write('|'.join(sys.argv[1:]) + '\\n')\n")
LOG = FAKE + ".log"

ANSWER = {"code": 404, "doc": None}


class Door(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps(ANSWER["doc"] or {}).encode()
        self.send_response(ANSWER["code"])
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


srv = HTTPServer(("127.0.0.1", 0), Door)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_port}/platform/care.json"


def notes():
    try:
        return open(LOG, encoding="utf-8").read().splitlines()
    except OSError:
        return []


def run_once(env_extra=None):
    env = dict(os.environ, OAAP_CARE_URL=URL, OAAP_CARE_HOME=TMP, OAAP_CARE_NOTIFY=FAKE,
               OAAP_CARE_ONCE="1", OAAP_CARE_USER="nobody-here")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, SCRIPT], env=env, capture_output=True, text=True, timeout=20)


def iso_in(minutes):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + minutes * 60))


print("")
print("1. Ohne Plan")
r = run_once()
ok("404: nichts gemeldet, keine Datei", r.returncode == 0 and notes() == [] and not os.path.exists(NOTE),
   (r.stdout, r.stderr))
ANSWER.update(code=200, doc={"schema": "oaap.care-door/0.1", "reboot_at": None, "urgency": None, "held": False})
r = run_once()
ok("200 ohne Termin: nichts gemeldet", notes() == [] and not os.path.exists(NOTE))
r = run_once({"OAAP_CARE_URL": ""})
ok("ohne OAAP_CARE_URL: wartet still (hier: sagt es)", "OAAP_CARE_URL fehlt" in (r.stdout + r.stderr) or r.returncode != 0)

print("")
print("2. Ein Plan, dann die Verlaufsmeldungen (ein Lauf mit 1-s-Takt)")
ANSWER.update(code=200, doc={"schema": "oaap.care-door/0.1", "reboot_at": iso_in(120),
                             "at_local": "2026-11-08 04:15 CET", "urgency": "care", "held": True,
                             "state": "planned"})
env = dict(os.environ, OAAP_CARE_URL=URL, OAAP_CARE_HOME=TMP, OAAP_CARE_NOTIFY=FAKE,
           OAAP_CARE_INTERVAL="1", OAAP_CARE_USER="nobody-here")
p = subprocess.Popen([sys.executable, SCRIPT], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
time.sleep(2.5)
n = notes()
ok("ein Termin: genau eine Meldung, normal, mit Zeit und Dringlichkeit",
   len(n) == 1 and n[0].startswith("normal|Dieser Knoten startet neu|am 2026-11-08 04:15 CET (Pflege, durch einen Schutz gehalten)"), n)
ok("die Datei auf dem Schreibtisch nennt Termin und Schutz",
   os.path.exists(NOTE) and "2026-11-08 04:15 CET" in open(NOTE, encoding="utf-8").read()
   and "gehalten" in open(NOTE, encoding="utf-8").read())
time.sleep(2)
ok("derselbe Termin eine Weile spaeter: keine zweite Meldung", len(notes()) == 1, notes())
ANSWER["doc"] = dict(ANSWER["doc"], reboot_at=iso_in(25), at_local="2026-11-08 05:00 CET", held=False)
time.sleep(2.5)
n = notes()
ok("verschoben und naeher als 30 min: die Meldung zum neuen Termin UND die dringende 30-Minuten-Warnung",
   len(n) == 3 and n[1].startswith("normal|Dieser Knoten startet neu|am 2026-11-08 05:00 CET (Pflege)")
   and n[2].startswith("critical|Neustart in Kürze|Dieser Knoten startet in etwa 24 Minuten"), n)
time.sleep(2)
ok("die 30-Minuten-Warnung kommt nur einmal", len(notes()) == 3, notes())
ANSWER["doc"] = dict(ANSWER["doc"], reboot_at=iso_in(5))
time.sleep(2.5)
n = notes()
ok("ein neuer Termin in 5 min: Meldung, dann 30- und 10-Minuten-Warnung",
   len(n) == 6 and n[4].startswith("critical|Neustart in Kürze") and n[5].startswith("critical|Neustart in Kürze|Dieser Knoten startet in etwa 4 Minuten"), n)
ANSWER["doc"] = dict(ANSWER["doc"], state="rebooting")
time.sleep(2.5)
n = notes()
ok("startet jetzt: eine dringende Meldung, einmal", len(n) == 7 and n[6].startswith("critical|Der Knoten startet jetzt neu"), n)
ANSWER["doc"] = {"schema": "oaap.care-door/0.1", "reboot_at": None, "urgency": None, "held": False, "state": None}
time.sleep(2.5)
n = notes()
ok("Plan weg: eine Meldung 'erledigt oder abgesagt', Datei weg",
   len(n) == 8 and n[7].startswith("normal|Neustart erledigt oder abgesagt") and not os.path.exists(NOTE), n)
ANSWER.update(code=404, doc=None)
time.sleep(2.5)
ok("404 danach: still", len(notes()) == 8)
p.kill()
out = p.communicate(timeout=5)[0]
ok("das Skript sagt, was es liest und was es sah", "lese http://" in out and "Plan: 2026-11-08 04:15 CET (care)" in out
   and "kein Plan mehr" in out, out)

print("")
print("ALLE PASS" if not fails else f"{fails} FAIL")
sys.exit(1 if fails else 0)
