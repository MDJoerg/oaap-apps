"""Sehen ist nicht Bekommen -- die EINE Regel (RFC-0077 §5).

Jeder Weg fragt diese Datei: Liste, Suche, Detailseite, Download, Feed,
öffentliche API, Verwaltung. Es gibt keinen zweiten Ort, an dem jemand
"darf das sehen" entscheidet (siehe „Zwei Wege, eine Regel“).

Ein Prinzipal ist das, was die Plattform über die anfragende Person oder
Maschine sagt -- auf einer öffentlichen Route NICHTS (die Plattform
entfernt dort die Identitäts-Kopfzeilen, und die App liest sie dort auch
nicht, siehe `principal()`).
"""
import fnmatch
import json
import time

ADMIN_ROLES = {"admin", "server_admin"}
ANON = {"anon": True, "user": "", "groups": frozenset(), "roles": frozenset()}


def principal(headers, public):
    """Wer fragt. Auf einem öffentlichen Pfad immer anonym -- auch wenn
    jemand Kopfzeilen mitschickt (die Plattform entfernt sie ohnehin)."""
    if public:
        return ANON
    user = (headers.get("X-OAAP-User") or "").strip()
    if not user:
        return ANON
    split = lambda v: frozenset(x.strip() for x in (v or "").split(",") if x.strip())  # noqa: E731
    return {"anon": False, "user": user,
            "groups": split(headers.get("X-OAAP-Groups")),
            "roles": split(headers.get("X-OAAP-Roles"))}


def is_admin(p):
    return not p["anon"] and bool(p["roles"] & ADMIN_ROLES)


def _matches(p, kind, name):
    if p["anon"]:
        return False
    if kind == "user":
        return name == p["user"]
    if kind == "group":
        return name in p["groups"]
    return False


def visibility_of(asset):
    try:
        v = json.loads(asset.get("visibility") or "{}")
    except ValueError:
        v = {}
    mode = v.get("mode") if v.get("mode") in ("anyone", "signed-in", "principals") else "anyone"
    return {"mode": mode, "principals": [x for x in v.get("principals") or []
                                          if isinstance(x, dict)]}


def free_channels(asset):
    try:
        return set(json.loads(asset.get("free_channels") or "[]"))
    except ValueError:
        return set()


def entitled(p, asset_id, channel, entitlements, now=None):
    """Hält p eine (nicht abgelaufene) Berechtigung für diesen Kanal?
    channel=None: für irgendeinen Kanal."""
    if p["anon"]:
        return False
    now = now or time.time()
    for e in entitlements:
        if e["asset"] != asset_id or not _matches(p, e["principal_kind"], e["principal"]):
            continue
        if e.get("expires_at") and float(e["expires_at"]) < now:
            continue
        chans = set(json.loads(e.get("channels") or '["*"]'))
        if channel is None or "*" in chans or channel in chans:
            return True
    return False


def can_see(p, asset, entitlements):
    """Gelistet, durchsuchbar, Detailseite. Sonst 404 -- nie 403."""
    if is_admin(p):
        return True
    v = visibility_of(asset)
    if v["mode"] == "anyone":
        return True
    if v["mode"] == "signed-in" and not p["anon"]:
        return True
    if v["mode"] == "principals" and any(
            _matches(p, x.get("kind"), x.get("name")) for x in v["principals"]):
        return True
    # wer berechtigt ist, sieht, was er bekommen darf
    return entitled(p, asset["id"], None, entitlements)


def can_obtain(p, asset, channel, entitlements):
    """Download und Feed auf diesem Kanal."""
    if not can_see(p, asset, entitlements):
        return False
    if is_admin(p):
        return True
    return channel in free_channels(asset) or entitled(p, asset["id"], channel, entitlements)


def state(p, asset, entitlements, channels):
    """'hidden' | 'locked' | 'obtainable' -- für Karten und Detailseite."""
    if not can_see(p, asset, entitlements):
        return "hidden"
    if any(can_obtain(p, asset, ch, entitlements) for ch in channels):
        return "obtainable"
    return "locked"


def is_reviewer(p, reviewers):
    """Prüfer (§15.8.5): ein Benutzer oder eine Gruppe, die der Betreiber benennt.
    Der Betreiber selbst ist kein Prüfer -- er entscheidet Eskalationen."""
    return any(_matches(p, r["principal_kind"], r["principal"]) for r in reviewers)


def can_manage(p, asset_id, publishers):
    """Partner light (§15.8.9): wer in ein Asset einstellen darf, beantwortet dessen
    Anfragen und vergibt dessen Berechtigungen -- nur für dieses Asset."""
    return can_publish(p, asset_id, None, publishers)


def can_publish(p, asset_id, channel, publishers):
    """Ins Asset einchecken / in einen Kanal legen. channel=None: irgendeiner."""
    if is_admin(p):
        return True
    if p["anon"]:
        return False
    for r in publishers:
        if not _matches(p, r["principal_kind"], r["principal"]):
            continue
        if not fnmatch.fnmatchcase(asset_id, r["asset_pattern"]):
            continue
        chans = set(json.loads(r.get("channels") or '["*"]'))
        if channel is None or "*" in chans or channel in chans:
            return True
    return False
