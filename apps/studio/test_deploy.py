#!/usr/bin/env python3
"""Upload-Leser und Drei-Phasen-Weg — ohne Netz, ohne Knoten.

Zwei Dinge werden hier festgehalten:

1. Der Upload-Leser (`multipart`) kommt mit einem Paket zurecht, in dem
   die Trennmarke zufällig als Bytefolge vorkommt, und bricht bei allem
   ab, was über die Grenze geht.
2. Der Drei-Phasen-Weg (`deployer`, RFC-0019 §2) läuft in der richtigen
   Reihenfolge, bricht bei einer Ablehnung ab — und **der Token taucht
   nirgends in einem Ergebnis auf**.

    python3 test_deploy.py
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import deployer  # noqa: E402
import multipart  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail}")


BOUNDARY = "----StudioTest7f3a"


def body(parts):
    """parts: Liste (name, dateiname oder None, bytes)."""
    out = b""
    for name, filename, content in parts:
        out += f"--{BOUNDARY}\r\n".encode()
        disp = f'form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
            out += f"Content-Disposition: {disp}\r\n".encode()
            out += b"Content-Type: application/zip\r\n\r\n"
        else:
            out += f"Content-Disposition: {disp}\r\n\r\n".encode()
        out += content
        out += b"\r\n"
    out += f"--{BOUNDARY}--\r\n".encode()
    return out


CT = f"multipart/form-data; boundary={BOUNDARY}"


def parse(raw, limit=10 << 20):
    return multipart.parse(io.BytesIO(raw), CT, len(raw), limit,
                           tempfile.gettempdir())


print("=== Upload lesen ===")
raw = body([("action", None, b"deployen"),
            ("token", None, "geheim-ähh".encode()),
            ("paket", "app.zip", b"PK\x03\x04binaerkram\x00\x01\x02")])
fields, files = parse(raw)
ok("Textfelder kommen an",
   fields["action"] == "deployen" and fields["token"] == "geheim-ähh", str(fields))
ok("die Datei landet auf der Platte, nicht im Speicher",
   os.path.exists(files["paket"]["path"]))
ok("Dateiname und Größe stimmen",
   files["paket"]["filename"] == "app.zip" and files["paket"]["bytes"] == 17,
   str(files["paket"]))
with open(files["paket"]["path"], "rb") as f:
    ok("Inhalt ist byteweise unverändert",
       f.read() == b"PK\x03\x04binaerkram\x00\x01\x02")
multipart.cleanup(files)
ok("Aufräumen entfernt die temporäre Datei",
   not os.path.exists(files["paket"]["path"]))

blob = (b"A" * 300000 + f"--{BOUNDARY}".encode() + b"B" * 300000)
raw = body([("paket", "gross.zip", blob)])
fields, files = parse(raw)
with open(files["paket"]["path"], "rb") as f:
    got = f.read()
ok("die Trennmarke IM Inhalt zerschneidet die Datei nicht",
   got == blob, f"{len(got)} statt {len(blob)} Byte")
multipart.cleanup(files)

raw = body([("paket", "x.zip", b"x" * 1000)])
try:
    parse(raw, limit=100)
    ok("über der Grenze wird abgebrochen", False)
except multipart.MultipartError as e:
    ok("über der Grenze wird abgebrochen", "Größe" in str(e))

try:
    multipart.parse(io.BytesIO(b"nix"), "application/json", 3, 1000)
    ok("ohne Trennmarke keine Verarbeitung", False)
except multipart.MultipartError as e:
    ok("ohne Trennmarke keine Verarbeitung", "boundary" in str(e))

cut = body([("paket", "x.zip", b"x" * 50)])[:-30]
try:
    multipart.parse(io.BytesIO(cut), CT, len(cut), 1 << 20, tempfile.gettempdir())
    ok("abgebrochener Upload wird abgelehnt", False)
except multipart.MultipartError as e:
    ok("abgebrochener Upload wird abgelehnt", "abgebrochen" in str(e))

print("\n=== Adressen ===")
u = deployer.hook_urls("https://knoten.example/deploy/app-test/")
ok("aus der Hook-Adresse werden die drei Adressen",
   u["announce"].endswith("/deploy/app-test/announce")
   and u["artifact"].endswith("/deploy/app-test/artifact")
   and u["status"].endswith("/deploy/app-test/status"), str(u))
for bad, why in (("", "leer"), ("knoten.example/deploy/x", "ohne Schema")):
    try:
        deployer.hook_urls(bad)
        ok(f"Hook-Adresse {why} wird abgelehnt", False)
    except deployer.DeployError:
        ok(f"Hook-Adresse {why} wird abgelehnt", True)

print("\n=== Drei Phasen ===")
fd, zip_path = tempfile.mkstemp(suffix=".zip")
os.write(fd, b"PK" + b"z" * 998)
os.close(fd)
SIZE = os.path.getsize(zip_path)
TOKEN = "deploy-token-geheim"


class Fake:
    """Ein Knoten aus Papier — merkt sich, was ankam."""

    def __init__(self, announce=(200, None), upload=(200, None)):
        self.calls = []
        self.announce = announce
        self.upload = upload

    def __call__(self, method, url, headers, data, timeout, length=None):
        payload = data
        if hasattr(data, "read"):
            payload = data.read()
        self.calls.append({"method": method, "url": url, "headers": dict(headers),
                           "body": payload, "length": length})
        if url.endswith("/announce"):
            st, res = self.announce
            return st, (res if res is not None else
                        {"ok": True, "upload_token": "einmal-token",
                         "upload_url": "https://knoten.example/deploy/app-test/artifact",
                         "expires_in": 900})
        st, res = self.upload
        return st, (res if res is not None else
                    {"ok": True, "version": "0.305.1", "revision": "abc123",
                     "message": "deployed", "url": "https://app-test.knoten.example/"})


fake = Fake()
out = deployer.deploy("https://knoten.example/deploy/app-test", TOKEN,
                      "oaap_manifest: \"0.2\"\n", "a" * 64, zip_path,
                      "0.305.1", 30, fake)
ok("beide Phasen gelaufen, in dieser Reihenfolge",
   [c["url"].rsplit("/", 1)[-1] for c in fake.calls] == ["announce", "artifact"],
   str([c["url"] for c in fake.calls]))
ann = json.loads(fake.calls[0]["body"])
ok("Phase 1 meldet Manifest, Prüfsumme und echte Größe an",
   ann["manifest"] == "oaap_manifest: \"0.2\"\n" and ann["artifact_sha256"] == "a" * 64
   and ann["artifact_bytes"] == SIZE, str(ann)[:200])
ok("Phase 1 trägt den Deploy-Token",
   fake.calls[0]["headers"]["Authorization"] == f"Bearer {TOKEN}")
ok("Phase 2 trägt NUR das Einmal-Token, nie den Deploy-Token",
   fake.calls[1]["headers"]["Authorization"] == "Bearer einmal-token")
ok("Phase 2 schickt das Paket mit Länge und ZIP-Typ",
   fake.calls[1]["length"] == SIZE
   and fake.calls[1]["headers"]["Content-Type"] == "application/zip"
   and fake.calls[1]["body"].startswith(b"PK"))
ok("kein Token in irgendeiner Adresse",
   not any(TOKEN in c["url"] or "einmal-token" in c["url"] for c in fake.calls))
ok("kein Token im Ergebnis", TOKEN not in json.dumps(out))
ok("Ergebnis ist „ausgerollt“",
   out["ok"] and out["result"]["version"] == "0.305.1")

fake = Fake(announce=(422, {"refused": "envelope_widened",
                            "details": ["route '/' becomes public"],
                            "message": "needs confirmation"}))
out = deployer.deploy("https://knoten.example/deploy/app-test", TOKEN,
                      "m", "b" * 64, zip_path, "0.4.0", 30, fake)
ok("nach einer Ablehnung wird NICHTS hochgeladen", len(fake.calls) == 1)
ok("die Ablehnung steht als Schritt da, mit Begründung des Knotens",
   not out["ok"] and out["steps"][0]["status"] == 422
   and out["steps"][0]["details"] == ["route '/' becomes public"])
ok("und mit einem Satz, was zu tun ist",
   "server_admin" in out["steps"][0]["hint"], out["steps"][0]["hint"])

fake = Fake(announce=(403, {"error": "denied"}))
out = deployer.deploy("https://knoten.example/deploy/app-test", TOKEN, "m",
                      "c" * 64, zip_path, "0.4.0", 30, fake)
ok("403 wird als Token-Problem erklärt, ohne Auskunft über die Instanz",
   "Deploy-Token" in out["steps"][0]["hint"]
   and "nicht, ob es die Instanz gibt" in out["steps"][0]["hint"])

fake = Fake(upload=(202, {"ok": None, "message": "deployment is still running"}))
out = deployer.deploy("https://knoten.example/deploy/app-test", TOKEN, "m",
                      "d" * 64, zip_path, "0.4.0", 30, fake)
ok("202 heißt „läuft noch“ und nicht „fehlgeschlagen“",
   out["ok"] and out["pending"], str(out["steps"][-1]))

print("\n=== Wenn gar nichts antwortet ===")


def dead(*a, **k):
    raise deployer.DeployError("Der Hook war nicht erreichbar: timed out")


try:
    deployer.deploy("https://knoten.example/deploy/app-test", TOKEN, "m",
                    "e" * 64, zip_path, "0.4.0", 5, dead)
    ok("unerreichbarer Hook wird gemeldet", False)
except deployer.DeployError as e:
    ok("unerreichbarer Hook wird gemeldet", "nicht erreichbar" in str(e))

os.remove(zip_path)
print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
