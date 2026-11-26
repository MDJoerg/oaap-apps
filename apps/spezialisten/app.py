"""Spezialisten (RFC-0105, Rahmen RFC-0118): KI-Agenten, die Vorgänge im Hintergrund bearbeiten.

Die Seite pflegt die Spezialisten (tenant_admin, admin); ein Faden fragt alle 30 s jeden
aktiven Spezialisten bei `vorgaenge`, was an ihm dran ist, und führt die Läufe aus
(runner.py). Nur Standardbibliothek.
"""
import html
import json
import os
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import appframe as af
import mcpdoor
import runner
from store import Refused, Store, import_assistant

VERSION = "0.8.3"
PORT = int(os.environ.get("PORT", "8000"))
DATA = os.environ.get("SPEZ_DATA_DIR", "/data")


def env_int(k, d, lo, hi):
    try:
        return min(max(int(os.environ.get(k) or d), lo), hi)
    except ValueError:
        return d


POLL = env_int("SPEZ_POLL_SECONDS", 30, 10, 3600)
# D8: Grenzen des Mandanten; je Spezialist nur niedriger
LIMITS = {"max_steps": env_int("SPEZ_MAX_STEPS", 20, 1, 100),
          "max_minutes": env_int("SPEZ_MAX_MINUTES", 10, 1, 120),
          "max_runs": env_int("SPEZ_MAX_RUNS", 10, 1, 100)}
STORE = None
STATUS = {}             # je Spezialist: letzter Abruf, Fehler (nur im Speicher)
RUNNING = set()
REASON = {"waiting_consult": "wartet auf Rückmeldung", "waiting_task": "wartet auf Auftrag",
          "closed": "abgeschlossen (Leitung)", "reviewed": "Prüfung abgegeben",
          "task_done": "Auftrag erledigt", "answered": "Rückfrage beantwortet",
          "not_asked": "Rückfrage nicht mehr offen", "needs_approval": "wartet auf Freigabe", "done": "fertig, zurückgegeben", "limit": "Grenze, zurückgegeben", "stopped": "angehalten",
          "error": "Fehler, zurückgegeben", "not_mine": "nicht mehr verantwortlich",
          "stopped_before": "schon angehalten", "runs": "Läufe je Vorgang erreicht, zurückgegeben"}
esc = html.escape


# ------------------------------------------------------------------ der Faden
def poll_once(store=None, make=None, log=print):
    """Ein Durchgang über alle aktiven Spezialisten. `make(spec)` liefert (Cases, Tools, Model)
    -- in Tests nachgebaut."""
    store = store or STORE
    make = make or (lambda s: (runner.Cases(s["vorgaenge_url"], s["agent_key"]),
                               runner.Tools(s["mcp_url"], s["mcp_key"]),
                               runner.Model(s["ai_url"], s["ai_key"], s["model"])))
    every = store.all(with_secrets=True)
    # 0.7.0 (RFC-0134 §4): das Team eines Leiters über die Kennung seiner Agenten (Name hier -> uid)
    team_map = {x["name"]: x["uid"] for x in every if x["uid"]}
    for s in every:
        if not s["active"]:
            continue
        s["team_map"] = team_map
        st = STATUS.setdefault(s["id"], {})
        try:
            cases, tools, model = make(s)
            turn = cases.turn()
            if turn.get("uid") and turn["uid"] != s["uid"]:
                store.set_uid(s["id"], turn["uid"])
                s["uid"] = turn["uid"]
            st.update(at=runner_now(), error="", waiting=len(turn.get("cases") or []))
            for item in turn.get("cases") or []:
                if not runner.due(item, s["uid"]) or (s["id"], item["number"]) in RUNNING:
                    continue
                # Spezialist kann mittendrin ausgeschaltet worden sein
                fresh = store.get(s["id"])
                if not fresh or not fresh["active"]:
                    break
                if item.get("task"):
                    # RFC-0134 §5: ein Auftrag des Leiters -- ein Lauf, ein Ergebnis
                    run = runner.Run(s, cases, tools, model, log=log)
                    RUNNING.add((s["id"], item["number"]))
                    store.run_start(run.id, s["id"], item["number"])
                    try:
                        why = run.task(item["number"], item["task"])
                    finally:
                        RUNNING.discard((s["id"], item["number"]))
                    store.run_end(run.id, why, run.steps)
                    continue
                if item.get("review"):
                    # RFC-0134 §8: ein markierter Vorgang -- ein Lauf, ein Urteil
                    run = runner.Run(s, cases, tools, model, log=log)
                    RUNNING.add((s["id"], item["number"]))
                    store.run_start(run.id, s["id"], item["number"])
                    try:
                        why = run.review(item["number"])
                    finally:
                        RUNNING.discard((s["id"], item["number"]))
                    store.run_end(run.id, why, run.steps)
                    continue
                if item.get("consult"):
                    # §6: eine Rückfrage an diesen Spezialisten -- ein Lauf, eine Antwort
                    run = runner.Run(s, cases, tools, model, log=log)
                    RUNNING.add((s["id"], item["number"]))
                    store.run_start(run.id, s["id"], item["number"])
                    try:
                        why = run.consult(item["number"], item["consult"])
                    finally:
                        RUNNING.discard((s["id"], item["number"]))
                    store.run_end(run.id, why, run.steps)
                    continue
                if item.get("runs", 0) >= s["max_runs"]:
                    cases.entry(item["number"], "end", f"Grenze: {s['max_runs']} Läufe an diesem Vorgang. "
                                "Zurück an die Zuständigen.", reason="limit", assistant=s["assistant"])
                    cases.give_back(item["number"], f"Grenze erreicht ({s['max_runs']} Läufe).", "")
                    continue
                run = runner.Run(s, cases, tools, model, log=log)
                RUNNING.add((s["id"], item["number"]))
                store.run_start(run.id, s["id"], item["number"])
                try:
                    why = run.go(item["number"])
                finally:
                    RUNNING.discard((s["id"], item["number"]))
                store.run_end(run.id, why, run.steps)
        except runner.Unreachable as e:
            st.update(at=runner_now(), error=str(e))
            log(f"{s['name']}: {e}")
        except Exception as e:      # ein Spezialist darf die anderen nicht anhalten
            st.update(at=runner_now(), error=f"interner Fehler: {e.__class__.__name__}")
            log(traceback.format_exc())


def runner_now():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def loop():
    while True:
        try:
            poll_once()
        except Exception:
            print(traceback.format_exc(), flush=True)
        time.sleep(POLL)


def check(s):
    """„Prüfen“: alle drei Gegenüber einmal, mit dem nächsten Schritt in Worten."""
    out = []
    try:
        t = runner.Cases(s["vorgaenge_url"], s["agent_key"]).turn()
        STORE.set_uid(s["id"], t.get("uid", ""))
        out.append((True, f"Vorgänge: angemeldet als Agent <code>{esc(t.get('uid', ''))}</code>, "
                          f"{len(t.get('cases') or [])} Vorgang/Vorgänge an der Reihe. Jetzt kann der "
                          f"Mandanten-Administrator ihn in Vorgänge unter „Bereiche“ als Erstbearbeitung wählen."))
    except runner.Unreachable as e:
        out.append((False, "Vorgänge: " + esc(str(e))))
    try:
        tools = runner.Tools(s["mcp_url"], s["mcp_key"]).open()
        out.append((True, f"Sicht im MCP-Gateway: {len(tools)} Werkzeuge ("
                          + esc(", ".join(t["name"] for t in tools[:12])) + ")."))
    except runner.Unreachable as e:
        out.append((False, "MCP-Gateway: " + esc(str(e))))
    try:
        st, d, _ = runner.http("GET", s["ai_url"].rstrip("/") + "/models", s["ai_key"], timeout=20)
        ids = [m.get("id") for m in (d.get("data") or [])] if isinstance(d, dict) else []
        if st == 200 and s["model"] in ids:
            out.append((True, f"KI-Gateway: Modell <code>{esc(s['model'])}</code> erreichbar."))
        elif st == 200:
            out.append((False, f"KI-Gateway: der Schlüssel erreicht <code>{esc(s['model'])}</code> nicht "
                               f"(erreichbar: {esc(', '.join(map(str, ids[:8])) or 'keins')})."))
        else:
            out.append((False, f"KI-Gateway: Antwort {st}."))
    except runner.Unreachable as e:
        out.append((False, "KI-Gateway: " + esc(str(e))))
    return out


# ------------------------------------------------------------------ Seiten
APP_NAME = "Spezialisten"
# Nur, was dieser App eigen ist; Farben aus dem Kit der Plattform (RFC-0118).
STYLE = """.row{display:flex;gap:.8rem;flex-wrap:wrap}.row>div{flex:1;min-width:12rem}
.spec label{display:block;font-size:.85rem;color:var(--oaap-color-text-muted);margin:.6rem 0 .2rem}
.spec input:not([type=checkbox]),.spec textarea{width:100%;box-sizing:border-box}.spec textarea{min-height:7rem}
.spec label.check{display:flex;gap:.4rem;align-items:center;color:var(--oaap-color-text)}
.spec h2{font-size:1.05rem;margin:1.2rem 0 .2rem}.ok{color:var(--oaap-color-success)}.err{color:var(--oaap-color-danger)}
form.inline{display:inline}.acts{display:flex;gap:.5rem;flex-wrap:wrap;margin:.6rem 0 1rem}"""


def page(title, body, tabs=None, active=None, obj=None, obj_title=None):
    return af.page(title, body, app=APP_NAME, version=VERSION, product="spezialisten",
                   tabs=tabs, active=active, obj=obj, obj_title=obj_title, style=STYLE)


def app_tabs(man):
    t = [("./", "Übersicht"), ("laeufe", "Läufe")]
    if man:
        t += [("neu", "Neuer Spezialist"), ("aenderungen", "Änderungen")]
    return t


def when(ts):
    return esc((ts or "")[:16].replace("T", " "))


def state_badge(s, st=None):
    if (st or {}).get("error"):
        return af.badge("Fehler", "err")
    return af.badge("aktiv", "ok") if s["active"] else af.badge("aus")


def spec_form(s, msg=""):
    s = s or {}
    v = lambda k, d="": esc(str(s.get(k, d)))
    sec = lambda k, word: (f"<label>{word} {'(eingetragen – leer lassen behält ihn)' if s.get(k + '_set') else ''}</label>"
                           f"<input type=password name={k} autocomplete=new-password>")
    return (f"{msg}<form method=post action='speichern' class='oaap-card spec'>"
            + (f"<input type=hidden name=id value='{v('id')}'>" if s.get("id") else "")
            + f"""<div class=row><div><label>Name (so steht er im Vorgang)</label><input name=name required value='{v("name")}'></div>
<div><label>Assistent (Kennung@Revision im MCP-Gateway)</label><input name=assistant required value='{v("assistant")}' placeholder='analyse@3'></div></div>
<label>Anweisung an das Modell (aus der Revision des Assistenten festgehalten)</label><textarea name=system required>{v("system")}</textarea>
<div class=row><div><label>Modellname im KI-Gateway</label><input name=model required value='{v("model")}'></div></div>
<h2>Wege und Schlüssel</h2>
<p class=oaap-muted>Jeder Spezialist hat drei eigene Schlüssel. Sie werden nie wieder angezeigt.</p>
<div class=row><div><label>Vorgänge (Adresse der Instanz, Plattform-Eingang)</label><input name=vorgaenge_url required value='{v("vorgaenge_url")}' placeholder='http://oaap-gateway-1:8131'></div>
<div>{sec("agent_key", "Schlüssel des Agenten (oaap authz agent-add)")}</div></div>
<div class=row><div><label>Seine Sicht im MCP-Gateway</label><input name=mcp_url required value='{v("mcp_url")}' placeholder='http://oaap-gateway-1:8128/mcp/spez-analyse'></div>
<div>{sec("mcp_key", "Gateway-Schlüssel nur für diese Sicht")}</div></div>
<div class=row><div><label>KI-Gateway (bis /v1)</label><input name=ai_url required value='{v("ai_url")}' placeholder='http://oaap-gateway-1:8123/v1'></div>
<div>{sec("ai_key", "Schlüssel des KI-Gateways")}</div></div>
<h2>Grenzen</h2><div class=row>
<div><label>Schritte je Lauf (höchstens {LIMITS['max_steps']})</label><input name=max_steps type=number min=1 max={LIMITS['max_steps']} value='{v("max_steps", LIMITS["max_steps"])}'></div>
<div><label>Minuten je Lauf (höchstens {LIMITS['max_minutes']})</label><input name=max_minutes type=number min=1 max={LIMITS['max_minutes']} value='{v("max_minutes", LIMITS["max_minutes"])}'></div>
<div><label>Läufe je Vorgang (höchstens {LIMITS['max_runs']})</label><input name=max_runs type=number min=1 max={LIMITS['max_runs']} value='{v("max_runs", LIMITS["max_runs"])}'></div></div>
<label class=check><input type=checkbox name=public value=1{' checked' if s.get('public') else ''}> darf Antworten an die meldende Person entwerfen (ein Mensch veröffentlicht sie)</label>
<h2>Leitung</h2>
<label class=check><input type=checkbox name=leads value=1{' checked' if s.get('leads') else ''}> leitet – gibt Aufträge an die Spezialisten seines Teams</label>
<div class=row><div><label>Team (Namen anderer Spezialisten, durch Komma getrennt)</label><input name=team value='{v("team")}' placeholder='Analyse, Recherche'></div>
<div><label>Aufträge je Vorgang (höchstens 8; Vorgänge kann weniger sagen)</label><input name=max_tasks type=number min=1 max=8 value='{v("max_tasks", 8)}'></div></div>
<label>Werkzeuge ohne Freigabe (nur der Mandanten-Administrator; Namen durch Leerzeichen getrennt{(', aufgehoben von ' + v("lifted_by")) if s.get("lifted_by") else ''})</label>
<input name=no_approval value='{v("no_approval")}' placeholder='leer: jedes ändernde Werkzeug braucht die Freigabe eines Menschen'>
<label class=check><input type=checkbox name=active value=1{' checked' if s.get('active') else ''}> aktiv – arbeitet Vorgänge im Hintergrund</label>
<p><button class=primary>Speichern</button></p></form>"""
            + ("" if s.get("id") else """<form method=post action='uebernehmen' class='oaap-card spec'><h2>Aus dem Export eines Assistenten übernehmen</h2>
<p class=oaap-muted>Im MCP-Gateway unter Assistenten „Exportieren“, den Inhalt hier einfügen. Übernommen werden Kennung@Revision, Anweisung und Modell.</p>
<textarea name=export></textarea><p><button>Übernehmen</button></p></form>"""))


def end_text(r):
    return REASON.get(r["reason"], r["reason"]) or "läuft"


RUN_COLS = [("Beginn", "started", lambda r: r["started"] or "", ""),
            ("Spezialist", "name", lambda r: r["name"] or "", ""),
            ("Vorgang", "case", lambda r: r["case_number"] or "", ""),
            ("Schritte", "steps", lambda r: r["steps"] or 0, "oaap-num"),
            ("Ende", "end", end_text, "")]


def run_row(r):
    kind = "warn" if r["reason"] in ("error", "limit", "runs") else ("" if r["reason"] else "info")
    return (f"<tr><td class=oaap-muted>{when(r['started'])}</td><td>{esc(r['name'] or '')} {af.badge('KI', 'info')}</td>"
            f"<td><code>{esc(r['case_number'])}</code></td><td class=oaap-num>{r['steps'] or 0}</td>"
            f"<td>{af.badge(end_text(r), kind)}</td></tr>")


class Handler(BaseHTTPRequestHandler):
    server_version = "spezialisten"

    def log_message(self, fmt, *a):
        pass

    def send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, to):
        self.send(303, "", extra=(("Location", to),))

    def who(self):
        uid = (self.headers.get("X-OAAP-User-Id") or "").strip()
        roles = {r.strip() for r in (self.headers.get("X-OAAP-Roles") or "").split(",") if r.strip()}
        name = urllib.parse.unquote(self.headers.get("X-OAAP-Display-Name") or self.headers.get("X-OAAP-User") or uid)
        return uid, roles, name

    def manager(self, roles):
        return manager(roles)

    def params(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/healthz":
            return self.send(200, "ok", "text/plain")
        if p == "/mcp":
            return DOOR.serve(self)
        uid, roles, name = self.who()
        if not uid:
            return self.send(401, page("Nicht angemeldet", af.empty("Bitte über die Plattform anmelden.")))
        man = self.manager(roles)
        if p == "/":
            return self.home(roles)
        if p == "/laeufe":
            return self.runs_page(roles)
        if p == "/aenderungen" and man:
            return self.audit_page()
        if p == "/neu" and man:
            return self.send(200, page("Neuer Spezialist", af.pagehead("Neuer Spezialist") + spec_form(None),
                                       app_tabs(man), "neu"))
        if p.startswith("/s/") and man:
            s = STORE.get(int(p[3:]) if p[3:].isdigit() else 0)
            if not s:
                return self.send(404, page("Nicht gefunden", af.empty("Diesen Spezialisten gibt es nicht. "
                                                                    "<a href='../'>Zur Übersicht</a>")))
            tab = (self.params().get("tab") or [""])[0]
            return self.send(200, self.obj_page(s, tab))
        return self.send(404, page("Nicht gefunden", af.empty("Diese Seite gibt es nicht. <a href='./'>Zur Übersicht</a>")))

    def obj_page(self, s, tab="", msg="", up=""):
        """Die Objektseite eines Spezialisten. `up` führt von der Adresse der Antwort
        zurück nach s/ (bei s/3/pruefen: '../')."""
        me = f"{up}{s['id']}"
        tabs = [(me, "Übersicht"), (f"{me}?tab=bearbeiten", "Bearbeiten")]
        st = STATUS.get(s["id"], {})
        head = (af.back(up + "../", "Zurück zu den Spezialisten")
                + f"<div class=oaap-objhead><h1>{esc(s['name'])} {af.badge('KI', 'info')} {state_badge(s, st)}</h1>"
                + af.facts([("Assistent", f"<code>{esc(s['assistant'])}</code>"),
                            ("Modell", f"<code>{esc(s['model'])}</code>"),
                            ("Agent", f"<code>{esc(s['uid'] or '–')}</code>"),
                            ("Zuletzt gefragt", f"{esc(st['at'])} UTC, {st.get('waiting', 0)} an der Reihe"
                             if st.get("at") else "–")])
                + "</div>")
        obj = f"Spezialist:{s['id']}"
        if tab == "bearbeiten":
            return page(s["name"], head + spec_form(s), tabs, f"{me}?tab=bearbeiten", obj=obj, obj_title=s["name"])
        keys = " ".join(af.badge(w, "ok") for k, w in (("agent_key", "Agent"), ("mcp_key", "Sicht"), ("ai_key", "KI"))
                        if s[k + "_set"]) or "keiner"
        err = af.msg(st["error"], error=True) if st.get("error") else ""
        acts = (f"<div class=acts><form method=post action='{me}/pruefen' class=inline><button>Prüfen</button></form>"
                f"<form method=post action='{me}/aktiv' class=inline><input type=hidden name=on value='{0 if s['active'] else 1}'>"
                f"<button>{'Ausschalten' if s['active'] else 'Einschalten'}</button></form>"
                f"<form method=post action='{me}/loeschen' class=inline onsubmit=\"return confirm('Spezialist und seine Schlüssel löschen?')\">"
                f"<button class=danger>Löschen</button></form></div>")
        limits = (f"<dl class=oaap-kv><dt>Schlüssel</dt><dd>{keys}</dd>"
                  f"<dt>Grenzen</dt><dd>{s['max_steps']} Schritte, {s['max_minutes']} Minuten je Lauf; "
                  f"{s['max_runs']} Läufe je Vorgang</dd>"
                  f"<dt>Antwortentwürfe</dt><dd>{'ja' if s.get('public') else 'nein'}</dd>"
                  + (f"<dt>Leitet</dt><dd>Team: {esc(s.get('team') or '–')}; höchstens {s.get('max_tasks') or 8} "
                     f"Aufträge je Vorgang</dd>" if s.get("leads") else "")
                  + 
                  f"<dt>Ohne Freigabe</dt><dd>{esc(s.get('no_approval') or 'keine Werkzeuge')}</dd></dl>")
        runs = STORE.runs(s["id"], limit=10)
        rt = af.table("".join(af.th(lbl, "", af.Query({}), cls=c) for lbl, _c, _f, c in RUN_COLS),
                      [run_row(r) for r in runs], "Noch kein Lauf.")
        more = f"<p><a href='{up}../laeufe?spezialist={urllib.parse.quote(s['name'])}'>Alle Läufe dieses Spezialisten</a></p>"
        body = head + msg + err + acts + af.card("Einstellungen", limits) + af.card("Letzte Läufe", rt + more)
        return page(s["name"], body, tabs, me, obj=obj, obj_title=s["name"])

    def runs_page(self, roles):
        q = af.Query(self.params(), filters=("spezialist", "ende"), default_sort="-started")
        rows = STORE.runs(None, limit=5000)
        names = sorted({r["name"] for r in rows if r["name"]})
        ends = sorted({end_text(r) for r in rows})
        rows = [r for r in rows if (not q.filters["spezialist"] or r["name"] == q.filters["spezialist"])
                and (not q.filters["ende"] or end_text(r) == q.filters["ende"])]
        body = (af.pagehead("Läufe")
                + "<p class=oaap-muted>Jeder Lauf eines Spezialisten an einem Vorgang. Was ein Lauf getan hat, "
                  "steht intern im Vorgang selbst.</p>"
                + af.list_report(q, rows, RUN_COLS, run_row, noun="Läufe", placeholder="Spezialist oder Vorgang",
                                 choices=[("spezialist", "Spezialist", [(n, n) for n in names]),
                                          ("ende", "Ende", [(e, e) for e in ends])],
                                 empty_text="Noch kein Lauf. Ein aktiver Spezialist beginnt, sobald ihm ein "
                                            "Vorgang zugeteilt ist."))
        return self.send(200, page("Läufe", body, app_tabs(self.manager(roles)), "laeufe"))

    def audit_page(self):
        q = af.Query(self.params(), default_sort="-at")
        cols = [("Zeit", "at", lambda a: a["at"] or "", ""), ("Änderung", "what", lambda a: a["what"] or "", ""),
                ("von", "actor", lambda a: a["actor"] or "", "")]
        body = (af.pagehead("Änderungen")
                + af.list_report(q, STORE.audit(limit=5000), cols,
                                 lambda a: (f"<tr><td class=oaap-muted>{when(a['at'])}</td><td>{esc(a['what'])}</td>"
                                            f"<td class=oaap-muted>{esc(a['actor'])}</td></tr>"),
                                 noun="Änderungen", empty_text="Noch keine Änderung."))
        return self.send(200, page("Änderungen", body, app_tabs(True), "aenderungen"))

    def home(self, roles, msg=""):
        man = self.manager(roles)
        q = af.Query(self.params(), filters=("zustand",), default_sort="name")
        f = q.filters["zustand"]
        rows = [s for s in STORE.all() if not f or (f == "aktiv") == bool(s["active"])]
        cols = [("Name", "name", lambda s: s["name"], ""), ("Zustand", "", None, ""),
                ("Assistent", "assistant", lambda s: s["assistant"], ""),
                ("Modell", "model", lambda s: s["model"], "oaap-minor"),
                ("Zuletzt gefragt", "", None, "oaap-minor")]

        def row(s):
            st = STATUS.get(s["id"], {})
            name = f"<a href='s/{s['id']}'>{esc(s['name'])}</a>" if man else esc(s["name"])
            seen = f"{esc(st['at'])} UTC, {st.get('waiting', 0)} an der Reihe" if st.get("at") else "–"
            err = f"<div class=err>{esc(st['error'])}</div>" if st.get("error") else ""
            return (f"<tr><td>{name} {af.badge('KI', 'info')}{err}</td><td>{state_badge(s, st)}</td>"
                    f"<td><code>{esc(s['assistant'])}</code></td><td class=oaap-minor><code>{esc(s['model'])}</code></td>"
                    f"<td class='oaap-minor oaap-muted'>{seen}</td></tr>")
        new = "<a class='oaap-btn primary' href='neu'>Neuer Spezialist</a>" if man else ""
        body = (af.pagehead("Spezialisten", new) + msg
                + "<p>Ein Spezialist ist ein KI-Assistent mit eigenem Agenten. Er bearbeitet Vorgänge "
                "seiner Bereiche im Hintergrund und schreibt jeden Schritt <b>intern</b> in den Vorgang. Was etwas "
                "ändert, legt er einem Menschen zur Freigabe vor. Kommt er nicht weiter, fragt er einen anderen "
                "Spezialisten oder einen Menschen des Bereichs; am Ende gibt er an die Zuständigen zurück. "
                "Die meldende Person sieht davon nichts.</p>"
                + af.list_report(q, rows, cols, row, noun="Spezialisten", placeholder="Name, Assistent, Modell",
                                 choices=[("zustand", "Zustand", [("aktiv", "aktiv"), ("aus", "aus")])],
                                 empty_text=("Noch kein Spezialist. <a href='neu'>Neuer Spezialist</a> legt den ersten an."
                                             if man else "Noch kein Spezialist.")))
        return self.send(200, page("Spezialisten", body, app_tabs(man), "./"))

    def same_origin(self):
        o = self.headers.get("Origin") or ""
        return not o or urllib.parse.urlparse(o).netloc == (self.headers.get("Host") or "")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/mcp":
            return DOOR.serve(self)
        # Erst den Rumpf lesen, dann ablehnen: sonst bricht die Verbindung ab, bevor die
        # Antwort ankommt, und der Browser zeigt statt des Grundes einen Netzfehler.
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200000:
            self.close_connection = True
            return self.send(413, "")
        raw = self.rfile.read(n).decode("utf-8", "replace")
        uid, roles, name = self.who()
        if not uid:
            return self.send(401, "")
        if not self.manager(roles):
            return self.send(403, page("Nicht erlaubt", af.msg("Spezialisten pflegt der Mandanten-Administrator.", True)))
        if not self.same_origin() or not (self.headers.get("Content-Type") or "").startswith(
                "application/x-www-form-urlencoded"):
            return self.send(403, page("Abgelehnt", af.msg("Dieses Formular kam nicht von dieser Seite.", True)))
        f = {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        actor = name or uid
        try:
            if p == "/speichern":
                if f.get("id"):
                    f["id"] = int(f["id"])
                STORE.save(f, actor, LIMITS, may_lift="tenant_admin" in roles)
                return self.redirect("./" if not f.get("id") else f"s/{f['id']}")
            if p == "/uebernehmen":
                return self.send(200, page("Neuer Spezialist", af.pagehead("Neuer Spezialist") + spec_form(
                    import_assistant(f.get("export", "")), af.msg("Übernommen. Bitte Wege und Schlüssel ergänzen.")),
                    app_tabs(True), "neu"))
            parts = p.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "s" and parts[1].isdigit():
                sid = int(parts[1])
                if parts[2] == "aktiv":
                    STORE.set_active(sid, f.get("on") == "1", actor)
                    return self.redirect(f"../{sid}")
                if parts[2] == "loeschen":
                    STORE.delete(sid, actor)
                    return self.redirect("../../")
                if parts[2] == "pruefen":
                    s = STORE.get(sid, with_secrets=True)
                    if not s:
                        raise Refused("Diesen Spezialisten gibt es nicht.")
                    res = check(s)
                    msg = af.card("Prüfung " + s["name"], "<ul>" + "".join(
                        f"<li class={'ok' if ok else 'err'}>{'✓' if ok else '✗'} {t}</li>" for ok, t in res) + "</ul>")
                    return self.send(200, self.obj_page(STORE.get(sid), "", msg, up="../"))
        except Refused as e:
            return self.send(400, page("Nicht möglich", af.msg(str(e), True)
                                       + "<p><a href='javascript:history.back()'>Zurück</a></p>"))
        return self.send(404, "")


# ------------------------------------------------------------------ MCP (RFC-0118 §9)
def manager(roles):
    return bool(set(roles) & {"tenant_admin", "admin"})


DOOR = mcpdoor.Door("oaap-spezialisten", VERSION,
                    "Die KI-Spezialisten des Mandanten und ihre Läufe an Vorgängen lesen. Nur lesend; "
                    "Schlüssel gibt die Tür nie heraus.")


def _signed_in(who):
    if not who.get("user_id"):
        raise mcpdoor.Refused("Nicht angemeldet: die Tür antwortet nur einer Person, die über die Plattform kommt.")


def _brief(s):
    st = STATUS.get(s["id"], {})
    return {"id": s["id"], "name": s["name"], "active": bool(s["active"]), "assistant": s["assistant"],
            "model": s["model"], "agent": s["uid"] or "", "last_asked_utc": st.get("at", ""),
            "waiting": st.get("waiting", 0), "error": st.get("error", ""), "ai": True}


def _run(r):
    return {"started": r["started"], "specialist": r["name"] or "", "case": r["case_number"],
            "steps": r["steps"] or 0, "end": end_text(r)}


@DOOR.tool("spezialisten_list", "Spezialisten auflisten",
           "Listet die KI-Spezialisten des Mandanten: Name, aktiv oder aus, Assistent, Modell, "
           "wann zuletzt nach Arbeit gefragt wurde und ein Fehler, falls einer vorliegt.",
           {"q": {"type": "string", "description": "Suchwort in Name, Assistent oder Modell (optional)"}})
def _list(args, who):
    _signed_in(who)
    q = af.Query({"q": str(args.get("q") or "")})
    rows = [_brief(s) for s in STORE.all() if q.matches(s["name"], s["assistant"], s["model"])]
    return {"count": len(rows), "specialists": rows}


@DOOR.tool("spezialisten_read", "Spezialist lesen",
           "Liest einen Spezialisten mit seinen letzten Läufen. Anweisung, Grenzen und Freigaben "
           "sieht nur, wer Spezialisten pflegen darf.",
           {"id": {"type": "integer", "description": "Kennung des Spezialisten (aus spezialisten_list)"},
            "name": {"type": "string", "description": "oder sein Name"}})
def _read(args, who):
    _signed_in(who)
    s = None
    if str(args.get("id") or "").isdigit():
        s = STORE.get(int(args["id"]))
    elif args.get("name"):
        s = next((x for x in STORE.all() if x["name"].lower() == str(args["name"]).strip().lower()), None)
    if not s:
        raise mcpdoor.Refused("Diesen Spezialisten gibt es nicht.")
    out = _brief(s)
    if manager(who.get("roles") or []):
        out.update(system=s["system"], max_steps=s["max_steps"], max_minutes=s["max_minutes"],
                   max_runs=s["max_runs"], drafts_to_reporter=bool(s.get("public")),
                   no_approval=s.get("no_approval") or "", lifted_by=s.get("lifted_by") or "",
                   leads=bool(s.get("leads")), team=s.get("team") or "", max_tasks=s.get("max_tasks") or 8,
                   keys_set={k: s[k + "_set"] for k in ("agent_key", "mcp_key", "ai_key")})
    out["runs"] = [_run(r) for r in STORE.runs(s["id"], limit=10)]
    return out


@DOOR.tool("spezialisten_runs", "Läufe lesen",
           "Listet die letzten Läufe der Spezialisten an Vorgängen: Beginn, Spezialist, Vorgang, "
           "Schritte und wie der Lauf endete.",
           {"name": {"type": "string", "description": "nur die Läufe dieses Spezialisten (optional)"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "description": "wie viele, Standard 30"}})
def _runs(args, who):
    _signed_in(who)
    try:
        limit = min(max(int(args.get("limit") or 30), 1), 200)
    except (TypeError, ValueError):
        limit = 30
    name = str(args.get("name") or "").strip().lower()
    rows = [r for r in STORE.runs(None, limit=5000) if not name or (r["name"] or "").lower() == name]
    return {"count": len(rows), "runs": [_run(r) for r in rows[:limit]]}


DOOR.prompt("spezialisten_ueberblick", "Überblick über die Spezialisten",
            "Welche Spezialisten arbeiten, und läuft alles?",
            "Gib mir einen kurzen Überblick über die KI-Spezialisten unseres Mandanten: wer ist aktiv, "
            "wer hat einen Fehler, und wie endeten die letzten Läufe? Nutze spezialisten_list und "
            "spezialisten_runs.")
DOOR.prompt("spezialisten_laeufe_pruefen", "Läufe eines Spezialisten prüfen",
            "Wie hat ein Spezialist zuletzt gearbeitet?",
            "Wie liefen die letzten Läufe des Spezialisten {name}? Wie oft endeten sie mit einer Grenze "
            "oder einem Fehler, und an welchen Vorgängen? Nutze spezialisten_read und spezialisten_runs.",
            arguments=("name",))


def main():
    global STORE
    os.makedirs(DATA, exist_ok=True)
    STORE = Store(os.path.join(DATA, "spezialisten.db"))
    threading.Thread(target=loop, daemon=True).start()
    print(f"Spezialisten {VERSION}: Abfrage alle {POLL} s, Grenzen {LIMITS}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
