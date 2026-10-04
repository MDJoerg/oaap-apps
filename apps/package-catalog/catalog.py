"""Der Speicher des Katalogs: Pakete, Versionen, Freigabe -- und die Liste.

Alles liegt in /data:

    packages/<app-id>/<version>-<sha12>.zip   die Pakete, wie hochgeladen
    state.json                                 Versionen, Notizen, Freigabe, Protokoll
    store.json                                 die Liste, die der KNOTEN liest

`store.json` ist das Einzige, was nach außen geht (RFC-0050 §4): je
App-Id die HÖCHSTE FREIGEGEBENE Version, mit Pfad, Prüfsumme und Größe
des Pakets. Der Knoten liest sie direkt aus diesem Verzeichnis, prüft
Größe und Prüfsumme an einer Kopie und vergleicht Id und Version mit dem
Manifest im Paket -- diese Datei ist also eine Behauptung, die er
nachprüft, kein Befehl.

Eine Version wird nie überschrieben: dieselbe App-Id mit derselben
Version ist ein Fehler. Eine freigegebene Version zu löschen verlangt
einen Grund, der im Protokoll bleibt.
"""
import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone

import zipinfo

VERSION_SPLIT = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+](.*))?$")


class CatalogError(Exception):
    """Eine Ablehnung für Menschen."""


def version_key(v):
    """Ordnung: 1.2.10 > 1.2.9, und 1.0.0-rc1 < 1.0.0."""
    m = VERSION_SPLIT.match(v or "")
    if not m:
        return (-1, -1, -1, 0, "")
    pre = m.group(4)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)),
            0 if pre else 1, pre or "")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Catalog:
    def __init__(self, data_dir):
        self.dir = data_dir
        self.lock = threading.RLock()
        os.makedirs(os.path.join(data_dir, "packages"), exist_ok=True)
        os.makedirs(os.path.join(data_dir, "tmp"), exist_ok=True)
        self.state = self._load()
        self.write_list()

    # -- state ------------------------------------------------------------
    @property
    def _state_path(self):
        return os.path.join(self.dir, "state.json")

    def _load(self):
        try:
            with open(self._state_path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and isinstance(d.get("apps"), dict):
                d.setdefault("audit", [])
                return d
        except (OSError, ValueError):
            pass
        return {"apps": {}, "audit": []}

    def _atomic(self, path, doc):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)

    def _save(self):
        self._atomic(self._state_path, self.state)
        self.write_list()

    def tmp_dir(self):
        return os.path.join(self.dir, "tmp")

    # -- reading ----------------------------------------------------------
    def apps(self):
        """[(app_id, name, [versions newest first])]."""
        out = []
        for app_id, a in sorted(self.state["apps"].items()):
            vs = sorted(a["versions"].values(),
                        key=lambda r: version_key(r["version"]), reverse=True)
            out.append((app_id, a.get("name") or app_id, vs))
        return out

    def get(self, app_id, version):
        return ((self.state["apps"].get(app_id) or {}).get("versions") or {}).get(version)

    def path_of(self, app_id, version):
        r = self.get(app_id, version)
        return os.path.join(self.dir, *r["file"].split("/")) if r else None

    # -- changing ---------------------------------------------------------
    def add(self, tmp_path, max_bytes, by, note=""):
        """Take an uploaded file in. The temp file is MOVED into the store."""
        info = zipinfo.read(tmp_path, max_bytes)
        app = info["app"]
        with self.lock:
            existing = self.get(app["id"], app["version"])
            if existing:
                raise CatalogError(
                    f"{app['id']} {app['version']} gibt es schon im Katalog "
                    "(Prüfsumme " + existing["sha256"][:12] + "). Eine Version "
                    "wird nie überschrieben -- bitte die Version im Manifest "
                    "erhöhen.")
            rel = f"packages/{app['id']}/{app['version']}-{info['sha256'][:12]}.zip"
            dest = os.path.join(self.dir, *rel.split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(tmp_path, dest)
            a = self.state["apps"].setdefault(
                app["id"], {"name": app["name"], "versions": {}})
            a["name"] = app["name"]
            a["versions"][app["version"]] = {
                "version": app["version"], "file": rel,
                "sha256": info["sha256"], "size": info["bytes"],
                "description": app["description"], "type": app["type"],
                "uploaded_by": by, "uploaded_at": _now(),
                "note": (note or "")[:500], "released": False}
            self._audit("upload", app["id"], app["version"], by)
            self._save()
            return self.get(app["id"], app["version"])

    def set_released(self, app_id, version, released, by):
        with self.lock:
            r = self.get(app_id, version)
            if not r:
                raise CatalogError("Diese Version gibt es nicht.")
            if bool(r["released"]) == bool(released):
                return r
            r["released"] = bool(released)
            if released:
                r["released_at"], r["released_by"] = _now(), by
            else:
                r.pop("released_at", None)
                r.pop("released_by", None)
            self._audit("release" if released else "withdraw", app_id, version, by)
            self._save()
            return r

    def set_note(self, app_id, version, note, by):
        with self.lock:
            r = self.get(app_id, version)
            if not r:
                raise CatalogError("Diese Version gibt es nicht.")
            r["note"] = (note or "")[:500]
            self._save()

    def delete(self, app_id, version, reason, by):
        """A version never released can go; a released one needs a reason."""
        with self.lock:
            r = self.get(app_id, version)
            if not r:
                raise CatalogError("Diese Version gibt es nicht.")
            reason = (reason or "").strip()
            if r["released"] and not reason:
                raise CatalogError("Eine freigegebene Version zu löschen "
                                   "verlangt einen Grund.")
            try:
                os.remove(self.path_of(app_id, version))
            except OSError:
                pass
            del self.state["apps"][app_id]["versions"][version]
            if not self.state["apps"][app_id]["versions"]:
                del self.state["apps"][app_id]
                try:
                    os.rmdir(os.path.join(self.dir, "packages", app_id))
                except OSError:
                    pass
            self._audit("delete", app_id, version, by,
                        reason=reason, was_released=bool(r["released"]))
            self._save()

    def _audit(self, action, app_id, version, by, **extra):
        self.state["audit"].append(dict(
            {"at": _now(), "action": action, "app": app_id,
             "version": version, "by": by}, **extra))
        del self.state["audit"][:-500]

    # -- the list the node reads -------------------------------------------
    def write_list(self):
        apps = []
        for app_id, name, versions in self.apps():
            rel = next((r for r in versions if r["released"]), None)
            if rel is None:
                continue
            entry = {"id": app_id, "name": name, "version": rel["version"],
                     "released": (rel.get("released_at") or "")[:10],
                     "summary": (rel.get("description") or "")[:300],
                     "description": rel.get("description") or "",
                     "package": {"zip": rel["file"], "sha256": rel["sha256"],
                                 "size": rel["size"]}}
            if rel.get("type"):
                entry["type"] = rel["type"]
            apps.append({k: v for k, v in entry.items() if v != ""})
        self._atomic(os.path.join(self.dir, "store.json"),
                     {"schema": "0.2", "name": "Paketkatalog", "apps": apps})
