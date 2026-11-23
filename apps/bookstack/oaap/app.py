"""Der Dienst `oaap` im Paket bookstack -- der Keim des Adapters (RFC-0110 §3).

Stufe 1 tut drei Dinge und sonst nichts:

1. **Einrichtungstür** `/oaap/setup` (RFC-0104): Rollen für Gruppen des
   Realms (setup_door.py).
2. **Beim Start**, sobald BookStack antwortet:
   - die Spalten prüfen, die der Dienst in BookStacks Datenbank anfasst (§3.3);
   - sein eigenes Konto in BookStack anlegen, falls es fehlt, und ein
     kurzlebiges Token dafür, das nur im Speicher liegt;
   - das bekannte Standardkennwort (admin@admin.com / password) durch
     `OAAP_WIKI_ADMIN_PASSWORD` ersetzen -- nur solange es noch das bekannte ist;
   - die Gruppe aus `OAAP_WIKI_ADMIN_GROUP` als „Externe
     Authentifizierungs-ID“ der Rolle Admin eintragen.
3. **Zustand** `/oaap/status` (hinter der Route für admin) und `/oaap/healthz`.

Es gibt keinen gemeinsamen technischen Benutzer für Inhalte: das eigene
Konto verwaltet Rollen und Konten, es liest und schreibt keine Seiten.
"""
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import setup_door
from wiki import Wiki, WikiError

ENV = os.environ
DATA = ENV.get("OAAP_WIKI_DATA") or "/data"
PORT = int(ENV.get("PORT") or "8000")

wiki = Wiki(ENV)
memory = setup_door.Memory(os.path.join(DATA, "setup.json"))
STATE = {"phase": "waiting", "since": None, "notes": [], "admin_group": None,
         "default_password": None, "adapter": None, "bookstack": None}


def log(msg):
    print(f"oaap-bookstack: {msg}", flush=True)


def note(msg):
    log(msg)
    STATE["notes"] = (STATE["notes"] + [msg])[-20:]


def apply_admin_group():
    want = (ENV.get("OAAP_WIKI_ADMIN_GROUP") or "").strip()
    if not want:
        STATE["admin_group"] = "nicht gesetzt (OAAP_WIKI_ADMIN_GROUP leer) -- die Rolle Admin bleibt, wie sie ist"
        return
    if "," in want:
        STATE["admin_group"] = "OAAP_WIKI_ADMIN_GROUP enthält ein Komma -- nicht übernommen"
        return
    st, doc = wiki.api("GET", "/api/roles?count=500")
    if st != 200:
        raise WikiError(f"Rollen lesen ging nicht ({st})")
    admin = [r for r in doc.get("data", []) if r.get("system_name") == "admin"]
    if not admin:
        raise WikiError("BookStack hat keine Rolle mit system_name admin")
    r = admin[0]
    if (r.get("external_auth_id") or "").strip().lower() == want.lower():
        STATE["admin_group"] = f"{want} (Rolle Admin, unverändert)"
        return
    st, doc = wiki.api("PUT", f"/api/roles/{r['id']}", {"external_auth_id": want})
    if st != 200:
        raise WikiError(f"Admin-Gruppe setzen ging nicht ({st}: {doc})")
    STATE["admin_group"] = f"{want} (Rolle Admin, eben gesetzt; vorher „{r.get('external_auth_id') or ''}“)"
    note(f"admin group set to {want}")


def replace_default_password():
    uid, unchanged = wiki.default_admin_unchanged()
    if not uid:
        STATE["default_password"] = "Standardkonto gibt es nicht (mehr)"
        return
    if not unchanged:
        STATE["default_password"] = "Standardkonto hat nicht mehr das bekannte Kennwort"
        return
    pw = ENV.get("OAAP_WIKI_ADMIN_PASSWORD") or ""
    if len(pw) < 12:
        raise WikiError("OAAP_WIKI_ADMIN_PASSWORD fehlt oder ist zu kurz -- das bekannte "
                        "Standardkennwort bleibt, bis es gesetzt ist")
    st, doc = wiki.api("PUT", f"/api/users/{uid}", {"password": pw})
    if st != 200:
        raise WikiError(f"Kennwort des Standardkontos setzen ging nicht ({st}: {doc})")
    STATE["default_password"] = "eben durch OAAP_WIKI_ADMIN_PASSWORD ersetzt"
    note("default administrator password replaced")


def startup():
    while not wiki.ping():
        time.sleep(5)
    STATE["phase"] = "starting"
    for attempt in range(1, 1000):
        try:
            problem = wiki.check_schema()
            if problem:
                STATE["phase"] = "schema"
                note(problem)
                return
            wiki.drop_tokens()          # §3.1: alte Tokens dieses Dienstes weg
            uid, made = wiki.ensure_adapter()
            STATE["adapter"] = f"Konto {uid}" + (" (eben angelegt)" if made else "")
            wiki.ensure_token()
            st, doc = wiki.api("GET", "/api/system")
            STATE["bookstack"] = (doc or {}).get("version") if st == 200 else f"unbekannt ({st})"
            replace_default_password()
            apply_admin_group()
            STATE["phase"] = "ready"
            STATE["since"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            log(f"ready: BookStack {STATE['bookstack']}, {STATE['adapter']}, admin group: {STATE['admin_group']}")
            return
        except (WikiError, OSError) as e:
            note(f"start, attempt {attempt}: {e}")
        except Exception as e:  # pymysql-Fehler u. a.: weiter versuchen, aber sagen
            note(f"start, attempt {attempt}: {type(e).__name__}: {e}")
        time.sleep(min(60, 5 * attempt))


class Handler(BaseHTTPRequestHandler):
    server_version = "oaap-bookstack"

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, doc):
        raw = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _route(self):
        u = urllib.parse.urlsplit(self.path)
        return u.path.rstrip("/") or "/", urllib.parse.parse_qs(u.query)

    def do_GET(self):
        path, q = self._route()
        if path == "/oaap/healthz":
            return self._send(200, {"ok": True, "phase": STATE["phase"]})
        if path == "/oaap/status":
            # hinter der Route /oaap (Rolle admin); nie ein Geheimnis
            return self._send(200, {"v": 1, "schema": wiki.schema_problem or ("gut" if wiki.schema_checked else "noch nicht geprüft"),
                                    **{k: v for k, v in STATE.items()}})
        if path == "/oaap/setup":
            if not setup_door.authorized(self.headers, ENV):
                return self._send(403, {"v": 1, "error": "nur mit dem Einrichtungsschlüssel"})
            if STATE["phase"] != "ready":
                return self._send(503, {"v": 1, "error": f"der Dienst ist noch nicht bereit ({STATE['phase']})"})
            try:
                code, doc = setup_door.get(wiki, (q.get("kind") or [""])[0], (q.get("id") or [""])[0])
            except WikiError as e:
                code, doc = 503, {"v": 1, "error": str(e)}
            return self._send(code, doc)
        return self._send(404, {"error": "nicht hier"})

    def do_POST(self):
        path, _ = self._route()
        if path != "/oaap/setup":
            return self._send(404, {"error": "nicht hier"})
        if not setup_door.authorized(self.headers, ENV):
            return self._send(403, {"v": 1, "error": "nur mit dem Einrichtungsschlüssel"})
        if STATE["phase"] != "ready":
            return self._send(503, {"v": 1, "error": f"der Dienst ist noch nicht bereit ({STATE['phase']})"})
        n = int(self.headers.get("Content-Length") or 0)
        if n > 256 * 1024:
            return self._send(400, {"v": 1, "error": "zu groß"})
        body = self.rfile.read(n)
        actor = setup_door.actor_of(self.headers)
        try:
            code, doc = setup_door.post(wiki, memory, body, actor)
        except setup_door.Malformed as e:
            code, doc = 400, {"v": 1, "error": str(e)}
        log(f"setup by {actor}: {json.dumps(doc, ensure_ascii=False)[:500]}")
        return self._send(code, doc)


def main():
    os.makedirs(DATA, exist_ok=True)
    threading.Thread(target=startup, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on {PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
