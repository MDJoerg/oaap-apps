#!/usr/bin/env python3
"""Spiegelmodus Ende zu Ende im Kleinen (RFC-0077 §7.2, M7/M9/M11).

Ein ECHTER Marketplace-Server (../marketplace) im Prozess, ein echter
Katalog. Dazwischen steht, was auf dem Knoten Destination und Gateway
tun: Die Anfrage des Katalogs bekommt die Identität des Maschinenbenutzers
`node-b`. Das spielt hier ein Opener nach, der die Kopfzeilen setzt.

Aufruf: python3 test_upstream.py
"""
import hashlib
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import urllib.request
import urllib.response
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MARKET = os.path.join(HERE, "..", "marketplace")
fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


# --- der Marketplace (eigene Module, eigener Namensraum) ---
sys.path.insert(0, MARKET)
mk = importlib.import_module("app")
for n in ("app", "db", "access", "blobs", "zipinfo", "multipart"):
    sys.modules.pop(n, None)
sys.path.remove(MARKET)
msrv = mk.make_server(tempfile.mkdtemp(prefix="up-market-"), 0)
threading.Thread(target=msrv.serve_forever, daemon=True).start()
MBASE = f"http://127.0.0.1:{msrv.server_address[1]}"

# --- der Katalog ---
sys.path.insert(0, HERE)
import app as cat_app                                          # noqa: E402

ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "server_admin,admin,user"}
NODE = {"X-OAAP-User": "node-b", "X-OAAP-Roles": "user"}


class AsNode(urllib.request.BaseHandler):
    """Was Destination + Gateway leisten: die Anfrage trägt node-b."""
    handler_order = 100

    def http_request(self, req):
        for k, v in NODE.items():
            req.add_unredirected_header(k, v)
        return req


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def catalog(mode="auto"):
    env = {"OAAP_DESTINATION_UPSTREAM_URL": MBASE, "CATALOG_UPSTREAM_CHANNEL": "stable",
           "CATALOG_UPSTREAM_RELEASE": mode}
    cat_app.make_server(tempfile.mkdtemp(prefix="up-cat-"), 0, env,
                        urllib.request.build_opener(AsNode(), NoRedirect()))
    return cat_app.CATALOG, cat_app.MIRROR


def zip_bytes(app_id, version, note=""):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("oaap-app.yaml", f'oaap_manifest: "0.10"\napp:\n  id: {app_id}\n'
                                    f'  name: Probe\n  version: "{version}"\n  type: native\n')
        z.writestr("Dockerfile", "FROM scratch\n# " + note)
    return buf.getvalue()


def market(method, path, headers, body=None, ctype=None):
    h = dict(headers)
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(MBASE + path, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def publish(data, channels):
    bd = "----t" + uuid.uuid4().hex
    body = (f'--{bd}\r\nContent-Disposition: form-data; name="channels"\r\n\r\n{channels}\r\n'
            f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="p.zip"\r\n'
            'Content-Type: application/zip\r\n\r\n').encode() + data + f"\r\n--{bd}--\r\n".encode()
    return market("POST", "/api/v1/releases", ADMIN, body, f"multipart/form-data; boundary={bd}")


def grant(asset):
    from urllib.parse import urlencode
    return market("POST", "/admin/grant", ADMIN,
                  urlencode({"asset": asset, "kind": "user", "name": "node-b",
                             "channels": "stable"}).encode(), "application/x-www-form-urlencoded")


print("M7 Spiegel holt, prüft, nimmt auf, gibt frei (auto)")
z1 = zip_bytes("probe-app", "0.1.0")
publish(z1, "stable")
C, MIR = catalog("auto")
st = MIR.poll()
ok("ohne Berechtigung: nichts übernommen, kein Fehler",
   not C.state["apps"] and not st.get("last_error"), st)
grant("probe-app")
st = MIR.poll()
r = C.get("probe-app", "0.1.0")
ok("M7 0.1.0 liegt im Katalog", r is not None, (st, C.state["apps"]))
ok("M7 Prüfsumme wie hochgeladen", r and r["sha256"] == hashlib.sha256(z1).hexdigest())
ok("M7 freigegeben (auto), von upstream", r and r["released"] and r["released_by"] == "upstream", r)
lst = json.load(open(os.path.join(C.dir, "store.json"), encoding="utf-8"))
ok("M7 store.json listet sie", [(a["id"], a["version"]) for a in lst["apps"]] == [("probe-app", "0.1.0")], lst)
ok("der Zustand nennt Feed und Übernahme", st.get("feed_name") and "probe-app 0.1.0" in st.get("taken", []), st)
st = MIR.poll()
ok("zweiter Abruf ohne Änderung: 304, nichts doppelt", not st.get("last_error")
   and len(C.state["apps"]["probe-app"]["versions"]) == 1, st)

print("\nM8/M9 Neue Version, dann Rückruf")
z2 = zip_bytes("probe-app", "0.1.1")
publish(z2, "stable")
MIR.poll()
ok("M8 0.1.1 übernommen und freigegeben", (C.get("probe-app", "0.1.1") or {}).get("released"))
market("POST", "/api/v1/assets/probe-app/releases/0.1.1/yank", ADMIN,
       json.dumps({"reason": "Fehler beim Anmelden"}).encode(), "application/json")
MIR.poll()
r = C.get("probe-app", "0.1.1")
ok("M9 0.1.1 lokal zurückgezogen (nicht gelöscht)", r is not None and not r["released"], r)
ok("M9 mit Grund im Protokoll",
   any(a["action"] == "withdraw" and "Fehler beim Anmelden" in a.get("reason", "")
       for a in C.state["audit"]), C.state["audit"][-3:])
lst = json.load(open(os.path.join(C.dir, "store.json"), encoding="utf-8"))
ok("M9 store.json bietet wieder 0.1.0", lst["apps"][0]["version"] == "0.1.0", lst)

print("\nM11 hold")
C2, MIR2 = catalog("hold")
MIR2.poll()
r = C2.get("probe-app", "0.1.0")
ok("M11 die Version kommt an, aber nicht freigegeben", r and not r["released"], r)
ok("M11 store.json bleibt leer", json.load(open(os.path.join(C2.dir, "store.json")))["apps"] == [])

print("\nWas der Spiegel ablehnt")


LIE = {"doc": {}, "files": {}}


class LyingUpstream(__import__("http.server").server.BaseHTTPRequestHandler):
    """Ein Upstream, der lügt -- ein echter kleiner Server."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/feed/v1/oaap-apps"):
            data = json.dumps(LIE["doc"]).encode()
        else:
            data = LIE["files"].get(self.path.rsplit("/", 1)[-1], b"")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


import http.server  # noqa: E402
lsrv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LyingUpstream)
threading.Thread(target=lsrv.serve_forever, daemon=True).start()


def lying(doc, files):
    LIE["doc"], LIE["files"] = doc, files
    env = {"OAAP_DESTINATION_UPSTREAM_URL": f"http://127.0.0.1:{lsrv.server_address[1]}"}
    cat_app.make_server(tempfile.mkdtemp(prefix="up-lie-"), 0, env)
    return cat_app.CATALOG, cat_app.MIRROR


other = zip_bytes("anderes-paket", "9.9.9")
sha_o = hashlib.sha256(other).hexdigest()
feed = {"schema": "oaap.marketplace.feed/0.1", "name": "L", "apps": [
    {"id": "probe-app", "version": "0.2.0",
     "package": {"url": "files/" + sha_o, "sha256": sha_o, "size": len(other)}}]}
C3, MIR3 = lying(feed, {sha_o: other})
st = MIR3.poll()
ok("ein Paket, das etwas anderes enthält als der Feed sagt: nicht übernommen",
   not C3.state["apps"] and "nicht übernommen" in st.get("last_error", ""), (st, C3.state["apps"]))
feed["apps"][0]["package"]["url"] = "https://evil.example/x.zip"
C4, MIR4 = lying(feed, {})
st = MIR4.poll()
ok("ein absoluter Paketverweis (an der Destination vorbei): abgelehnt",
   not C4.state["apps"] and "Destination" in st.get("last_error", ""), st)
z = zip_bytes("probe-app", "0.3.0")
feed["apps"] = [{"id": "probe-app", "version": "0.3.0",
                 "package": {"url": "files/" + "0" * 64, "sha256": "0" * 64, "size": len(z)}}]
C5, MIR5 = lying(feed, {"0" * 64: z})
st = MIR5.poll()
ok("falsche Prüfsumme: abgelehnt", not C5.state["apps"] and "Prüfsumme" in st.get("last_error", ""), st)
C6, MIR6 = lying({"schema": "etwas/9"}, {})
ok("fremdes Feed-Format: abgelehnt", "Feed-Format" in MIR6.poll().get("last_error", ""))

print("\nOhne Destination: genau 0.1")
cat_app.make_server(tempfile.mkdtemp(prefix="up-none-"), 0, {})
ok("kein Spiegel, keine Karte", not cat_app.MIRROR.enabled and cat_app.mirror_card() == "")

docker = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()
mods = [f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_")]
ok("Dockerfile kopiert jedes Modul", all(m in docker for m in mods), mods)

msrv.shutdown()
print("\nALLE PRUEFUNGEN BESTANDEN" if not fails else f"\n{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
