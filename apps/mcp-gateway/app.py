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
import html
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, quote, unquote_plus

import argcheck
import books
import compose
import guard
import signin
import upstream
from gwcore import charts as gcharts
from gwcore import report as greport
from gwcore.vault import Vault, VaultError

VERSION = "0.3.0"
PORT = 8000

DATA_DIR = os.environ.get("MCPGW_DATA_DIR", "/data")
DB_PATH = os.path.join(DATA_DIR, "gateway.db")
MASTER = os.environ.get("MCPGW_MASTER_KEY", "")
VAULT = Vault(b"oaap-mcp-gateway-vault|", "MCPGW_MASTER_KEY")

# Anmeldung (Spec §2.2, RFC-0065 D14/D15). Ohne Issuer ist sie aus.
ISSUER = (os.environ.get("MCPGW_ISSUER") or "").strip().rstrip("/")
AUDIENCE = (os.environ.get("MCPGW_AUDIENCE") or "").strip()
SIGNIN_CLIENT = (os.environ.get("MCPGW_SIGNIN_CLIENT") or "").strip()
ISSUER_KEYS = signin.Keys(ISSUER) if ISSUER else None

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


EXCHANGED = {}                       # (Issuer, sub, Server) → (Token, gültig bis); nur im Speicher
_EXCHANGED_LOCK = threading.Lock()


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
    if srow["auth_kind"] != "exchange":
        return {"auth_kind": srow["auth_kind"], "credential": credential}, url
    if not person or not person.get("token"):
        raise NoPerson("Dieser Server arbeitet mit Verfahren X und braucht einen angemeldeten "
                       "Menschen; mit einem Schlüssel geht es nicht.")
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
    """
    srow = books.server_row(DB, name)
    if not srow:
        return False, f"Einen Server „{name}“ gibt es nicht."
    if srow["auth_kind"] == "exchange" and not person:
        return False, (f"Katalog von „{name}“ nicht gelesen: Verfahren X braucht ein Token eines "
                       f"Menschen — app.py server read {name} --token-stdin ja.")
    lim = limits_of(srow)
    try:
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
    while True:
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
    for item in books.items(DB, vrow["name"], kind):
        if books.item_state(DB, item) != "active":
            continue
        if not rank_ok(item["effect"], vrow["max_effect"], key["max_effect"]):
            continue
        out[item["name"]] = item
    return out


def refusal(vrow, kind, name):
    """Warum ein Eintrag nicht sichtbar ist: (Grund, Code, Satz). Nennt nie den Server."""
    word = compose.KIND_WORDS[kind]
    item = books.item_row(DB, vrow["name"], kind, name)
    state = books.item_state(DB, item) if item else None
    if state == "suspended":
        return "suspended", -32002, (f"{word} {name} ist gesperrt, bis der Betreiber einen "
                                     "geänderten Text bestätigt.")
    if state == "active":
        return "effect", -32002, (f"{word} {name} liegt über der Wirkungsklasse dieses "
                                  "Schlüssels oder der Sicht.")
    if kind in ("resource", "template"):
        return "unknown_resource", -32002, "Diese Resource gibt es in dieser Sicht nicht."
    return "unknown_" + kind, -32602, f"Unbekanntes {word}: {name}"


def entitled(key, vrow, way):
    if not vrow or vrow["name"] not in books.key_views(key) or way not in books.view_ways(vrow):
        return False
    light = books.view_light(DB, vrow["name"])
    return books.LIGHT_RANK[light] <= books.LIGHT_RANK.get(key["ceiling"], 1)


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
  nav.tabs{display:flex;gap:1rem;margin:0 0 1rem}
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


def page(title, body, user, roles):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — OAAP MCP-Gateway</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP MCP-GATEWAY</b><small>Eine Sicht, ein Schlüssel, freigegebene Werkzeuge</small></span></a>
  <div class="userbox">{esc(user)}<br><small>{esc(roles)}</small></div>
</header>
<main>
<nav class="tabs"><a href="./">Übersicht</a><a href="auswertung">Auswertung</a></nav>
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


def admin_page(user, roles, host, notice="", secret=None):
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
<pre>{esc(cmd)}</pre></div>''')
    parts.append(signin_card(host))
    if not VAULT.usable(MASTER):
        parts.append('<div class="card attention"><p><b>MCPGW_MASTER_KEY fehlt.</b> Server ohne '
                     'Zugangsdaten und Server hinter einer Destination lassen sich pflegen; eigene '
                     'Zugangsdaten verweigert die App, bis der Tresorschlüssel gesetzt ist (Portal → '
                     'Instanz → Konfiguration → erzeugen).</p></div>')

    rows = []
    for s in books.servers(DB):
        g = GUARDS.get(s["name"])
        st = g.state() if g else None
        load = (f'{st["inflight"]} laufend, {st["waiting"]} wartend'
                + (f', Pause {st["open_for"]} s' if st["open_for"] else "")) if st else "—"
        rows.append(f'<tr><td><a href="server?name={quote(s["name"])}"><b>{esc(s["name"])}</b></a></td>'
                    f'<td><code>{esc(s["address"])}</code></td><td>{badge(s["light"], s["light"])}</td>'
                    f'<td>{esc(s["version"] or "—")}</td>'
                    f'<td>{esc(s["last_read"] or "nie")}'
                    f'{"<br><span class=err>" + esc(s["last_error"]) + "</span>" if s["last_error"] else ""}'
                    f'</td><td>{esc(load)}</td></tr>')
    parts.append(f'''<div class="card"><h2>Server</h2>
<p class="hint">MCP-Server mit Streamable HTTP. Adresse als URL oder <code>destination:&lt;name&gt;</code>
— dann setzt die Plattform die Zugangsdaten ein, und das Gateway hält kein Geheimnis.</p>
<div class="scroll"><table><tr><th>Name</th><th>Adresse</th><th>Ampel</th><th>Protokoll</th>
<th>Katalog gelesen</th><th>Last</th></tr>{"".join(rows) or '<tr><td colspan=6 class=muted>Noch kein Server.</td></tr>'}</table></div>
<h2 style="margin-top:1.2rem">Server anlegen</h2>{server_form()}</div>''')

    vrows = []
    for v in books.views(DB):
        states = {}
        for item in books.items(DB, v["name"]):
            st = books.item_state(DB, item)
            states[st] = states.get(st, 0) + 1
        summary = ", ".join(f"{n} {STATE_WORDS[s][1]}" for s, n in sorted(states.items())) or "leer"
        light = books.view_light(DB, v["name"])
        vrows.append(f'<tr><td><a href="view?name={quote(v["name"])}"><b>{esc(v["name"])}</b></a></td>'
                     f'<td>{esc(", ".join(books.view_servers(DB, v["name"])) or "—")}<br>{badge(light, light)}</td>'
                     f'<td>bis {esc(books.EFFECT_WORDS[v["max_effect"]])}<br>'
                     f'<span class="muted">{esc(books.WAY_WORDS.get(v["ways"], v["ways"]))}</span></td>'
                     f'<td>{esc(summary)}</td><td><code>{esc(endpoint(host, v["name"]))}</code></td></tr>')
    parts.append(f'''<div class="card"><h2>Sichten</h2>
<p class="hint">Eine Sicht ist ein MCP-Endpunkt. Sie bündelt, was hier freigegeben ist — aus einem
Server oder mehreren. Ein neues Tool eines Servers erscheint nirgends von selbst.</p>
<div class="scroll"><table><tr><th>Sicht</th><th>Server, Ampel</th><th>Höchste Klasse</th><th>Einträge</th>
<th>Endpunkt</th></tr>{"".join(vrows) or '<tr><td colspan=5 class=muted>Noch keine Sicht.</td></tr>'}</table></div>
<h2 style="margin-top:1.2rem">Sicht anlegen</h2>
<form class="grid" method="post" action="view-save">
<div><label>Name (Pfad)</label><input type="text" name="name" required></div>
<div><label>Höchste Wirkungsklasse</label><select name="max_effect">{options(books.EFFECTS, "read", books.EFFECT_WORDS)}</select></div>
<div><label>Zugänge</label><select name="ways">{options(books.WAYS, "key", books.WAY_WORDS)}</select></div>
<div><button>Anlegen</button></div></form></div>''')

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
        krows.append(f'<tr><td><b>{esc(k["label"])}</b><br>{state}</td>'
                     f'<td>{esc(", ".join(books.key_views(k)))}</td>'
                     f'<td>bis {esc(books.EFFECT_WORDS.get(k["max_effect"], k["max_effect"]))}<br>'
                     f'{badge(k["ceiling"], "Ampel bis " + k["ceiling"])}</td>'
                     f'<td>{esc(used)}{" / Budget " + str(k["budget_calls"]) if k["budget_calls"] else ""}</td>'
                     f'<td class="muted">{esc(context_line(k))}</td><td>{action}</td></tr>')
    vnames = [v["name"] for v in books.views(DB)]
    parts.append(f'''<div class="card"><h2>Schlüssel und Zugangsprofile</h2>
<p class="hint">Ein Schlüssel ist ein Geheimnis für Agenten und Pipelines. Ein Zugangsprofil hat keinen Wert:
Es gilt für angemeldete Menschen der genannten Gruppen (volle Pfade wie /sap-dev, oder * für alle).</p>
<div class="scroll"><table><tr><th>Etikett</th><th>Sichten</th><th>Grenzen</th><th>Verbrauch</th>
<th>Kontext</th><th></th></tr>{"".join(krows) or '<tr><td colspan=6 class=muted>Noch kein Schlüssel.</td></tr>'}</table></div>
<h2 style="margin-top:1.2rem">Schlüssel ausstellen oder Zugangsprofil anlegen</h2>
<form class="grid" method="post" action="issue">
<div><label>Etikett</label><input type="text" name="label" required></div>
<div><label>Art</label><select name="kind">{options(("key", "profile"), "key", {"key": "Schlüssel", "profile": "Zugangsprofil"})}</select></div>
<div><label>Gruppen (nur Zugangsprofil, Komma)</label><input type="text" name="groups"></div>
<div><label>Sichten (Komma) — bekannt: {esc(", ".join(vnames) or "keine")}</label>
<input type="text" name="views" required></div>
<div><label>Höchste Wirkungsklasse</label><select name="max_effect">{options(books.EFFECTS, "read", books.EFFECT_WORDS)}</select></div>
<div><label>Ampel höchstens</label><select name="ceiling">{options(books.LIGHTS, "yellow")}</select></div>
<div><label>Budget (Aufrufe, 0 = ohne)</label><input type="number" name="budget" value="0" min="0"></div>
<div><label>Rate je Minute (0 = {DEFAULT_RATE})</label><input type="number" name="rate" value="0" min="0"></div>
{context_inputs()}<div><button>Ausstellen</button></div></form></div>''')

    labels = {k["id"]: k["label"] for k in books.keys(DB)}
    urows = "".join(
        f'<tr><td>{esc(r["time"])}</td><td>{esc(labels.get(r["key_id"], r["key_id"]))}</td>'
        f'<td>{esc(r["view"])}</td><td><code>{esc(r["tool"] or "—")}</code>'
        f'{"" if r["kind"] == "tool" else " <span class=muted>" + esc(compose.KIND_WORDS.get(r["kind"], r["kind"])) + "</span>"}</td>'
        f'<td>{esc(r["outcome"])}{" (" + esc(r["reason"]) + ")" if r["reason"] else ""}</td>'
        f'<td class="num">{r["ms"]} ms</td><td class="num">{esc(fmt_bytes(r["bytes_up"]))} / '
        f'{esc(fmt_bytes(r["bytes_down"]))}</td></tr>' for r in books.recent(DB, limit=40))
    proto = ", ".join(f'{r["method"]} {r["n"]}' for r in books.protocol_totals(DB)) or "—"
    parts.append(f'''<div class="card"><h2>Letzte Aufrufe</h2>
<p class="hint">Gezählt, nicht mitgeschrieben: Argumente und Ergebnisse stehen nirgends.
Protokollverkehr der letzten 30 Tage: {esc(proto)}.</p>
<div class="scroll"><table><tr><th>Zeit</th><th>Schlüssel</th><th>Sicht</th><th>Eintrag</th><th>Ausgang</th>
<th>Dauer</th><th>Bytes hoch/runter</th></tr>{urows or '<tr><td colspan=7 class=muted>Noch keine.</td></tr>'}</table></div></div>''')

    arows = "".join(f'<tr><td>{esc(r["time"])}</td><td>{esc(r["actor"])}</td><td>{esc(r["action"])}</td>'
                    f'<td>{esc(r["subject"])}</td><td class="muted">{esc(r["detail"])}</td></tr>'
                    for r in books.audit(DB, 40))
    parts.append(f'<div class="card"><h2>Prüfspur</h2><div class="scroll"><table><tr><th>Zeit</th>'
                 f'<th>Wer</th><th>Was</th><th>Woran</th><th>Einzelheiten</th></tr>{arows}</table></div></div>')
    return page("Übersicht", "".join(parts), user, roles)


CALLBACK_PORT = 33418                # fester Rücksprung-Port für Claude Code (D15)


def signin_card(host):
    """Wie sich Menschen anmelden (Spec §2.2) — oder warum es noch nicht geht."""
    if not ISSUER:
        return ('<div class="card"><h2>Anmeldung von Menschen</h2><p class="muted">Aus. Für die Anmeldung am '
                'Anmeldedienst des Mandanten MCPGW_ISSUER, MCPGW_AUDIENCE und MCPGW_SIGNIN_CLIENT in der '
                'Konfiguration der Instanz setzen (RFC-0065 D14, D15).</p></div>')
    err = ISSUER_KEYS.error if ISSUER_KEYS else ""
    views = [v["name"] for v in books.views(DB) if "signin" in books.view_ways(v)]
    cmd = "\n".join(f"claude mcp add --transport http {v} {endpoint(host, v)} \\\n"
                    f"  --client-id {SIGNIN_CLIENT or '<client>'} --callback-port {CALLBACK_PORT}"
                    for v in views) or "Noch keine Sicht nimmt Anmeldungen an."
    return f'''<div class="card"><h2>Anmeldung von Menschen</h2>
<p>Issuer <code>{esc(ISSUER)}</code> · Audience <code>{esc(AUDIENCE or "— fehlt")}</code> ·
Client für Menschen <code>{esc(SIGNIN_CLIENT or "— fehlt")}</code></p>
{"<p class=err>Schlüssel des Issuers: " + esc(err) + "</p>" if err else ""}
<p class="hint">Im Realm braucht es einen öffentlichen Client (Rücksprung nur
<code>http://localhost:{CALLBACK_PORT}/callback</code>) mit einem Audience-Mapper auf
<code>{esc(AUDIENCE or "MCPGW_AUDIENCE")}</code>. Wer zahlt, entscheidet ein Zugangsprofil über die
Gruppen im Token; ohne passendes Profil gibt es 403.</p>
<pre>{esc(cmd)}</pre></div>'''


def server_form(s=None):
    lim = limits_of(s) if s else dict(guard.DEFAULTS)
    kind = s["auth_kind"] if s else "none"
    plain = kind if kind in books.AUTH_KINDS else "header"
    header = kind[7:] if kind.startswith("header:") else ""
    ex = json.loads(s["exchange"] or "{}") if s else {}
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
<div><label>Zugang (Verfahren T, oder X = Token-Tausch)</label><select name="auth">{options(("none", "bearer", "basic", "header", "exchange"), plain)}</select></div>
<div><label>Kopfzeile (bei Zugang header)</label><input type="text" name="header" value="{esc(header)}"></div>
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
{limit_inputs}<div><button>Speichern</button></div></form>'''


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
<p>{badge(s["light"], LIGHT_WORDS[s["light"]])} Protokoll {esc(s["version"] or "noch nicht ausgehandelt")}
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
<div class="card"><h2>Ändern</h2>{server_form(s)}
<form method="post" action="server-delete" style="margin-top:1rem"><input type="hidden" name="name" value="{esc(name)}">
<button class="quiet">Server löschen</button></form></div>''')
    return page(f"Server {name}", "".join(parts), user, roles)


REQUEST_WORDS = {"elicitation": "Rückfrage an den Menschen (elicitation)",
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


def view_page(user, roles, host, name, notice=""):
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
    parts.append(f'''<div class="card"><h1>Sicht {esc(name)}</h1>
<p>Endpunkt <code>{esc(endpoint(host, name))}</code> · Server: {esc(", ".join(books.view_servers(DB, name)) or "noch keiner")}
· {badge(light, "Ampel " + light)} · Schlüssel: {esc(", ".join(users) or "keiner")}</p>
<p class="hint">Die Ampel einer Sicht ist die ihres schlechtesten Servers. Rückfragen eines Servers an den
Client gehen nur durch, wenn sie hier durchgereicht werden UND der Client sie kann; Sampling lässt einen
Server das Modell des Clients benutzen und ihm Daten schicken.</p>
<form class="grid" method="post" action="view-save"><input type="hidden" name="name" value="{esc(name)}">
<div><label>Höchste Wirkungsklasse der Sicht</label><select name="max_effect">{options(books.EFFECTS, v["max_effect"], books.EFFECT_WORDS)}</select></div>
<div><label>Zugänge</label><select name="ways">{options(books.WAYS, v["ways"], books.WAY_WORDS)}</select></div>
{req_inputs}<div><button class="quiet">Setzen</button></div></form></div>
{"".join(compare)}
<div class="card"><h2>Freigegebene Einträge</h2><p class="hint">Neue Einträge kommen über den Katalog eines
Servers in die Sicht. Clients sehen nur den hier festgehaltenen Text, unter dem Namen der Sicht. Ein festes
Argument verschwindet aus dem Schema, das der Client sieht; eine Grenze erlaubt nur bestimmte Werte oder ein
Muster mit <code>*</code> und <code>?</code>.</p>
<div class="scroll"><table><tr><th>Art</th><th>Name (Server: Original)</th><th>Zustand</th><th>Klasse</th>
<th>Zuschnitt, Freigabe</th><th></th></tr>
{"".join(rows) or '<tr><td colspan=6 class=muted>Leer.</td></tr>'}</table></div>
<form method="post" action="view-delete" style="margin-top:1rem"><input type="hidden" name="name" value="{esc(name)}">
<button class="quiet">Sicht löschen</button></form></div>''')
    return page(f"Sicht {name}", "".join(parts), user, roles)


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
    return page("Auswertung", body, user, roles)


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


class Refusal(Exception):
    """Das Gateway weist einen Aufruf ab, bevor er zum Server geht."""

    def __init__(self, kind, name, reason, code, text):
        super().__init__(text)
        self.kind, self.name, self.reason, self.code, self.text = kind, name, reason, code, text


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "oaap-mcp-gateway/" + VERSION

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
            if not profile or not entitled(profile, vrow, "signin"):
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
        if path.startswith("/mcp/"):
            self.handle_mcp_post()
            return
        if path in ("/issue", "/revoke", "/context", "/server-save", "/server-delete",
                    "/server-read", "/view-save", "/view-delete", "/item-add", "/item-remove",
                    "/item-confirm", "/item-tailor"):
            self.handle_admin_post(path)
            return
        self.send_json(404, {"error": "Diesen Pfad kennt das Gateway nicht."})

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
            self.send_json(400, rpc_error(None, -32700, "Kein gültiges JSON."))
            return
        if isinstance(msg, list):
            self.send_json(400, rpc_error(None, -32600, "Stapel (JSON-RPC-Batches) werden nicht "
                                                        "unterstützt."))
            return
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
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
                self.send_json(400, rpc_error(msg_id, -32600, LEGACY_ONLY))
                return
            self.send_json(404 if self.headers.get("Mcp-Session-Id") else 400, SESSION_UNKNOWN)
            return
        header_version = self.headers.get("MCP-Protocol-Version")
        if header_version and header_version != sess.version:
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
                 "kind": CALLS[method], "name": "", "server": "", "source": ""}

        def done(outcome, reason=""):
            state["outcome"], state["reason"] = outcome, reason

        def answer(doc, status=200, headers=()):
            if state["sse"]:
                state["down"] += self.sse_event(doc)
            else:
                state["down"] += self.send_json(status, doc, headers)

        try:
            try:
                kind, item, measured = self.resolve(vrow, key, method, params)
            except Refusal as exc:
                state["kind"], state["name"] = exc.kind, exc.name
                done("refused", exc.reason)
                answer(rpc_error(msg_id, exc.code, exc.text))
                return
            state.update(kind=kind, name=measured, server=item["server"], source=item["source"])
            doc = json.loads(item["approved_doc"])
            try:
                if kind == "tool":
                    args = compose.prepare_call(doc, json.loads(item["fixed"]),
                                                json.loads(item["bounds"]), params.get("arguments"))
                    out_params = {"name": item["source"], "arguments": args}
                elif kind == "prompt":
                    args = compose.check_prompt_arguments(doc, params.get("arguments"))
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
            srow = books.server_row(DB, item["server"])
            g = guard_of(srow)
            try:
                probe = g.enter(self.rate_id(key))
            except guard.Limited as exc:
                done("limited", exc.reason)
                answer(rpc_error(msg_id, -32001, f"Sicht {vrow['name']}: {exc}", exc.reason,
                                 exc.retry_after))
                return
            failed = False
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
                         person=self.person, procedure=state.get("procedure", "T"))
            if state["sse"]:
                self.close_connection = True

    def forward(self, sess, vrow, srow, out, lim, state, answer, done):
        """Zum Server und zurück. Gibt True zurück, wenn der WEG scheiterte (Sicherungsautomat)."""
        msg_id = out["id"]
        wants_sse = "text/event-stream" in (self.headers.get("Accept") or "")
        reqs = books.view_requests(books.view_row(DB, vrow["name"]) or vrow)
        if srow["auth_kind"] == "exchange":
            state["procedure"] = "X"
        try:
            server, url = server_access(srow, self.person)
        except NoPerson as exc:
            # Kein Rückfall auf einen technischen Zugang (D8) — und kein Fehler auf dem
            # Weg zum Server: Der Sicherungsautomat zählt das nicht.
            done("refused" if not self.person else "upstream_error", "exchange")
            answer(rpc_error(msg_id, -32003, f"Sicht {vrow['name']}: {exc}"))
            return False
        except upstream.UpstreamError as exc:
            done("upstream_error", exc.kind)
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
            except upstream.TooLarge as exc:
                done("limited", "size")
                answer(rpc_error(msg_id, -32001, f"Sicht {vrow['name']}: Die Antwort ist größer "
                                                 f"als {exc.limit} Bytes und wird nicht "
                                                 "weitergereicht — auch nicht gekürzt.", "size"))
                return False
            except upstream.UpstreamError as exc:
                if exc.kind == "version":
                    books.note_server(DB, srow["name"], error=str(exc))
                done("upstream_error", exc.kind)
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
        if path.startswith(WELL_KNOWN):
            self.resource_metadata(path[len(WELL_KNOWN):])
            return
        if path.startswith("/mcp"):
            # Kein vom Server ausgehender Strom in 0.1 (Spec §6). Gleich für jede
            # Sicht, ob es sie gibt oder nicht: Hier wird nichts verraten.
            self.send_bytes(405, "application/json", b"", [("Allow", "POST, DELETE")])
            return
        if path.startswith("/report"):
            self.handle_report(path)
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
            self.send_html(200, view_page(user, roles, host, query.get("name", "")))
        elif path == "/auswertung":
            self.send_html(200, charts_page(user, roles, query))
        else:
            self.send_html(200, admin_page(user, roles, host))

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

    # ---- Betreiber

    def handle_admin_post(self, path):
        raw = self.read_body(1 << 20) or b""
        if not self.is_admin():
            self.send_html(403, page("Kein Zugriff", '<div class="card"><p>Nicht erlaubt.</p></div>',
                                     "", ""))
            return
        user, roles = self.identity()
        host = self.headers.get("Host", "<gateway>")
        form = {}
        for pair in raw.decode("utf-8", "replace").split("&"):
            if pair:
                name, _, value = pair.partition("=")
                form[unquote_plus(name)] = unquote_plus(value)
        notice, secret, back = "", None, None
        try:
            if path == "/issue":
                views = [v.strip() for v in form.get("views", "").split(",") if v.strip()]
                kind = form.get("kind", "key")
                value, _ = books.issue(
                    DB, form.get("label", ""), user, views=views,
                    max_effect=form.get("max_effect", "read"), ceiling=form.get("ceiling", "yellow"),
                    budget_calls=_int(form.get("budget")), rate_per_min=_int(form.get("rate")),
                    kind=kind, groups=form.get("groups", ""),
                    **{n: form.get(n, "") for n in books.CONTEXT_FIELDS})
                if kind == "profile":
                    notice = f"Zugangsprofil „{form.get('label', '')}“ angelegt."   # Wert verworfen
                else:
                    secret = {"label": form.get("label", ""), "value": value, "views": views}
            elif path == "/revoke":
                label = form.get("label", "")
                notice = (f"Schlüssel „{label}“ widerrufen — ab sofort abgelehnt."
                          if books.revoke(DB, label, user) else f"Nichts zu widerrufen: „{label}“.")
            elif path == "/context":
                changed = books.update_context(DB, form.get("label", ""), user,
                                               **{n: form[n] for n in books.CONTEXT_FIELDS if n in form})
                notice = "Kontext geändert: " + ", ".join(changed) if changed else "Kontext unverändert."
            elif path == "/server-save":
                notice, back = save_server(form, user), ("server", form.get("name", ""))
            elif path == "/server-delete":
                books.delete_server(DB, user, form.get("name", ""))
                notice = f"Server „{form.get('name', '')}“ gelöscht."
            elif path == "/server-read":
                _, notice = read_catalog(form.get("name", ""))
                back = ("server", form.get("name", ""))
            elif path == "/view-save":
                reqs = {r: form["req_" + r] for r in books.REQUESTS if "req_" + r in form}
                books.save_view(DB, user, form.get("name", ""), form.get("max_effect", "read"),
                                reqs or None, form.get("ways") or None)
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
        except ValueError as exc:
            notice = str(exc)
        if back and back[0] == "server" and books.server_row(DB, back[1]):
            self.send_html(200, server_page(user, roles, host, back[1], notice))
        elif back and back[0] == "view" and books.view_row(DB, back[1]):
            self.send_html(200, view_page(user, roles, host, back[1], notice))
        else:
            self.send_html(200, admin_page(user, roles, host, notice, secret))


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
        if not books.NAME.match(address[len("destination:"):]):
            raise ValueError("Nach destination: folgt der Name der Bindung (Kleinbuchstaben, "
                             "Ziffern, Bindestriche).")
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
    books.save_server(DB, user, name, address, auth, form.get("light", "red"), limits,
                      sealed_credential=sealed, drop_credential=form.get("drop_credential") == "1",
                      exchange=exchange)
    srow = books.server_row(DB, name)
    if srow:
        guard_of(srow)
    return f"Server „{name}“ gespeichert."


# ------------------------------------------------------------- Kommandozeile

def cli(argv):
    """Für kopflose Knoten: `docker exec <container> python3 /srv/app.py …`."""
    usage = ("Aufruf: app.py server set <name> --address A [--auth none|bearer|basic|header:N] "
             "[--light green|yellow|red] [--credential-stdin ja] | server read <name> | server list\n"
             "        app.py view set <name> [--max-effect read|write|execute] "
             "[--elicitation pass|refuse] [--roots pass|refuse] [--sampling pass|refuse] [--ways key|signin|key,signin] | view list\n"
             "        app.py view add <sicht> <server> <name> [--kind tool|resource|template|prompt] "
             "[--as NAME] [--effect read] | view confirm <sicht> <name> [--kind K] [--effect E]\n"
             "        app.py view tailor <sicht> <name> [--kind K] [--rename N] [--description T] "
             "[--fixed JSON] [--bounds JSON] [--effect E] | view remove <sicht> <name> [--kind K]\n"
             "        app.py key issue <etikett> --views a,b [--max-effect read] [--ceiling yellow] "
             "[--budget N] [--rate N] [--owner X ...] | key list | key revoke <etikett>\n"
             "        app.py profile create <etikett> --groups /a,/b|* --views a,b [wie key issue] | "
             "profile groups <etikett> --groups ...\n"
             "        app.py server set <name> --auth exchange --token-url U --client-id C --audience A "
             "--credential-stdin ja | server read <name> [--token-stdin ja]")
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
        if area == "server" and action == "list":
            for s in books.servers(DB):
                print(f'{s["name"]:20} {s["address"]:50} {s["light"]:6} {s["version"] or "-":11} '
                      f'{s["last_error"] or ""}')
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
                      + " ".join(f"{r}={reqs[r]}" for r in books.REQUESTS))
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
