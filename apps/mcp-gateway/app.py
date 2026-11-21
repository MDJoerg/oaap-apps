"""OAAP MCP-Gateway 0.3 — Tool-Server hinter einem Endpunkt je Sicht.

Umsetzung der Capability `oaap.ai.mcp-gateway` 0.3 (RFC-0065 Stufen 1
bis 3): Eine Sicht bündelt Tools, Resources und Prompts mehrerer Server
unter eigenen Namen, mit festen Argumenten und Grenzen; Rückfragen vom
Server an den Client sind je Sicht einstellbar. Hinein kommt man mit
einem Schlüssel oder als Mensch, angemeldet am Anmeldedienst des
Mandanten (Zugangsprofil); ein Server mit Verfahren X sieht den
Menschen über einen getauschten Token.
Ein KI-Client — Claude Code, Copilot, ein eigener Agent — spricht mit
`/mcp/<sicht>` wie mit einem einzigen MCP-Server. Welcher Server
dahintersteht und wo, erfährt er nie.

Zwei Wege in dieselbe App, mit verschiedenen Rollen im Manifest:

- `/mcp/...` ist `public`: Die Plattform authentifiziert dort nichts,
  entfernt gefälschte Identitäts-Kopfzeilen und bremst je Client
  (RFC-0010). Geprüft wird hier: ein **API-Schlüssel** oder das **Token
  eines angemeldeten Menschen** (signin.py), und was Schlüssel oder
  Zugangsprofil benutzen dürfen.
- `/.well-known/oauth-protected-resource…` ist `public`: Dort erfährt
  ein Client, bei welchem Anmeldedienst er sich anmelden soll.
- `/` ist `admin`: Server, Katalog, Sichten, Schlüssel, Verbrauch,
  Auswertung und `/report/v1/…` für ein Abrechnungssystem.

Je `tools/call`, `resources/read` und `prompts/get`, in dieser
Reihenfolge (Spec §7, §8): Eintrag in der Sicht und freigegeben,
Wirkungsklasse, Argumente gegen das **freigegebene** Schema und die
Grenzen der Sicht, Budget, dann der Schutz des Servers. Dann erst geht
der Aufruf hinaus — und genau eine Messzeile entsteht, ohne Argument,
ohne Ergebnis (Spec §10).

Gebaut als gewöhnliche OAAP-App: ein HTTP-Port, Persistenz nur unter
dem deklarierten Mount, Konfiguration über deklarierte Variablen, Logs
nach stdout, Gesundheitspfad, nur Standardbibliothek.
"""
import hmac
import html
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote_plus

import argcheck
import assistants as arules
CASE_NUMBER_RE = __import__("re").compile(r"^V-\d{4}-\d{4,6}$")
import books
import clientfiles
import compose
import guard
import landscape
import signed_approval
import signin
import upstream
from gwcore import charts as gcharts
from gwcore import report as greport
from gwcore.vault import Vault, VaultError
from authz_client import Authz

VERSION = "0.39.0"
PORT = 8000

DATA_DIR = os.environ.get("MCPGW_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "gateway.db")
MASTER = os.environ.get("MCPGW_MASTER_KEY", "")
VAULT = Vault(b"oaap-mcp-gateway-vault|", "MCPGW_MASTER_KEY")
# 0.16 (RFC-0095 §4.2): ein eigener Bereich für die Zugänge der Personen -- ein
# Zugang eines Servers und der einer Person sind nie gegeneinander austauschbar.
VAULT_PERSONAL = Vault(b"oaap-mcp-gateway-personal|", "MCPGW_MASTER_KEY")

# Einstellungen von der Plattform (Spec §3.8, RFC-0073 Stufe 3): Hat ein
# Konnektor die Clients dieses Gateways angelegt, reicht die Plattform fünf
# Werte als OAAP_MCP_IDP_* herein. Sie gelten nur, wo die eigene Einstellung
# leer ist -- ein von Hand gesetzter Wert gewinnt, einzeln je Einstellung.
PLATFORM_SETTINGS = (("MCPGW_ISSUER", "OAAP_MCP_IDP_ISSUER"),
                     ("MCPGW_AUDIENCE", "OAAP_MCP_IDP_AUDIENCE"),
                     ("MCPGW_SIGNIN_CLIENT", "OAAP_MCP_IDP_SIGNIN"),
                     ("MCPGW_CLIENT_SECRET", "OAAP_MCP_IDP_SECRET"),
                     ("MCPGW_PLATFORM_AUDIENCE", "OAAP_MCP_IDP_PLATFORM_AUDIENCE"))


def setting(own, platform):
    """(Wert, Herkunft): 'hand', 'platform' oder '' (leer)."""
    v = (os.environ.get(own) or "").strip()
    if v:
        return v, "hand"
    v = (os.environ.get(platform) or "").strip()
    return (v, "platform") if v else ("", "")


SETTING_FROM = {own: setting(own, plat)[1] for own, plat in PLATFORM_SETTINGS}
_S = dict((own, setting(own, plat)[0]) for own, plat in PLATFORM_SETTINGS)

# Anmeldung (Spec §2.2, RFC-0065 D14/D15). Ohne Issuer ist sie aus.
ISSUER = _S["MCPGW_ISSUER"].rstrip("/")
AUDIENCE = _S["MCPGW_AUDIENCE"]
SIGNIN_CLIENT = _S["MCPGW_SIGNIN_CLIENT"]
ISSUER_KEYS = signin.Keys(ISSUER) if ISSUER else None
# Verfahren I (RFC-0073 Stufe 2, Spec §3.7): Tausch mit dem eigenen Client.
CLIENT_SECRET = _S["MCPGW_CLIENT_SECRET"]
PLATFORM_AUDIENCE = _S["MCPGW_PLATFORM_AUDIENCE"]
ACT_HEADER = "X-OAAP-Act-Token"
# Verfahren A (RFC-0074 Stufe 1, Spec §3.9): Ein Chat der Plattform trägt den
# Menschen hierher. Die Tür auf der Seite des Chats setzt die sechs Kopfzeilen
# der Person (die Zusicherung an DIESES Gateway adressiert) und den Namen des
# Chats; auf /mcp entfernt die Plattform jede vom Client gesendete Kopfzeile
# dieser Art. Gegenüber einer Quelle reicht das Gateway die Zusicherung weiter.
ACT_ASSERTION = "X-OAAP-Act-Assertion"
CLIENT_HEADER = "X-OAAP-MCP-Client"
ASSISTANT_HEADER = "X-OAAP-Assistant"
ASSISTANT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
# 0.22 (RFC-0097): der Chat sagt `<assistent>@<revision>`; ohne @ wie bisher.
ASSISTANT_REV_RE = re.compile(r"^([a-z0-9][a-z0-9-]{0,47})(?:@([1-9][0-9]{0,8}))?$")
# 0.7.2 (RFC-0074 §5.6): der Chat sagt, dass die Person DIESEN Aufruf bestätigt hat.
# Nur zur Auskunft in der Messzeile, nur bei Verfahren A; nie eine Erlaubnis.
CONFIRMED_HEADER = "X-OAAP-Confirmed"
# 0.34 (RFC-0105 §7.1): die Person, die genau diesen Aufruf freigab (ein Spezialist schreibt
# nie auf eigenes Wort). Eine Angabe des Aufrufers, gemessen und gezeigt, nicht geprüft.
APPROVED_HEADER = "X-OAAP-Approved-By"
# 0.21.0 (RFC-0101 §2.2): die Ampel eines Gesprächs als zusätzliche Decke DIESER
# Anfrage -- verschärft die Decke des Schlüssels/Profils, lockert sie nie.
LIGHT_HEADER = "X-OAAP-Light"
_TOKEN_URL = {}

# Quellen der Plattform (RFC-0073 Stufe 1, Spec §3.6): nur gesetzt, wenn das
# Manifest `mcp_sources: {read: true}` erklärt; die Plattform prägt den Schlüssel.
SOURCES_URL = (os.environ.get("OAAP_MCP_SOURCES_URL") or "").strip()
SOURCES_KEY = (os.environ.get("OAAP_MCP_SOURCES_KEY") or "").strip()
SOURCES_EVERY = 300
SOURCES_STATE = {"at": "", "error": "", "report": {}}
# RFC-0073 §16 (0.10.0): je Quelle die Erkundungs-Tür der Plattform, wie die
# Liste sie nennt. Dort liest das Gateway den Katalog ohne Menschen, nur die
# Listen; jeder Aufruf geht weiter als Person durch die Tür von Verfahren I.
# Nur im Speicher: die Liste wird beim Start und alle 5 Minuten neu gelesen.
SOURCE_DISCOVER = {}


def fetch_sources():
    """Die Liste von der eigenen Seite der Plattform. Wirft ValueError mit einem Satz."""
    if not (SOURCES_URL and SOURCES_KEY):
        raise ValueError("Diese Instanz liest keine Quellen (Manifest ohne mcp_sources oder "
                         "Generalprobe).")
    req = urllib.request.Request(SOURCES_URL, headers={"Authorization": "Bearer " + SOURCES_KEY})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            doc = json.loads(resp.read(1_000_000))
    except urllib.error.HTTPError as exc:
        raise ValueError(f"Die Plattform antwortete {exc.code} auf die Quellenliste.") from None
    except (OSError, ValueError) as exc:
        raise ValueError(f"Quellenliste nicht gelesen: {type(exc).__name__}") from None
    if not isinstance(doc, dict) or doc.get("v") != 1 or not isinstance(doc.get("sources"), list):
        raise ValueError("Die Quellenliste hat eine unbekannte Form.")
    return doc["sources"]


def remember_discover(sources):
    """Merkt sich die Erkundungs-Türen der Liste (nur http://, gültige Namen)."""
    found = {}
    for src in sources:
        name, url = str(src.get("name") or ""), str(src.get("discover") or "")
        if books.NAME.match(name) and url.startswith("http://"):
            found[name] = url
    SOURCE_DISCOVER.clear()
    SOURCE_DISCOVER.update(found)


def discover_door(name):
    """Die Erkundungs-Tür einer Quelle, oder ''. Kennt dieser Prozess sie nicht
    (die Kommandozeile ist ein eigener Prozess, gemessen 18.11.), liest er die
    Liste einmal nach -- ohne abzugleichen."""
    if name not in SOURCE_DISCOVER and SOURCES_URL and SOURCES_KEY:
        try:
            remember_discover(fetch_sources())
        except ValueError:
            pass
    return SOURCE_DISCOVER.get(name, "")


HELP_ACTOR = "plattform (RFC-0094 D6)"


def reading_tools(name):
    """Die lesenden Tools im Katalog eines Servers: angeboten und `readOnlyHint: true`.

    Eine Regel für zwei Leser: die einmalige Freigabe einer Hilfe (RFC-0094 D6)
    und eine Sicht der Einrichtungstür mit `{"server", "effect": "read"}`
    (RFC-0104 §3.3). Ein Tool ohne den Hinweis gilt nie als lesend."""
    out = []
    for row in books.catalog(DB, name, "tool"):
        if not row["present"]:
            continue
        try:
            doc = json.loads(row["doc"] or "{}")
        except ValueError:
            continue
        if (doc.get("annotations") or {}).get("readOnlyHint") is True:
            out.append(row)
    return out


def help_auto_done(name):
    """Ist die einmalige Freigabe für diesen Hilfe-Server schon gelaufen?"""
    return DB.execute("SELECT 1 FROM audit WHERE action='help.auto' AND subject=? LIMIT 1",
                      (name,)).fetchone() is not None


def auto_approve_help(name):
    """Gibt die LESENDEN Tools und die Prompts einer Hilfe-Instanz des eigenen
    Mandanten in jede Sicht mit dem Zugang „Plattform“ (Chat, Verfahren A) frei.

    Genau einmal je Hilfe-Server (Protokolleintrag `help.auto`, RFC-0094 D6): Was
    der Betreiber danach entfernt, bleibt entfernt; eine später angelegte Sicht
    bekommt nichts von selbst. Ein Tool ohne `readOnlyHint: true` wird nie
    automatisch freigegeben, Resources auch nicht (jedes Dokument wäre eine).
    Gibt die freigegebenen Einträge zurück."""
    ok, why = read_catalog(name)
    if not ok:
        books.log(DB, HELP_ACTOR, "item.auto", name, f"nicht freigegeben: {why}")
        DB.commit()
        return []
    views = [v["name"] for v in books.views(DB) if "platform" in books.view_ways(v)]
    done = []
    rows = [r for r in books.catalog(DB, name, "prompt") if r["present"]] + reading_tools(name)
    for row in rows:
        for view in views:
            if any(i["server"] == name and i["source"] == row["name"]
                   for i in books.items(DB, view, row["kind"])):
                continue
            try:
                exposed = books.approve(DB, HELP_ACTOR, view, row["kind"], name, row["name"],
                                        effect="read")
                done.append(f"{view}/{exposed}")
            except ValueError as exc:
                books.log(DB, HELP_ACTOR, "item.auto", f"{view}/{row['name']}", str(exc))
    books.log(DB, HELP_ACTOR, "help.auto", name,
              (f"Hilfe freigegeben: {', '.join(done)}" if done else
               "Hilfe erkannt, keine Sicht mit Zugang „Plattform“ -- nichts freigegeben"))
    DB.commit()
    return done


def sync_sources():
    """Liste holen und abgleichen. Gibt (ok, Satz) zurück; merkt sich das Ergebnis für die Seite."""
    SOURCES_STATE["at"] = books.now()
    try:
        sources = fetch_sources()
    except ValueError as exc:
        SOURCES_STATE["error"] = str(exc)
        return False, str(exc)
    rep = books.sync_sources(DB, sources, guard.clean(dict(guard.DEFAULTS)))
    remember_discover(sources)
    # RFC-0094 D6: a help of the own tenant is searchable by the chat
    # without an approval step -- once per help server, also for one an
    # older gateway already entered (measured 18.11. on oaap-test).
    for src in sources:
        name = str(src.get("name") or "")
        srow = (DB.execute("SELECT * FROM servers WHERE source=?", (name,)).fetchone()
                if src.get("app") == "help" else None)
        if srow and srow["source"] == name and not srow["gone"] and not help_auto_done(srow["name"]):
            auto_approve_help(srow["name"])
    SOURCES_STATE["error"] = ""
    SOURCES_STATE["report"] = rep
    for name in rep["added"] + rep["moved"] + rep["back"]:
        srow = books.server_row(DB, name)
        if srow:
            guard_of(srow)
    words = {"added": "neu", "moved": "Adresse geändert", "back": "wieder da",
             "gone": "verschwunden", "conflict": "Name schon von Hand vergeben",
             "untrusted": "als fremder Inhalt markiert (Manifest)"}
    parts = [f"{words[k]}: {', '.join(v)}" for k, v in rep.items() if v]
    return True, f"{len(sources)} Quelle(n) in der Liste" + ("; " + "; ".join(parts) if parts else "")


try:
    CATALOG_MINUTES = max(0, min(1440, int(os.environ.get("MCPGW_CATALOG_MINUTES") or 15)))
except ValueError:
    CATALOG_MINUTES = 15

MAX_BODY = 4 * 1024 * 1024
DEFAULT_RATE = 120                  # Anfragen je Minute und Schlüssel, wenn nichts gesetzt
SESSION_IDLE = 1800                 # Sekunden ohne Anfrage, dann ist eine Sitzung vorbei

esc = html.escape
DB = books.connect(DB_PATH)
LIMITER = books.RateLimiter()

# ------------------------------------------------------------- Antworten

# Dieselbe Antwort für jedes Scheitern (Spec §2): kein Schlüssel, unbekannt,
# widerrufen, Token ungültig, Sicht gibt es nicht oder nimmt den Weg nicht an.
# Wer probiert, soll nichts lernen — nicht einmal, ob es die Sicht gibt. Seit
# 0.3 eine 401: Die MCP-Anmeldung braucht sie, um den Weg zur Anmeldung zu zeigen.
DENIED = json.dumps({"jsonrpc": "2.0", "id": None,
                     "error": {"code": -32000, "message": "Kein gültiger Schlüssel und keine "
                                                          "gültige Anmeldung für diese Sicht."}},
                    ensure_ascii=False).encode("utf-8")
# Ein angemeldeter Mensch, der diese Sicht nicht benutzen darf (Spec §2.3).
FORBIDDEN = json.dumps({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32000, "message": "Angemeldet, aber kein Zugangsprofil "
                                                             "erlaubt diese Sicht."}},
                       ensure_ascii=False).encode("utf-8")
SESSION_UNKNOWN = {"jsonrpc": "2.0", "id": None,
                   "error": {"code": -32000, "message": "Sitzung unbekannt oder abgelaufen — "
                                                        "bitte neu initialisieren."}}


def rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def rpc_error(msg_id, code, message, reason=None, retry_after=None):
    err = {"code": code, "message": message}
    if reason:
        err["data"] = {"oaap": {"reason": reason}}
        if retry_after:
            err["data"]["oaap"]["retry_after"] = int(retry_after)
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


# ------------------------------------------------------------- Fehlversuche

_FAILS = {}
_FAILS_LOCK = threading.Lock()
FAIL_WINDOW = 300
FAIL_LIMIT = 5


def note_failure(addr):
    with _FAILS_LOCK:
        cutoff = time.monotonic() - FAIL_WINDOW
        hits = [t for t in _FAILS.get(addr, ()) if t > cutoff]
        hits.append(time.monotonic())
        _FAILS[addr] = hits


def failing_hard(addr):
    with _FAILS_LOCK:
        cutoff = time.monotonic() - FAIL_WINDOW
        hits = [t for t in _FAILS.get(addr, ()) if t > cutoff]
        _FAILS[addr] = hits
        return len(hits) >= FAIL_LIMIT


# ------------------------------------------------------------- Server, Schutz

GUARDS = {}
_GUARDS_LOCK = threading.Lock()


def limits_of(srow):
    try:
        return guard.clean(json.loads(srow["limits"] or "{}"))
    except (ValueError, TypeError):
        return dict(guard.DEFAULTS)


def guard_of(srow):
    with _GUARDS_LOCK:
        g = GUARDS.get(srow["name"])
        lim = limits_of(srow)
        if g is None:
            g = GUARDS[srow["name"]] = guard.ServerGuard(lim)
        elif g.limits != lim:
            g.update(lim)
        return g


class NoPerson(upstream.UpstreamError):
    """Verfahren X ohne angemeldeten Menschen — oder der Tausch scheiterte. Nie ein Rückfall (D8)."""

    def __init__(self, text):
        super().__init__("exchange", text)


class NoPersonal(upstream.UpstreamError):
    """Verfahren U ohne gespeicherten Zugang dieser Person -- abgelehnt, BEVOR etwas hinausgeht."""

    def __init__(self, text):
        super().__init__("personal", text)


def seal_personal(iss, sub, value):
    """Versiegelt einen persönlichen Zugang, gebunden an die Person (Issuer, sub):
    Eine Zeile, die jemand in der Datei einer anderen Person unterschiebt, öffnet sich nicht."""
    return VAULT_PERSONAL.seal(MASTER, json.dumps({"iss": iss, "sub": sub, "v": value}))


def personal_owner(person):
    """Unter welcher Kennung der Zugang dieser Person liegt: ("platform", uid). Eine Person mit
    Anmeldung am Anmeldedienst über die Verknüpfung, die die Plattform auf /me nannte (D2)."""
    if not person or not person.get("sub"):
        return None
    if person["iss"] == "platform":
        return ("platform", person["sub"])
    linked = books.person_link_get(DB, person["iss"], person["sub"])
    return ("platform", linked) if linked else None


def server_readiness(person, srow):
    """RFC-0095 §5: ready, needs_credential oder credential_rejected -- für einen Server."""
    if srow["auth_kind"] != "personal":
        return "ready"
    owner = personal_owner(person)
    row = books.personal_row(DB, owner[0], owner[1], srow["name"]) if owner else None
    if row is None:
        return "needs_credential"
    last = DB.execute("SELECT outcome, reason, time FROM usage WHERE user_iss=? AND user_sub=? "
                      "AND server=? ORDER BY id DESC LIMIT 1",
                      (person["iss"], person["sub"], srow["name"])).fetchone()
    if last and last["outcome"] == "refused" and last["reason"] == "credential" \
            and last["time"] >= row["stored"]:
        return "credential_rejected"
    return "ready"


def readiness_doc(person, vrow, reason=""):
    """Je Server der Sicht: Zustand, Zahl der aktiven Einträge, Hinweis -- für eine Person.
    `reason`: warum die Sicht dieser Person nicht offensteht (dann not_for_you, ohne Server)."""
    out = {"v": 1, "view": vrow["name"] if vrow else "", "me": me_url(), "servers": []}
    if reason or not vrow:
        out.update(ready=False, reason=reason or "kein Zugangsprofil erlaubt diese Sicht.",
                   state="not_for_you", missing_tools=0)
        return out
    counts = {}
    for i in books.items(DB, vrow["name"]):
        if books.item_state(DB, i) == "active":
            c = counts.setdefault(i["server"], {"tools": 0, "entries": 0})
            c["entries"] += 1
            c["tools"] += i["kind"] == "tool"
    missing = 0
    for name, c in sorted(counts.items()):
        srow = books.server_row(DB, name)
        if not srow:
            continue
        state = server_readiness(person, srow)
        spec = books.personal_spec(srow) if srow["auth_kind"] == "personal" else {}
        if state != "ready":
            missing += c["tools"]
        out["servers"].append({"server": name, "state": state, "tools": c["tools"],
                               "entries": c["entries"], "hint": spec.get("hint", ""),
                               "link": spec.get("link", "")})
    out.update(ready=not missing and all(s["state"] == "ready" for s in out["servers"]),
               missing_tools=missing, state="ready" if not missing else "incomplete", reason="")
    return out


def personal_access(srow, person):
    """Verfahren U (0.16, RFC-0095 §4.3): der Zugang genau dieser Person, sonst nichts von ihr."""
    name = srow["name"]
    owner = personal_owner(person)
    if not person or not person.get("sub"):
        raise NoPersonal(f"Der Server {name} braucht den persönlichen Zugang einer Person; mit "
                         "einem Schlüssel geht es nicht.")
    row = books.personal_row(DB, owner[0], owner[1], name) if owner else None
    if row is None:
        where = ("/me" if owner else
                 "/me -- öffne die Seite einmal über die Plattform, damit das Gateway deine "
                 "Anmeldung am Anmeldedienst dir zuordnen kann")
        raise NoPersonal(f"Für den Server {name} hast du noch keinen Zugang hinterlegt -- "
                         f"„Meine Werkzeuge“ auf diesem Gateway, {where}.")
    try:
        doc = json.loads(VAULT_PERSONAL.open_(MASTER, row["sealed"]))
    except (VaultError, ValueError) as exc:
        raise upstream.UpstreamError("credential", f"Dein Zugang ist nicht lesbar ({exc}); "
                                                   "trag ihn auf /me neu ein.") from None
    if doc.get("iss") != owner[0] or doc.get("sub") != owner[1]:
        raise upstream.UpstreamError("credential", "Dieser Zugang gehört nicht dir.")
    spec = books.personal_spec(srow)
    send = spec.get("send") or "bearer"
    if send == "basic" and ":" not in doc["v"]:
        # Vor 0.18 als ein Wert eingetragen, der Server ist inzwischen auf basic gestellt.
        raise upstream.UpstreamError("credential", f"Der Server {name} braucht jetzt Benutzer und "
                                                   "Token getrennt; trag deinen Zugang auf /me neu ein.")
    books.personal_touch(DB, owner[0], owner[1], name)
    # basic: upstream bildet base64(benutzer:token) erst hier, beim Senden (auth_headers).
    return {"auth_kind": send, "credential": ("" if send == "basic" else spec.get("prefix") or "")
            + doc["v"], "door": "personal"}


EXCHANGED = {}                       # (Issuer, sub, Server) → (Token, gültig bis); nur im Speicher
_EXCHANGED_LOCK = threading.Lock()


def token_endpoint():
    """Der Token-Endpunkt des Issuers, aus seinem Discovery-Dokument, gleicher Ursprung."""
    if "url" in _TOKEN_URL:
        return _TOKEN_URL["url"]
    status, body = upstream.plain("GET", ISSUER + "/.well-known/openid-configuration",
                                  headers={"Accept": "application/json"})
    try:
        doc = json.loads(body) if status == 200 else {}
    except ValueError:
        doc = {}
    url = str(doc.get("token_endpoint") or "")
    if doc.get("issuer", "").rstrip("/") != ISSUER or not signin.same_origin(url, ISSUER):
        raise upstream.UpstreamError("exchange", "Token-Endpunkt des Issuers nicht lesbar")
    _TOKEN_URL["url"] = url
    return url


def platform_act(person):
    """Verfahren I: das Token des Menschen gegen eines für den Plattform-Client tauschen.

    Gibt das Act-Token zurück; wirft NoPerson mit einem Satz. Kein Rückfall
    auf den eigenen Prinzipal (D8): ohne Menschen kein Aufruf."""
    if not person or not person.get("token"):
        raise NoPerson("Diese Quelle der Plattform arbeitet mit Verfahren I und braucht eine "
                       "angemeldete Person; mit einem Schlüssel geht es nicht.")
    if not (ISSUER and AUDIENCE and CLIENT_SECRET and PLATFORM_AUDIENCE and SOURCES_KEY):
        raise NoPerson("Verfahren I ist nicht eingerichtet (MCPGW_ISSUER, MCPGW_AUDIENCE, "
                       "MCPGW_CLIENT_SECRET, MCPGW_PLATFORM_AUDIENCE und der Quellen-Schlüssel "
                       "der Plattform).")
    cache = (person["iss"], person["sub"], "@platform")
    with _EXCHANGED_LOCK:
        hit = EXCHANGED.get(cache)
        if hit and hit[1] > time.time() + 30:
            return hit[0]
    try:
        token, lifetime = signin.exchange(token_endpoint(), AUDIENCE, CLIENT_SECRET,
                                          person["token"], PLATFORM_AUDIENCE)
    except signin.Invalid as exc:
        raise NoPerson(f"Token-Tausch zur Plattform abgelehnt: {exc}") from None
    except upstream.UpstreamError as exc:
        raise NoPerson(f"Token-Tausch zur Plattform nicht möglich: {exc}") from None
    with _EXCHANGED_LOCK:
        EXCHANGED[cache] = (token, time.time() + lifetime)
    return token


USER_ASSERTION = "X-OAAP-User-Assertion"
VIEWS_PATH = "/mcp/_views"            # 0.14.0: Suchhilfe des Chats
READINESS_PATH = "/mcp/_readiness"    # 0.17.0: Bereitschaft je Sicht für den Chat (RFC-0095 §5)
CLIENT_CONFIG_PATH = "/mcp/_client-config"  # 0.28.0: Datei für die Sichten eines Schlüssels (RFC-0107)
ASSISTANTS_PATH = "/mcp/_assistants"  # 0.22.0: Assistenten mit Revisionen (RFC-0097 Stufe 1)
# Fachliche Berechtigungen (RFC-0045) für Team-Assistenten: das Gateway erklärt das
# Objekt `assistant` selbst (Manifest), die Plattform gibt ihm dafür OAAP_AUTHZ_*.
AUTHZ = Authz(app=None)
# Felder, die das Gateway führt und die beim Speichern nie vom Schreibenden kommen.
ASSISTANT_RECORD_FIELDS = ("rev", "state", "manage", "changed_at", "changed_by", "created")

# 0.23.0 (RFC-0101 Stufe 2): Sterne. Die Person steht in einer Bewertung nur als HMAC mit
# einem Schlüssel dieses Gateways (D3) -- genug, um die eigene Bewertung zu ersetzen und
# Personen zu zählen, nicht genug, sie zu nennen.
RATING_KEY_FILE = os.path.join(DATA_DIR, "ratings.key")
RATING_TEXT_MAX = 8000                                   # D4, je Frage und Antwort
RATING_LIGHTS = ("", "green", "yellow", "red")
RATING_WORD_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
RATING_NAME_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,100}$")
_RATING_KEY = []


def rating_key():
    """Der Schlüssel für `who` und `slot` -- einmal erzeugt, 0600, bleibt in /data."""
    if not _RATING_KEY:
        try:
            with open(RATING_KEY_FILE, "rb") as f:
                key = f.read()
        except FileNotFoundError:
            key = b""
        if len(key) < 32:
            key = secrets.token_bytes(32)
            os.makedirs(DATA_DIR, exist_ok=True)
            fd = os.open(RATING_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        _RATING_KEY.append(key)
    return _RATING_KEY[0]


def rating_mac(*parts):
    import hashlib
    import hmac
    return hmac.new(rating_key(), "\x1f".join(str(p) for p in parts).encode(), hashlib.sha256).hexdigest()[:32]


def rating_who(uid):
    return rating_mac("who", uid)


def rating_check(body):
    """(geprüfte Bewertung, Fehlersätze). Was nicht in der Liste steht, wird nicht gespeichert --
    so hält eine Bewertung ohne Haken keinen Inhalt (RFC-0101 §3.2), auch wenn ein Chat mehr schickt."""
    errs = []

    def word(key, rx, n=100):
        v = body.get(key, "")
        if v in ("", None):
            return ""
        if not isinstance(v, str) or len(v) > n or not rx.match(v):
            errs.append(f"{key}: ungültig.")
            return ""
        return v

    r = {}
    rev = body.get("rev")
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 1:
        errs.append("rev: die Revision, die gesprochen hat (eine Zahl ab 1).")
    r["rev"] = rev
    r["scope"] = body.get("scope")
    if r["scope"] not in ("answer", "conversation"):
        errs.append("scope: answer oder conversation.")
    rnd = body.get("round", 0)
    if not isinstance(rnd, int) or isinstance(rnd, bool) or not 0 <= rnd <= 100000:
        errs.append("round: eine Zahl.")
        rnd = 0
    r["round"] = rnd
    stars = body.get("stars")
    if stars is not None and (not isinstance(stars, int) or isinstance(stars, bool) or not 0 <= stars <= 5):
        errs.append("stars: 0 bis 5, oder null zum Zurückziehen.")
    r["stars"] = stars
    conv = body.get("conversation")
    if not isinstance(conv, str) or not 1 <= len(conv) <= 64:
        errs.append("conversation: die Kennung des Gesprächs (1 bis 64 Zeichen).")
    r["conversation"] = conv
    r["model"] = word("model", RATING_NAME_RE)
    r["view"] = word("view", RATING_NAME_RE)
    r["gateway"] = word("gateway", RATING_NAME_RE)
    for k in ("model_light", "light"):
        r[k] = body.get(k) or ""
        if r[k] not in RATING_LIGHTS:
            errs.append(f"{k}: green, yellow oder red.")
    ms = body.get("ms", 0)
    r["ms"] = ms if isinstance(ms, int) and not isinstance(ms, bool) and 0 <= ms < 10 ** 9 else 0
    tools = body.get("tools") or []
    if not isinstance(tools, list) or len(tools) > 100:
        errs.append("tools: eine Liste (höchstens 100).")
        tools = []
    r["tools"] = []
    for t in tools:
        if not isinstance(t, dict) or not RATING_NAME_RE.match(str(t.get("name") or "")):
            errs.append("tools: je Aufruf name, effect, outcome.")
            break
        # Nur Name, Wirkung, Ausgang -- nie Argumente oder Ergebnisse (§3.2).
        r["tools"].append({"name": t["name"],
                           "effect": t.get("effect") if RATING_WORD_RE.match(str(t.get("effect") or "")) else "",
                           "outcome": t.get("outcome") if RATING_WORD_RE.match(str(t.get("outcome") or "")) else ""})
    text = body.get("text")
    r["text"] = None
    if text is not None:
        if not isinstance(text, dict) or not all(isinstance(text.get(k, ""), str) for k in ("question", "answer")):
            errs.append("text: {question, answer}, nur mit dem Haken „Frage und Antwort mitgeben“.")
        elif any(len(text.get(k, "")) > RATING_TEXT_MAX for k in ("question", "answer")):
            errs.append(f"text: höchstens {RATING_TEXT_MAX} Zeichen je Frage und Antwort.")
        else:
            r["text"] = {"question": text.get("question", ""), "answer": text.get("answer", "")}
    return r, errs
# D2 (0.17): die Kennung der Person im Realm, von der Plattform auf /me gesetzt. Geglaubt
# NUR, wenn die Plattform sagt, dass sie diese Kopfzeile setzt und auf jeder anderen Route
# entfernt (OAAP_IDENTITY_HEADERS, ab Plattform 0.1.307) -- eine ältere ließe einen Client
# sie selbst schicken.
IDP_HEADER = "X-OAAP-User-Idp"
TRUST_IDP = IDP_HEADER.lower() in {h.strip().lower() for h in
                                   (os.environ.get("OAAP_IDENTITY_HEADERS") or "").split(",")}
# Die eigene öffentliche Adresse (RFC-0043), für den Link auf /me in der Antwort an den Chat.
# 0.19 (Plattform ab 0.1.308, runtime 2.38, L5): die Seite DIESER Instanz im Portal --
# dort richtet die Verwaltung die Anmeldung ein. Leer bei einer älteren Plattform.
PORTAL_URL = (os.environ.get("OAAP_PORTAL_URL") or "").strip()


def portal_mcp_link(text="im Portal"):
    """Ein Link auf die Karte MCP der Instanzseite im Portal, sonst der Weg in Worten."""
    if PORTAL_URL.startswith("https://") or PORTAL_URL.startswith("http://"):
        return (f'<a href="{esc(PORTAL_URL)}?tab=ueberblick#mcp" target="_blank" '
                f'rel="noopener">{esc(text)}</a>')
    return f"{esc(text)} (Instanzen → diese Instanz → Reiter Überblick → Karte MCP)"


PUBLIC_BASE = next((n.strip().rstrip("/") for n in (os.environ.get("OAAP_INSTANCE_NAMES") or "")
                    .split(",") if n.strip().startswith(("https://", "http://"))), "")


def me_url():
    return PUBLIC_BASE + "/me" if PUBLIC_BASE else ""


def propagate_access(person):
    """Verfahren P (0.12, RFC-0066 Form A): die Person über eine Destination bis ins Zielsystem.

    Das Gateway gibt der Destination die Zusicherung, die die Plattform
    DIESEM Gateway für die Person gab (Chat über die via-Tür, Verfahren A,
    oder die Betreiberseite). Die Destination prüft sie gegen die Instanz,
    von der der Aufruf kommt, nennt den Benutzer im Zielsystem (`sap_user`)
    und stellt für genau ihn ein kurzlebiges Zertifikat aus. Das Gateway
    hält dafür nichts: keinen Schlüssel, kein Zertifikat, keinen Cookie.

    Ohne Zusicherung kein Aufruf — nie ein technischer Rückfall (D8). Ein
    Token des Anmeldediensts (Form B) nimmt die Destination noch nicht an.
    """
    if person and person.get("assertion"):
        return {"auth_kind": "none", "credential": "", "door": "destination",
                "headers": {USER_ASSERTION: person["assertion"]}}
    if person and person.get("token"):
        raise NoPerson("Dieser Server trägt die Person über eine Destination bis ins Zielsystem "
                       "(Verfahren P). Das geht heute mit einer Person aus einem Chat der "
                       "Plattform; eine Anmeldung beim Anmeldedienst nimmt die Destination noch "
                       "nicht an (RFC-0066 Form B).")
    raise NoPerson("Dieser Server trägt die Person über eine Destination bis ins Zielsystem "
                   "(Verfahren P) und braucht eine Person; mit einem Schlüssel geht es nicht.")


def server_access(srow, person=None):
    """Adresse und Zugang für upstream.py. Wirft UpstreamError mit einem Satz.

    Verfahren X (Spec §3.5): Statt eines eigenen Zugangs bekommt der
    Server ein Token, das der Anmeldedienst für genau ihn ausstellt — im
    Tausch gegen das Token des Menschen. Ohne Menschen kein Aufruf.
    """
    url = upstream.resolve(srow["address"])
    credential = ""
    if srow["credential"]:
        try:
            credential = VAULT.open_(MASTER, srow["credential"])
        except VaultError as exc:
            raise upstream.UpstreamError("credential",
                                         f"Zugangsdaten nicht lesbar: {exc}") from None
    if srow["auth_kind"] == "platform":
        # Zwei Nachweise an der Tür: der Quellen-Schlüssel dieser Instanz und das
        # Act-Token der Person. Die Plattform macht daraus die Kopfzeilen der Person.
        # Verfahren A (0.7): statt des Act-Tokens die Zusicherung, die die Plattform
        # DIESEM Gateway für die Person gab — kein Tausch, kein Anmeldedienst.
        if person and person.get("assertion"):
            if not SOURCES_KEY:
                raise NoPerson("Diese Instanz hat keinen Quellen-Schlüssel der Plattform.")
            return {"auth_kind": "bearer", "credential": SOURCES_KEY, "door": True,
                    "headers": {ACT_ASSERTION: person["assertion"]}}, url
        return {"auth_kind": "bearer", "credential": SOURCES_KEY, "door": True,
                "headers": {ACT_HEADER: platform_act(person)}}, url
    if srow["auth_kind"] == "propagate":
        return propagate_access(person), url
    if srow["auth_kind"] == "personal":
        return personal_access(srow, person), url
    if srow["auth_kind"] != "exchange":
        return {"auth_kind": srow["auth_kind"], "credential": credential}, url
    if person and person.get("procedure") == "A":
        raise NoPerson("Dieser Server arbeitet mit Verfahren X und braucht ein Token des "
                       "Anmeldediensts; über einen Chat der Plattform (Verfahren A) gibt es "
                       "keins. Dieses Tool ist hier nicht verfügbar.")
    if not person or not person.get("token"):
        raise NoPerson("Dieser Server arbeitet mit Verfahren X und braucht eine angemeldete "
                       "Person; mit einem Schlüssel geht es nicht.")
    ex = json.loads(srow["exchange"] or "{}")
    cache = (person["iss"], person["sub"], srow["name"])
    with _EXCHANGED_LOCK:
        hit = EXCHANGED.get(cache)
        if hit and hit[1] > time.time() + 30:
            return {"auth_kind": "bearer", "credential": hit[0]}, url
    try:
        token, lifetime = signin.exchange(upstream.resolve(ex.get("token_url", "")),
                                          ex.get("client_id", ""), credential, person["token"],
                                          ex.get("audience", ""))
    except signin.Invalid as exc:
        raise NoPerson(f"Token-Tausch abgelehnt: {exc}") from None
    except upstream.UpstreamError as exc:
        raise NoPerson(f"Token-Tausch nicht möglich: {exc}") from None
    with _EXCHANGED_LOCK:
        EXCHANGED[cache] = (token, time.time() + lifetime)
    return {"auth_kind": "bearer", "credential": token}, url


KIND_PLURAL = {"tool": "Tools", "resource": "Resources", "template": "Resource-Vorlagen",
               "prompt": "Prompts"}


def read_catalog(name, person=None):
    """Liest, was ein Server anbietet. Gibt (ok, satz) zurück; schreibt Katalog und Zustand.

    Bei Verfahren X braucht es einen Menschen: `person` mit dem Token des
    Betreibers (Kommandozeile `--token-stdin`). Ohne ihn bleibt der Katalog,
    wie er zuletzt gelesen wurde.

    Bei Verfahren I (Quelle der Plattform) liest das Gateway ohne Menschen
    über die Erkundungs-Tür (RFC-0073 §16): nur die Listen, als eigener
    Maschinen-Benutzer. Mit Menschen wie bisher durch die Tür von Verfahren I.
    """
    srow = books.server_row(DB, name)
    if not srow:
        return False, f"Einen Server „{name}“ gibt es nicht."
    discover = (discover_door(srow["source"] or name) if srow["auth_kind"] == "platform"
                and not person and SOURCES_KEY else "")
    if srow["auth_kind"] == "exchange" and not person:
        return False, (f"Katalog von „{name}“ nicht gelesen: Verfahren X braucht ein Token einer "
                       f"Person — app.py server read {name} --token-stdin ja.")
    if srow["auth_kind"] == "propagate" and not (person and person.get("assertion")):
        # P3: kein technischer Katalog-Benutzer -- es liest eine Person als sie selbst.
        return False, (f"Katalog von „{name}“ nicht gelesen: Verfahren P liest als Person. Eine "
                       f"Person mit eigenem Benutzer im Zielsystem liest die erste Werkzeugliste "
                       f"im Portal unter „Meine Werkzeuge“; die Verwaltung liest auf der Seite "
                       f"des Gateways mit „Katalog jetzt lesen“, wenn sie selbst dort einen "
                       f"Benutzer hat.")
    if srow["auth_kind"] == "personal" and not person:
        return False, (f"Katalog von „{name}“ nicht gelesen: Verfahren U liest mit dem eigenen "
                       f"Zugang einer Person -- auf der Seite des Gateways auf „Katalog jetzt "
                       f"lesen“ klicken, nachdem du ihn auf /me hinterlegt hast; oder eine Person "
                       f"mit Zugang liest die erste Werkzeugliste auf ihrer Seite /me.")
    if srow["auth_kind"] == "platform" and not person and not discover:
        # Eine Plattform vor 0.1.300 nennt keine Erkundungs-Tür (L8a, 18.11.).
        return False, (f"Katalog von „{name}“ nicht gelesen: Verfahren I braucht eine Person, "
                       f"und die Plattform nennt für diese Quelle keine Erkundungs-Tür (ab Referenz "
                       f"0.1.300). Als Person des Mandanten auf „Katalog lesen“ klicken, oder "
                       f"app.py server read {name} --token-stdin ja.")
    lim = limits_of(srow)
    try:
        if discover:
            server, url = {"auth_kind": "bearer", "credential": SOURCES_KEY, "door": True}, discover
        else:
            server, url = server_access(srow, person)
        offers, session = upstream.list_catalog(server, url, timeout=min(60, lim["timeout_s"]))
    except upstream.UpstreamError as exc:
        books.note_server(DB, name, error=str(exc))
        return False, f"Katalog von „{name}“ nicht gelesen: {exc}"
    changed = books.store_catalog(DB, name, offers)
    books.note_server(DB, name, version=session.version, error="", read=True)
    counts = ", ".join(f"{len(offers.get(k, []))} {KIND_PLURAL[k]}" for k in compose.KINDS
                       if offers.get(k) or k == "tool")
    text = f"Katalog von „{name}“ gelesen: {counts}, Protokoll {session.version}."
    if discover:
        # D15: die App zeigt dem Gateway SEINE Liste, nicht die einer Person.
        text += " Als Gateway gelesen (Erkundungs-Tür, RFC-0073 §16)."
    if changed:
        text += " Gesperrt, weil geändert oder verschwunden: " + ", ".join(changed) + "."
    return True, text


# ------------------------------------------------------------- Sitzungen

class DownSession:
    """Eine Sitzung des Clients. Gehört dem Schlüssel und der Sicht, die sie eröffnet haben."""

    def __init__(self, key_id, view, version, client_caps=None, person=None):
        self.id = secrets.token_urlsafe(24)
        self.key_id = key_id
        # Bei Anmeldung: der Mensch (Issuer, sub) — die Sitzung gehört ihm allein.
        self.person = person
        self.subject = (person["iss"], person["sub"]) if person else None
        self.view = view
        self.version = version
        # Welche Rückfragen der Client beantworten kann (aus seinem initialize).
        self.client_caps = {r for r in books.REQUESTS if isinstance(client_caps, dict)
                            and isinstance(client_caps.get(r), dict)}
        self.last = time.monotonic()
        self.upstream = {}               # Servername → upstream.Session
        self.inflight = {}               # Anfrage-ID des Clients → Servername (für Abbruch)
        self.pending = {}                # Rückfrage-ID beim Client → (Server, Sitzung, ID beim Server)
        self.lock = threading.Lock()


SESSIONS = {}
_SESS_LOCK = threading.Lock()


def end_session(sess):
    with _SESS_LOCK:
        SESSIONS.pop(sess.id, None)
    for name, us in list(sess.upstream.items()):
        srow = books.server_row(DB, name)
        if srow:
            try:
                server, url = server_access(srow, sess.person)
                upstream.delete(server, url, us.id)
            except upstream.UpstreamError:
                pass
    sess.upstream.clear()


def housekeeping():
    """Im Hintergrund: abgelaufene Sitzungen beenden, Kataloge neu lesen."""
    next_read = time.monotonic() + 30
    next_sources = time.monotonic()
    while True:
        if SOURCES_URL and time.monotonic() >= next_sources:
            ok, text = sync_sources()
            # Gemessen auf oaap-test (07.11.): beim ersten Start antwortet die
            # eigene Seite noch 404, weil die Plattform sie erst nach dem Start
            # des Containers neu schreibt. Ein Fehlschlag fragt bald wieder.
            next_sources = time.monotonic() + (SOURCES_EVERY if ok else 30)
            sys.stdout.write(f"Quellen: {text}\n")
            sys.stdout.flush()
        time.sleep(30)
        now = time.monotonic()
        with _SESS_LOCK:
            old = [s for s in SESSIONS.values() if now - s.last > SESSION_IDLE]
        for sess in old:
            end_session(sess)
        if CATALOG_MINUTES and now >= next_read:
            next_read = now + CATALOG_MINUTES * 60
            for srow in books.servers(DB):
                ok, text = read_catalog(srow["name"])
                sys.stdout.write(f"Katalog: {text}\n")
                sys.stdout.flush()


# ------------------------------------------------------------- Sichten

def rank_ok(effect, *ceilings):
    return all(books.EFFECT_RANK[effect] <= books.EFFECT_RANK.get(c, 0) for c in ceilings)


def visible_items(vrow, key, kind):
    """Die Einträge einer Art, die dieser Schlüssel in dieser Sicht sieht: aktiv, nicht über der Klasse."""
    out = {}
    foreign = books.untrusted_in_view(DB, vrow["name"])
    for item in books.items(DB, vrow["name"], kind):
        if books.item_state(DB, item) != "active":
            continue
        if not rank_ok(item["effect"], vrow["max_effect"], key["max_effect"]):
            continue
        if item["effect"] != "read" and foreign:
            continue                    # 0.36 (RFC-0116 §4.3): neben fremdem Inhalt nur Lesen
        out[item["name"]] = item
    return out


def multi_tools(vrow, key, version, asked):
    """0.32 (RFC-0114 §4.2): die Werkzeuge einer Multi-System-Sicht -- jedes einmal,
    mit dem Pflichtargument `system` (nur Systeme, auf denen es jetzt geht), und das
    Werkzeug `systeme` des Gateways. Ein Werkzeug ohne nutzbares System fehlt."""
    rank = landscape.ceiling_rank(key["ceiling"], asked)
    out = []
    for _n, item in sorted(visible_items(vrow, key, "tool").items()):
        choices = landscape.usable(DB, vrow["name"], item, rank)
        if choices:
            out.append(landscape.shaped_tool(compose.shaped("tool", item, version), choices))
    out.append(landscape.system_tool(version))
    return out


def multi_entries(vrow, key, kind, version, asked):
    """0.38 (RFC-0114 Stufe 2, D6): Prompts einer Multi-System-Sicht -- jeder einmal, mit
    dem Pflichtargument `system`; Ressourcen und Vorlagen je System, auf dem sie gehen,
    die URI mit dem System davor. Eine Ansicht (`ui://`) bleibt die der Referenz."""
    rank = landscape.ceiling_rank(key["ceiling"], asked)
    out = []
    for _n, item in sorted(visible_items(vrow, key, kind).items()):
        base = compose.shaped(kind, item, version)
        if kind in ("resource", "template") and landscape.plain_uri(item["name"]):
            ref = [c for c in landscape.usable(DB, vrow["name"], item, rank)
                   if c[0]["id"] == landscape.reference_of(DB, vrow["name"])]
            if ref:
                out.append(base)
            continue
        choices = landscape.usable(DB, vrow["name"], item, rank)
        if not choices:
            continue
        if kind == "prompt":
            out.append(landscape.shaped_prompt(base, choices))
        else:
            out.extend(landscape.shaped_resource(base, kind, row) for row, _s, _d in choices)
    return out


def refusal(vrow, kind, name):
    """Warum ein Eintrag nicht sichtbar ist: (Grund, Code, Satz). Nennt nie den Server."""
    word = compose.KIND_WORDS[kind]
    item = books.item_row(DB, vrow["name"], kind, name)
    state = books.item_state(DB, item) if item else None
    if state == "suspended":
        return "suspended", -32002, (f"{word} {name} ist gesperrt, bis der Betreiber einen "
                                     "geänderten Text bestätigt.")
    if state == "active" and item["effect"] != "read" and books.untrusted_in_view(DB, vrow["name"]):
        return "untrusted", -32002, (f"{word} {name} schreibt; diese Sicht enthält fremden Inhalt "
                                     "(Webseiten) und liefert deshalb nur Lesendes aus.")
    if state == "active":
        return "effect", -32002, (f"{word} {name} liegt über der Wirkungsklasse dieses "
                                  "Schlüssels oder der Sicht.")
    if kind in ("resource", "template"):
        return "unknown_resource", -32002, "Diese Resource gibt es in dieser Sicht nicht."
    return "unknown_" + kind, -32602, f"Unbekanntes {word}: {name}"


def entitled(key, vrow, way):
    return why_not(key, vrow, way) is None


WAY_NAMES = {"platform": "„Plattform“ (Personen aus einem Chat)", "signin": "„Anmeldung“",
             "key": "„Schlüssel“"}
LIGHT_DE = {"green": "grün", "yellow": "gelb", "red": "rot"}


def worst_server(view):
    """Der Server, der die Ampel einer Sicht bestimmt (der schlechteste), oder ''."""
    worst, name = -1, ""
    for s in books.view_servers(DB, view):
        srow = books.server_row(DB, s)
        rank = books.LIGHT_RANK[srow["light"] if srow else "red"]
        if rank > worst:
            worst, name = rank, s
    return name


def why_not(key, vrow, way):
    """Warum dieser Schlüssel/dieses Profil diese Sicht auf diesem Weg nicht öffnet -- oder None.

    Ein Satz für den, der es einrichtet (0.7.3): vorher hieß jeder Fall „kein
    Zugangsprofil“, auch wenn es das Profil gab und nur Ampel oder Zugang nicht passten."""
    if not vrow or vrow["name"] not in books.key_views(key):
        return "kein Zugangsprofil erlaubt diese Sicht."
    ways = books.view_ways(vrow)
    if way not in ways:
        # L12 (0.20): * und /pfad meinen Menschen, die sich AM GATEWAY anmelden
        # (Zugang „Anmeldung“), @<chat> und @* Menschen, die ein Chat herträgt
        # (Zugang „Plattform“). Wer für einen Chat * schrieb, bekam „Anmeldung
        # einschalten“ geraten -- der Chat braucht aber @<chat>.
        if way == "signin" and "platform" in ways:
            return (f"die Sicht {vrow['name']} nimmt Personen aus einem Chat (Zugang „Plattform“), "
                    "das Profil nennt aber Gruppen der Anmeldung am Gateway (* oder /pfad). Für einen "
                    "Chat im Profil @<chat-instanz> oder @* nennen; den Zugang „Anmeldung“ bei der Sicht "
                    "nur einschalten, wenn sich Personen selbst am Gateway anmelden (z. B. Claude Code).")
        if way == "platform":
            return (f"die Sicht {vrow['name']} hat den Zugang „Plattform“ nicht, den Personen aus einem "
                    "Chat (@<chat-instanz>, @*) brauchen -- auf der Betreiberseite bei der Sicht "
                    "einschalten.")
        return (f"die Sicht {vrow['name']} hat den Zugang {WAY_NAMES.get(way, way)} nicht -- "
                "auf der Betreiberseite bei der Sicht einschalten.")
    light = books.view_light(DB, vrow["name"])
    if books.LIGHT_RANK[light] > books.LIGHT_RANK.get(key["ceiling"], 1):
        # L13 (0.20): „die Ampel der Quelle senken“ war mehrdeutig. Jetzt mit
        # Farben, dem Server, der die Ampel bestimmt, und beiden Wegen.
        srv = worst_server(vrow["name"])
        lw, cw = LIGHT_DE.get(light, light), LIGHT_DE.get(key["ceiling"], key["ceiling"])
        return (f"die Ampel der Sicht {vrow['name']} ist {lw}"
                + (f" (Server {srv} ist {lw} eingestuft)" if srv else "")
                + f", das Zugangsprofil reicht nur bis {cw}. Entweder beim Profil „Ampel höchstens“ "
                f"auf {lw} stellen, oder -- wenn der Server harmloser ist als eingestuft -- seine "
                f"Ampel auf {cw} oder grün stellen (neue Quellen beginnen rot).")
    return None


def asked_light(value):
    """(Farbe oder '', Fehlersatz oder ''). Ein unbekannter Wert wird abgelehnt, nicht
    übergangen: wer eine Decke verlangt, soll nicht stumm ohne sie weiterarbeiten."""
    v = (value or "").strip().lower()
    if not v:
        return "", ""
    if v not in books.LIGHT_RANK:
        return "", f"{LIGHT_HEADER} kennt nur green, yellow oder red, nicht „{v[:20]}“."
    return v, ""


def light_refusal(asked, vrow):
    """Ein Satz, wenn die Sicht schlechter ist als die Ampel des Gesprächs -- sonst None.

    Die Person darf diese Sicht öffnen (die Prüfung von Schlüssel/Profil lief vorher),
    also verrät der Satz nichts, was sie nicht ohnehin sähe."""
    if not asked or not vrow:
        return None
    light = books.view_light(DB, vrow["name"])
    if books.LIGHT_RANK[light] <= books.LIGHT_RANK[asked]:
        return None
    srv = worst_server(vrow["name"])
    lw, aw = LIGHT_DE.get(light, light), LIGHT_DE.get(asked, asked)
    return (f"das Gespräch steht auf {aw}, die Sicht {vrow['name']} ist {lw}"
            + (f" (Server {srv})" if srv else "")
            + ". Im Chat die Ampel des Gesprächs bewusst ändern -- oder die Verwaltung um eine "
            f"Sicht bitten, die {aw} ist.")


def forbidden(sentence):
    return json.dumps({"jsonrpc": "2.0", "id": None,
                       "error": {"code": -32000, "message": "Angemeldet, aber " + sentence}},
                      ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------- Optik

STYLE = """<style>
  :root{--oaap-blue-950:#172554;--oaap-blue-900:#1e3a8a;--oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb;--oaap-blue-100:#dbeafe;--oaap-bg:#f4f6fa;--oaap-surface:#fff;
    --oaap-text:#1f2937;--oaap-muted:#6b7280;--oaap-border:#e5e7eb;--ok:#15803d;--err:#b91c1c}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;
       background:var(--oaap-bg);color:var(--oaap-text)}
  header.oaap{background:linear-gradient(135deg,var(--oaap-blue-900),var(--oaap-blue-950));
       color:#fff;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;padding:.6rem 1.2rem}
  .brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;color:#fff}
  .brand b{font-size:1.15rem;letter-spacing:.08em}
  .brand small{display:block;font-size:.62rem;opacity:.75}
  .userbox{margin-left:auto;font-size:.9rem;text-align:right;line-height:1.2}
  .userbox small{opacity:.75}
  /* Reiter (0.9.0): eine Seite je Aufgabe statt einer langen Liste. */
  nav.tabs{display:flex;gap:.1rem;overflow-x:auto;background:var(--oaap-surface);
       border-bottom:1px solid var(--oaap-border);padding:0 1.2rem;position:sticky;top:0;z-index:5}
  nav.tabs a{padding:.85rem .9rem .7rem;text-decoration:none;color:var(--oaap-muted);
       border-bottom:3px solid transparent;white-space:nowrap;font-size:.93rem}
  nav.tabs a:hover{color:var(--oaap-text)}
  nav.tabs a.on{color:var(--oaap-blue-900);border-bottom-color:var(--oaap-blue-600);font-weight:600}
  .chead{display:flex;justify-content:space-between;align-items:center;gap:.6rem;flex-wrap:wrap;
       margin-bottom:.6rem}
  .chead h2{margin:0}
  details.add{margin-top:1rem;border-top:1px solid var(--oaap-border);padding-top:1rem}
  details.add>summary{display:inline-block;list-style:none;padding:.5rem 1.1rem;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;font-size:.92rem;margin:0}
  details.add>summary::-webkit-details-marker{display:none}
  details.add[open]>summary{background:#f3f4f6;color:#374151;margin-bottom:1rem}
  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(11rem,1fr));gap:.8rem;margin-bottom:1.2rem}
  a.tile{background:var(--oaap-surface);border:1px solid var(--oaap-border);border-radius:.6rem;
       padding:1rem 1.1rem;text-decoration:none;color:var(--oaap-text);display:block}
  a.tile:hover{border-color:var(--oaap-blue-600)}
  a.tile b{display:block;font-size:1.7rem;line-height:1.1;margin-bottom:.2rem}
  a.tile span{color:var(--oaap-muted);font-size:.85rem}
  ul.todo{margin:0;padding:0;list-style:none}
  ul.todo li{padding:.55rem 0;border-bottom:1px solid var(--oaap-border);display:flex;gap:.6rem;
       align-items:baseline;flex-wrap:wrap}
  ul.todo li:last-child{border-bottom:0}
  ol.steps{margin:.2rem 0 0;padding-left:1.2rem}ol.steps li{margin:.35rem 0}
  .card.sources{display:flex;gap:1rem;align-items:center;justify-content:space-between;flex-wrap:wrap}
  .card.sources p{margin:0;max-width:48rem}
  main{max-width:68rem;margin:1.6rem auto;padding:0 1.2rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem} h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);border-radius:.6rem;
       padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .card.secretbox{border-color:#86efac;background:#f0fdf4}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;background:var(--oaap-blue-100);
       color:var(--oaap-blue-900);white-space:nowrap}
  .badge.green{background:#dcfce7;color:#166534}.badge.yellow{background:#fef3c7;color:#92400e}
  .badge.red,.badge.err{background:#fee2e2;color:#991b1b}.badge.off{background:#f3f4f6;color:#6b7280}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  button.quiet{background:#f3f4f6;color:#374151;min-height:36px;padding:.35rem .9rem;font-size:.85rem}
  button.quiet:hover{background:#e5e7eb}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  details summary{cursor:pointer;color:var(--oaap-blue-700);font-size:.85rem;margin:.1rem 0 .5rem}
  .err{color:var(--err)}.muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:top;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  code{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
  pre{background:#0f172a;color:#e2e8f0;padding:.9rem 1rem;border-radius:.5rem;overflow-x:auto;
       font-size:.82rem;white-space:pre-wrap;word-break:break-word}
  form.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.8rem 1rem;
       align-items:end}
  form.inline{display:inline}
  label{display:block;font-size:.82rem;color:var(--oaap-muted);margin-bottom:.25rem}
  input[type=text],input[type=number],input[type=password],select{width:100%;padding:.5rem .6rem;
       border-radius:.4rem;border:1px solid var(--oaap-border);font-size:.95rem;min-height:44px;
       background:#fff}
  .side{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
  .scroll{overflow-x:auto}td.num{text-align:right;white-space:nowrap}
  .legend{display:flex;flex-wrap:wrap;gap:.3rem 1rem;margin:0 0 .5rem;font-size:.85rem}
  .lg{display:inline-flex;align-items:center;gap:.35rem}
  .lg i{display:inline-block;width:.8rem;height:.8rem;border-radius:2px}
  svg.chart{width:100%;height:auto;display:block} svg.chart .ax{font-size:11px;fill:var(--oaap-muted)}
  .hbar{display:grid;grid-template-columns:minmax(8rem,2fr) 3fr minmax(6rem,auto);gap:.6rem;
       align-items:center;padding:.3rem 0;font-size:.9rem}
  .hl{overflow:hidden;text-overflow:ellipsis}.hl code{white-space:nowrap;font-size:.78rem}
  .ht{background:#f3f4f6;border-radius:4px;height:.8rem;overflow:hidden}
  .ht i{display:block;height:100%;border-radius:0 4px 4px 0}
  .hv{text-align:right;white-space:nowrap}.hv small{color:var(--oaap-muted)}
  footer.oaap{max-width:68rem;margin:2rem auto 1.2rem;padding:0 1.2rem;color:var(--oaap-muted);
       font-size:.8rem}
  @media (max-width:640px){.side{grid-template-columns:1fr}.hbar{grid-template-columns:1fr 1fr}}
</style>"""

LOGO_SVG = ('<svg viewBox="0 0 100 100" width="34" height="34" aria-hidden="true">'
            '<polygon points="50,6 88,28 88,72 50,94 12,72 12,28" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round"/><circle cx="50" cy="50" r="13" fill="#fff"/>'
            '</svg>')

STATE_WORDS = {"active": ("green", "freigegeben"), "suspended": ("yellow", "gesperrt: Text geändert"),
               "missing": ("red", "fehlt beim Server")}
LIGHT_WORDS = {"green": "grün — Daten verlassen das Unternehmen nicht",
               "yellow": "gelb — unter verbindlicher Zusage",
               "red": "rot — Daten können abfließen"}


TABS = (("start", "./", "Übersicht"), ("server", "./?tab=server", "Server"),
        ("sichten", "./?tab=sichten", "Sichten"),
        ("systeme", "./?tab=systeme", "Systemlandschaft"),          # 0.32, RFC-0114
        ("zugaenge", "./?tab=zugaenge", "Schlüssel &amp; Profile"),
        ("anmeldung", "./?tab=anmeldung", "Anmeldung"), ("aufrufe", "./?tab=aufrufe", "Aufrufe &amp; Prüfspur"),
        ("auswertung", "auswertung", "Auswertung"),
        ("assistenten", "assistenten", "Assistenten"),
        ("vorlagen", "vorlagen", "Vorlagen"),          # 0.28.0, RFC-0107
        # 0.15.2: die eigene Seite auch für die Verwaltung erreichbar -- die
        # Kachel führt eine Admin-Person auf die Verwaltung, nicht auf /me.
        ("me", "me", "Meine Aufrufe"))


def page(title, body, user, roles, active="start"):
    nav = "".join(f'<a href="{href}"{" class=on" if key == active else ""}>{label}</a>'
                  for key, href, label in TABS)
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — OAAP MCP-Gateway</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP MCP-GATEWAY</b><small>Eine Sicht, ein Schlüssel, freigegebene Werkzeuge</small></span></a>
  <div class="userbox">{esc(user)}<br><small>{esc(roles)}</small></div>
</header>
<nav class="tabs" aria-label="Bereiche">{nav}</nav>
<main>
{body}</main>
<footer class="oaap">OAAP MCP-Gateway {VERSION} — zählt Aufrufe, speichert keine Argumente
und keine Ergebnisse</footer></html>"""


def badge(cls, text):
    return f'<span class="badge {esc(cls)}">{esc(text)}</span>'


def options(values, current, words=None):
    return "".join(f'<option value="{esc(v)}"{" selected" if v == current else ""}>'
                   f'{esc((words or {}).get(v, v))}</option>' for v in values)


def fmt_bytes(value):
    return gcharts.fmt_bytes(value or 0)


def context_line(row):
    bits = [f"{n}: {row[n]}" for n in ("owner", "account", "project", "cost_center", "application")
            if row[n] and not (n == "account" and row[n] == "default")]
    if row["labels"]:
        bits.append(books.labels_text(row["labels"]))
    return " · ".join(bits) or "—"


CONTEXT_LABELS = {"owner": "Verantwortlich", "account": "Konto", "project": "Projekt",
                  "cost_center": "Kostenstelle", "application": "Anwendung",
                  "labels": "Etiketten (name=wert, …)"}


def context_inputs(row=None):
    out = []
    for name in books.CONTEXT_FIELDS:
        value = ""
        if row is not None:
            value = books.labels_text(row[name]) if name == "labels" else row[name]
        out.append(f'<div><label>{esc(CONTEXT_LABELS[name])}</label>'
                   f'<input type="text" name="{name}" value="{esc(value or "")}"></div>')
    return "".join(out)


def endpoint(host, view):
    return f"https://{host}/mcp/{view}"


def sources_card():
    """Die Quellen der Plattform (0.7.3): wann zuletzt abgeglichen, und ein Knopf für jetzt.
    Seit 0.9.0 ein eigener Kasten -- in der Server-Tabelle hat ihn niemand gefunden."""
    if not SOURCES_URL:
        return ""
    at = SOURCES_STATE["at"] or "noch nie"
    err = (f'<br><span class="err">{esc(SOURCES_STATE["error"])}</span>' if SOURCES_STATE["error"] else "")
    return (f'<div class="card sources"><p><b>Quellen der Plattform</b><br><span class="muted">Apps des '
            f'Mandanten mit MCP-Tür werden alle 15 Minuten abgeglichen, zuletzt {esc(at)}. Eine neue Quelle '
            f'startet mit Ampel rot.</span>{err}</p><form class="inline" method="post" action="source-sync">'
            f'<button>Quellen jetzt einlesen</button></form></div>')


def key_problems(k):
    """Warum ein gültiger Schlüssel oder ein Profil an einer seiner Sichten scheitert -- ein Satz je Sicht."""
    out = []
    if k["revoked"]:
        return out
    ways = (["platform"] if any(g.startswith("@") for g in books.profile_groups(k)) else []) + \
           (["signin"] if any(not g.startswith("@") for g in books.profile_groups(k)) else []) \
        if k["kind"] == "profile" else ["key"]
    for vn in books.key_views(k):
        vr = books.view_row(DB, vn)
        for w in ways:
            r = why_not(k, vr, w) if vr else f"die Sicht {vn} gibt es nicht mehr."
            if r:
                out.append(f"{vn}: {r}")
    return out


def view_states(name):
    states = {}
    for item in books.items(DB, name):
        st = books.item_state(DB, item)
        states[st] = states.get(st, 0) + 1
    return states


def add_box(label, inner):
    """Ein Formular zum Anlegen, eingeklappt hinter einem Knopf."""
    return f'<details class="add"><summary>{label}</summary>{inner}</details>'


def start_tab(host):
    servers, views, keys = list(books.servers(DB)), list(books.views(DB)), list(books.keys(DB))
    lights = {}
    for x in servers:
        lights[x["light"]] = lights.get(x["light"], 0) + 1
    light_text = " · ".join(f"{lights[w]} {t}" for w, t in (("green", "grün"), ("yellow", "gelb"), ("red", "rot")) if lights.get(w))
    live = [k for k in keys if not k["revoked"]]
    calls = sum(t["calls"] for t in books.totals(DB).values())
    tiles = (f'<div class="tiles">'
             f'<a class="tile" href="./?tab=server"><b>{len(servers)}</b><span>Server{" · " + esc(light_text) if light_text else ""}</span></a>'
             f'<a class="tile" href="./?tab=sichten"><b>{len(views)}</b><span>Sichten (MCP-Endpunkte)</span></a>'
             f'<a class="tile" href="./?tab=zugaenge"><b>{len(live)}</b><span>gültige Schlüssel und Profile</span></a>'
             f'<a class="tile" href="./?tab=aufrufe"><b>{calls}</b><span>Aufrufe gezählt</span></a></div>')
    todo = []
    if SOURCES_URL and SOURCES_STATE["error"]:
        todo.append(("Quellen", f'Abgleich gescheitert: {esc(SOURCES_STATE["error"])}', "./?tab=server"))
    for x in servers:
        if x["gone"]:
            todo.append(("Server", f'<b>{esc(x["name"])}</b> steht seit {esc(x["gone"])} nicht mehr in der Quellenliste',
                         f'server?name={quote(x["name"])}'))
        elif x["last_error"]:
            todo.append(("Server", f'<b>{esc(x["name"])}</b>: Katalog nicht gelesen — {esc(x["last_error"])}',
                         f'server?name={quote(x["name"])}'))
        elif not x["last_read"]:
            todo.append(("Server", f'<b>{esc(x["name"])}</b>: Katalog noch nie gelesen',
                         f'server?name={quote(x["name"])}'))
    for v in views:
        st = view_states(v["name"])
        if st.get("suspended") or st.get("missing"):
            bits = ", ".join(f"{st[k]} {STATE_WORDS[k][1]}" for k in ("suspended", "missing") if st.get(k))
            todo.append(("Sicht", f'<b>{esc(v["name"])}</b>: {esc(bits)}', f'view?name={quote(v["name"])}'))
        elif not st:
            todo.append(("Sicht", f'<b>{esc(v["name"])}</b> ist leer — noch nichts freigegeben', f'view?name={quote(v["name"])}'))
    for k in live:
        for p in key_problems(k):
            todo.append(("Zugang", f'<b>{esc(k["label"])}</b> — {esc(p)}', "./?tab=zugaenge"))
    rows = "".join(f'<li>{badge("yellow", what)} <span>{text}</span> <a href="{href}">ansehen</a></li>'
                   for what, text, href in todo)
    todo_card = (f'<div class="card{" attention" if todo else ""}"><h2>Zu tun</h2>'
                 + (f'<ul class="todo">{rows}</ul>' if todo else '<p class="muted">Nichts zu tun.</p>') + '</div>')
    guide = """<div class="card"><h2>So hängt es zusammen</h2><ol class="steps">
<li><b>Server</b> — ein MCP-Server, den das Gateway erreicht (URL, Destination oder Quelle der Plattform), mit Ampel.</li>
<li><b>Sicht</b> — ein MCP-Endpunkt; du gibst darin einzelne Werkzeuge der Server frei. Neues erscheint nie von selbst.</li>
<li><b>Schlüssel oder Zugangsprofil</b> — wer die Sicht benutzen darf: ein Agent mit Schlüssel, angemeldete Personen
per Gruppe, oder ein Chat der Plattform (<code>@&lt;chat&gt;</code>).</li></ol></div>"""
    return tiles + todo_card + sources_card() + guide


def servers_tab(host):
    rows = []
    for s in books.servers(DB):
        g = GUARDS.get(s["name"])
        st = g.state() if g else None
        load = (f'{st["inflight"]} laufend, {st["waiting"]} wartend'
                + (f', Pause {st["open_for"]} s' if st["open_for"] else "")) if st else "—"
        rows.append(f'<tr><td><a href="server?name={quote(s["name"])}"><b>{esc(s["name"])}</b></a>'
                    + (f'<br><span class=hint>Quelle der Plattform</span>' if s["source"] else "")
                    + (f'<br><span class=err>nicht mehr in der Quellenliste seit {esc(s["gone"])}</span>'
                       if s["gone"] else "")
                    + f'</td><td><code>{esc(s["address"])}</code></td><td>{badge(s["light"], s["light"])}{" " + badge("yellow", "fremder Inhalt") if s["untrusted"] else ""}</td>'
                    f'<td>{esc(s["version"] or "—")}</td>'
                    f'<td>{esc(s["last_read"] or "nie")}'
                    f'{"<br><span class=err>" + esc(s["last_error"]) + "</span>" if s["last_error"] else ""}'
                    f'</td><td>{esc(load)}</td></tr>')
    return sources_card() + f'''<div class="card"><div class="chead"><h2>Server</h2></div>
<p class="hint">MCP-Server mit Streamable HTTP. Adresse als URL oder <code>destination:&lt;name&gt;</code>
— dann setzt die Plattform die Zugangsdaten ein, und das Gateway hält kein Geheimnis.</p>
<div class="scroll"><table><tr><th>Name</th><th>Adresse</th><th>Ampel</th><th>Protokoll</th>
<th>Katalog gelesen</th><th>Last</th></tr>{"".join(rows) or '<tr><td colspan=6 class=muted>Noch kein Server.</td></tr>'}</table></div>
{add_box("+ Server anlegen", server_form())}</div>'''


def views_tab(host):
    vrows = []
    for v in books.views(DB):
        states = view_states(v["name"])
        summary = ", ".join(f"{n} {STATE_WORDS[s][1]}" for s, n in sorted(states.items())) or "leer"
        light = books.view_light(DB, v["name"])
        vrows.append(f'<tr><td><a href="view?name={quote(v["name"])}"><b>{esc(v["name"])}</b></a></td>'
                     f'<td>{esc(", ".join(books.view_servers(DB, v["name"])) or "—")}<br>{badge(light, light)}</td>'
                     f'<td>bis {esc(books.EFFECT_WORDS[v["max_effect"]])}<br>'
                     f'<span class="muted">{esc(books.WAY_WORDS.get(v["ways"], v["ways"]))}</span></td>'
                     f'<td>{esc(summary)}</td><td><code>{esc(endpoint(host, v["name"]))}</code></td></tr>')
    return f'''<div class="card"><div class="chead"><h2>Sichten</h2></div>
<p class="hint">Eine Sicht ist ein MCP-Endpunkt. Sie bündelt, was hier freigegeben ist — aus einem
Server oder mehreren. Ein neues Tool eines Servers erscheint nirgends von selbst.</p>
<div class="scroll"><table><tr><th>Sicht</th><th>Server, Ampel</th><th>Höchste Klasse</th><th>Einträge</th>
<th>Endpunkt</th></tr>{"".join(vrows) or '<tr><td colspan=5 class=muted>Noch keine Sicht.</td></tr>'}</table></div>
<details class="add"><summary>+ Sicht anlegen</summary>
<form class="grid" method="post" action="view-save">
<div><label>Name (Pfad)</label><input type="text" name="name" required></div>
<div><label>Höchste Wirkungsklasse</label><select name="max_effect">{options(books.EFFECTS, "read", books.EFFECT_WORDS)}</select></div>
<div><label>Zugänge</label><select name="ways">{options(books.WAYS, "key", books.WAY_WORDS)}</select></div>
<div><button>Anlegen</button></div></form></details></div>''' + views_tree()


def keys_tab(host):
    totals = books.totals(DB)
    krows = []
    for k in books.keys(DB):
        t = totals.get(k["id"])
        used = f'{t["calls"]} Aufrufe' if t else "—"
        state = badge("off", "widerrufen") if k["revoked"] else badge("green", "gültig")
        action = "" if k["revoked"] else (
            f'<form class="inline" method="post" action="revoke"><input type="hidden" name="label" '
            f'value="{esc(k["label"])}"><button class="quiet">Widerrufen</button></form>')
        if k["kind"] == "profile":
            state += " " + badge("yellow", "Zugangsprofil") + f'<br><span class="muted">Gruppen: ' \
                f'{esc(", ".join(books.profile_groups(k)))}</span>'
        if not k["revoked"]:
            for p in key_problems(k):
                state += f'<br><span class="err">{esc(p)}</span>'
            action += (f'<details><summary class="quiet">Ändern</summary><form method="post" action="key-limits">'
                       f'<input type="hidden" name="label" value="{esc(k["label"])}">'
                       f'<label>Sichten (Komma)</label><input type="text" name="views" value="{esc(k["views"])}" required>'
                       f'<label>Höchste Wirkungsklasse</label><select name="max_effect">'
                       f'{options(books.EFFECTS, k["max_effect"], books.EFFECT_WORDS)}</select>'
                       f'<label>Ampel höchstens</label><select name="ceiling">{options(books.LIGHTS, k["ceiling"])}</select>'
                       + (f'<label>Gruppen (Komma): * oder /pfad = Anmeldung am Gateway; '
                          f'@&lt;chat&gt; oder @* = Personen aus einem Chat</label>'
                          f'<input type="text" name="groups" value="{esc(", ".join(books.profile_groups(k)))}" required>'
                          if k["kind"] == "profile" else "")
                       + f'<button>Speichern</button></form></details>')
        krows.append(f'<tr><td><b>{esc(k["label"])}</b><br>{state}</td>'
                     f'<td>{esc(", ".join(books.key_views(k)))}</td>'
                     f'<td>bis {esc(books.EFFECT_WORDS.get(k["max_effect"], k["max_effect"]))}<br>'
                     f'{badge(k["ceiling"], "Ampel bis " + k["ceiling"])}</td>'
                     f'<td>{esc(used)}{" / Budget " + str(k["budget_calls"]) if k["budget_calls"] else ""}</td>'
                     f'<td class="muted">{esc(context_line(k))}</td><td>{action}</td></tr>')
    vnames = [v["name"] for v in books.views(DB)]
    return f'''<div class="card"><div class="chead"><h2>Schlüssel und Zugangsprofile</h2></div>
<p class="hint">Ein Schlüssel ist ein Geheimnis für Agenten und Pipelines. Ein Zugangsprofil hat keinen Wert,
es gilt für Personen — und die kommen auf zwei Wegen:</p>
<ul class="hint">
<li><b>Selbst am Gateway angemeldet</b> (z. B. Claude Code mit Anmeldung): Gruppen <code>*</code> für alle,
die sich anmelden dürfen, oder volle Pfade wie <code>/sap-dev</code>. Die Sicht braucht den Zugang „Anmeldung“.</li>
<li><b>Von einem Chat der Plattform hergetragen</b> (Verfahren A): <code>@&lt;chat-instanz&gt;</code>, etwa
<code>@bec-chat</code>, oder <code>@*</code> für jeden Chat des Mandanten. Die Sicht braucht den Zugang „Plattform“.
Ein <code>*</code> gilt für einen Chat <b>nicht</b>.</li>
</ul>
<div class="scroll"><table><tr><th>Etikett</th><th>Sichten</th><th>Grenzen</th><th>Verbrauch</th>
<th>Kontext</th><th></th></tr>{"".join(krows) or '<tr><td colspan=6 class=muted>Noch kein Schlüssel.</td></tr>'}</table></div>
<details class="add"><summary>+ Schlüssel ausstellen oder Zugangsprofil anlegen</summary>
<form class="grid" method="post" action="issue">
<div><label>Etikett</label><input type="text" name="label" required></div>
<div><label>Art</label><select name="kind">{options(("key", "profile"), "key", {"key": "Schlüssel", "profile": "Zugangsprofil"})}</select></div>
<div><label>Gruppen (nur Zugangsprofil, Komma): * oder /pfad, für einen Chat @&lt;chat&gt; oder @*</label><input type="text" name="groups"></div>
<div><label>Sichten (Komma) — bekannt: {esc(", ".join(vnames) or "keine")}</label>
<input type="text" name="views" required></div>
<div><label>Höchste Wirkungsklasse</label><select name="max_effect">{options(books.EFFECTS, "read", books.EFFECT_WORDS)}</select></div>
<div><label>Ampel höchstens</label><select name="ceiling">{options(books.LIGHTS, "yellow")}</select></div>
<div><label>Budget (Aufrufe, 0 = ohne)</label><input type="number" name="budget" value="0" min="0"></div>
<div><label>Rate je Minute (0 = {DEFAULT_RATE})</label><input type="number" name="rate" value="0" min="0"></div>
{context_inputs()}<div><button>Ausstellen</button></div></form></details></div>'''


# ------------------------------------------------------------------ /me (RFC-0095)
# Die zweite Seite des Gateways: für die Person, deren Aufrufe hindurchgehen.
# Stufe 1 liest nur -- die eigenen Messzeilen der letzten 30 Tage, Fehler in
# Worten. Die Sätze entstehen aus den CODES der Zeile (Ausgang, Grund, Status,
# Tür), nie aus gespeichertem Text: Den gibt es nicht (Spec §10).

ME_DAYS = 30
DEFAULT_DOOR = {"refused": "gateway", "limited": "gateway", "upstream_error": "server",
                "tool_error": "server"}
DOOR_WORDS = {"gateway": "das Gateway", "platform": "die Plattform",
              "destination": "die Destination oder das Zielsystem", "server": "der Server"}
ME_OUTCOME_WORDS = {"ok": "ok", "refused": "abgelehnt", "limited": "begrenzt",
                 "upstream_error": "Serverfehler", "tool_error": "Werkzeugfehler"}
HINT_GATEWAY = "Frag die Verwaltung des MCP-Gateways; nenne Zeit und Sicht."
HINT_TENANT = "Frag die Verwaltung des Mandanten; nenne Zeit und Sicht."
HINT_LATER = "Versuch es später noch einmal; hält es an, sag der Verwaltung Zeit und Sicht."
REASON_SENTENCES = {
    ("refused", "suspended"): ("Der Eintrag ist gesperrt, bis die Verwaltung einen geänderten "
                               "Text bestätigt.", HINT_GATEWAY),
    ("refused", "effect"): ("Der Eintrag liegt über der Wirkungsklasse, die dein Zugang oder die "
                            "Sicht erlaubt.", HINT_GATEWAY),
    ("refused", "untrusted"): ("Der Eintrag schreibt; die Sicht enthält Webinhalt und liefert "
                               "deshalb nur Lesendes aus.", HINT_GATEWAY),
    ("refused", "unknown_tool"): ("Dieses Werkzeug gibt es in der Sicht nicht (mehr).", HINT_GATEWAY),
    ("refused", "unknown_prompt"): ("Diesen Prompt gibt es in der Sicht nicht (mehr).", HINT_GATEWAY),
    ("refused", "unknown_resource"): ("Diese Resource gibt es in der Sicht nicht.", HINT_GATEWAY),
    ("refused", "ambiguous"): ("Die Adresse passt zu mehreren Vorlagen der Sicht.", HINT_GATEWAY),
    ("refused", "arguments"): ("Die Argumente passten nicht zu dem, was die Sicht erlaubt (feste "
                               "Argumente, Grenzen).", "Formuliere die Anfrage anders; hält es an, "
                               + HINT_GATEWAY[0].lower() + HINT_GATEWAY[1:]),
    ("refused", "ui_changed"): ("Die Ansicht hat sich seit der Freigabe geändert.", HINT_GATEWAY),
    ("refused", "exchange"): ("Der Tausch deiner Anmeldung beim Anmeldedienst ist nicht gelungen.",
                              HINT_TENANT),
    ("refused", "personal"): ("Für diesen Server hast du noch keinen Zugang hinterlegt.",
                              "Trag ihn unten unter „Meine Zugangsdaten“ ein."),
    ("limited", "budget"): ("Das Budget des Zugangs ist aufgebraucht.", HINT_GATEWAY),
    ("limited", "rate"): ("Zu viele Aufrufe in kurzer Zeit.", "Warte eine Minute."),
    ("limited", "queue"): ("Der Server war ausgelastet.", HINT_LATER),
    ("limited", "breaker"): ("Der Server pausiert nach wiederholten Fehlern.", HINT_LATER),
    ("limited", "size"): ("Die Antwort war größer als erlaubt und wurde nicht weitergereicht.",
                          HINT_GATEWAY),
    ("tool_error", ""): ("Das Werkzeug hat einen Fehler gemeldet (sein Text wird nicht "
                         "gespeichert).", "Sieh in der Antwort im Chat nach oder versuch es "
                                         "mit anderen Angaben."),
}
UPSTREAM_WORDS = {"connect": "keine Verbindung", "timeout": "keine Antwort in der Zeit",
                  "http": "HTTP-Fehler", "version": "fremde Protokollversion",
                  "protocol": "kein gültiges MCP", "session": "Sitzung verloren",
                  "jsonrpc": "Fehler im Protokoll", "address": "Adresse nicht nutzbar",
                  "redirect": "leitet weiter"}


def call_sentence(row):
    """(Satz, Abhilfe, Tür) für eine Messzeile -- nur aus ihren Codes."""
    outcome, reason = row["outcome"], row["reason"] or ""
    status = row["status"] if "status" in row.keys() else 0
    door = (row["door"] if "door" in row.keys() else "") or DEFAULT_DOOR.get(outcome, "")
    code = f" (HTTP {status})" if status else ""
    if outcome == "ok":
        return "", "", ""
    if outcome == "refused" and reason in ("destination", "platform") and door in (
            "destination", "platform"):
        who = DOOR_WORDS[door]
        return (f"{who[0].upper() + who[1:]} hat abgelehnt{code}.", HINT_TENANT, door)
    raw_door = row["door"] if "door" in row.keys() else ""
    if outcome == "refused" and reason == "destination" and not raw_door:
        # Vor 0.15 hieß „destination“ beides: das Gateway trug die Person nicht
        # (keine Zusicherung) ODER die Destination lehnte ab. Nicht raten (0.15.1).
        return ("Auf dem Weg zum Zielsystem abgelehnt: von der Destination, vom Zielsystem "
                "oder weil keine Zusicherung der Plattform mitkam (Zeile von vor 0.15, ohne "
                "genauere Angabe).", HINT_TENANT, "")
    if outcome == "refused" and reason == "destination":
        return ("Das Gateway konnte dich nicht bis zur Destination tragen (keine Zusicherung "
                "der Plattform in diesem Aufruf).", HINT_TENANT, door)
    if outcome == "refused" and reason == "credential":
        return (f"Der Server hat deinen Zugang abgelehnt{code}.",
                "Erneuere ihn unten unter „Meine Zugangsdaten“.", door)
    if outcome == "upstream_error":
        word = UPSTREAM_WORDS.get(reason, "")
        return (f"Der Server war nicht erreichbar oder fehlerhaft"
                f"{' – ' + word if word else ''}{code}.", HINT_LATER, door)
    hit = REASON_SENTENCES.get((outcome, reason)) or REASON_SENTENCES.get((outcome, ""))
    if hit:
        return hit[0], hit[1], door
    return (f"{ME_OUTCOME_WORDS.get(outcome, outcome)}{code}.", HINT_GATEWAY, door)


def me_call(row):
    sentence, hint, door = call_sentence(row)
    return {"time": row["time"], "view": row["view"], "kind": row["kind"], "name": row["tool"],
            "client": row["client"], "assistant": row["assistant"], "procedure": row["procedure"],
            "outcome": row["outcome"], "reason": row["reason"], "status": row["status"],
            "door": door, "ms": row["ms"], "sentence": sentence, "hint": hint}


def me_filter(query, uid, also=()):
    """Filter aus der Anfrage. „Nur Probleme“ ist voreingestellt, wenn es in den
    letzten 7 Tagen welche gab (RFC-0095 §3)."""
    raw = query.get("problems", "")
    if raw in ("0", "1"):
        problems = raw == "1"
    else:
        problems = bool(books.person_calls(DB, "platform", uid, days=7, problems=True, limit=1,
                                           also=also))
    view = query.get("view", "").strip()
    day = query.get("day", "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        day = ""
    return {"problems": problems, "view": view[:100], "day": day}


ME_REALM_NOTE = ("Aufrufe, bei denen du dich selbst am Anmeldedienst angemeldet hast (etwa aus "
                 "Claude Code), erscheinen hier, sobald die Plattform dem Gateway deine Kennung "
                 "aus dem Anmeldedienst mitgibt (RFC-0095 D2). Aufrufe mit einem API-Schlüssel "
                 "haben keine Person und stehen nie hier.")


def me_doc(uid, query, also=()):
    flt = me_filter(query, uid, also)
    rows = books.person_calls(DB, "platform", uid, days=ME_DAYS, also=also, **flt)
    return {"v": 1, "days": ME_DAYS, "filter": flt, "calls": [me_call(r) for r in rows],
            "note": ME_LINKED_NOTE if also else ME_REALM_NOTE}


ME_LINKED_NOTE = ("Dabei sind auch deine Aufrufe mit eigener Anmeldung am Anmeldedienst (etwa aus "
                  "Claude Code): Die Plattform hat dem Gateway gesagt, dass das deine Kennung dort "
                  "ist. Aufrufe mit einem API-Schlüssel haben keine Person und stehen nie hier.")


def me_readiness(uid):
    """/me/v1/readiness: deine Server mit Verfahren U und ihr Zustand (RFC-0095 §5)."""
    person = {"iss": "platform", "sub": uid}
    rows = []
    for s in books.servers(DB):
        if s["auth_kind"] == "personal":
            spec = books.personal_spec(s)
            rows.append({"server": s["name"], "state": server_readiness(person, s),
                         "hint": spec.get("hint", ""), "link": spec.get("link", "")})
    return {"v": 1, "me": me_url(), "servers": rows,
            "ready": all(r["state"] == "ready" for r in rows)}


def me_catalog_read(uid, server, assertion=""):
    """L29 (0.18.1): die ERSTE Werkzeugliste eines Servers mit Verfahren U, gelesen mit dem
    Zugang dieser Person. Nur solange sie noch nie gelesen wurde -- ein Zugang mit
    weniger Rechten soll die Liste der anderen nie verkürzen. Freigeben bleibt Sache der
    Verwaltung; die Prüfspur nennt die Person mit Kennung.

    0.33.0 (bec, 20.11.): dasselbe für Verfahren P -- die Person liest als sie selbst,
    mit ihrem Benutzer im Zielsystem, über die Zusicherung, die die Plattform ihr auf
    dieser Seite mitgibt. Wer SAP-Rechte hat, braucht dafür keine Verwaltungsrolle."""
    srow = books.server_row(DB, server)
    if not srow or srow["auth_kind"] not in ("personal", "propagate"):
        return (f"Der Server „{server}“ nimmt weder einen persönlichen Zugang noch trägt er "
                "die Person ins Zielsystem.")
    if srow["last_read"]:
        return (f"Die Werkzeugliste von {server} ist schon gelesen; neu lesen kann sie die "
                "Verwaltung.")
    if srow["auth_kind"] == "propagate":
        if not assertion:
            return (f"Für {server} fehlt die Zusicherung der Plattform -- diese Seite bitte "
                    "über das Portal öffnen („Meine Werkzeuge“).")
        person = {"iss": "platform", "sub": uid, "assertion": assertion, "procedure": "A"}
    else:
        if books.personal_row(DB, "platform", uid, server) is None:
            return f"Für {server} hast du noch keinen Zugang hinterlegt."
        person = {"iss": "platform", "sub": uid, "procedure": "U"}
    ok_, text = read_catalog(server, person)
    books.log(DB, f"person:{uid}", "catalog.first", server, "gelesen" if ok_ else "nicht gelesen")
    if ok_:
        return (f"Werkzeugliste von {server} gelesen. Die Verwaltung gibt die Werkzeuge in "
                "einer Sicht frei; bis dahin kannst du sie noch nicht benutzen.")
    return text


def me_first_read_card():
    """0.33.0: Server mit Verfahren P, deren Katalog noch nie gelesen wurde. Jede Person
    mit eigenem Benutzer im Zielsystem darf die ERSTE Liste lesen -- als sie selbst."""
    servers = [s for s in books.servers(DB) if s["auth_kind"] == "propagate" and not s["last_read"]]
    if not servers:
        return ""
    rows = "".join(
        f'<tr><td><b>{esc(s["name"])}</b>'
        + (f'<br><span class="err">{esc(s["last_error"])}</span>' if s["last_error"] else "")
        + f'</td><td><form method="post" action="me/catalog-read" style="display:inline">'
        f'<input type="hidden" name="server" value="{esc(s["name"])}">'
        f'<button>Werkzeugliste für die Verwaltung lesen</button></form></td></tr>'
        for s in servers)
    return f'''<div class="card"><h2>Werkzeuglisten, die noch niemand gelesen hat</h2>
<p class="hint">Diese Server tragen dich mit deinem eigenen Benutzer ins Zielsystem, etwa SAP. Ihre
Werkzeugliste kann nur eine Person lesen, die dort einen Benutzer hat -- als sie selbst. Hast du einen,
lies sie hier einmal für die Verwaltung. Gelesen ist noch nicht freigegeben: Welche Werkzeuge der Chat
bekommt, entscheidet die Verwaltung. Danach liest nur noch sie neu.</p>
<div class="scroll"><table><tr><th>Server</th><th></th></tr>{rows}</table></div></div>'''


def me_credentials_card(uid, notice=""):
    """„Meine Zugangsdaten“ (RFC-0095 §4.2): je Server mit Verfahren U eintragen,
    ersetzen, löschen. Nur schreiben: Gezeigt wird wann, nie der Wert."""
    servers = [s for s in books.servers(DB) if s["auth_kind"] == "personal"]
    if not servers:
        return ""
    mine = books.personal_of_person(DB, "platform", uid)
    rows = []
    for s in servers:
        spec = books.personal_spec(s)
        have = mine.get(s["name"])
        rejected = have and server_readiness({"iss": "platform", "sub": uid}, s) == "credential_rejected"
        state = (badge("red", "vom Server abgelehnt – erneuern") + " " if rejected else "") + (
                 badge("green", "hinterlegt") + f' <span class="muted">am {esc(have["stored"][:10])}'
                 + (f', zuletzt benutzt {esc(have["last_used"].replace("T", " ")[:16])} UTC'
                    if have["last_used"] else ", noch nicht benutzt") + "</span>"
                 if have else badge("yellow", "noch nicht eingerichtet"))
        how = esc(spec.get("hint") or "")
        if spec.get("link"):
            how += (" " if how else "") + (f'<a href="{esc(spec["link"])}" target="_blank" '
                                           f'rel="noopener noreferrer">Wo du ihn bekommst</a>')
        # L29 (0.18.1): Noch nie gelesen und die Person hat einen Zugang -- sie darf die
        # ERSTE Werkzeugliste lesen. Danach ist das wieder Sache der Verwaltung, damit ein
        # Zugang mit weniger Rechten die Liste der anderen nie verkürzt.
        first = (f'<form method="post" action="me/catalog-read" style="display:inline">'
                 f'<input type="hidden" name="server" value="{esc(s["name"])}">'
                 f'<button class="quiet">Werkzeugliste für die Verwaltung lesen</button></form> '
                 if have and not s["last_read"] else "")
        drop = first + (f'<form method="post" action="me/credential-delete" style="display:inline">'
                f'<input type="hidden" name="server" value="{esc(s["name"])}">'
                f'<button class="quiet">Löschen</button></form>' if have else "")
        user_in = ('<input type="text" name="user" autocomplete="username" required '
                   'placeholder="Benutzer bzw. E-Mail">' if spec.get("send") == "basic" else "")
        rows.append(f'''<tr><td><b>{esc(s["name"])}</b><br><span class="muted">{how or "—"}</span></td>
<td>{state}</td><td><form method="post" action="me/credential" class="grid" style="gap:.4rem">
<input type="hidden" name="server" value="{esc(s["name"])}">{user_in}
<input type="password" name="value" autocomplete="off" required placeholder="{"ersetzen" if have else "Token oder Schlüssel"}">
<button>{"Ersetzen" if have else "Speichern"}</button></form> {drop}</td></tr>''')
    top = f'<p class="attention">{esc(notice)}</p>' if notice else ""
    return f'''<div class="card"><h2>Meine Zugangsdaten</h2>{top}
<p class="hint">Diese Server kennen dich nur mit deinem eigenen Zugang, etwa einem persönlichen Token.
Das Gateway hält ihn verschlüsselt, schickt ihn nur bei deinen Aufrufen an genau diesen Server und
zeigt ihn nie wieder an, auch der Verwaltung nicht. Die Verwaltung sieht nur, dass es ihn gibt, und
kann ihn löschen.</p>
<div class="scroll"><table><tr><th>Server</th><th>Stand</th><th>Eintragen</th></tr>{"".join(rows)}</table></div></div>'''


def me_page(user, uid, query, admin=False, notice="", also=()):
    doc = me_doc(uid, query, also)
    flt = doc["filter"]
    views_seen = sorted({r["view"] for r in books.person_calls(DB, "platform", uid, days=ME_DAYS,
                                                               also=also)})

    def link(**change):
        q = {"problems": "1" if flt["problems"] else "0", "view": flt["view"], "day": flt["day"]}
        q.update(change)
        return "me?" + "&amp;".join(f"{k}={quote(v)}" for k, v in q.items() if v)

    rows = []
    for c in doc["calls"]:
        who = " · ".join(x for x in (c["client"], c["assistant"]) if x) or "—"
        mark = badge("green" if c["outcome"] == "ok" else "yellow" if c["outcome"] in (
            "limited", "tool_error") else "red", ME_OUTCOME_WORDS.get(c["outcome"], c["outcome"]))
        kind = "" if c["kind"] == "tool" else (
            " <span class=muted>" + esc(compose.KIND_WORDS.get(c["kind"], c["kind"])) + "</span>")
        rows.append(f'<tr><td>{esc(c["time"].replace("T", " ").rstrip("Z"))}</td>'
                    f'<td><a href="{link(view=c["view"])}">{esc(c["view"])}</a></td>'
                    f'<td><code>{esc(c["name"] or "—")}</code>{kind}</td><td>{esc(who)}</td>'
                    f'<td>{esc(c["procedure"])}</td><td>{mark}</td><td class="num">{c["ms"]} ms</td></tr>')
        if c["sentence"]:
            rows.append(f'<tr class="why"><td></td><td colspan="6">{esc(c["sentence"])} '
                        f'<span class="muted">Abgelehnt oder gemeldet von: '
                        f'{esc(DOOR_WORDS.get(c["door"], "—"))}. {esc(c["hint"])}</span></td></tr>')
    filters = [f'<a href="{link(problems="0" if flt["problems"] else "1")}">'
               f'{"Alle Aufrufe zeigen" if flt["problems"] else "Nur Probleme zeigen"}</a>']
    if flt["view"]:
        filters.append(f'Sicht <b>{esc(flt["view"])}</b> (<a href="{link(view="")}">alle Sichten</a>)')
    if flt["day"]:
        filters.append(f'Tag <b>{esc(flt["day"])}</b> (<a href="{link(day="")}">alle Tage</a>)')
    day_form = (f'<form method="get" action="me" style="display:inline">'
                f'<input type="hidden" name="problems" value="{"1" if flt["problems"] else "0"}">'
                f'<input type="hidden" name="view" value="{esc(flt["view"])}">'
                f'<input type="date" name="day" value="{esc(flt["day"])}"> <button class="quiet">Tag</button></form>')
    empty = ("Keine Probleme in den letzten 30 Tagen." if flt["problems"] and not flt["view"]
             and not flt["day"] else "Keine Aufrufe für diesen Filter.")
    top = f'<div class="card attention"><p>{esc(notice)}</p></div>' if notice else ""
    body = top + f'''<div class="card"><h2>Meine Aufrufe</h2>
<p class="hint">Deine Aufrufe über dieses Gateway in den letzten {ME_DAYS} Tagen – aus dem Chat,
aus Apps der Plattform. Gezählt, nicht mitgeschrieben: Argumente und Ergebnisse stehen nirgends.
Der Grund einer Ablehnung steht in Worten darunter, mit dem, der abgelehnt hat.</p>
<p>{" · ".join(filters)} · {day_form}</p>
<div class="scroll"><table><tr><th>Zeit (UTC)</th><th>Sicht</th><th>Eintrag</th><th>Chat · Assistent</th>
<th>Verfahren</th><th>Ausgang</th><th>Dauer</th></tr>
{"".join(rows) or f'<tr><td colspan=7 class=muted>{empty}</td></tr>'}</table></div>
<p class="hint">Sichten mit Aufrufen: {esc(", ".join(views_seen) or "keine")}.
Als JSON: <a href="me/v1/calls">me/v1/calls</a>.</p>
<p class="hint">{esc(doc["note"])}</p></div>
{me_first_read_card()}{me_credentials_card(uid)}{me_templates_card(uid)}'''
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Meine Werkzeuge — OAAP MCP-Gateway</title>
{STYLE}<style>tr.why td{{border-top:0;padding-top:0;font-size:.85rem}}</style>
<header class="oaap">
  <a class="brand" href="me">{LOGO_SVG}
    <span><b>MEINE WERKZEUGE</b><small>Deine Aufrufe über das MCP-Gateway</small></span></a>
  <div class="userbox">{esc(user)}{'<br><small><a href="./">zur Verwaltung</a></small>' if admin else ""}</div>
</header>
<main>
{body}</main>
<footer class="oaap">OAAP MCP-Gateway {VERSION} — du siehst nur deine eigenen Aufrufe</footer></html>"""


def calls_tab(host):
    labels = {k["id"]: k["label"] for k in books.keys(DB)}
    urows = "".join(
        f'<tr><td>{esc(r["time"])}</td><td>{esc(labels.get(r["key_id"], r["key_id"]))}</td>'
        f'<td>{esc(r["view"])}</td><td><code>{esc(r["tool"] or "—")}</code>'
        f'{"" if r["kind"] == "tool" else " <span class=muted>" + esc(compose.KIND_WORDS.get(r["kind"], r["kind"])) + "</span>"}</td>'
        f'<td>{esc(r["outcome"])}{" (" + esc(r["reason"]) + ")" if r["reason"] else ""}</td>'
        f'<td class="num">{r["ms"]} ms</td><td class="num">{esc(fmt_bytes(r["bytes_up"]))} / '
        f'{esc(fmt_bytes(r["bytes_down"]))}</td></tr>' for r in books.recent(DB, limit=40))
    proto = ", ".join(f'{r["method"]} {r["n"]}' for r in books.protocol_totals(DB)) or "—"
    out = f'''<div class="card"><h2>Letzte Aufrufe</h2>
<p class="hint">Gezählt, nicht mitgeschrieben: Argumente und Ergebnisse stehen nirgends.
Protokollverkehr der letzten 30 Tage: {esc(proto)}.</p>
<div class="scroll"><table><tr><th>Zeit</th><th>Schlüssel</th><th>Sicht</th><th>Eintrag</th><th>Ausgang</th>
<th>Dauer</th><th>Bytes hoch/runter</th></tr>{urows or '<tr><td colspan=7 class=muted>Noch keine.</td></tr>'}</table></div></div>'''

    arows = "".join(f'<tr><td>{esc(r["time"])}</td><td>{esc(r["actor"])}</td><td>{esc(r["action"])}</td>'
                    f'<td>{esc(r["subject"])}</td><td class="muted">{esc(r["detail"])}</td></tr>'
                    for r in books.audit(DB, 40))
    return out + (f'<div class="card"><h2>Prüfspur</h2><div class="scroll"><table><tr><th>Zeit</th>'
                  f'<th>Wer</th><th>Was</th><th>Woran</th><th>Einzelheiten</th></tr>{arows}</table></div></div>')


TAB_PAGES = lambda: {"start": ("Übersicht", start_tab), "server": ("Server", servers_tab),
             "sichten": ("Sichten", views_tab), "systeme": ("Systemlandschaft", systems_tab),
             "zugaenge": ("Schlüssel und Profile", keys_tab),
             "anmeldung": ("Anmeldung", signin_card), "aufrufe": ("Aufrufe und Prüfspur", calls_tab)}


def admin_page(user, roles, host, notice="", secret=None, tab="start"):
    pages = TAB_PAGES()   # signin_card steht weiter unten
    if tab not in pages:
        tab = "start"
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p>{esc(notice)}</p></div>')
    if secret:
        views = secret["views"]
        cmd = "\n".join(f'claude mcp add --transport http {v} {endpoint(host, v)} \\\n'
                        f'  --header "Authorization: Bearer {secret["value"]}"' for v in views)
        parts.append(f'''<div class="card secretbox"><h2>Schlüssel „{esc(secret["label"])}“</h2>
<p><b>Nur jetzt sichtbar.</b> Gespeichert ist nur ein Hash; wer den Wert verliert, bekommt einen neuen.</p>
<pre>{esc(secret["value"])}</pre><p class="hint">Zum Beispiel für Claude Code:</p>
<pre>{esc(cmd)}</pre>
<p class="hint">Eine Datei für Claude Code, VS Code oder opencode, ohne den Wert (er steht dort als
Umgebungsvariable <code>{clientfiles.KEY_VAR}</code>):
<a href="vorlagen?key={quote(secret.get("id", ""))}">Vorlagen für diesen Schlüssel</a>.</p></div>''')
    if not VAULT.usable(MASTER) and tab in ("start", "server"):
        parts.append('<div class="card attention"><p><b>MCPGW_MASTER_KEY fehlt.</b> Server ohne '
                     'Zugangsdaten und Server hinter einer Destination lassen sich pflegen; eigene '
                     'Zugangsdaten verweigert die App, bis der Tresorschlüssel gesetzt ist (Portal → '
                     'Instanz → Konfiguration → erzeugen).</p></div>')
    title, build = pages[tab]
    parts.append(build(host))
    return page(title, "".join(parts), user, roles, tab)


CALLBACK_PORT = 33418                # fester Rücksprung-Port für Claude Code (D15)


def signin_card(host):
    """Wie sich Menschen anmelden (Spec §2.2) — oder warum es noch nicht geht."""
    if not ISSUER:
        return ('<div class="card"><h2>Anmeldung von Personen</h2><p class="muted">Aus. Einrichten: '
                + portal_mcp_link("im Portal auf der Seite dieser Instanz")
                + ' auf <b>„Anmeldung einrichten“</b> klicken (das darf die Verwaltung des Mandanten, tenant_admin, ab Plattform 0.1.309; davor nur der Betreiber, server_admin) -- die Plattform legt die Clients im Realm '
                'an und gibt dem Gateway die Werte. Die Felder MCPGW_* in der Konfiguration der Instanz '
                'bleiben dafür <b>leer</b>; von Hand gesetzt gewinnen sie (nur ohne Konnektor nötig). Am '
                'Knoten: <code>oaap idp mcp &lt;konnektor&gt; --instance &lt;name&gt;</code> '
                '(RFC-0065 D14, D15; RFC-0073 Stufe 3).</p></div>')
    err = ISSUER_KEYS.error if ISSUER_KEYS else ""
    views = [v["name"] for v in books.views(DB) if "signin" in books.view_ways(v)]
    cmd = "\n".join(f"claude mcp add --transport http {v} {endpoint(host, v)} \\\n"
                    f"  --client-id {SIGNIN_CLIENT or '<client>'} --callback-port {CALLBACK_PORT}"
                    for v in views) or "Noch keine Sicht nimmt Anmeldungen an."
    mcp_json = mcp_json_text(host, views)
    return f'''<div class="card"><h2>Anmeldung von Personen</h2>
<p>Issuer <code>{esc(ISSUER)}</code> · Audience <code>{esc(AUDIENCE or "— fehlt")}</code> ·
Client für Personen <code>{esc(SIGNIN_CLIENT or "— fehlt")}</code></p>
<p class="hint">Eingerichtet und abgeschaltet wird die Anmeldung {portal_mcp_link("im Portal auf der Seite dieser Instanz")}.</p>
{"<p class=err>Schlüssel des Issuers: " + esc(err) + "</p>" if err else ""}
<p class="hint">Im Realm braucht es einen öffentlichen Client (Rücksprung nur
<code>http://localhost:{CALLBACK_PORT}/callback</code>) mit einem Audience-Mapper auf
<code>{esc(AUDIENCE or "MCPGW_AUDIENCE")}</code>. Wer zahlt, entscheidet ein Zugangsprofil über die
Gruppen im Token; ohne passendes Profil gibt es 403.</p>
<p>Im Terminal mit <code>claude</code>:</p>
<pre>{esc(cmd)}</pre>
<p>In VS Code (die Erweiterung hat keinen Befehl <code>claude mcp</code>): eine Datei
<code>.mcp.json</code> in den geöffneten Ordner legen, dann in <code>/mcp</code> die Sicht wählen und
„Authenticate“. <b>Nicht</b> über den Dialog in <code>/mcp</code> eintragen: der kennt keine
Client-ID, Claude Code versucht dann, sich selbst beim Realm zu registrieren, und der Realm lehnt
das ab (gemessen am 07.11.).</p>
<pre>{esc(mcp_json)}</pre>
<p class="hint">Dieselbe Datei für opencode und zum Herunterladen: <a href="vorlagen?{"&amp;".join(
        "pick=" + quote(v + ":signin") for v in views)}">Vorlagen</a>.</p>{settings_table()}</div>'''


def mcp_json_text(host, views):
    """Dieselbe Angabe wie `claude mcp add ... --client-id --callback-port`, als Datei.
    Seit 0.28.0 aus der Vorlage „Claude Code“ (RFC-0107 §2.5): eine Erzeugung, nicht zwei."""
    if not views:
        return "{}"
    picks = [{"name": v, "url": endpoint(host, v), "way": "signin"} for v in views]
    return clientfiles.render(TEMPLATES["claude-code"], picks, host, SIGNIN_CLIENT or "<client>",
                              CALLBACK_PORT, REDIRECT_URI).rstrip("\n")


# ------------------------------------------------------------------ Vorlagen (0.28.0, RFC-0107)

TEMPLATES = clientfiles.load()        # eine fehlerhafte Vorlage hält den Start an
REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}/callback"


def personal_servers_of(view):
    out = []
    for name in books.view_servers(DB, view):
        srow = books.server_row(DB, name)
        if srow and srow["auth_kind"] == "personal":
            out.append(name)
    return out


def pick_problems(tpl, picks):
    """Was gegen diese Auswahl spricht, als Sätze; leer = die Datei wird gebaut."""
    out, seen = [], set()
    if not picks:
        out.append("Keine Sicht gewählt.")
    for p in picks:
        vrow = books.view_row(DB, p["name"])
        if not vrow:
            out.append(f"Die Sicht „{p['name']}“ gibt es nicht.")
            continue
        if p["name"] in seen:
            out.append(f"Die Sicht „{p['name']}“ ist zweimal gewählt; ein Werkzeug kennt jeden "
                       "Namen nur einmal.")
            continue
        seen.add(p["name"])
        way = WAY_NAMES.get(p["way"], p["way"])
        if p["way"] not in books.view_ways(vrow):
            out.append(f"Die Sicht „{p['name']}“ hat den Zugang {way} nicht.")
        elif p["way"] not in tpl["ways"]:
            out.append(f"Die Vorlage {tpl['title']} bietet den Zugang {way} noch nicht an.")
        elif p["way"] == "signin" and not (ISSUER and SIGNIN_CLIENT):
            out.append("Die Anmeldung von Personen ist nicht eingerichtet (Issuer oder Client für "
                       "Personen fehlt); ohne Client-ID gibt es keine Datei für den Zugang "
                       "„Anmeldung“.")
    return out


def client_file(host, target, picks):
    """(text, probleme, hinweise) -- text ist None, wenn etwas dagegen spricht. Nie ein
    Schlüsselwert: es gibt keinen Platzhalter dafür (D3)."""
    tpl = TEMPLATES.get(target)
    if not tpl:
        return None, [f"Ein Werkzeug „{target}“ kennt das Gateway nicht."], []
    picks = [dict(p, url=endpoint(host, p["name"])) for p in picks]
    problems = pick_problems(tpl, picks)
    if problems:
        return None, problems, []
    text = clientfiles.render(tpl, picks, host, SIGNIN_CLIENT, CALLBACK_PORT, REDIRECT_URI)
    hints = clientfiles.notes(tpl, picks)
    for p in picks:
        if p["way"] != "signin":
            continue
        for name in personal_servers_of(p["name"]):
            hints.append(f"In der Sicht {p['name']} braucht der Server {name} deinen eigenen "
                         f"Zugang: erst auf „Meine Werkzeuge“ ({me_url() or 'me'}) hinterlegen.")
    return text, [], hints


def key_picks(key_id):
    """(schlüsselzeile, auswahl, ausgelassen) -- die Sichten eines Schlüssels auf dem Weg `key`,
    nach derselben Regel wie ein Aufruf (`why_not`). Ausgelassen = [(sicht, grund)]."""
    row = DB.execute("SELECT * FROM keys WHERE id=?", (key_id,)).fetchone() if key_id else None
    if not row or row["kind"] != "key" or row["revoked"]:
        return None, [], []
    picks, skipped = [], []
    for v in books.key_views(row):
        reason = why_not(row, books.view_row(DB, v), "key")
        if reason:
            skipped.append((v, reason))
        else:
            picks.append({"name": v, "way": "key"})
    return row, picks, skipped


def parse_picks(values):
    out = []
    for raw in values:
        name, _, way = raw.rpartition(":")
        if name and way in clientfiles.WAYS:
            out.append({"name": name, "way": way})
    return out


def templates_page(user, roles, host, pairs):
    """Reiter „Vorlagen“ (RFC-0107 §2.1): Werkzeug, Sichten aus einem Schlüssel oder von Hand."""
    query = dict(pairs)
    target = query.get("target") or "claude-code"
    key_id = query.get("key", "")
    picks = parse_picks([v for k, v in pairs if k == "pick"])
    skipped, key_row = [], None
    if key_id and not picks:
        key_row, picks, skipped = key_picks(key_id)
    result = ""
    if picks or key_id:
        text, problems, hints = client_file(host, target, picks)
        tpl = TEMPLATES.get(target, {})
        lost = "".join(f"<li>Sicht {esc(v)} ausgelassen: {esc(r)}</li>" for v, r in skipped)
        if key_id and key_row is None:
            problems = ["Diesen Schlüssel gibt es nicht (mehr), oder er ist widerrufen."]
            text = None
        if text is None:
            result = ('<div class="card attention"><h2>Keine Datei</h2><ul>'
                      + "".join(f"<li>{esc(p)}</li>" for p in problems) + lost + "</ul></div>")
        else:
            qs = "&amp;".join([f"target={quote(target)}"]
                              + ([f"key={quote(key_id)}"] if key_row is not None and
                                 not [k for k, _v in pairs if k == "pick"] else
                                 [f"pick={quote(p['name'] + ':' + p['way'])}" for p in picks]))
            result = (f'<div class="card"><h2>{esc(tpl["title"])}: <code>{esc(tpl["file"])}</code></h2>'
                      f'<pre>{esc(text)}</pre>'
                      f'<p><a class="button" href="vorlagen?{qs}&amp;laden=1">Herunterladen</a></p>'
                      f'<p class="hint">{esc(tpl["where"])}</p><ul>'
                      + "".join(f"<li>{esc(h)}</li>" for h in hints) + lost
                      + f'</ul><p class="hint">Format: {esc(tpl["format"])} (geprüft am '
                      f'{esc(tpl["checked"])}). Die Formate der Werkzeuge ändern sich; passt eine '
                      'Datei nicht mehr, bitte melden.</p></div>')
    keys = [k for k in DB.execute("SELECT * FROM keys WHERE kind='key' AND revoked='' "
                                  "ORDER BY label")]
    key_opts = '<option value="">— von Hand wählen —</option>' + "".join(
        f'<option value="{esc(k["id"])}"{" selected" if k["id"] == key_id else ""}>'
        f'{esc(k["label"])} ({esc(", ".join(books.key_views(k)) or "keine Sicht")})</option>'
        for k in keys)
    tpl_opts = "".join(f'<option value="{esc(t)}"{" selected" if t == target else ""}>'
                       f'{esc(v["title"])}</option>' for t, v in sorted(TEMPLATES.items()))
    chosen = {(p["name"], p["way"]) for p in picks} if not key_id else set()
    rows, blocked = [], []
    for v in books.views(DB):
        ways = [w for w in books.view_ways(v) if w in clientfiles.WAYS]
        if not ways:
            blocked.append(v["name"])
            continue
        boxes = " ".join(
            f'<label><input type="checkbox" name="pick" value="{esc(v["name"] + ":" + w)}"'
            f'{" checked" if (v["name"], w) in chosen else ""}> {esc(books._WAY_WORD[w])}</label>'
            for w in ways)
        rows.append(f'<tr><td>{esc(v["name"])}</td><td>{boxes}</td></tr>')
    note = (f'<p class="hint">Ohne Datei: {esc(", ".join(blocked))} -- diese Sichten nehmen nur '
            'Personen aus einem Chat (Zugang „Plattform“); ein Werkzeug erreicht sie nicht.</p>'
            if blocked else "")
    form = f'''<div class="card"><h2>Vorlagen für Werkzeuge</h2>
<p class="hint">Die Datei, mit der ein Werkzeug Sichten dieses Gateways benutzt. Statt eines
Schlüssels steht dort immer die Umgebungsvariable <code>{clientfiles.KEY_VAR}</code>, nie ein Wert;
bei „Anmeldung“ die Client-ID und der Rücksprung-Port. Wer seinen Schlüssel hat, holt die Datei
auch selbst: <code>GET /mcp/_client-config?target=…</code> mit dem Schlüssel.</p>
<form method="get" action="vorlagen">
<div><label>Werkzeug</label><select name="target">{tpl_opts}</select></div>
<div><label>Sichten eines Schlüssels</label><select name="key">{key_opts}</select></div>
<p class="hint">… oder von Hand (dann den Schlüssel leer lassen):</p>
<div class="scroll"><table><tr><th>Sicht</th><th>Zugang</th></tr>
{"".join(rows) or '<tr><td colspan=2 class=muted>Noch keine Sicht.</td></tr>'}</table></div>
{note}<p><button>Datei bauen</button></p></form></div>'''
    return page("Vorlagen", form + result, user, roles, "vorlagen")


def signin_offer(uid):
    """Für /me (RFC-0107 §3.2): Sichten mit Anmeldung, die ein Zugangsprofil öffnet. Die Gruppen
    der Person im Realm kennt das Gateway hier nicht (die Plattform gibt Apps keine Gruppen,
    `oaap.core.identity` §2) -- ob sie dazugehört, entscheidet die Anmeldung. [(sicht, für_alle,
    fehlende_zugänge)]"""
    if not (ISSUER and SIGNIN_CLIENT):
        return []
    profiles = list(DB.execute("SELECT * FROM keys WHERE kind='profile' AND revoked=''"))
    out = []
    for v in books.views(DB):
        if "signin" not in books.view_ways(v):
            continue
        mine = [p for p in profiles if v["name"] in books.key_views(p)]
        if not mine:
            continue
        everyone = any("*" in books.profile_groups(p) for p in mine)
        missing = [s for s in personal_servers_of(v["name"])
                   if books.personal_row(DB, "platform", uid, s) is None]
        out.append((v["name"], everyone, missing))
    return out


def me_templates_card(uid):
    offer = signin_offer(uid)
    if not offer:
        return ""
    targets = [(t, v) for t, v in sorted(TEMPLATES.items()) if "signin" in v["ways"]]
    rows = "".join(
        f'<tr><td>{esc(name)}</td><td>{"alle mit Anmeldung" if everyone else "bestimmte Gruppen -- ob du dazugehörst, entscheidet die Anmeldung"}</td>'
        f'<td>{("erst Zugang hinterlegen: " + esc(", ".join(missing))) if missing else "—"}</td></tr>'
        for name, everyone, missing in offer)
    links = " · ".join(f'<a href="me/client-config?target={quote(t)}">{esc(v["title"])}</a>'
                       for t, v in targets)
    return f'''<div class="card"><h2>In meinem Werkzeug einrichten</h2>
<p class="hint">Diese Sichten nehmen eine Anmeldung bei deinem Mandanten. Die Datei enthält kein
Geheimnis: Das Werkzeug öffnet beim ersten Aufruf die Anmeldung im Browser.</p>
<div class="scroll"><table><tr><th>Sicht</th><th>Für wen</th><th>Vorher</th></tr>{rows}</table></div>
<p>Datei herunterladen: {links}</p></div>'''


FROM_WORDS = {"hand": "von Hand", "platform": "von der Plattform", "": "leer"}


def settings_table():
    """Woher jede der fünf Einstellungen kommt (Spec §3.8). Das Geheimnis nie."""
    rows = "".join(
        f"<tr><td><code>{own}</code></td><td>"
        + (esc(_S[own]) if own != "MCPGW_CLIENT_SECRET" else ("gesetzt" if _S[own] else "—"))
        + f"</td><td>{FROM_WORDS[SETTING_FROM[own]]}</td></tr>"
        for own, _plat in PLATFORM_SETTINGS)
    return ('<table><tr><th>Einstellung</th><th>Wert</th><th>Herkunft</th></tr>'
            f"{rows}</table>")


def untrusted_note(s):
    if not s or not s["untrusted_by"]:
        return ""
    who = "vom Manifest der Quelle vorgeschlagen" if s["untrusted_by"] == "plattform"         else f"zuletzt entschieden von {s['untrusted_by']}"
    return f" — {who}"


def server_form(s=None):
    lim = limits_of(s) if s else dict(guard.DEFAULTS)
    kind = s["auth_kind"] if s else "none"
    plain = kind if kind in books.AUTH_KINDS else "header"
    header = kind[7:] if kind.startswith("header:") else ""
    ex = json.loads(s["exchange"] or "{}") if s else {}
    pspec = books.personal_spec(s) if s else {}
    psend = pspec.get("send") or "bearer"
    limit_inputs = "".join(
        f'<div><label>{esc(guard.LABELS[n])}</label><input type="number" name="lim_{n}" '
        f'value="{lim[n]}"></div>' for n in guard.DEFAULTS)
    cred_note = ("gesetzt — leer lassen heißt unverändert" if s and s["credential"]
                 else "nicht gesetzt")
    return f'''<form class="grid" method="post" action="server-save">
<div><label>Name</label><input type="text" name="name" value="{esc(s["name"] if s else "")}"
{" readonly" if s else ""} required></div>
<div><label>Adresse (URL oder destination:&lt;name&gt;)</label>
<input type="text" name="address" value="{esc(s["address"] if s else "")}" required></div>
<div><label>Zugang (Verfahren T; X = Token-Tausch; P = propagate, die Person über eine Destination bis ins Zielsystem)</label><select name="auth">{options(("none", "bearer", "basic", "header", "exchange", "propagate", "personal"), plain)}</select></div>
<div><label>Verfahren U (personal): so geht der Zugang jeder Person zum Server</label>
<select name="personal_send">{options(("bearer", "basic", "header"), psend if psend in ("bearer", "basic") else "header", {"bearer": "Authorization: Bearer", "basic": "Authorization: Basic – Benutzer/E-Mail und Token (z. B. Atlassian Cloud)", "header": "Kopfzeile (Name unten bei „Kopfzeile“)"})}</select></div>
<div><label>Verfahren U: Präfix vor dem Wert (nur Kopfzeile, z. B. „Token “)</label>
<input type="text" name="personal_prefix" value="{esc(pspec.get("prefix", ""))}"></div>
<div><label>Verfahren U: Hinweis für die Person (wo erzeugen, welche Rechte)</label>
<input type="text" name="personal_hint" value="{esc(pspec.get("hint", ""))}"></div>
<div><label>Verfahren U: Link dorthin</label>
<input type="text" name="personal_link" value="{esc(pspec.get("link", ""))}"></div>
<div><label>Kopfzeile (bei Zugang header oder Verfahren U mit Kopfzeile)</label><input type="text" name="header" value="{esc(header or (psend[7:] if psend.startswith("header:") else ""))}"></div>
<div><label>Verfahren X: Token-Endpunkt (URL oder destination:&lt;name&gt;)</label>
<input type="text" name="token_url" value="{esc(ex.get("token_url", ""))}"></div>
<div><label>Verfahren X: eigener Client (= MCPGW_AUDIENCE)</label>
<input type="text" name="client_id" value="{esc(ex.get("client_id", ""))}"></div>
<div><label>Verfahren X: Audience des Backends</label>
<input type="text" name="audience" value="{esc(ex.get("audience", ""))}"></div>
<div><label>Zugangsdaten ({esc(cred_note)}; basic: name:passwort; X: Client-Geheimnis)</label>
<input type="password" name="credential" autocomplete="off"></div>
<div><label><input type="checkbox" name="drop_credential" value="1"> Zugangsdaten entfernen</label></div>
<div><label>Ampel</label><select name="light">{options(books.LIGHTS, s["light"] if s else "red", LIGHT_WORDS)}</select></div>
<div><label><input type="checkbox" name="untrusted" value="1"{" checked" if s and s["untrusted"] else ""}> Fremder Inhalt
(Webseiten o. ä.): nur in Sichten, deren Einträge alle lesen{esc(untrusted_note(s))}</label></div>
{limit_inputs}<div><button>Speichern</button></div></form>'''


def personal_card(s):
    """Für den Betreiber (RFC-0095 D6): wie viele Personen einen Zugang hinterlegt haben,
    wer (Kennung), wann -- löschen ja, lesen nie. Ungenutzt seit 180 Tagen: markiert (D8)."""
    if s["auth_kind"] != "personal":
        return ""
    holders = books.personal_holders(DB, s["name"])
    rows = "".join(
        f'<tr><td><code>{esc(h["sub"])}</code>{"" if h["iss"] == "platform" else " <span class=muted>" + esc(h["iss"]) + "</span>"}</td>'
        f'<td>{esc(h["stored"][:10])}</td><td>{esc((h["last_used"] or "—")[:10])}'
        f'{" " + badge("yellow", "seit 180 Tagen ungenutzt") if h["unused"] else ""}</td>'
        f'<td><form method="post" action="personal-delete"><input type="hidden" name="name" value="{esc(s["name"])}">'
        f'<input type="hidden" name="iss" value="{esc(h["iss"])}"><input type="hidden" name="sub" value="{esc(h["sub"])}">'
        f'<button class="quiet">Löschen</button></form></td></tr>' for h in holders)
    return f'''<div class="card"><h2>Persönliche Zugänge (Verfahren U)</h2>
<p class="hint">{len(holders)} Person(en) haben einen eigenen Zugang hinterlegt. Die Werte liest niemand, auch
nicht die Verwaltung; löschen geht (etwa nach einem Leck oder einem Abgang). Personen erscheinen mit ihrer
Kennung der Plattform, nie mit Namen. Den Katalog liest das Gateway mit deinem eigenen Zugang -- hinterlege
ihn auf <a href="me">Meine Aufrufe</a>.</p>
<div class="scroll"><table><tr><th>Person</th><th>Hinterlegt</th><th>Zuletzt benutzt</th><th></th></tr>
{rows or '<tr><td colspan=4 class=muted>Noch niemand.</td></tr>'}</table></div></div>
'''


def server_page(user, roles, host, name, notice=""):
    s = books.server_row(DB, name)
    if not s:
        return page("Unbekannt", '<div class="card"><p>Diesen Server gibt es nicht.</p></div>', user, roles)
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p>{esc(notice)}</p></div>')
    vnames = [v["name"] for v in books.views(DB)]
    in_views = {}
    for item in books.approved_items(DB, name):
        in_views.setdefault((item["kind"], item["source"]), []).append(
            f'{item["view"]}: {item["name"]}' + (f' ({books.EFFECT_WORDS[item["effect"]]})'
                                                 if item["kind"] == "tool" else ""))
    sections = []
    for kind in compose.KINDS:
        rows = []
        for c in books.catalog(DB, name, kind):
            doc = json.loads(c["doc"])
            ann = doc.get("annotations") or {}
            note, add = "", ""
            if kind == "tool":
                unchecked = argcheck.unchecked(doc.get("inputSchema"))
                remote = argcheck.remote_refs(doc.get("inputSchema"))
                if unchecked:
                    note += "<br><span class=hint>nicht geprüft: " + esc(", ".join(unchecked)) + "</span>"
                if remote:
                    note += "<br><span class=err>Schema verweist nach außen — nicht freigebbar</span>"
                blocked = bool(remote)
            elif kind == "template":
                blocked = not compose.template_supported(c["name"])
                if blocked:
                    note += "<br><span class=err>Vorlage nicht eindeutig prüfbar — nicht freigebbar</span>"
            else:
                blocked = False
            if c["present"] and vnames and not blocked:
                extra = ""
                if kind == "tool":
                    hint = ("read" if ann.get("readOnlyHint") else
                            "execute" if ann.get("destructiveHint") else "write")
                    extra = f'<select name="effect">{options(books.EFFECTS, hint, books.EFFECT_WORDS)}</select> '
                if kind in ("tool", "prompt"):
                    extra = (f'<input type="text" name="as" placeholder="Name in der Sicht (leer = '
                             f'Vorschlag)" style="max-width:16rem"> ' + extra)
                add = (f'<form method="post" action="item-add"><input type="hidden" name="server" '
                       f'value="{esc(name)}"><input type="hidden" name="kind" value="{kind}">'
                       f'<input type="hidden" name="source" value="{esc(c["name"])}">'
                       f'<select name="view">{options(vnames, vnames[0])}</select> {extra}'
                       f'<button class="quiet">In Sicht legen</button></form>')
            hints = ", ".join(f"{k}={v}" for k, v in ann.items() if k.endswith("Hint"))
            rows.append(
                f'<tr><td><code>{esc(c["name"])}</code>'
                f'{"" if c["present"] else " " + badge("red", "nicht mehr angeboten")}'
                f'<br><span class="muted">{esc((doc.get("description") or "")[:240])}</span></td>'
                f'<td>{esc(hints or "—")}{"<br><span class=hint>nur Hinweis, nie durchgesetzt</span>" if hints else ""}</td>'
                f'<td>{esc("; ".join(in_views.get((kind, c["name"]), [])) or "in keiner Sicht")}{note}</td>'
                f'<td><code>{esc(c["fingerprint"][:12])}</code></td><td>{add}</td></tr>')
        if rows:
            sections.append(f'<h2 style="margin-top:1rem">{esc(KIND_PLURAL[kind])}</h2>'
                            f'<div class="scroll"><table><tr><th>Name</th><th>Angabe des Servers</th>'
                            f'<th>Sichten</th><th>Fingerabdruck</th><th></th></tr>{"".join(rows)}</table></div>')
    parts.append(f'''<div class="card"><h1>Server {esc(name)}</h1>
<p>{badge(s["light"], LIGHT_WORDS[s["light"]])}{" " + badge("yellow", "fremder Inhalt") if s["untrusted"] else ""} Protokoll {esc(s["version"] or "noch nicht ausgehandelt")}
· Katalog gelesen: {esc(s["last_read"] or "nie")}</p>
{"<p class=err>" + esc(s["last_error"]) + "</p>" if s["last_error"] else ""}
<p class="hint">Was mit Ergebnissen geschieht, sieht das Gateway nicht: Sie fließen in das Modell des
Clients. Durchgesetzt wird die Ampel nur für das, was zum Server geht.</p>
<form method="post" action="server-read"><input type="hidden" name="name" value="{esc(name)}">
<button>Katalog jetzt lesen</button></form></div>
<div class="card"><h2>Katalog</h2><p class="hint">Was der Server anbietet. Nichts davon ist sichtbar, bis es
in einer Sicht liegt. Ein Tool oder Prompt bekommt dort einen Namen der Sicht — vorgeschlagen wird der des
Servers, wenn er frei ist, sonst <code>&lt;server&gt;_&lt;name&gt;</code>. Die Wirkungsklasse vergibt der
Betreiber; die Angaben des Servers sind nur ein Vorschlag.</p>
{"".join(sections) or '<p class=muted>Noch nicht gelesen.</p>'}</div>
{personal_card(s)}<div class="card"><h2>Umbenennen</h2><p class="hint">Katalog und Einträge in den Sichten ziehen mit;
die Namen in den Sichten bleiben (Clients merken nichts). {"Der Server stammt aus der Quelle " + "<code>" + esc(s["source"]) + "</code>" + " und wird beim Abgleich weiter gefunden. " if s["source"] else ""}Messzeilen behalten
den alten Namen.</p>
<form class="grid" method="post" action="server-rename"><input type="hidden" name="name" value="{esc(name)}">
<div><label>Neuer Name</label><input type="text" name="new" required></div>
<div><button class="quiet">Umbenennen</button></div></form></div>
<div class="card"><h2>Ändern</h2>{server_form(s)}
<form method="post" action="server-delete" style="margin-top:1rem"><input type="hidden" name="name" value="{esc(name)}">
<button class="quiet">Server löschen</button></form></div>''')
    return page(f"Server {name}", "".join(parts), user, roles, "server")


REQUEST_WORDS = {"elicitation": "Rückfrage an die Person (elicitation)",
                 "roots": "Arbeitsverzeichnisse des Clients (roots)",
                 "sampling": "Modell des Clients benutzen (sampling)"}


def item_hidden(item):
    return (f'<input type="hidden" name="view" value="{esc(item["view"])}">'
            f'<input type="hidden" name="kind" value="{esc(item["kind"])}">'
            f'<input type="hidden" name="name" value="{esc(item["name"])}">')


def tailor_form(item):
    """Was die Sicht aus einem Eintrag macht: Name, Beschreibung, feste Argumente, Grenzen, Klasse."""
    kind = item["kind"]
    fields = []
    if kind in ("tool", "prompt"):
        fields.append(f'<div><label>Name in der Sicht</label><input type="text" name="new_name" '
                      f'value="{esc(item["name"])}"></div>')
    fields.append(f'<div><label>Beschreibung (leer = die des Servers)</label><input type="text" '
                  f'name="description" value="{esc(item["description"])}"></div>')
    if kind == "tool":
        fields.append(f'<div><label>Feste Argumente (JSON), z. B. {{"package": "ZDEMO"}}</label>'
                      f'<input type="text" name="fixed" value="{esc(item["fixed"])}"></div>')
        fields.append(f'<div><label>Grenzen (JSON), z. B. {{"name": {{"glob": "Z*"}}}} oder '
                      f'{{"mode": {{"enum": ["a", "b"]}}}}</label>'
                      f'<input type="text" name="bounds" value="{esc(item["bounds"])}"></div>')
        fields.append(f'<div><label>Wirkungsklasse</label><select name="effect">'
                      f'{options(books.EFFECTS, item["effect"], books.EFFECT_WORDS)}</select></div>')
    return (f'<form class="grid" method="post" action="item-tailor">{item_hidden(item)}'
            f'{"".join(fields)}<div><button class="quiet">Übernehmen</button></div></form>')


# ------------------------------------------------------- Bäume (0.13.0)
# Zuordnung wie in einem Dateibaum: Server → Art → Eintrag, Ankreuzfelder.
# Ohne Skript geht alles (jedes Blatt ist ein eigenes Feld); das Skript
# setzt nur die Haken der Gruppen (ganz, teilweise, keiner).

SEP = "\t"
TREE_STYLE = """<style>
ul.tree{list-style:none;margin:.15rem 0 .15rem 1.2rem;padding:0}
ul.tree.top{margin-left:0}
ul.tree li{margin:.12rem 0}
ul.tree details>summary{cursor:pointer}
ul.tree label{display:inline;font-weight:normal;margin:0}
ul.tree input[type=checkbox]{width:auto;margin:0 .35rem 0 0;vertical-align:middle}
ul.tree select{width:auto;max-width:10rem;padding:.05rem .3rem;font-size:.8rem;margin-left:.4rem}
ul.tree .d{display:block;margin-left:1.6rem;font-size:.8rem}
</style>"""
TREE_SCRIPT = """<script>
for (const f of document.querySelectorAll("form.treeform")) {
  const leaves = li => [...li.querySelectorAll("input.leaf:not(:disabled)")];
  const sync = () => { for (const g of f.querySelectorAll("input.grp")) {
    const c = leaves(g.closest("li")), n = c.filter(x => x.checked).length;
    g.checked = n > 0 && n === c.length; g.indeterminate = n > 0 && n < c.length; } };
  f.addEventListener("change", e => {
    if (e.target.classList.contains("grp")) for (const x of leaves(e.target.closest("li"))) x.checked = e.target.checked;
    sync(); });
  sync();
}
</script>"""


def tree_key(server, kind, source):
    return SEP.join((server, kind, source))


def tree_leaf(key, checked, label, extra="", disabled_why=""):
    dis = " disabled" if disabled_why else ""
    return (f'<li><input type="hidden" name="seen" value="{esc(key)}">'
            f'<label><input type="checkbox" class="leaf" name="pick" value="{esc(key)}"'
            f'{" checked" if checked else ""}{dis}>{label}</label>{extra}'
            + (f'<span class="d err">{esc(disabled_why)}</span>' if disabled_why else "") + '</li>')


def tree_node(label, inner, open_=True):
    return (f'<li><details{" open" if open_ else ""}><summary><input type="checkbox" class="grp" '
            f'aria-label="alle">{label}</summary><ul class="tree">{inner}</ul></details></li>')


def assign_tree(name):
    """Seite einer Sicht: alle Kataloge, angekreuzt = in dieser Sicht."""
    v = books.view_row(DB, name)
    key_view = "key" in books.view_ways(v)
    have = {}
    for i in books.items(DB, name):
        have.setdefault((i["server"], i["kind"], i["source"]), []).append(i)
    servers = []
    for s in books.servers(DB):
        person = s["auth_kind"] in books.PERSON_KINDS and key_view
        kinds, n_in, n_all = [], 0, 0
        for kind in compose.KINDS:
            leaves = []
            for c in books.catalog(DB, s["name"], kind):
                if not c["present"]:
                    continue
                key = (s["name"], kind, c["name"])
                doc = json.loads(c["doc"])
                inside = have.get(key, [])
                why = ""
                if person:
                    why = "Verfahren X, I oder P -- die Sicht nimmt Schlüssel an"
                elif kind == "tool" and argcheck.remote_refs(doc.get("inputSchema")):
                    why = "Schema verweist nach außen -- nicht freigebbar"
                elif kind == "template" and not compose.template_supported(c["name"]):
                    why = "Vorlage nicht eindeutig prüfbar"
                if why and inside:
                    why = ""                  # was drin ist, lässt sich immer herausnehmen
                label = f'<code>{esc(c["name"])}</code>'
                for i in inside:
                    if i["name"] != c["name"]:
                        label += f' <span class="muted">→ {esc(i["name"])}</span>'
                    state = books.item_state(DB, i)
                    if state != "active":
                        label += " " + badge(*STATE_WORDS[state])
                extra = ""
                if kind == "tool" and not why:
                    ann = doc.get("annotations") or {}
                    cur = inside[0]["effect"] if inside else (
                        "read" if ann.get("readOnlyHint") else
                        "execute" if ann.get("destructiveHint") else "write")
                    extra = (f'<select name="eff{SEP}{esc(tree_key(*key))}" aria-label="Wirkungsklasse">'
                             f'{options(books.EFFECTS, cur, books.EFFECT_WORDS)}</select>')
                desc = (doc.get("description") or doc.get("title") or "")[:160]
                if desc:
                    extra += f'<span class="d muted">{esc(desc)}</span>'
                leaves.append(tree_leaf(tree_key(*key), bool(inside), label, extra, why))
                n_all += 1
                n_in += bool(inside)
            if leaves:
                kinds.append(tree_node(f'{esc(KIND_PLURAL[kind])} <span class="muted">({len(leaves)})</span>',
                                       "".join(leaves), open_=False))
        if kinds:
            servers.append(tree_node(f'<b>{esc(s["name"])}</b> {badge(s["light"], s["light"])} '
                                     f'<span class="muted">{n_in} von {n_all} in der Sicht</span>',
                                     "".join(kinds), open_=n_in > 0))
    if not servers:
        return '<p class="muted">Noch kein Server mit gelesenem Katalog.</p>'
    return (f'<form class="treeform" method="post" action="view-items">'
            f'<input type="hidden" name="view" value="{esc(name)}">'
            f'<ul class="tree top">{"".join(servers)}</ul>'
            f'<div style="margin-top:.6rem"><button>Zuordnung speichern</button></div></form>')


def views_tree():
    """Reiter Sichten: was in jeder Sicht liegt, Sicht → Server → Art → Eintrag; Haken weg = heraus."""
    out = []
    for v in books.views(DB):
        rows = books.items(DB, v["name"])
        if not rows:
            continue
        by = {}
        for i in rows:
            by.setdefault(i["server"], {}).setdefault(i["kind"], {}).setdefault(i["source"], []).append(i)
        servers = []
        for server, kinds in sorted(by.items()):
            knodes = []
            for kind in compose.KINDS:
                if kind not in kinds:
                    continue
                leaves = []
                for source, its in sorted(kinds[kind].items()):
                    label = " ".join(
                        f'<code>{esc(i["name"])}</code>'
                        + (f' <span class="muted">({esc(source)})</span>' if i["name"] != source else "")
                        + (f' <span class="muted">{esc(books.EFFECT_WORDS[i["effect"]])}</span>'
                           if kind == "tool" else "")
                        + ("" if books.item_state(DB, i) == "active"
                           else " " + badge(*STATE_WORDS[books.item_state(DB, i)]))
                        for i in its)
                    leaves.append(tree_leaf(tree_key(server, kind, source), True, label))
                knodes.append(tree_node(f'{esc(KIND_PLURAL[kind])} <span class="muted">({len(leaves)})</span>',
                                        "".join(leaves)))
            servers.append(tree_node(f'<b>{esc(server)}</b>', "".join(knodes)))
        out.append(f'<li><details><summary><a href="view?name={quote(v["name"])}"><b>{esc(v["name"])}</b></a> '
                   f'<span class="muted">{len(rows)} {"Eintrag" if len(rows) == 1 else "Einträge"}</span></summary>'
                   f'<form class="treeform" method="post" action="view-items">'
                   f'<input type="hidden" name="view" value="{esc(v["name"])}">'
                   f'<input type="hidden" name="back" value="sichten">'
                   f'<ul class="tree">{"".join(servers)}</ul>'
                   f'<button class="quiet">Ohne Haken herausnehmen</button></form></details></li>')
    if not out:
        return ""
    return (f'<div class="card"><h2>Was in den Sichten liegt</h2><p class="hint">Je Sicht als Baum. '
            f'Haken entfernen und „Ohne Haken herausnehmen“ -- hinzufügen geht auf der Seite der Sicht '
            f'(Baum aller Kataloge) oder beim Server.</p><ul class="tree top">{"".join(out)}</ul></div>'
            + TREE_STYLE + TREE_SCRIPT)


def copy_card(name):
    readers = books.view_readers(DB, name)
    words = ", ".join(f'{label} ({"Profil" if kind == "profile" else "Schlüssel"})' for label, kind in readers)
    return f'''<div class="card"><h2>Kopieren oder umbenennen</h2>
<p class="hint">Den Namen einer Sicht nennen: {esc(words or "kein gültiger Schlüssel und kein Profil")} --
außerdem die Adresse <code>/mcp/{esc(name)}</code> bei jedem MCP-Client und die Assistenten eines Chats
(Feld „Sicht“). <b>Umbenennen</b> stellt Schlüssel und Profile um und löscht die alte Sicht; Clients und
Assistenten stellt das Gateway nicht um -- dort den neuen Namen eintragen. <b>Kopieren</b> lässt alles, wie
es ist; die Freigaben gehen in beiden Fällen mit (derselbe festgehaltene Text).</p>
<form class="grid" method="post" action="view-copy"><input type="hidden" name="view" value="{esc(name)}">
<div><label>Neuer Name</label><input type="text" name="new" required></div>
<div><label>Was</label><select name="mode"><option value="copy">kopieren</option>
<option value="rename">umbenennen</option></select></div>
<div><button>Ausführen</button></div></form></div>'''


# ------------------------------------------------- Systemlandschaft (RFC-0114)

def systems_tab(host):
    """Die Zielsysteme dieses Gateways: Titel, Rolle, aktiv, Art, Angaben, Server, Ampel."""
    rows = []
    for s in landscape.systems(DB):
        facts = ", ".join(f"{k}={v}" for k, v in sorted(landscape.facts_of(s).items()))
        light = landscape.system_light(DB, s)
        rows.append(f'<tr><td><code>{esc(s["id"])}</code><br>{esc(s["title"])}</td>'
                    f'<td>{esc(landscape.ROLE_WORDS.get(s["role"], s["role"]))}</td>'
                    f'<td>{"aktiv" if s["active"] else "abgeschaltet"}</td><td>{esc(s["kind"] or "—")}</td>'
                    f'<td class="muted">{esc(facts or "—")}</td>'
                    f'<td>{esc(", ".join(landscape.servers_of(DB, s["id"])) or "—")}</td>'
                    f'<td>{badge(light, light)}</td>'
                    f'<td><details><summary>Ändern</summary>{system_form(s)}'
                    f'<form method="post" action="system-delete"><input type="hidden" name="id" '
                    f'value="{esc(s["id"])}"><button class="quiet">Löschen</button></form></details></td></tr>')
    return f"""<div class="card"><h1>Systemlandschaft</h1>
<p class="hint">Ein Zielsystem ist, wohin ein Werkzeug über einen MCP-Server reicht -- ein SAP-System, eine
Jira-Seite, ein Wiki. Eine <b>Multi-System-Sicht</b> wird einmal an einem Referenzsystem ausgeprägt und für
weitere Systeme angeboten; im Chat steht jedes Werkzeug einmal, mit dem Argument <code>system</code>.
Die Liste hält nie eine Adresse oder einen Zugang. Die Ampel kommt aus der Rolle (Produktion: gelb),
wenn keine gesetzt ist, und ist nie lockerer als ein Server des Systems (RFC-0114).</p>
<div class="scroll"><table><tr><th>System</th><th>Rolle</th><th>Zustand</th><th>Art</th><th>Angaben</th>
<th>Server</th><th>Ampel</th><th></th></tr>{"".join(rows) or '<tr><td colspan=8 class=muted>Noch kein Zielsystem.</td></tr>'}</table></div></div>
<div class="card"><h2>Zielsystem anlegen</h2>{system_form(None)}</div>"""


def system_form(s):
    servers = [r["name"] for r in books.servers(DB)]
    mine = set(landscape.servers_of(DB, s["id"])) if s else set()
    boxes = "".join(
        f'<label class="checkline"><input type="checkbox" name="servers" value="{esc(n)}"'
        f'{" checked" if n in mine else ""}> {esc(n)}'
        + (f' <span class="muted">(gehört zu {esc(landscape.system_of_server(DB, n))})</span>'
           if landscape.system_of_server(DB, n) not in ("", s["id"] if s else "") else "")
        + "</label>" for n in servers)
    facts = ", ".join(f"{k}={v}" for k, v in sorted(landscape.facts_of(s).items())) if s else ""
    role = s["role"] if s else "development"
    return f"""<form class="grid" method="post" action="system-save">
<div><label>Kennung</label><input type="text" name="id" value="{esc(s["id"] if s else "")}" {"readonly" if s else "required"}></div>
<div><label>Titel</label><input type="text" name="title" value="{esc(s["title"] if s else "")}" required></div>
<div><label>Rolle</label><select name="role">{options(landscape.ROLES, role, landscape.ROLE_WORDS)}</select></div>
<div><label>Aktiv</label><select name="active">{options(("1", "0"), "1" if (not s or s["active"]) else "0", {"1": "ja", "0": "nein"})}</select></div>
<div><label>Art (sap, jira …)</label><input type="text" name="kind" value="{esc(s["kind"] if s else "")}"></div>
<div><label>Angaben (z. B. sid=A4H, client=001)</label><input type="text" name="facts" value="{esc(facts)}"></div>
<div><label>Ampel</label><select name="light">{options(("",) + books.LIGHTS, s["light"] if s else "", {"": "aus der Rolle", "green": "grün", "yellow": "gelb", "red": "rot"})}</select></div>
<div><label>Beschreibung (für den Chat)</label><input type="text" name="description" value="{esc(s["description"] if s else "")}"></div>
<div><label>Server dieses Systems</label>{boxes or '<span class="muted">noch kein Server</span>'}<input type="hidden" name="servers_seen" value="1"></div>
<div><button>Speichern</button></div></form>"""


def convert_part(name, convert=""):
    """0.38 (RFC-0114 Stufe 2): trägt die Sicht Server mehrerer Zielsysteme (Präfix-Namen),
    bietet sie das Umwandeln in eine NEUE Multi-System-Sicht an -- erst der Plan."""
    systems = []
    for s in books.view_servers(DB, name):
        sid = landscape.system_of_server(DB, s)
        if sid and sid not in systems:
            systems.append(sid)
    if len(systems) < 2:
        return ""
    opts = "".join(f'<option value="{esc(s)}"{" selected" if s == convert else ""}>{esc(s)}</option>'
                   for s in systems)
    out = (f'<h3>Umwandeln</h3><p class="hint">Diese Sicht trägt Server mehrerer Zielsysteme '
           f'({esc(", ".join(systems))}). Umwandeln legt eine <b>neue</b> Multi-System-Sicht an: die '
           f'Einträge des Referenzsystems, die anderen Systeme zugeordnet, ihre Freigaben übernommen. Diese '
           f'Sicht bleibt, wie sie ist; Schlüssel, Profile und Assistenten stellst Du selbst um.</p>'
           f'<form class="grid" method="get" action="view"><input type="hidden" name="name" value="{esc(name)}">'
           f'<div><label>Referenzsystem</label><select name="convert">{opts}</select></div>'
           f'<div><button class="quiet">Plan zeigen</button></div></form>')
    if not convert:
        return out
    plan = landscape.convert_plan(DB, name, convert)
    if plan["problems"]:
        return out + "".join(f'<p class="err">{esc(p)}</p>' for p in plan["problems"])
    rows = []
    for r in plan["rows"]:
        if r["carry"]:
            what = "Freigabe übernommen (eigener Text dieses Servers)"
        elif r["old"]:
            what = "gleich (derselbe Text wie an der Referenz)"
        else:
            what = "kommt hinzu, wenn sein Server denselben Text meldet (war für dieses System nicht in der Sicht)"
        rows.append(f'<tr><td>{esc(r["system"])}</td><td><code>{esc(r["name"])}</code></td>'
                    f'<td>{esc(r["old"] or "—")}</td><td>{esc(what)}</td></tr>')
    return out + (f'<table><tr><th>System</th><th>Eintrag</th><th>bisher</th><th>danach</th></tr>'
                  f'{"".join(rows)}</table>'
                  f'<form class="grid" method="post" action="view-convert"><input type="hidden" name="view" value="{esc(name)}">'
                  f'<input type="hidden" name="system" value="{esc(convert)}">'
                  f'<div><label>Name der neuen Sicht</label><input type="text" name="new" required '
                  f'value="{esc(name)}-multi"></div><div><button>Neue Multi-System-Sicht anlegen</button></div></form>')


def multi_card(name, convert=""):
    """Auf der Seite einer Sicht: Multi-System machen, oder Systeme und Abgleich zeigen."""
    ref = landscape.reference_of(DB, name)
    if not ref:
        cands = [s for s in landscape.systems(DB) if landscape.servers_of(DB, s["id"])]
        if not cands:
            return ('<div class="card"><h2>Multi-System-Sicht</h2><p class="hint">Erst unter '
                    '<a href="./?tab=systeme">Systemlandschaft</a> Zielsysteme mit ihren Servern anlegen.</p></div>')
        cands = [s for s in cands if set(landscape.servers_of(DB, s["id"]))
                 & set(books.view_servers(DB, name))] or cands
        opts = "".join(f'<option value="{esc(s["id"])}">{esc(s["id"])} – {esc(s["title"])}</option>' for s in cands)
        return f"""<div class="card"><h2>Multi-System-Sicht</h2>
<p class="hint">Die Einträge dieser Sicht werden zur Vorlage, das gewählte System zur Referenz; ihre Einträge
müssen auf Servern dieses Systems liegen. Weitere Systeme bekommen dieselbe Auswahl (RFC-0114).</p>
<form class="grid" method="post" action="view-multi"><input type="hidden" name="view" value="{esc(name)}">
<div><label>Referenzsystem</label><select name="system">{opts}</select></div>
<div><button>Zur Multi-System-Sicht machen</button></div></form>{convert_part(name, convert)}</div>"""
    sids = landscape.view_system_ids(DB, name)
    head = "".join(f"<th>{esc(sid)}{' (Referenz)' if sid == ref else ''}</th>" for sid in sids)
    rows = []
    for item in books.items(DB, name):
        if item["kind"] in ("resource", "template") and landscape.plain_uri(item["name"]):
            continue                    # eine Ansicht bleibt die der Referenz
        cells = []
        for sid in sids:
            state, server, _doc, note = landscape.availability(DB, name, item, sid)
            cls = {"referenz": "green", "gleich": "green", "freigegeben": "green",
                   "abweichend": "yellow", "fehlt": "red", "mehrdeutig": "red",
                   "gesperrt": "red"}.get(state, "red")
            act = ""
            if state == "abweichend":
                act = (f'<form class="inline" method="post" action="view-system-approve">'
                       f'<input type="hidden" name="view" value="{esc(name)}">'
                       f'<input type="hidden" name="system" value="{esc(sid)}">'
                       f'<input type="hidden" name="kind" value="{esc(item["kind"])}">'
                       f'<input type="hidden" name="name" value="{esc(item["name"])}">'
                       f'<button class="quiet">Für {esc(sid)} freigeben</button></form>')
            cells.append(f'<td>{badge(cls, landscape.STATE_WORDS[state])}'
                         + (f'<br><span class="muted">{esc(note)}</span>' if note and state != "freigegeben" else "")
                         + f"{act}</td>")
        kw = "" if item["kind"] == "tool" else f'<br><span class="muted">{esc(landscape.KIND_LABELS[item["kind"]])}</span>'
        rows.append(f'<tr><td><code>{esc(item["name"])}</code>{kw}</td>{"".join(cells)}</tr>')
    systems_rows = []
    for sid in sids:
        s = landscape.system_row(DB, sid)
        if not s:
            continue
        rm = ("" if sid == ref else
              f'<form class="inline" method="post" action="view-system-remove"><input type="hidden" name="view" '
              f'value="{esc(name)}"><input type="hidden" name="system" value="{esc(sid)}">'
              f'<button class="quiet">Herausnehmen</button></form>')
        systems_rows.append(f'<tr><td><code>{esc(sid)}</code> {esc(s["title"])}</td>'
                            f'<td>{esc(landscape.ROLE_WORDS.get(s["role"], s["role"]))}</td>'
                            f'<td>{"aktiv" if s["active"] else "abgeschaltet"}</td>'
                            f'<td>{badge(landscape.system_light(DB, s), landscape.system_light(DB, s))}</td><td>{rm}</td></tr>')
    others = [s for s in landscape.systems(DB) if s["id"] not in sids and landscape.servers_of(DB, s["id"])]
    add = ("" if not others else
           f'<form class="grid" method="post" action="view-system-add"><input type="hidden" name="view" value="{esc(name)}">'
           f'<div><label>Weiteres System</label><select name="system">'
           + "".join(f'<option value="{esc(s["id"])}">{esc(s["id"])} – {esc(s["title"])}</option>' for s in others)
           + '</select></div><div><button>Zuordnen</button></div></form>')
    return f"""<div class="card"><h2>Multi-System-Sicht · Referenz {esc(ref)}</h2>
<p class="hint">Im Chat steht jedes Werkzeug einmal, mit dem Argument <code>system</code> (nur aktive Systeme, deren
Ampel das Gespräch zulässt), dazu das Werkzeug <code>{landscape.SYSTEM_TOOL}</code>; ebenso jeder Prompt. Eine Ressource
steht je System einmal, ihre URI mit dem System davor (<code>{landscape.URI_PREFIX}&lt;system&gt;/…</code>). <i>gleich</i>: derselbe Text wie
an der Referenz; <i>abweichend</i>: der Server meldet einen anderen Text -- nicht angeboten, bis Du ihn für dieses
System freigibst; <i>fehlt</i>: kein Server des Systems bietet es an.</p>
<table><tr><th>System</th><th>Rolle</th><th>Zustand</th><th>Ampel</th><th></th></tr>{"".join(systems_rows)}</table>
{add}
<div class="scroll"><table><tr><th>Eintrag</th>{head}</tr>{"".join(rows) or '<tr><td class=muted>Noch kein Eintrag.</td></tr>'}</table></div></div>"""


def untrusted_hint(view):
    """0.36 (RFC-0116 §4.3, §4.4): was fremder Inhalt in dieser Sicht bedeutet -- ein Satz neben
    der Sicht, wenn ein Server mit fremdem Inhalt darin steht; auffällig, wenn er sich die
    Sicht mit anderen Servern teilt oder (von vor der Markierung) Schreibendes darin steht."""
    foreign = books.untrusted_in_view(DB, view)
    if not foreign:
        return ""
    others = [s for s in books.view_servers(DB, view) if s not in foreign]
    writing = books.writing_in_view(DB, view)
    lines = [f"Fremder Inhalt: {', '.join(foreign)} liefert Webseiten. Diese Sicht nimmt nur "
             "lesende Einträge an; eine Seite kann das Modell ansprechen, mit nichts zum Schreiben "
             "ist das Schlimmste eine falsche Antwort."]
    if others:
        lines.append(f"Die Sicht mischt Webinhalt mit Daten von {', '.join(others)}. Das ist "
                     "erlaubt, aber die Ampel ist dann die des Webservers (rot): Daten der anderen "
                     "Server landen nur nach Bestätigung der Person in einem roten Gespräch, und eine "
                     "gelesene Adresse kann sie hinaustragen.")
    if writing:
        lines.append(f"Schreibende Einträge ({', '.join(writing)}) stammen von vor der Markierung; "
                     "das Gateway liefert sie nicht aus. Bitte herausnehmen oder auf „lesen“ stellen.")
    cls = "card attention" if others or writing else "card"
    return f'<div class="{cls}">' + "".join(f"<p>{esc(x)}</p>" for x in lines) + "</div>"


APPROVAL_WORDS = {"": "ohne Freigabe", "required": "mit Freigabe (Kopfzeilen)",
                  "signed": "nur mit unterschriebener Freigabe"}


def view_page(user, roles, host, name, notice="", convert=""):
    v = books.view_row(DB, name)
    if not v:
        return page("Unbekannt", '<div class="card"><p>Diese Sicht gibt es nicht.</p></div>', user, roles)
    parts = []
    if notice:
        parts.append(f'<div class="card attention"><p>{esc(notice)}</p></div>')
    rows, compare = [], []
    for item in books.items(DB, name):
        state = books.item_state(DB, item)
        cls, word = STATE_WORDS[state]
        remove = (f'<form class="inline" method="post" action="item-remove">{item_hidden(item)}'
                  f'<button class="quiet">Herausnehmen</button></form>')
        tailored = []
        if item["description"]:
            tailored.append("eigene Beschreibung")
        if item["fixed"] != "{}":
            tailored.append("fest: " + item["fixed"])
        if item["bounds"] != "{}":
            tailored.append("Grenzen: " + item["bounds"])
        source = item["source"] if item["source"] == item["name"] else f'{item["source"]} → {item["name"]}'
        rows.append(f'<tr><td>{esc(compose.KIND_WORDS[item["kind"]])}</td><td><code>{esc(item["name"])}</code>'
                    f'<br><span class="muted">{esc(item["server"])}: {esc(source)}</span></td>'
                    f'<td>{badge(cls, word)}</td><td>{esc(books.EFFECT_WORDS[item["effect"]])}</td>'
                    f'<td class="muted">{esc("; ".join(tailored) or "—")}<br>{esc(item["approved_by"])}, '
                    f'{esc(item["approved_at"])}</td><td>{remove}</td></tr>'
                    f'<tr><td></td><td colspan=5><details><summary>Zuschnitt ändern</summary>'
                    f'{tailor_form(item)}</details></td></tr>')
        if state == "suspended":
            cat = books.catalog_row(DB, item["server"], item["kind"], item["source"])
            old = json.dumps(json.loads(item["approved_doc"]), indent=2, ensure_ascii=False)
            new = json.dumps(json.loads(cat["doc"]), indent=2, ensure_ascii=False)
            effect = (f'<label>Wirkungsklasse</label><select name="effect" style="max-width:14rem">'
                      f'{options(books.EFFECTS, item["effect"], books.EFFECT_WORDS)}</select>'
                      if item["kind"] == "tool" else "")
            compare.append(f'''<div class="card attention"><h2>„{esc(item["name"])}“: der Server meldet einen anderen Text</h2>
<p class="hint">Bis zur Bestätigung ist der Eintrag weder sichtbar noch aufrufbar. Ein Text, der in das Modell
geht, kann Anweisungen enthalten — darum wird er nicht still übernommen.</p>
<div class="side"><div><b>Freigegeben</b><pre>{esc(old)}</pre></div><div><b>Jetzt beim Server</b><pre>{esc(new)}</pre></div></div>
<form method="post" action="item-confirm">{item_hidden(item)}{effect}
<button>Neuen Text bestätigen</button></form></div>''')
    users = [k["label"] for k in books.keys(DB, include_revoked=False) if name in books.key_views(k)]
    reqs = books.view_requests(v)
    req_inputs = "".join(
        f'<div><label>{esc(REQUEST_WORDS[r])}</label><select name="req_{r}">'
        f'{options(("pass", "refuse"), reqs[r], {"pass": "durchreichen", "refuse": "ablehnen"})}'
        f'</select></div>' for r in books.REQUESTS)
    light = books.view_light(DB, name)
    parts.append(untrusted_hint(name))
    parts.append(f'''<div class="card"><h1>Sicht {esc(name)}</h1>
<p>Endpunkt <code>{esc(endpoint(host, name))}</code> · Server: {esc(", ".join(books.view_servers(DB, name)) or "noch keiner")}
· {badge(light, "Ampel " + light)} · Schlüssel: {esc(", ".join(users) or "keiner")}</p>
<p class="hint">Die Ampel einer Sicht ist die ihres schlechtesten Servers. Rückfragen eines Servers an den
Client gehen nur durch, wenn sie hier durchgereicht werden UND der Client sie kann; Sampling lässt einen
Server das Modell des Clients benutzen und ihm Daten schicken.</p>
<form class="grid" method="post" action="view-save"><input type="hidden" name="name" value="{esc(name)}">
<div><label>Höchste Wirkungsklasse der Sicht</label><select name="max_effect">{options(books.EFFECTS, v["max_effect"], books.EFFECT_WORDS)}</select></div>
<div><label>Zugänge</label><select name="ways">{options(books.WAYS, v["ways"], books.WAY_WORDS)}</select></div>
{req_inputs}<div><label>Schreiben mit Schlüssel</label><select name="approval">{options(books.APPROVAL, v["approval"], APPROVAL_WORDS)}</select></div>
<div><label>Freigabeschlüssel{" (hinterlegt)" if v["approval_key"] else ""}</label><input type="password" name="approval_key" autocomplete="off" placeholder="oaap-fk-… aus der App Vorgänge"></div><div><button class="quiet">Setzen</button></div></form></div>
{"".join(compare)}
<div class="card"><h2>Freigegebene Einträge</h2><p class="hint">Neue Einträge kommen über den Katalog eines
Servers in die Sicht. Clients sehen nur den hier festgehaltenen Text, unter dem Namen der Sicht. Ein festes
Argument verschwindet aus dem Schema, das der Client sieht; eine Grenze erlaubt nur bestimmte Werte oder ein
Muster mit <code>*</code> und <code>?</code>.</p>
<div class="scroll"><table><tr><th>Art</th><th>Name (Server: Original)</th><th>Zustand</th><th>Klasse</th>
<th>Zuschnitt, Freigabe</th><th></th></tr>
{"".join(rows) or '<tr><td colspan=6 class=muted>Leer.</td></tr>'}</table></div>
<form method="post" action="view-delete" style="margin-top:1rem"><input type="hidden" name="name" value="{esc(name)}">
<button class="quiet">Sicht löschen</button></form></div>
<div class="card"><h2>Zuordnen</h2><p class="hint">Alle Kataloge als Baum: angekreuzt = in dieser Sicht.
Haken setzen gibt frei (Name der Sicht wird vorgeschlagen, Wirkungsklasse daneben), Haken entfernen nimmt
heraus. Ein Eintrag, den sein Server nicht mehr anbietet, steht hier nicht und bleibt, wie er ist.</p>
{assign_tree(name)}</div>
{multi_card(name, convert)}{copy_card(name)}{TREE_STYLE}{TREE_SCRIPT}''')
    return page(f"Sicht {name}", "".join(parts), user, roles, "sichten")


# ------------------------------------------------------------- Auswertung

DIMENSIONS = {**greport.CONTEXT_DIMENSIONS, "view": "u.view", "kind": "u.kind", "user": "u.user_sub",
              "tool": "u.tool",
              "server": "u.server", "outcome": "u.outcome", **greport.TIME_DIMENSIONS}
REPORT = greport.Report(DIMENSIONS, [
    ("calls", "COUNT(*)"),
    ("bytes_up", "COALESCE(SUM(u.bytes_up),0)"),
    ("bytes_down", "COALESCE(SUM(u.bytes_down),0)"),
])
OUTCOME_WORDS = {"ok": "beantwortet", "tool_error": "Tool meldet Fehler",
                 "refused": "vom Gateway abgewiesen", "limited": "Grenze erreicht",
                 "upstream_error": "Server nicht erreichbar oder fehlerhaft"}
CHARTS = gcharts.Charts(
    {"calls": "Aufrufe", "bytes_up": "Bytes hoch", "bytes_down": "Bytes runter"},
    lambda o: {"ok": "good", "tool_error": "warning", "refused": "serious",
               "limited": "serious"}.get(o, "critical"),
    lambda o: OUTCOME_WORDS.get(o, o))
CHART_DIMS = {"view": "Sicht", "tool": "Eintrag", "kind": "Art", "key": "Schlüssel", "server": "Server",
              "account": "Konto", "project": "Projekt", "cost_center": "Kostenstelle",
              "owner": "Verantwortlich", "application": "Anwendung"}


def portal_base():
    """Schema und Name des Portals aus OAAP_PORTAL_URL, oder '' (ältere Plattform)."""
    from urllib.parse import urlsplit
    u = urlsplit(PORTAL_URL)
    return f"{u.scheme}://{u.netloc}" if u.scheme in ("https", "http") and u.netloc else ""


def template_link(who):
    """0.37 (RFC-0117 §3.5): für den tenant_admin der Weg zu den Vorlagen im Portal."""
    if "tenant_admin" not in (getattr(who, "roles", None) or ()):
        return ""
    base = portal_base()
    if not base:
        return ('<p class="hint">Neue Assistenten aus einer Vorlage legst Du im Portal an '
                '(Mandant → Assistenten).</p>')
    return (f'<p><a class="btn" href="{esc(base)}/assistenten" target="_blank" rel="noopener">'
            'Aus Vorlage …</a> <span class="hint">Hilfe, Recherche, Leer: die Vorlage bringt Sicht, '
            'Zugang des Chats und Modell mit. Eine exportierte Datei öffnet dort ihre Vorlage wieder '
            '(„Aus Datei“).</span></p>')


def assistants_page(user, roles, who, params):
    """0.25 (RFC-0097 Stufe 2, RFC-0101 Stufe 4): je Assistent und Revision Aufrufe,
    Ablehnungen, Fehler, Personen, Sterne und Vorgänge; Vergleich zweier Revisionen;
    Export einer Revision. Gezeigt wird, was die gemeinsame Regel diese Person sehen lässt;
    Sterne und Vorgänge nur für die, die den Assistenten pflegen, und den tenant_admin."""
    rows = [r for r in books.assistant_rows(DB)
            if r["state"] != "deleted" and arules.can_see(r["doc"], who)]
    pick = params.get("id", "")
    cards = []
    for r in rows:
        d, aid = r["doc"], r["doc"]["id"]
        manage = arules.can_manage(d, who)
        reader = manage or "tenant_admin" in who.roles
        ev = books.assistant_evaluation(DB, aid)
        trs = []
        for x in ev["revisions"]:
            rt = x["ratings"]
            dist = " ".join(f"{i}★{n}" for i, n in enumerate(rt["stars"]) if n) or "—"
            stars = (f'{rt["count"]} ({rt["persons"]} Pers.) · {esc(dist)} · 0–2: {round(rt["low"] * 100)} %'
                     if rt["count"] else "—")
            exp = ("" if x["compacted"] else
                   f' <a href="assistenten/export?id={quote(aid)}&amp;rev={x["rev"]}">Export</a>')
            trs.append(f'<tr><td>{x["rev"]}{" (geltend)" if x["rev"] == r["rev"] else ""}'
                       f'{" · verdichtet" if x["compacted"] else ""}{exp}</td>'
                       f'<td>{esc(x["changed_at"][:16].replace("T", " "))}</td>'
                       f'<td>{x["calls"]}</td><td>{x["refused"]}</td><td>{x["errors"]}</td><td>{x["persons"]}</td>'
                       + (f'<td>{stars}</td><td>{x["cases"]}</td><td>{x["accepted"]}</td>' if reader else "") + '</tr>')
        head = ('<tr><th>Revision</th><th>seit</th><th>Aufrufe</th><th>abgewiesen</th><th>Fehler</th>'
                '<th>Personen</th>' + ('<th>Sterne</th><th>Vorgänge</th><th>davon „Lösung passt“</th>'
                                       if reader else "") + '</tr>')
        revs = [x["rev"] for x in ev["revisions"] if not x["compacted"]]
        diff = ""
        if pick == aid and params.get("a", "").isdigit() and params.get("b", "").isdigit():
            lines, err = books.assistant_diff(DB, aid, int(params["a"]), int(params["b"]))
            if err:
                diff = f'<p class="muted">{esc(err)}</p>'
            else:
                body = "\n".join(lines[2:]) or "(gleich)"
                diff = f'<pre class="diff">{esc(body)}</pre>'
        form = ""
        if len(revs) >= 2:
            opts_a = "".join(f'<option{" selected" if i == 1 else ""}>{v}</option>' for i, v in enumerate(revs))
            opts_b = "".join(f'<option{" selected" if i == 0 else ""}>{v}</option>' for i, v in enumerate(revs))
            form = (f'<form method="get" action="assistenten" class="grid"><input type="hidden" name="id" value="{esc(aid)}">'
                    f'<div><label>von</label><select name="a">{opts_a}</select></div>'
                    f'<div><label>nach</label><select name="b">{opts_b}</select></div>'
                    f'<div><button>Revisionen vergleichen</button></div></form>')
        vis = d["visibility"]["scope"]
        vis_word = {"private": "privat", "context": "Team " + str(list((d["visibility"].get("context") or {}).values())[:1]),
                    "tenant": "ganzer Mandant"}.get(vis, vis)
        cards.append(
            f'<div class="card" id="a-{esc(aid)}"><h2>{esc(d.get("title") or aid)} <small class="muted">'
            f'{esc(aid)} · Revision {r["rev"]} · {esc(vis_word)}{" · deaktiviert" if r["state"] == "inactive" else ""}</small></h2>'
            f'<p class="hint">Modell <code>{esc(d.get("model", ""))}</code> · Sicht <code>{esc(d.get("view", ""))}</code>'
            + (f' · Bereich für „Nicht zufrieden“ <code>{esc(d["support_queue"])}</code>' if d.get("support_queue") else "")
            + '</p>'
            f'<div class="scroll"><table>{head}{"".join(trs)}</table></div>'
            + ("" if reader else '<p class="hint">Sterne und Vorgänge liest, wer den Assistenten pflegt, und der tenant_admin.</p>')
            + form + diff + '</div>')
    since = books.assistant_evaluation(DB, rows[0]["doc"]["id"])["usage_since"] if rows else ""
    intro = (f'<div class="card"><h1>Assistenten</h1><p class="hint">Je Revision: Aufrufe, Ablehnungen und Fehler '
             f'aus den Messzeilen (vorhanden seit {esc(since[:10]) or "—"}; ältere sind nach der Aufbewahrung '
             f'gelöscht), Personen nur gezählt. Sterne und Vorgänge „Nicht zufrieden“ aus dem Chat (RFC-0101). '
             f'Export: eine Revision als Austauschdatei ohne Besitzer und Nummer.</p>'
             + template_link(who) + '</div>')
    return page("Assistenten", intro + ("".join(cards) or '<div class="card"><p class="muted">Noch keine Assistenten.</p></div>'),
                user, roles, "assistenten")


def charts_page(user, roles, params):
    today = datetime.now(timezone.utc).date()
    try:
        first = datetime.strptime(params.get("von", ""), "%Y-%m-%d").date()
    except ValueError:
        first = today - timedelta(days=29)
    try:
        last = datetime.strptime(params.get("bis", ""), "%Y-%m-%d").date()
    except ValueError:
        last = today
    if last < first:
        first, last = last, first
    by = params.get("nach") if params.get("nach") in CHART_DIMS else "view"
    metric = params.get("mass") if params.get("mass") in CHARTS.metric_labels else "calls"
    step = "month" if (last - first).days > 92 else "day"
    frm = greport.stamp(datetime(first.year, first.month, first.day))
    to = greport.stamp(datetime(last.year, last.month, last.day) + timedelta(days=1))
    over = REPORT.usage(DB, frm, to, [step, by])
    ranked = REPORT.usage(DB, frm, to, ["tool"])
    outcomes = REPORT.usage(DB, frm, to, ["outcome"])
    body = f'''<div class="card"><h1>Auswertung</h1>
<form class="grid" method="get" action="auswertung">
<div><label>Von</label><input type="text" name="von" value="{first}"></div>
<div><label>Bis</label><input type="text" name="bis" value="{last}"></div>
<div><label>Gestapelt nach</label><select name="nach">{options(list(CHART_DIMS), by, CHART_DIMS)}</select></div>
<div><label>Maß</label><select name="mass">{options(list(CHARTS.metric_labels), metric, CHARTS.metric_labels)}</select></div>
<div><button>Zeigen</button></div></form>
<p class="hint">Dieselbe Abfrage wie <code>/report/v1/usage</code>. Volumen, keine Preise.</p></div>
<div class="card"><h2>{esc(CHARTS.metric_labels[metric])} je {"Tag" if step == "day" else "Monat"}</h2>
{CHARTS.stacked_bars(over, step, by, metric)}</div>
<div class="card"><h2>Einträge nach {esc(CHARTS.metric_labels[metric])}</h2>{CHARTS.top_list(ranked, "tool", metric)}</div>
<div class="card"><h2>Ausgänge</h2>{CHARTS.outcome_list(outcomes)}</div>'''
    return page("Auswertung", body, user, roles, "auswertung")


def keys_doc():
    out = []
    for row in DB.execute("SELECT * FROM keys ORDER BY label"):
        out.append({"key": row["label"], "created": row["created"], "revoked": row["revoked"] or None,
                    "views": books.key_views(row), "max_effect": row["max_effect"],
                    "ceiling": row["ceiling"], "budget_calls": row["budget_calls"],
                    "owner": row["owner"], "account": row["account"], "project": row["project"],
                    "cost_center": row["cost_center"], "application": row["application"],
                    "tenant": row["tenant"], "kind": row["kind"],
                    "groups": books.profile_groups(row),
                    "labels": json.loads(row["labels"]) if row["labels"] else {}})
    return {"keys": out}


# ------------------------------------------------------------- HTTP

def _int(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


# Was ein Client aufruft (eine Messzeile je Aufruf) und was er auflistet.
CALLS = {"tools/call": "tool", "resources/read": "resource", "prompts/get": "prompt"}
LISTS = {"tools/list": ("tool", "tools"), "resources/list": ("resource", "resources"),
         "resources/templates/list": ("template", "resourceTemplates"),
         "prompts/list": ("prompt", "prompts")}
LEGACY_ONLY = ("Dieses Gateway spricht MCP mit Sitzung (initialize), Protokollversionen "
               + ", ".join(upstream.TESTED) + " — 2026-07-28 noch nicht.")


def modern(msg):
    """Eine Anfrage der Ära MCP 2026-07-28: `server/discover` oder Version im `_meta`."""
    if msg.get("method") == "server/discover":
        return True
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    return "io.modelcontextprotocol/protocolVersion" in meta


WELL_KNOWN = "/.well-known/oauth-protected-resource"


def assistant_view_problem(a):
    """Die Sicht eines Assistenten muss es hier geben (via-Tür und Einrichtungstür)."""
    view = a.get("view") if isinstance(a, dict) else None
    if isinstance(view, str) and not books.view_row(DB, view):
        return [f"view: die Sicht {view} gibt es in diesem Gateway nicht."]
    return []


class Refusal(Exception):
    """Das Gateway weist einen Aufruf ab, bevor er zum Server geht."""

    def __init__(self, kind, name, reason, code, text):
        super().__init__(text)
        self.kind, self.name, self.reason, self.code, self.text = kind, name, reason, code, text


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-mcp-gateway/" + VERSION
    asked = ""          # die Ampel des Gesprächs dieser Anfrage (RFC-0101), sonst ''

    def log_message(self, fmt, *args):
        # Nur Methode und Pfad — Anfragen dieses Dienstes tragen ihren Inhalt im Körper (§10).
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def client_addr(self):
        return (self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or self.client_address[0])

    def send_bytes(self, status, ctype, body, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    def send_json(self, status, doc, headers=()):
        return self.send_bytes(status, "application/json; charset=utf-8",
                               json.dumps(doc, ensure_ascii=False).encode("utf-8"), headers)

    def send_html(self, status, text):
        self.send_bytes(status, "text/html; charset=utf-8", text.encode("utf-8"))

    def read_body(self, limit=MAX_BODY):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > limit:
            return None
        return self.rfile.read(length) if length else b""

    def identity(self):
        return self.headers.get("X-OAAP-User", ""), self.headers.get("X-OAAP-Roles", "")

    def page_who(self):
        """Wer die Betreiberseite ansieht, für die gemeinsame Regel der Assistenten: ein
        privater Assistent einer anderen Person bleibt auch hier unsichtbar (RFC-0101 §6.2)."""
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        roles = {r.strip() for r in self.identity()[1].split(",") if r.strip()}
        return arules.Who(uid, roles, AUTHZ.grants(uid) if uid else None)

    def assistant_export_download(self, query):
        aid, rev = query.get("id", ""), query.get("rev", "")
        row = books.assistant_row(DB, aid) if ASSISTANT_RE.match(aid) else None
        doc = None
        if row and row["state"] != "deleted" and arules.can_see(row["doc"], self.page_who()) \
                and (not rev or rev.isdigit()):
            doc = books.assistant_export(DB, aid, int(rev) if rev else None)
        if doc is None:
            self.send_html(404, page("Nicht gefunden", '<div class="card"><p>Diesen Assistenten oder diese '
                                     'Revision gibt es hier nicht (oder ihr Text ist verdichtet).</p></div>', "", ""))
            return
        name = f"assistent-{aid}" + (f"-rev{rev}" if rev else "") + ".json"
        self.send_bytes(200, "application/json; charset=utf-8",
                        json.dumps(doc, ensure_ascii=False, indent=1).encode(),
                        [("Content-Disposition", f'attachment; filename="{name}"')])

    def is_admin(self):
        return "admin" in [r.strip() for r in self.identity()[1].split(",")]

    def route(self):
        return self.path.split("?")[0].rstrip("/") or "/"

    # ---- MCP

    def mcp_view(self):
        rest = self.route()[len("/mcp/"):]
        return rest if rest and "/" not in rest else None

    def origin(self):
        proto = (self.headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
        return f"{proto if proto in ('http', 'https') else 'http'}://{self.headers.get('Host', 'gateway')}"

    def deny(self):
        """Die eine Antwort für jedes Scheitern (Spec §2), mit dem Weg zur Anmeldung."""
        meta = f"{self.origin()}/.well-known/oauth-protected-resource{self.route()}"
        self.send_bytes(401, "application/json; charset=utf-8", DENIED,
                        [("WWW-Authenticate", f'Bearer resource_metadata="{meta}"')])

    def authorize(self, view_name):
        """Wie `_authorize`, dazu die Ampel des Gesprächs (RFC-0101 §2.2): erst wer, dann
        ob die Sicht für diese Ampel grün genug ist. Ohne Kopfzeile unverändert."""
        key, vrow = self._authorize(view_name)
        if key is None:
            return None, None
        asked, bad = asked_light(self.headers.get(LIGHT_HEADER))
        if bad:
            self.send_json(400, rpc_error(None, -32600, bad, "bad_light"))
            return None, None
        refusal = light_refusal(asked, vrow)
        if refusal:
            self.note_refusal(403, None, "ampel-des-gespraechs")
            self.send_bytes(403, "application/json; charset=utf-8", forbidden(refusal))
            return None, None
        self.asked = asked
        return key, vrow

    def _authorize(self, view_name):
        """Zahler (Schlüssel- oder Profilzeile) und Sicht — oder die Anfrage ist beantwortet.

        Setzt `self.person` bei einer Anmeldung: Issuer, sub, Gruppen und das
        Token selbst (nur im Speicher, für Verfahren X).
        """
        self.person = None
        addr = self.client_addr()
        if failing_hard(addr):
            self.send_json(429, {"jsonrpc": "2.0", "id": None,
                                 "error": {"code": -32000, "message": "Zu viele Fehlversuche."}},
                           [("Retry-After", "60")])
            return None, None
        header = self.headers.get("Authorization", "")
        value = header[7:].strip() if header[:7].lower() == "bearer " else ""
        vrow = books.view_row(DB, view_name) if view_name else None
        carried = self.platform_person()
        if carried is not None:
            # Verfahren A: die Plattform hat geprüft (Schlüssel des Chats, seine
            # Zusicherung für diese Person) und trägt den Menschen her. Wer zahlt,
            # sagt ein Zugangsprofil mit @<chat> oder @*; ohne eines 403.
            profile = books.match_profile(DB, carried["groups"], view_name or "", "@*")
            reason = why_not(profile, vrow, "platform") if profile else None
            if not profile or reason:
                self.send_bytes(403, "application/json; charset=utf-8",
                                forbidden(reason) if reason else FORBIDDEN)
                return None, None
            self.person = carried
            return profile, vrow
        if ISSUER and signin.looks_like_token(value):
            try:
                claims = signin.check(value, ISSUER_KEYS, ISSUER, AUDIENCE)
            except signin.Invalid:
                note_failure(addr)
                self.deny()
                return None, None
            person = {"iss": claims["iss"], "sub": claims["sub"], "token": value,
                      "groups": sorted(signin.groups_of(claims))}
            profile = books.match_profile(DB, person["groups"], view_name or "")
            reason = why_not(profile, vrow, "signin") if profile else None
            if not profile or reason:
                # Gültig angemeldet, aber nicht für diese Sicht: 403, nicht 401 — eine 401
                # schickte den Client in eine neue Anmeldung, die nichts ändert (Spec §2.3).
                self.send_bytes(403, "application/json; charset=utf-8", FORBIDDEN)
                return None, None
            self.person = person
            return profile, vrow
        key = books.find(DB, value)
        if not key or key["revoked"] or key["kind"] != "key" or not entitled(key, vrow, "key"):
            note_failure(addr)
            self.deny()
            return None, None
        return key, vrow

    def readiness_for_carried(self):
        """GET /mcp/_readiness?view=<sicht> (0.17.0, RFC-0095 §5): Ist diese Sicht für die
        Person, die der Chat trägt, eingerichtet? Nur über die via-Tür; sonst dieselbe
        Ablehnung wie überall. Eine Sicht, die die Person nicht öffnen kann, zeigt keine
        Server -- ob es sie gibt, verrät die Antwort nicht."""
        carried = self.platform_person()
        if carried is None:
            self.deny()
            return
        query = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        vrow = books.view_row(DB, query.get("view", "").strip())
        profile = books.match_profile(DB, carried["groups"], vrow["name"], "@*") if vrow else None
        reason = (why_not(profile, vrow, "platform") if profile else
                  "kein Zugangsprofil erlaubt diese Sicht.")
        doc = readiness_doc(carried, vrow if not reason else None, reason or "")
        doc["view"] = query.get("view", "")[:100]
        self.send_json(200, doc)

    # ---- Assistenten (0.22.0, RFC-0097 Stufe 1) -- nur über die via-Tür, als die Person

    def assistant_who(self):
        """(Person, Who) oder (None, None) -- dann ist schon abgelehnt."""
        carried = self.platform_person()
        if carried is None:
            self.deny()
            return None, None
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles") or "").split(",") if r.strip()}
        return carried, arules.Who(carried["sub"], roles, AUTHZ.grants(carried["sub"]))

    def assistants_get(self, path):
        """GET /mcp/_assistants: die Assistenten, die diese Person benutzen darf (geltende
        Revision; inaktive nur für die, die sie pflegen). GET …/<id>[?rev=n]: eine Revision.
        GET …/<id>/revisions: die Revisionen (nur wer pflegt)."""
        carried, who = self.assistant_who()
        if who is None:
            return
        rest = path[len(ASSISTANTS_PATH):].strip("/")
        if not rest:
            out = []
            for r in books.assistant_rows(DB):
                d, manage = r["doc"], arules.can_manage(r["doc"], who)
                if r["state"] == "deleted" or not arules.can_see(d, who):
                    continue
                if r["state"] == "inactive" and not manage:
                    continue
                out.append(dict(d, rev=r["rev"], state=r["state"], changed_at=r["changed_at"],
                                manage=manage,
                                # 0.37 (RFC-0117 Stufe 2): die Vorlage, damit der Export des
                                # Chats sie nennt -- nur wenn die Einrichtung sie kennt
                                **({"from": "recipe:" + r["origin"]}
                                   if books.ORIGIN.match(r.get("origin") or "") else {})))
            self.send_json(200, {"v": 1, "assistants": out, "grants_known": who.grants is not None})
            return
        if rest == "_ratings":
            # 0.23 (RFC-0101 §6.6): die eigenen Bewertungen, über den neu gerechneten HMAC.
            self.send_json(200, {"ratings": books.ratings_mine(DB, rating_who(carried["sub"]))})
            return
        aid, _, sub = rest.partition("/")
        row = books.assistant_row(DB, aid) if ASSISTANT_RE.match(aid) else None
        if not row or row["state"] == "deleted" or not arules.can_see(row["doc"], who):
            # Wie unbekannt: ob es einen fremden privaten Assistenten gibt, verrät nichts.
            self.send_json(404, {"error": "Diesen Assistenten gibt es für dich nicht."})
            return
        manage = arules.can_manage(row["doc"], who)
        if sub == "ratings":
            # Lesen dürfen, wer ihn pflegt, und der tenant_admin (RFC-0101 §3.3) -- nur
            # für Assistenten, die sie sehen; wer A pflegt, liest nichts von B.
            if not (manage or "tenant_admin" in who.roles):
                self.send_json(403, {"error": "Die Bewertungen liest, wer den Assistenten pflegt."})
                return
            self.send_json(200, dict(books.ratings_of(DB, aid), id=aid, rev=row["rev"]))
            return
        if sub == "evaluation":
            # 0.25 (RFC-0097 Stufe 2, RFC-0101 Stufe 4): dieselben Leser wie die Bewertungen
            if not (manage or "tenant_admin" in who.roles):
                self.send_json(403, {"error": "Die Auswertung liest, wer den Assistenten pflegt."})
                return
            self.send_json(200, dict(books.assistant_evaluation(DB, aid), id=aid, rev=row["rev"]))
            return
        if sub == "diff":
            # Jede Revision liest, wer den Assistenten sieht (?rev=n) -- also auch den Vergleich.
            q = dict(parse_qsl(self.path.partition("?")[2]))
            if not (q.get("a", "").isdigit() and q.get("b", "").isdigit()):
                self.send_json(400, {"error": "a und b sind Revisionsnummern."})
                return
            lines, err = books.assistant_diff(DB, aid, int(q["a"]), int(q["b"]))
            if err:
                self.send_json(404, {"error": err})
                return
            self.send_json(200, {"id": aid, "a": int(q["a"]), "b": int(q["b"]), "diff": lines})
            return
        if sub == "revisions":
            if not manage:
                self.send_json(403, {"error": "Die Revisionen sieht, wer den Assistenten pflegt."})
                return
            self.send_json(200, {"id": aid, "rev": row["rev"],
                                 "revisions": books.assistant_revisions(DB, aid)})
            return
        if sub:
            self.send_json(404, {"error": "Diesen Pfad kennt das Gateway nicht."})
            return
        query = dict(parse_qsl(self.path.partition("?")[2]))
        want = query.get("rev", "")
        if want and not want.isdigit():
            self.send_json(400, {"error": "rev ist eine Zahl."})
            return
        if not want or int(want) == row["rev"]:
            self.send_json(200, dict(row["doc"], rev=row["rev"], state=row["state"],
                                     changed_at=row["changed_at"], manage=manage))
            return
        old = books.assistant_revision(DB, aid, int(want))
        if old is None:
            self.send_json(404, {"error": f"Revision {want} gibt es nicht."})
        elif old["doc"] is None:
            self.send_json(410, {"error": f"Revision {want} ist verdichtet (D6): Nummer, Zeit und "
                                          "wer sind geblieben, der Text nicht."})
        else:
            self.send_json(200, dict(old["doc"], rev=old["rev"], changed_at=old["changed_at"],
                                     manage=manage, current=row["rev"]))

    def assistants_post(self, path):
        """POST /mcp/_assistants: speichern (jedes Speichern = neue Revision).
        POST …/_import: eine Austauschdatei, alles oder nichts, wer importiert, wird Besitzer.
        POST …/_migrate: die alte Datei eines Chats, Besitzer bleiben (nur tenant_admin).
        POST …/<id>/state: active | inactive | deleted (wer pflegt)."""
        carried, who = self.assistant_who()
        if who is None:
            return
        raw = self.read_body()
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self.send_json(400, {"error": "Kein JSON-Objekt."})
            return
        rest = path[len(ASSISTANTS_PATH):].strip("/")
        by = carried["sub"]
        if not rest:
            # 0.37: `from` führt das Gateway (aus der Einrichtung), es kommt nie in den Assistenten
            body.pop("from", None)
            errs = self.assistant_problems(body, who)
            if errs:
                self.send_json(400, {"errors": errs})
                return
            rev = books.assistant_write(DB, self.assistant_owned(body, who), by)
            books.log(DB, by, "assistant.save", body["id"], f"rev={rev}")
            DB.commit()          # 0.22.1: log() schreibt, festgeschrieben wird hier
            self.send_json(200, {"saved": body["id"], "rev": rev})
            return
        if rest in ("_import", "_migrate"):
            migrate = rest == "_migrate"
            if migrate and "tenant_admin" not in who.roles:
                self.send_json(403, {"error": "Die Assistenten eines Chats übernimmt der tenant_admin."})
                return
            if isinstance(body, dict) and isinstance(body.get("assistants"), list):
                # 0.37 (oaap.ai.assistants 0.8): `from` sagt, aus welcher Vorlage eine Datei
                # kam -- eine Auskunft für den Assistenten im Portal, kein Teil des Assistenten
                body = dict(body, assistants=[{k: v for k, v in a.items() if k != "from"}
                                              if isinstance(a, dict) else a
                                              for a in body["assistants"]])
            errs = arules.check_document(body)
            if not errs:
                for i, a in enumerate(body["assistants"]):
                    if migrate:
                        errs += [f"[{i}] {e}" for e in arules.problems(a)]
                        errs += [f"[{i}] {e}" for e in self.assistant_view_problem(a)]
                        if books.assistant_row(DB, a["id"]):
                            errs.append(f"[{i}] {a['id']}: gibt es im Gateway schon -- nicht überschrieben.")
                    else:
                        errs += [f"[{i}] {e}" for e in self.assistant_problems(a, who)]
            if errs:
                self.send_json(400, {"errors": errs})
                return
            for a in body["assistants"]:
                doc = a if migrate else self.assistant_owned(a, who, importer=True)
                rev = books.assistant_write(DB, doc, ("migration:" if migrate else "import:") + by)
                books.log(DB, by, "assistant.migrate" if migrate else "assistant.import", a["id"],
                          f"rev={rev}")
                DB.commit()      # 0.22.1: sonst blieb der letzte Eintrag offen (bec, 19.11.)
            self.send_json(200, {"migrated" if migrate else "imported": len(body["assistants"])})
            return
        aid, _, sub = rest.partition("/")
        row = books.assistant_row(DB, aid) if ASSISTANT_RE.match(aid) else None
        if not row or row["state"] == "deleted" or not arules.can_see(row["doc"], who):
            self.send_json(404, {"error": "Diesen Assistenten gibt es für dich nicht."})
            return
        if sub == "ratings":
            self.rating_post(aid, carried, body)
            return
        if sub == "cases":
            # 0.25 (RFC-0101 Stufe 4): ein Vorgang „Nicht zufrieden“ zu aid@rev, gezählt
            rev, number = body.get("rev"), body.get("number")
            outcome = body.get("outcome") or ""
            if not (isinstance(rev, int) and not isinstance(rev, bool) and rev > 0
                    and isinstance(number, str) and CASE_NUMBER_RE.match(number)):
                self.send_json(400, {"error": "rev (Zahl) und number (V-JJJJ-NNNN) erwartet."})
                return
            # 0.26 (RFC-0103 D10): wie er ausging -- nur dieses Wort reist
            if outcome and outcome not in books.CASE_OUTCOMES:
                self.send_json(400, {"error": "outcome ist accepted, reopened oder no_response."})
                return
            if books.assistant_revision(DB, aid, rev) is None:
                self.send_json(400, {"error": f"rev: die Revision {rev} gibt es nicht."})
                return
            new = books.case_note(DB, aid, rev, rating_who(carried["sub"]),
                                  rating_mac("case", carried["sub"], number), outcome)
            self.send_json(200, {"counted": new, "rev": rev})
            return
        if sub != "state":
            self.send_json(404, {"error": "Diesen Pfad kennt das Gateway nicht."})
            return
        if not arules.can_manage(row["doc"], who):
            self.send_json(403, {"error": f"Den Assistenten {aid} darfst du nicht ändern."})
            return
        state = body.get("state")
        if state not in books.ASSISTANT_STATES:
            self.send_json(400, {"error": "state ist active, inactive oder deleted."})
            return
        books.assistant_set_state(DB, aid, state, by)
        books.log(DB, by, "assistant.state", aid, state)
        DB.commit()
        self.send_json(200, {"id": aid, "state": state})

    def rating_post(self, aid, carried, body):
        """POST /mcp/_assistants/<id>/ratings (0.23, RFC-0101 Stufe 2): 0–5 Sterne je Antwort
        oder Gespräch; dieselbe Person an derselben Stelle ersetzt, `stars: null` zieht zurück.
        Sichtbarkeit ist schon geprüft (wer ihn nicht sieht, bekam 404 wie unbekannt)."""
        r, errs = rating_check(body)
        if not errs and books.assistant_revision(DB, aid, r["rev"]) is None:
            errs.append(f"rev: die Revision {r['rev']} gibt es nicht.")
        if errs:
            self.send_json(400, {"errors": errs})
            return
        who = rating_who(carried["sub"])
        slot = rating_mac("slot", carried["sub"], r["conversation"], r["scope"],
                          r["round"] if r["scope"] == "answer" else 0)
        if r["stars"] is None:
            books.rating_withdraw(DB, aid, who, slot)
            self.send_json(200, {"withdrawn": True})
            return
        books.rating_write(DB, aid, who, slot, r)
        self.send_json(200, {"rated": r["stars"], "rev": r["rev"], "scope": r["scope"],
                             "text": r["text"] is not None})

    def assistant_view_problem(self, a):
        return assistant_view_problem(a)

    def assistant_owned(self, a, who, importer=False):
        """Wem er gehört: bei einem bestehenden der bisherige Besitzer, sonst wer speichert.
        Wer importiert, wird Besitzer (RFC-0097 §2.3)."""
        old = None if importer else books.assistant_row(DB, a["id"])
        owner = old["doc"].get("owner") if old and old["state"] != "deleted" else who.uid
        if (a.get("visibility") or {}).get("scope") == "private":
            owner = who.uid
        return dict(a, owner=owner or who.uid)

    def assistant_problems(self, a, who):
        """Sätze, warum diese Person diesen Assistenten so nicht speichern darf -- oder []."""
        if isinstance(a, dict):
            extra = sorted(set(a) & set(ASSISTANT_RECORD_FIELDS))
            if extra:
                return [f"{', '.join(extra)}: führt das Gateway, nicht wer speichert."]
            a = dict(a, owner=a.get("owner") or who.uid or "-")
        errs = arules.problems(a)
        if errs:
            return errs
        errs = self.assistant_view_problem(a)
        old = books.assistant_row(DB, a["id"])
        if old and old["state"] != "deleted" and not arules.can_manage(old["doc"], who):
            errs.append(f"Den Assistenten {a['id']} darfst du nicht ändern.")
        elif not arules.can_manage(self.assistant_owned(a, who), who):
            errs.append("So darfst du keinen Assistenten anlegen: "
                        + {"tenant": "für den ganzen Mandanten nur als tenant_admin.",
                           "context": "für ein Team nur, wer dort Assistenten verwalten darf.",
                           }.get(a["visibility"]["scope"], "nur für dich selbst."))
        return errs

    def views_for_carried(self):
        """GET /mcp/_views (0.14.0): die Sichten, die die Person aus diesem Chat
        öffnen kann -- für die Suchhilfe im Formular „Assistent“. Nur über die
        via-Tür der Plattform (sie allein setzt Chat, Person und Zusicherung);
        sonst dieselbe Antwort wie jede Ablehnung. `_views` kann nie eine Sicht
        sein (ein Name beginnt mit Buchstabe oder Ziffer). Die Verwaltung des
        Mandanten sieht auch die übrigen Sichten, je mit dem Grund."""
        carried = self.platform_person()
        if carried is None:
            self.deny()
            return
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles") or "").split(",")}
        see_all = bool(roles & {"tenant_admin", "server_admin", "admin"})
        rows = books.views_for_person(DB, carried["groups"], "@*",
                                      lambda prof, v: why_not(prof, v, "platform"), see_all)
        self.send_json(200, {"v": 1, "client": carried["client"], "all": see_all, "views": rows})

    def platform_person(self):
        """Der Mensch, den die Plattform durch einen Chat herträgt (Verfahren A), oder None.

        Nur wenn alle drei da sind: der Name des Chats (von der Tür gesetzt), die
        Kennung der Person und die Zusicherung an dieses Gateway. Auf /mcp
        entfernt die Plattform alle drei, wenn ein Client sie selbst schickt; nur
        die via-Tür der Plattform setzt sie. Die Zusicherung prüft das Gateway
        nicht (es hat den Schlüssel nicht) — es reicht sie an eine Quelle weiter,
        und dort prüft die Plattform sie erneut."""
        client = (self.headers.get(CLIENT_HEADER) or "").strip()
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        assertion = (self.headers.get("X-OAAP-User-Assertion") or "").strip()
        if not (client and uid and assertion):
            return None
        m = ASSISTANT_REV_RE.match((self.headers.get(ASSISTANT_HEADER) or "").strip())
        return {"iss": "platform", "sub": uid, "assertion": assertion, "client": client,
                "assistant": m.group(1) if m else "",
                "assistant_rev": int(m.group(2)) if m and m.group(2) else 0,
                "confirmed": "yes" if (self.headers.get(CONFIRMED_HEADER) or "").strip().lower()
                == "yes" else "",
                "groups": ["@" + client], "procedure": "A"}

    def rate_id(self, key):
        """Rate je Schlüssel — bei einem Profil je Mensch (Spec §2.3)."""
        return key["id"] + (":" + self.person["sub"] if self.person else "")

    def session_of(self, key, vrow):
        sid = self.headers.get("Mcp-Session-Id", "")
        with _SESS_LOCK:
            sess = SESSIONS.get(sid)
        subject = (self.person["iss"], self.person["sub"]) if self.person else None
        if not sess or sess.key_id != key["id"] or sess.view != vrow["name"] \
                or sess.subject != subject:
            return None
        sess.last = time.monotonic()
        if self.person:
            sess.person = self.person            # das jüngste Token — für Verfahren X
        return sess

    def do_POST(self):
        path = self.route()
        if path == SETUP_PATH:
            self.handle_setup("POST")
            return
        if path == ASSISTANTS_PATH or path.startswith(ASSISTANTS_PATH + "/"):
            self.assistants_post(path)
            return
        if path.startswith("/mcp/"):
            self.handle_mcp_post()
            return
        if path in ("/me/credential", "/me/credential-delete", "/me/catalog-read"):
            self.handle_me_post(path)
            return
        if path in ("/issue", "/revoke", "/key-limits", "/source-sync", "/context", "/server-save",
                    "/server-delete", "/personal-delete",
                    "/server-read", "/view-save", "/view-delete", "/item-add", "/item-remove",
                    "/view-copy", "/view-items", "/server-rename",
                    "/item-confirm", "/item-tailor",
                    # 0.32 (RFC-0114)
                    "/system-save", "/system-delete", "/view-multi", "/view-system-add",
                    "/view-system-remove", "/view-system-approve",
                    # 0.38 (RFC-0114 Stufe 2)
                    "/view-convert"):
            self.handle_admin_post(path)
            return
        self.send_json(404, {"error": "Diesen Pfad kennt das Gateway nicht."})

    def note_refusal(self, status, method, why):
        """Eine Zeile im Protokoll je abgelehnter MCP-Anfrage: Sicht, Methode, Grund.

        Nie Parameter, nie ein Token. Anlass: am 07.11. bekam Claude Code nach der
        Anmeldung einmal eine 400, und nichts sagte, auf welche Anfrage.
        """
        name = method if isinstance(method, str) else "-"
        name = "".join(ch for ch in name[:60] if ch.isprintable() and ch != " ") or "-"
        sid = "mit" if self.headers.get("Mcp-Session-Id") else "ohne"
        print(f"MCP {status} {self.mcp_view() or '-'} methode={name} sitzung={sid} grund={why}",
              file=sys.stderr, flush=True)

    def handle_mcp_post(self):
        raw = self.read_body()
        key, vrow = self.authorize(self.mcp_view())
        if key is None:
            return
        if raw is None:
            self.send_json(413, rpc_error(None, -32600, f"Anfrage größer als {MAX_BODY} Bytes."))
            return
        ok, wait = LIMITER.allow(self.rate_id(key), key["rate_per_min"] or DEFAULT_RATE)
        if not ok:
            self.send_json(429, rpc_error(None, -32000, "Zu viele Anfragen dieses Schlüssels je "
                                                        "Minute.", "rate", wait),
                           [("Retry-After", str(wait))])
            return
        try:
            msg = json.loads(raw)
        except ValueError:
            self.note_refusal(400, None, "kein-json")
            self.send_json(400, rpc_error(None, -32700, "Kein gültiges JSON."))
            return
        if isinstance(msg, list):
            self.note_refusal(400, None, "stapel")
            self.send_json(400, rpc_error(None, -32600, "Stapel (JSON-RPC-Batches) werden nicht "
                                                        "unterstützt."))
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            self.note_refusal(400, None, "kein-jsonrpc-2.0")
            self.send_json(400, rpc_error(None, -32600, "Keine JSON-RPC-2.0-Nachricht."))
            return
        method, msg_id = msg.get("method"), msg.get("id")
        if method == "initialize":
            self.mcp_initialize(key, vrow, msg)
            return
        sess = self.session_of(key, vrow)
        if sess is None:
            if not self.headers.get("Mcp-Session-Id") and modern(msg):
                # MCP 2026-07-28 ohne Sitzung (Spec §6.2): eine 400 OHNE modernen
                # Fehlercode — daran erkennt ein Client dieser Ära einen Server mit
                # initialize und fällt zurück. Ein Code aus -32020…-32099 hieße
                # „moderner Server“, und der Client käme nie zurück.
                books.count_protocol(DB, key["id"], vrow["name"], "server/discover"
                                     if method == "server/discover" else "modern")
                self.note_refusal(400, method, "modern-ohne-sitzung")
                self.send_json(400, rpc_error(msg_id, -32600, LEGACY_ONLY))
                return
            self.note_refusal(404 if self.headers.get("Mcp-Session-Id") else 400, method,
                              "sitzung-unbekannt")
            self.send_json(404 if self.headers.get("Mcp-Session-Id") else 400, SESSION_UNKNOWN)
            return
        header_version = self.headers.get("MCP-Protocol-Version")
        if header_version and header_version != sess.version:
            self.note_refusal(400, method, f"version-{header_version[:20]}-statt-{sess.version}")
            self.send_json(400, rpc_error(msg_id, -32600, f"MCP-Protocol-Version {header_version} "
                                                          f"passt nicht zur Sitzung ({sess.version})."))
            return
        if method is None:                     # Antwort des Clients auf eine Rückfrage
            self.relay_response(sess, key, vrow, msg)
            self.send_bytes(202, "application/json", b"")
            return
        if msg_id is None:                     # Mitteilung
            books.count_protocol(DB, key["id"], vrow["name"], method)
            if method == "notifications/cancelled":
                self.forward_cancel(sess, msg)
            self.send_bytes(202, "application/json", b"")
            return
        if method in CALLS:
            self.mcp_call(sess, key, vrow, msg, len(raw))
            return
        books.count_protocol(DB, key["id"], vrow["name"], method)
        if method == "ping":
            self.send_json(200, rpc_result(msg_id, {}))
        elif method in LISTS:
            kind, field = LISTS[method]
            if kind == "tool" and landscape.is_multi(DB, vrow["name"]):
                entries = multi_tools(vrow, key, sess.version, self.asked)
            elif landscape.is_multi(DB, vrow["name"]):
                entries = multi_entries(vrow, key, kind, sess.version, self.asked)
            else:
                entries = [compose.shaped(kind, i, sess.version)
                           for _, i in sorted(visible_items(vrow, key, kind).items())]
            self.send_json(200, rpc_result(msg_id, {field: entries}))
        else:
            self.send_json(200, rpc_error(msg_id, -32601, f"Methode {method} wird in dieser "
                                                          "Version nicht angeboten."))

    def mcp_initialize(self, key, vrow, msg):
        books.count_protocol(DB, key["id"], vrow["name"], "initialize")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        asked = params.get("protocolVersion")
        version = asked if asked in upstream.TESTED else upstream.NEWEST
        sess = DownSession(key["id"], vrow["name"], version, params.get("capabilities"),
                           self.person)
        with _SESS_LOCK:
            SESSIONS[sess.id] = sess
        kinds = {i["kind"] for i in books.items(DB, vrow["name"])}
        caps = {"tools": {"listChanged": False}}
        if kinds & {"resource", "template"}:
            caps["resources"] = {"subscribe": False, "listChanged": False}
        if "prompt" in kinds:
            caps["prompts"] = {"listChanged": False}
        self.send_json(200, rpc_result(msg.get("id"), {
            "protocolVersion": version,
            "capabilities": caps,
            "serverInfo": {"name": vrow["name"], "version": VERSION},
        }), [("Mcp-Session-Id", sess.id)])

    def forward_cancel(self, sess, msg):
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        with sess.lock:
            name = sess.inflight.get(json.dumps(params.get("requestId")))
            us = sess.upstream.get(name) if name else None
        srow = books.server_row(DB, name) if name else None
        if not us or not srow:
            return
        try:
            server, url = server_access(srow, sess.person)
            upstream.post(server, url, msg, session_id=us.id, version=us.version, timeout=10)
        except upstream.UpstreamError:
            pass

    def relay_response(self, sess, key, vrow, msg):
        """Die Antwort des Clients auf eine durchgereichte Rückfrage geht an den Server, der fragte."""
        with sess.lock:
            pending = sess.pending.pop(str(msg.get("id")), None)
        if not pending:
            return                              # nichts gefragt, oder schon beantwortet
        name, us, server_id = pending
        books.count_protocol(DB, key["id"], vrow["name"], "client-response")
        srow = books.server_row(DB, name)
        if not srow:
            return
        out = {"jsonrpc": "2.0", "id": server_id}
        if "error" in msg:
            out["error"] = msg["error"]
        else:
            out["result"] = msg.get("result") if isinstance(msg.get("result"), dict) else {}
        try:
            server, url = server_access(srow, sess.person)
            upstream.post(server, url, out, session_id=us.id, version=us.version, timeout=10)
        except upstream.UpstreamError:
            pass

    def resolve(self, vrow, key, method, params):
        """Welcher Eintrag ist gemeint? Gibt (kind, item, name_für_messung) oder wirft Refusal."""
        if method == "tools/call":
            name = params.get("name")[:200] if isinstance(params.get("name"), str) else ""
            if name == landscape.SYSTEM_TOOL and landscape.is_multi(DB, vrow["name"]):
                return "tool", None, name          # 0.32: das Werkzeug des Gateways
            item = visible_items(vrow, key, "tool").get(name)
            if item is None:
                raise Refusal("tool", name, *refusal(vrow, "tool", name))
            return "tool", item, name
        if method == "prompts/get":
            name = params.get("name")[:200] if isinstance(params.get("name"), str) else ""
            item = visible_items(vrow, key, "prompt").get(name)
            if item is None:
                raise Refusal("prompt", name, *refusal(vrow, "prompt", name))
            return "prompt", item, name
        uri = params.get("uri") if isinstance(params.get("uri"), str) else ""
        item = visible_items(vrow, key, "resource").get(uri)
        if item is not None:
            return "resource", item, uri[:500]
        hits = [i for t, i in sorted(visible_items(vrow, key, "template").items())
                if uri and compose.template_match(t, uri)]
        if len(hits) == 1:
            return "template", hits[0], hits[0]["name"]
        # Eine URI, die niemand freigab, landet nicht in der Messzeile: In ihr können Werte stehen.
        if len(hits) > 1:
            raise Refusal("resource", "", "ambiguous", -32602,
                          "Diese URI passt zu mehreren Vorlagen der Sicht; der Betreiber muss sie "
                          "eindeutig machen.")
        known = books.item_row(DB, vrow["name"], "resource", uri)
        if known:
            raise Refusal("resource", uri[:500], *refusal(vrow, "resource", uri))
        raise Refusal("resource", "", *refusal(vrow, "resource", ""))

    def mcp_call(self, sess, key, vrow, msg, bytes_up):
        """Ein Aufruf — Tool, Resource, Prompt: prüfen, begrenzen, weiterreichen, messen — eine Messzeile."""
        t0 = time.monotonic()
        msg_id, method = msg.get("id"), msg.get("method")
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        state = {"down": 0, "outcome": "upstream_error", "reason": "", "sse": False,
                 "kind": CALLS[method], "name": "", "server": "", "source": "",
                 "status": 0, "door": "server"}

        def done(outcome, reason="", status=0, door=None):
            # 0.15 (RFC-0095 D3): wer abgelehnt hat und mit welchem HTTP-Status.
            state["outcome"], state["reason"], state["status"] = outcome, reason, status
            state["door"] = DEFAULT_DOOR.get(outcome, "") if door is None else door

        def answer(doc, status=200, headers=()):
            if state["sse"]:
                state["down"] += self.sse_event(doc)
            else:
                state["down"] += self.send_json(status, doc, headers)

        multi = landscape.is_multi(DB, vrow["name"])
        uri_system = ""
        if multi and method == "resources/read":
            # 0.38 (RFC-0114 Stufe 2): die URI trägt das System; gesucht wird ohne es
            uri_system, inner = landscape.split_uri(params.get("uri"))
            if uri_system:
                params = dict(params, uri=inner)
        try:
            if multi and method == "resources/read" and not uri_system \
                    and not landscape.plain_uri(params.get("uri")):
                done("refused", "system_missing")
                answer(rpc_error(msg_id, -32602, f"Sicht {vrow['name']}: In einer Multi-System-Sicht "
                                 f"trägt die URI ihr Zielsystem: {landscape.URI_PREFIX}<system>/<uri> "
                                 f"(Werkzeug „{landscape.SYSTEM_TOOL}“ nennt sie)."))
                return
            try:
                kind, item, measured = self.resolve(vrow, key, method, params)
            except Refusal as exc:
                state["kind"], state["name"] = exc.kind, exc.name
                done("refused", exc.reason)
                answer(rpc_error(msg_id, exc.code, exc.text))
                return
            if item is None:
                # 0.32 (RFC-0114 §4.3): die Systeme der Sicht -- nie Adresse oder Zugang
                state.update(kind="tool", name=measured, server="", source="")
                rank = landscape.ceiling_rank(key["ceiling"], self.asked)
                land = landscape.landscape_doc(DB, vrow["name"], rank)
                done("ok", door="")
                answer(rpc_result(msg_id, {"content": [{"type": "text", "text": json.dumps(
                    land, ensure_ascii=False, indent=1)}], "structuredContent": land}))
                return
            state.update(kind=kind, name=measured, server=item["server"], source=item["source"])
            appr = vrow["approval"] if "approval" in vrow.keys() else ""
            if kind == "tool" and not self.person and item["effect"] != "read" and appr == "signed":
                # 0.39 (RFC-0105 §7.1): das Gateway prüft die Freigabe selbst -- unterschrieben
                # mit dem Freigabeschlüssel der Sicht, genau dieser Aufruf, genau einmal
                try:
                    fresh = books.view_row(DB, vrow["name"])
                    fk = VAULT.open_(MASTER, fresh["approval_key"]) if fresh["approval_key"] else ""
                    p = signed_approval.verify(fk, self.headers.get(signed_approval.HEADER), measured,
                                               params.get("arguments"))
                except (signed_approval.Invalid, VaultError) as exc:
                    done("refused", "needs_approval", door="gateway")
                    answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {measured} ändert etwas; "
                                     f"hier nur mit einer unterschriebenen Freigabe. {exc}"))
                    return
                if not books.use_approval(DB, p["jti"], vrow["name"], measured, p["by"]):
                    done("refused", "approval_used", door="gateway")
                    answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: diese Freigabe ist schon "
                                     "benutzt; jeder Aufruf braucht seine eigene."))
                    return
                state["approved_by"] = p["by"][:120]
            if kind == "tool" and not self.person and item["effect"] != "read" \
                    and appr == "required":
                # 0.34 (RFC-0105 §7.1): in dieser Sicht schreibt ein Schlüssel nur mit Freigabe
                who_ok = " ".join((self.headers.get(APPROVED_HEADER) or "").split())[:120]
                if (self.headers.get(CONFIRMED_HEADER) or "").strip().lower() != "yes" or not who_ok:
                    done("refused", "needs_approval", door="gateway")
                    answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {measured} ändert etwas; "
                                     "hier nur mit der Freigabe eines Menschen (X-OAAP-Confirmed: yes, "
                                     "X-OAAP-Approved-By)."))
                    return
                state["approved_by"] = who_ok
            doc = json.loads(item["approved_doc"])
            server_name = item["server"]
            arguments = params.get("arguments")
            if multi and kind in ("resource", "template") and not uri_system:
                pass                        # 0.38: eine Ansicht (ui://) -- die der Referenz
            elif multi:
                # 0.32 (RFC-0114 §4.2): das System aus dem Argument, geprüft, bevor etwas
                # hinausgeht (S2); der Text ist der für dieses System freigegebene.
                # 0.38 (Stufe 2): ebenso Prompts (Argument) und Ressourcen (URI).
                if kind in ("resource", "template"):
                    arguments = {landscape.SYSTEM_ARG: uri_system}
                try:
                    sysrow, server_name, doc, arguments = landscape.pick(
                        DB, vrow["name"], item, arguments,
                        landscape.ceiling_rank(key["ceiling"], self.asked))
                except landscape.Refused as exc:
                    done("refused", exc.reason)
                    answer(rpc_error(msg_id, -32602, f"Sicht {vrow['name']}: {exc.text}"))
                    return
                state.update(server=server_name, system=sysrow["id"])
            try:
                if kind == "tool":
                    args = compose.prepare_call(doc, json.loads(item["fixed"]),
                                                json.loads(item["bounds"]), arguments)
                    out_params = {"name": item["source"], "arguments": args}
                elif kind == "prompt":
                    args = compose.check_prompt_arguments(doc, arguments)
                    out_params = {"name": item["source"]}
                    if args:
                        out_params["arguments"] = args
                else:
                    out_params = {"uri": params["uri"]}
            except argcheck.Refused as exc:
                done("refused", "arguments")
                answer(rpc_error(msg_id, -32602, str(exc)))
                return
            meta = params.get("_meta")
            if isinstance(meta, dict) and "progressToken" in meta:
                out_params["_meta"] = {"progressToken": meta["progressToken"]}
            if key["budget_calls"] and books.calls_used(DB, key["id"]) >= key["budget_calls"]:
                done("limited", "budget")
                answer(rpc_error(msg_id, -32001, "Das Budget dieses Schlüssels ist aufgebraucht.",
                                 "budget"), status=429)
                return
            srow = books.server_row(DB, server_name)
            g = guard_of(srow)
            try:
                probe = g.enter(self.rate_id(key))
            except guard.Limited as exc:
                done("limited", exc.reason)
                answer(rpc_error(msg_id, -32001, f"Sicht {vrow['name']}: {exc}", exc.reason,
                                 exc.retry_after))
                return
            failed = False
            if kind == "resource" and "content_sha256" in doc:
                # RFC-0075 D3: bei einer Ansicht ist der Inhalt Code -- er muss der
                # freigegebene sein, sonst geht nichts an den Client.
                plain_answer = answer

                def answer(doc_out, status=200, headers=(), _doc=doc):
                    if isinstance(doc_out, dict) and isinstance(doc_out.get("result"), dict) \
                            and not compose.ui_content_matches(_doc, doc_out["result"]):
                        done("refused", "ui_changed")
                        doc_out = rpc_error(msg_id, -32002, "Diese Ansicht hat sich seit der Freigabe "
                                                            "geändert; ein Betreiber muss sie neu "
                                                            "bestätigen.")
                    return plain_answer(doc_out, status, headers)
            out = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": out_params}
            inflight = json.dumps(msg_id)
            with sess.lock:
                sess.inflight[inflight] = srow["name"]
            try:
                failed = self.forward(sess, vrow, srow, out, g.limits, state, answer, done)
            finally:
                with sess.lock:
                    sess.inflight.pop(inflight, None)
                g.leave(probe, failed)
        finally:
            books.record(DB, key, vrow["name"], state["kind"], state["name"], state["server"],
                         state["source"], state["outcome"], state["reason"],
                         (time.monotonic() - t0) * 1000, bytes_up, state["down"],
                         person=self.person, procedure=state.get("procedure", "T"),
                         status=state["status"], door=state["door"], light=self.asked,
                         system=state.get("system", ""), approved_by=state.get("approved_by", ""))
            if state["sse"]:
                self.close_connection = True

    def forward(self, sess, vrow, srow, out, lim, state, answer, done):
        """Zum Server und zurück. Gibt True zurück, wenn der WEG scheiterte (Sicherungsautomat)."""
        msg_id = out["id"]
        wants_sse = "text/event-stream" in (self.headers.get("Accept") or "")
        reqs = books.view_requests(books.view_row(DB, vrow["name"]) or vrow)
        if srow["auth_kind"] == "propagate":
            state["procedure"] = "P"
        elif srow["auth_kind"] == "personal":
            state["procedure"] = "U"
        elif self.person and self.person.get("procedure") == "A":
            state["procedure"] = "A"
        elif srow["auth_kind"] == "exchange":
            state["procedure"] = "X"
        elif srow["auth_kind"] == "platform":
            state["procedure"] = "I"
        try:
            server, url = server_access(srow, self.person)
        except NoPersonal as exc:
            # RFC-0095 §4.3: abgelehnt, bevor etwas hinausgeht; der Automat zählt es nicht.
            done("refused", "personal", door="gateway")
            answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {exc}"))
            return False
        except NoPerson as exc:
            # Kein Rückfall auf einen technischen Zugang (D8) — und kein Fehler auf dem
            # Weg zum Server: Der Sicherungsautomat zählt das nicht.
            done("refused" if not self.person or self.person.get("procedure") == "A"
                 or srow["auth_kind"] == "propagate" else "upstream_error",
                 "destination" if srow["auth_kind"] == "propagate" else "exchange",
                 door="gateway")
            answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {exc}"))
            return False
        except upstream.UpstreamError as exc:
            done("upstream_error", exc.kind, exc.status)
            answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: Server nicht nutzbar — {exc}"))
            return True

        def stream():
            if not state["sse"]:
                self.start_sse()
                state["sse"] = True

        def current():
            with sess.lock:
                return sess.upstream.get(srow["name"])

        def to_server(doc):
            us = current()
            if us is None:
                return
            try:
                upstream.post(server, url, doc, session_id=us.id, version=us.version, timeout=10)
            except upstream.UpstreamError:
                pass

        def on_message(m):
            if "method" in m and "id" in m:
                # Rückfrage des Servers an den Client (Spec §6.1).
                if m["method"] == "ping":
                    to_server(rpc_result(m["id"], {}))
                    return False
                what = books.REQUEST_METHODS.get(m["method"])
                if what and reqs[what] == "pass" and what in sess.client_caps and wants_sse:
                    gid = "oaap-" + secrets.token_urlsafe(12)
                    asked.append(gid)
                    us = current()              # nimmt die Sperre selbst — nicht darin aufrufen
                    with sess.lock:
                        sess.pending[gid] = (srow["name"], us, m["id"])
                    stream()
                    state["down"] += self.sse_event({**m, "id": gid})
                    return False
                why = ("Die Sicht reicht diese Rückfrage nicht an den Client weiter."
                       if not what or reqs[what] != "pass" else
                       "Der Client kann diese Rückfrage nicht beantworten.")
                to_server(rpc_error(m["id"], -32601, why))
                return False
            if m.get("method") in ("notifications/progress", "notifications/message") and wants_sse:
                stream()
                state["down"] += self.sse_event(m)
            return False

        asked = []                              # Rückfragen dieses Aufrufs: enden mit ihm
        try:
            result = self._forward_loop(sess, vrow, srow, out, lim, reqs, server, url, on_message,
                                        answer, done)
        finally:
            with sess.lock:
                for gid in asked:
                    sess.pending.pop(gid, None)
        return result

    def _forward_loop(self, sess, vrow, srow, out, lim, reqs, server, url, on_message, answer, done):
        msg_id = out["id"]
        for attempt in (1, 2):
            try:
                us = self.upstream_session(sess, srow, server, url, lim, reqs)
                ex = upstream.post(server, url, out, session_id=us.id, version=us.version,
                                   timeout=lim["timeout_s"], max_bytes=lim["max_bytes"],
                                   on_message=on_message)
                break
            except upstream.SessionGone:
                with sess.lock:
                    sess.upstream.pop(srow["name"], None)
                if attempt == 2:
                    done("upstream_error", "session")
                    answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: der Server verliert "
                                                     "die Sitzung."))
                    return True
            except upstream.Refused as exc:
                # Die Plattform lehnt diesen Menschen ab (Route, Bindung, Token): sein
                # Satz geht weiter, der Sicherungsautomat zählt es nicht (RFC-0073 §4.1).
                with sess.lock:
                    sess.upstream.pop(srow["name"], None)
                if server.get("door") == "personal":
                    # Verfahren U: der Server lehnt DIESEN Zugang ab -- die Person erneuert ihn.
                    done("refused", "credential", exc.status, "server")
                    answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: Der Server lehnt deinen "
                                                     f"Zugang ab (HTTP {exc.status}) -- erneuere ihn "
                                                     "auf /me."))
                    return False
                via = "destination" if server.get("door") == "destination" else "platform"
                done("refused", via, exc.status, via)
                answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {exc}"))
                return False
            except upstream.TooLarge as exc:
                done("limited", "size")
                answer(rpc_error(msg_id, -32001, f"Sicht {vrow['name']}: Die Antwort ist größer "
                                                 f"als {exc.limit} Bytes und wird nicht "
                                                 "weitergereicht — auch nicht gekürzt.", "size"))
                return False
            except upstream.UpstreamError as exc:
                if exc.kind == "version":
                    books.note_server(DB, srow["name"], error=str(exc))
                done("upstream_error", exc.kind, exc.status)
                answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: Server nicht nutzbar — "
                                                 f"{exc}"))
                return True
        final = ex.messages[-1]
        if "error" in final:
            done("upstream_error", "jsonrpc")
        elif isinstance(final.get("result"), dict) and final["result"].get("isError"):
            done("tool_error")
        else:
            done("ok")
        answer(final)
        return False

    def upstream_session(self, sess, srow, server, url, lim, reqs):
        with sess.lock:
            us = sess.upstream.get(srow["name"])
            if us is None:
                # Zugesagt wird nur, was die Sicht durchreicht UND der Client kann.
                caps = {r: {} for r in books.REQUESTS if reqs[r] == "pass" and r in sess.client_caps}
                us = upstream.open_session(server, url, timeout=min(30, lim["timeout_s"]),
                                           max_bytes=lim["max_bytes"], capabilities=caps)
                sess.upstream[srow["name"]] = us
                if us.version != srow["version"]:
                    books.note_server(DB, srow["name"], version=us.version)
            return us

    def start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()

    def sse_event(self, doc):
        data = ("event: message\ndata: " + json.dumps(doc, ensure_ascii=False) + "\n\n").encode()
        self.wfile.write(data)
        self.wfile.flush()
        return len(data)

    def resource_metadata(self, rest):
        """RFC 9728 für jede Sicht gleich — ob es sie gibt, verrät das nicht (Spec §2.2)."""
        if not ISSUER or not (rest == "" or (rest.startswith("/mcp/") and "/" not in rest[5:]
                                              and rest[5:])):
            self.send_json(404, {"error": "Keine Anmeldung an diesem Gateway."})
            return
        doc = {"resource": self.origin() + (rest or "/mcp"),
               "authorization_servers": [ISSUER],
               "bearer_methods_supported": ["header"],
               "resource_name": rest[5:] or "OAAP MCP-Gateway"}
        self.send_json(200, doc, [("Cache-Control", "max-age=300")])

    def do_DELETE(self):
        if not self.route().startswith("/mcp/"):
            self.send_json(404, {"error": "Diesen Pfad kennt das Gateway nicht."})
            return
        key, vrow = self.authorize(self.mcp_view())
        if key is None:
            return
        sess = self.session_of(key, vrow)
        if sess is None:
            self.send_json(404, SESSION_UNKNOWN)
            return
        books.count_protocol(DB, key["id"], vrow["name"], "DELETE")
        end_session(sess)
        self.send_bytes(204, "application/json", b"")

    # ---- GET

    def do_GET(self):
        path = self.route()
        if path == "/healthz":
            self.send_json(200, {"status": "ok", "version": VERSION})
            return
        if path == SETUP_PATH:
            self.handle_setup("GET")
            return
        if path.startswith(WELL_KNOWN):
            self.resource_metadata(path[len(WELL_KNOWN):])
            return
        if path == VIEWS_PATH:
            self.views_for_carried()
            return
        if path == READINESS_PATH:
            self.readiness_for_carried()
            return
        if path == CLIENT_CONFIG_PATH:
            self.client_config_for_key()
            return
        if path == ASSISTANTS_PATH or path.startswith(ASSISTANTS_PATH + "/"):
            self.assistants_get(path)
            return
        if path.startswith("/mcp"):
            # Kein vom Server ausgehender Strom in 0.1 (Spec §6). Gleich für jede
            # Sicht, ob es sie gibt oder nicht: Hier wird nichts verraten.
            self.send_bytes(405, "application/json", b"", [("Allow", "POST, DELETE")])
            return
        if path.startswith("/report"):
            self.handle_report(path)
            return
        if path == "/me" or path.startswith("/me/"):
            self.handle_me(path)
            return
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Diese Seite ist der '
                                     'Verwaltung vorbehalten.</p></div>', "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        query = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        if path == "/server":
            self.send_html(200, server_page(user, roles, host, query.get("name", "")))
        elif path == "/view":
            self.send_html(200, view_page(user, roles, host, query.get("name", ""),
                                          convert=query.get("convert", "")))
        elif path == "/auswertung":
            self.send_html(200, charts_page(user, roles, query))
        elif path == "/assistenten":
            self.send_html(200, assistants_page(user, roles, self.page_who(), query))
        elif path == "/assistenten/export":
            self.assistant_export_download(query)
        elif path == "/vorlagen":
            pairs = parse_qsl(self.path.partition("?")[2], keep_blank_values=True)
            if query.get("laden") == "1":
                target = query.get("target") or "claude-code"
                picks = parse_picks([v for k, v in pairs if k == "pick"])
                if query.get("key") and not picks:
                    _row, picks, _s = key_picks(query["key"])
                text, problems, _h = client_file(host, target, picks)
                if text is None:
                    self.send_html(409, page("Keine Datei", '<div class="card attention"><p>'
                                             + esc(" ".join(problems)) + "</p></div>", user, roles,
                                             "vorlagen"))
                else:
                    self.send_file(text, TEMPLATES[target]["file"])
            else:
                self.send_html(200, templates_page(user, roles, host, pairs))
        else:
            self.send_html(200, admin_page(user, roles, host, tab=query.get("tab", "start")))

    def handle_me(self, path):
        """/me und /me/v1/calls (RFC-0095 Stufe 1): die eigenen Aufrufe der Person.

        Die Plattform meldet die Person an (Route /me, Rolle user) und setzt
        X-OAAP-User-Id -- die Kennung, mit der die von ihr getragenen Aufrufe
        (Verfahren A, P, I) in den Messzeilen stehen. Nur Zeilen genau dieser
        Kennung; ohne sie nichts."""
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        query = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        also = self.realm_subject(uid)
        if path in ("/me/v1/calls", "/me/v1/readiness"):
            if not uid:
                self.send_json(403, {"error": "Nur über die Plattform angemeldet: Es fehlt die "
                                              "Kennung der Person."})
                return
            self.send_json(200, me_doc(uid, query, also) if path == "/me/v1/calls"
                           else me_readiness(uid))
            return
        if path == "/me/client-config":
            self.me_client_config(uid, query)
            return
        if path != "/me":
            self.send_json(404, {"error": "Bekannt sind /me, /me/v1/calls, /me/v1/readiness und "
                                          "/me/client-config."})
            return
        if not uid:
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Diese Seite zeigt deine '
                                     'eigenen Aufrufe und braucht die Anmeldung über die Plattform.'
                                     '</p></div>', "", ""))
            return
        self.send_html(200, me_page(self.identity()[0], uid, query, self.is_admin(), also=also))

    def send_file(self, text, filename):
        """Eine Datei zum Speichern: als Anhang, nie zwischengespeichert (RFC-0107)."""
        self.send_bytes(200, "application/json; charset=utf-8", text.encode("utf-8"),
                        [("Content-Disposition", f'attachment; filename="{filename}"'),
                         ("Cache-Control", "no-store")])

    def me_client_config(self, uid, query):
        """GET /me/client-config?target= (RFC-0107 §3.2): die Datei für die Sichten mit
        Anmeldung, die ein Zugangsprofil öffnet -- Weg `signin`, kein Geheimnis darin."""
        if not uid:
            self.send_json(403, {"error": "Nur über die Plattform angemeldet: Es fehlt die "
                                          "Kennung der Person."})
            return
        target = query.get("target", "")
        tpl = TEMPLATES.get(target)
        if not tpl or "signin" not in tpl["ways"]:
            self.send_json(400, {"error": "Dieses Werkzeug bietet die Anmeldung nicht an.",
                                 "targets": sorted(t for t, v in TEMPLATES.items()
                                                   if "signin" in v["ways"])})
            return
        picks = [{"name": name, "way": "signin"} for name, _e, _m in signin_offer(uid)]
        text, problems, _hints = client_file(self.headers.get("Host", "<gateway>"), target, picks)
        if text is None:
            self.send_json(409, {"error": " ".join(problems)})
            return
        books.log(DB, f"person:{uid}", "client.config", target, f"{len(picks)} Sichten, Anmeldung")
        self.send_file(text, tpl["file"])

    def client_config_for_key(self):
        """GET /mcp/_client-config?target= mit dem Schlüssel (RFC-0107 §3.1): die Datei für
        dessen Sichten. Ein falscher oder widerrufener Schlüssel bekommt dieselbe 401 wie
        überall unter /mcp. Kein Aufruf eines Servers: Prüfspur, keine Messzeile."""
        addr = self.client_addr()
        if failing_hard(addr):
            self.send_json(429, {"error": "Zu viele Fehlversuche."}, [("Retry-After", "60")])
            return
        header = self.headers.get("Authorization", "")
        value = header[7:].strip() if header[:7].lower() == "bearer " else ""
        key = books.find(DB, value)
        if not key or key["revoked"] or key["kind"] != "key":
            note_failure(addr)
            self.deny()
            return
        query = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
        target = query.get("target", "")
        if target not in TEMPLATES:
            self.send_json(400, {"error": f"Ein Werkzeug „{target[:40]}“ kennt das Gateway nicht.",
                                 "targets": sorted(TEMPLATES)})
            return
        _row, picks, skipped = key_picks(key["id"])
        text, problems, _hints = client_file(self.headers.get("Host", "<gateway>"), target, picks)
        if text is None:
            self.send_json(409, {"error": " ".join(problems),
                                 "skipped": [{"view": v, "why": r} for v, r in skipped]})
            return
        books.log(DB, f"key:{key['label']}", "client.config", target,
                  f"{len(picks)} Sichten" + (f", {len(skipped)} ausgelassen" if skipped else ""))
        self.send_file(text, TEMPLATES[target]["file"])

    def realm_subject(self, uid):
        """D2 (0.17): die Kennung dieser Person im Realm des Gateways, wenn die Plattform sie
        nennt (X-OAAP-User-Idp: „<Issuer> <sub>“) und wenn sie sie schützt (TRUST_IDP). Merkt
        sich die Zuordnung für Verfahren U. Gibt [(Issuer, sub)] oder []."""
        if not (uid and TRUST_IDP and ISSUER):
            return []
        iss, _, sub = (self.headers.get(IDP_HEADER) or "").strip().partition(" ")
        sub = sub.strip()
        if not sub or iss.rstrip("/") != ISSUER or len(sub) > 200 or " " in sub:
            return []
        books.person_link_set(DB, ISSUER, sub, uid)
        return [(ISSUER, sub)]

    def handle_me_post(self, path):
        """Zugang eintragen oder löschen (RFC-0095 §4.2) -- nur für sich selbst."""
        raw = self.read_body(1 << 16) or b""
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        if not uid:
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Nur über die Plattform '
                                     'angemeldet.</p></div>', "", ""))
            return
        form = {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            if pair:
                name, _, value = pair.partition("=")
                form[unquote_plus(name)] = unquote_plus(value)
        server = form.get("server", "")
        try:
            if path == "/me/credential":
                value = form.get("value", "").strip()
                if not value or len(value) > 4096 or any(ch in value for ch in "\r\n"):
                    raise ValueError("Der Zugang ist eine Zeile, höchstens 4096 Zeichen.")
                srow = books.server_row(DB, server)
                if srow and books.personal_spec(srow).get("send") == "basic":
                    # 0.18: Benutzer und Token getrennt; versiegelt wird „benutzer:token“.
                    user_ = form.get("user", "").strip()
                    if not user_ or len(user_) > 256 or ":" in user_ \
                            or any(not ch.isprintable() for ch in user_):
                        raise ValueError("Dieser Server braucht Benutzer bzw. E-Mail und Token; "
                                         "der Benutzer ist eine Zeile ohne Doppelpunkt.")
                    value = user_ + ":" + value
                try:
                    sealed = seal_personal("platform", uid, value)
                except VaultError as exc:
                    raise ValueError(f"Nicht gespeichert: Der Tresor des Gateways ist nicht "
                                     f"eingerichtet ({exc}).") from None
                replaced = books.personal_store(DB, "platform", uid, server, sealed)
                notice = f"Zugang für {server} {'ersetzt' if replaced else 'gespeichert'}."
            elif path == "/me/catalog-read":
                notice = me_catalog_read(uid, server,
                                         (self.headers.get("X-OAAP-User-Assertion") or "").strip())
            else:
                gone = books.personal_delete(DB, "platform", uid, server)
                notice = f"Zugang für {server} gelöscht." if gone else "Da war kein Zugang."
        except ValueError as exc:
            notice = str(exc)
        self.send_html(200, me_page(self.identity()[0], uid, {}, self.is_admin(), notice))

    def handle_report(self, path):
        if not self.is_admin():
            self.send_json(403, {"error": "Nur für die Rolle admin — ein Abrechnungssystem fragt "
                                          "mit einem Plattform-API-Schlüssel (Portal → Zugänge)."})
            return
        if path == "/report/v1/keys":
            self.send_json(200, keys_doc())
            return
        if path != "/report/v1/usage":
            self.send_json(404, {"error": "Bekannt sind /report/v1/usage und /report/v1/keys."})
            return
        try:
            frm, to, group_by, filters, fmt = REPORT.parse_query(
                parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            doc = REPORT.usage(DB, frm, to, group_by, filters)
        except ValueError as exc:
            self.send_json(400, {"error": str(exc)})
            return
        if fmt == "json":
            self.send_json(200, doc)
            return
        self.send_bytes(200, "text/csv; charset=utf-8", REPORT.to_csv(doc).encode("utf-8"),
                        [("X-OAAP-Complete-Until", doc["complete_until"]),
                         ("X-OAAP-Complete", "true" if doc["complete"] else "false")])

    # ---- Einrichtungstür (0.27, RFC-0104 Stufe 2)

    def handle_setup(self, method):
        """GET/POST /oaap/setup -- nur mit dem Einrichtungsschlüssel der Instanz.

        Kein OAAP_SETUP_KEY in der Umgebung: jeder Aufruf 403. Identitäts-Kopfzeilen
        zählen hier nicht (§3.1), auch nicht die eines tenant_admin."""
        raw = self.read_body(1 << 20) if method == "POST" else b""
        want = (os.environ.get("OAAP_SETUP_KEY") or "").strip()
        got = (self.headers.get("Authorization") or "").strip()
        given = got[7:].strip() if got[:7].lower() == "bearer " else ""
        if not (want and given and hmac.compare_digest(given.encode("utf-8"), want.encode("utf-8"))):
            self.send_json(403, {"v": 1, "error": "Die Einrichtungstür öffnet nur der "
                                                  "Einrichtungsschlüssel dieser Instanz."})
            return
        actor = setup_actor(self.headers.get("X-OAAP-Setup-Actor"))
        # 0.37 (RFC-0117 Stufe 2): welches Rezept fragt ('<id>@<major>', sonst '')
        recipe = (self.headers.get("X-OAAP-Setup-Recipe") or "").strip()
        recipe = recipe if books.ORIGIN.match(recipe) else ""
        if method == "GET":
            query = dict(parse_qsl(self.path.partition("?")[2], keep_blank_values=True))
            if "kind" not in query and "id" not in query:
                self.send_json(200, {"v": 1, "kinds": list(SETUP_KINDS)})
                return
            if "id" not in query and query.get("kind") in SETUP_KINDS:
                self.send_json(200, {"v": 1, "kind": query["kind"],
                                     "items": setup_items(query["kind"])})
                return
            kind, sid = query.get("kind", ""), query.get("id", "")
            if kind not in SETUP_KINDS or not 0 < len(sid) <= 128:
                self.send_json(400, {"v": 1, "error": "kind ist eine von " + ", ".join(SETUP_KINDS)
                                                      + ", id ein Name."})
                return
            state, ready, note = setup_state(kind, sid)
            self.send_json(200, {"v": 1, "kind": kind, "id": sid, "state": state, "ready": ready,
                                 "note": note})
            return
        try:
            body = json.loads(raw or b"")
        except ValueError:
            self.send_json(400, {"v": 1, "error": "Kein JSON (oder größer als 1 MiB)."})
            return
        try:
            doc = setup_post(body, actor, recipe)
        except ValueError as exc:
            self.send_json(400, {"v": 1, "error": str(exc)})
            return
        self.send_json(200, doc)

    # ---- Betreiber

    def operator(self):
        """Die angemeldete Betreiberin als Person für Verfahren I (0.7.2).

        Die Plattform gibt jeder Anfrage einer Person an diese Seite ihre
        Zusicherung, adressiert an DIESES Gateway. Damit liest das Gateway den
        Katalog einer Quelle der Plattform -- auch in einem Mandanten ohne
        Anmeldedienst, wo es kein Token zum Tauschen gibt. Ohne Zusicherung:
        None, und der Katalog bleibt wie zuletzt gelesen."""
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        assertion = (self.headers.get("X-OAAP-User-Assertion") or "").strip()
        if not (uid and assertion):
            return None
        return {"iss": "platform", "sub": uid, "assertion": assertion, "procedure": "A"}

    def operator_for(self, name):
        """Die Betreiberin als Person für das Lesen des Katalogs von `name`. L29 (0.18.1):
        Verfahren U braucht keine Zusicherung -- es schickt nur den eigenen Zugang --, also
        genügt die Kennung, die die Plattform auf dieser angemeldeten Route setzt."""
        person = self.operator()
        srow = books.server_row(DB, name)
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        if person is None and uid and srow and srow["auth_kind"] == "personal":
            person = {"iss": "platform", "sub": uid, "procedure": "U"}
        return person

    def handle_admin_post(self, path):
        raw = self.read_body(1 << 20) or b""
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Nicht erlaubt.</p></div>',
                                     "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        form, multi = {}, {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            if pair:
                name, _, value = pair.partition("=")
                form[unquote_plus(name)] = unquote_plus(value)
                multi.setdefault(unquote_plus(name), []).append(unquote_plus(value))
        notice, secret, back = "", None, None
        try:
            if path == "/issue":
                views = [v.strip() for v in form.get("views", "").split(",") if v.strip()]
                kind = form.get("kind", "key")
                value, kid = books.issue(
                    DB, form.get("label", ""), user, views=views,
                    max_effect=form.get("max_effect", "read"), ceiling=form.get("ceiling", "yellow"),
                    budget_calls=_int(form.get("budget")), rate_per_min=_int(form.get("rate")),
                    kind=kind, groups=form.get("groups", ""),
                    **{n: form.get(n, "") for n in books.CONTEXT_FIELDS})
                if kind == "profile":
                    notice = f"Zugangsprofil „{form.get('label', '')}“ angelegt."   # Wert verworfen
                else:
                    secret = {"label": form.get("label", ""), "value": value, "views": views,
                              "id": kid}
            elif path == "/revoke":
                label = form.get("label", "")
                notice = (f"Schlüssel „{label}“ widerrufen — ab sofort abgelehnt."
                          if books.revoke(DB, label, user) else f"Nichts zu widerrufen: „{label}“.")
            elif path == "/key-limits":
                label = form.get("label", "")
                # L11 (0.19): die Gruppen eines Zugangsprofils ändern, ohne es neu anzulegen.
                # Zuerst prüfen, damit ein Fehler in den Gruppen nichts halb ändert.
                prow = DB.execute("SELECT kind FROM keys WHERE label=? AND revoked=''", (label,)).fetchone()
                if "groups" in form and prow and prow["kind"] == "profile":
                    books.clean_groups(form["groups"])
                changed = books.set_key_limits(DB, user, label, form.get("views", ""),
                                               form.get("max_effect", "read"), form.get("ceiling", "yellow"))
                if "groups" in form and prow and prow["kind"] == "profile" \
                        and books.set_profile_groups(DB, user, label, form["groups"]):
                    changed = list(changed) + ["Gruppen"]
                notice = (f"„{label}“ geändert: {', '.join(changed)}. Gilt ab dem nächsten Aufruf."
                          if changed else f"„{label}“ unverändert.")
            elif path == "/source-sync":
                _, notice = sync_sources()
            elif path == "/context":
                changed = books.update_context(DB, form.get("label", ""), user,
                                               **{n: form[n] for n in books.CONTEXT_FIELDS if n in form})
                notice = "Kontext geändert: " + ", ".join(changed) if changed else "Kontext unverändert."
            elif path == "/server-save":
                notice, back = save_server(form, user), ("server", form.get("name", ""))
            elif path == "/server-rename":
                old, new = form.get("name", ""), form.get("new", "").strip()
                hit = books.rename_server(DB, user, old, new)
                GUARDS.pop(old, None)
                notice = (f"Server „{old}“ heißt jetzt „{new}“; Sichten: {', '.join(hit) or 'keine'}. "
                          "Die Namen in den Sichten bleiben.")
                back = ("server", new)
            elif path == "/personal-delete":
                gone = books.personal_delete(DB, form.get("iss", ""), form.get("sub", ""),
                                             form.get("name", ""), actor=user)
                notice = "Persönlicher Zugang gelöscht." if gone else "Diesen Zugang gibt es nicht mehr."
                back = ("server", form.get("name", ""))
            elif path == "/server-delete":
                books.delete_server(DB, user, form.get("name", ""))
                notice = f"Server „{form.get('name', '')}“ gelöscht."
            elif path == "/server-read":
                _, notice = read_catalog(form.get("name", ""), self.operator_for(form.get("name", "")))
                back = ("server", form.get("name", ""))
            elif path == "/view-save":
                reqs = {r: form["req_" + r] for r in books.REQUESTS if "req_" + r in form}
                books.save_view(DB, user, form.get("name", ""), form.get("max_effect", "read"),
                                reqs or None, form.get("ways") or None)
                if "approval" in form:
                    # 0.39 (RFC-0105 §7.1): Freigabe der Sicht, mit Freigabeschlüssel
                    fk = form.get("approval_key", "").strip()
                    if fk and not signed_approval.KEY_RE.match(fk):
                        raise ValueError("Ein Freigabeschlüssel sieht so aus: oaap-fk-<64 Hex-Zeichen>.")
                    books.set_view_approval(DB, user, form.get("name", ""), form.get("approval", ""),
                                            VAULT.seal(MASTER, fk) if fk else None)
                notice, back = "Sicht gespeichert.", ("view", form.get("name", ""))
            elif path == "/view-delete":
                books.delete_view(DB, user, form.get("name", ""))
                notice = f"Sicht „{form.get('name', '')}“ gelöscht."
            elif path == "/item-add":
                name = books.approve(DB, user, form.get("view", ""), form.get("kind", "tool"),
                                     form.get("server", ""), form.get("source", ""),
                                     form.get("as", "").strip() or None, form.get("effect", "read"))
                notice = (f"„{name}“ freigegeben in Sicht „{form.get('view', '')}“; "
                          "der Text ist festgehalten.")
                back = ("view", form.get("view", ""))
            elif path == "/view-copy":
                src, new = form.get("view", ""), form.get("new", "").strip()
                rename = form.get("mode") == "rename"
                moved = books.copy_view(DB, user, src, new, move_readers=rename)
                notice = (f"Sicht „{src}“ heißt jetzt „{new}“. Umgestellt: {', '.join(moved) or 'kein Schlüssel, kein Profil'}. "
                          f"MCP-Clients und Chat-Assistenten, die „{src}“ nennen, dort umstellen."
                          if rename else f"Sicht „{new}“ als Kopie von „{src}“ angelegt; „{src}“ bleibt.")
                back = ("view", new)
            elif path == "/view-items":
                split = lambda v: tuple(v.split(SEP, 2))
                wanted = [split(v) for v in multi.get("pick", []) if v.count(SEP) == 2]
                seen = [split(v) for v in multi.get("seen", []) if v.count(SEP) == 2]
                effects = {split(k[4:]): val for k, val in form.items()
                           if k.startswith("eff" + SEP) and k[4:].count(SEP) == 2}
                added, removed, changed, errors = books.set_view_items(
                    DB, user, form.get("view", ""), wanted, seen, effects)
                bits = ([f"freigegeben: {', '.join(added)}"] if added else []) + \
                       ([f"herausgenommen: {', '.join(removed)}"] if removed else []) + \
                       ([f"Klasse geändert: {', '.join(changed)}"] if changed else []) + \
                       ([f"nicht freigegeben -- {' | '.join(errors)}"] if errors else [])
                notice = "; ".join(bits) or "Zuordnung unverändert."
                back = ("view", form.get("view", "")) if form.get("back") != "sichten" else None
            elif path == "/item-confirm":
                books.confirm(DB, user, form.get("view", ""), form.get("kind", "tool"),
                              form.get("name", ""), form.get("effect") or None)
                notice = f"Neuer Text von „{form.get('name', '')}“ bestätigt."
                back = ("view", form.get("view", ""))
            elif path == "/item-tailor":
                changed = tailor_from_form(form, user)
                notice = "Geändert: " + "; ".join(changed) if changed else "Unverändert."
                back = ("view", form.get("view", ""))
            elif path == "/item-remove":
                books.remove_item(DB, user, form.get("view", ""), form.get("kind", "tool"),
                                  form.get("name", ""))
                notice, back = "Herausgenommen.", ("view", form.get("view", ""))
            elif path == "/system-save":
                servers = multi.get("servers", []) if form.get("servers_seen") else None
                landscape.save_system(
                    DB, user, form.get("id", "").strip(), form.get("title", ""),
                    form.get("role", "other"), form.get("active", "1") == "1", form.get("kind", ""),
                    landscape.parse_facts(form.get("facts", "")), form.get("light", ""),
                    form.get("description", ""), servers)
                notice = f"Zielsystem „{form.get('id', '')}“ gespeichert."
            elif path == "/system-delete":
                landscape.delete_system(DB, user, form.get("id", ""))
                notice = f"Zielsystem „{form.get('id', '')}“ gelöscht."
            elif path == "/view-multi":
                landscape.make_multi(DB, user, form.get("view", ""), form.get("system", ""))
                notice = (f"„{form.get('view', '')}“ ist jetzt eine Multi-System-Sicht mit Referenz "
                          f"„{form.get('system', '')}“.")
                back = ("view", form.get("view", ""))
            elif path == "/view-system-add":
                landscape.add_system(DB, user, form.get("view", ""), form.get("system", ""))
                notice = f"Zielsystem „{form.get('system', '')}“ zugeordnet; der Abgleich steht unten."
                back = ("view", form.get("view", ""))
            elif path == "/view-system-remove":
                landscape.remove_system(DB, user, form.get("view", ""), form.get("system", ""))
                notice, back = "Herausgenommen.", ("view", form.get("view", ""))
            elif path == "/view-system-approve":
                kind = form.get("kind", "tool")
                if kind not in landscape.KIND_LABELS:
                    raise ValueError("Unbekannte Art.")
                landscape.approve_for_system(DB, user, form.get("view", ""), form.get("system", ""),
                                             kind, form.get("name", ""))
                notice = (f"„{form.get('name', '')}“ für „{form.get('system', '')}“ freigegeben; "
                          "der Text ist festgehalten.")
                back = ("view", form.get("view", ""))
            elif path == "/view-convert":
                plan = landscape.convert(DB, user, form.get("view", ""), form.get("system", ""),
                                         form.get("new", "").strip())
                carried = sum(1 for r in plan["rows"] if r["carry"])
                notice = (f"Neue Multi-System-Sicht „{form.get('new', '').strip()}“ mit Referenz "
                          f"„{form.get('system', '')}“ und {', '.join(plan['systems'])}; "
                          f"{carried} Freigabe(n) übernommen. „{form.get('view', '')}“ bleibt, wie sie "
                          "war -- Schlüssel, Profile und Assistenten nennen noch sie.")
                back = ("view", form.get("new", "").strip())
        except ValueError as exc:
            notice = str(exc)
        if back and back[0] == "server" and books.server_row(DB, back[1]):
            self.send_html(200, server_page(user, roles, host, back[1], notice))
        elif back and back[0] == "view" and books.view_row(DB, back[1]):
            self.send_html(200, view_page(user, roles, host, back[1], notice))
        else:
            self.send_html(200, admin_page(user, roles, host, notice, secret, POST_TAB.get(path, "start")))


# Nach einer Handlung zeigt die Seite den Reiter, zu dem sie gehört (0.9.0).
POST_TAB = {"/issue": "zugaenge", "/revoke": "zugaenge", "/key-limits": "zugaenge", "/context": "zugaenge",
            "/source-sync": "server", "/server-delete": "server", "/server-save": "server",
            "/server-rename": "server",
            "/view-delete": "sichten", "/view-save": "sichten", "/view-items": "sichten",
            "/view-copy": "sichten", "/system-save": "systeme", "/system-delete": "systeme"}


def json_field(form, name):
    """Ein JSON-Feld eines Formulars: None, wenn es fehlt; ValueError mit einem Satz, wenn kaputt."""
    if name not in form:
        return None
    text = form[name].strip() or "{}"
    try:
        return json.loads(text)
    except ValueError:
        raise ValueError(f"„{name}“ ist kein gültiges JSON.") from None


def tailor_from_form(form, user):
    """Gemeinsam für Seite und Kommandozeile."""
    return books.tailor_item(
        DB, user, form.get("view", ""), form.get("kind", "tool"), form.get("name", ""),
        new_name=form["new_name"].strip() if "new_name" in form else None,
        description=form["description"].strip() if "description" in form else None,
        fixed=json_field(form, "fixed"), bounds=json_field(form, "bounds"),
        effect=form.get("effect") or None)


def save_server(form, user):
    """Gemeinsam für Seite und Kommandozeile. Gibt einen Satz zurück, wirft ValueError."""
    name = form.get("name", "").strip()
    address = form.get("address", "").strip()
    if address.startswith("destination:"):
        need, _, path = address[len("destination:"):].partition("/")
        if not books.NAME.match(need):
            raise ValueError("Nach destination: folgt der Name der Bindung (Kleinbuchstaben, "
                             "Ziffern, Bindestriche), danach wahlweise /pfad.")
        if path and (not upstream.DESTINATION_PATH.match(path) or ".." in path.split("/")):
            raise ValueError("Der Pfad hinter der Destination darf kein „..“, keine Abfrage und "
                             "kein Fragment enthalten.")
    else:
        try:
            upstream.check_host(upstream.resolve(address))
        except upstream.UpstreamError as exc:
            # Verbotene Adressen schon beim Speichern abweisen; ein Name, der sich
            # gerade nicht auflösen lässt, darf trotzdem eingetragen werden.
            if exc.kind == "address":
                raise ValueError(str(exc)) from None
    auth = form.get("auth", "none")
    if auth == "header":
        auth = "header:" + form.get("header", "").strip()
    limits = guard.clean({n: form.get("lim_" + n, guard.DEFAULTS[n]) for n in guard.DEFAULTS})
    sealed = None
    if form.get("credential"):
        try:
            sealed = VAULT.seal(MASTER, form["credential"])
        except VaultError as exc:
            raise ValueError(f"Zugangsdaten nicht gespeichert: {exc}") from None
    exchange = None
    if auth == "exchange":
        exchange = {k: form.get(k) for k in ("token_url", "client_id", "audience")}
        if exchange.get("client_id") and AUDIENCE and exchange["client_id"] != AUDIENCE:
            raise ValueError("Der eigene Client für den Tausch muss MCPGW_AUDIENCE sein: Keycloak "
                             "tauscht nur für einen Client, an den das Token adressiert ist.")
    personal = None
    if auth == "personal":
        send = form.get("personal_send", "bearer")
        personal = {"send": send if send in ("bearer", "basic") else "header:" + form.get("header", "").strip(),
                    "prefix": form.get("personal_prefix", ""), "hint": form.get("personal_hint", ""),
                    "link": form.get("personal_link", "")}
    books.save_server(DB, user, name, address, auth, form.get("light", "red"), limits,
                      sealed_credential=sealed, drop_credential=form.get("drop_credential") == "1",
                      exchange=exchange, personal=personal)
    note = ""
    try:
        books.set_untrusted(DB, user, name, form.get("untrusted") == "1")
    except ValueError as exc:
        note = f" Fremder Inhalt nicht geändert: {exc}"
    srow = books.server_row(DB, name)
    if srow:
        guard_of(srow)
    return f"Server „{name}“ gespeichert.{note}"


# ------------------------------------------------------------- Einrichtungstür (0.27)
#
# RFC-0104 Stufe 2, §3.1 und §3.3: Ein Profil des Mandanten (etwa
# `ki-arbeitsplatz`) richtet das Gateway ein, ohne dass ein Mensch klickt. Der
# Knoten ruft diese Instanz über den internen Listener der Plattform mit
# `Authorization: Bearer <OAAP_SETUP_KEY>`; nur die Plattform kennt den Wert.
# Identitäts-Kopfzeilen zählen hier nicht -- auch ein tenant_admin kommt ohne
# den Schlüssel nicht durch. Jede Art benutzt dieselben Funktionen wie die
# Betreiberseite (books.save_view, books.approve, books.issue, books.save_server,
# books.assistant_write, sync_sources, read_catalog): eine Regel, zwei Türen.
# Was ein Mensch angelegt oder geändert hat, wird nie überschrieben: Ein
# Unterschied ist `conflict`.

SETUP_PATH = "/oaap/setup"
SETUP_KINDS = ("refresh", "server_light", "view", "profile", "assistant", "server", "system")
# 0.31.0 (RFC-0109 §12): ein Server hinter einer Destination der Plattform -- nur
# `destination:<bindung>[/pfad]`, nie eine freie Adresse: wohin er reicht, sagt die
# Destination, die der Betreiber oder ein freigegebenes Rezept angelegt hat.
SETUP_SERVER_AUTH = ("propagate", "none")
SETUP_SECRET_FIELDS = {"key", "token", "secret", "password", "credentials"}
SETUP_FIELDS = {
    "refresh": set(),
    "server_light": {"light"},
    "view": {"accesses", "tools", "max_effect"},
    "profile": {"group", "views", "ceiling", "max_effect"},
    "assistant": {"title", "view", "gateway", "model", "greeting", "visibility", "system",
                  "commands", "views", "support_queue"},
    "server": {"address", "auth", "light"},
    # 0.32 (RFC-0114): ein Zielsystem der Landschaft; `type` ist seine Art (sap, jira)
    "system": {"title", "role", "type", "active", "facts", "light", "description", "servers"},
}
SETUP_PROFILE_ID = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@._-]{0,63}$")
SETUP_LOCK = threading.Lock()


class SetupRefused(Exception):
    """Die App erlaubt es nicht; der Text sagt warum."""


def setup_secret_field(value):
    """Der Name eines Feldes, das ein Geheimnis tragen würde (auch verschachtelt), oder ''."""
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in SETUP_SECRET_FIELDS:
                return str(k)
            hit = setup_secret_field(v)
            if hit:
                return hit
    elif isinstance(value, list):
        for v in value:
            hit = setup_secret_field(v)
            if hit:
                return hit
    return ""


def setup_server(sid):
    """Der Server zu einer Kennung: der mit diesem Namen, sonst der aus dieser Instanz
    (ein Server einer Quelle kann umbenannt sein, 0.14.0)."""
    return books.server_row(DB, sid) or DB.execute("SELECT * FROM servers WHERE source=?",
                                                   (sid,)).fetchone()


def catalog_tools(name):
    return [r for r in books.catalog(DB, name, "tool") if r["present"]]


def setup_observed(kind, sid):
    """Was jetzt unter dieser Kennung da ist, als Abbildung -- oder None."""
    if kind == "server_light":
        srow = setup_server(sid)
        return {"server": srow["name"], "light": srow["light"]} if srow else None
    if kind == "view":
        vrow = books.view_row(DB, sid)
        if not vrow:
            return None
        return {"ways": vrow["ways"], "max_effect": vrow["max_effect"],
                "requests": books.view_requests(vrow),
                "items": [[i["kind"], i["name"], i["server"], i["source"], i["effect"],
                           i["description"], i["fixed"], i["bounds"], i["approved_fp"]]
                          for i in books.items(DB, sid)]}
    if kind == "profile":
        row = DB.execute("SELECT * FROM keys WHERE label=?", (sid,)).fetchone()
        if not row:
            return None
        return {"kind": row["kind"], "revoked": row["revoked"], "groups": row["groups"],
                "views": books.key_views(row), "max_effect": row["max_effect"],
                "ceiling": row["ceiling"], "budget_calls": row["budget_calls"],
                "rate_per_min": row["rate_per_min"]}
    if kind == "assistant":
        row = books.assistant_row(DB, sid)
        return {"rev": row["rev"], "state": row["state"], "doc": row["doc"]} if row else None
    if kind == "server":
        srow = books.server_row(DB, sid)
        return ({"address": srow["address"], "auth_kind": srow["auth_kind"],
                 "light": srow["light"], "source": srow["source"] or ""} if srow else None)
    if kind == "system":
        row = landscape.system_row(DB, sid)
        return ({"title": row["title"], "role": row["role"], "type": row["kind"],
                 "active": bool(row["active"]), "facts": landscape.facts_of(row),
                 "light": row["light"], "description": row["description"],
                 "servers": landscape.servers_of(DB, sid)} if row else None)
    return None


def setup_gone(kind, obs):
    """Ist das Beobachtete nur noch ein Rest (widerrufen, gelöscht) oder gar nicht da?"""
    return (obs is None or (kind == "profile" and bool(obs["revoked"]))
            or (kind == "assistant" and obs["state"] == "deleted"))


def setup_removed_by_door(kind, sid, obs):
    """Steht hier nur noch ein Rest von etwas, das die Einrichtungstür SELBST entfernt
    hat? (0.29.0, RFC-0109 D12) Dann darf die Tür es zurückholen -- sonst ginge nach
    einem Rückgängig nie wieder ein Lauf, denn ein Etikett bleibt seinem Eintrag.

    Beweis ist die Prüfspur des Gateways, nicht das Gedächtnis der Tür: beim Profil kam
    der letzte Widerruf dieses Etiketts von „setup:…“, beim Assistenten die letzte
    Änderung (das Löschen). Was ein Mensch widerrufen oder gelöscht hat, bleibt ein
    conflict. Ein Schlüssel (kein Profil) kommt nie zurück: sein Wert ist verworfen."""
    if obs is None or not setup_gone(kind, obs):
        return False
    if kind == "profile":
        if obs["kind"] != "profile":
            return False
        row = DB.execute("SELECT actor FROM audit WHERE action='key.revoke' AND subject=? "
                         "ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
        return bool(row) and str(row["actor"]).startswith("setup:")
    if kind == "assistant":
        row = DB.execute("SELECT changed_by FROM assistants WHERE id=?", (sid,)).fetchone()
        return bool(row) and str(row["changed_by"]).startswith("setup:")
    return False


def setup_revive(kind, sid, wish, by):
    """Holt zurück, wie gewünscht (setup_removed_by_door ist geprüft). Wirft ValueError."""
    if kind == "profile":
        row = DB.execute("SELECT id FROM keys WHERE label=? AND kind='profile'", (sid,)).fetchone()
        if not row:
            raise ValueError(f"Ein Zugangsprofil „{sid}“ gibt es nicht.")
        views = ",".join(wish["views"])
        DB.execute("UPDATE keys SET revoked='', groups=?, views=?, max_effect=?, ceiling=? "
                   "WHERE id=?", (wish["groups"], views, wish["max_effect"], wish["ceiling"],
                                  row["id"]))
        books.log(DB, by, "profile.revive", sid,
                  f"gruppen={wish['groups']} sichten={views} bis={wish['max_effect']} "
                  f"ampel={wish['ceiling']}")
        DB.commit()
        return f"Zugangsprofil „{sid}“ zurückgeholt (die Einrichtung hatte es widerrufen)."
    if kind == "assistant":
        rev = books.assistant_write(DB, wish, by)
        books.log(DB, by, "assistant.revive", sid, f"rev={rev}")
        DB.commit()
        return f"Assistent „{sid}“ zurückgeholt, Revision @{rev} (die früheren bleiben)."
    raise ValueError("unbekannte Art")


def setup_wish(kind, sid, item, planned, actor):
    """Der Wunsch in der Form, in der er verglichen und angelegt wird. Wirft SetupRefused.

    `planned` (nur bei `dry`): was weiter vorn in derselben Prüfung angelegt würde --
    eine Sicht, die eine Prüfung erst anlegen würde, macht ein Profil dahinter nicht
    `refused`. `pending` heißt: erst nach dem Einlesen vergleichbar."""
    if kind == "server_light":
        if item.get("light") not in books.LIGHTS:
            raise SetupRefused("light ist green, yellow oder red.")
        return {"light": item["light"]}
    if kind == "system":
        try:
            books.valid_name(sid, "eines Zielsystems")
            facts = landscape.clean_facts(item.get("facts") or {})
        except ValueError as exc:
            raise SetupRefused(str(exc)) from None
        role = item.get("role", "other")
        if role not in landscape.ROLES:
            raise SetupRefused("role ist eine von " + ", ".join(landscape.ROLES) + ".")
        light = item.get("light", "")
        if light and light not in books.LIGHTS:
            raise SetupRefused("light ist green, yellow, red oder leer (aus der Rolle).")
        servers = item.get("servers") or []
        if not isinstance(servers, list) or not all(isinstance(x, str) for x in servers):
            raise SetupRefused("servers: eine Liste von Servernamen.")
        missing = [x for x in servers if not books.server_row(DB, x) and ("server", x) not in planned]
        if missing:
            raise SetupRefused("Diese Server gibt es nicht: " + ", ".join(missing) + ".")
        if not isinstance(item.get("title"), str) or not item["title"].strip():
            raise SetupRefused("title fehlt.")
        return {"title": item["title"].strip(), "role": role,
                "type": str(item.get("type") or "").strip().lower(),
                "active": item.get("active", True) is not False, "facts": facts, "light": light,
                "description": str(item.get("description") or "").strip()[:landscape.DESC_MAX],
                "servers": sorted(servers)}
    if kind == "server":
        try:
            books.valid_name(sid, "eines Servers")
        except ValueError as exc:
            raise SetupRefused(str(exc)) from None
        address = str(item.get("address") or "").strip()
        if not address.startswith("destination:"):
            raise SetupRefused("address: destination:<bindung>[/pfad] -- die Einrichtung legt "
                               "nur Server hinter einer Destination der Plattform an.")
        need, _, path = address[len("destination:"):].partition("/")
        if not books.NAME.match(need):
            raise SetupRefused("Nach destination: folgt der Name der Bindung (Kleinbuchstaben, "
                               "Ziffern, Bindestriche), danach wahlweise /pfad.")
        if path and (not upstream.DESTINATION_PATH.match(path) or ".." in path.split("/")):
            raise SetupRefused("Der Pfad hinter der Destination darf kein „..“, keine Abfrage "
                               "und kein Fragment enthalten.")
        auth = item.get("auth", "propagate")
        if auth not in SETUP_SERVER_AUTH:
            raise SetupRefused("auth ist propagate oder none -- Zugangsdaten trägt ein Mensch "
                               "auf der Seite des Gateways ein.")
        light = item.get("light", "red")
        if light not in books.LIGHTS:
            raise SetupRefused("light ist green, yellow oder red.")
        return {"address": address, "auth_kind": auth, "light": light}
    if kind == "view":
        if not books.NAME.match(sid):
            raise SetupRefused("Der Name einer Sicht besteht aus Kleinbuchstaben, Ziffern und "
                               "Bindestrichen, höchstens 48 Zeichen.")
        acc = item.get("accesses")
        if not isinstance(acc, list) or not acc or not all(a in books.WAY_PARTS for a in acc):
            raise SetupRefused("accesses: eine Liste aus key, signin und platform.")
        ways = ",".join(p for p in books.WAY_PARTS if p in acc)
        tools, wanted, pending = item.get("tools"), set(), False
        shape = 'tools: {"server": <s>, "effect": "read"} oder eine Liste von {"server", "tool"}.'
        if isinstance(tools, dict):
            if set(tools) - {"server", "effect"} or tools.get("effect") != "read" \
                    or not isinstance(tools.get("server"), str):
                raise SetupRefused(shape)
            srow = setup_server(tools["server"])
            rows = reading_tools(srow["name"]) if srow else []
            if rows:
                wanted = {(srow["name"], r["name"], "read") for r in rows}
            elif ("refresh", tools["server"]) in planned:
                pending = True
            elif not srow:
                raise SetupRefused(f"Einen Server „{tools['server']}“ gibt es nicht (erst refresh).")
            else:
                raise SetupRefused(f"Der Katalog von „{srow['name']}“ hat keine lesenden Tools "
                                   "(erst refresh; lesend heißt readOnlyHint: true).")
        elif isinstance(tools, list) and tools:
            for t in tools:
                if not isinstance(t, dict) or set(t) - {"server", "tool", "effect"} \
                        or not isinstance(t.get("server"), str) or not isinstance(t.get("tool"), str):
                    raise SetupRefused(shape)
                eff = t.get("effect", "read")
                if eff not in books.EFFECTS:
                    raise SetupRefused("effect ist read, write oder execute.")
                srow = setup_server(t["server"])
                cat = books.catalog_row(DB, srow["name"], "tool", t["tool"]) if srow else None
                if cat and cat["present"]:
                    wanted.add((srow["name"], t["tool"], eff))
                elif ("refresh", t["server"]) in planned:
                    pending = True
                else:
                    raise SetupRefused(f"Der Server „{t['server']}“ bietet das Tool „{t['tool']}“ "
                                       "nicht an (erst refresh).")
        else:
            raise SetupRefused(shape)
        effects = [e for _s, _t, e in wanted] or ["read"]
        max_effect = item.get("max_effect") or max(effects, key=lambda e: books.EFFECT_RANK[e])
        if max_effect not in books.EFFECTS:
            raise SetupRefused("max_effect ist read, write oder execute.")
        wish = {"ways": ways, "max_effect": max_effect, "tools": sorted(list(w) for w in wanted)}
        if pending:
            wish["pending"] = True
        return wish
    if kind == "profile":
        if not SETUP_PROFILE_ID.match(sid):
            raise SetupRefused("Das Etikett eines Zugangsprofils: Buchstaben, Ziffern, @ . _ -, "
                               "höchstens 64 Zeichen.")
        group = item.get("group")
        try:
            groups = books.clean_groups([group] if isinstance(group, str) else [])
        except ValueError as exc:
            raise SetupRefused(str(exc)) from None
        views = item.get("views")
        if not isinstance(views, list) or not views or not all(isinstance(v, str) for v in views):
            raise SetupRefused("views: eine Liste von Sichten.")
        missing = [v for v in views if not books.view_row(DB, v) and ("view", v) not in planned]
        if missing:
            raise SetupRefused("Diese Sichten gibt es nicht: " + ", ".join(missing) + ".")
        ceiling, max_effect = item.get("ceiling", "yellow"), item.get("max_effect", "read")
        if ceiling not in books.LIGHTS:
            raise SetupRefused("ceiling ist green, yellow oder red.")
        if max_effect not in books.EFFECTS:
            raise SetupRefused("max_effect ist read, write oder execute.")
        return {"groups": groups, "views": views, "ceiling": ceiling, "max_effect": max_effect}
    if kind == "assistant":
        vis = item.get("visibility", "tenant")
        if vis not in ("tenant", {"scope": "tenant"}):
            raise SetupRefused("visibility: die Einrichtung legt nur Assistenten für den ganzen "
                               "Mandanten an (tenant); private und solche eines Teams legen "
                               "Menschen an.")
        doc = {"id": sid, "visibility": {"scope": "tenant"}, "owner": ("setup:" + actor)[:64]}
        for f in SETUP_FIELDS["assistant"] - {"visibility"}:
            if f in item:
                doc[f] = item[f]
        if "system" not in doc and isinstance(doc.get("title"), str) and isinstance(doc.get("view"), str):
            doc["system"] = (f"Du bist der Assistent „{doc['title']}“. Antworte knapp und nutze "
                             f"die Werkzeuge der Sicht {doc['view']}; sag, wenn sie keine Antwort "
                             "hergeben.")
        elif "system" not in doc and isinstance(doc.get("title"), str):
            # 0.35.0 (RFC-0116 D9): ohne Sicht spricht er ohne Werkzeuge
            doc["system"] = (f"Du bist der Assistent „{doc['title']}“. Antworte knapp; du hast "
                             "keine Werkzeuge und sagst, wenn du etwas nicht weißt.")
        errs = arules.problems(doc)
        if not errs and "view" in doc and ("view", doc["view"]) not in planned:
            errs = assistant_view_problem(doc)
        if errs:
            raise SetupRefused(" ".join(errs))
        return doc
    raise SetupRefused("unbekannte Art")


def setup_matches(kind, sid, wish, obs):
    """Ist das, was da ist, schon wie gewünscht?"""
    if setup_gone(kind, obs) or wish.get("pending"):
        return False
    if kind == "server_light":
        return obs["light"] == wish["light"]
    if kind == "view":
        if obs["ways"] != wish["ways"] or obs["max_effect"] != wish["max_effect"]:
            return False
        rows = books.items(DB, sid)
        tools = sorted([i["server"], i["source"], i["effect"]] for i in rows if i["kind"] == "tool")
        uis = {books.ui_uri(i) for i in rows if i["kind"] == "tool"} - {None}
        extra = [i for i in rows if i["kind"] != "tool"
                 and not (i["kind"] == "resource" and i["name"] in uis)]
        return tools == wish["tools"] and not extra
    if kind == "profile":
        return (obs["kind"] == "profile" and obs["groups"] == wish["groups"]
                and sorted(obs["views"]) == sorted(wish["views"])
                and obs["ceiling"] == wish["ceiling"] and obs["max_effect"] == wish["max_effect"])
    if kind == "assistant":
        strip = lambda d: {k: v for k, v in d.items() if k != "owner"}
        return strip(obs["doc"]) == strip(wish)
    if kind == "server":
        return (not obs["source"] and obs["address"] == wish["address"]
                and obs["auth_kind"] == wish["auth_kind"] and obs["light"] == wish["light"])
    if kind == "system":
        return dict(obs, servers=sorted(obs["servers"])) == wish
    return False


def setup_difference(kind, wish, obs):
    """Ein Satz, was abweicht -- für die Notiz eines `conflict`."""
    if kind == "system":
        diff = sorted(k for k in wish if obs.get(k) != wish[k])
        return "Das Zielsystem gibt es schon, anders in: " + ", ".join(diff) + "."
    if kind == "server":
        if obs["source"]:
            return "Ein Server dieses Namens kommt aus einer Quelle der Plattform."
        bits = [f"{w} {obs[k]} statt {wish[k]}" for k, w in (
            ("address", "Adresse"), ("auth_kind", "Verfahren"), ("light", "Ampel"))
            if obs[k] != wish[k]]
        return "Den Server gibt es schon, anders: " + "; ".join(bits) + "."
    if kind == "profile":
        if obs["kind"] != "profile":
            return "Das Etikett trägt einen Schlüssel, kein Zugangsprofil."
        if obs["revoked"]:
            return ("Das Zugangsprofil ist widerrufen, nicht von der Einrichtung; ein Etikett "
                    "bleibt seinem Eintrag für immer (bitte ein anderes wählen).")
        bits = [f"{w} {obs[k] if k != 'views' else ','.join(obs[k])} statt "
                f"{wish[k] if k != 'views' else ','.join(wish[k])}"
                for k, w in (("groups", "Gruppen"), ("views", "Sichten"), ("ceiling", "Ampel"),
                             ("max_effect", "Wirkungsklasse"))
                if (sorted(obs[k]) != sorted(wish[k]) if k == "views" else obs[k] != wish[k])]
        return "Das Zugangsprofil gibt es schon, anders: " + "; ".join(bits) + "."
    if kind == "assistant":
        if obs["state"] == "deleted":
            return (f"Der Assistent ist gelöscht, nicht von der Einrichtung; seine Revisionen "
                    f"bleiben (zuletzt @{obs['rev']}), und die Einrichtung holt ihn nicht zurück.")
        if obs["state"] != "active":
            return f"Der Assistent ist {obs['state']} (@{obs['rev']})."
        diff = sorted(k for k in set(obs["doc"]) | set(wish) if k != "owner"
                      and obs["doc"].get(k) != wish.get(k))
        return f"Den Assistenten gibt es schon (@{obs['rev']}), anders in: {', '.join(diff)}."
    if kind == "view":
        if wish.get("pending"):
            return "Die Sicht gibt es schon; ihr Inhalt lässt sich erst nach dem Einlesen vergleichen."
        bits = []
        if obs["ways"] != wish["ways"]:
            bits.append(f"Zugänge {obs['ways']} statt {wish['ways']}")
        if obs["max_effect"] != wish["max_effect"]:
            bits.append(f"bis {obs['max_effect']} statt {wish['max_effect']}")
        have = sorted({f"{i[2]}:{i[3]}" for i in obs["items"] if i[0] == "tool"})
        want = sorted({f"{s}:{t}" for s, t, _e in wish["tools"]})
        if have != want or not bits:
            bits.append(f"Einträge {', '.join(have) or 'keine'} statt {', '.join(want)}"
                        if have != want else "Einträge oder Wirkungsklassen geändert")
        return "Die Sicht gibt es schon, anders: " + "; ".join(bits) + "."
    return ""


def setup_set_light(srow, light, by):
    """Die Ampel über books.save_server, mit allem anderen, wie es ist -- dieselben Regeln."""
    books.save_server(DB, by, srow["name"], srow["address"], srow["auth_kind"], light,
                      json.loads(srow["limits"] or "{}"),
                      exchange=(json.loads(srow["exchange"] or "{}")
                                if srow["auth_kind"] == "exchange" else None),
                      personal=(json.loads(srow["personal"] or "{}")
                                if srow["auth_kind"] == "personal" else None))


def setup_make(kind, sid, wish, by):
    """Legt an, mit den Funktionen der Betreiberseite. Wirft ValueError mit einem Satz."""
    if kind == "system":
        landscape.save_system(DB, by, sid, wish["title"], wish["role"], wish["active"],
                              wish["type"], wish["facts"], wish["light"], wish["description"],
                              wish["servers"])
        return f"Zielsystem „{sid}“ angelegt."
    if kind == "server":
        books.save_server(DB, by, sid, wish["address"], wish["auth_kind"], wish["light"],
                          guard.clean(dict(guard.DEFAULTS)))
        return (f"Server „{sid}“ angelegt ({wish['address']}, Verfahren "
                f"{'P' if wish['auth_kind'] == 'propagate' else 'ohne Zugang'}, Ampel "
                f"{wish['light']}); den Katalog liest eine Person.")
    if kind == "view":
        books.save_view(DB, by, sid, wish["max_effect"], None, wish["ways"])
        try:
            for server, source, eff in wish["tools"]:
                books.approve(DB, by, sid, "tool", server, source, None, eff)
        except ValueError:
            books.delete_view(DB, by, sid)            # nichts halb stehen lassen
            raise
        return f"Sicht „{sid}“ angelegt, {len(wish['tools'])} Tool(s) freigegeben."
    if kind == "profile":
        books.issue(DB, sid, by, views=wish["views"], max_effect=wish["max_effect"],
                    ceiling=wish["ceiling"], kind="profile", groups=wish["groups"])
        return f"Zugangsprofil „{sid}“ angelegt."     # ein Profil hat keinen benutzbaren Wert
    if kind == "assistant":
        rev = books.assistant_write(DB, wish, by)
        books.log(DB, by, "assistant.save", sid, f"rev={rev}")
        DB.commit()
        return f"Assistent „{sid}“ angelegt, Revision @{rev}."
    raise ValueError("unbekannte Art")


def setup_refresh(sid, dry, planned):
    """refresh: Quellen einlesen, dann den Katalog über die Erkundungs-Tür (ohne Person)."""
    srow = setup_server(sid)
    had = bool(srow) and bool(catalog_tools(srow["name"]))
    if dry:
        if had:
            return "present", f"{len(catalog_tools(srow['name']))} Tool(s) im Katalog."
        planned.add(("refresh", sid))
        return "would_make", "Liest die Quellen der Plattform und den Katalog ein."
    ok, text = sync_sources()
    srow = setup_server(sid)
    if not srow:
        return "refused", f"Einen Server „{sid}“ gibt es nach dem Einlesen nicht. {text}".strip()
    ok, text = read_catalog(srow["name"])
    if catalog_tools(srow["name"]):
        return ("present" if had else "made"), text
    return "refused", text if not ok else f"Der Katalog von „{srow['name']}“ hat keine Tools."


def setup_item(item, dry, actor, planned):
    """Ein Wunsch: (result, note)."""
    kind, sid = item["kind"], item["id"]
    if kind not in SETUP_KINDS:
        return "refused", "Diese Art kennt das MCP-Gateway nicht; es kennt " + ", ".join(SETUP_KINDS) + "."
    secret = setup_secret_field(item)
    if secret:
        return "refused", (f"{secret}: Geheimnisse gehen nie durch die Einrichtung (RFC-0104 §3.1); "
                           "Zugangsdaten trägt ein Mensch auf der Seite des Gateways ein.")
    extra = sorted(set(item) - {"kind", "id"} - SETUP_FIELDS[kind])
    if extra:
        return "refused", f"Unbekannte Felder für {kind}: {', '.join(extra)}."
    if kind == "refresh":
        return setup_refresh(sid, dry, planned)
    by = "setup:" + actor
    try:
        wish = setup_wish(kind, sid, item, planned, actor)
    except SetupRefused as exc:
        return "refused", str(exc)
    if kind == "server_light":
        srow = setup_server(sid)
        if not srow:
            if ("refresh", sid) in planned:
                return "would_make", f"Ampel {wish['light']}, sobald der Server eingelesen ist."
            return "refused", f"Einen Server „{sid}“ gibt es nicht (erst refresh)."
        if srow["light"] == wish["light"]:
            return "present", f"Ampel {srow['light']}."
        if srow["light"] != "red":
            return "conflict", (f"Die Ampel von „{srow['name']}“ ist {srow['light']}, von Hand gesetzt; "
                                "die Einrichtung ändert nur das Rot, mit dem jeder neue Server beginnt.")
        if dry:
            return "would_make", f"Ampel red → {wish['light']}."
        try:
            setup_set_light(srow, wish["light"], by)
        except ValueError as exc:
            return "refused", str(exc)
        books.setup_remember(DB, kind, sid, actor, books.setup_digest(wish),
                             books.setup_digest(setup_observed(kind, sid)))
        return "made", f"Ampel von „{srow['name']}“: red → {wish['light']}."
    obs = setup_observed(kind, sid)
    if setup_removed_by_door(kind, sid, obs):
        if dry:
            planned.add((kind, sid))
            return "would_make", "Holt zurück, was die Einrichtung selbst entfernt hat."
        try:
            note = setup_revive(kind, sid, wish, by)
        except ValueError as exc:
            return "refused", str(exc)
        books.setup_remember(DB, kind, sid, actor, books.setup_digest(wish),
                             books.setup_digest(setup_observed(kind, sid)))
        return "made", note
    if obs is not None:
        if setup_matches(kind, sid, wish, obs):
            return "present", ""
        rec = books.setup_record(DB, kind, sid)
        if rec and not rec["removed"] and not setup_gone(kind, obs) \
                and rec["wish"] == books.setup_digest(wish) \
                and rec["made"] == books.setup_digest(obs):
            # Unverändert, wie die Einrichtung es anlegte -- auch wenn der Katalog seither wuchs.
            return "present", "Unverändert, wie die Einrichtung es angelegt hat."
        return "conflict", setup_difference(kind, wish, obs)
    if dry:
        planned.add((kind, sid))
        return "would_make", ("Erst nach dem Einlesen genau bestimmbar." if wish.get("pending") else "")
    try:
        note = setup_make(kind, sid, wish, by)
    except ValueError as exc:
        return "refused", str(exc)
    books.setup_remember(DB, kind, sid, actor, books.setup_digest(wish),
                         books.setup_digest(setup_observed(kind, sid)))
    return "made", note


def setup_remove(item, actor):
    """Rückgängig, nur solange das Ding so ist, wie die Einrichtung es anlegte: (result, note)."""
    kind, sid = item["kind"], item["id"]
    if kind not in SETUP_KINDS:
        return "refused", "Diese Art kennt das MCP-Gateway nicht."
    if kind == "refresh":
        return "gone", "Einlesen hinterlässt nichts zu entfernen."
    by = "setup:" + actor
    obs = setup_observed(kind, sid)
    rec = books.setup_record(DB, kind, sid)
    if not rec or rec["removed"]:
        if setup_gone(kind, obs) or (kind == "server_light" and obs["light"] == "red"):
            return "gone", ""
        return "conflict", "Nicht von der Einrichtung angelegt -- es bleibt."
    if setup_gone(kind, obs):
        books.setup_forget(DB, kind, sid)
        return "gone", ""
    if books.setup_digest(obs) != rec["made"]:
        return "conflict", "Seit der Einrichtung von Hand geändert -- es bleibt."
    try:
        if kind == "server_light":
            setup_set_light(setup_server(sid), "red", by)
        elif kind == "view":
            books.delete_view(DB, by, sid)
        elif kind == "profile":
            books.revoke(DB, sid, by)
        elif kind == "assistant":
            books.assistant_set_state(DB, sid, "deleted", by)
            books.log(DB, by, "assistant.state", sid, "deleted")
            DB.commit()
        elif kind == "server":
            books.delete_server(DB, by, sid)
        elif kind == "system":
            landscape.delete_system(DB, by, sid)
    except ValueError as exc:
        return "conflict", str(exc)
    books.setup_forget(DB, kind, sid)
    return "removed", ""


def setup_state(kind, sid):
    """GET ?kind=&id=: (state, ready, note) -- `ready` ist, was `setup.ready` fragt."""
    if kind in ("refresh", "server_light", "server"):
        srow = setup_server(sid)
        if not srow:
            return "absent", False, f"Einen Server „{sid}“ gibt es nicht."
        n = len(catalog_tools(srow["name"]))
        if kind in ("refresh", "server"):
            return "present", n > 0, f"{n} Tool(s) im Katalog."
        return "present", True, f"Ampel {srow['light']}."
    obs = setup_observed(kind, sid)
    if setup_gone(kind, obs):
        return "absent", False, ""
    if kind == "system":
        return "present", bool(obs["servers"]), ("aktiv" if obs["active"] else "abgeschaltet")
    if kind == "view":
        rows = books.items(DB, sid)
        active = sum(1 for i in rows if books.item_state(DB, dict(i)) == "active")
        return "present", active > 0, f"{len(rows)} Eintrag/Einträge, {active} freigegeben und aktiv."
    if kind == "profile":
        if obs["kind"] != "profile":
            return "absent", False, "Das Etikett trägt einen Schlüssel, kein Zugangsprofil."
        missing = [v for v in obs["views"] if not books.view_row(DB, v)]
        return "present", not missing, ("Sichten fehlen: " + ", ".join(missing)) if missing else ""
    view_ok = not assistant_view_problem(obs["doc"])
    return ("present", obs["state"] == "active" and view_ok,
            f"@{obs['rev']}, {obs['state']}" + ("" if view_ok else ", Sicht fehlt"))


def setup_items(kind):
    """GET ?kind= ohne id (0.30.0, RFC-0109 §4): was es von dieser Art gibt, zum
    Auswählen in einem Rezept -- Kennung, kurzer Titel, Ampel, wo das Gateway eine
    kennt. **Nie** ein Schlüssel, eine Zugangsangabe oder die Adresse eines Servers.
    Widerrufene Profile und gelöschte Assistenten stehen nicht darin."""
    out = []
    if kind in ("refresh", "server_light", "server"):
        for row in DB.execute("SELECT name, light FROM servers ORDER BY name"):
            out.append({"id": row["name"], "title": row["name"], "light": row["light"]})
    elif kind == "view":
        for row in books.views(DB):
            out.append({"id": row["name"], "title": f"Sicht bis {row['max_effect']}"
                                                    + (f", Server {row['server']}" if row["server"] else "")})
    elif kind == "profile":
        for row in DB.execute("SELECT label, groups, views, ceiling FROM keys WHERE "
                              "kind='profile' AND revoked='' ORDER BY label"):
            vs = ", ".join(books.key_views(row))
            out.append({"id": row["label"], "light": row["ceiling"],
                        "title": f"Zugangsprofil für {row['groups'] or '(keine Gruppe)'}"
                                 + (f", Sichten {vs}" if vs else "")})
    elif kind == "system":
        for s in landscape.systems(DB):
            out.append({"id": s["id"], "title": s["title"], "light": landscape.system_light(DB, s)})
    elif kind == "assistant":
        for a in books.assistant_rows(DB):
            if a["state"] == "deleted":
                continue
            doc = a["doc"] if isinstance(a["doc"], dict) else {}
            out.append({"id": str(doc.get("id") or ""),
                        "title": str(doc.get("name") or doc.get("title") or doc.get("id") or "")[:120]
                                 + f" (@{a['rev']}, {a['state']})"})
    return out


SETUP_ACTOR_MAX = 120


def setup_actor(value):
    """Der Name aus X-OAAP-Setup-Actor, für die Prüfspur. HTTP liest Kopfzeilen als
    Latin-1; kam UTF-8 (etwa „jörg“), wird es zurückgeholt."""
    value = value or ""
    try:
        value = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    text = "".join(ch for ch in value if ch.isprintable())
    return " ".join(text.split())[:SETUP_ACTOR_MAX] or "plattform"


def setup_body_items(body):
    """Prüft die Form: (dry, mode, list) oder ValueError mit einem Satz."""
    if not isinstance(body, dict) or body.get("v") != 1:
        raise ValueError('Erwartet {"v": 1, "items": [...]} oder {"v": 1, "remove": [...]}.')
    if ("items" in body) == ("remove" in body):
        raise ValueError("Genau eines von items und remove.")
    mode = "items" if "items" in body else "remove"
    entries = body[mode]
    if not isinstance(entries, list) or not entries or len(entries) > 200:
        raise ValueError(f"{mode}: eine Liste mit 1 bis 200 Einträgen.")
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get("kind"), str) \
                or not isinstance(e.get("id"), str) or not 0 < len(e["id"]) <= 128 \
                or not 0 < len(e["kind"]) <= 40:
            raise ValueError("Jeder Eintrag ist ein Objekt mit kind und id (Text).")
    dry = body.get("dry", False)
    if not isinstance(dry, bool):
        raise ValueError("dry ist true oder false.")
    return dry, mode, entries


def setup_post(body, actor, recipe=""):
    """POST: die Ergebnisse, eins je Eintrag, in Reihenfolge. Ohne `dry` hält die
    Einrichtung am ersten conflict/refused an (§3.1: „the build stops there“) -- was
    dahinter steht, wird nicht mehr angefasst und als refused gemeldet."""
    dry, mode, entries = setup_body_items(body)
    results, planned, stopped = [], set(), ""
    with SETUP_LOCK:
        for e in entries:
            if stopped:
                result, note = "refused", f"Nicht ausgeführt: die Einrichtung hält bei {stopped}."
            elif mode == "remove":
                result, note = setup_remove(e, actor)
            else:
                result, note = setup_item(e, dry, actor, planned)
                if not dry and result in ("conflict", "refused"):
                    stopped = f"{e['kind']}/{e['id']}"
                if not dry and recipe and e["kind"] == "assistant" and result in ("made", "present"):
                    books.assistant_set_origin(DB, e["id"], recipe)   # 0.37: für `from` im Export
            if not dry and result not in ("present",):
                books.log(DB, "setup:" + actor, "setup." + result, f"{e['kind']}/{e['id']}",
                          note[:500])
                DB.commit()
            results.append({"kind": e["kind"], "id": e["id"], "result": result, "note": note})
    return {"v": 1, "results": results}


# ------------------------------------------------------------- Kommandozeile

def cli(argv):
    """Für kopflose Knoten: `docker exec <container> python3 /srv/app.py …`."""
    usage = ("Aufruf: app.py server set <name> --address A|destination:D[/pfad] [--auth none|bearer|basic|header:N|propagate] "
             "[--light green|yellow|red] [--credential-stdin ja] | server read <name> | server list\n"
             "        app.py view set <name> [--max-effect read|write|execute] "
             "[--elicitation pass|refuse] [--roots pass|refuse] [--sampling pass|refuse] [--ways key,signin,platform] [--approval required|signed|none] [--approval-key-stdin ja] | view list\n"
             "        app.py view add <sicht> <server> <name> [--kind tool|resource|template|prompt] "
             "[--as NAME] [--effect read] | view confirm <sicht> <name> [--kind K] [--effect E]\n"
             "        app.py view tailor <sicht> <name> [--kind K] [--rename N] [--description T] "
             "[--fixed JSON] [--bounds JSON] [--effect E] | view remove <sicht> <name> [--kind K]\n"
             "        app.py key set <etikett> [--views a,b] [--max-effect E] [--ceiling L] (auch Zugangsprofile)\n"
             "        app.py key issue <etikett> --views a,b [--max-effect read] [--ceiling yellow] "
             "[--budget N] [--rate N] [--owner X ...] | key list | key revoke <etikett>\n"
             "        app.py profile create <etikett> --groups /a,/b|*|@chat|@* --views a,b [wie key issue] | "
             "profile groups <etikett> --groups ...\n"
             "        app.py server set <name> --auth exchange --token-url U --client-id C --audience A "
             "--credential-stdin ja | server read <name> [--token-stdin ja]\n"
             "        app.py source list | source sync   (Quellen der Plattform, RFC-0073)\n"
             "        app.py system set <id> --title T [--role production] [--kind sap] [--active ja|nein] "
             "[--facts sid=A4H,client=001] [--light yellow] [--servers s1,s2] [--description D] | "
             "system list | system remove <id>   (RFC-0114)\n"
             "        app.py view multi <sicht> <referenzsystem> | view system-add <sicht> <system> | "
             "view system-remove <sicht> <system> | view system-approve <sicht> <system> <werkzeug> | "
             "view systems <sicht> | view system-approve <sicht> <system> <name> --kind prompt|resource|template | "
             "view convert <sicht> <referenzsystem> [<neue sicht>]   (ohne neue Sicht: nur der Plan)")
    if len(argv) < 2:
        print(usage)
        return 2
    area, action, rest = argv[0], argv[1], argv[2:]
    opts, i = {}, 0
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            opts[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            i += 1
    pos = []
    i = 0
    while i < len(rest):
        if rest[i].startswith("--"):
            i += 2
        else:
            pos.append(rest[i])
            i += 1
    try:
        if area == "source" and action == "sync":
            ok, text = sync_sources()
            print(text, file=sys.stdout if ok else sys.stderr)
            return 0 if ok else 1
        if area == "source" and action == "list":
            for src in fetch_sources():
                print(f'{src.get("name", ""):24} {src.get("app", "")}@{src.get("version", "")} '
                      f'{src.get("channel", ""):10} {src.get("address", "")}')
            return 0
        if area == "system" and action == "list":
            for s in landscape.systems(DB):
                print(f'{s["id"]:16} {landscape.ROLE_WORDS.get(s["role"], s["role"]):12} '
                      f'{"aktiv" if s["active"] else "aus":6} {landscape.system_light(DB, s):6} '
                      f'{",".join(landscape.servers_of(DB, s["id"])) or "-":30} {s["title"]}')
            return 0
        if area == "system" and action == "set" and pos:
            old = landscape.system_row(DB, pos[0])
            servers = ([x.strip() for x in opts["servers"].split(",") if x.strip()]
                       if "servers" in opts else None)
            landscape.save_system(
                DB, "cli", pos[0], opts.get("title", old["title"] if old else ""),
                opts.get("role", old["role"] if old else "other"),
                opts.get("active", "ja" if (not old or old["active"]) else "nein").lower()
                in ("ja", "yes", "1", "true"),
                opts.get("kind", old["kind"] if old else ""),
                landscape.parse_facts(opts["facts"]) if "facts" in opts
                else (landscape.facts_of(old) if old else {}),
                opts.get("light", old["light"] if old else ""),
                opts.get("description", old["description"] if old else ""), servers)
            print(f"Zielsystem „{pos[0]}“ gespeichert.")
            return 0
        if area == "system" and action == "remove" and pos:
            landscape.delete_system(DB, "cli", pos[0])
            print(f"Zielsystem „{pos[0]}“ gelöscht.")
            return 0
        if area == "view" and action == "multi" and len(pos) >= 2:
            landscape.make_multi(DB, "cli", pos[0], pos[1])
            print(f"„{pos[0]}“ ist eine Multi-System-Sicht mit Referenz „{pos[1]}“.")
            return 0
        if area == "view" and action == "system-add" and len(pos) >= 2:
            landscape.add_system(DB, "cli", pos[0], pos[1])
            action, pos = "systems", pos[:1]
        if area == "view" and action == "system-remove" and len(pos) >= 2:
            landscape.remove_system(DB, "cli", pos[0], pos[1])
            print("Herausgenommen.")
            return 0
        if area == "view" and action == "system-approve" and len(pos) >= 3:
            landscape.approve_for_system(DB, "cli", pos[0], pos[1], opts.get("kind", "tool"), pos[2])
            print(f"„{pos[2]}“ für „{pos[1]}“ freigegeben.")
            return 0
        if area == "view" and action == "convert" and len(pos) >= 2:
            plan = landscape.convert_plan(DB, pos[0], pos[1])
            for p in plan["problems"]:
                print(p, file=sys.stderr)
            if plan["problems"]:
                return 1
            for r in plan["rows"]:
                what = ("Freigabe übernommen" if r["carry"] else "gleich" if r["old"]
                        else "kommt hinzu bei gleichem Text")
                print(f'{r["system"]:16} {r["name"][:35]:36} {(r["old"] or "-")[:35]:36} {what}')
            if len(pos) < 3:
                print("Nur der Plan; mit einem Namen für die neue Sicht wird sie angelegt.")
                return 0
            landscape.convert(DB, "cli", pos[0], pos[1], pos[2])
            print(f"Neue Multi-System-Sicht „{pos[2]}“ angelegt; „{pos[0]}“ bleibt.")
            return 0
        if area == "view" and action == "systems" and pos:
            sids = landscape.view_system_ids(DB, pos[0])
            if not sids:
                print(f"„{pos[0]}“ ist keine Multi-System-Sicht.")
                return 1
            print("Werkzeug".ljust(36) + "".join(s[:16].ljust(18) for s in sids))
            for item in books.items(DB, pos[0]):
                print(item["name"][:35].ljust(36) + "".join(
                    landscape.STATE_WORDS[landscape.availability(DB, pos[0], item, s)[0]][:16].ljust(18)
                    for s in sids))
            return 0
        if area == "server" and action == "list":
            for s in books.servers(DB):
                print(f'{s["name"]:20} {s["address"]:50} {s["light"]:6} {s["version"] or "-":11} '
                      + ("Quelle " if s["source"] else "")
                      + (f'WEG seit {s["gone"]} ' if s["gone"] else "")
                      + f'{s["last_error"] or ""}')
            return 0
        if area == "server" and action == "set" and pos:
            form = {"name": pos[0], "address": opts.get("address", ""),
                    "auth": opts.get("auth", "none"), "light": opts.get("light", "red")}
            if form["auth"].startswith("header:"):
                form["auth"], form["header"] = "header", form["auth"][7:]
            for opt, field in (("token-url", "token_url"), ("client-id", "client_id"),
                               ("audience", "audience")):
                if opt in opts:
                    form[field] = opts[opt]
            old = books.server_row(DB, pos[0])
            for n in guard.DEFAULTS:
                form["lim_" + n] = opts.get(n.replace("_", "-"),
                                            limits_of(old)[n] if old else guard.DEFAULTS[n])
            if opts.get("credential-stdin", "").lower() in ("ja", "yes", "1"):
                form["credential"] = sys.stdin.readline().rstrip("\n")
            print(save_server(form, "cli"))
            return 0
        if area == "server" and action == "read" and pos:
            person = None
            if opts.get("token-stdin", "").lower() in ("ja", "yes", "1"):
                # Verfahren X: ein Token des Betreibers, nur für diesen einen Lesevorgang.
                tok = sys.stdin.readline().strip()
                try:
                    claims = signin.check(tok, ISSUER_KEYS, ISSUER, AUDIENCE) if ISSUER_KEYS else None
                except signin.Invalid as exc:
                    raise ValueError(f"Token taugt nicht: {exc}") from None
                if not claims:
                    raise ValueError("Ohne MCPGW_ISSUER gibt es keine Anmeldung.")
                person = {"iss": claims["iss"], "sub": claims["sub"], "token": tok}
            ok, text = read_catalog(pos[0], person)
            print(text)
            return 0 if ok else 1
        if area == "view" and action == "list":
            for v in books.views(DB):
                reqs = books.view_requests(v)
                print(f'{v["name"]:20} bis={v["max_effect"]} ampel={books.view_light(DB, v["name"])} zugänge={v["ways"]} '
                      + " ".join(f"{r}={reqs[r]}" for r in books.REQUESTS)
                      + (f" freigabe={v['approval']}" if v["approval"] else ""))
                for item in books.items(DB, v["name"]):
                    print(f'    {item["kind"]:8} {item["name"]:30} {item["server"]}:{item["source"]} '
                          f'{item["effect"]:8} {books.item_state(DB, item)}'
                          + (f' fest={item["fixed"]}' if item["fixed"] != "{}" else "")
                          + (f' grenzen={item["bounds"]}' if item["bounds"] != "{}" else ""))
            return 0
        if area == "view" and action == "set" and pos:
            if "server" in opts:
                raise ValueError("Seit 0.2 hat eine Sicht keinen festen Server mehr; Einträge "
                                 "kommen mit `view add <sicht> <server> <name>` hinein.")
            old = books.view_row(DB, pos[0])
            reqs = {r: opts[r] for r in books.REQUESTS if r in opts}
            books.save_view(DB, "cli", pos[0],
                            opts.get("max-effect", old["max_effect"] if old else "read"), reqs or None,
                            opts.get("ways"))
            sealed = None
            if opts.get("approval-key-stdin", "").lower() in ("ja", "yes", "1"):
                fk = sys.stdin.readline().strip()
                if not signed_approval.KEY_RE.match(fk):
                    raise ValueError("Ein Freigabeschlüssel sieht so aus: oaap-fk-<64 Hex-Zeichen>.")
                sealed = VAULT.seal(MASTER, fk)
            if "approval" in opts or sealed:
                old = books.view_row(DB, pos[0])
                books.set_view_approval(DB, "cli", pos[0], opts.get("approval", old["approval"] if old else ""),
                                        sealed)
            print("gespeichert")
            return 0
        if area == "view" and action == "add" and len(pos) >= 3:
            name = books.approve(DB, "cli", pos[0], opts.get("kind", "tool"), pos[1], pos[2],
                                 opts.get("as"), opts.get("effect", "read"))
            print(f"freigegeben als „{name}“, Text festgehalten")
            return 0
        if area == "view" and action == "confirm" and len(pos) >= 2:
            books.confirm(DB, "cli", pos[0], opts.get("kind", "tool"), pos[1], opts.get("effect"))
            print("bestätigt, neuer Text festgehalten")
            return 0
        if area == "view" and action == "tailor" and len(pos) >= 2:
            form = {"view": pos[0], "name": pos[1], "kind": opts.get("kind", "tool")}
            for opt, field in (("rename", "new_name"), ("description", "description"),
                               ("fixed", "fixed"), ("bounds", "bounds"), ("effect", "effect")):
                if opt in opts:
                    form[field] = opts[opt]
            changed = tailor_from_form(form, "cli")
            print("; ".join(changed) or "unverändert")
            return 0
        if area == "view" and action == "remove" and len(pos) >= 2:
            ok = books.remove_item(DB, "cli", pos[0], opts.get("kind", "tool"), pos[1])
            print("herausgenommen" if ok else "nicht gefunden")
            return 0 if ok else 1
        if area == "key" and action == "list":
            for k in books.keys(DB):
                print(f'{k["label"]:24} {"WIDERRUFEN" if k["revoked"] else "gültig":10} '
                      f'sichten={k["views"]} bis={k["max_effect"]} ampel={k["ceiling"]} '
                      f'| {context_line(k)}')
            return 0
        if area == "key" and action == "set" and pos:
            row = next((k for k in books.keys(DB) if k["label"] == pos[0] and not k["revoked"]), None)
            if not row:
                print(f"„{pos[0]}“ gibt es nicht, oder es ist widerrufen.", file=sys.stderr)
                return 1
            changed = books.set_key_limits(DB, "cli", pos[0], opts.get("views", row["views"]),
                                           opts.get("max-effect", row["max_effect"]),
                                           opts.get("ceiling", row["ceiling"]))
            print("geändert: " + ", ".join(changed) if changed else "unverändert")
            return 0
        if area == "key" and action == "revoke" and pos:
            print("widerrufen" if books.revoke(DB, pos[0], "cli") else "unbekannt oder schon widerrufen")
            return 0
        if area == "key" and action == "issue" and pos:
            value, _ = books.issue(
                DB, pos[0], "cli", views=[v for v in opts.get("views", "").split(",") if v],
                max_effect=opts.get("max-effect", "read"), ceiling=opts.get("ceiling", "yellow"),
                budget_calls=_int(opts.get("budget")), rate_per_min=_int(opts.get("rate")),
                **{n: opts[n.replace("_", "-")] for n in books.CONTEXT_FIELDS
                   if n.replace("_", "-") in opts})
            print(value)
            print("Dieser Wert wird nicht wieder angezeigt.", file=sys.stderr)
            return 0
        if area == "profile" and action == "create" and pos:
            books.issue(DB, pos[0], "cli", views=[v for v in opts.get("views", "").split(",") if v],
                        max_effect=opts.get("max-effect", "read"), ceiling=opts.get("ceiling", "yellow"),
                        budget_calls=_int(opts.get("budget")), rate_per_min=_int(opts.get("rate")),
                        kind="profile", groups=opts.get("groups", ""),
                        **{n: opts[n.replace("_", "-")] for n in books.CONTEXT_FIELDS
                           if n.replace("_", "-") in opts})
            print("Zugangsprofil angelegt")
            return 0
        if area == "profile" and action == "groups" and pos:
            books.set_profile_groups(DB, "cli", pos[0], opts.get("groups", ""))
            print("Gruppen gesetzt")
            return 0
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(usage)
    return 2


def main():
    if len(sys.argv) > 1:
        sys.exit(cli(sys.argv[1:]))
    sys.stdout.write(f"OAAP MCP-Gateway {VERSION} auf Port {PORT} — {len(books.servers(DB))} "
                     f"Server, {len(books.views(DB))} Sichten; Katalog alle "
                     f"{CATALOG_MINUTES or '—'} Minuten\n")
    sys.stdout.flush()
    threading.Thread(target=housekeeping, daemon=True).start()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
