"""Der Zugang dieser App zur Verwaltungs-Tuer des Identity-Dienstes
(oaap.core.authorization 0.3 §2.10, `/authz/admin/*`).

Nur Standardbibliothek. Der Schluessel (`OAAP_AUTHZ_ADMIN_KEY`) ist ein
Werkzeug des Mandanten-Admins: die Plattform stellt ihn nur aus, wenn der
Betreiber bei der Installation zugestimmt hat. Er gilt fuer GENAU EINEN
Mandanten -- welchen, sagt der Identity-Dienst, nicht diese App.

Jeder Aufruf nennt die PERSON (`on_behalf_of`, die Benutzer-ID aus
`X-OAAP-User-Id`); der Identity-Dienst prueft selbst, ob sie Admin ist.
Diese App entscheidet das nie -- sie kann es nicht einmal lesen.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 8


class DoorError(Exception):
    """Eine Antwort, die kein Erfolg war. `status` 0 = nicht erreichbar."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail

    @property
    def denied(self):
        """Die Person (oder der Schluessel) darf hier nicht verwalten."""
        return self.status in (401, 403)


def configured():
    return bool(os.environ.get("OAAP_AUTHZ_URL")
                and os.environ.get("OAAP_AUTHZ_ADMIN_KEY"))


def call(method, path, person, body=None, params=None):
    """Ein Aufruf als `person` (Benutzer-ID). Gibt das JSON zurueck oder
    wirft DoorError mit dem Satz des Dienstes."""
    if not configured():
        raise DoorError(0, "Diese Instanz hat keinen Verwaltungsschluessel "
                           "(OAAP_AUTHZ_ADMIN_KEY). Wurde sie mit "
                           "--confirm-administer installiert?")
    if not person:
        raise DoorError(403, "Ohne angemeldete Person kein Zugriff.")
    url = os.environ["OAAP_AUTHZ_URL"].rstrip("/") + "/admin/" + path
    data = None
    q = dict(params or {})
    headers = {"Authorization": "Bearer " + os.environ["OAAP_AUTHZ_ADMIN_KEY"],
               "Accept": "application/json"}
    if method == "GET":
        q["on_behalf_of"] = person
    else:
        data = json.dumps(dict(body or {}, on_behalf_of=person)).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if q:
        url += "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read().decode("utf-8")).get("error") or ""
        except (ValueError, AttributeError):
            msg = ""
        raise DoorError(e.code, msg or f"Der Identity-Dienst antwortete {e.code}.")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise DoorError(0, f"Der Identity-Dienst ist nicht erreichbar ({e}).")
