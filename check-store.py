#!/usr/bin/env python3
"""Sagen Katalog und README dasselbe wie die Apps, die sie auflisten?

Die Version einer App steht in diesem Repository an DREI Stellen: im
Manifest `apps/<id>/oaap-app.yaml`, aus dem jeder Knoten wirklich
installiert; in `oaap-store.json`, das die Store-Seite anzeigt; und in
der Tabelle der README. Dieselbe Angabe dreimal laeuft auseinander,
sobald niemand sie zaehlt -- und am 22.09.2026 waren tatsaechlich
beide Abschriften zurueck, die README am weitesten.

Genau das ist am 21.09.2026 passiert: Ein Commit hob drei Apps auf die
Rolle `support` (RFC-0039) und dabei drei Manifest-Versionen an --
`oaap-store.json` fasste er nicht an. Die Folge fiel erst einen Tag
spaeter im Klicktest auf, und zwar in der unangenehmsten Form: Die
Store-Seite bot "Aktualisieren auf v0.4.2" ueber einer Instanz, die
0.4.3 lief. Eine **Rueckstufung, als Aktualisierung beschriftet** --
und dazu nannte der Katalog fuer FleetView weiter die Rolle `partner`,
die dieselbe Aenderung gerade ersetzt hatte.

Das Manifest gewinnt immer. Es ist das, woraus installiert wird; der
Katalog ist die Beschreibung davon.

Geprueft wird jede Angabe, die BEIDE Stellen fuehren -- nicht nur die
Version, denn die Rolle war der gefaehrlichere Teil des Befunds:

  version   app.version
  name      app.name
  type      app.type
  app_class app.class
  roles     die Vereinigung aller Routen-Rollen

Nicht geprueft wird `released`: Im Manifest steht kein Datum, also kann
dieses Skript keines herleiten. Es sagt aber, bei welcher App das Datum
noch die alte Version meint, sobald es eine Version nachzieht -- denn
die Store-Seite schreibt "Version 0.4.3 vom 2026-09-18", und damit
gehoert das Datum zur Zahl daneben.

Aufruf: python3 check-store.py        # meldet, was auseinanderlaeuft
        python3 check-store.py --fix  # zieht den Katalog nach
"""
import json
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("Braucht PyYAML (pip install pyyaml)")
    sys.exit(2)

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.join(HERE, "oaap-store.json")
FIX = "--fix" in sys.argv


def manifest_roles(m):
    """Was das Manifest ueber Rollen sagt: die Vereinigung ALLER Routen.

    Zuerst stand hier "nimm die Wurzelroute, das ist es, was jemand
    beim Aufruf trifft". Das war falsch, und zwar auf die teure Art:
    `ai-gateway` hat `/v1` fuer `public` und `/` fuer `admin`, und der
    Katalog nennt richtigerweise beide. Eine Wurzelroute-Regel haette
    das als Abweichung gemeldet und mit `--fix` dem oeffentlichen
    KI-Endpunkt seine Oeffentlichkeit aus dem Katalog gestrichen.

    Eine Pruefung, die faelschlich anschlaegt, ist schlimmer als keine,
    wenn sie eine Reparatur anbietet.

    Der Katalog beantwortet "wer kann diese App ueberhaupt benutzen",
    nicht "welcher Pfad verlangt was" -- also die Vereinigung.
    """
    seen, out = set(), []
    for r in (m.get("routes") or []):
        for role in (r.get("roles") or []):
            if role not in seen:
                seen.add(role)
                out.append(role)
    return out


# Katalogfeld -> wie es aus dem Manifest kommt.
FIELDS = {
    "version": lambda m: (m.get("app") or {}).get("version"),
    "name": lambda m: (m.get("app") or {}).get("name"),
    "type": lambda m: (m.get("app") or {}).get("type"),
    "app_class": lambda m: (m.get("app") or {}).get("class"),
    "roles": manifest_roles,
}

with open(STORE, encoding="utf-8") as f:
    store = json.load(f)

problems = []
fixed = []
dates_todo = []

for entry in store.get("apps") or []:
    app_id = entry.get("id") or "?"
    path = os.path.join(HERE, "apps", app_id, "oaap-app.yaml")
    if not os.path.exists(path):
        # Kein Fehler: der Katalog darf auf Apps zeigen, die woanders
        # liegen. Nur pruefen laesst sich das hier nicht.
        print(f"--  {app_id}: kein Manifest in diesem Repo, nicht geprueft")
        continue
    with open(path, encoding="utf-8") as f:
        man = yaml.safe_load(f) or {}

    for field, read in FIELDS.items():
        want = read(man)
        if want is None:
            continue
        have = entry.get(field)
        # Rollen sind eine Menge, keine Reihenfolge: `[admin, public]`
        # und `[public, admin]` sagen dasselbe, und ein Unterschied
        # daran waere Rauschen, das die echten Befunde zudeckt.
        if field == "roles":
            if set(have or []) == set(want or []):
                continue
        elif have == want:
            continue
        problems.append(f"{app_id}.{field}: Katalog {have!r} — Manifest {want!r}")
        if FIX:
            entry[field] = want
            fixed.append(f"{app_id}.{field} -> {want!r}")
            if field == "version":
                # `released` gehoert zu DIESER Zahl: die Store-Seite
                # schreibt "Version 0.4.3 vom 2026-09-18". Ein Datum
                # kann dieses Skript nicht erfinden -- im Manifest
                # steht keines --, also sagt es, was noch fehlt,
                # statt eine falsche Angabe stehen zu lassen.
                dates_todo.append(
                    f"{app_id}: 'released' steht auf {entry.get('released')!r} "
                    f"und meint noch die alte Version")

# --- dritte Stelle: die Tabelle in der README -------------------------
# Beim Reparieren des Katalogs aufgefallen: Die Version steht nicht an
# zwei Stellen, sondern an DREI, und die README war die am weitesten
# zurueckliegende (LiveKit 0.1.0 statt 0.1.2). Eine Tabelle, die
# Versionen wiederholt, laeuft immer auseinander -- also zaehlt sie
# hier mit, statt sich darauf zu verlassen, dass jemand daran denkt.
README = os.path.join(HERE, "README.md")
ROW = re.compile(r"^\|\s*\[[^\]]+\]\(apps/([a-z0-9-]+)/\)\s*\|.*\|\s*([0-9][^|]*?)\s*\|\s*$")

readme_lines = []
if os.path.exists(README):
    with open(README, encoding="utf-8", newline="") as f:
        readme_lines = f.read().split("\n")

readme_changed = False
for n, line in enumerate(readme_lines):
    m = ROW.match(line)
    if not m:
        continue
    app_id, have = m.group(1), m.group(2)
    path = os.path.join(HERE, "apps", app_id, "oaap-app.yaml")
    if not os.path.exists(path):
        continue
    with open(path, encoding="utf-8") as f:
        want = str(((yaml.safe_load(f) or {}).get("app") or {}).get("version"))
    if have == want:
        continue
    problems.append(f"README {app_id}: Tabelle {have!r} — Manifest {want!r}")
    if FIX:
        # Nur die Zahl ersetzen, die Spaltenbreite bleibt wie sie ist:
        # eine neu ausgerichtete Tabelle waere ein Unterschied, in dem
        # die Aenderung nicht mehr zu sehen ist.
        start = line.rfind(have)
        readme_lines[n] = line[:start] + want + line[start + len(have):]
        readme_changed = True
        fixed.append(f"README {app_id} -> {want}")

if not problems:
    print("Katalog, README und Manifeste stimmen ueberein.")
    sys.exit(0)

print(f"{len(problems)} Abweichung(en) — das Manifest gilt:")
for p in problems:
    print(f"  {p}")

if not FIX:
    print("\nNachziehen mit: python3 check-store.py --fix")
    sys.exit(1)

# Mit derselben Formatierung zurueckschreiben, damit der Unterschied im
# Commit die Aenderung ist und nicht die Einrueckung.
with open(STORE, "w", encoding="utf-8", newline="\n") as f:
    json.dump(store, f, indent=2, ensure_ascii=False)
    f.write("\n")
if readme_changed:
    with open(README, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(readme_lines))
print(f"\n{len(fixed)} Angabe(n) nachgezogen.")
if dates_todo:
    print("\nVON HAND NACHZUTRAGEN — das Datum gehoert zur Version:")
    for d in dates_todo:
        print(f"  {d}")
sys.exit(0)
