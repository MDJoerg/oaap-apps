#!/usr/bin/env python3
"""Tragen die Apps dieselben Kopien der gemeinsamen Module wie `lib/`?

Die Plattform baut eine App nur aus ihrem eigenen Ordner
(`docker build apps/<id>/`), ein Paket als ZIP enthält auch nur diesen
Ordner. Ein Modul, das zwei Apps teilen, muss deshalb in beiden
**liegen** — als Kopie. Die Quelle der Wahrheit ist `lib/<modul>/`.

Kopien laufen auseinander, sobald niemand sie zählt; genau das wollte
RFC-0065 Stufe 0 verhindern („ein Tresor und eine Abfrage, nicht zwei,
die sich eines Tages widersprechen“). Dieses Skript zählt.

Geprüft wird Byte für Byte, in beide Richtungen: eine Datei, die in der
Kopie fehlt, eine, die verändert ist, und eine, die nur in der Kopie
steht (jemand hat dort etwas angelegt statt in `lib/`).

Aufruf: python3 check-shared.py        # meldet Abweichungen, Ausgang 1
        python3 check-shared.py --fix  # schreibt lib/ in die Apps
"""
import filecmp
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

# Modul in lib/ → Apps, die es tragen.
SHARED = {
    "gwcore": ["ai-gateway"],
}


def files_of(folder):
    out = set()
    for base, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in names:
            if name.endswith(".pyc"):
                continue
            out.add(os.path.relpath(os.path.join(base, name), folder))
    return out


def compare(source, copy):
    """Gibt eine Liste (art, pfad) der Abweichungen zurück."""
    want = files_of(source)
    have = files_of(copy) if os.path.isdir(copy) else set()
    out = [("fehlt", p) for p in sorted(want - have)]
    out += [("nur in der Kopie", p) for p in sorted(have - want)]
    out += [("verändert", p) for p in sorted(want & have)
            if not filecmp.cmp(os.path.join(source, p), os.path.join(copy, p), shallow=False)]
    return out


def main(argv):
    fix = "--fix" in argv
    bad = 0
    for module, apps in SHARED.items():
        source = os.path.join(ROOT, "lib", module)
        for app in apps:
            copy = os.path.join(ROOT, "apps", app, module)
            diffs = compare(source, copy)
            if not diffs:
                print(f"  ok    apps/{app}/{module}")
                continue
            if fix:
                shutil.rmtree(copy, ignore_errors=True)
                shutil.copytree(source, copy,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                print(f"  neu   apps/{app}/{module} ({len(diffs)} Abweichungen ersetzt)")
                continue
            bad += 1
            for kind, path in diffs:
                print(f"  FEHL  apps/{app}/{module}/{path}: {kind}")
    if bad:
        print(f"\n{bad} Kopie(n) weichen von lib/ ab. Geändert wird nur in lib/, "
              "dann: python3 check-shared.py --fix")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
