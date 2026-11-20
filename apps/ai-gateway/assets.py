"""Was das Gateway von sich nennt (RFC-0087 §8.2, Manifest 0.15 `assets`).

Der Knoten fragt bei jedem Statusdokument die Tür `/oaap/assets` und
liest dort Modellnamen und Bezugsquellen: wer wen nutzt, welche Quelle
über welche Destination der Plattform geht und wie es ihr zuletzt ging.
Rein bis auf die Uhr: Die App reicht Quellen, Modellnamen und
Destinationen herein, damit jede Regel ohne Netz geprüft werden kann.

Was nie hinausgeht: Adressen, Modellkennungen bei der Quelle, Ampel,
Schlüssel, Personen. Der Knoten liest ohnehin nur nach Whitelist; hier
steht gar nicht erst mehr drin.

Woher der Zustand einer Quelle kommt:

* **jeder echte Versuch** -- jede Weiterleitung, auch die vor einem
  Ausweichen, und jeder Verbindungstest. „Erreicht“ heißt: Die Quelle hat
  mit HTTP unter 500 geantwortet (auch 401 und 404 -- der Weg steht).
  Keine Antwort oder 5xx heißt „nicht erreicht“; über eine Destination
  sagt die Plattform mit 502/503, dass Tunnel oder Angebot fehlen.
* **eine Prüfung je Minute** für Quellen hinter einer Destination der
  Plattform (eigene Wege: Tunnel, VPN). `GET …/models` rechnet nichts und
  kostet nichts. Eine Quelle bei einem fremden Anbieter wird für die
  Flotte **nicht** zusätzlich angefragt -- ihr Zustand kommt nur aus
  echten Anfragen.
* Liegt der letzte Versuch länger als RECENT zurück: `idle`.
"""
import threading
import time

RECENT = 15 * 60          # wie lange ein Versuch etwas über die Quelle sagt
CHECK_EVERY = 60          # Prüfung der Quellen hinter einer Destination
MAX_ASSETS = 200          # so viele liest der Knoten höchstens


def reached(status):
    """Hat die Quelle geantwortet? Jede HTTP-Antwort unter 500 zählt."""
    return isinstance(status, int) and 0 < status < 500


class Seen:
    """Der letzte Versuch je Quelle: (Zeitpunkt, erreicht?). Nur im
    Speicher -- nach einem Neustart weiß das Gateway nichts und sagt
    `idle`, bis der nächste Versuch kommt (höchstens eine Minute für
    Quellen hinter einer Destination)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._last = {}

    def note(self, source, ok, at=None):
        with self._lock:
            self._last[source] = (time.time() if at is None else at, bool(ok))

    def state(self, source, now=None):
        now = time.time() if now is None else now
        with self._lock:
            got = self._last.get(source)
        if got is None or now - got[0] > RECENT:
            return "idle"
        return "ok" if got[1] else "down"

    def forget(self, keep):
        """Quellen, die es nicht mehr gibt, vergessen."""
        with self._lock:
            for name in [n for n in self._last if n not in keep]:
                del self._last[name]


def model_state(states):
    """Ein Modellname ist `ok`, wenn eine seiner Quellen es ist (das
    Gateway weicht aus), `down`, wenn alle es sind, sonst `idle`."""
    if any(s == "ok" for s in states):
        return "ok"
    if states and all(s == "down" for s in states):
        return "down"
    return "idle"


def answer(suppliers, aliases, seen, via, now=None, props=None):
    """Die Antwort der Tür: {"v": 1, "assets": [...]}.

    suppliers -- {name: quelle}, aliases -- {name: {"targets": [{"supplier":
    name, "model": ...}]}}, seen -- Seen, via -- {quelle: destination} für
    Quellen, deren Adresse über eine gebundene Destination führt; props --
    {modellname: {"oaap_tools": …}} (RFC-0106 §2.5, nur gesetzte), ohne
    Präfix in den Eintrag übernommen."""
    out = []
    states = {}
    for name in sorted(suppliers):
        states[name] = seen.state(name, now)
        row = {"kind": "source", "name": name, "state": states[name]}
        if via.get(name):
            row["destination"] = via[name]
        out.append(row)
    for name in sorted(aliases):
        uses = sorted({t["supplier"] for t in aliases[name].get("targets") or []
                       if t.get("supplier") in suppliers})
        row = {"kind": "model", "name": name,
               "state": model_state([states[u] for u in uses]), "uses": uses}
        for k, v in ((props or {}).get(name) or {}).items():
            row[k[len("oaap_"):] if k.startswith("oaap_") else k] = v
        out.append(row)
    return {"v": 1, "assets": out[:MAX_ASSETS]}
