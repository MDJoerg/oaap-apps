#!/usr/bin/env python3
"""Halten sich die Apps an RFC-0118 („Ein Gesicht für alle OAAP-Apps“)?

Liest jede App unter apps/ -- Manifest und Quelltext -- und meldet, was
den Regeln widerspricht. Läuft neben check-store.py und check-shared.py,
vor jedem Push. (Das ist der Kern von „CI“, continuous integration: jede
Änderung wird geprüft, bevor sie landet -- hier noch von Hand gestartet.)

Fehler (Ausgang 1):
  name      „OAAP“ im App-Namen (RFC-0118 §3)
  docs      keine Hilfe (docs:) -- Pflicht für jede App (§9, D5)
  mcp       keine MCP-Tür bei einer Fach- oder Betriebs-App mit Daten (§9, D6)
  template  eine App mit MCP-Tür bringt keine Vorlage für einen Assistenten mit
            (RFC-0119 D10, §7) und steht nicht unter den Ausnahmen
  frame     eine App mit Oberfläche lädt Kit und Rahmen nicht (§4, D3)
  palette   eine App setzt die Markenfarben selbst statt über das Kit
Hinweise (kein Fehler):
  words     Umlaute als ae/oe/ue oder RFC-Nummern in Texten der Oberfläche

Aufruf: python3 check-design.py [app ...]   # ohne Namen: alle
"""
import os
import re
import sys

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = os.path.dirname(os.path.abspath(__file__))

# RFC-0118 §2/§5: A Arbeitsplatz, B Fachanwendung, C Betriebs-App,
# D Dienst ohne Seite, W eingepacktes Fremdprodukt.
TYPES = {
    "chat": "A",
    "vorgaenge": "B", "spezialisten": "B", "studio": "B", "wegweiser": "B",
    "partnerverwaltung": "B", "mitarbeiterverwaltung": "B", "projekt": "B", "raci": "B",
    "ai-gateway": "C", "mcp-gateway": "C", "help": "C", "fleetview": "C",
    "store-editor": "C", "package-catalog": "C", "marketplace": "C",
    "ollama-models": "C", "rollen-rechte": "C", "vpn-client": "C", "recherche": "C",
    "keycloak": "D", "livekit": "D",
    "code-server": "W", "webtop": "W",
}
# Ausnahmen von der MCP-Pflicht (§9): der Chat ist Client, das MCP-Gateway
# ist die Tür selbst.
NO_MCP = {"chat", "mcp-gateway"}
# RFC-0119 D10: eine App mit MCP-Tür bringt mindestens eine Vorlage für einen
# Assistenten mit (`assistant_templates:`), außer hier -- je mit Grund. Die
# Ausnahmen stehen hier und nicht als Kommentar im Manifest: so ändert eine
# Ausnahme kein Paket.
NO_TEMPLATE = {
    "ai-gateway": "Betriebs-App des Modellzugangs; keine Frage, die eine Person des Mandanten stellt",
    "ollama-models": "Betriebswerkzeug für lokale Modelle",
    "package-catalog": "Betriebswerkzeug für Pakete",
    "vpn-client": "Betriebswerkzeug für den Tunnel",
    "store-editor": "Werkzeug des Katalogpflegers, nicht des Mandanten",
    "help": "die Plattform bringt die Vorlage „hilfe-assistent“ für dieselbe Quelle mit",
    "recherche": "die Plattform bringt die Vorlage „recherche“ mit (RFC-0116); der Betreiber gibt sie frei",
}
# Eigene Oberfläche, aber kein Rahmen: der Chat ordnet selbst an (Typ A).
NO_FRAME = {"chat"}

BRAND_DEFS = re.compile(r"--oaap-(blue-\d{3}|bg|surface)\s*:|#2f6fed|#1e3a8a|#172554")
FOLDED = re.compile(r"\b(fuer|gehoer\w*|aender\w*|Aender\w*|loesch\w*|Loesch\w*|moecht\w*|koenn\w*|"
                    r"muess\w*|Pruef\w*|pruef\w*|zurueck|Zurueck|Gultig|Verknupf\w*|Aktivitat)\b")
RFC_IN_UI = re.compile(r">[^<]*RFC-\d{4}[^<]*<")


def manifest(app):
    p = os.path.join(ROOT, "apps", app, "oaap-app.yaml")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) if yaml else {}


def sources(app):
    """Der Quelltext der App ohne die Kopien aus lib/ und ohne Tests."""
    base = os.path.join(ROOT, "apps", app)
    skip = {"appframe", "mcpdoor", "gwcore", "assistants", "md", "node_modules", "__pycache__"}
    for d, dirs, names in os.walk(base):
        dirs[:] = [x for x in dirs if x not in skip]
        for n in names:
            if n.startswith("test_") or not n.endswith((".py", ".html", ".js", ".css")):
                continue
            p = os.path.join(d, n)
            try:
                with open(p, encoding="utf-8") as f:
                    yield os.path.relpath(p, base), f.read()
            except (OSError, UnicodeDecodeError):
                continue


def check(app):
    errs, hints = [], []
    m = manifest(app)
    a = m.get("app") or {}
    kind = TYPES.get(app, "?")
    name = str(a.get("name") or "")
    if re.search(r"\bOAAP\b", name):
        errs.append(f"name: „{name}“ trägt OAAP (§3)")
    if not m.get("docs"):
        errs.append("docs: keine Hilfe deklariert (§9)")
    if kind in ("B", "C") and app not in NO_MCP and not m.get("mcp"):
        errs.append("mcp: keine MCP-Tür (§9)")
    if m.get("mcp") and app not in NO_TEMPLATE and app not in NO_MCP:
        folder = str((m.get("assistant_templates") or {}).get("path") or "").strip().rstrip("/")
        d = os.path.join(ROOT, "apps", app, folder) if folder else ""
        if not d or not os.path.isdir(d) or not any(n.endswith(".json") for n in os.listdir(d)):
            errs.append("template: keine Vorlage für einen Assistenten (assistant_templates:, "
                        "RFC-0119 D10) -- oder als Ausnahme in NO_TEMPLATE mit Grund")
    src = list(sources(app))
    if kind in ("B", "C") and app not in NO_FRAME:
        has_kit = any("/platform/theme.css" in s for _p, s in src) or os.path.isdir(
            os.path.join(ROOT, "apps", app, "appframe"))
        has_frame = any("/platform/app.js" in s for _p, s in src) or os.path.isdir(
            os.path.join(ROOT, "apps", app, "appframe"))
        if not (has_kit and has_frame):
            errs.append("frame: Kit (/platform/theme.css) und Rahmen (/platform/app.js) fehlen (§4)")
    for p, s in src:
        # views/ sind MCP-Apps-Ansichten im Sandkasten: dort gibt es kein Kit
        if kind in ("B", "C") and not p.startswith("views") and BRAND_DEFS.search(s):
            errs.append(f"palette: {p} setzt Markenfarben selbst ({BRAND_DEFS.search(s).group(0)})")
        for hit in sorted(set(FOLDED.findall(s)))[:5]:
            hints.append(f"words: {p}: „{hit}“")
        r = RFC_IN_UI.search(s)
        if r and kind in ("B", "C"):
            hints.append(f"words: {p}: RFC-Nummer in der Oberfläche: {r.group(0)[:60]}")
    return kind, errs, hints


def main(argv):
    apps = [x for x in argv if not x.startswith("-")] or sorted(
        d for d in os.listdir(os.path.join(ROOT, "apps"))
        if os.path.isfile(os.path.join(ROOT, "apps", d, "oaap-app.yaml")))
    bad = 0
    for app in apps:
        kind, errs, hints = check(app)
        if not errs:
            print(f"  ok    {app} ({kind})" + (f"  [{len(hints)} Hinweise]" if hints else ""))
        else:
            bad += 1
            print(f"  FEHLT {app} ({kind})")
            for e in errs:
                print(f"          {e}")
        if "-v" in argv:
            for h in hints:
                print(f"          (Hinweis) {h}")
    print(f"{len(apps) - bad} von {len(apps)} Apps halten RFC-0118 und RFC-0119 D10 ein")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
