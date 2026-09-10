"""Minimal client for oaap.data.twin's HTTP API (RFC-0031 Schritt 3,
oaap.data.twin 0.1 SS2.4-2.7) -- stdlib only, like every other reference
app in this repo. Talks ONLY through the gateway (OAAP_TWIN_URL), with
the machine-principal key appctl mints at install (OAAP_PLATFORM_KEY) --
both platform-owned (RESERVED_ENV), never something this app invents,
shows or edits.

Both are ABSENT on a node without the 'store' profile, or on a
rehearsal instance (RFC-0031 SS2.2: a rehearsal gets neither, on
purpose -- D8's own schema copy is not built yet). `configured()` is
how the rest of this app tells that apart from a real outage.

Identical to partnerverwaltung's twin.py on purpose -- both are small,
independent reference apps (RFC-0031 Schritt 4) with no shared package
to import from, same as every other app in this monorepo owning its own
copy of a small helper module (see ollama-models/ollama.py, store-
editor/checker.py). A change here belongs in the sibling copy too.
"""
import json
import os
import urllib.error
import urllib.request

TWIN_URL = os.environ.get("OAAP_TWIN_URL", "").rstrip("/")
PLATFORM_KEY = os.environ.get("OAAP_PLATFORM_KEY", "")
# TWIN_URL already ends in '/twin' (appctl.py: f"http://{GATEWAY_CONTAINER}/twin"),
# and the Caddyfile's 'handle /twin/*' block forwards the path UNCHANGED --
# it does not strip the prefix. So every path below is '/objects...',
# NOT '/twin/objects...': the spec's route headers (SS2.5-2.7) name the
# full path as reached through the gateway from its root, but TWIN_URL
# is already past that root. Found live on oaap-test 2026-09-10 (a 404
# from the twin's own Flask app, not from the gateway -- the give-away
# that the path, not the auth, was wrong).


class TwinError(Exception):
    """status == 0 means the request never reached the twin at all
    (misconfigured or unreachable) -- distinct from a real HTTP status
    the service itself returned."""

    def __init__(self, status, detail):
        super().__init__(f"{status}: {detail}")
        self.status = status
        self.detail = detail


def configured():
    return bool(TWIN_URL and PLATFORM_KEY)


def _call(method, path, body=None):
    if not configured():
        raise TwinError(0, "OAAP_TWIN_URL/OAAP_PLATFORM_KEY fehlen -- diese Instanz "
                            "hat keinen Zwilling-Zugang (kein 'store'-Profil auf "
                            "diesem Knoten, oder eine Generalprobe -- RFC-0031 SS2.2 "
                            "gibt einer Generalprobe absichtlich keinen Schluessel)")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        TWIN_URL + path, data=data, method=method,
        headers={"Authorization": f"Bearer {PLATFORM_KEY}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise TwinError(exc.code, detail) from None
    except urllib.error.URLError as exc:
        raise TwinError(0, str(exc.reason)) from None


def get_object(obj_id):
    """GET /twin/objects/{id} (SS2.6) -- refused unless the caller
    contributes or consumes the object's type (RFC-0031 D7's simple
    half). Returns None for '404 no such object' rather than raising,
    since a stale or mistyped id pasted into a form is routine, not
    exceptional."""
    try:
        return _call("GET", f"/objects/{obj_id}")
    except TwinError as exc:
        if exc.status == 404:
            return None
        raise


def write_group(obj_id, group_key, attributes=None, relations=None,
                 activities=None, attr_valid_from=None, attr_valid_to=None):
    """PUT /twin/objects/{id}/groups/{group} (SS2.7) -- refused unless the
    caller contributes to the object's type, and refused again if the
    group already belongs to a different origin ("nobody else writes
    there", RFC-0031 SS3.3, enforced at the service, not trusted here)."""
    body = {}
    if attributes:
        body["attributes"] = attributes
        if attr_valid_from:
            body["valid_from"] = attr_valid_from
        if attr_valid_to:
            body["valid_to"] = attr_valid_to
    if relations:
        body["relations"] = relations
    if activities:
        body["activities"] = activities
    _call("PUT", f"/objects/{obj_id}/groups/{group_key}", body)
