"""OAAP FleetView 0.1 — die Flotten-Übersicht (RFC-0021 §3).

Erste Ausbaustufe, strikt lesend: Der Betreiber hinterlegt die
Knotenliste und je Knoten einen Flotten-Schlüssel (als geheimen
Konfigurationswert); FleetView **pollt** die Knoten im Minutentakt
über deren `GET /fleet/status` (spec `oaap.fleet.status` 0.1) und
zeigt die Landschaft: Ampeln, Plattformversionen, Instanzen und die
`attention`-Merker aller Knoten oben auf der Seite. Gehandelt wird im
Portal des jeweiligen Knotens — jede Zeile verlinkt dorthin. Kein
Schreibweg, keine Fernsteuerung: das ist Stufe 2 (RFC-0021 Ausblick).

Regeln, die die Bauform tragen:

- **Schlüssel erscheinen nirgends.** Sie kommen als geheimer
  Konfigurationswert (`FLEETVIEW_KEYS`) herein, wandern ausschließlich
  in den Authorization-Header der Abfrage und werden weder gespeichert
  noch angezeigt — die Oberfläche nennt höchstens die Knotennamen, für
  die einer hinterlegt ist.
- **Nicht erreichbar ist ein Zustand, keine Fehlerseite** (Spec §4):
  der letzte bekannte Stand bleibt stehen und wird als veraltet
  markiert.
- **Alarmierung bleibt bei Uptime Kuma** (Entscheidung 2 zu RFC-0021):
  FleetView zeigt Zustand und Auffälligkeiten, es weckt niemanden.

Gebaut als gewöhnliche OAAP-App nach dem App Deployment Contract —
kein eigener Login (geprüfte Identität als Gateway-Kopfzeile), ein
HTTP-Port, Persistenz nur unter /data, Konfiguration über deklarierte
Umgebungsvariablen, Logs nach stdout, Gesundheitspfad, offline-fähig,
nur Standardbibliothek. Optik nach oaap-design/docs/
design-guidelines.md v0.1 (Blau-Palette, Hexagon, Listenbericht/
Objektseite).
"""
import html
import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import fleet

VERSION = "0.1.0"
PORT = 8000

DATA_DIR = os.environ.get("FLEETVIEW_DATA_DIR", "/data")
STATE_PATH = os.path.join(DATA_DIR, "fleet-state.json")


def _int_env(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(key) or default)))
    except ValueError:
        return default


# Deklarierte Konfiguration (Manifest `config`)
NODES, NODE_ERRORS = fleet.parse_nodes(os.environ.get("FLEETVIEW_NODES", ""))
KEYS = fleet.parse_keys(os.environ.get("FLEETVIEW_KEYS", ""))
POLL_SECONDS = _int_env("FLEETVIEW_POLL_SECONDS", 60, 30, 3600)

esc = html.escape

# ---------------------------------------------------------------- state

_lock = threading.Lock()
_state = fleet.load_state(STATE_PATH)


def poll_now():
    global _state
    with _lock:
        previous = _state
    state = fleet.poll_all(NODES, KEYS, previous)
    with _lock:
        _state = state
        try:
            fleet.save_state(STATE_PATH, state)
        except OSError as e:
            print(f"WARN state not saved: {type(e).__name__}", flush=True)
    reachable = sum(1 for s in state.values() if not s.get("error"))
    print(f"poll: {reachable}/{len(NODES)} Knoten geantwortet", flush=True)


def _poller():
    import time
    while True:
        try:
            poll_now()
        except Exception as e:  # der Poller darf nie sterben
            print(f"WARN poll failed: {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_SECONDS)


# ------------------------------------------------------------ layout
# Gemeinsame Optik mit Portal und Studio (App-UI-Kit von Hand, bis die
# Plattform ein echtes liefert) — identische Palette und Bausteine.

STYLE = """<style>
  :root{
    --oaap-blue-950:#172554; --oaap-blue-900:#1e3a8a; --oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb; --oaap-blue-100:#dbeafe;
    --oaap-bg:#f4f6fa; --oaap-surface:#fff; --oaap-text:#1f2937;
    --oaap-muted:#6b7280; --oaap-border:#e5e7eb;
    --ok:#15803d; --err:#b91c1c; --warn:#b45309;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       margin:0;background:var(--oaap-bg);color:var(--oaap-text)}
  header.oaap{background:linear-gradient(135deg,var(--oaap-blue-900),var(--oaap-blue-950));
       color:#fff;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;padding:.6rem 1.2rem}
  .brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;color:#fff}
  .brand b{font-size:1.15rem;letter-spacing:.08em}
  .brand small{display:block;font-size:.62rem;opacity:.75;letter-spacing:.02em}
  .userbox{display:flex;align-items:center;gap:.7rem;font-size:.9rem;margin-left:auto}
  .userbox .who{text-align:right;line-height:1.2}
  .userbox .who small{opacity:.75}
  main{max-width:62rem;margin:1.6rem auto;padding:0 1.2rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .pagehead{display:flex;align-items:center;justify-content:space-between;gap:1rem;
       flex-wrap:wrap;margin-bottom:1rem}
  .pagehead h1{margin:0}
  .back{display:inline-block;margin-bottom:.8rem;color:var(--oaap-blue-600);text-decoration:none}
  .back:hover{text-decoration:underline}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.test{background:#fef3c7;color:#92400e}
  .badge.off{background:#f3f4f6;color:#6b7280}
  .badge.ok{background:#dcfce7;color:#166534}
  .badge.err{background:#fee2e2;color:#991b1b}
  .badge.warn{background:#fef3c7;color:#92400e}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .err{color:var(--err)}.muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.6rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle}
  th{font-size:.82rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  tr.rowlink:hover td{background:#f8fafc}
  td a{color:var(--oaap-blue-600);text-decoration:none}
  td a:hover{text-decoration:underline}
  dl.facts{margin:0;display:grid;grid-template-columns:11rem 1fr;gap:.45rem 1rem}
  dl.facts dt{color:var(--oaap-muted);font-size:.85rem}
  dl.facts dd{margin:0;word-break:break-word}
  ul.attn{margin:.2rem 0 0;padding-left:1.2rem}
  ul.attn li{margin:.25rem 0}
  footer.oaap{max-width:62rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
       color:var(--oaap-muted);font-size:.8rem;display:flex;gap:.5rem;align-items:center}
  @media (max-width:640px){
    .userbox .who{display:none}
    dl.facts{grid-template-columns:1fr;gap:.1rem .5rem}
    dl.facts dd{margin-bottom:.5rem}
  }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

LOGO_SVG = ('<svg viewBox="0 0 100 100" width="34" height="34" aria-hidden="true">'
            '<polygon points="34,6 58,20 58,48 34,62 10,48 10,20" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round"/>'
            '<polygon points="72,30 92,41 92,64 72,76 52,64 52,41" fill="#fff" opacity=".85"/>'
            '<polygon points="42,58 66,72 66,94 42,96 22,84 22,70" fill="none" stroke="#fff" '
            'stroke-width="6" stroke-linejoin="round" opacity=".6"/></svg>')


def page(title, body, user, roles):
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — OAAP FleetView</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="./">{LOGO_SVG}
    <span><b>OAAP FLEETVIEW</b><small>Die Flotte auf einen Blick — lesend</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP FleetView {VERSION} — zeigt die Landschaft; gehandelt wird im Portal des Knotens
</footer>
</html>"""


STATE_BADGE = {"ok": "ok", "warn": "warn", "error": "err",
               "unknown": "off", "unreachable": "err"}


def badge(state, extra=""):
    label = fleet.STATE_LABELS.get(state, state)
    if extra:
        label += f" · {extra}"
    return f'<span class="badge {STATE_BADGE.get(state, "off")}">{esc(label)}</span>'


def fmt_age(row):
    if not row["fetched"]:
        return "noch nie"
    age = row["age"]
    if age is None:
        return esc(row["fetched"])
    if age < 90:
        return f"vor {age} s"
    if age < 5400:
        return f"vor {age // 60} Min."
    return f"vor {age // 3600} Std."


def attention_card(items):
    if not items:
        return ""
    lis = "".join(
        f'<li><strong>{esc(i["node"])}</strong>: {esc(i["label"])}'
        + (f' — {esc(i["detail"])}' if i["detail"] else "") + "</li>"
        for i in items)
    return (f'<div class="card attention"><h2>Braucht einen Menschen '
            f'({len(items)})</h2><ul class="attn">{lis}</ul></div>')


def overview(user, roles):
    with _lock:
        state = dict(_state)
    rows = fleet.node_rows(NODES, state, interval=POLL_SECONDS)
    attn = fleet.fleet_attention(rows)
    note = fleet.version_note(rows)

    body = ['<div class="pagehead"><h1>Flotte</h1>'
            '<form method="post" action="poll"><button>Jetzt aktualisieren</button></form>'
            "</div>"]
    if NODE_ERRORS:
        errs = "".join(f"<li>{esc(e)}</li>" for e in NODE_ERRORS)
        body.append(f'<div class="card attention"><h2>Knotenliste unvollständig'
                    f'</h2><ul class="attn">{errs}</ul>'
                    '<p class="hint">FLEETVIEW_NODES prüfen (eine Angabe je '
                    "Zeile oder mit ';' getrennt: name=https://adresse).</p></div>")
    body.append(attention_card(attn))
    if note:
        body.append(f'<p class="hint">{esc(note)} — während eines '
                    "Flotten-Updates normal.</p>")

    if not NODES:
        body.append('<div class="card"><h2>Noch keine Knoten</h2>'
                    "<p>Diese App liest ihre Knotenliste aus der Konfiguration "
                    "der Instanz (Portal → Instanzen → FleetView → Konfiguration):</p>"
                    '<dl class="facts">'
                    "<dt>FLEETVIEW_NODES</dt><dd>eine Angabe je Zeile oder mit "
                    "';' getrennt: <code>name=https://adresse</code></dd>"
                    "<dt>FLEETVIEW_KEYS</dt><dd>geheim; je Knoten "
                    "<code>name=schlüssel</code> — den Schlüssel stellt auf dem "
                    "Knoten <code>sudo oaap fleet key issue</code> aus</dd></dl></div>")
    else:
        trs = []
        for r in rows:
            extra = "veraltet" if r["stale"] and r["fetched"] else ""
            trs.append(
                '<tr class="rowlink">'
                f'<td><a href="node/{esc(r["name"])}">{esc(r["name"])}</a></td>'
                f'<td>{badge(r["state"], extra)}</td>'
                f'<td>{esc(r["version"])}</td>'
                f'<td>{r["inst_ok"]}/{r["inst_total"]}</td>'
                f'<td>{fmt_age(r)}</td>'
                f'<td><a href="{esc(r["url"])}/" rel="noopener">Portal öffnen</a></td>'
                "</tr>")
        keys_for = fleet.key_names(KEYS)
        body.append(
            '<div class="card"><h2>Knoten</h2><table>'
            "<tr><th>Knoten</th><th>Zustand</th><th>Plattform</th>"
            "<th>Instanzen gesund</th><th>Stand</th><th></th></tr>"
            + "".join(trs) + "</table>"
            f'<p class="hint">Abfrage alle {POLL_SECONDS} s; Schlüssel '
            f'hinterlegt für: {esc(", ".join(keys_for) if keys_for else "—")}. '
            "Alarmierung bleibt Sache des Monitorings (Uptime Kuma).</p></div>")
    return page("Flotte", "".join(body), user, roles)


def node_page(name, user, roles):
    node = next((n for n in NODES if n["name"] == name), None)
    if not node:
        return None
    with _lock:
        state = dict(_state)
    rows = fleet.node_rows([node], state, interval=POLL_SECONDS)
    r = rows[0]
    attn = fleet.fleet_attention(rows)

    body = [f'<a class="back" href="../">&larr; Zur Flotte</a>',
            f'<div class="pagehead"><h1>{esc(name)}</h1>'
            f'<a class="btn" href="{esc(r["url"])}/" rel="noopener">Portal öffnen</a></div>']
    body.append(attention_card(attn))
    facts = [
        ("Zustand", badge(r["state"], "veraltet" if r["stale"] and r["fetched"] else "")),
        ("Adresse", f'<a href="{esc(r["url"])}/">{esc(r["url"])}</a>'),
        ("Plattformversion", esc(r["version"])),
        ("Profile", esc(", ".join(r["profiles"])) if r["profiles"] else "(keine)"),
        ("Knoten meldet sich als", esc(r["node_says"] or "—")),
        ("Zuletzt gesehen", fmt_age(r)),
    ]
    if r["error"]:
        facts.append(("Letzte Abfrage", f'<span class="err">{esc(r["error"])}</span>'))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    body.append(f'<div class="card"><h2>Überblick</h2><dl class="facts">{dl}</dl></div>')

    if r["instances"]:
        trs = []
        for i in r["instances"]:
            addr = i.get("address", "")
            addr_html = (f'<a href="https://{esc(addr)}/">{esc(addr)}</a>'
                         if addr else "")
            test = i.get("channel") == "test"
            trs.append(
                "<tr>"
                f'<td>{esc(i.get("instance", "?"))}</td>'
                f'<td>{esc(i.get("app", ""))}</td>'
                f'<td>{esc(i.get("version", ""))}</td>'
                f'<td><span class="badge {"test" if test else ""}">'
                f'{"Test" if test else "Produktiv"}</span></td>'
                f'<td>{badge(i.get("state", "unknown"))}</td>'
                f'<td class="muted">{esc(i.get("origin", ""))}</td>'
                f"<td>{addr_html}</td>"
                "</tr>")
        body.append('<div class="card"><h2>Instanzen</h2><table>'
                    "<tr><th>Instanz</th><th>App</th><th>Version</th><th>Kanal</th>"
                    "<th>Zustand</th><th>Herkunft</th><th>Adresse</th></tr>"
                    + "".join(trs) + "</table></div>")
    elif r["has_doc"]:
        body.append('<div class="card"><h2>Instanzen</h2>'
                    "<p>Dieser Knoten meldet keine App-Instanzen.</p></div>")
    else:
        body.append('<div class="card"><h2>Instanzen</h2>'
                    "<p>Noch kein Status-Dokument von diesem Knoten — "
                    "sobald eine Abfrage gelingt, steht hier seine Selbstauskunft.</p></div>")
    return page(name, "".join(body), user, roles)


# ------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "oaap-fleetview"

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _identity(self):
        user = self.headers.get("X-OAAP-User", "?")
        roles = {r.strip() for r in
                 (self.headers.get("X-OAAP-Roles", "") or "").split(",")
                 if r.strip()}
        return user, roles

    def _allowed(self, roles):
        # Das Gateway erzwingt die Manifest-Rollen; die App prüft
        # zusätzlich (Verteidigung in der Tiefe, wie das Studio).
        return bool(roles & {"admin", "partner"})

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            return self._send(200, '{"status":"ok"}',
                              "application/json; charset=utf-8")
        user, roles = self._identity()
        if not self._allowed(roles):
            return self._send(403, page(
                "Kein Zugriff",
                '<div class="card"><h2>Kein Zugriff</h2><p>Die Flotten-'
                "Übersicht erfordert die Rolle <strong>admin</strong> oder "
                "<strong>partner</strong>.</p></div>", user, ",".join(sorted(roles)) or "?"))
        if path == "/":
            return self._send(200, overview(user, ",".join(sorted(roles))))
        if path.startswith("/node/"):
            name = unquote(path[len("/node/"):]).strip("/")
            out = node_page(name, user, ",".join(sorted(roles)))
            if out is not None:
                return self._send(200, out)
        return self._send(404, page(
            "Nicht gefunden", '<div class="card"><h2>Nicht gefunden</h2>'
            '<p><a href="/">Zur Flotte</a></p></div>',
            user, ",".join(sorted(roles))))

    do_HEAD = do_GET

    def do_POST(self):
        user, roles = self._identity()
        if not self._allowed(roles):
            return self._send(403, "Kein Zugriff")
        path = self.path.split("?", 1)[0]
        if path == "/poll":
            try:
                poll_now()
            except Exception as e:
                print(f"WARN manual poll: {type(e).__name__}: {e}", flush=True)
            return self._send(303, "", extra={"Location": "/"})
        return self._send(404, "Nicht gefunden")

    def log_message(self, fmt, *args):
        # stdout-Logs ohne Query-Strings — Kopfzeilen oder Schlüssel
        # haben in einer Logzeile nichts zu suchen.
        sys.stdout.write("%s %s\n" % (self.command, self.path.split("?", 1)[0]))
        sys.stdout.flush()


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    threading.Thread(target=_poller, daemon=True).start()
    print(f"OAAP FleetView {VERSION} auf :{PORT} — {len(NODES)} Knoten, "
          f"Abfrage alle {POLL_SECONDS} s", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
