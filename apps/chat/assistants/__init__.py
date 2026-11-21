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
# 0.6 (RFC-0101 D6): der Bereich der App Vorgänge, in den „Nicht zufrieden“ geht
QUEUE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
FIELDS = {"id", "title", "greeting", "system", "model", "view", "gateway", "visibility",
          "owner", "commands", "views", "support_queue", "from"}
# 0.8 (RFC-0117 Stufe 2): `from` in einer Austauschdatei nennt die Vorlage, aus der die
# Einrichtung den Assistenten machte -- recipe:<id>@<major>. Eine Auskunft für den Import,
# kein Recht und kein Teil, nach dem sich der Assistent verhält.
FROM = re.compile(r"^recipe:[a-z0-9][a-z0-9-]{0,62}@[0-9]{1,4}$")
# 0.7 (RFC-0116 D9): `view` ist freiwillig -- ein Assistent ohne Sicht spricht ohne
# Werkzeuge (der wörtliche „Assistent ohne MCP“). `gateway` ohne `view` bleibt ein Fehler:
# wer ein Gateway nennt, glaubt, der Assistent habe dort Werkzeuge.
REQUIRED = ("id", "title", "system", "model", "visibility", "owner")
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
    if "gateway" in a and "view" not in a:
        errs.append("gateway ohne view: ein Gateway nennt nur, wer dort eine Sicht nutzt -- "
                    "ohne Sicht spricht der Assistent ohne Werkzeuge und braucht keins "
                    "(oaap.ai.assistants 0.7).")
    if a.get("views") is True and "view" not in a:
        errs.append("views ohne view: Ansichten kommen aus den Werkzeugen einer Sicht.")
    if "from" in a and not (isinstance(a["from"], str) and FROM.match(a["from"])):
        errs.append("from: recipe:<vorlage>@<hauptversion>, z. B. recipe:hilfe-assistent@1 "
                    "(oaap.ai.assistants 0.8).")
    if "commands" in a and not isinstance(a["commands"], bool):
        errs.append("commands: true oder false.")
    if "views" in a and not isinstance(a["views"], bool):
        errs.append("views: true oder false (Ansichten von MCP Apps zeigen, RFC-0075 D2).")
    if "support_queue" in a and not (isinstance(a["support_queue"], str) and QUEUE.match(a["support_queue"])):
        errs.append("support_queue: die Kennung eines Bereichs der App Vorgänge (z. B. sap-support), "
                    "Kleinbuchstaben, Ziffern, Bindestrich, höchstens 40 Zeichen.")
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


# ------------------------------------------------------------- Sichtbarkeit (D4)
# Seit RFC-0097 Stufe 1 ein gemeinsames Modul (lib/assistants, Kopien in chat und
# mcp-gateway, geprüft von check-shared.py): Der Chat prüft damit Assistenten aus
# seiner Konfiguration und seiner alten Datei, das MCP-Gateway die, die bei ihm
# liegen. Eine Regel, zwei Leser -- nicht zwei Regeln, die sich eines Tages
# widersprechen.
#
# | scope   | sieht                                   | verwaltet                          |
# |---------|-----------------------------------------|------------------------------------|
# | private | nur `owner`                             | nur `owner`                        |
# | context | wer `assistant.use` im Team hat         | wer `assistant.manage` im Team hat |
# | tenant  | jede Person, die die App öffnen darf    | `tenant_admin`                     |
#
# Sind die fachlichen Berechtigungen nicht lesbar (grants None), sieht niemand
# einen Team-Assistenten (geschlossen scheitern, wie authz_client).

CONTEXT_FIELD = "team"


class Who:
    """Wer fragt: Kennung, Plattformrollen, fachliche Berechtigungen (oder None)."""

    def __init__(self, uid, roles, grants):
        self.uid, self.roles, self.grants = uid, set(roles), grants


def may_in(grants, permission, **fields):
    """Rein: erlauben schon aufgelöste Berechtigungen `objekt.tätigkeit`? (wie authz_client)"""
    ob, _, act = (permission or "").partition(".")
    for g in grants or ():
        if not isinstance(g, dict) or g.get("object") != ob \
                or act not in (g.get("activities") or ()):
            continue
        fl = g.get("fields") or {}
        if all(fk in fields and fields[fk] in have for fk, have in fl.items()) \
                and all(fl[fk] is None or fields[fk] in fl[fk]
                        for fk in fields if fk in fl):
            return True
    return False


def _team(a):
    ctx = (a.get("visibility") or {}).get("context") or {}
    return ctx.get(CONTEXT_FIELD)


def can_see(a, who):
    scope = (a.get("visibility") or {}).get("scope")
    if scope == "tenant":
        return True
    if scope == "private":
        return bool(who.uid) and a.get("owner") == who.uid
    if scope == "context":
        team = _team(a)
        return bool(team) and (may_in(who.grants, "assistant.use", team=team)
                               or may_in(who.grants, "assistant.manage", team=team))
    return False


def can_manage(a, who):
    scope = (a.get("visibility") or {}).get("scope")
    if scope == "tenant":
        return "tenant_admin" in who.roles
    if scope == "private":
        return bool(who.uid) and a.get("owner") == who.uid
    if scope == "context":
        team = _team(a)
        return bool(team) and may_in(who.grants, "assistant.manage", team=team)
    return False


def problems(a):
    """Die Sätze von oaap.ai.assistants, dazu: als Kontext nur ein Team."""
    errs = check(a)
    v = a.get("visibility") if isinstance(a, dict) else None
    if not errs and v.get("scope") == "context" and set(v["context"]) != {CONTEXT_FIELD}:
        errs.append(f"visibility: als Kontext gibt es nur {CONTEXT_FIELD} "
                    f"(z. B. {{\"{CONTEXT_FIELD}\": \"service\"}}).")
    return errs
