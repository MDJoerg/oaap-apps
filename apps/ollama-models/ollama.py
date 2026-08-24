"""Der schmale Draht zu einem Ollama — nur die vier Auskünfte, die zählen.

Bewusst **kein allgemeiner Ollama-Client**: gebraucht werden Liste,
Laufende, Holen und Löschen. Alles andere kann diese App nicht, und das
ist die Absicht — sie ist eine Betriebsoberfläche für einen Dienst, der
selbst keine hat, kein zweites Produkt.

Fehler werden **berichtet, nicht geworfen**: Ein nicht erreichbares
Ollama ist ein Zustand, den die Seite zeigen soll (dieselbe Haltung wie
FleetView gegenüber einem stummen Knoten), keine Ausnahme, die eine
Oberfläche zerreißt.
"""
import json
import urllib.error
import urllib.request

TIMEOUT = 20            # Auskünfte sind schnell
PULL_TIMEOUT = 7200     # Ein großes Modell zu holen dauert; zwei Stunden reichen


def _call(base, path, method="GET", payload=None, timeout=TIMEOUT, stream=False):
    url = base.rstrip("/") + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        return None, _message(body) or f"Ollama antwortet mit HTTP {exc.code}"
    except Exception as exc:
        return None, f"Ollama nicht erreichbar ({type(exc).__name__})"
    if stream:
        return resp, ""
    raw = resp.read()
    resp.close()
    if not raw:
        return {}, ""
    try:
        return json.loads(raw), ""
    except Exception:
        return None, "Ollama antwortet nicht in JSON"


def _message(body):
    try:
        doc = json.loads(body)
        return doc.get("error") or ""
    except Exception:
        return ""


def version(base):
    doc, err = _call(base, "/api/version")
    return (doc or {}).get("version", ""), err


def models(base):
    """Installierte Modelle, größte zuerst."""
    doc, err = _call(base, "/api/tags")
    if err:
        return [], err
    rows = []
    for m in (doc or {}).get("models", []):
        details = m.get("details") or {}
        rows.append({
            "name": m.get("name") or m.get("model") or "?",
            "size": int(m.get("size") or 0),
            "modified": (m.get("modified_at") or "")[:19].replace("T", " "),
            "parameters": details.get("parameter_size", ""),
            "quantization": details.get("quantization_level", ""),
            "family": details.get("family", ""),
        })
    rows.sort(key=lambda r: r["size"], reverse=True)
    return rows, ""


def running(base):
    """Was gerade im Speicher liegt — auf einer Maschine ohne GPU die
    interessanteste Zahl, weil sie erklärt, warum die erste Antwort nach
    einer Pause so lange braucht."""
    doc, err = _call(base, "/api/ps")
    if err:
        return [], err
    rows = []
    for m in (doc or {}).get("models", []):
        rows.append({
            "name": m.get("name") or m.get("model") or "?",
            "size": int(m.get("size") or 0),
            "vram": int(m.get("size_vram") or 0),
            "until": (m.get("expires_at") or "")[:19].replace("T", " "),
        })
    return rows, ""


def delete(base, name):
    doc, err = _call(base, "/api/delete", method="DELETE", payload={"model": name})
    return err == "", err


def pull(base, name, on_progress):
    """Holt ein Modell und meldet den Fortschritt zeilenweise zurück.

    Ollama antwortet mit NDJSON: je Zeile ein Zustand mit `status` und,
    solange geladen wird, `completed`/`total`. Wir reichen jede Zeile an
    `on_progress` weiter und sammeln nichts an.
    """
    resp, err = _call(base, "/api/pull", method="POST",
                      payload={"model": name, "stream": True},
                      timeout=PULL_TIMEOUT, stream=True)
    if err:
        return False, err
    last_error = ""
    try:
        while True:
            line = resp.readline()
            if not line:
                break
            try:
                doc = json.loads(line)
            except Exception:
                continue
            if doc.get("error"):
                last_error = doc["error"]
                break
            on_progress(doc.get("status", ""),
                        int(doc.get("completed") or 0),
                        int(doc.get("total") or 0))
    except Exception as exc:
        last_error = f"Verbindung abgebrochen ({type(exc).__name__})"
    finally:
        try:
            resp.close()
        except Exception:
            pass
    return (not last_error), last_error


def human_size(value):
    if not value:
        return "—"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.1f} {unit}".replace(".0 ", " ")
        value /= step
    return f"{value:.1f} TB"


def instance_of(url):
    """Der Instanzname hinter einer Adresse wie `http://oaap-app-ollama:11434`.

    Gebraucht für die Zeilen, die man ins KI-Gateway einträgt — dort
    steht derselbe Containername, weil beide Apps über eine
    App-zu-App-Verbindung (RFC-0016) auf einem Netz liegen.
    """
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0]
    return host[len("oaap-app-"):] if host.startswith("oaap-app-") else host
