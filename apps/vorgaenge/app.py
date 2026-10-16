"""OAAP Vorgaenge 0.1 -- Melden und Klaeren im Mandanten (RFC-0061 Stufe 1).

Eine Instanz je Mandant. Sie nimmt zweierlei an:

1. **Meldungen ueber den Feedback-Knopf** der Plattform. Jede App, die
   `<script src="/platform/feedback.js" defer>` einbindet, schickt ihr
   Formular an `/platform/feedback` auf ihrem EIGENEN Ursprung; das
   Gateway leitet es unveraendert an diese Instanz weiter und setzt
   dabei, was nur es weiss: wer fragt (`X-OAAP-User-Id`, `X-OAAP-Roles`)
   und woher (`X-OAAP-Source-Instance`, `-App`, `-Version`, `-Tenant`).
   Diese Kopfzeilen streicht das Gateway auf JEDER anderen Anfrage --
   fehlen sie, kam die Anfrage nicht ueber den Knopf, und `/platform/feedback`
   lehnt ab. Das ist die ganze Beglaubigung (RFC-0061 §3.1, Q1).
2. **Interne Vorgaenge**, in dieser App selbst angelegt.

Geklaert wird in **Bereichen** (Queues). Ein Bereich ist ein Kontext im
Sinn von RFC-0045: wer welchen Bereich bearbeitet, vergibt der Mandant in
„Rollen & Rechte“ mit den Rollenvorlagen dieser App und der Kennung des
Bereichs als Kontext. Diese App fragt nur `/authz/effective` (authz_client,
schlaegt geschlossen fehl). Der Bereich `eingang` gehoert, solange ihn
niemand sonst hat, den Admins des Mandanten (Plattformrolle `admin`):
irgendwer muss den ersten Vorgang sehen koennen (RFC-0061 §2.2).

Was die Meldende sieht: ihre eigenen Vorgaenge, mit allen Eintraegen, und
sie kann kommentieren und eine Antwort schliessen.

Gebaut wie jede Referenz-App: kein eigener Login, ein HTTP-Port, Daten nur
unter /data, Logs nach stdout, nur Standardbibliothek.
"""
import hashlib
import html
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlparse

import re
import urllib.error
import urllib.request

import authz_client
from store import (BUTTON_KINDS, DEFAULT_QUEUE, KIND_LABEL, KINDS, MANUAL_EXCLUDED,
                   NEXT, RETENTION_MONTHS, STATUS_LABEL, Refused, Store, canonical)

VERSION = "0.2.1"
APP_ID = "vorgaenge"
PORT = 8000
DATA_DIR = os.environ.get("VORGAENGE_DATA_DIR", "/data")

# RFC-0061 §10.2: Drosselung je Person. Das Gateway drosselt nur oeffentliche
# Routen; diese ist angemeldet, also zaehlt die App selbst.
REPORTS_PER_HOUR = 20
# §3.2: Vorgaenge aus dem Backend einer App, je meldende Instanz und Stunde
SUBMISSIONS_PER_HOUR = 600
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")

# RFC-0061 Q6: Personendaten eines abgeschlossenen Vorgangs werden nach so
# vielen Monaten entfernt. Eine Instanz je Mandant, also ist das die
# Einstellung des Mandanten. Wie oft nachgesehen wird: alle sechs Stunden
# und beim Start -- ein Tag Verspaetung ist bei Monaten kein Verzug.
RETENTION_CHECK_SECONDS = 6 * 3600


def retention_months(raw=None):
    """Die eingestellte Frist, ganze Monate ab 1; alles andere ist die
    Vorgabe, und das wird gesagt statt geschluckt."""
    raw = os.environ.get("VORGAENGE_RETENTION_MONTHS", "") if raw is None else raw
    try:
        n = int(str(raw).strip())
        if n >= 1:
            return n
    except ValueError:
        pass
    if str(raw).strip():
        print(f"VORGAENGE_RETENTION_MONTHS={raw!r} ist keine ganze Zahl ab 1 -- "
              f"es gelten {RETENTION_MONTHS} Monate.", flush=True)
    return RETENTION_MONTHS


# ------------------------------------------------------------------ Ausgang
#
# RFC-0061 §5.3: mindestens einmal, idempotent auf handover_id. Was
# freigegeben ist, liegt im Ausgang, bis der Partner es quittiert hat;
# der Empfaenger ignoriert eine zweite Zustellung.

OUTBOX_KICK = threading.Event()
OUTBOX_SECONDS = 30


def send_doc(url, key, raw):
    """(ok, Fehlertext). ok nur bei einer JSON-Antwort mit ok: true --
    eine Umleitung zur Anmeldeseite ist auch ein 200, aber kein Empfang."""
    req = urllib.request.Request(
        url.rstrip("/") + "/handover", data=raw.encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read() or b"{}")
            ok = r.status in (200, 201) and d.get("ok") is True
            return ok, ("" if ok else "Antwort ohne ok")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        return False, f"HTTP {e.code} {detail}".strip()
    except (OSError, ValueError) as e:
        return False, str(e)


def flush_outbox(store):
    for row in store.outbox_due():
        p = store.partner(row["partner_id"])
        if not p or not p["active"] or not p["url"] or not p["out_key"]:
            store.outbox_result(row["seq"], False, "Partner nicht zum Senden eingerichtet")
            continue
        ok, err = send_doc(p["url"], p["out_key"], row["doc"])
        store.outbox_result(row["seq"], ok, err)
        print(f"Übergabe {row['handover_id'][:8]} an {p['id']}: "
              f"{'quittiert' if ok else 'nicht angekommen: ' + err}", flush=True)


def outbox_loop(store, kick):
    while True:
        try:
            flush_outbox(store)
        except Exception as e:      # der Ausgang darf die App nie anhalten
            print(f"Ausgang: Lauf fehlgeschlagen: {e}", flush=True)
        kick.wait(OUTBOX_SECONDS)
        kick.clear()


def retention_loop(months):
    while True:
        try:
            done = STORE.remove_person_data(months)
            if done:
                print(f"Löschfrist: Personendaten in {len(done)} Vorgang/Vorgängen "
                      f"entfernt ({', '.join(done)})", flush=True)
        except Exception as e:      # der Lauf darf die App nie anhalten
            print(f"Löschfrist: Lauf fehlgeschlagen: {e}", flush=True)
        time.sleep(RETENTION_CHECK_SECONDS)

esc = html.escape
STORE = None
AUTHZ = None
RETENTION = RETENTION_MONTHS


# ------------------------------------------------------------------ Rechte

class Who:
    def __init__(self, uid, name, roles, grants):
        self.id, self.name, self.roles, self.grants = uid, name, roles, grants

    @property
    def admin(self):
        return "admin" in self.roles

    def may(self, activity, queue):
        if self.admin and queue == DEFAULT_QUEUE:
            return True
        return bool(self.grants) and authz_client.may_in(
            self.grants, f"case.{activity}", queue=queue)

    def readable(self, queues):
        return [q["id"] for q in queues if self.may("read", q["id"])]


def can_see(who, c):
    return (c["reporter"] == who.id or who.may("read", c["queue"])
            or any(who.may("read", o) for o in c.get("observers") or ()))


def can_comment(who, c):
    return (c["reporter"] == who.id or who.may("comment", c["queue"])
            or any(who.may("read", o) for o in c.get("observers") or ()))


def next_statuses(who, c):
    """Was diese Person aus dem jetzigen Status heraus anbieten darf."""
    out = []
    for s in NEXT.get(c["status"], ()):
        if s in MANUAL_EXCLUDED:
            continue
        if s == "closed":
            ok = who.may("close", c["queue"]) or c["reporter"] == who.id
        elif s == "reopened":
            ok = who.may("triage", c["queue"]) or c["reporter"] == who.id
        else:
            ok = who.may("triage", c["queue"])
        if ok:
            out.append(s)
    return out


# ------------------------------------------------------------------ Optik

STYLE = """<link rel="stylesheet" href="/platform/theme.css">
<style>
  *{box-sizing:border-box}
  body{font-family:var(--oaap-font-family, system-ui, -apple-system, "Segoe UI", sans-serif);
       font-size:var(--oaap-font-size-base, 15px);margin:0;
       background:var(--oaap-color-bg, #fff);color:var(--oaap-color-text, #1a1d21)}
  main{max-width:62rem;margin:1.6rem auto;padding:0 var(--oaap-space-3, 16px)}
  h1{font-size:1.25rem;margin:0 0 1rem} h2{font-size:1.02rem;margin:1.4rem 0 .6rem}
  nav.tabs{display:flex;flex-wrap:wrap;gap:.4rem;margin-bottom:1.2rem}
  nav.tabs a{padding:.45rem .9rem;border-radius:6px;border:1px solid var(--oaap-color-border, #d8dbe0);
       text-decoration:none;color:var(--oaap-color-text, #1a1d21);font-size:.9rem}
  nav.tabs a.on{background:var(--oaap-color-primary, #2f6fed);color:var(--oaap-color-primary-text, #fff);border-color:transparent}
  table{border-collapse:collapse;width:100%} td,th{text-align:left;padding:.4rem .5rem;
       border-bottom:1px solid var(--oaap-color-border, #d8dbe0);vertical-align:top;font-size:.92rem}
  th{color:var(--oaap-color-text-muted, #5b616b);font-weight:600}
  .card{background:var(--oaap-color-surface, #f4f5f7);border:1px solid var(--oaap-color-border, #d8dbe0);
       border-radius:6px;padding:.8rem 1rem;margin:.6rem 0}
  .muted{color:var(--oaap-color-text-muted, #5b616b);font-size:.88rem}
  .pill{display:inline-block;padding:.05rem .5rem;border-radius:9px;font-size:.8rem;
       border:1px solid var(--oaap-color-border, #d8dbe0)}
  .err{color:var(--oaap-color-danger, #c4342f)} .ok{color:var(--oaap-color-success, #1f8a4c)}
  input,select,textarea{font:inherit;padding:.35rem .5rem;border:1px solid var(--oaap-color-border, #d8dbe0);border-radius:4px}
  textarea{width:100%;min-height:6rem} input[type=text]{width:100%}
  button{font:inherit;padding:.4rem .9rem;border-radius:4px;border:1px solid transparent;cursor:pointer;
       background:var(--oaap-color-primary, #2f6fed);color:var(--oaap-color-primary-text, #fff)}
  pre{white-space:pre-wrap;word-break:break-word;margin:.3rem 0;font-size:.85rem}
  .body{white-space:pre-wrap}
  form.inline{display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin:.5rem 0}
  form.inline > *{flex:0 0 auto}
  label{display:block;font-size:.85rem;color:var(--oaap-color-text-muted, #5b616b);margin:.5rem 0 .2rem}
</style>"""


def page(title, body, tab=""):
    tabs = [("/", "Übersicht", "home"), ("/neu", "Neuer Vorgang", "new"),
            ("/bereiche", "Bereiche", "queues"), ("/partner", "Partner", "partners")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == tab else ""}">{esc(t)}</a>'
                  for h, t, k in tabs)
    return (f"<!doctype html><html lang=de><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)} · Vorgänge</title>{STYLE}"
            f'<script src="/platform/feedback.js" defer></script></head>'
            f"<body><main><h1>{esc(title)}</h1><nav class=tabs>{nav}</nav>{body}</main></body></html>")


def pill(status):
    return f'<span class=pill>{esc(STATUS_LABEL.get(status, status))}</span>'


def case_table(rows, queues):
    if not rows:
        return "<p class=muted>Keine Vorgänge.</p>"
    qt = {q["id"]: q["title"] for q in queues}
    tr = "".join(
        f"<tr><td><a href='/v/{quote(r['number'])}'>{esc(r['number'])}</a></td>"
        f"<td>{esc(KIND_LABEL.get(r['kind'], r['kind']))}</td>"
        f"<td>{esc(r['title'])}</td><td>{pill(r['status'])}</td>"
        f"<td>{esc(qt.get(r['queue'], r['queue']))}</td>"
        f"<td class=muted>{esc(r['updated'][:16].replace('T', ' '))}</td></tr>"
        for r in rows)
    return ("<table><tr><th>Nr.</th><th>Art</th><th>Betreff</th><th>Status</th>"
            f"<th>Bereich</th><th>Geändert</th></tr>{tr}</table>")


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = f"vorgaenge/{VERSION}"

    # Ablage und Rechte haengen am Server, nicht am Modul: so laufen im Test
    # zwei Mandanten in einem Prozess und uebergeben einander.
    @property
    def store(self):
        return getattr(self.server, "store", None) or STORE

    @property
    def authz(self):
        return getattr(self.server, "authz", None) or AUTHZ

    @property
    def kick(self):
        return getattr(self.server, "kick", None) or OUTBOX_KICK

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    # --- Antworten
    def send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def json(self, code, doc):
        self.send(code, json.dumps(doc, ensure_ascii=False), "application/json; charset=utf-8")

    def redirect(self, to):
        self.send(303, "", headers={"Location": to})

    # --- wer fragt
    def who(self):
        uid = self.headers.get("X-OAAP-User-Id", "").strip()
        if not uid:
            return None
        roles = {r.strip() for r in self.headers.get("X-OAAP-Roles", "").split(",") if r.strip()}
        name = (self.headers.get("X-OAAP-Display-Name", "").strip()
                or self.headers.get("X-OAAP-User", "").strip() or uid)
        return Who(uid, name, roles, self.authz.grants(uid))

    def is_partner(self):
        """Ein Maschinenzugang, den wir einem Partner ausgestellt haben, traegt
        die Rolle `user` -- das Gateway liesse ihn also auf jede Seite dieser
        App. Gemessen auf oaap-test (16.10.): 200 auf `/`. Er bekommt hier
        `/handover` und sonst nichts: weder die Namen unserer Bereiche noch
        das Formular fuer einen neuen Vorgang. `Origin` schuetzt dort nur vor
        einem Browser, nicht vor einem Skript mit Schluessel."""
        return bool(self.store.partner_by_principal(self.headers.get("X-OAAP-User", "").strip()))

    def same_origin(self):
        """Ein POST muss von dieser Seite kommen. Der Browser schickt Origin
        bei jedem POST mit; fehlt er, war es kein Browser-Formular."""
        origin = self.headers.get("Origin", "")
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host", "")
        if not origin:
            return False
        return urlparse(origin).netloc == host

    BODY_LIMIT = 64 * 1024

    def body(self):
        return self._raw

    def read_body(self):
        """Den Rumpf IMMER zuerst lesen, auch wenn gleich abgelehnt wird:
        eine Antwort auf einen ungelesenen Rumpf bricht beim Client als
        Verbindungsabbruch an statt als lesbare Ablehnung."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > self.BODY_LIMIT:
            self.close_connection = True
            return False
        self._raw = self.rfile.read(n) if n else b""
        return True

    def form(self):
        return {k: v[0] for k, v in parse_qs(self.body().decode("utf-8"),
                                             keep_blank_values=True).items()}

    # --- Routen
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        p = u.path
        if p == "/healthz":
            return self.send(200, "ok", "text/plain")
        if p == "/platform/feedback/session":
            return self.feedback_session()
        if self.is_partner():
            return self.send(403, page("Nur Übergaben", "<p>Dieser Zugang liefert Übergaben, sonst nichts.</p>"))
        who = self.who()
        if not who:
            return self.send(401, page("Nicht angemeldet", "<p>Bitte über die Plattform anmelden.</p>"))
        q = parse_qs(u.query)
        if p == "/":
            return self.home(who, (q.get("alle") or [""])[0] == "1")
        if p == "/neu":
            return self.new_form(who)
        if p == "/bereiche":
            return self.queues_page(who)
        if p == "/partner":
            return self.partners_page(who)
        if p.startswith("/v/") and p.endswith("/uebergabe"):
            return self.handover_form(who, p[3:-len("/uebergabe")],
                                      (q.get("partner") or [""])[0])
        if p.startswith("/v/"):
            return self.case_page(who, p[3:], (q.get("m") or [""])[0])
        return self.send(404, page("Nicht gefunden", ""))

    def do_POST(self):
        p = urlparse(self.path).path
        if not self.read_body():
            return self.send(413, "")
        if p == "/platform/feedback":
            return self.feedback_post()
        if p == "/platform/cases/submit":
            return self.backend_submit()
        if p == "/handover":
            return self.handover_in()
        if self.is_partner():
            return self.json(403, {"error": "Dieser Zugang liefert Übergaben, sonst nichts."})
        who = self.who()
        if not who:
            return self.send(401, "")
        if not self.same_origin():
            return self.send(403, page("Abgelehnt", "<p>Dieses Formular kam nicht von dieser Seite.</p>"))
        try:
            f = self.form()
            if p == "/neu":
                return self.new_post(who, f)
            if p == "/bereiche":
                return self.queues_post(who, f)
            if p == "/partner":
                return self.partners_post(who, f)
            if p.startswith("/v/") and p.endswith("/uebergabe"):
                return self.handover_post(who, p[3:-len("/uebergabe")], f)
            if p.startswith("/v/"):
                return self.case_post(who, p[3:].split("/")[0], p.rsplit("/", 1)[-1], f)
        except Refused as e:
            return self.send(400, page("Nicht möglich", f"<p class=err>{esc(str(e))}</p>"
                                       "<p><a href='javascript:history.back()'>Zurück</a></p>"))
        return self.send(404, "")

    # --- der Knopf (RFC-0061 §3.1)
    def attested(self):
        """Die Herkunft, so wie das Gateway sie gesetzt hat -- oder None."""
        inst = self.headers.get("X-OAAP-Source-Instance", "").strip()
        if not inst:
            return None
        return {"instance": inst,
                "app": self.headers.get("X-OAAP-Source-App", "").strip(),
                "version": self.headers.get("X-OAAP-Source-Version", "").strip(),
                "tenant": self.headers.get("X-OAAP-Source-Tenant", "").strip()}

    def feedback_session(self):
        src = self.attested()
        uid = self.headers.get("X-OAAP-User-Id", "").strip()
        if not src or not uid:
            # nicht ueber das Gateway gekommen, oder ohne Sitzung: nichts zeigen
            return self.json(404, {"ok": False})
        return self.json(200, {"ok": True, "kinds": list(BUTTON_KINDS)})

    def feedback_post(self):
        src = self.attested()
        who = self.who()
        if not src or not who:
            return self.json(404, {"ok": False})
        if not self.same_origin():
            return self.json(403, {"error": "Diese Meldung kam nicht von der Seite der App."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        if self.store.recent_by(who.id, 3600) >= REPORTS_PER_HOUR:
            return self.json(429, {"error": "Zu viele Meldungen in der letzten Stunde. "
                                            "Bitte später noch einmal."})
        try:
            doc = json.loads(self.body().decode("utf-8") or "{}")
            if not isinstance(doc, dict):
                raise Refused("JSON-Objekt erwartet.")
            # RFC-0061 §10.1: beglaubigt ist, was das Gateway setzt -- der
            # Client kann darunter nichts ablegen, sein Teil heisst `browser`
            attested = dict(src, user_id=who.id,
                            roles=sorted(who.roles))
            made = self.store.create(kind=doc.get("kind"), title=doc.get("title"),
                                body=doc.get("body") or "", reporter=who.id,
                                reporter_name=who.name, obj=doc.get("object"),
                                attested=attested, browser=doc.get("browser"),
                                allowed_kinds=BUTTON_KINDS)
        except (Refused, ValueError) as e:
            return self.json(400, {"error": str(e) if isinstance(e, Refused) else "Ungültige Meldung."})
        print(f"case {made['number']} from {src['instance']}", flush=True)
        return self.json(201, {"ok": True, "number": made["number"], "id": made["id"]})

    # --- Vorgaenge aus dem Backend einer App (RFC-0061 §3.2)
    def backend_submit(self):
        """Nur der Maschinenprinzipal DER Instanz, aus der das Gateway die
        Anfrage meldet. Das Gateway liess an dieser Tuer nur ihren Schluessel
        durch; hier steht dieselbe Regel noch einmal, weil eine Sitzung, die
        den Pfad erreicht, sonst als Person einreichen koennte."""
        src = self.attested()
        principal = self.headers.get("X-OAAP-User", "").strip()
        if not src or principal != f"instance:{src['instance']}":
            return self.json(403, {"error": "Nur das Backend der App selbst, mit ihrem "
                                            "Schlüssel (OAAP_CASES_KEY)."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        if self.store.submissions_by(src["instance"], 3600) >= SUBMISSIONS_PER_HOUR:
            return self.json(429, {"error": "Zu viele Vorgänge dieser App in der letzten Stunde."})
        try:
            doc = json.loads(self.body().decode("utf-8") or "{}")
            if not isinstance(doc, dict):
                raise Refused("JSON-Objekt erwartet.")
            on = doc.get("on_behalf_of") or {}
            if isinstance(on, str):
                on = {"user_id": on}
            if not isinstance(on, dict):
                raise Refused("on_behalf_of: Benutzer-ID oder {user_id, name}.")
            uid = str(on.get("user_id") or "")
            if uid and not UUID.fullmatch(uid):
                raise Refused("on_behalf_of.user_id ist keine Benutzer-ID (RFC-0040).")
            app_label = src.get("app") or src["instance"]
            name = (str(on.get("name") or "") or f"App {app_label}") if uid else f"App {app_label}"
            made = self.store.create(
                kind=doc.get("kind") or "internal", title=doc.get("title"),
                body=doc.get("body") or "",
                reporter=uid or f"app:{src['instance']}", reporter_name=name,
                queue=str(doc.get("queue") or DEFAULT_QUEUE), obj=doc.get("object"),
                attested=dict(src, submitted_by=f"app:{src['instance']}"),
                asserted={"on_behalf_of": uid, "name": on.get("name")} if uid else None)
        except (Refused, ValueError) as e:
            return self.json(400, {"error": str(e) if isinstance(e, Refused) else "Ungültiger Vorgang."})
        print(f"case {made['number']} from backend of {src['instance']}", flush=True)
        return self.json(201, {"ok": True, "number": made["number"], "id": made["id"]})

    # --- Posteingang fuer Uebergaben (RFC-0061 §5.3)
    def handover_in(self):
        """Ein Partner liefert. Wer er ist, sagt der Schluessel, den unser
        Mandant ihm ausgestellt hat (das Gateway prueft ihn und nennt den
        Prinzipal); ob er liefern DARF, sagt die Partnerliste hier."""
        if not self.headers.get("Authorization", "").startswith("Bearer "):
            return self.json(401, {"error": "Übergaben kommen mit einem Schlüssel."})
        partner = self.store.partner_by_principal(self.headers.get("X-OAAP-User", "").strip())
        if not partner:
            return self.json(403, {"error": "Für diesen Schlüssel ist kein Partner eingetragen."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        try:
            status, number = self.store.receive(partner, self.body())
        except Refused as e:
            return self.json(400, {"error": str(e)})
        print(f"handover from {partner['id']}: {status} {number}", flush=True)
        return self.json(200, {"ok": True, "status": status})

    # --- Partner (nur Admins des Mandanten)
    def partners_page(self, who):
        if not who.admin:
            return self.send(403, page("Partner", "<p>Partner tragen die Admins des Mandanten ein.</p>",
                                       "partners"))
        queues = self.store.queues()
        rows = "".join(
            f"<tr><td><code>{esc(x['id'])}</code></td><td>{esc(x['name'])}</td>"
            f"<td>{esc(x['url']) or '–'}</td><td>{'ja' if x['out_key'] else 'nein'}</td>"
            f"<td><code>{esc(x['in_principal']) or '–'}</code></td><td>{esc(x['in_queue'])}</td>"
            f"<td>{'aktiv' if x['active'] else 'aus'}</td></tr>"
            for x in self.store.partners())
        qs = "".join(f"<option value='{esc(q['id'])}'>{esc(q['title'])}</option>" for q in queues)
        body = f"""<p>Ein Partner ist ein anderer Mandant, mit dem Vorgänge ausgetauscht werden,
auf diesem oder einem anderen Knoten (RFC-0061 §5). Was übergeben wird, ist eine freigegebene
Kopie ohne Personendaten; der Vorgang selbst bleibt hier.</p>
<ul class=muted>
<li><b>Damit Sie an ihn übergeben können:</b> Adresse seiner Vorgänge-App und der Schlüssel, den
<i>er</i> Ihnen ausgestellt hat.</li>
<li><b>Damit er Ihnen übergeben oder antworten kann:</b> Legen Sie im Portal unter <i>Zugänge</i>
einen Maschinenzugang mit Rolle <code>user</code> an, beschränkt auf diese Instanz, geben Sie ihm
den Schlüssel, und tragen Sie den Namen des Zugangs hier als <i>eingehenden Zugang</i> ein.</li>
</ul>
<table><tr><th>Kennung</th><th>Name</th><th>Adresse</th><th>Schlüssel</th><th>Eingehender Zugang</th>
<th>landet in</th><th></th></tr>{rows}</table>
<h2>Partner eintragen oder ändern</h2>
<form method=post action=/partner class=card>
 <label for=i>Kennung</label><input id=i name=id pattern='[a-z0-9][a-z0-9-]{{0,39}}' required>
 <label for=n>Name</label><input type=text id=n name=name maxlength=80>
 <label for=u>Adresse seiner Vorgänge-App (leer: er übergibt nur an uns)</label>
 <input type=text id=u name=url placeholder='https://vorgaenge.dev.example.org'>
 <label for=k>Schlüssel, den er uns ausgestellt hat (leer lassen = unverändert)</label>
 <input type=password id=k name=out_key autocomplete=off>
 <label for=p>Eingehender Zugang (Name des Maschinenzugangs)</label>
 <input type=text id=p name=in_principal>
 <label for=q>Übergaben von ihm landen in</label><select id=q name=in_queue>{qs}</select>
 <p><button name=action value=save>Speichern</button>
    <button name=action value=off>Abschalten</button></p></form>"""
        return self.send(200, page("Partner", body, "partners"))

    def partners_post(self, who, f):
        if not who.admin:
            raise Refused("Partner tragen die Admins des Mandanten ein.")
        if f.get("action") == "off":
            self.store.deactivate_partner((f.get("id") or "").strip().lower())
        else:
            self.store.save_partner(f.get("id"), f.get("name"), f.get("url"),
                                    f.get("out_key"), f.get("in_principal"), f.get("in_queue"))
        return self.redirect("/partner")

    # --- Uebergabe (RFC-0061 §5.2, Q3: ein Mensch gibt frei)
    def _handover_case(self, who, number, pid):
        c = self.store.get(number)
        if not c or not can_see(who, c):
            return None, None, "Diesen Vorgang gibt es nicht."
        if not who.may("hand_over", c["queue"]):
            return None, None, "Übergeben darf nur das Clearing dieses Bereichs."
        partner = self.store.partner(pid)
        if not partner or not partner["active"] or not partner["url"]:
            return None, None, "Diesen Partner gibt es nicht, oder er nimmt nichts an."
        return c, partner, ""

    def handover_form(self, who, number, pid):
        c, partner, err = self._handover_case(who, number, pid)
        if err:
            return self.send(403, page("Übergabe", f"<p class=err>{esc(err)}</p>"))
        return self.send(200, page(f"Übergabe {c['number']} an {partner['name']}",
                                   self._draft_html(c, partner, c["title"], c["body"], (), True)))

    def _draft_html(self, c, partner, title, body, picked, with_tech):
        comments = [e for e in c["entries"] if e["type"] == "comment" and e["text"]]
        boxes = "".join(
            f"<label><input type=checkbox name=entry value={e['seq']}"
            f"{' checked' if str(e['seq']) in picked else ''}> {esc(e['at'][:16].replace('T', ' '))}: "
            f"{esc(e['text'][:120])}</label>" for e in comments) or "<p class=muted>keine</p>"
        return f"""
<p>Was hier steht, verlässt den Mandanten. Die meldende Person, Benutzer-IDs und alle Einträge,
die Sie nicht auswählen, gehen <b>nicht</b> mit. Namen im Text findet kein Filter — prüfen Sie
Betreff und Text.</p>
<form method=post action='/v/{quote(c['number'])}/uebergabe' class=card>
 <input type=hidden name=partner value='{esc(partner['id'])}'>
 <label for=t>Betreff</label><input type=text id=t name=title maxlength=200 value='{esc(title)}' required>
 <label for=b>Text</label><textarea id=b name=body maxlength=10000>{esc(body)}</textarea>
 <label><input type=checkbox name=with_tech value=1{' checked' if with_tech else ''}>
  technische Angaben mitgeben (App, Version, Seite, Browser, letzter Fehler)</label>
 <h2>Kommentare, die mitgehen</h2>{boxes}
 <p><button name=step value=preview>Vorschau</button></p></form>"""

    def handover_post(self, who, number, f):
        c, partner, err = self._handover_case(who, number, f.get("partner", ""))
        if err:
            raise Refused(err)
        picked = parse_qs(self.body().decode("utf-8")).get("entry", [])
        with_tech = f.get("with_tech") == "1"
        sender = (os.environ.get("OAAP_INSTANCE_NAMES", "").split(",")[0] or "").strip()
        if f.get("step") == "release":
            doc = self.store.handover_draft(c, partner, f.get("title"), f.get("body", ""),
                                            picked, with_tech, f.get("handover_id"),
                                            f.get("created"), sender)
            if hashlib.sha256(canonical(doc).encode()).hexdigest() != f.get("sha256"):
                raise Refused("Das Dokument weicht von der Vorschau ab. Bitte neu prüfen.")
            self.store.release(c, partner, doc, who.id, who.name)
            self.kick.set()
            return self.redirect(f"/v/{quote(c['number'])}")
        doc = self.store.handover_draft(c, partner, f.get("title"), f.get("body", ""),
                                        picked, with_tech, sender=sender)
        raw = canonical(doc)
        hidden = "".join(f"<input type=hidden name={k} value='{esc(v)}'>" for k, v in (
            ("partner", partner["id"]), ("title", f.get("title", "")), ("body", f.get("body", "")),
            ("handover_id", doc["handover_id"]), ("created", doc["created"]),
            ("sha256", hashlib.sha256(raw.encode()).hexdigest()),
            ("with_tech", "1" if with_tech else "0")))
        hidden += "".join(f"<input type=hidden name=entry value='{esc(x)}'>" for x in picked)
        body = (f"<p>Genau diese Bytes gehen an <b>{esc(partner['name'])}</b> "
                f"(<code>{esc(partner['url'])}</code>):</p>"
                f"<pre class=card>{esc(raw)}</pre>"
                f"<form method=post action='/v/{quote(c['number'])}/uebergabe'>{hidden}"
                f"<button name=step value=release>Freigeben und senden</button></form>"
                f"<p><a href='/v/{quote(c['number'])}/uebergabe?partner={quote(partner['id'])}'>"
                f"Zurück zum Entwurf</a></p>")
        return self.send(200, page(f"Vorschau der Übergabe {c['number']}", body))

    # --- Seiten
    def home(self, who, all_):
        queues = self.store.queues()
        readable = who.readable(queues)
        mine = self.store.listing(reporter=who.id, open_only=not all_)
        work = self.store.listing(queues=readable, open_only=not all_) if readable else []
        toggle = ("<a href='/'>nur offene</a>" if all_ else "<a href='/?alle=1'>auch erledigte zeigen</a>")
        body = f"<p class=muted>{toggle}</p>"
        if readable:
            names = ", ".join(q["title"] for q in queues if q["id"] in readable)
            body += f"<h2>In meinen Bereichen</h2><p class=muted>{esc(names)}</p>" + case_table(work, queues)
        body += "<h2>Von mir gemeldet</h2>" + case_table(mine, queues)
        return self.send(200, page("Vorgänge", body, "home"))

    def new_form(self, who, err=""):
        queues = self.store.queues()
        kinds = "".join(f"<option value={k}>{esc(KIND_LABEL[k])}</option>" for k in KINDS)
        qs = "".join(f"<option value='{esc(q['id'])}'>{esc(q['title'])}</option>" for q in queues)
        body = (f"<p class=err>{esc(err)}</p>" if err else "") + f"""
<form method=post action=/neu class=card>
 <label for=k>Art</label><select id=k name=kind>{kinds}</select>
 <label for=q>Bereich</label><select id=q name=queue>{qs}</select>
 <label for=t>Betreff</label><input type=text id=t name=title maxlength=200 required>
 <label for=b>Beschreibung</label><textarea id=b name=body maxlength=10000></textarea>
 <p><button>Anlegen</button></p></form>"""
        return self.send(200, page("Neuer Vorgang", body, "new"))

    def new_post(self, who, f):
        made = self.store.create(kind=f.get("kind"), title=f.get("title"), body=f.get("body", ""),
                            reporter=who.id, reporter_name=who.name,
                            queue=f.get("queue") or DEFAULT_QUEUE,
                            attested={"app": APP_ID, "version": VERSION,
                                      "user_id": who.id, "via": "app"})
        return self.redirect(f"/v/{quote(made['number'])}")

    def queues_page(self, who, msg=""):
        queues = self.store.queues()
        rows = "".join(
            f"<tr><td><code>{esc(q['id'])}</code></td><td>{esc(q['title'])}</td>"
            f"<td>{'ja' if who.may('read', q['id']) else ''}</td></tr>" for q in queues)
        body = (f"<p>Ein Bereich ist der Ort, an dem Vorgänge geklärt werden. Wer einen "
                f"Bereich bearbeitet, vergibt der Mandant in <b>Rollen &amp; Rechte</b>: "
                f"App <i>Vorgänge</i>, Rollenvorlage <i>Bearbeiter/in</i> oder <i>Clearing</i>, "
                f"und als Kontext <b>Zuständigkeitsbereich</b> die Kennung aus der ersten Spalte. "
                f"Den Bereich <code>{DEFAULT_QUEUE}</code> sehen außerdem die Admins des Mandanten.</p>"
                f"<p class=muted>Löschfrist: {RETENTION} Monate nach dem Schließen oder Ablehnen "
                f"wird die meldende Person aus einem Vorgang entfernt "
                f"(Einstellung <code>VORGAENGE_RETENTION_MONTHS</code> dieser Instanz).</p>"
                f"<table><tr><th>Kennung</th><th>Titel</th><th>Ich sehe ihn</th></tr>{rows}</table>")
        if who.admin:
            body += """<h2>Bereich anlegen</h2>
<form method=post action=/bereiche class=inline>
 <div><label for=i>Kennung</label><input id=i name=id pattern='[a-z0-9][a-z0-9-]{0,39}' required></div>
 <div><label for=t>Titel</label><input id=t name=title maxlength=80 required></div>
 <button>Anlegen</button></form>"""
        return self.send(200, page("Bereiche", body, "queues"))

    def queues_post(self, who, f):
        if not who.admin:
            raise Refused("Bereiche legen die Admins des Mandanten an.")
        self.store.add_queue(f.get("id"), f.get("title"))
        return self.redirect("/bereiche")

    def case_page(self, who, number, msg=""):
        c = self.store.get(number)
        if not c or not can_see(who, c):
            return self.send(404, page("Nicht gefunden", "<p>Diesen Vorgang gibt es nicht, "
                                       "oder er liegt nicht in einem Ihrer Bereiche.</p>"))
        queues = self.store.queues()
        qt = {q["id"]: q["title"] for q in queues}
        obj = (f"<div class=muted>Bezug: {esc(c['object_kind'])} <code>{esc(c['object_id'])}</code></div>"
               if c["object_id"] else "")
        tech = c["tech"] or {}
        att = tech.get("attested") or {}
        src = ""
        if att.get("instance"):
            src = (f"<div class=muted>Gemeldet aus <b>{esc(att.get('app', ''))}</b> "
                   f"{esc(att.get('version', ''))}, Instanz <code>{esc(att['instance'])}</code> "
                   f"(vom Gateway beglaubigt)</div>")
        techbox = ""
        if who.may("read", c["queue"]) and tech.get("browser"):
            techbox = ("<details><summary class=muted>Technische Angaben aus dem Browser</summary><pre>"
                       + esc("\n".join(f"{k}: {v}" for k, v in tech["browser"].items()))
                       + "</pre></details>")
        ent = []
        for e in c["entries"]:
            when = esc(e["at"][:16].replace("T", " "))
            who_ = esc(e["actor_name"])
            d = e["data"]
            if e["type"] == "created":
                head = f"angelegt in {esc(qt.get(d.get('queue'), d.get('queue', '')))}"
            elif e["type"] == "status":
                head = (f"Status {esc(STATUS_LABEL.get(d.get('from'), d.get('from', '')))} → "
                        f"{esc(STATUS_LABEL.get(d.get('to'), d.get('to', '')))}")
            elif e["type"] == "move":
                head = (f"verschoben {esc(qt.get(d.get('from'), d.get('from', '')))} → "
                        f"{esc(qt.get(d.get('to'), d.get('to', '')))}")
            elif e["type"] == "retention":
                head = "Löschfrist"
            elif e["type"] == "handover":
                head = "übergeben (freigegeben)"
            elif e["type"] == "received":
                head = "aus einer Übergabe übernommen"
            elif e["type"] == "answer":
                head = "Antwort des Partners"
            elif e["type"] == "answer_sent":
                head = "Antwort an den Partner gesendet"
            else:
                head = "Kommentar"
            text = f"<div class=body>{esc(e['text'])}</div>" if e["text"] else ""
            ent.append(f"<div class=card><div class=muted>{when} · {who_} · {head}</div>{text}</div>")
        forms = ""
        base = f"/v/{quote(c['number'])}"
        if can_comment(who, c):
            forms += (f"<h2>Kommentar</h2><form method=post action='{base}/comment'>"
                      f"<textarea name=text maxlength=10000 required></textarea>"
                      f"<p><button>Kommentieren</button></p></form>")
        nxt = next_statuses(who, c)
        if nxt:
            opts = "".join(f"<option value={s}>{esc(STATUS_LABEL[s])}</option>" for s in nxt)
            forms += (f"<h2>Status</h2><form method=post action='{base}/status'>"
                      f"<select name=status>{opts}</select>"
                      f"<label>Begründung oder Antwort (bei Ablehnung Pflicht)</label>"
                      f"<textarea name=text maxlength=10000></textarea>"
                      f"<p><button>Ändern</button></p></form>")
        if who.may("move", c["queue"]):
            opts = "".join(f"<option value='{esc(q['id'])}'>{esc(q['title'])}</option>"
                           for q in queues if q["id"] != c["queue"])
            if opts:
                forms += (f"<h2>Verschieben</h2><form method=post action='{base}/move' class=inline>"
                          f"<select name=queue>{opts}</select>"
                          f"<input type=text name=text placeholder='Notiz (optional)' style='width:20rem'>"
                          f"<button>Verschieben</button></form>")
        sendable = [x for x in self.store.partners(active_only=True) if x["url"] and x["out_key"]]
        if (who.may("hand_over", c["queue"]) and "handed_over" in NEXT.get(c["status"], ())
                and sendable):
            opts = "".join(f"<option value='{esc(x['id'])}'>{esc(x['name'])}</option>"
                           for x in sendable)
            forms += (f"<h2>Übergeben</h2><form method=get action='{base}/uebergabe' class=inline>"
                      f"<select name=partner>{opts}</select><button>Entwurf öffnen</button></form>")
        got = self.store.received_from(c)
        if got and got[1] and who.may("hand_over", c["queue"]):
            forms += (f"<h2>Antwort an {esc(got[1]['name'])}</h2>"
                      f"<p class=muted>Verlässt den Mandanten. Keine Namen von Personen.</p>"
                      f"<form method=post action='{base}/antwort'>"
                      f"<textarea name=text maxlength=10000 required></textarea>"
                      f"<p><button>Antwort senden</button></p></form>")
        out = self.store.outbox_for_case(c["id"]) if who.may("read", c["queue"]) else []
        for o in out:
            state = (f"quittiert {esc(o['acked'][:16].replace('T', ' '))}" if o["acked"]
                     else f"offen, Versuch {o['attempts']}"
                     + (f" · {esc(o['last_error'])}" if o["last_error"] else ""))
            forms += (("<h2>Ausgang</h2>" if o is out[0] else "")
                      + f"<div class=muted><code>{esc(o['handover_id'][:8])}</code> · {state}</div>")
        observers = (f"<div class=muted>Beobachtet von: {esc(', '.join(qt.get(o, o) for o in c['observers']))}</div>"
                     if c["observers"] else "")
        body = (f"<div class=card><div><b>{esc(KIND_LABEL.get(c['kind'], c['kind']))}</b> · "
                f"{pill(c['status'])} · Bereich {esc(qt.get(c['queue'], c['queue']))} · "
                f"gemeldet von {esc(c['reporter_name'])}</div>{obj}{src}{observers}"
                f"<div class=body style='margin-top:.6rem'>{esc(c['body'])}</div>{techbox}</div>"
                + "<h2>Verlauf</h2>" + "".join(ent) + forms)
        return self.send(200, page(f"{c['number']}: {c['title']}", body))

    def case_post(self, who, number, action, f):
        c = self.store.get(number)
        if not c or not can_see(who, c):
            return self.send(404, page("Nicht gefunden", ""))
        if action == "comment":
            if not can_comment(who, c):
                raise Refused("Kommentieren dürfen hier nur die Meldende und der Bereich.")
            self.store.comment(c, who.id, who.name, f.get("text"))
        elif action == "status":
            new = f.get("status", "")
            if new not in next_statuses(who, c):
                raise Refused("Diesen Statuswechsel dürfen Sie hier nicht vornehmen.")
            self.store.set_status(c, who.id, who.name, new, f.get("text", ""))
        elif action == "move":
            if not who.may("move", c["queue"]):
                raise Refused("Verschieben darf nur, wer den Bereich bearbeitet.")
            self.store.move(c, who.id, who.name, f.get("queue", ""), f.get("text", ""))
        elif action == "antwort":
            if not who.may("hand_over", c["queue"]):
                raise Refused("Antworten an einen Partner darf nur das Clearing dieses Bereichs.")
            self.store.answer(c, who.id, who.name, f.get("text", ""))
            self.kick.set()
        else:
            return self.send(404, "")
        return self.redirect(f"/v/{quote(c['number'])}")


def main():
    global STORE, AUTHZ, RETENTION
    os.makedirs(DATA_DIR, exist_ok=True)
    STORE = Store(os.path.join(DATA_DIR, "vorgaenge.db"))
    RETENTION = retention_months()
    print(f"Löschfrist: {RETENTION} Monate nach Abschluss", flush=True)
    threading.Thread(target=retention_loop, args=(RETENTION,), daemon=True).start()
    threading.Thread(target=outbox_loop, args=(STORE, OUTBOX_KICK), daemon=True).start()
    AUTHZ = authz_client.Authz(app=None)
    if not AUTHZ.url:
        print("OAAP_AUTHZ_URL fehlt: nur die Admins sehen den Eingang, "
              "Bereiche lassen sich nicht vergeben.", flush=True)
    print(f"vorgaenge {VERSION} on :{PORT}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
