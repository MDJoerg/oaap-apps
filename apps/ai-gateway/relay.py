"""Die Weiterleitung — durchreichen, nicht vermitteln (RFC-0023 A1).

Der Verkehr fließt durch jede Schicht, weil jede Schicht protokolliert
und misst; eine Schicht, die den Verkehr nicht sieht, kann beides
nicht. Der Preis (Latenz, ein Ausfallpunkt mehr) ist bewusst genommen,
und die Gegenleistung ist, dass jedes Gateway ohne Rückfrage sagen
kann, was ein Schlüssel verbraucht hat.

Zwei Eigenschaften, die hier in der Bauform stecken:

- **Zugangsdaten verlassen dieses Modul nie.** Sie gehen in den
  Authorization-Header der ausgehenden Anfrage und in keine Antwort,
  keine Fehlermeldung und keine Messzeile (Spec §4).
- **Inhalte werden nicht angefasst.** Es wird durchgeschrieben und
  ausschließlich nach dem `usage`-Feld gesehen. Kein Puffern des
  Gesprächs, kein Protokollieren, keine Auswertung (Spec §6).
"""
import json
import urllib.error
import urllib.request

# Wie lange auf ein Modell gewartet wird. Ein großes Modell mit langem
# Kontext braucht Minuten; ein zu kurzer Wert erzeugt Abbrüche, die wie
# ein Fehler der Bezugsquelle aussehen, aber unsere Ungeduld sind.
CONNECT_TIMEOUT = 20
READ_TIMEOUT = 600


class Upstream:
    """Antwort einer Bezugsquelle. `resp` ist offen, solange gestreamt wird."""

    def __init__(self, status, content_type="", body=b"", resp=None, error=""):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.resp = resp
        self.error = error

    def close(self):
        if self.resp is not None:
            try:
                self.resp.close()
            except Exception:
                pass


def call(supplier, path, payload, timeout=READ_TIMEOUT, stream=False):
    """Eine Anfrage an eine Bezugsquelle. Wirft nicht, sondern berichtet."""
    url = supplier["url"] + path
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "*/*"}
    if supplier.get("credential"):
        headers["Authorization"] = "Bearer " + supplier["credential"]
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read()
        except Exception:
            pass
        return Upstream(exc.code, exc.headers.get("Content-Type", ""), body)
    except Exception as exc:  # Verbindung, DNS, TLS, Zeitüberschreitung
        # Absichtlich nur die Art des Fehlers, nie die URL mit etwaigen
        # Zugangsdaten darin.
        return Upstream(0, error=f"{type(exc).__name__}: {exc}")
    ctype = resp.headers.get("Content-Type", "")
    if stream:
        return Upstream(resp.status, ctype, resp=resp)
    body = resp.read()
    resp.close()
    return Upstream(resp.status, ctype, body)


def call_json(supplier, path, payload, timeout=READ_TIMEOUT):
    """Nicht-Strom-Fall: Antwort plus Token-Zahlen aus `usage`."""
    up = call(supplier, path, payload, timeout=timeout, stream=False)
    counts = (None, None)
    if 200 <= up.status < 300:
        counts = usage_of(_safe_json(up.body))
    return up, counts


def open_stream(supplier, path, payload, timeout=READ_TIMEOUT):
    """Strom-Fall, mit einem Rückfall für Quellen ohne `stream_options`.

    Nach Token-Zahlen im Strom muss ausdrücklich gefragt werden
    (`stream_options.include_usage`). Nicht jede OpenAI-kompatible
    Quelle kennt das Feld; manche antworten darauf mit 400. Dann wird
    **einmal ohne** wiederholt — und die Messzeile sagt später ehrlich,
    dass es keine Zahlen gab, statt welche zu schätzen (Spec §5).
    """
    asked = dict(payload)
    if "stream_options" not in asked:
        asked["stream_options"] = {"include_usage": True}
    up = call(supplier, path, asked, timeout=timeout, stream=True)
    if up.status == 400 and "stream_options" not in payload:
        up.close()
        return call(supplier, path, payload, timeout=timeout, stream=True), False
    return up, True


def pump(up, write):
    """Schreibt einen SSE-Strom durch und liest dabei nur `usage` mit.

    Zeilenweise, weil SSE zeilenweise ist. Der Inhalt wird
    weitergereicht, nicht gesammelt: Was hier nicht im Speicher landet,
    kann auch nirgends hinfallen.
    """
    in_tokens = out_tokens = None
    while True:
        line = up.resp.readline()
        if not line:
            break
        write(line)
        if line.startswith(b"data:"):
            chunk = line[5:].strip()
            if chunk and chunk != b"[DONE]":
                got = usage_of(_safe_json(chunk))
                if got[0] is not None or got[1] is not None:
                    in_tokens, out_tokens = got
    return in_tokens, out_tokens


def usage_of(doc):
    """`prompt_tokens` / `completion_tokens`, wenn die Quelle sie nennt."""
    if not isinstance(doc, dict):
        return None, None
    usage = doc.get("usage")
    if not isinstance(usage, dict):
        return None, None
    def num(*names):
        for name in names:
            value = usage.get(name)
            if isinstance(value, int):
                return value
        return None
    return num("prompt_tokens", "input_tokens"), num("completion_tokens", "output_tokens")


def _safe_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None
