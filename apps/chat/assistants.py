"""oaap.ai.assistants 0.1 -- ein Assistent prüfen (RFC-0074 §2, D1-D4).

Ein Assistent ist eine Sicht, ein Systemprompt mit Begrüßung, ein Modellname
und eine Sichtbarkeit. KEINE Rechte: kein Schlüssel, keine Rolle, kein
`on_behalf_of`. Ein Feld, das nach einem Recht aussieht, wird mit einem Satz
abgelehnt, statt still übergangen -- wer es schreibt, glaubt, es wirke.

Nur Standardbibliothek; die Funktion ist rein, damit ein Chat sie beim
Import einer Datei und beim Speichern gleich anwendet.
"""
import re

VERSION = 1
ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MODEL = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
INSTANCE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
CONTEXT_KEY = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
FIELDS = {"id", "title", "greeting", "system", "model", "view", "gateway", "visibility",
          "owner", "commands"}
REQUIRED = ("id", "title", "system", "model", "view", "visibility", "owner")
# Was nach einem Recht aussieht (D1): ausdrücklich abgelehnt.
RIGHTS = {"key", "api_key", "token", "secret", "roles", "role", "on_behalf_of", "principal",
          "scopes", "scope", "grants", "password"}
SCOPES = ("private", "context", "tenant")
LIMITS = {"title": 80, "greeting": 500, "system": 8000, "owner": 64}


def check(a):
    """Liste von Sätzen; leer = gültig."""
    if not isinstance(a, dict):
        return ["Ein Assistent ist ein Objekt."]
    errs = []
    for f in sorted(set(a) & RIGHTS):
        errs.append(f"{f}: ein Assistent hat keine Rechte (RFC-0074 D1) -- er handelt mit den "
                    "Rechten der Person, die mit ihm spricht.")
    for f in sorted(set(a) - FIELDS - RIGHTS):
        errs.append(f"{f}: unbekanntes Feld in oaap.ai.assistants 0.1.")
    for f in REQUIRED:
        if f not in a:
            errs.append(f"{f}: fehlt.")
    if "id" in a and not (isinstance(a["id"], str) and ID.match(a["id"])):
        errs.append("id: Kleinbuchstaben, Ziffern, Bindestrich, höchstens 48 Zeichen.")
    for f, n in LIMITS.items():
        if f in a and not (isinstance(a[f], str) and 0 < len(a[f]) <= n):
            errs.append(f"{f}: Text mit 1 bis {n} Zeichen.")
    if "model" in a and not (isinstance(a["model"], str) and MODEL.match(a["model"])):
        errs.append("model: ein NAME des KI-Gateways (z. B. standard), nie ein Modell oder eine "
                    "Adresse (RFC-0074 D3).")
    if "view" in a and not (isinstance(a["view"], str) and ID.match(a["view"])):
        errs.append("view: der Name einer Sicht des MCP-Gateways.")
    if "gateway" in a and not (isinstance(a["gateway"], str) and INSTANCE.match(a["gateway"])):
        errs.append("gateway: der Name einer Gateway-Instanz des Mandanten.")
    if "commands" in a and not isinstance(a["commands"], bool):
        errs.append("commands: true oder false.")
    v = a.get("visibility")
    if "visibility" in a:
        if not isinstance(v, dict) or v.get("scope") not in SCOPES:
            errs.append("visibility: {scope: private | context | tenant}.")
        elif v["scope"] == "context":
            ctx = v.get("context")
            if set(v) - {"scope", "context"} or not isinstance(ctx, dict) or len(ctx) != 1 \
                    or not all(isinstance(k, str) and CONTEXT_KEY.match(k)
                               and isinstance(x, str) and 0 < len(x) <= 64
                               for k, x in ctx.items()):
                errs.append("visibility: scope context nennt genau einen Kontext, "
                            "z. B. {team: service}.")
        elif set(v) - {"scope"}:
            errs.append(f"visibility: scope {v['scope']} hat keine weiteren Angaben.")
    return errs


def check_document(doc):
    """Eine Datei zum Austausch zwischen Chats: {"v": 1, "assistants": [...]}."""
    if not isinstance(doc, dict) or doc.get("v") != VERSION \
            or not isinstance(doc.get("assistants"), list):
        return ['Eine Datei ist {"v": 1, "assistants": [...]}.']
    errs, seen = [], set()
    for i, a in enumerate(doc["assistants"]):
        errs += [f"[{i}] {e}" for e in check(a)]
        aid = a.get("id") if isinstance(a, dict) else None
        if aid in seen:
            errs.append(f"[{i}] id: {aid} kommt zweimal vor.")
        seen.add(aid)
    return errs
