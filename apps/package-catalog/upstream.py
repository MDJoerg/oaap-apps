"""Spiegelmodus: Pakete aus einem Marketplace-Feed übernehmen (RFC-0077 §7.2).

Der Katalog holt den Feed über die Destination `upstream` (der
Maschinenschlüssel liegt dort, nie hier), lädt jedes Paket, das er noch
nicht hat, prüft Größe und SHA-256 gegen den Feed und gibt es dann an das
BESTEHENDE `Catalog.add()` -- alle bisherigen Regeln gelten (Manifest,
Pfade, Version, Id, nie überschreiben). Mit `auto` gibt er die Version
danach frei, mit `hold` wartet sie auf den Betreiber (D3).

Zurückgerufen im Marketplace (D7): mit `auto` wird eine lokal
freigegebene Version zurückgezogen, mit Grund im Protokoll; gelöscht wird
nie, laufende Instanzen bleiben unberührt. Mit `hold` wird sie nur markiert.

Ohne gebundene Destination tut dieses Modul nichts: der Katalog ist dann
genau 0.1.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import zipinfo

FEED_PATH = "/feed/v1/oaap-apps"
SCHEMA = "oaap.marketplace.feed/0.1"
PKG_URL_RE = re.compile(r"^files/([0-9a-f]{64})$")
MAX_FEED_BYTES = 4 * 1024 * 1024


class UpstreamError(Exception):
    """Ein Abruf ging schief; der Text steht auf der Seite."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Eine Weiterleitung führte an der Destination vorbei -- nicht folgen."""

    def redirect_request(self, *a, **k):
        return None


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Mirror:
    def __init__(self, catalog, env, max_bytes, opener=None):
        self.cat = catalog
        self.base = (env.get("OAAP_DESTINATION_UPSTREAM_URL") or "").rstrip("/")
        self.channel = (env.get("CATALOG_UPSTREAM_CHANNEL") or "stable").strip() or "stable"
        mode = (env.get("CATALOG_UPSTREAM_RELEASE") or "auto").strip().lower()
        self.mode = mode if mode in ("auto", "hold") else "auto"
        try:
            self.interval = max(1, int(env.get("CATALOG_UPSTREAM_INTERVAL_MIN") or 15))
        except ValueError:
            self.interval = 15
        self.max_bytes = max_bytes
        self.opener = opener or urllib.request.build_opener(_NoRedirect)
        self.lock = threading.Lock()

    @property
    def enabled(self):
        return bool(self.base)

    def status(self):
        return self.cat.state.setdefault("upstream", {})

    # -- one poll -------------------------------------------------------------
    def _get(self, url, headers=None, limit=MAX_FEED_BYTES, to_file=None):
        req = urllib.request.Request(url, headers=dict({"User-Agent": "oaap-package-catalog"},
                                                       **(headers or {})))
        try:
            r = self.opener.open(req, timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                return 304, None, {}
            raise UpstreamError(f"{url.replace(self.base, '<upstream>')} antwortet {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise UpstreamError(f"Upstream nicht erreichbar ({type(e).__name__})")
        with r:
            if r.status in (301, 302, 303, 307, 308):
                raise UpstreamError("Upstream leitet weiter -- dem folgt der Katalog nicht.")
            hdrs = dict(r.headers)
            if to_file:
                h, n = hashlib.sha256(), 0
                with open(to_file, "wb") as f:
                    for block in iter(lambda: r.read(1 << 20), b""):
                        n += len(block)
                        if n > limit:
                            raise UpstreamError("Paket größer als erlaubt.")
                        h.update(block)
                        f.write(block)
                return r.status, (h.hexdigest(), n), hdrs
            data = r.read(limit + 1)
            if len(data) > limit:
                raise UpstreamError("Feed zu groß.")
            return r.status, data, hdrs

    def poll(self):
        """Einmal abrufen. Gibt den Zustand zurück; wirft nie (Fehler stehen im Zustand)."""
        if not self.enabled:
            return self.status()
        with self.lock:
            st = self.status()
            st.update({"channel": self.channel, "mode": self.mode, "last_poll": _now()})
            try:
                taken = self._poll(st)
                st["last_error"] = ""
                st["last_ok"] = _now()
                if taken:
                    st["taken"] = (taken + st.get("taken", []))[:20]
            except UpstreamError as e:
                st["last_error"] = str(e)[:300]
            self.cat._save()
            return st

    def _poll(self, st):
        feed_url = f"{self.base}{FEED_PATH}?channel={self.channel}"
        headers = {"If-None-Match": st["etag"]} if st.get("etag") else {}
        code, raw, hdrs = self._get(feed_url, headers)
        if code == 304:
            return []
        try:
            doc = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise UpstreamError("Der Feed ist kein JSON.")
        if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
            raise UpstreamError(f"Unbekanntes Feed-Format (erwartet {SCHEMA}).")
        st["feed_name"] = str(doc.get("name") or "")[:60]
        taken = []
        for app in doc.get("apps") or []:
            taken += self._take(app, st)
        for w in doc.get("withdrawn") or []:
            self._withdraw(w)
        st["etag"] = hdrs.get("ETag") or hdrs.get("Etag") or ""
        return taken

    def _take(self, app, st):
        if not isinstance(app, dict):
            return []
        aid, version = str(app.get("id") or ""), str(app.get("version") or "")
        pkg = app.get("package") or {}
        m = PKG_URL_RE.match(str(pkg.get("url") or ""))
        if not m or m.group(1) != pkg.get("sha256"):
            raise UpstreamError(f"{aid} {version}: der Paketverweis ist kein files/<sha256> "
                                "des Feeds -- der Katalog lädt nur über die Destination.")
        if self.cat.get(aid, version):
            return []                          # nie überschreiben, auch nicht vom Spiegel
        try:
            size = int(pkg.get("size"))
        except (TypeError, ValueError):
            raise UpstreamError(f"{aid} {version}: Größe fehlt im Feed.")
        if size > self.max_bytes:
            raise UpstreamError(f"{aid} {version}: {size // 1048576} MB, erlaubt sind "
                                f"{self.max_bytes // 1048576} MB.")
        tmp = os.path.join(self.cat.tmp_dir(), f"upstream-{pkg['sha256'][:16]}.zip")
        try:
            _code, (sha, n), _h = self._get(f"{self.base}/feed/v1/{pkg['url']}",
                                            limit=self.max_bytes, to_file=tmp)
            if n != size or sha != pkg["sha256"]:
                raise UpstreamError(f"{aid} {version}: Größe oder Prüfsumme weicht vom Feed ab.")
            # Was der Feed behauptet, muss das Manifest IM Paket sagen -- vor der
            # Aufnahme, sonst landete ein fremdes Paket unter seinem eigenen Namen.
            inner = zipinfo.read(tmp, self.max_bytes)["app"]
            if (inner["id"], inner["version"]) != (aid, version):
                raise UpstreamError(f"{aid} {version}: das Paket ist "
                                    f"{inner['id']} {inner['version']} -- nicht übernommen.")
            self.cat.add(tmp, self.max_bytes, "upstream",
                         f"aus {st.get('feed_name') or 'Upstream'}, Kanal {self.channel}")
        except Exception as e:  # noqa: BLE001 -- PackageError/CatalogError als Satz
            if isinstance(e, UpstreamError):
                raise
            raise UpstreamError(f"{aid} {version}: {e}")
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if self.mode == "auto":
            self.cat.set_released(aid, version, True, "upstream")
        return [f"{aid} {version}"]

    def _withdraw(self, w):
        if not isinstance(w, dict):
            return
        aid, version = str(w.get("id") or ""), str(w.get("version") or "")
        reason = str(w.get("reason") or "")[:300]
        r = self.cat.get(aid, version)
        if not r or r.get("withdrawn_upstream"):
            return
        r["withdrawn_upstream"] = reason or "ohne Grund"
        if self.mode == "auto" and r.get("released"):
            self.cat.set_released(aid, version, False, "upstream",
                                  reason="im Marketplace zurückgerufen: " + (reason or "ohne Grund"))

    # -- the loop ---------------------------------------------------------------
    def start(self):
        if not self.enabled:
            return

        def loop():
            time.sleep(5)
            while True:
                self.poll()
                time.sleep(self.interval * 60)
        threading.Thread(target=loop, daemon=True, name="upstream").start()
