"""Die Einrichtungstür des Wikis (RFC-0104 §3.1; RFC-0110 §4 „Roles“).

Eine Art: `role` -- eine BookStack-Rolle für eine Gruppe des Realms.

    {"kind": "role", "id": "/wiki-redaktion", "title": "Redaktion", "like": "editor"}

- `id` ist der Gruppenpfad, wie er im Claim `groups` steht (der Konnektor
  setzt `full.path`, also mit führendem /). Er wird die „Externe
  Authentifizierungs-ID“ der Rolle; BookStack vergleicht ohne Rücksicht auf
  Groß- und Kleinschreibung, ein Komma trennt dort mehrere -- deshalb ist
  ein Komma in `id` nicht erlaubt.
- `title` ist der Name, den Menschen in BookStack sehen (Vorgabe: der
  Gruppenname ohne /).
- `like` sagt, wessen Rechte die neue Rolle bekommt: `viewer` (Vorgabe,
  nur lesen) oder `editor` -- kopiert von BookStacks Rollen „Viewer“ und
  „Editor“. Was ein Buch nur bestimmten Rollen zeigt, regelt BookStack an
  diesem Buch, nicht diese Tür.

Gibt es schon eine Rolle für die Gruppe, ist das `present`, wer immer sie
angelegt hat -- die Tür ändert nie eine bestehende Rolle. `remove` nimmt eine
Rolle nur weg, solange sie noch so ist, wie die Tür sie angelegt hat.

Geprüft wird nur der Schlüssel (`Authorization: Bearer <OAAP_SETUP_KEY>`);
Identitäts-Kopfzeilen liest diese Tür nie.
"""
import datetime
import hashlib
import hmac
import json
import os
import re

from wiki import WikiError

KINDS = ["role"]
LIKE = {"viewer": "Viewer", "editor": "Editor"}
SECRET_FIELDS = ("key", "token", "secret", "password", "credentials")
ITEM_FIELDS = {"kind", "id", "title", "like"}
MAX_ITEMS = 50


class Malformed(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def authorized(headers, env):
    want = (env.get("OAAP_SETUP_KEY") or "").strip()
    if not want:
        return False
    got = headers.get("Authorization", "") or ""
    if not got.startswith("Bearer "):
        return False
    return hmac.compare_digest(got[len("Bearer "):].strip().encode("utf-8"), want.encode("utf-8"))


def actor_of(headers):
    a = re.sub(r"[\x00-\x1f\x7f]", "", headers.get("X-OAAP-Setup-Actor", "") or "")
    return a.strip()[:200] or "?"


# --- Erinnerung ------------------------------------------------------------------
class Memory:
    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                m = json.load(f)
            return m if isinstance(m, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, m):
        tmp = self.path + ".new"
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, key):
        return (self.load().get("made") or {}).get(key)

    def put(self, key, value):
        m = self.load()
        m.setdefault("made", {})[key] = value
        self.save(m)

    def drop(self, key):
        m = self.load()
        (m.get("made") or {}).pop(key, None)
        self.save(m)


# --- Rollen in BookStack ------------------------------------------------------------
def _ids(role):
    return [x.strip().lower() for x in (role.get("external_auth_id") or "").split(",") if x.strip()]


def _roles(wiki):
    st, doc = wiki.api("GET", "/api/roles?count=500")
    if st != 200:
        raise WikiError(f"Rollen lesen ging nicht ({st})")
    return doc.get("data", [])


def _role_for(wiki, group):
    g = group.strip().lower()
    for r in _roles(wiki):
        if g in _ids(r):
            return r
    return None


def _role_state(wiki, role_id):
    st, doc = wiki.api("GET", f"/api/roles/{role_id}")
    if st != 200:
        return None
    return doc


def _digest(state):
    keep = {"display_name": state.get("display_name"),
            "external_auth_id": state.get("external_auth_id"),
            "permissions": sorted(state.get("permissions") or [])}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode("utf-8")).hexdigest()


def _template(wiki, like):
    name = LIKE[like]
    for r in _roles(wiki):
        if r.get("display_name") == name and not r.get("system_name"):
            st = _role_state(wiki, r["id"])
            if st:
                return st.get("permissions") or []
    raise WikiError(f"BookStacks Rolle „{name}“ gibt es nicht mehr (umbenannt?) -- "
                    f"ohne Vorlage legt die Tür keine Rolle an")


# --- Prüfen des Rumpfs ----------------------------------------------------------------
def _check_item(it, removing):
    if not isinstance(it, dict):
        raise Malformed("jeder Eintrag ist ein Objekt")
    if not isinstance(it.get("kind"), str) or not isinstance(it.get("id"), str):
        raise Malformed("jeder Eintrag braucht kind und id (Text)")


def parse(body):
    try:
        msg = json.loads(body or b"")
    except ValueError:
        raise Malformed("kein JSON")
    if not isinstance(msg, dict) or msg.get("v") != 1:
        raise Malformed("erwartet ein Objekt mit v: 1")
    items, remove = msg.get("items"), msg.get("remove")
    if (items is None) == (remove is None):
        raise Malformed("genau eines von items oder remove")
    lst = items if items is not None else remove
    if not isinstance(lst, list) or len(lst) > MAX_ITEMS:
        raise Malformed(f"items/remove ist eine Liste (höchstens {MAX_ITEMS})")
    for it in lst:
        _check_item(it, items is None)
    dry = msg.get("dry", False)
    if not isinstance(dry, bool):
        raise Malformed("dry ist true oder false")
    return lst, items is None, dry


def _invalid(it):
    """Ein Satz, wenn der Eintrag nicht geht; sonst None."""
    bad = sorted(k for k in it if isinstance(k, str) and k.lower() in SECRET_FIELDS)
    if bad:
        return f"Geheimnisse nie über die Einrichtungstür ({', '.join(bad)})"
    if it["kind"] not in KINDS:
        return f"unbekannte Art {it['kind']!r} (diese App kennt {', '.join(KINDS)})"
    extra = sorted(set(it) - ITEM_FIELDS)
    if extra:
        return f"unbekannte Felder: {', '.join(extra)}"
    g = it["id"].strip()
    if not g or len(g) > 180 or "," in g or re.search(r"[\x00-\x1f\x7f]", g):
        return "id ist der Gruppenpfad des Realms (1–180 Zeichen, ohne Komma)"
    if "title" in it and (not isinstance(it["title"], str) or not 1 <= len(it["title"].strip()) <= 180):
        return "title ist ein Text mit 1–180 Zeichen"
    if it.get("like", "viewer") not in LIKE:
        return f"like ist {' oder '.join(LIKE)}"
    return None


# --- die Tür --------------------------------------------------------------------------
def get(wiki, kind, id_):
    if not kind:
        return 200, {"v": 1, "kinds": KINDS}
    if kind not in KINDS:
        return 400, {"v": 1, "error": f"unbekannte Art {kind!r}", "kinds": KINDS}
    if not id_:
        items = [{"id": r.get("external_auth_id"), "title": r.get("display_name")}
                 for r in _roles(wiki) if _ids(r)]
        return 200, {"v": 1, "kind": kind, "items": items}
    r = _role_for(wiki, id_)
    if r:
        return 200, {"v": 1, "kind": kind, "id": id_, "state": "present", "ready": True,
                     "note": f"Rolle „{r.get('display_name')}“ (id {r.get('id')})"}
    return 200, {"v": 1, "kind": kind, "id": id_, "state": "absent", "ready": False,
                 "note": "keine Rolle für diese Gruppe"}


def _make(wiki, mem, it, dry, actor):
    group = it["id"].strip()
    r = _role_for(wiki, group)
    if r:
        return "present", f"Rolle „{r.get('display_name')}“ (id {r['id']}) gilt schon für {group}"
    like = it.get("like", "viewer")
    perms = _template(wiki, like)
    title = (it.get("title") or group.lstrip("/").split("/")[-1] or group).strip()
    if dry:
        return "would_make", f"würde Rolle „{title}“ wie „{LIKE[like]}“ für {group} anlegen"
    st, doc = wiki.api("POST", "/api/roles", {
        "display_name": title, "external_auth_id": group, "permissions": perms,
        "description": f"Gruppe {group} des Realms -- angelegt von der Einrichtung (OAAP)"})
    if st != 200:
        return "refused", f"BookStack legte die Rolle nicht an ({st}: {doc})"
    state = _role_state(wiki, doc["id"]) or doc
    mem.put(f"role:{group.lower()}", {"role_id": doc["id"], "actor": actor, "at": now(),
                                      "digest": _digest(state)})
    return "made", f"Rolle „{title}“ (id {doc['id']}) wie „{LIKE[like]}“"


def _remove(wiki, mem, it, dry):
    group = it["id"].strip()
    key = f"role:{group.lower()}"
    r = _role_for(wiki, group)
    made = mem.get(key)
    if not r:
        if made and not dry:
            mem.drop(key)
        return "gone", "keine Rolle für diese Gruppe"
    if not made or made.get("role_id") != r["id"]:
        return "conflict", f"Rolle „{r.get('display_name')}“ hat nicht die Einrichtung angelegt -- sie bleibt"
    state = _role_state(wiki, r["id"])
    if not state or _digest(state) != made.get("digest"):
        return "conflict", f"Rolle „{r.get('display_name')}“ wurde seit der Einrichtung geändert -- sie bleibt"
    if dry:
        return "would_remove", f"würde Rolle „{r.get('display_name')}“ entfernen"
    st, doc = wiki.api("DELETE", f"/api/roles/{r['id']}")
    if st not in (200, 204):
        return "refused", f"BookStack entfernte die Rolle nicht ({st}: {doc})"
    mem.drop(key)
    return "removed", f"Rolle „{r.get('display_name')}“ entfernt"


def post(wiki, mem, body, actor):
    lst, is_remove, dry = parse(body)
    results = []
    for it in lst:
        kind, id_ = it["kind"], it["id"]
        bad = _invalid(it)
        try:
            if bad:
                res, note = "refused", bad
            elif is_remove:
                res, note = _remove(wiki, mem, it, dry)
            else:
                res, note = _make(wiki, mem, it, dry, actor)
        except WikiError as e:
            res, note = "refused", str(e)
        results.append({"kind": kind, "id": id_, "result": res, "note": note})
    return 200, {"v": 1, "results": results}
