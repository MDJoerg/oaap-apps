"""Schutz der Server (Spec §8, RFC-0065 §8).

Der Grund steht im RFC: Ein Agent, der in einer Schleife zweihundertmal
`read_class` ruft, ist realistisch — und ein SAP-System, dem die
Dialog-Workprozesse ausgehen, steht dann auch für seine Menschen still.

Reihenfolge je Aufruf, wie in der Spec: Sicherungsautomat, Rate je
Server, Rate je Server und Schlüssel, gleichzeitige Aufrufe mit kurzer
Warteschlange. Zeitlimit und Antwortgröße setzt `upstream.py` beim
Lesen durch; ihre Werte stehen hier mit den übrigen Grenzen.

Alles im Speicher, wie die Rate des KI-Gateways: Ein Neustart setzt die
Zähler zurück. Das Budget eines Schlüssels steht in der Datenbank und
trägt die harte Grenze.
"""
import threading
import time

from gwcore.ledger import RateLimiter

DEFAULTS = {
    "max_concurrent": 4,        # gleichzeitig beim Server
    "queue_len": 16,            # Plätze in der Warteschlange
    "queue_wait_s": 10,         # so lange wartet ein Platz höchstens
    "rate_per_min": 0,          # je Server, 0 = keine
    "rate_per_key_min": 0,      # je Server und Schlüssel, 0 = keine
    "timeout_s": 60,            # je Aufruf
    "max_bytes": 1 << 20,       # Antwortgröße (A2)
    "breaker_failures": 5,      # so viele Fehler in Folge öffnen den Automaten
    "breaker_cooldown_s": 30,   # so lange bleibt er offen
}
BOUNDS = {
    "max_concurrent": (1, 64), "queue_len": (0, 256), "queue_wait_s": (0, 120),
    "rate_per_min": (0, 100000), "rate_per_key_min": (0, 100000),
    "timeout_s": (1, 3600), "max_bytes": (1024, 64 << 20),
    "breaker_failures": (1, 1000), "breaker_cooldown_s": (1, 3600),
}
LABELS = {
    "max_concurrent": "gleichzeitige Aufrufe",
    "queue_len": "Plätze in der Warteschlange",
    "queue_wait_s": "Wartezeit in der Schlange (s)",
    "rate_per_min": "Aufrufe je Minute (Server)",
    "rate_per_key_min": "Aufrufe je Minute (je Schlüssel)",
    "timeout_s": "Zeitlimit je Aufruf (s)",
    "max_bytes": "Antwortgröße höchstens (Bytes)",
    "breaker_failures": "Fehler in Folge bis zur Pause",
    "breaker_cooldown_s": "Pause (s)",
}


def clean(limits):
    """Ganzzahlen innerhalb der Grenzen; Fehlendes nimmt den Standard."""
    out = {}
    for name, default in DEFAULTS.items():
        lo, hi = BOUNDS[name]
        try:
            value = int((limits or {}).get(name, default))
        except (TypeError, ValueError):
            raise ValueError(f"„{LABELS[name]}“ muss eine ganze Zahl sein.") from None
        if not lo <= value <= hi:
            raise ValueError(f"„{LABELS[name]}“ muss zwischen {lo} und {hi} liegen.")
        out[name] = value
    return out


class Limited(Exception):
    """Eine Grenze greift. `reason` ∈ rate, queue, breaker; `retry_after` in Sekunden oder None."""

    def __init__(self, reason, text, retry_after=None):
        super().__init__(text)
        self.reason = reason
        self.retry_after = retry_after


class ServerGuard:
    def __init__(self, limits):
        self.limits = clean(limits)
        self._lock = threading.Condition()
        self._inflight = 0
        self._waiting = 0
        self._failures = 0
        self._open_until = 0.0
        self._probing = False
        self._rate = RateLimiter()
        self._rate_key = RateLimiter()
        self.peak = 0                    # höchste gemessene Gleichzeitigkeit, für die Seite

    def update(self, limits):
        with self._lock:
            self.limits = clean(limits)
            self._lock.notify_all()

    def state(self):
        with self._lock:
            now = time.monotonic()
            return {"inflight": self._inflight, "waiting": self._waiting,
                    "failures": self._failures, "peak": self.peak,
                    "open_for": max(0, int(self._open_until - now)) if self._open_until > now else 0,
                    "probing": self._probing}

    def enter(self, key_id):
        """Gibt zurück, wenn der Aufruf zum Server darf; wirft sonst Limited."""
        lim = self.limits
        with self._lock:
            now = time.monotonic()
            probe = False
            if self._open_until:
                if now < self._open_until:
                    raise Limited("breaker", "Der Server pausiert nach wiederholten Fehlern.",
                                  max(1, int(self._open_until - now)))
                if self._probing:
                    raise Limited("breaker", "Der Server wird gerade mit einem Aufruf geprüft.",
                                  max(1, lim["breaker_cooldown_s"] // 3))
                self._probing = probe = True
        try:
            ok, wait = self._rate.allow("server", lim["rate_per_min"])
            if not ok:
                raise Limited("rate", "Zu viele Aufrufe an diesen Server je Minute.", wait)
            ok, wait = self._rate_key.allow(key_id, lim["rate_per_key_min"])
            if not ok:
                raise Limited("rate", "Zu viele Aufrufe dieses Schlüssels an diesen Server "
                                      "je Minute.", wait)
            with self._lock:
                if self._inflight >= lim["max_concurrent"]:
                    if self._waiting >= lim["queue_len"]:
                        raise Limited("queue", "Der Server ist ausgelastet, die Warteschlange "
                                               "ist voll.", 1)
                    self._waiting += 1
                    end = time.monotonic() + lim["queue_wait_s"]
                    try:
                        while self._inflight >= self.limits["max_concurrent"]:
                            left = end - time.monotonic()
                            if left <= 0:
                                raise Limited("queue", "Der Server ist ausgelastet; die Wartezeit "
                                                       "in der Schlange ist abgelaufen.", 1)
                            self._lock.wait(left)
                    finally:
                        self._waiting -= 1
                self._inflight += 1
                self.peak = max(self.peak, self._inflight)
        except Limited:
            if probe:
                with self._lock:
                    self._probing = False
            raise
        return probe

    def leave(self, probe, failed):
        """`failed` = Fehler auf dem Weg zum Server (nicht: ein Tool, das scheitert)."""
        with self._lock:
            self._inflight -= 1
            if failed:
                self._failures += 1
                if probe or self._failures >= self.limits["breaker_failures"]:
                    self._open_until = time.monotonic() + self.limits["breaker_cooldown_s"]
            else:
                self._failures = 0
                self._open_until = 0.0
            if probe:
                self._probing = False
            self._lock.notify_all()
