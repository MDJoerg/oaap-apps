"""Spezialisten (RFC-0105 Stufe 1): KI-Agenten, die Vorgänge im Hintergrund bearbeiten.

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

import runner
from store import Refused, Store, import_assistant

VERSION = "0.2.0"
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
REASON = {"needs_approval": "wartet auf Freigabe", "done": "fertig, zurückgegeben", "limit": "Grenze, zurückgegeben", "stopped": "angehalten",
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
    for s in store.all(with_secrets=True):
        if not s["active"]:
            continue
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
                          f"tenant_admin ihn in Vorgänge unter „Bereiche“ als Erstbearbeitung wählen."))
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
CSS = """body{font-family:system-ui,sans-serif;margin:0;background:#f7f7f5;color:#1f2937}
main{max-width:60rem;margin:0 auto;padding:1rem}h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.6rem}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:.6rem;padding:.8rem 1rem;margin:.6rem 0}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{text-align:left;padding:.35rem .4rem;border-bottom:1px solid #e5e7eb;vertical-align:top}
label{display:block;font-size:.85rem;color:#6b7280;margin:.6rem 0 .2rem}input,textarea,select{font:inherit;width:100%;box-sizing:border-box;padding:.4rem;border:1px solid #d1d5db;border-radius:.35rem}
textarea{min-height:7rem}button{font:inherit;padding:.4rem .9rem;border-radius:.35rem;border:1px solid #1d4ed8;background:#1d4ed8;color:#fff;cursor:pointer}
button.sec{background:#fff;color:#1d4ed8}.row{display:flex;gap:.8rem;flex-wrap:wrap}.row>div{flex:1;min-width:12rem}
.muted{color:#6b7280;font-size:.88rem}.ok{color:#15803d}.err{color:#b91c1c}.pill{display:inline-block;font-size:.75rem;padding:.05rem .45rem;border-radius:.7rem;background:#e0e7ff;color:#3730a3}
form.inline{display:inline}a{color:#1d4ed8}code{background:#f3f4f6;padding:0 .25rem;border-radius:.2rem}"""


def page(title, body):
    return (f"<!doctype html><html lang=de><meta charset=utf-8><meta name=viewport content='width=device-width,"
            f"initial-scale=1'><title>{esc(title)} · Spezialisten</title><style>{CSS}</style><main>"
            f"<p class=muted><a href='./'>Spezialisten</a> · RFC-0105 · {VERSION}</p><h1>{esc(title)}</h1>{body}</main>")


def spec_form(s, msg=""):
    s = s or {}
    v = lambda k, d="": esc(str(s.get(k, d)))
    sec = lambda k, word: (f"<label>{word} {'(eingetragen -- leer lassen behält ihn)' if s.get(k + '_set') else ''}</label>"
                           f"<input type=password name={k} autocomplete=new-password>")
    return (f"{msg}<form method=post action='speichern' class=card>"
            + (f"<input type=hidden name=id value='{v('id')}'>" if s.get("id") else "")
            + f"""<div class=row><div><label>Name (so steht er im Vorgang)</label><input name=name required value='{v("name")}'></div>
<div><label>Assistent (Kennung@Revision im MCP-Gateway)</label><input name=assistant required value='{v("assistant")}' placeholder='analyse@3'></div></div>
<label>Anweisung an das Modell (aus der Revision des Assistenten festgehalten)</label><textarea name=system required>{v("system")}</textarea>
<div class=row><div><label>Modellname im KI-Gateway</label><input name=model required value='{v("model")}'></div></div>
<h2>Wege und Schlüssel</h2>
<p class=muted>Jeder Spezialist hat drei eigene Schlüssel. Sie werden nie wieder angezeigt.</p>
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
<label style='display:flex;gap:.4rem;align-items:center;color:#1f2937'><input type=checkbox name=public value=1 style='width:auto'{' checked' if s.get('public') else ''}> darf Antworten an die meldende Person entwerfen (ein Mensch veröffentlicht sie)</label>
<label style='display:flex;gap:.4rem;align-items:center;color:#1f2937'><input type=checkbox name=active value=1 style='width:auto'{' checked' if s.get('active') else ''}> aktiv -- arbeitet Vorgänge im Hintergrund</label>
<p><button>Speichern</button></p></form>
<form method=post action='uebernehmen' class=card><h2 style='margin-top:0'>Aus dem Export eines Assistenten übernehmen</h2>
<p class=muted>Im MCP-Gateway unter Assistenten „Exportieren“, den Inhalt hier einfügen. Übernommen werden Kennung@Revision, Anweisung und Modell.</p>
<textarea name=export></textarea><p><button class=sec>Übernehmen</button></p></form>"""
            )


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
        return bool(roles & {"tenant_admin", "admin"})

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/healthz":
            return self.send(200, "ok", "text/plain")
        uid, roles, name = self.who()
        if not uid:
            return self.send(401, page("Nicht angemeldet", "<p>Bitte über die Plattform anmelden.</p>"))
        if p == "/":
            return self.home(roles)
        if p == "/neu" and self.manager(roles):
            return self.send(200, page("Neuer Spezialist", spec_form(None)))
        if p.startswith("/s/") and self.manager(roles):
            s = STORE.get(int(p[3:]) if p[3:].isdigit() else 0)
            if not s:
                return self.send(404, page("Nicht gefunden", ""))
            return self.send(200, page(s["name"], spec_form(s) + self.runs_html(s["id"])))
        return self.send(404, page("Nicht gefunden", ""))

    def runs_html(self, sid=None):
        rows = STORE.runs(sid)
        if not rows:
            return "<p class=muted>Noch kein Lauf.</p>"
        return ("<h2>Läufe</h2><table><tr><th>Beginn</th><th>Spezialist</th><th>Vorgang</th><th>Schritte</th><th>Ende</th></tr>"
                + "".join(f"<tr><td class=muted>{esc(r['started'][:16].replace('T', ' '))}</td><td>{esc(r['name'] or '')}</td>"
                          f"<td><code>{esc(r['case_number'])}</code></td><td>{r['steps']}</td>"
                          f"<td>{esc(REASON.get(r['reason'], r['reason']) or 'läuft')}</td></tr>" for r in rows)
                + "</table>")

    def home(self, roles, msg=""):
        man = self.manager(roles)
        cards = []
        for s in STORE.all():
            st = STATUS.get(s["id"], {})
            state = ("<span class=ok>aktiv</span>" if s["active"] else "<span class=muted>aus</span>")
            err = f"<div class=err>{esc(st['error'])}</div>" if st.get("error") else ""
            seen = f"<span class=muted> · zuletzt gefragt {esc(st['at'])} UTC, {st.get('waiting', 0)} an der Reihe</span>" if st.get("at") else ""
            acts = ""
            if man:
                acts = (f"<p><a href='s/{s['id']}'>bearbeiten</a> · "
                        f"<form method=post action='s/{s['id']}/pruefen' class=inline><button class=sec>Prüfen</button></form> "
                        f"<form method=post action='s/{s['id']}/aktiv' class=inline><input type=hidden name=on value='{0 if s['active'] else 1}'>"
                        f"<button class=sec>{'Ausschalten' if s['active'] else 'Einschalten'}</button></form> "
                        f"<form method=post action='s/{s['id']}/loeschen' class=inline onsubmit=\"return confirm('Spezialist und seine Schlüssel löschen?')\">"
                        f"<button class=sec>Löschen</button></form></p>")
            keys = " ".join(f"<span class=pill>{w}</span>" for k, w in (("agent_key", "Agent"), ("mcp_key", "Sicht"), ("ai_key", "KI"))
                            if s[k + "_set"])
            cards.append(f"<div class=card><b>{esc(s['name'])}</b> <span class=pill>KI</span> · {state}{seen}"
                         f"<div class=muted>Assistent <code>{esc(s['assistant'])}</code> · Modell <code>{esc(s['model'])}</code>"
                         f" · Agent <code>{esc(s['uid'] or '–')}</code> · Schlüssel: {keys or 'keiner'}</div>{err}{acts}</div>")
        body = (msg + "<p>Ein Spezialist ist ein KI-Assistent mit eigenem Agenten. Er bearbeitet Vorgänge "
                "seiner Bereiche im Hintergrund, schreibt jeden Schritt <b>intern</b> in den Vorgang, ruft nur "
                "lesende Werkzeuge seiner Sicht und gibt am Ende an die Zuständigen zurück. Die meldende Person "
                "sieht davon nichts.</p>"
                + ("".join(cards) or "<p class=muted>Noch kein Spezialist.</p>"))
        if man:
            body += "<p><a href='neu'>Neuer Spezialist</a></p>"
        body += self.runs_html()
        if man:
            au = STORE.audit()
            if au:
                body += "<h2>Änderungen</h2><table>" + "".join(
                    f"<tr><td class=muted>{esc(a['at'][:16].replace('T', ' '))}</td><td>{esc(a['what'])}</td>"
                    f"<td class=muted>{esc(a['actor'])}</td></tr>" for a in au) + "</table>"
        return self.send(200, page("Spezialisten", body))

    def same_origin(self):
        o = self.headers.get("Origin") or ""
        return not o or urllib.parse.urlparse(o).netloc == (self.headers.get("Host") or "")

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
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
            return self.send(403, page("Nicht erlaubt", "<p>Spezialisten pflegt der tenant_admin.</p>"))
        if not self.same_origin() or not (self.headers.get("Content-Type") or "").startswith(
                "application/x-www-form-urlencoded"):
            return self.send(403, page("Abgelehnt", "<p>Dieses Formular kam nicht von dieser Seite.</p>"))
        f = {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
        actor = name or uid
        try:
            if p == "/speichern":
                if f.get("id"):
                    f["id"] = int(f["id"])
                STORE.save(f, actor, LIMITS)
                return self.redirect("./" if not f.get("id") else f"s/{f['id']}")
            if p == "/uebernehmen":
                return self.send(200, page("Neuer Spezialist", spec_form(import_assistant(f.get("export", "")),
                                 "<p class=ok>Übernommen. Bitte Wege und Schlüssel ergänzen.</p>")))
            parts = p.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "s" and parts[1].isdigit():
                sid = int(parts[1])
                if parts[2] == "aktiv":
                    STORE.set_active(sid, f.get("on") == "1", actor)
                    return self.redirect("../../")
                if parts[2] == "loeschen":
                    STORE.delete(sid, actor)
                    return self.redirect("../../")
                if parts[2] == "pruefen":
                    s = STORE.get(sid, with_secrets=True)
                    if not s:
                        raise Refused("Diesen Spezialisten gibt es nicht.")
                    res = check(s)
                    msg = "<div class=card><b>Prüfung " + esc(s["name"]) + "</b><ul>" + "".join(
                        f"<li class={'ok' if ok else 'err'}>{'✓' if ok else '✗'} {t}</li>" for ok, t in res) + "</ul></div>"
                    return self.home(roles, msg)
        except Refused as e:
            return self.send(400, page("Nicht möglich", f"<p class=err>{esc(str(e))}</p>"
                                       "<p><a href='javascript:history.back()'>Zurück</a></p>"))
        return self.send(404, "")


def main():
    global STORE
    os.makedirs(DATA, exist_ok=True)
    STORE = Store(os.path.join(DATA, "spezialisten.db"))
    threading.Thread(target=loop, daemon=True).start()
    print(f"Spezialisten {VERSION}: Abfrage alle {POLL} s, Grenzen {LIMITS}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
