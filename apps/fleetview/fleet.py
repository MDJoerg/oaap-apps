"""FleetView-Regeln — testbar ohne Netz und ohne HTTP-Server (RFC-0021 §3).

Hier liegt alles, was Entscheidungen trifft: Knotenliste und Schlüssel
aus der Betreiber-Konfiguration lesen, ein Status-Dokument abholen und
prüfen, den letzten bekannten Stand fortschreiben (nicht erreichbar ist
ein ZUSTAND, keine Fehlerseite) und die Sicht für die Oberfläche bauen.

Die eine Regel über allem: **Flotten-Schlüssel erscheinen nirgends** —
nicht im Zustand auf der Platte, nicht in einer Sicht, nicht in einer
Logzeile. Aus `parse_keys` kommen sie nur in den Authorization-Header
von `fetch_status`; alles, was Richtung Oberfläche geht, kennt
höchstens die Knotennamen, für die ein Schlüssel hinterlegt ist.
"""
import json
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCHEMA_PREFIX = "oaap.fleet.status/"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

# Zustands-Vokabular eines Knotens aus FleetView-Sicht. Die Werte des
# Dokuments (ok/warn/error/unknown) beschreiben, was der Knoten über
# sich sagt; diese hier beschreiben zusätzlich, ob wir ihn überhaupt
# gefragt bekommen.
STATE_LABELS = {
    "ok": "Gesund", "warn": "Auffällig", "error": "Gestört",
    "unknown": "Unbekannt", "unreachable": "Nicht erreichbar",
}

ATTENTION_LABELS = {
    "core_service_down": "Kerndienst ausgefallen",
    "dns_drift": "DNS zeigt woanders hin",
    "dns_unresolved": "Name löst nicht auf",
    "confirmation_pending": "Bestätigung offen",
    "instance_unhealthy": "Instanz ungesund",
}


def _entries(text):
    """`name=wert`-Zeilen; Zeilenumbruch ODER Semikolon trennt, damit
    der Wert auch durch ein einzeiliges Eingabefeld passt. `#` beginnt
    einen Kommentar."""
    for raw in re.split(r"[\n;]", text or ""):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield line


def parse_nodes(text):
    """Knotenliste aus der Konfiguration: `name=https://adresse`.

    Liefert (Knoten, Fehlerzeilen). Fehler werden benannt statt
    verschluckt — eine stille Kürzung der Liste sähe aus wie ein
    gesunder Teilbestand.
    """
    nodes, errors = [], []
    seen = set()
    for line in _entries(text):
        name, sep, url = (p.strip() for p in line.partition("="))
        if not sep or not url:
            errors.append(f"'{line}': erwartet name=https://adresse")
            continue
        if not NAME_RE.fullmatch(name):
            errors.append(f"'{name}': Name aus Kleinbuchstaben, Ziffern, '-'")
            continue
        if not re.match(r"^https?://[^\s/]+", url):
            errors.append(f"'{name}': Adresse muss mit http(s):// beginnen")
            continue
        if name in seen:
            errors.append(f"'{name}': doppelt in der Liste")
            continue
        seen.add(name)
        nodes.append({"name": name, "url": url.rstrip("/")})
    return nodes, errors


def parse_keys(text):
    """Schlüssel aus der geheimen Konfiguration: `name=schlüssel`.

    Bewusst nachsichtig (keine Fehlerliste): Die Oberfläche darf über
    diesen Wert nur sagen, für WELCHE Namen etwas hinterlegt ist —
    jede detailliertere Meldung wäre ein Kanal, über den Bruchstücke
    des Werts nach außen sickern.
    """
    keys = {}
    for line in _entries(text):
        name, sep, value = (p.strip() for p in line.partition("="))
        if sep and NAME_RE.fullmatch(name) and value:
            keys[name] = value
    return keys


def key_names(keys):
    """Das Einzige, was die Oberfläche über die Schlüssel erfährt."""
    return sorted(keys)


def fetch_status(url, key, timeout=10):
    """Ein Status-Dokument abholen. Liefert (dokument, "") oder (None, grund).

    Der Grund ist für die Oberfläche bestimmt und enthält daher nie den
    Schlüssel und nie Antwort-Inhalte (eine Fehlerseite könnte alles
    Mögliche sein).
    """
    req = urllib.request.Request(
        url + "/fleet/status",
        headers={"Authorization": f"Bearer {key}",
                 "User-Agent": "oaap-fleetview"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(1024 * 1024)
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return None, "Abgewiesen (403) — Schlüssel prüfen"
        if e.code == 429:
            return None, "Gebremst (429) — zu viele Fehlversuche"
        return None, f"HTTP {e.code}"
    except OSError as e:
        return None, f"Nicht erreichbar ({type(e).__name__})"
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "Antwort ist kein Status-Dokument"
    if not str(doc.get("schema", "")).startswith(SCHEMA_PREFIX):
        return None, "Antwort ist kein Status-Dokument"
    return doc, ""


def poll_all(nodes, keys, previous=None, fetch=fetch_status, now=None):
    """Alle Knoten abfragen und den Zustand fortschreiben.

    Ein Fehlschlag verwirft den letzten bekannten Stand NICHT — er
    bleibt als „zuletzt gesehen" stehen und bekommt den Fehler daneben
    (RFC-0021: nicht erreichbar ist ein Zustand).
    """
    previous = previous or {}
    now_iso = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    state = {}
    for node in nodes:
        name = node["name"]
        old = previous.get(name) or {}
        key = keys.get(name, "")
        if not key:
            doc, err = None, "Kein Schlüssel hinterlegt (FLEETVIEW_KEYS)"
        else:
            doc, err = fetch(node["url"], key)
        if doc is not None:
            state[name] = {"url": node["url"], "doc": doc, "error": "",
                           "fetched": now_iso}
        else:
            state[name] = {"url": node["url"], "doc": old.get("doc"),
                           "error": err, "fetched": old.get("fetched", "")}
    return state


def _age_seconds(fetched_iso, now):
    try:
        fetched = datetime.strptime(fetched_iso, "%Y-%m-%dT%H:%M:%SZ") \
                          .replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, int((now - fetched).total_seconds()))


def _doc_state(doc):
    """Gesamtzustand aus dem Dokument: schlechtester Einzelzustand."""
    rank = {"ok": 0, "unknown": 1, "warn": 2, "error": 3}
    worst = "ok"
    for row in list(doc.get("core") or []) + list(doc.get("instances") or []):
        s = row.get("state", "unknown")
        if rank.get(s, 1) > rank.get(worst, 0):
            worst = s
    return worst


def node_rows(nodes, state, now=None, interval=60):
    """Die Listenbericht-Zeilen: eine je konfiguriertem Knoten."""
    now = now or datetime.now(timezone.utc)
    rows = []
    for node in nodes:
        name = node["name"]
        entry = state.get(name) or {}
        doc = entry.get("doc")
        age = _age_seconds(entry.get("fetched", ""), now)
        stale = age is None or age > max(3 * interval, 180)
        insts = list((doc or {}).get("instances") or [])
        row = {
            "name": name, "url": entry.get("url", node["url"]),
            "has_doc": doc is not None,
            "error": entry.get("error", ""),
            "fetched": entry.get("fetched", ""), "age": age,
            "stale": stale,
            "state": "unreachable" if doc is None else _doc_state(doc),
            "version": (doc or {}).get("platform_version", "—"),
            "node_says": (doc or {}).get("node", ""),
            "profiles": list((doc or {}).get("profiles") or []),
            "instances": insts,
            "inst_total": len(insts),
            "inst_ok": sum(1 for i in insts if i.get("state") == "ok"),
            "attention": list((doc or {}).get("attention") or []),
            # Schema 0.2 (oaap.fleet.status): veröffentlichte Namen mit
            # den DNS-Urteilen des Knotens + seine öffentliche Adresse.
            # Ältere Knoten (0.1) liefern beides nicht — leer ist leer.
            "names": list((doc or {}).get("names") or []),
            "public_ip": (doc or {}).get("public_ip", ""),
        }
        rows.append(row)
    return rows


def duplicate_instances(rows):
    """Instanznamen, die auf mehreren Knoten existieren.

    Der eine Ort, der das sehen KANN, ist die Flotten-Sicht (Treiber:
    das doppelte bdt-hub-test vom 23.08.). Bewusst eine **Hinweis**-
    Liste und kein attention-Alarm: dieselbe App auf mehreren Knoten
    kann gewollt sein (Monitoring je Knoten) — benannt gehört es
    trotzdem.
    """
    where = {}
    for row in rows:
        for inst in row.get("instances") or []:
            name = inst.get("instance", "")
            if name:
                where.setdefault(name, []).append(row["name"])
    return [{"instance": n, "nodes": nodes}
            for n, nodes in sorted(where.items()) if len(nodes) > 1]


def fleet_attention(rows):
    """Alle Auffälligkeiten der Flotte, oben auf der Seite (RFC-0021:
    was einen Menschen braucht, darf keine Gruppierung verstecken)."""
    items = []
    for row in rows:
        if row["state"] == "unreachable":
            items.append({"node": row["name"], "label": "Nicht erreichbar",
                          "detail": row["error"]})
        elif row["error"]:
            items.append({"node": row["name"],
                          "label": "Letzte Abfrage fehlgeschlagen",
                          "detail": row["error"]})
        for a in row["attention"]:
            kind = a.get("kind", "")
            items.append({
                "node": row["name"],
                # Unbekannte Arten tolerieren (Spec §3.4): der rohe
                # kind ist besser als eine verschluckte Warnung.
                "label": ATTENTION_LABELS.get(kind, kind or "Hinweis"),
                "detail": a.get("instance") or a.get("detail") or "",
            })
    return items


def version_note(rows):
    """Weichen die Plattformversionen ab? Während eines Rollouts normal —
    aber sichtbar, nicht versteckt."""
    versions = {r["version"] for r in rows if r["version"] not in ("", "—")}
    if len(versions) > 1:
        return "Plattformversionen weichen ab: " + ", ".join(sorted(versions))
    return ""


def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    import os
    os.replace(tmp, path)
