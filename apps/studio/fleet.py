"""Der Blick des Studios auf den **Zielknoten** — lesend, sonst nichts.

Ein Vorhaben lebt seit 0.3 nicht mehr zwangsläufig auf dem Knoten, auf
dem das Studio läuft: Die Test-Instanz kann auf einem anderen Knoten
stehen (Jörgs Weg am 23.08.: Studio auf oaap-demo, Instanz auf
oaapx01). Damit stellt sich eine Frage, die das Studio bisher nicht
beantworten konnte: *Was ist dort eigentlich los?*

Beantwortet wird sie mit der Auskunft, die es dafür schon gibt —
`GET /fleet/status` (Spec `oaap.fleet.status`, RFC-0021). Autorisiert
mit einem **Flotten-Schlüssel** je Knoten, hinterlegt als geheime
Konfiguration.

**Warum das die Regel „Das Studio hält nie ein Recht" nicht bricht:**
Die Regel meint das Recht, etwas zu *verändern* — und genau das bleibt
beim Anwender: Der Deploy-Token wird weiterhin bei jeder einzelnen
Handlung eingegeben und nirgends abgelegt. Ein Flotten-Schlüssel kann
laut Spec §2 ausschließlich **diese eine Auskunft lesen**: keine
Sitzung, keine Rollen, kein anderer Weg, kein Schreibweg. Und was er
liest, sind laut §3.1 **Fakten, nie Geheimnisse** — keine Tokens, keine
Konfigurationswerte, keine Quell-URLs. Er ist damit nicht mehr wert als
ein Blick auf die Gesundheitsseite, und ohne ihn läuft das Studio
unverändert weiter: Die Zustandsanzeige sagt dann, dass sie nicht
eingerichtet ist, und alles andere funktioniert wie zuvor.

Der Schlüssel selbst erscheint nirgends: nicht in der Datenbank (das
Studio schreibt ihn nie), nicht in einer Sicht, nicht in einer
Logzeile. Aus `parse_keys` kommt er nur in den Authorization-Header von
`fetch` — dieselbe Regel wie in FleetView.

Bewusst **kein** zweites FleetView: Das Studio pollt nicht im Takt und
hebt keinen letzten bekannten Stand auf. Es fragt beim Aufschlagen der
Seite nach, hält die Antwort kurz vor (ein Seitenaufbau soll nicht
jedes Mal an einem fernen Knoten hängen) und sagt ehrlich, wenn es
keine Antwort bekam. Wer die Landschaft über die Zeit beobachten will,
nimmt FleetView; hier geht es um zwei Instanzen eines Vorhabens.
"""

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import urlparse

SCHEMA_PREFIX = "oaap.fleet.status/"

# Knotenadressen als Schlüssel der Konfiguration: Hostname, notfalls mit
# Port. Punkte sind hier — anders als bei den Knoten-Kurznamen in
# FleetView — ausdrücklich erlaubt, denn hier steht die Adresse selbst.
HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,190}(:[0-9]{1,5})?$")

STATE_LABELS = {
    "ok": "Gesund", "warn": "Auffällig", "error": "Gestört",
    "unknown": "Unbekannt",
}
STATE_BADGE = {
    "ok": "ok", "warn": "warn", "error": "err", "unknown": "off",
}

ATTENTION_LABELS = {
    "core_service_down": "Kerndienst ausgefallen",
    "dns_drift": "DNS zeigt woanders hin",
    "dns_unresolved": "Name löst nicht auf",
    "confirmation_pending": "Bestätigung offen",
    "instance_unhealthy": "Instanz ungesund",
}

CHANNEL_LABELS = {"test": "Test", "production": "Produktiv"}

# Wie lange eine Antwort vorgehalten wird. Kurz genug, dass „Neu
# abfragen" nach dem Ausrollen etwas Neues zeigt, lang genug, dass das
# Blättern über mehrere Seiten den Knoten nicht befragt.
CACHE_SECONDS = 30
# Ein Fehlschlag wird kürzer vorgehalten als ein Erfolg: Ein Knoten, der
# gerade neu startet, soll nicht eine halbe Minute lang als tot gelten.
CACHE_ERROR_SECONDS = 10
TIMEOUT = 6

_cache = {}
_lock = threading.Lock()


def _entries(text):
    """`schlüssel=wert`-Zeilen; Zeilenumbruch ODER Semikolon trennt,
    damit der Wert auch durch ein einzeiliges Eingabefeld passt."""
    for raw in re.split(r"[\n;]", text or ""):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield line


def parse_keys(text):
    """Flotten-Schlüssel aus der geheimen Konfiguration: `knoten=wert`.

    Bewusst nachsichtig und ohne Fehlerliste (FleetView-Muster): Über
    diesen Wert darf die Oberfläche nur sagen, für WELCHE Knoten etwas
    hinterlegt ist — jede genauere Meldung wäre ein Kanal, durch den
    Bruchstücke des Werts nach außen sickern.
    """
    keys = {}
    for line in _entries(text):
        host, sep, value = (p.strip() for p in line.partition("="))
        host = host.lower()
        # Bequemlichkeit: Wer die ganze Adresse einträgt, bekommt sie
        # auf den Hostnamen zurückgeschnitten statt einer stillen
        # Nicht-Zuordnung.
        if "://" in host:
            host = urlparse(host).netloc.lower()
        if sep and value and HOST_RE.fullmatch(host):
            keys[host] = value
    return keys


def key_hosts(keys):
    """Das Einzige, was die Oberfläche über die Schlüssel erfährt."""
    return sorted(keys)


def node_base(url):
    """Aus einer beliebigen Adresse des Knotens seine Wurzel.

    Der Deploy-Hook `https://knoten/deploy/instanz` und die Adresse des
    Portals `https://knoten/` sind derselbe Knoten — das Studio rechnet
    beides auf `https://knoten` zurück.
    """
    u = urlparse((url or "").strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        return ""
    return f"{u.scheme}://{u.netloc}"


def host_of(url):
    return urlparse((url or "").strip()).netloc.lower()


def _fetch(base, key, timeout=TIMEOUT):
    """Ein Status-Dokument abholen. Liefert (dokument, grund).

    Der Grund geht in die Oberfläche und enthält deshalb nie den
    Schlüssel und nie Antwort-Inhalte (eine Fehlerseite kann alles
    Mögliche sein).
    """
    req = urllib.request.Request(
        base + "/fleet/status",
        headers={"Authorization": f"Bearer {key}",
                 "User-Agent": "oaap-studio"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1024 * 1024)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return None, ("Abgewiesen (403) — der Flotten-Schlüssel für "
                          "diesen Knoten passt nicht (mehr)")
        if e.code == 404:
            return None, ("Der Knoten kennt /fleet/status nicht — "
                          "Plattform älter als 0.1.41")
        if e.code == 429:
            return None, "Gebremst (429) — zu viele Fehlversuche"
        return None, f"HTTP {e.code}"
    except OSError as e:
        return None, f"Nicht erreichbar ({type(e).__name__})"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "Antwort ist kein Status-Dokument"
    if not isinstance(doc, dict) or \
            not str(doc.get("schema", "")).startswith(SCHEMA_PREFIX):
        return None, "Antwort ist kein Status-Dokument"
    return doc, ""


def status(base, keys, fresh=False, fetch=_fetch, now=None):
    """Der Zustand eines Knotens für die Oberfläche.

    Liefert immer ein Wörterbuch, nie eine Ausnahme: `doc` ist das
    Dokument oder None, `error` der Grund, `age` das Alter der Antwort
    in Sekunden. Ohne hinterlegten Schlüssel wird gar nicht erst
    gefragt — das ist kein Fehler, sondern ein nicht eingerichteter
    Zustand.

    **Wichtig dabei:** Die Adresse kommt aus dem Vorhaben, also von
    einem Anwender. Der Schlüssel wird deshalb **je Host** nachgesehen
    (`keys.get(host_of(base))`) — passt keiner, unterbleibt die Anfrage
    ganz. Ein Schlüssel kann so nie an einen anderen Host geraten als
    den, für den er ausgestellt wurde, und eine hingeschriebene fremde
    Adresse löst nicht einmal eine Verbindung aus.
    """
    now = now or datetime.now(timezone.utc)
    base = (base or "").rstrip("/")
    if not base:
        return {"configured": False, "doc": None, "error": "", "age": None}
    key = keys.get(host_of(base))
    if not key:
        return {"configured": False, "doc": None, "error": "", "age": None}

    with _lock:
        hit = _cache.get(base)
        if hit and not fresh:
            age = (now - hit["at"]).total_seconds()
            ttl = CACHE_ERROR_SECONDS if hit["error"] else CACHE_SECONDS
            if age < ttl:
                return {"configured": True, "doc": hit["doc"],
                        "error": hit["error"], "age": int(age)}

    doc, error = fetch(base, key)
    with _lock:
        _cache[base] = {"at": now, "doc": doc, "error": error}
    return {"configured": True, "doc": doc, "error": error, "age": 0}


def forget(base=None):
    """Vorgehaltene Antworten vergessen (Tests, und nach dem Ausrollen —
    da hat sich auf dem Knoten gerade nachweislich etwas geändert)."""
    with _lock:
        if base is None:
            _cache.clear()
        else:
            _cache.pop((base or "").rstrip("/"), None)


def instance(doc, name):
    """Die Zeile einer Instanz aus dem Dokument — oder None.

    None heißt: **Der Knoten kennt diese Instanz nicht.** Das ist eine
    Aussage, keine Lücke — nach einem Umzug oder bei einem Tippfehler im
    Instanznamen ist sie genau die, die man sehen will.
    """
    if not doc or not name:
        return None
    for row in doc.get("instances") or []:
        if row.get("instance") == name:
            return row
    return None


def attention_for(doc, names):
    """Auffälligkeiten des Knotens, die diese Instanzen betreffen.

    Unbekannte Arten werden mitgenommen (Spec §3.4: Konsumenten müssen
    sie tolerieren) — ein roher `kind` ist besser als eine verschluckte
    Warnung. Was keiner Instanz zugeordnet ist (etwa ein ausgefallener
    Kerndienst), gehört ebenfalls hierher: Es trifft dieses Vorhaben
    genauso.
    """
    wanted = {n for n in names if n}
    items = []
    for a in (doc or {}).get("attention") or []:
        inst = a.get("instance") or ""
        if inst and inst not in wanted:
            continue
        kind = a.get("kind", "")
        items.append({
            "label": ATTENTION_LABELS.get(kind, kind or "Hinweis"),
            "instance": inst,
            "detail": a.get("detail") or "",
        })
    return items


def names_for(doc, name):
    """Veröffentlichte Namen einer Instanz mit dem DNS-Urteil des Knotens.

    Aus Schema 0.2; ein Knoten mit 0.1 liefert die Liste nicht — leer
    ist dann leer und keine Aussage.
    """
    return [n for n in (doc or {}).get("names") or []
            if n.get("kind") in ("instance", "alias")
            and n.get("instance") == name]
