"""Die Einrichtungstür von help (RFC-0104 §3.1, §3.3; Manifest 0.18 `setup`).

Der Knoten ruft sie beim Aufbau eines Mandanten aus einem Profil, über den
internen Zuhörer des Gateways (nie über eine öffentliche Route). Geprüft wird
nur ein Schlüssel: `Authorization: Bearer <OAAP_SETUP_KEY>`, den die
Plattform bei der Installation macht und im Environment der Instanz hält.
Ohne diesen Wert im Environment lehnt die Tür jeden Ruf ab (403).
Identitäts-Kopfzeilen werden hier nie gelesen -- auch ein tenant_admin kommt
ohne den Schlüssel nicht durch. `X-OAAP-Setup-Actor` (wer den Aufbau
startete) geht nur ins eigene Protokoll und in die Erinnerung je Eintrag.

Eine Art: `{"kind": "fetch", "id": "docs"}` -- die Dokumentation jetzt holen
(was „Jetzt holen“ auf /admin tut). `present`, sobald Dokumente aus der
Destination docs-origin da sind (nicht nur der mitgelieferte Stand). Nichts
zu entfernen: `remove` antwortet `gone`.
"""
import datetime
import hashlib
import hmac
import json
import os
import re

import index as ix
from library import ARCHIVE_SOURCE

KINDS = ["fetch"]
FETCH_ID = "docs"
SECRET_FIELDS = ("key", "token", "secret", "password", "credentials")
MAX_ITEMS = 50


class Malformed(Exception):
    pass


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def authorized(headers, env):
    """Nur der eigene Einrichtungsschlüssel. Ohne Schlüssel im Environment: nie."""
    want = (env.get("OAAP_SETUP_KEY") or "").strip()
    if not want:
        return False
    got = headers.get("Authorization", "")
    if not got.startswith("Bearer "):
        return False
    return hmac.compare_digest(got[len("Bearer "):].strip().encode("utf-8"), want.encode("utf-8"))


def actor_of(headers):
    """Für das Protokoll: ohne Steuerzeichen, gekürzt."""
    a = re.sub(r"[\x00-\x1f\x7f]", "", headers.get("X-OAAP-Setup-Actor", "") or "")
    return a.strip()[:200] or "?"


# --- Erinnerung: wer hat was wann gemacht (§3.1) --------------------------
def _memory_file(lib):
    return os.path.join(lib.data_dir, "setup.json")


def _memory(lib):
    try:
        with open(_memory_file(lib), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(lib, item, actor):
    m = _memory(lib)
    digest = hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False)
                            .encode("utf-8")).hexdigest()
    m.setdefault("made", {})[f"{item['kind']}:{item['id']}"] = {"actor": actor, "at": now(),
                                                                "digest": digest}
    tmp = _memory_file(lib) + ".new"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(m, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _memory_file(lib))


# --- die Art fetch ----------------------------------------------------------
def fetch_state(lib):
    """(present, note). present: Dokumente aus docs-origin sind im Index --
    der mitgelieferte Stand (seed) zählt nicht."""
    if not lib.oaap_docs:
        return False, "HELP_OAAP_DOCS=off: die Plattform-Doku ist abgeschaltet, es wird nichts geholt."
    st = lib.state()
    good = st.get("last_good") or {}
    have = bool(lib.topics(roles=ix.UNFILTERED).get(ARCHIVE_SOURCE))
    if st.get("answering") == "fetch" and good.get("files") and have:
        return True, f"geholt {good.get('at')}, {good.get('files')} Dateien"
    last = st.get(ARCHIVE_SOURCE) or {}
    if last and not last.get("ok") and last.get("error"):
        return False, f"noch keine Doku aus docs-origin; letzter Abruf: {last.get('error')}"
    if st.get("answering") == "seed":
        return False, "es antwortet nur der mitgelieferte Stand aus dem Paket"
    return False, "noch keine Doku aus docs-origin"


def _bound(lib):
    import sources as src
    return bool(src.destination_url(lib.env))


def _fetch_item(lib, item, dry, actor):
    present, note = fetch_state(lib)
    if present:
        return "present", note
    if not lib.oaap_docs:
        return "refused", note
    if dry:
        if not _bound(lib):
            return "would_make", "würde holen -- aber die Destination docs-origin ist nicht zugeordnet"
        return "would_make", "würde die Doku über docs-origin holen"
    st = lib.refresh()
    entry = st.get(ARCHIVE_SOURCE) or {}
    present, note = fetch_state(lib)
    if entry.get("ok") and present:
        _remember(lib, item, actor)
        return "made", note
    return "refused", f"Abruf gescheitert: {entry.get('error') or note}"


# --- Prüfen des Rumpfs -------------------------------------------------------
def _check_item(it):
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
        _check_item(it)
    dry = msg.get("dry", False)
    if not isinstance(dry, bool):
        raise Malformed("dry ist true oder false")
    return lst, items is None, dry


def secret_fields(it):
    return sorted(k for k in it if isinstance(k, str) and k.lower() in SECRET_FIELDS)


# --- die Tür -------------------------------------------------------------------
def get(lib, kind, id_):
    """GET ohne kind: was die App einrichten kann; mit kind und id: der Zustand."""
    if not kind:
        return 200, {"v": 1, "kinds": KINDS}
    if kind != "fetch":
        return 400, {"v": 1, "error": f"unbekannte Art {kind!r}", "kinds": KINDS}
    if id_ != FETCH_ID:
        return 200, {"v": 1, "kind": kind, "id": id_, "state": "absent", "ready": False,
                     "note": f"nur id {FETCH_ID!r} gibt es"}
    present, note = fetch_state(lib)
    return 200, {"v": 1, "kind": kind, "id": id_, "state": "present" if present else "absent",
                 "ready": present, "note": note}


def post(lib, body, actor):
    lst, is_remove, dry = parse(body)
    results = []
    for it in lst:
        kind, id_ = it["kind"], it["id"]
        bad = secret_fields(it)
        if bad:
            res, note = "refused", f"Geheimnisse nie über die Einrichtungstür ({', '.join(bad)})"
        elif kind != "fetch":
            res, note = "refused", f"unbekannte Art {kind!r} (diese App kennt {', '.join(KINDS)})"
        elif id_ != FETCH_ID:
            res, note = "refused", f"nur id {FETCH_ID!r} gibt es"
        elif is_remove:
            res, note = "gone", "nichts zu entfernen: geholte Doku bleibt, der nächste Abruf ersetzt sie"
        else:
            res, note = _fetch_item(lib, it, dry, actor)
        results.append({"kind": kind, "id": id_, "result": res, "note": note})
    return 200, {"v": 1, "results": results}
