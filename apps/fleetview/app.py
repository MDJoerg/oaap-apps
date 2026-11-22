"""Flotte (fleetview) — die Flotten-Übersicht (RFC-0021, RFC-0085 §10).

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
from urllib.parse import parse_qs, unquote

import appframe as af
import fleet
import mcpdoor

VERSION = "1.2.1"
APP_NAME = "Flotte"
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
# Der Rahmen kommt aus appframe (Kit und Rahmenskript der Plattform);
# hier steht nur, was der Flotte eigen ist.

PAGE_STYLE = (".fv-attn{border-left:4px solid var(--oaap-color-warning)}"
              ".fv-attn ul{margin:.2rem 0 0;padding-left:1.2rem}.fv-attn li{margin:.25rem 0}"
              ".fv-actions{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center}"
              ".fv-actions form{display:inline}")


def tabs_for(root):
    return [(root or "./", "Übersicht"), (root + "instanzen", "Instanzen")]


def page(title, body, root="", active=None, obj=None, obj_title=None):
    return af.page(title, body, app=APP_NAME, version=VERSION, product="fleetview",
                   tabs=tabs_for(root), active=active, obj=obj, obj_title=obj_title,
                   style=PAGE_STYLE)


STATE_BADGE = {"ok": "ok", "warn": "warn", "error": "err", "unknown": "",
               "silent": "err", "removed": "", "standby": ""}


def badge(state, extra=""):
    label = fleet.STATE_LABELS.get(state, state)
    if extra:
        label += f" · {extra}"
    return af.badge(label, STATE_BADGE.get(state, ""))


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
    solange beide abweichen."""
    out = f'<a href="{esc(href)}">{esc(r["label"])}</a>'
    if r["renamed"]:
        out += f' <span class="oaap-muted">({esc(r["name"])})</span>'
    return out


def attention_card(items):
    if not items:
        return ""
    lis = "".join(
        f'<li><strong>{esc(i["node"])}</strong>: {esc(i["label"])}'
        + (f' — {esc(i["detail"])}' if i["detail"] else "") + "</li>"
        for i in items)
    return af.card(f"Braucht einen Menschen ({len(items)})", f"<ul>{lis}</ul>", "fv-attn")


def _snapshot():
    with _lock:
        state = dict(_state)
    rows = fleet.node_rows(state.get("nodes") or [], state.get("instances") or [])
    return state, rows


def read_card(state):
    """Wann zuletzt gelesen, und was beim letzten Lesen schiefging."""
    out = []
    if state.get("error"):
        out.append(af.card("Zwilling nicht gelesen",
                           f'<p>{esc(state["error"])}</p>'
                           + ('<p class="oaap-muted">Unten steht der zuletzt gelesene Stand '
                              f'({esc(fmt_age(fleet.age_seconds(state.get("read"))))}).</p>'
                              if state.get("read") else ""), "fv-attn"))
    if state.get("truncated"):
        out.append('<p class="oaap-muted">Der Zwilling hat die Liste gekürzt (mehr als '
                   "500 Objekte eines Typs) — nicht alles ist zu sehen.</p>")
    return "".join(out)


STATE_CHOICES = [(k, fleet.STATE_LABELS[k]) for k in
                 ("ok", "warn", "error", "silent", "unknown", "removed")]


def overview(params):
    state, rows = _snapshot()
    attn = fleet.fleet_attention(rows)
    note = fleet.version_note(rows)
    hubs = fleet.pairing_targets(rows)
    pair_btns = "".join(
        f'<a class="oaap-btn" href="{esc(h["url"])}" rel="noopener">Knoten koppeln'
        + (f' ({esc(h["name"])})' if len(hubs) > 1 else "") + "</a>"
        for h in hubs)
    actions = (f'<div class="fv-actions">{pair_btns}'
               '<form method="post" action="poll"><button class="primary">Jetzt aktualisieren</button></form></div>')
    body = [af.pagehead("Flotte", actions)]
    body.append(read_card(state))
    body.append(attention_card(attn))
    if note:
        body.append(f'<p class="oaap-muted">{esc(note)} — während eines '
                    "Flotten-Updates normal.</p>")
    dups = fleet.duplicate_instances(rows)
    if dups:
        lis = "".join(
            f'<li><strong>{esc(d["instance"])}</strong> auf: '
            f'{esc(", ".join(d["nodes"]))}</li>' for d in dups)
        body.append(af.card(f"Mehrfach vorhandene Instanznamen ({len(dups)})",
                            f"<ul>{lis}</ul>"
                            '<p class="oaap-muted">Kann gewollt sein (z. B. Monitoring je '
                            "Knoten) — nach einem Umzug ist es oft ein Rest, der "
                            "aufgeräumt gehört.</p>"))
    if not rows:
        body.append(af.card("Noch keine Knoten",
                            "<p>Im Zwilling dieses Mandanten steht noch kein Knoten. "
                            "Die Flotte schreibt dorthin, sobald auf der Zentrale "
                            "<code>sudo oaap fleet hub twin &lt;mandant&gt;</code> "
                            "gesetzt ist und Knoten gekoppelt sind.</p>"))
        return page("", "".join(body), active="./")
    q = af.Query(params, filters=("state",))
    shown = [r for r in rows if not q.filters["state"] or r["state"] == q.filters["state"]]

    def row_html(r):
        via = f'über {esc(r["via"])}' if r["via"] else "direkt"
        standby = f' · {r["inst_standby"]} Standby' if r["inst_standby"] else ""
        return ("<tr>"
                f'<td>{name_cell(r, "node/" + r["name"])}</td>'
                f'<td>{badge(r["state"])}</td>'
                f'<td>{esc(r["version"])}</td>'
                f'<td class="oaap-num">{r["inst_ok"]}/{r["inst_total"]}{standby}</td>'
                f'<td class="oaap-muted oaap-minor">{via}</td>'
                "</tr>")
    columns = [("Knoten", "name", lambda r: r["label"] + " " + r["name"], ""),
               ("Zustand", "state", lambda r: fleet.STATE_LABELS.get(r["state"], r["state"]), ""),
               ("Plattform", "version", lambda r: r["version"], ""),
               ("Instanzen gesund", "inst", lambda r: r["inst_ok"], "oaap-num"),
               ("Meldet", "via", lambda r: r["via"] or "direkt", "oaap-minor")]
    report = af.list_report(q, shown, columns, row_html, noun="Knoten",
                            empty_text="Noch keine Knoten.",
                            choices=[("state", "Zustand", STATE_CHOICES)],
                            placeholder="Name, Version …")
    body.append(af.card("Knoten", report
                        + '<p class="oaap-muted">Aus dem Zwilling der Zentrale, gelesen '
                        f'{esc(fmt_age(fleet.age_seconds(state.get("read"))))} (alle '
                        f"{POLL_SECONDS} s). Jeder gekoppelte Knoten erscheint hier von "
                        "selbst; der Name ist der, den der Knoten unter „Gesundheit“ trägt. "
                        "Gehandelt wird im Portal des Knotens.</p>"))
    return page("", "".join(body), active="./")


def all_instances(rows):
    """Jede lebende Instanz der Flotte mit ihrem Knoten."""
    out = []
    for r in rows:
        if r["liveness"] == "removed":
            continue
        for i in r["instances"]:
            out.append({**i, "node": r["name"], "node_label": r["label"]})
    return out


INST_STATE_CHOICES = [(k, fleet.STATE_LABELS[k]) for k in
                      ("ok", "warn", "error", "standby", "unknown")]


def instance_columns(with_node):
    cols = [("Instanz", "instance", lambda i: i["instance"], ""),
            ("App", "app", lambda i: i["app"], ""),
            ("Version", "version", lambda i: i["version"], "oaap-minor"),
            ("Kanal", "channel", lambda i: "Test" if i["channel"] == "test" else "Produktiv", ""),
            ("Zustand", "state", lambda i: fleet.STATE_LABELS.get(i["state"], i["state"]), "")]
    if with_node:
        cols.insert(1, ("Knoten", "node", lambda i: i["node_label"], ""))
    else:
        cols += [("Eigene Adresse", "", None, "oaap-minor"),
                 ("Automatischer Name", "", None, "oaap-minor")]
    return cols


def instance_row_html(i, root, with_node):
    test = i["channel"] == "test"
    cells = [f'<td>{esc(i["instance"])}</td>']
    if with_node:
        cells.append(f'<td><a href="{esc(root + "node/" + i["node"])}">{esc(i["node_label"])}</a></td>')
    cells += [f'<td>{esc(i["app"])}</td>',
              f'<td class="oaap-minor">{esc(i["version"])}</td>',
              f'<td>{af.badge("Test", "test") if test else af.badge("Produktiv")}</td>',
              f'<td>{badge(i["state"])}</td>']
    if not with_node:
        addr, auto = i["address"], i["auto_address"]
        cells.append('<td class="oaap-minor">' + (f'<a href="https://{esc(addr)}/">{esc(addr)}</a>'
                                                   if addr else '<span class="oaap-muted">—</span>') + "</td>")
        cells.append('<td class="oaap-minor">' + (f'<a href="https://{esc(auto)}/">{esc(auto)}</a> '
                                                   + badge(i["auto_state"] or "unknown")
                                                   if auto else '<span class="oaap-muted">—</span>') + "</td>")
    return "<tr>" + "".join(cells) + "</tr>"


def instances_page(params):
    state, rows = _snapshot()
    q = af.Query(params, filters=("state", "channel"), default_sort="instance")
    insts = [i for i in all_instances(rows)
             if (not q.filters["state"] or i["state"] == q.filters["state"])
             and (not q.filters["channel"] or (i["channel"] == "test") == (q.filters["channel"] == "test"))]
    report = af.list_report(
        q, insts, instance_columns(True), lambda i: instance_row_html(i, "", True),
        noun="Instanzen",
        empty_text="Noch keine Instanzen. Sobald gekoppelte Knoten Apps melden, stehen sie hier.",
        choices=[("state", "Zustand", INST_STATE_CHOICES),
                 ("channel", "Kanal", [("production", "Produktiv"), ("test", "Test")])],
        placeholder="Instanz, App, Knoten …")
    body = af.pagehead("Instanzen") + read_card(state) + af.card("", report)
    return page("Instanzen", body, active="instanzen")


def node_page(name, params):
    state, rows = _snapshot()
    r = fleet.find_row(rows, name)
    if r is None:
        return None
    root = "../"
    attn = fleet.fleet_attention([r])
    btns = ""
    if r["url"]:
        btns = (f'<div class="fv-actions"><a class="oaap-btn" href="{esc(fleet.pairing_link(r))}" rel="noopener">Kopplung</a>'
                f'<a class="oaap-btn primary" href="{esc(r["url"])}/" rel="noopener">Portal öffnen</a></div>')
    title = r["label"] + (f" ({r['name']})" if r["renamed"] else "")
    body = [af.back(root, "Zur Flotte"), af.pagehead(title, btns)]
    body.append(read_card(state))
    body.append(attention_card(attn))
    live = badge(r["liveness"] if r["liveness"] != "up" else "ok")
    if r["since"]:
        live += f' <span class="oaap-muted">seit {esc(fmt_time(r["since"]))}</span>'
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
                      + (" — " + af.badge(r["names_problems"], "err")
                         if r["names_problems"] else " — alle in Ordnung")))
    if r["reboot_required"] is not None:
        facts.append(("Neustart nötig", "ja" if r["reboot_required"] else "nein"))
    if r["reboot_plan"]:
        facts.append(("Neustart geplant", esc(fmt_time(r["reboot_plan"]))))
    if r["advisories_open"] is not None:
        facts.append(("Offene Sicherheitsmeldungen", esc(str(r["advisories_open"]))))
    body.append(af.card("Überblick", af.facts(facts)))

    if r["instances"]:
        q = af.Query(params, default_sort="instance")
        report = af.list_report(q, r["instances"], instance_columns(False),
                                lambda i: instance_row_html(i, root, False),
                                noun="Instanzen", empty_text="")
        body.append(af.card("Instanzen", report
                            + '<p class="oaap-muted">Das Urteil beim <b>automatischen Namen</b> sagt, '
                            "ob die Instanz unter diesem Namen auf diesem Knoten erreichbar "
                            "ist (Route vorhanden, App antwortet) — nicht, ob TLS steht.</p>"))
    else:
        body.append(af.card("Instanzen", af.empty("Dieser Knoten meldet keine App-Instanzen.")))
    if r["removed_instances"]:
        names = ", ".join(i["instance"] for i in r["removed_instances"])
        body.append(f'<p class="oaap-muted">Früher auf diesem Knoten, inzwischen entfernt: '
                    f"{esc(names)}. Ihre Geschichte bleibt im Zwilling.</p>")
    return page(title, "".join(body), root=root, active=None,
                obj=f"FleetNode:{r['name']}", obj_title=r["label"])


# ---------------------------------------------------------------- MCP
# Nur lesen, mit denselben Rollen wie die Seite (admin, support). Das
# Ergebnis ist kleiner als die Seite: keine öffentliche IP.

DOOR = mcpdoor.Door("oaap-fleetview", VERSION,
                    "Die Flotte lesen: Knoten, ihr Zustand und ihre App-Instanzen, aus dem "
                    "Zwilling der Zentrale. Nur lesend; gehandelt wird im Portal des Knotens.")


def _allowed_roles(roles):
    return bool(set(roles or []) & {"admin", "support"})


def _check(who):
    if not _allowed_roles(who.get("roles")):
        raise mcpdoor.Refused("Die Flotte sehen nur die Rollen admin und support.")


def _node_brief(r):
    return {"name": r["name"], "label": r["label"],
            "state": r["state"], "state_text": fleet.STATE_LABELS.get(r["state"], r["state"]),
            "platform_version": r["version"], "via": r["via"] or "",
            "instances_ok": r["inst_ok"], "instances_total": r["inst_total"],
            "attention": r["attention_count"]}


def _inst_brief(i):
    return {"instance": i["instance"], "app": i["app"], "version": i["version"],
            "channel": i["channel"] or "production",
            "state": i["state"], "state_text": fleet.STATE_LABELS.get(i["state"], i["state"]),
            "node": i.get("node_label") or ""}


@DOOR.tool("fleetview_nodes", "Knoten der Flotte",
           "Listet die Knoten der Flotte mit Zustand, Plattformversion und Zahl der gesunden "
           "Instanzen, dazu was einen Menschen braucht. Optional gefiltert nach Suchwort.",
           {"q": {"type": "string", "description": "Suchwort im Knotennamen oder in der Version"}})
def _mcp_nodes(args, who):
    _check(who)
    state, rows = _snapshot()
    q = af.Query({"q": str(args.get("q") or "")})
    hits = [r for r in rows if q.matches(r["name"], r["label"], r["version"])]
    return {"read": state.get("read") or "", "error": state.get("error") or "",
            "nodes": [_node_brief(r) for r in hits],
            "attention": fleet.fleet_attention(rows)}


@DOOR.tool("fleetview_node_read", "Einen Knoten lesen",
           "Liest einen Knoten der Flotte (fester Name oder Name im Verbund) mit Zustand, "
           "Lebenszeichen, Profilen, Hinweisen und seinen App-Instanzen.",
           {"name": {"type": "string", "description": "Knotenname oder Name im Verbund"}},
           required=("name",))
def _mcp_node(args, who):
    _check(who)
    _state, rows = _snapshot()
    r = fleet.find_row(rows, str(args.get("name") or "").strip())
    if r is None:
        raise mcpdoor.Refused("Diesen Knoten kennt die Flotte nicht.")
    return {**_node_brief(r), "liveness": r["liveness"], "since": r["since"],
            "os": r["os"], "profiles": r["profiles"], "address": r["address"],
            "attention": fleet.fleet_attention([r]),
            "reboot_required": r["reboot_required"], "advisories_open": r["advisories_open"],
            "instances": [_inst_brief(i) for i in r["instances"]]}


@DOOR.tool("fleetview_instances", "Instanzen der Flotte",
           "Listet die App-Instanzen aller Knoten mit App, Version, Kanal und Zustand. "
           "Optional gefiltert nach Suchwort (Instanz, App, Knoten) und Zustand.",
           {"q": {"type": "string", "description": "Suchwort"},
            "state": {"type": "string", "description": "ok, warn, error, standby oder unknown"}})
def _mcp_instances(args, who):
    _check(who)
    _state, rows = _snapshot()
    q = af.Query({"q": str(args.get("q") or "")})
    want = str(args.get("state") or "").strip()
    hits = [i for i in all_instances(rows)
            if q.matches(i["instance"], i["app"], i["node_label"], i["node"])
            and (not want or i["state"] == want)]
    return {"instances": [_inst_brief(i) for i in hits[:200]], "total": len(hits)}


DOOR.prompt("fleetview_check", "Wie steht die Flotte?",
            "Fasst den Zustand der Flotte zusammen und nennt, was einen Menschen braucht.",
            "Lies mit fleetview_nodes den Zustand der Flotte. Nenne zuerst, was einen Menschen "
            "braucht (stumme oder gestörte Knoten, Hinweise), dann abweichende "
            "Plattformversionen, in wenigen Sätzen.")
DOOR.prompt("fleetview_where", "Wo läuft eine App?",
            "Findet die Instanzen einer App in der Flotte.",
            "Finde mit fleetview_instances alle Instanzen der App „{app}“ und sag, auf welchen "
            "Knoten sie in welcher Version und in welchem Zustand laufen.",
            arguments=("app",))


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
        path, _, query = self.path.partition("?")
        if path == "/healthz":
            return self._send(200, '{"status":"ok"}',
                              "application/json; charset=utf-8")
        if path == "/mcp":
            return DOOR.serve(self)
        _user, roles = self._identity()
        if not self._allowed(roles):
            return self._send(403, page(
                "Kein Zugriff",
                af.card("Kein Zugriff", "<p>Die Flotten-Übersicht erfordert die Rolle "
                        "<strong>admin</strong> oder <strong>support</strong>.</p>")))
        params = parse_qs(query)
        if path == "/":
            return self._send(200, overview(params))
        if path == "/instanzen":
            return self._send(200, instances_page(params))
        if path.startswith("/node/"):
            name = unquote(path[len("/node/"):]).strip("/")
            out = node_page(name, params)
            if out is not None:
                return self._send(200, out)
        return self._send(404, page(
            "Nicht gefunden", af.card("Nicht gefunden", '<p><a href="/">Zur Flotte</a></p>')))

    do_HEAD = do_GET

    def do_POST(self):
        if self.path.split("?", 1)[0] == "/mcp":
            return DOOR.serve(self)
        _user, roles = self._identity()
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
    print(f"Flotte (fleetview) {VERSION} auf :{PORT} — liest den Zwilling alle "
          f"{POLL_SECONDS} s" + ("" if TWIN_URL and PLATFORM_KEY else " (kein Zugang)"),
          flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
