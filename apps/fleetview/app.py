"""OAAP FleetView 1.0 — die Flotten-Übersicht (RFC-0021, RFC-0085 §10).

Strikt lesend. Seit 1.0.0 liest FleetView die Flotte aus dem Zwilling
seines Mandanten auf der Zentrale (meist `oaap-ops`): Der Flottendienst
der Zentrale schreibt jeden gekoppelten Knoten und seine Instanzen dort
hinein (RFC-0085 Nachtrag A2). FleetView fragt keinen Knoten mehr selbst
ab, hält keine Knotenliste und keinen Schlüssel je Knoten. Ein neu
gekoppelter Knoten erscheint von selbst, unter dem Namen, den er unter
„Gesundheit" trägt.

Regeln, die die Bauform tragen:

- **Ein Zugang:** der Maschinenschlüssel der Instanz (`OAAP_PLATFORM_KEY`,
  bei der Installation ausgestellt, weil das Manifest `FleetNode` und
  `FleetInstance` als `consumes` bindet). Er erscheint nirgends außer im
  Authorization-Header.
- **Nicht erreichbar ist ein Zustand, keine Fehlerseite:** der letzte
  gelesene Stand bleibt stehen, der Fehler steht daneben.
- **Alarmierung bleibt bei der Zentrale** (RFC-0080): FleetView zeigt
  Zustand und Auffälligkeiten, es weckt niemanden.
- **Gehandelt wird im Portal des Knotens;** gekoppelt im Portal der
  Zentrale. Jede Zeile verlinkt dorthin.

Gebaut als gewöhnliche OAAP-App nach dem App Deployment Contract,
nur Standardbibliothek.
"""
import html
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import fleet

VERSION = "1.0.0"
PORT = 8000

DATA_DIR = os.environ.get("FLEETVIEW_DATA_DIR", "/data")
STATE_PATH = os.path.join(DATA_DIR, "fleet-state.json")


def _int_env(key, default, lo, hi):
    try:
        return max(lo, min(hi, int(os.environ.get(key) or default)))
    except ValueError:
        return default


TWIN_URL = os.environ.get("OAAP_TWIN_URL", "").rstrip("/")
PLATFORM_KEY = os.environ.get("OAAP_PLATFORM_KEY", "")
POLL_SECONDS = _int_env("FLEETVIEW_POLL_SECONDS", 60, 30, 3600)

esc = html.escape

# ---------------------------------------------------------------- state

_lock = threading.Lock()
_state = fleet.load_state(STATE_PATH)


def poll_now():
    global _state
    with _lock:
        previous = _state
    state = fleet.read_fleet(TWIN_URL, PLATFORM_KEY, previous)
    with _lock:
        _state = state
        try:
            fleet.save_state(STATE_PATH, state)
        except OSError as e:
            print(f"WARN state not saved: {type(e).__name__}", flush=True)
    if state["error"]:
        print(f"read: {state['error']}", flush=True)
    else:
        print(f"read: {len(state['nodes'])} Knoten, {len(state['instances'])} Instanzen",
              flush=True)


def _poller():
    import time
    while True:
        try:
            poll_now()
        except Exception as e:  # der Leser darf nie sterben
            print(f"WARN read failed: {type(e).__name__}: {e}", flush=True)
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
    <span><b>OAAP FLEETVIEW</b><small>Die Flotte auf einen Blick — aus dem Zwilling der Zentrale</small></span>
  </a>
  <div class="userbox"><span class="who">{esc(user)}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">
  <svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">
    <polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>
  OAAP FleetView {VERSION} — zeigt die Flotte; gehandelt wird im Portal des Knotens
</footer>
</html>"""


STATE_BADGE = {"ok": "ok", "warn": "warn", "error": "err", "unknown": "off",
               "silent": "err", "removed": "off", "standby": "off"}


def badge(state, extra=""):
    label = fleet.STATE_LABELS.get(state, state)
    if extra:
        label += f" · {extra}"
    return f'<span class="badge {STATE_BADGE.get(state, "off")}">{esc(label)}</span>'


def fmt_age(age):
    if age is None:
        return "noch nie"
    if age < 90:
        return f"vor {age} s"
    if age < 5400:
        return f"vor {age // 60} Min."
    return f"vor {age // 3600} Std."


def fmt_time(iso):
    """2026-11-15T21:02:20Z -> 15.11. 21:02 UTC; anderes unverändert."""
    t = str(iso or "")
    if len(t) >= 16 and t[4] == "-" and t[10] == "T":
        return f"{t[8:10]}.{t[5:7]}. {t[11:16]} UTC"
    return t


def name_cell(r, href):
    """Der Name im Verbund (Alias), der feste Knotenname klein daneben,
    solange beide abweichen (RFC-0082)."""
    out = f'<a href="{esc(href)}">{esc(r["label"])}</a>'
    if r["renamed"]:
        out += f' <span class="muted">({esc(r["name"])})</span>'
    return out


def attention_card(items):
    if not items:
        return ""
    lis = "".join(
        f'<li><strong>{esc(i["node"])}</strong>: {esc(i["label"])}'
        + (f' — {esc(i["detail"])}' if i["detail"] else "") + "</li>"
        for i in items)
    return (f'<div class="card attention"><h2>Braucht einen Menschen '
            f'({len(items)})</h2><ul class="attn">{lis}</ul></div>')


def _snapshot():
    with _lock:
        state = dict(_state)
    rows = fleet.node_rows(state.get("nodes") or [], state.get("instances") or [])
    return state, rows


def read_card(state):
    """Wann zuletzt gelesen, und was beim letzten Lesen schiefging."""
    out = []
    if state.get("error"):
        out.append('<div class="card attention"><h2>Zwilling nicht gelesen</h2>'
                   f'<p>{esc(state["error"])}</p>'
                   + ('<p class="hint">Unten steht der zuletzt gelesene Stand '
                      f'({esc(fmt_age(fleet.age_seconds(state.get("read"))))}).</p>'
                      if state.get("read") else "")
                   + "</div>")
    if state.get("truncated"):
        out.append('<p class="hint">Der Zwilling hat die Liste gekürzt (mehr als '
                   "500 Objekte eines Typs) — nicht alles ist zu sehen.</p>")
    return "".join(out)


def overview(user, roles):
    state, rows = _snapshot()
    attn = fleet.fleet_attention(rows)
    note = fleet.version_note(rows)
    hubs = fleet.pairing_targets(rows)
    pair_btns = "".join(
        f'<a class="btn" href="{esc(h["url"])}" rel="noopener">Knoten koppeln'
        + (f' ({esc(h["name"])})' if len(hubs) > 1 else "") + "</a> "
        for h in hubs)
    body = ['<div class="pagehead"><h1>Flotte</h1><div>' + pair_btns +
            '<form method="post" action="poll" style="display:inline">'
            "<button>Jetzt aktualisieren</button></form></div></div>"]
    body.append(read_card(state))
    body.append(attention_card(attn))
    if note:
        body.append(f'<p class="hint">{esc(note)} — während eines '
                    "Flotten-Updates normal.</p>")
    dups = fleet.duplicate_instances(rows)
    if dups:
        lis = "".join(
            f'<li><strong>{esc(d["instance"])}</strong> auf: '
            f'{esc(", ".join(d["nodes"]))}</li>' for d in dups)
        body.append('<div class="card"><h2>Mehrfach vorhandene '
                    f'Instanznamen ({len(dups)})</h2><ul class="attn">{lis}</ul>'
                    '<p class="hint">Kann gewollt sein (z. B. Monitoring je '
                    "Knoten) — nach einem Umzug ist es oft ein Rest, der "
                    "aufgeräumt gehört.</p></div>")
    if not rows:
        body.append('<div class="card"><h2>Noch keine Knoten</h2>'
                    "<p>Im Zwilling dieses Mandanten steht noch kein Knoten. "
                    "Die Flotte schreibt dorthin, sobald auf der Zentrale "
                    "<code>sudo oaap fleet hub twin &lt;mandant&gt;</code> "
                    "gesetzt ist und Knoten gekoppelt sind.</p></div>")
    else:
        trs = []
        for r in rows:
            via = f'über {esc(r["via"])}' if r["via"] else "direkt"
            portal = (f'<a href="{esc(r["url"])}/" rel="noopener">Portal öffnen</a>'
                      if r["url"] else "")
            trs.append(
                '<tr class="rowlink">'
                f'<td>{name_cell(r, "node/" + r["name"])}</td>'
                f'<td>{badge(r["state"])}</td>'
                f'<td>{esc(r["version"])}</td>'
                f'<td>{r["inst_ok"]}/{r["inst_total"]}'
                f'{" · " + str(r["inst_standby"]) + " Standby" if r["inst_standby"] else ""}</td>'
                f'<td class="muted">{via}</td>'
                f"<td>{portal}</td>"
                "</tr>")
        body.append(
            '<div class="card"><h2>Knoten</h2><table>'
            "<tr><th>Knoten</th><th>Zustand</th><th>Plattform</th>"
            "<th>Instanzen gesund</th><th>Meldet</th><th></th></tr>"
            + "".join(trs) + "</table>"
            f'<p class="hint">Aus dem Zwilling der Zentrale, gelesen '
            f'{esc(fmt_age(fleet.age_seconds(state.get("read"))))} (alle '
            f"{POLL_SECONDS} s). Jeder gekoppelte Knoten erscheint hier von "
            "selbst; der Name ist der, den der Knoten unter „Gesundheit“ trägt."
            "</p></div>")
    return page("Flotte", "".join(body), user, roles)


def node_page(name, user, roles):
    state, rows = _snapshot()
    r = fleet.find_row(rows, name)
    if r is None:
        return None
    attn = fleet.fleet_attention([r])
    btns = ""
    if r["url"]:
        btns = (f'<a class="btn" href="{esc(fleet.pairing_link(r))}" rel="noopener">Kopplung</a> '
                f'<a class="btn" href="{esc(r["url"])}/" rel="noopener">Portal öffnen</a>')
    title = r["label"] + (f" ({r['name']})" if r["renamed"] else "")
    body = ['<a class="back" href="../">&larr; Zur Flotte</a>',
            f'<div class="pagehead"><h1>{esc(title)}</h1><div>{btns}</div></div>']
    body.append(read_card(state))
    body.append(attention_card(attn))
    live = badge(r["liveness"] if r["liveness"] != "up" else "ok")
    if r["since"]:
        live += f' <span class="muted">seit {esc(fmt_time(r["since"]))}</span>'
    facts = [
        ("Zustand", badge(r["state"])),
        ("Lebenszeichen", live),
        ("Name im Verbund", esc(r["label"])),
        ("Knotenname", esc(r["name"])),
        ("Adresse", (f'<a href="{esc(r["url"])}/">{esc(r["address"])}</a>'
                     if r["url"] else "—")),
        ("Meldet", f'über den Eingang {esc(r["via"])}' if r["via"] else "direkt an die Zentrale"),
        ("Plattformversion", esc(r["version"])),
        ("Betriebssystem", esc(r["os"]) if r["os"] else "—"),
        ("Profile", esc(", ".join(r["profiles"])) if r["profiles"] else "(keine)"),
    ]
    if r["place"]:
        facts.append(("Ort", esc(r["place"])))
    if r["tags"]:
        facts.append(("Tags", esc(", ".join(r["tags"]))))
    if r["core"]:
        facts.append(("Kerndienste", esc(", ".join(r["core"]))))
    facts.append(("Öffentliche IP", esc(r["public_ip"]) if r["public_ip"] else "—"))
    if r["names_total"]:
        facts.append(("Veröffentlichte Namen",
                      f'{r["names_total"]}'
                      + (f' — <span class="err">{esc(r["names_problems"])}</span>'
                         if r["names_problems"] else " — alle in Ordnung")))
    if r["reboot_required"] is not None:
        facts.append(("Neustart nötig", "ja" if r["reboot_required"] else "nein"))
    if r["reboot_plan"]:
        facts.append(("Neustart geplant", esc(fmt_time(r["reboot_plan"]))))
    if r["advisories_open"] is not None:
        facts.append(("Offene Sicherheitsmeldungen", esc(str(r["advisories_open"]))))
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    body.append(f'<div class="card"><h2>Überblick</h2><dl class="facts">{dl}</dl></div>')

    if r["instances"]:
        trs = []
        for i in r["instances"]:
            addr = i["address"]
            addr_html = (f'<a href="https://{esc(addr)}/">{esc(addr)}</a>'
                         if addr else '<span class="muted">—</span>')
            auto = i["auto_address"]
            auto_html = (f'<a href="https://{esc(auto)}/">{esc(auto)}</a> '
                         + badge(i["auto_state"] or "unknown")
                         if auto else '<span class="muted">—</span>')
            test = i["channel"] == "test"
            trs.append(
                "<tr>"
                f'<td>{esc(i["instance"])}</td>'
                f'<td>{esc(i["app"])}</td>'
                f'<td>{esc(i["version"])}</td>'
                f'<td><span class="badge {"test" if test else ""}">'
                f'{"Test" if test else "Produktiv"}</span></td>'
                f'<td>{badge(i["state"])}</td>'
                f'<td class="muted">{esc(i["origin"])}</td>'
                f"<td>{addr_html}</td>"
                f"<td>{auto_html}</td>"
                "</tr>")
        body.append('<div class="card" style="overflow-x:auto">'
                    "<h2>Instanzen</h2><table>"
                    "<tr><th>Instanz</th><th>App</th><th>Version</th><th>Kanal</th>"
                    "<th>Zustand</th><th>Herkunft</th><th>Eigene Adresse</th>"
                    "<th>Automatischer Name</th></tr>"
                    + "".join(trs) + "</table>"
                    '<p class="hint">Das Urteil beim <b>automatischen Namen</b> sagt, '
                    "ob die Instanz unter diesem Namen auf diesem Knoten erreichbar "
                    "ist (Route vorhanden, App antwortet) — nicht, ob TLS steht.</p></div>")
    else:
        body.append('<div class="card"><h2>Instanzen</h2>'
                    "<p>Dieser Knoten meldet keine App-Instanzen.</p></div>")
    if r["removed_instances"]:
        names = ", ".join(i["instance"] for i in r["removed_instances"])
        body.append(f'<p class="hint">Früher auf diesem Knoten, inzwischen entfernt: '
                    f"{esc(names)}. Ihre Geschichte bleibt im Zwilling.</p>")
    return page(title, "".join(body), user, roles)


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
        # `support` seit RFC-0039 — die Rolle der Dienstleister, die
        # die Landschaft lesend betreuen; vorher hieß sie hier
        # `partner`, das inzwischen nur noch die externe Firma meint.
        return bool(roles & {"admin", "support"})

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
                "<strong>support</strong>.</p></div>", user, ",".join(sorted(roles)) or "?"))
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
    print(f"OAAP FleetView {VERSION} auf :{PORT} — liest den Zwilling alle "
          f"{POLL_SECONDS} s" + ("" if TWIN_URL and PLATFORM_KEY else " (kein Zugang)"),
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
