"""Der Drei-Phasen-Weg aus RFC-0019 §2 — von der Studio-Seite aus.

Das Studio bekommt hier **kein Sonderrecht**. Es läuft denselben Weg
wie die Projekt-KI, gegen denselben Deploy-Hook, mit dem Deploy-Token
derselben Instanz — die Plattform kann Studio von einem beliebigen
anderen Client nicht unterscheiden, und alle Prüfungen greifen
unverändert.

Drei Phasen:

1. **Anmelden** — `POST <hook>/announce` mit Version, vollständigem
   Manifest, Prüfsumme und Größe. Der Knoten prüft Schema und Rahmen
   und antwortet mit einem Einmal-Token (15 Minuten, an genau diese
   Instanz und genau diese Prüfsumme gebunden).
2. **Hochladen** — `PUT <upload_url>` mit dem Paket, nur mit diesem
   Einmal-Token.
3. **Nachsehen** — dauert der Bau länger als die Wartezeit des Hooks,
   antwortet er 202; dann fragt man `GET <hook>/status`.

**Der Token wird nirgends abgelegt.** Er lebt in den Argumenten dieser
Funktionen, für die Dauer einer Anfrage, und geht danach mit dem
Aufrufer unter — nicht in die Datenbank, nicht in eine URL, nicht in
eine Logzeile. Das Gateway protokolliert vollständige URIs samt
Query-String; ein Token in einem Query-Parameter läge damit im Klartext
in einer Logdatei.
"""

import json
import os
import urllib.error
import urllib.request

DEFAULT_TIMEOUT = 60
USER_AGENT = "oaap-studio"

ANNOUNCE = "announce"
UPLOAD = "upload"
STATUS = "status"


class DeployError(Exception):
    """Der Hook war nicht erreichbar oder antwortete unbrauchbar."""


def _request(method, url, headers, body, timeout, length=None):
    """Eine Anfrage. Rückgabe: (status, dict). Wirft `DeployError` nur,
    wenn gar keine Antwort zustande kam — eine Ablehnung ist Antwort."""
    req = urllib.request.Request(url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("User-Agent", USER_AGENT)
    if length is not None:
        req.add_header("Content-Length", str(length))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw, status = res.read(1 << 20), res.status
    except urllib.error.HTTPError as e:          # 4xx/5xx = Antwort
        raw, status = e.read(1 << 20), e.code
    except urllib.error.URLError as e:
        raise DeployError(f"Der Hook war nicht erreichbar: {e.reason}")
    except OSError as e:
        raise DeployError(f"Der Hook war nicht erreichbar: {e}")
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(data, dict):
            data = {"message": str(data)}
    except ValueError:
        text = raw.decode("utf-8", "replace").strip()
        data = {"message": text[:400] or f"HTTP {status} ohne Inhalt"}
    return status, data


def hook_urls(hook_url):
    """Aus der Hook-Adresse die drei Adressen des Ablaufs."""
    base = (hook_url or "").strip().rstrip("/")
    if not base:
        raise DeployError("Für dieses Vorhaben ist keine Hook-Adresse hinterlegt.")
    if not base.startswith(("http://", "https://")):
        raise DeployError("Die Hook-Adresse muss mit http:// oder https:// "
                          "beginnen.")
    return {"deploy": base, "announce": f"{base}/announce",
            "artifact": f"{base}/artifact", "status": f"{base}/status"}


def announce(hook_url, token, manifest_text, sha256, size, version="",
             timeout=DEFAULT_TIMEOUT, request=_request):
    """Phase 1. Rückgabe: (ok, status, antwort).

    `version` ist Beiwerk: Verbindlich ist die Version **im** Manifest,
    und der Knoten liest sie dort. Sie steht trotzdem im Aufruf, weil
    RFC-0019 §2 sie so beschreibt und ein Protokoll dadurch ohne
    YAML-Kenntnis lesbar bleibt.
    """
    body = json.dumps({
        "version": version,
        "manifest": manifest_text,
        "artifact_sha256": sha256,
        "artifact_bytes": size,
    }).encode("utf-8")
    status, data = request("POST", hook_urls(hook_url)["announce"],
                           {"Authorization": f"Bearer {token}",
                            "Content-Type": "application/json"},
                           body, timeout, len(body))
    return status == 200 and bool(data.get("upload_token")), status, data


def upload(upload_url, upload_token, path, timeout=DEFAULT_TIMEOUT,
           request=_request):
    """Phase 2 — das Paket selbst, im Fluss von der Platte."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        status, data = request("PUT", upload_url,
                               {"Authorization": f"Bearer {upload_token}",
                                "Content-Type": "application/zip"},
                               f, timeout, size)
    return status in (200, 202), status, data


def status(hook_url, token, timeout=DEFAULT_TIMEOUT, request=_request):
    """Phase 3 — nachsehen, wenn der Bau länger dauert."""
    st, data = request("GET", hook_urls(hook_url)["status"],
                       {"Authorization": f"Bearer {token}"}, None, timeout)
    return st == 200, st, data


REFUSAL_HELP = {
    "no_manifest": "Das Paket wurde ohne Manifest angemeldet — das Studio "
                   "sendet es immer mit, das deutet auf einen Fehler beim "
                   "Lesen des Archivs hin.",
    "bad_size": "Die angemeldete Größe passt nicht oder das Paket ist größer "
                "als der Knoten annimmt.",
    "envelope_widened": "Das Paket erweitert den Rahmen der Instanz. Ein "
                        "server_admin bestätigt das im Portal auf der "
                        "Instanzseite — danach dieselbe Datei erneut senden.",
    "rejected": "Der Knoten hat die Anmeldung geprüft und abgelehnt. Die "
                "Begründung darunter kommt von ihm.",
    "timeout": "Der Knoten hat nicht rechtzeitig geantwortet. Nochmal "
               "versuchen; passiert das wiederholt, ist der Knoten "
               "überlastet oder der Dienst hängt.",
}


def explain(status_code, data):
    """Eine Ablehnung in einem Satz, den ein Mensch versteht."""
    if status_code == 403:
        return ("Der Token wurde nicht angenommen. Ein Deploy-Token gehört zu "
                "genau einer bestehenden Instanz und nur zum Test-Kanal; nach "
                "einem Widerruf ist er ungültig. Eine Anlege-Erlaubnis gilt "
                "nur für einen Namen, den es noch NICHT gibt, einmal und für "
                "eine halbe Stunde. Die Plattform sagt bewusst nicht, ob es "
                "die Instanz gibt.")
    if status_code == 429:
        return ("Zu viele Versuche in kurzer Zeit — der Hook drosselt. Eine "
                "Minute warten.")
    if status_code == 413:
        return "Das Paket ist größer, als der Knoten annimmt."
    if status_code == 504:
        return REFUSAL_HELP["timeout"]
    refused = str(data.get("refused") or "")
    if refused in REFUSAL_HELP:
        return REFUSAL_HELP[refused]
    if refused:
        return f"Der Knoten lehnt ab ({refused})."
    return ""


def deploy(hook_url, token, manifest_text, sha256, path, version="",
           timeout=DEFAULT_TIMEOUT, request=_request):
    """Beide Phasen nacheinander, mit Protokoll für die Oberfläche.

    Rückgabe: dict mit `ok`, `steps` (je Phase: name, status, ok,
    message, hint) und `result` (die Antwort der letzten Phase).
    Kein Schritt und kein Rückgabewert enthält jemals den Token.
    """
    size = os.path.getsize(path)
    steps = []

    ok, st, data = announce(hook_url, token, manifest_text, sha256, size,
                            version, timeout, request)
    steps.append({"phase": ANNOUNCE, "status": st, "ok": ok,
                  "message": data.get("message", "")
                             or ("Angemeldet — der Knoten gibt den Upload frei."
                                 if ok else ""),
                  "details": data.get("details") or [],
                  "hint": "" if ok else explain(st, data)})
    if not ok:
        return {"ok": False, "steps": steps, "result": data}

    url = data.get("upload_url") or hook_urls(hook_url)["artifact"]
    ok, st, data = upload(url, data["upload_token"], path, timeout, request)
    steps.append({"phase": UPLOAD, "status": st, "ok": ok,
                  "message": data.get("message", ""),
                  "details": [],
                  "hint": "" if ok else explain(st, data)})
    return {"ok": ok, "steps": steps, "result": data,
            "pending": st == 202 or data.get("ok") is None}
