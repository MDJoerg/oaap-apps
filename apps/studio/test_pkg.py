#!/usr/bin/env python3
"""Was das Studio über ein Paket sagt, bevor es jemand hochlädt.

Läuft ohne Netz, ohne Docker, ohne Knoten: Die Pakete entstehen hier im
Speicher. Festgehalten werden Entscheidungen aus RFC-0019 (§3 Rahmen,
§5 Entpacken) und die Manifest-Regeln aus dem veröffentlichten Schema —
wer eine davon ändern will, ändert zuerst den RFC.

    python3 test_pkg.py
"""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pkg  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail}")


MANIFEST = """oaap_manifest: "0.2"

app:
  id: bdt-app
  name: BDT App
  version: 0.305.0
  type: native
  class: frontend

services:
  web:
    build: .
    port: 8000

routes:
  - path: /
    roles: [keyuser, admin]

storage:
  - name: data
    mount: /data

config:
  - key: BDT_MODE
    label: "Betriebsart"
    default: "normal"

health:
  path: /healthz
"""


def make_zip(entries, path=None):
    """Ein Paket bauen. `entries` ist eine Liste (name, inhalt-oder-None)."""
    fd, p = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(p, "w") as z:
        for name, content in entries:
            z.writestr(name, content if content is not None else "x")
    return p


def make_zip_with_symlink(target_name="link"):
    fd, p = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("oaap-app.yaml", MANIFEST)
        info = zipfile.ZipInfo(target_name)
        info.external_attr = (0xA1FF) << 16          # Symlink, 0777
        z.writestr(info, "/etc/passwd")
    return p


MB = 1024 * 1024


def inspect(path, previous=None, limit=64 * MB):
    return pkg.inspect(path, limit, previous)


print("=== Paketwurzel: Wurzel oder EIN Oberordner (wie beim Knoten) ===")
ok("Manifest in der Archivwurzel",
   pkg.package_root(["oaap-app.yaml", "app.py"]) == "")
ok("Manifest in einem einzelnen Oberordner",
   pkg.package_root(["projekt/oaap-app.yaml", "projekt/app.py"]) == "projekt/")
ok("zwei Oberordner sind kein Paket",
   pkg.package_root(["a/oaap-app.yaml", "b/app.py"]) is None)
ok("kein Manifest, keine Wurzel",
   pkg.package_root(["projekt/app.py"]) is None)

print("\n=== Ein gutes Paket ===")
good = make_zip([("oaap-app.yaml", MANIFEST), ("app.py", "print(1)")])
r = inspect(good)
ok("Prüfsumme und Größe stehen fest",
   len(r["sha256"]) == 64 and r["bytes"] == os.path.getsize(good))
ok("keine Beanstandung", not r["findings"], str(r["findings"]))
ok("bereit zum Ausrollen", r["deployable"])
ok("Zusammenfassung nennt App, Version und Typ",
   (r["summary"]["id"], r["summary"]["version"], r["summary"]["type"])
   == ("bdt-app", "0.305.0", "native"))
ok("das Manifest wird zeichengleich weitergereicht",
   r["manifest_text"] == MANIFEST)

print("\n=== Ein Paket im Oberordner ===")
nested = make_zip([("bdt-app/oaap-app.yaml", MANIFEST), ("bdt-app/app.py", "x")])
r2 = inspect(nested)
ok("„Projektordner zippen“ ist erlaubt und wird erkannt", r2["root"] == "bdt-app/")

print("\n=== Was der Knoten beim Entpacken ablehnt (RFC-0019 §5) ===")
absolute = make_zip([("oaap-app.yaml", MANIFEST), ("/etc/cron.d/x", "böse")])
r3 = inspect(absolute)
ok("absoluter Pfad ist ein Fehler",
   any(f["level"] == pkg.FEHLER and "absolute" in f["text"] for f in r3["findings"]))
ok("und damit nicht ausrollbar", not r3["deployable"])

travers = make_zip([("oaap-app.yaml", MANIFEST), ("../../etc/x", "böse")])
r4 = inspect(travers)
ok("Ausbruch mit „..“ ist ein Fehler",
   any(f["level"] == pkg.FEHLER and ".." in f["text"] for f in r4["findings"]))

link = make_zip_with_symlink()
r5 = inspect(link)
ok("Symlink ist ein Fehler",
   any(f["level"] == pkg.FEHLER and "Verknüpfungen" in f["text"]
       for f in r5["findings"]))

print("\n=== Was gar kein Paket ist ===")
fd, plain = tempfile.mkstemp(suffix=".zip")
os.write(fd, b"ich bin kein zip")
os.close(fd)
try:
    inspect(plain)
    ok("Nicht-ZIP wird abgewiesen", False)
except pkg.PackageError as e:
    ok("Nicht-ZIP wird abgewiesen", "kein ZIP-Archiv" in str(e))

nomani = make_zip([("app.py", "x"), ("README.md", "y")])
try:
    inspect(nomani)
    ok("Paket ohne Manifest wird abgewiesen", False)
except pkg.PackageError as e:
    ok("Paket ohne Manifest wird abgewiesen", "oaap-app.yaml" in str(e))

try:
    inspect(good, limit=100)
    ok("zu großes Paket wird abgewiesen", False)
except pkg.PackageError as e:
    ok("zu großes Paket wird abgewiesen", "erlaubt sind" in str(e))

broken = make_zip([("oaap-app.yaml", "app: [unbalanced\n  - x: {")])
try:
    inspect(broken)
    ok("kaputtes YAML wird abgewiesen", False)
except pkg.PackageError as e:
    ok("kaputtes YAML wird abgewiesen", "YAML" in str(e))

print("\n=== Manifest-Regeln ===")


def levels(text):
    return [(f["level"], f["text"]) for f in pkg.validate(pkg.parse_manifest(text))]


def has_error(text, needle):
    return any(lv == pkg.FEHLER and needle in t for lv, t in levels(text))


ok("Pflichtfeld fehlt",
   has_error(MANIFEST.replace('oaap_manifest: "0.2"', ""), "oaap_manifest"))
ok("App-Kennung mit Großbuchstaben",
   has_error(MANIFEST.replace("id: bdt-app", "id: BDT-App"), "app.id"))
ok("Version ohne Patch-Stelle",
   has_error(MANIFEST.replace("version: 0.305.0", "version: \"0.305\""),
             "app.version"))
ok("unbekannter App-Typ",
   has_error(MANIFEST.replace("type: native", "type: docker"), "app.type"))
ok("Dienst ohne Port",
   has_error(MANIFEST.replace("    port: 8000", "    x: 1"), "port"))
ok("Dienst mit build UND image",
   has_error(MANIFEST.replace("    build: .", "    build: .\n    image: nginx"),
             "genau eines"))
ok("unbekannte Rolle in einer Route",
   has_error(MANIFEST.replace("roles: [keyuser, admin]", "roles: [chef]"),
             "unbekannte Rollen"))
ok("Route ohne führenden Schrägstrich",
   has_error(MANIFEST.replace("  - path: /\n", "  - path: portal\n"), "path"))
ok("Gesundheitspfad fehlt",
   has_error(MANIFEST.replace("  path: /healthz", "  grace: 1"), "health.path"))
ok("fester Port außerhalb 8200–8299 (RFC-0017 §5.1)",
   has_error(MANIFEST + "\nendpoints:\n  - name: media\n    protocol: both\n"
                        "    container_port: 9999\n    reason: Medien\n"
                        "    fixed: true\n", "8200"))
ok("class in einem 0.1-Manifest ist ein Befund",
   any(lv == pkg.BEFUND and "app.class" in t
       for lv, t in levels(MANIFEST.replace('"0.2"', '"0.1"'))))
ok("unbekanntes Feld ist nur ein Hinweis",
   any(lv == pkg.HINWEIS and "Unbekannte Felder" in t
       for lv, t in levels(MANIFEST + "\nfarbe: blau\n")))
ok("must_understand wird als Befund gemeldet",
   any(lv == pkg.BEFUND and "must_understand" in t
       for lv, t in levels(MANIFEST + "\nmust_understand: [gpu]\n")))
ok("Geheimnis mit Vorgabewert ist ein Befund",
   any(lv == pkg.BEFUND and "Geheimnis" in t
       for lv, t in levels(MANIFEST.replace(
           '    default: "normal"', '    secret: true\n    default: "geheim"'))))
ok("ein gültiges Manifest bleibt unbeanstandet", not levels(MANIFEST))

print("\n=== Rahmen-Vorschau (RFC-0019 §3) ===")
base = pkg.summary(pkg.parse_manifest(MANIFEST))

same = pkg.summary(pkg.parse_manifest(MANIFEST))
hart, best = pkg.envelope_preview(base, same)
ok("unveränderte Version wird hart abgelehnt",
   any("unverändert" in h for h in hart), str(hart))

nxt = pkg.summary(pkg.parse_manifest(MANIFEST.replace("0.305.0", "0.305.1")))
hart, best = pkg.envelope_preview(base, nxt)
ok("neue Version läuft durch", not hart and not best)

other = pkg.summary(pkg.parse_manifest(
    MANIFEST.replace("id: bdt-app", "id: andere-app").replace("0.305.0", "0.305.1")))
hart, best = pkg.envelope_preview(base, other)
ok("andere App-Kennung wird hart abgelehnt",
   any("gehört zu genau" in h for h in hart), str(hart))

public = pkg.summary(pkg.parse_manifest(
    MANIFEST.replace("0.305.0", "0.306.0")
            .replace("roles: [keyuser, admin]", "roles: [public]")))
hart, best = pkg.envelope_preview(base, public)
ok("neu ohne Anmeldung erreichbar braucht eine Bestätigung",
   not hart and any("Ohne Anmeldung" in b for b in best), str(best))

mount = pkg.summary(pkg.parse_manifest(
    MANIFEST.replace("0.305.0", "0.306.0")
            .replace("  - name: data\n    mount: /data",
                     "  - name: data\n    mount: /data\n"
                     "  - name: bilder\n    mount: /bilder")))
hart, best = pkg.envelope_preview(base, mount)
ok("neuer Speicher braucht eine Bestätigung",
   any("Speicher" in b for b in best), str(best))

endpoint = pkg.summary(pkg.parse_manifest(
    MANIFEST.replace("0.305.0", "0.306.0")
    + "\nendpoints:\n  - name: media\n    protocol: both\n"
      "    container_port: 8280\n    reason: Medien\n    fixed: true\n"))
hart, best = pkg.envelope_preview(base, endpoint)
ok("neuer Endpunkt am Gateway vorbei braucht eine Bestätigung",
   any("Endpunkte" in b for b in best), str(best))

ok("ohne Vorgänger gibt es nichts zu vergleichen",
   pkg.envelope_preview(None, base) == ([], []))

print("\n=== Vorschau bleibt Vorschau ===")
r6 = inspect(good, previous=base)
ok("gleiche Version: das Studio hält es zurück, bevor der Knoten es tut",
   not r6["deployable"] and r6["envelope_hard"])

for f in (good, nested, absolute, travers, link, plain, nomani, broken):
    try:
        os.remove(f)
    except OSError:
        pass

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
