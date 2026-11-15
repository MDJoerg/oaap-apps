"""FleetView-Regeln — testbar ohne Netz und ohne HTTP-Server.

Seit 1.0.0 (RFC-0085 §10) liest FleetView die Flotte aus dem Zwilling
seines Mandanten (auf der Zentrale `oaap-ops`): Der Flottendienst der
Zentrale schreibt dort jeden gekoppelten Knoten als `FleetNode` und jede
seiner Instanzen als `FleetInstance` hinein (RFC-0085 Nachtrag A2).
FleetView fragt keinen Knoten mehr selbst, hält keine Knotenliste und
keinen Schlüssel je Knoten. Ein neu gekoppelter Knoten erscheint von
selbst, mit dem Namen, den er unter „Gesundheit" trägt (Alias,
RFC-0082).

Hier liegt alles, was Entscheidungen trifft: die Antwort des Zwillings
lesen (`GET /twin/objects?type=`, oaap.data.twin 0.7 §2.6a), Knoten und
Instanzen zusammenführen, den Zustand je Knoten bilden und die Sicht für
die Oberfläche bauen. Der eine Zugang ist der Maschinenschlüssel der
Instanz (`OAAP_PLATFORM_KEY`); er erscheint nirgends außer im
Authorization-Header von `fetch_type`.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

NODE, INSTANCE = "FleetNode", "FleetInstance"

# Zustand eines Knotens aus FleetView-Sicht. Lebenszeichen (das Urteil der
# Zentrale) zuerst: ein stummer Knoten ist stumm, egal was er zuletzt über
# sich sagte. Nur ein Knoten, der lebt, wird nach seinen Diensten beurteilt.
STATE_LABELS = {
    "ok": "Gesund", "warn": "Auffällig", "error": "Gestört",
    "unknown": "Unbekannt", "silent": "Stumm", "removed": "Entfernt",
    "standby": "Standby",
}

ATTENTION_LABELS = {
    "core_service_down": "Kerndienst ausgefallen",
    "dns_drift": "DNS zeigt woanders hin",
    "dns_unresolved": "Name löst nicht auf",
    "confirmation_pending": "Bestätigung offen",
    "instance_unhealthy": "Instanz ungesund",
    # Host-Pflege (oaap.fleet.status 0.5/0.6)
    "reboot_required": "Neustart nötig",
    "reboot_planned": "Neustart geplant",
    "reboot_overdue": "Neustart überfällig",
    "reboot_deferred": "Neustart aufgeschoben",
    "images_open": "Abbilder mit Sicherheitsmeldungen",
    "advisories_open": "Offene Sicherheitsmeldungen",
    "advisories_stale": "Liste der Sicherheitsmeldungen veraltet",
    "auto_updates_off": "Automatische Updates aus",
    # Verbindungen (RFC-0033, RFC-0060)
    "connector_down": "Konnektor getrennt",
    "tunnel_down": "Tunnel getrennt",
    "gateway_mptcp": "Gateway ohne MPTCP",
}

LIVENESS_REASON = {
    "silent": "Kein Lebenszeichen an der Zentrale",
    "unknown": "Lebenszeichen unklar (Eingang getrennt oder Zentrale startet)",
}

_RANK = {"ok": 0, "standby": 0, "": 0, "unknown": 1, "warn": 2, "error": 3}


class TwinError(Exception):
    """status 0: der Zwilling war gar nicht erreichbar (Konfiguration oder
    Netz) -- anders als ein echter HTTP-Status des Dienstes."""

    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def fetch_type(twin_url, key, type_key, timeout=15):
    """Alle Objekte eines Typs: [objekt, ...]. TWIN_URL endet schon auf
    `/twin` (appctl), das Gateway reicht den Pfad unverändert weiter --
    deshalb `/objects`, nicht `/twin/objects`."""
    if not twin_url or not key:
        raise TwinError(0, "OAAP_TWIN_URL/OAAP_PLATFORM_KEY fehlen")
    req = urllib.request.Request(
        twin_url.rstrip("/") + "/objects?type=" + urllib.parse.quote(type_key),
        headers={"Authorization": f"Bearer {key}", "User-Agent": "oaap-fleetview"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(16 * 1024 * 1024)
    except urllib.error.HTTPError as e:
        # Der Text des Zwillings sagt, was fehlt (Bindung, Schema) -- er
        # enthält nie den Schlüssel; gekürzt, weil er auf die Seite geht.
        detail = e.read(400).decode("utf-8", "replace").strip()
        raise TwinError(e.code, detail) from None
    except OSError as e:
        raise TwinError(0, type(e).__name__) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise TwinError(0, "Antwort ist kein JSON") from None
    objs = data.get("objects") if isinstance(data, dict) else None
    if not isinstance(objs, list):
        raise TwinError(0, "Antwort ohne 'objects'")
    return objs, bool(data.get("truncated"))


def _group(obj, key):
    """Eine Gruppe eines Objekts. Nach einer Zusammenführung kann eine
    Gruppe doppelt stehen (`key+<id>`); dann gilt die erste."""
    groups = (obj or {}).get("groups") or {}
    if key in groups:
        return groups[key]
    for k in sorted(groups):
        if k.startswith(key + "+"):
            return groups[k]
    return {}


def attr(obj, group, name, default=""):
    """Wert eines Flotten-Attributs (Schlüssel mit Präfix `fleet`,
    fleet_model.ak)."""
    a = (_group(obj, group).get("attributes") or {}).get(
        "fleet" + name[0].upper() + name[1:])
    if not isinstance(a, dict):
        return default
    v = a.get("value")
    return default if v is None else v


def rel_targets(obj, group, name):
    key = "fleet" + name[0].upper() + name[1:]
    return [r.get("target") for r in (_group(obj, group).get("relations") or [])
            if r.get("key") == key and not r.get("valid_to")]


def _split(text):
    return [p.strip() for p in str(text or "").split(",") if p.strip()]


def portal_url(address):
    """Die Portaladresse eines Knotens aus seiner gemeldeten Adresse: ein
    Name mit Punkt bekommt https, ein LAN-Name oder eine IP http."""
    a = str(address or "").strip().strip("/")
    if not a:
        return ""
    if re.fullmatch(r"[0-9.]+|\[?[0-9a-fA-F:]+\]?", a) or "." not in a:
        return "http://" + a
    return "https://" + a


def instance_row(obj):
    return {
        "id": obj.get("id", ""),
        "instance": attr(obj, "oaap.appsoftware", "instanceName") or obj.get("title", ""),
        "app": attr(obj, "oaap.appsoftware", "app"),
        "version": attr(obj, "oaap.appsoftware", "appVersion"),
        "channel": attr(obj, "oaap.runtime", "channel"),
        "state": attr(obj, "oaap.runtime", "runState") or "unknown",
        "origin": attr(obj, "oaap.runtime", "origin"),
        "address": attr(obj, "oaap.runtime", "address"),
        "auto_address": attr(obj, "oaap.runtime", "autoAddress"),
        "auto_state": attr(obj, "oaap.runtime", "autoState"),
        "node_id": next(iter(rel_targets(obj, "oaap.runtime", "isPartOf")), ""),
    }


def node_state(liveness, core_worst, instances):
    """Gesamtzustand: Lebenszeichen vor Selbstauskunft."""
    if liveness in ("removed", "silent", "unknown"):
        return liveness
    worst = core_worst or "ok"
    for i in instances:
        s = i.get("state") or "unknown"
        if _RANK.get(s, 1) > _RANK.get(worst, 0):
            worst = s
    return worst if worst in _RANK and worst != "" else "unknown"


def node_rows(nodes, instances):
    """Eine Zeile je Knoten, sortiert nach dem angezeigten Namen; ein an
    der Zentrale entfernter Knoten steht am Ende."""
    by_node = {}
    for o in instances:
        r = instance_row(o)
        by_node.setdefault(r["node_id"], []).append(r)
    rows = []
    for o in nodes:
        name = o.get("title", "")
        alias = attr(o, "oaap.placement", "alias") or name
        insts = sorted(by_node.get(o.get("id", ""), []), key=lambda r: r["instance"])
        live = [i for i in insts if i["state"] != "removed"]
        liveness = attr(o, "oaap.liveness", "state") or "unknown"
        rows.append({
            "name": name, "label": alias, "renamed": alias != name,
            "id": o.get("id", ""),
            "liveness": liveness, "since": attr(o, "oaap.liveness", "since"),
            "state": node_state(liveness, attr(o, "oaap.services", "coreWorst"), live),
            "version": attr(o, "oaap.software", "platformVersion") or "—",
            "os": attr(o, "oaap.software", "os"),
            "profiles": _split(attr(o, "oaap.software", "profiles")),
            "address": attr(o, "oaap.placement", "address"),
            "url": portal_url(attr(o, "oaap.placement", "address")),
            "via": attr(o, "oaap.placement", "via"),
            "place": attr(o, "oaap.placement", "place"),
            "tags": _split(attr(o, "oaap.placement", "tags")),
            "core": _split(attr(o, "oaap.services", "coreServices")),
            "attention_count": int(attr(o, "oaap.attention", "attentionCount", 0) or 0),
            "attention_kinds": _split(attr(o, "oaap.attention", "attentionKinds")),
            "attention_detail": attr(o, "oaap.attention", "attentionDetail"),
            "public_ip": attr(o, "oaap.dns", "publicIp"),
            "names_total": attr(o, "oaap.dns", "namesTotal", 0) or 0,
            "names_problems": attr(o, "oaap.dns", "namesProblems"),
            "reboot_required": attr(o, "oaap.hostcare", "rebootRequired", None),
            "reboot_plan": attr(o, "oaap.hostcare", "rebootPlanAt"),
            "advisories_open": attr(o, "oaap.hostcare", "advisoriesOpen", None),
            "instances": live,
            "removed_instances": [i for i in insts if i["state"] == "removed"],
            "inst_total": len(live),
            "inst_ok": sum(1 for i in live if i["state"] == "ok"),
            "inst_standby": sum(1 for i in live if i["state"] == "standby"),
        })
    rows.sort(key=lambda r: (r["liveness"] == "removed", r["label"].lower()))
    return rows


def find_row(rows, name):
    """Ein Knoten nach seinem festen Namen (Quellschlüssel) oder Alias."""
    return next((r for r in rows if r["name"] == name), None) or \
        next((r for r in rows if r["label"] == name), None)


def duplicate_instances(rows):
    """Instanznamen, die auf mehreren Knoten laufen -- ein Hinweis, kein
    Alarm (dieselbe App auf mehreren Knoten kann gewollt sein)."""
    where = {}
    for row in rows:
        if row["liveness"] == "removed":
            continue
        for inst in row["instances"]:
            if inst["instance"]:
                where.setdefault(inst["instance"], []).append(row["label"])
    return [{"instance": n, "nodes": nodes}
            for n, nodes in sorted(where.items()) if len(nodes) > 1]


def fleet_attention(rows):
    """Was einen Menschen braucht, oben auf der Seite."""
    items = []
    for row in rows:
        if row["liveness"] == "removed":
            continue
        if row["liveness"] in LIVENESS_REASON:
            items.append({"node": row["label"], "label": STATE_LABELS[row["liveness"]],
                          "detail": LIVENESS_REASON[row["liveness"]]})
        if row["attention_count"]:
            kinds = [ATTENTION_LABELS.get(k, k) for k in row["attention_kinds"]] or ["Hinweis"]
            items.append({"node": row["label"], "label": ", ".join(kinds),
                          "detail": str(row["attention_detail"] or "")})
    return items


def version_note(rows):
    versions = {r["version"] for r in rows
                if r["version"] not in ("", "—") and r["liveness"] != "removed"}
    if len(versions) > 1:
        return "Plattformversionen weichen ab: " + ", ".join(sorted(versions))
    return ""


def pairing_targets(rows):
    """RFC-0085 §17: gekoppelt wird im Portal der Zentrale (Profil
    `fleet-hub`); FleetView bleibt lesend und verlinkt nur."""
    return [{"name": r["label"], "url": r["url"].rstrip("/") + "/flotte#zentrale"}
            for r in rows if "fleet-hub" in r["profiles"] and r["url"]]


def pairing_link(row):
    return row["url"].rstrip("/") + "/flotte#kopplung" if row["url"] else ""


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
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def read_fleet(twin_url, key, previous=None, fetch=fetch_type, now=None):
    """Ein Lesen des Zwillings. Ein Fehlschlag verwirft den letzten
    bekannten Stand nicht: er bleibt mit dem Fehler daneben stehen."""
    previous = previous or {}
    now_iso = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        nodes, t1 = fetch(twin_url, key, NODE)
        insts, t2 = fetch(twin_url, key, INSTANCE)
    except TwinError as e:
        return {"nodes": previous.get("nodes") or [], "instances": previous.get("instances") or [],
                "read": previous.get("read", ""), "error": error_text(e),
                "truncated": previous.get("truncated", False)}
    return {"nodes": nodes, "instances": insts, "read": now_iso, "error": "",
            "truncated": t1 or t2}


def error_text(e):
    """Was die Seite über einen Fehlschlag sagt."""
    if e.status == 0 and "fehlen" in e.detail:
        return ("Diese Instanz hat keinen Zugang zum Zwilling. FleetView gehört "
                "auf die Zentrale (Profil fleet-hub) in den Mandanten, in den die "
                "Flotte schreibt (oaap fleet hub twin), meist oaap-ops.")
    if e.status == 403:
        return ("Der Zwilling verweigert das Lesen (403): Diese Instanz hat die "
                "Flotten-Typen nicht gebunden. " + e.detail[:200])
    if e.status == 409:
        return "Dieser Mandant hat noch keinen Zwilling (409)."
    if e.status == 0:
        return f"Zwilling nicht erreichbar ({e.detail})"
    return f"Zwilling antwortet mit HTTP {e.status}"


def age_seconds(read_iso, now=None):
    now = now or datetime.now(timezone.utc)
    try:
        t = datetime.strptime(read_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, int((now - t).total_seconds()))
