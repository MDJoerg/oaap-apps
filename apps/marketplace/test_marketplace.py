#!/usr/bin/env python3
"""Marketplace Stufe 1 im Kleinen (RFC-0077 §15.4).

Ein echter Server im Prozess; die Plattform spielt dieser Test über die
Identitäts-Kopfzeilen nach, wie das Gateway sie nach /verify setzt. Auf
einem öffentlichen Weg schickt der Test sie ABSICHTLICH mit -- die App
darf sie dort nicht lesen (der Köder an der richtigen Tür).

Aufruf: python3 test_marketplace.py
"""
import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import app as mk                                              # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


DATA = tempfile.mkdtemp(prefix="market-test-")
srv = mk.make_server(DATA, 0)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "server_admin,admin,user"}
CI = {"X-OAAP-User": "ci", "X-OAAP-Roles": "user"}
NODE = {"X-OAAP-User": "node-b", "X-OAAP-Roles": "user", "X-OAAP-Groups": "knoten"}
OTHER = {"X-OAAP-User": "fremd", "X-OAAP-Roles": "user"}


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedir)


def call(method, path, headers=None, body=None, ctype=None):
    h = dict(headers or {})
    if ctype:
        h["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=body, method=method, headers=h)
    try:
        with OPENER.open(req, timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def jcall(method, path, headers=None, doc=None):
    st, b, h = call(method, path, headers, json.dumps(doc).encode() if doc is not None else None,
                    "application/json" if doc is not None else None)
    try:
        return st, json.loads(b), h
    except ValueError:
        return st, b, h


def zip_bytes(app_id, version, extra=None, manifest=True):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        if manifest:
            z.writestr("oaap-app.yaml", f'oaap_manifest: "0.10"\napp:\n  id: {app_id}\n'
                                        f'  name: Probe {app_id}\n  version: "{version}"\n'
                                        '  type: native\n  description: "Eine Probe-App."\n')
        z.writestr("Dockerfile", "FROM scratch\n")
        for n, d in (extra or {}).items():
            z.writestr(n, d)
    return buf.getvalue()


def publish(headers, data, channels="", filename="p.zip", fields=None):
    import uuid
    bd = "----t" + uuid.uuid4().hex
    parts = []
    for k, v in dict({"channels": channels}, **(fields or {})).items():
        parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    st, b, h = call("POST", "/api/v1/releases", headers, b"".join(parts),
                    f"multipart/form-data; boundary={bd}")
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def form(path, fields, headers=ADMIN):
    from urllib.parse import urlencode
    return call("POST", path, headers, urlencode(fields).encode(),
                "application/x-www-form-urlencoded")


print("Rechte zum Veröffentlichen (D8)")
form("/admin/publisher", {"pattern": "probe-*", "kind": "user", "name": "ci", "channels": ""})
ok("der Betreiber erlaubt ci das Muster probe-*", len(mk.M.publishers()) == 1, mk.M.publishers())

print("\nM1/M2 Einchecken")
z1 = zip_bytes("probe-app", "0.1.0")
st, d = publish(CI, z1, "beta")
import hashlib  # noqa: E402
ok("M1 201, Prüfsumme stimmt, liegt in beta",
   st == 201 and d.get("sha256") == hashlib.sha256(z1).hexdigest() and d.get("channels") == ["beta"], (st, d))
st, d = publish(CI, z1, "beta")
ok("M2 dieselbe Datei noch einmal: 200, dasselbe Release", st == 200 and d.get("created") is False, (st, d))
st, d = publish(CI, zip_bytes("probe-app", "0.1.0", {"x.txt": "anders"}))
ok("M2 andere Bytes, gleiche Version: 409", st == 409, (st, d))

print("\nM3 Was abgelehnt wird")
for label, data in (("../x", zip_bytes("probe-bad", "0.1.0", {"../x": "x"})),
                    ("ohne Manifest", zip_bytes("probe-bad", "0.1.0", manifest=False)),
                    ("Version 1.0", zip_bytes("probe-bad", "1.0")),
                    ("kein ZIP", b"nicht zip")):
    st, d = publish(CI, data)
    ok(f"M3 {label}: abgelehnt mit Satz", st == 400 and isinstance(d, dict) and d.get("error"), (st, d))
ok("M3 und nichts gespeichert", mk.M.asset("probe-bad") is None)

print("\nM4 Ohne Recht")
st, d = publish(CI, zip_bytes("other-app", "0.1.0"))
ok("M4 ci in other-*: 403", st == 403, (st, d))
st, d = publish(OTHER, zip_bytes("probe-zwei", "0.1.0"))
ok("M4 jemand ohne Recht: 403", st == 403, (st, d))
st, d = publish({}, zip_bytes("probe-zwei", "0.1.0"))
ok("ohne Anmeldung: 401", st == 401, st)

print("\nM5 Sichtbar, gesperrt (D11)")
st, feed, _ = jcall("GET", "/feed/v1/oaap-apps?channel=beta", NODE)
ok("M5 Feed ohne Berechtigung: leer", st == 200 and feed["apps"] == [], (st, feed))
st, body, _ = call("GET", "/assets/probe-app")
ok("M5 Detailseite für alle: sichtbar, „freischalten“", st == 200 and "freischalten" in body.decode(), st)
sha = hashlib.sha256(z1).hexdigest()
ok("M5 Datei für alle: 404", call("GET", f"/d/{sha}")[0] == 404)
ok("M5 Datei im Feed ohne Berechtigung: 404", call("GET", f"/feed/v1/files/{sha}", NODE)[0] == 404)

print("\nKöder: öffentliche Wege lesen keine Kopfzeilen")
st, body, _ = call("GET", f"/d/{sha}", ADMIN)
ok("Admin-Kopfzeilen auf /d/ bringen nichts (404)", st == 404, st)
st, d, _ = jcall("GET", "/api/public/v1/assets", ADMIN)
ok("…und /api/public sieht anonym", all(a["state"] != "obtainable" for a in d["assets"]), d)

print("\nM6 Berechtigung und Kanal")
form("/admin/grant", {"asset": "probe-app", "kind": "group", "name": "knoten", "channels": "stable"})
st, feed, _ = jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)
ok("M6 berechtigt für stable, Release nur in beta: Feed leer", feed["apps"] == [], feed)
st, d, _ = jcall("POST", "/api/v1/assets/probe-app/releases/0.1.0/channels/stable", CI)
ok("ci legt 0.1.0 in stable", st == 200 and "stable" in d.get("channels", []), (st, d))
st, feed, h = jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)
ok("M6 jetzt listet der Feed 0.1.0",
   [(a["id"], a["version"]) for a in feed["apps"]] == [("probe-app", "0.1.0")], feed)
ok("Feed-Eintrag wie store.json: package.url relativ, sha256, size",
   feed["apps"][0]["package"] == {"url": "files/" + sha, "sha256": sha, "size": len(z1)}, feed)
st, body, _ = call("GET", f"/feed/v1/files/{sha}", NODE)
ok("die Datei kommt, Bytes gleich", st == 200 and body == z1, st)
etag = h.get("ETag")
st, _, _ = call("GET", "/feed/v1/oaap-apps?channel=stable", dict(NODE, **{"If-None-Match": etag}))
ok("unverändert: 304 mit ETag", st == 304, st)
ok("anderer Benutzer bleibt draußen", jcall("GET", "/feed/v1/oaap-apps?channel=stable", OTHER)[1]["apps"] == [])
ok("Empfänger steht in der Verwaltung", any(r["principal"] == "node-b" for r in mk.M.recipients()))

print("\nM8/M9 Neue Version, Rückruf")
z2 = zip_bytes("probe-app", "0.1.1")
st, d = publish(CI, z2, "stable")
st, feed, _ = jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)
ok("M8 0.1.1 ist aktuell", feed["apps"][0]["version"] == "0.1.1", feed)
st, d, _ = jcall("POST", "/api/v1/assets/probe-app/releases/0.1.1/yank", CI, {})
ok("Rückruf ohne Grund: 400", st == 400, (st, d))
st, d, _ = jcall("POST", "/api/v1/assets/probe-app/releases/0.1.1/yank", CI, {"reason": "Fehler in der Anmeldung"})
st, feed, _ = jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)
ok("M9 der Kanal fällt auf 0.1.0 zurück", feed["apps"][0]["version"] == "0.1.0", feed)
ok("M9 0.1.1 steht unter withdrawn, mit Grund",
   feed["withdrawn"] == [{"id": "probe-app", "version": "0.1.1", "reason": "Fehler in der Anmeldung"}], feed)
ok("M9 die zurückgerufene Datei kommt nicht mehr",
   call("GET", f"/feed/v1/files/{hashlib.sha256(z2).hexdigest()}", NODE)[0] == 404)
st, d = publish(CI, z2, "stable")
ok("ein zurückgerufenes Release wird nicht wieder in einen Kanal gelegt (409, mit Satz)",
   st == 409 and "zurückgerufen" in d.get("error", "")
   and jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)[1]["apps"][0]["version"] == "0.1.0", (st, d))

print("\nM12 Verborgen")
form("/admin/asset", {"asset": "probe-app", "mode": "principals", "free": ""})
st, d, _ = jcall("GET", "/api/public/v1/assets")
ok("M12 /api/public listet es nicht, die Zahl ändert sich", d["count"] == 0, d)
ok("M12 Detailseite anonym: 404", call("GET", "/assets/probe-app")[0] == 404)
ok("M12 ein Fremder angemeldet: 404", call("GET", "/my/assets/probe-app", OTHER)[0] == 404)
ok("die berechtigte Maschine sieht es weiter", call("GET", "/my/assets/probe-app", NODE)[0] == 200)
ok("…und der Feed bleibt", jcall("GET", "/feed/v1/oaap-apps?channel=stable", NODE)[1]["apps"] != [])

print("\nFrei geschaltet")
form("/admin/asset", {"asset": "probe-app", "mode": "anyone", "free": "stable"})
ok("frei in stable: anonym ladbar", call("GET", f"/d/{sha}")[0] == 200)
st, d, _ = jcall("GET", "/api/public/v1/assets/probe-app")
ok("…die Detail-API nennt Dateien nur für beziehbare Releases",
   d["state"] == "obtainable" and all("files" in r for r in d["releases"] if "stable" in r["channels"]), d)
ok("Sichtbarkeit setzt nur der Betreiber",
   jcall("PUT", "/api/v1/assets/probe-app", CI, {"free_channels": ["beta"]})[0] == 403)

print("\nTyp file")
st, d = publish(ADMIN, b"%PDF-1.4 probe", "", "handbuch.pdf",
                {"type": "file", "asset": "probe-handbuch", "version": "1.0.0"})
ok("eine PDF als Asset vom Typ file", st == 201 and d["asset"] == "probe-handbuch", (st, d))
st, d = publish(ADMIN, b"MZ", "", "tool.exe", {"type": "file", "asset": "probe-tool", "version": "1.0.0"})
ok("eine .exe wird abgelehnt", st == 400, (st, d))

print("\nVerwaltung")
ok("/admin für Nicht-Admins: 404", call("GET", "/admin", CI)[0] == 404)
ok("/admin für den Betreiber: 200", call("GET", "/admin", ADMIN)[0] == 200)
st, _, h = call("POST", "/admin/grant", dict(ADMIN, Origin="https://evil.example"), b"x=1",
                "application/x-www-form-urlencoded")
ok("ein Formular von fremder Seite: 403", st == 403, st)

print("\nDockerfile kopiert jedes Modul")
docker = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()
mods = sorted(f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_"))
ok("jedes Modul steht im COPY", all(m in docker for m in mods), mods)

srv.shutdown()
print("\nALLE PRUEFUNGEN BESTANDEN" if not fails else f"\n{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
