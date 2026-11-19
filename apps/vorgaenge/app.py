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
3. Seit 0.2.0 **Vorgaenge aus dem Backend einer App** (`/platform/cases/submit`,
   RFC-0061 §3.2) und **Uebergaben** an einen Partner-Mandanten (§5).
4. Seit 0.3.0 **Vorgaenge vom Knoten selbst** (`/platform/cases/node`,
   RFC-0069 Stufe 2): der Knoten kuendigt seinen Neustart als Vorgang an,
   schreibt ihn bei jedem Verschieben fort und schliesst ihn nach dem
   Neustart mit dem Beweis. Durch darf nur der Prinzipal
   `platform:<diese Instanz>`, dessen Schluessel das Gateway nur an dieser
   Tuer dieser Site annimmt.

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
from urllib.parse import parse_qs, quote, unquote, urlparse

import re
import urllib.error
import urllib.request

import authz_client
import mcp_door
from store import (ACCEPT_DAYS, BUTTON_KINDS, DEFAULT_QUEUE, KIND_LABEL, KINDS, MANUAL_EXCLUDED,
                   MARK_LABEL, NEXT, OPEN, QUEUE_ID, RETENTION_MONTHS, STATUS_LABEL, Refused, Store,
                   canonical)

VERSION = "0.9.0"
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


def accept_days(raw=None):
    """0.9.0 (RFC-0103 D11): nach so vielen Tagen ohne Rückmeldung schließt sich ein
    beantworteter Vorgang (Einstellung des Mandanten); ganze Tage ab 1, sonst 14."""
    raw = os.environ.get("VORGAENGE_ACCEPT_DAYS", "") if raw is None else raw
    try:
        n = int(str(raw).strip())
        if n >= 1:
            return n
    except ValueError:
        pass
    if str(raw).strip():
        print(f"VORGAENGE_ACCEPT_DAYS={raw!r} ist keine ganze Zahl ab 1 -- "
              f"es gelten {ACCEPT_DAYS} Tage.", flush=True)
    return ACCEPT_DAYS


def retention_loop(months, days=ACCEPT_DAYS):
    while True:
        try:
            done = STORE.remove_person_data(months)
            if done:
                print(f"Löschfrist: Personendaten in {len(done)} Vorgang/Vorgängen "
                      f"entfernt ({', '.join(done)})", flush=True)
        except Exception as e:      # der Lauf darf die App nie anhalten
            print(f"Löschfrist: Lauf fehlgeschlagen: {e}", flush=True)
        try:
            # 0.9.0 (D11): im selben Takt -- sechs Stunden später ist bei 14 Tagen kein Verzug
            done = STORE.close_unanswered(days)
            if done:
                print(f"Ohne Rückmeldung geschlossen: {', '.join(done)}", flush=True)
        except Exception as e:      # noqa: BLE001
            print(f"Schließen ohne Rückmeldung: Lauf fehlgeschlagen: {e}", flush=True)
        time.sleep(RETENTION_CHECK_SECONDS)

esc = html.escape
ACCEPT_DAYS_NOW = [ACCEPT_DAYS]      # die eingestellte Frist (main); eine Liste, damit Tests sie setzen
STORE = None
AUTHZ = None
RETENTION = RETENTION_MONTHS


# ------------------------------------------------------------------ Rechte

# 0.8.0 (RFC-0103 D2): was Zuständige eines Bereichs dürfen -- dieselben Tätigkeiten
# wie die Rollenvorlagen „Bearbeiter/in“ und „Clearing“ im Manifest.
MEMBER_ACTIVITIES = {"handler": {"read", "comment", "triage", "move", "close"},
                     "clearing": {"read", "comment", "triage", "move", "close", "hand_over"}}


class Who:
    def __init__(self, uid, name, roles, grants, members=None):
        self.id, self.name, self.roles, self.grants = uid, name, roles, grants
        self.members = members or {}          # {Bereich: handler|clearing}

    @property
    def admin(self):
        return "admin" in self.roles

    @property
    def tenant_admin(self):
        return "tenant_admin" in self.roles

    @property
    def manager(self):
        """Legt Bereiche an und pflegt ihre Zuständigen (RFC-0103 D1/D2)."""
        return self.admin or self.tenant_admin

    def may(self, activity, queue):
        # RFC-0103 D1: der tenant_admin bearbeitet jeden Bereich -- so stand es in
        # RFC-0061 §2.2; 0.1–0.7 prüften nur die App-Rolle admin, und nur im Eingang.
        # Übergeben (Daten verlassen den Mandanten) nicht: das bleibt beim Clearing.
        if self.tenant_admin and activity != "hand_over":
            return True
        if self.admin and queue == DEFAULT_QUEUE:
            return True
        if activity in MEMBER_ACTIVITIES.get(self.members.get(queue), ()):
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
            # 0.9.0 (D10): die meldende Person schließt mit „Lösung passt“, nicht hier --
            # sonst fehlte die Markierung accepted
            ok = who.may("close", c["queue"])
        elif s == "reopened":
            ok = who.may("triage", c["queue"]) or c["reporter"] == who.id
        else:
            ok = who.may("triage", c["queue"])
        if ok:
            out.append(s)
    return out


# ------------------------------------------------------------------ Optik

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/static/md/markdown.js": "markdown.js", "/static/md/view.js": "view.js",
          "/static/md/mermaid.min.js": "mermaid.min.js"}
_REQ = threading.local()          # wer gerade fragt -- für Kopf- und Fußzeile

LOGO = ('<svg viewBox="0 0 100 100" width="26" height="26" aria-hidden="true">'
        '<polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="none" stroke="#fff" '
        'stroke-width="7" stroke-linejoin="round"/><polygon points="50,28 69,39 69,61 50,72 31,61 31,39" '
        'fill="#93c5fd"/></svg>')

STYLE = """<link rel="stylesheet" href="/platform/theme.css">
<style>
  *{box-sizing:border-box} [hidden]{display:none!important}
  /* 0.7.0: OAAP-Design (oaap-design §5, §5.1): dunkelblaue Kopfzeile mit Marke und
     Benutzermenü, Fußzeile mit Version und den eigenen Rechten */
  body{display:flex;flex-direction:column;min-height:100vh;background:var(--oaap-bg, #f4f6fa)}
  header.oaap{display:flex;align-items:center;gap:.8rem;padding:.55rem 1rem;color:#fff;
       background:linear-gradient(90deg, var(--oaap-blue-950, #172554), var(--oaap-blue-900, #1e3a8a))}
  header.oaap .brand{display:flex;align-items:center;gap:.55rem;color:#fff;text-decoration:none}
  header.oaap .brand b{letter-spacing:.06em;font-size:.95rem} header.oaap .brand small{opacity:.8;font-size:.82rem}
  header.oaap .sp{flex:1}
  details.user{position:relative} details.user summary{list-style:none;cursor:pointer;padding:.35rem .7rem;
       border-radius:6px;border:1px solid rgba(255,255,255,.3);font-size:.88rem}
  details.user summary::-webkit-details-marker{display:none}
  details.user[open] summary{background:rgba(255,255,255,.12)}
  details.user .menu{position:absolute;right:0;top:calc(100% + .35rem);min-width:15rem;z-index:5;
       background:var(--oaap-surface, #fff);color:var(--oaap-text, #1f2937);border:1px solid var(--oaap-border, #e5e7eb);
       border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.18);padding:.6rem .7rem}
  details.user .menu a{display:block;padding:.35rem 0;color:var(--oaap-blue-600, #2563eb);text-decoration:none}
  details.user .menu form{margin:.3rem 0 0} details.user .menu button{width:100%}
  details.user .menu .hint{font-size:.78rem;color:var(--oaap-muted, #6b7280);margin:.35rem 0 0}
  footer.oaap{margin-top:auto;padding:.6rem 1rem;font-size:.8rem;color:#cbd5e1;background:var(--oaap-blue-950, #172554)}
  footer.oaap b{color:#fff;font-weight:600}
  .seg{display:inline-flex;border:1px solid var(--oaap-color-border, #d8dbe0);border-radius:6px;overflow:hidden;vertical-align:middle}
  .seg button{background:transparent;color:var(--oaap-color-text, #1a1d21);border:0;border-radius:0;padding:.25rem .65rem;font-size:.82rem}
  .seg button[aria-pressed=true]{background:var(--oaap-color-primary, #2f6fed);color:#fff}
  html[data-md-view=raw] .md-f{display:none} html:not([data-md-view=raw]) .md-raw[data-mounted]{display:none}
  .md-f > :first-child{margin-top:0} .md-f > :last-child{margin-bottom:0}
  .md-f table{width:auto} .md-f pre{background:var(--oaap-color-bg, #fff);padding:.5rem;border-radius:4px;overflow:auto}
  .md-f code{font-size:.88em} .md-f blockquote{margin:.4rem 0;padding-left:.7rem;border-left:3px solid var(--oaap-color-border, #d8dbe0)}
  .mermaid-src svg,.svg-src img{max-width:100%;height:auto;background:#fff;border-radius:4px}
  .rights{border-left:3px solid var(--oaap-color-warning, #b45309);background:var(--oaap-color-bg, #fff);padding:.55rem .8rem;margin:.8rem 0;font-size:.9rem}
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


def rights_summary(who, store):
    """Ein Satz für die Fußzeile: was diese Person hier darf."""
    if who is None:
        return ""
    try:
        queues = store.queues() if store else []
    except Exception:  # noqa: BLE001 -- die Fußzeile darf keine Seite verhindern
        queues = []
    work = [q["title"] for q in queues if who.may("triage", q["id"])]
    read = [q["title"] for q in queues if who.may("read", q["id"]) and q["title"] not in work]
    parts = []
    if work:
        parts.append("bearbeiten: " + ", ".join(work))
    if read:
        parts.append("lesen: " + ", ".join(read))
    if not parts:
        return "Du siehst deine eigenen Meldungen und kannst sie kommentieren. Bearbeiten in keinem Bereich."
    return "Deine Bereiche — " + " · ".join(parts)


def page(title, body, tab=""):
    tabs = [("/", "Übersicht", "home"), ("/neu", "Neuer Vorgang", "new"),
            ("/bereiche", "Bereiche", "queues"), ("/partner", "Partner", "partners")]
    nav = "".join(f'<a href="{h}" class="{"on" if k == tab else ""}">{esc(t)}</a>'
                  for h, t, k in tabs)
    who = getattr(_REQ, "who", None)
    user = ""
    if who is not None:
        # oaap-design §5.1: dasselbe Benutzermenü wie im Portal. Der Name kommt vom
        # Gateway; /auth/whoami sagt, ob es ein Passwort gibt. Abmelden ist ein POST.
        user = (f'<details class=user><summary id=uname>{esc(who.name)}</summary><div class=menu>'
                f'<div class=muted id=ulogin></div>'
                f'<a id=upw href="/auth/password" hidden>Passwort ändern</a>'
                f'<form method=post action="/auth/logout"><button>Abmelden</button></form>'
                f'<p class=hint>Als andere Person weiter: abmelden und neu anmelden.</p></div></details>')
    foot = rights_summary(who, getattr(_REQ, "store", None))
    return (f"<!doctype html><html lang=de><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)} · Vorgänge</title>{STYLE}"
            f'<script src="/platform/feedback.js" defer></script>'
            f'<script src="/static/md/markdown.js" defer></script>'
            f'<script src="/static/md/view.js" defer></script></head>'
            f'<body><header class=oaap><a class=brand href="/">{LOGO}<span><b>OAAP</b> '
            f'<small>Vorgänge</small></span></a><span class=sp></span>{user}</header>'
            f"<main><h1>{esc(title)}</h1><nav class=tabs>{nav}</nav>{body}</main>"
            f"<footer class=oaap><b>Vorgänge {esc(VERSION)}</b>"
            + (f" · {esc(foot)}" if foot else "") + "</footer>"
            + """<script>
addEventListener("DOMContentLoaded", function () {
  if (window.OAAPView) { OAAPView.mode(); OAAPView.mount(document); }
  document.querySelectorAll("[data-md-mode]").forEach(function (b) {
    b.onclick = function () { OAAPView.mode(b.getAttribute("data-md-mode")); };
  });
  fetch("/auth/whoami", {headers: {"Accept": "application/json"}})
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (me) {
      var n = document.getElementById("uname"); if (!n) return;
      if (me.display_name || me.username) n.textContent = me.display_name || me.username;
      document.getElementById("ulogin").textContent = me.username ? "angemeldet als " + me.username : "";
      if (me.links && me.links.password) document.getElementById("upw").hidden = false;
    }).catch(function () {});
});
</script></body></html>""")


def pill(status, mark=""):
    word = STATUS_LABEL.get(status, status)
    if status == "closed" and mark in MARK_LABEL:
        word += f" ({MARK_LABEL[mark]})"
    return f'<span class=pill>{esc(word)}</span>'


def case_url(number):
    """0.9.0 (RFC-0103 D7): die Adresse des Vorgangs, aus dem ersten öffentlichen Namen
    dieser Instanz -- ohne Schema (`//host/v/N`): der Browser nimmt das der Seite, auf
    der der Link steht (hinter einem Edge nennt OAAP_INSTANCE_NAMES `http`, der Browser
    sieht `https`). Ohne öffentlichen Namen: leer, der Chat zeigt dann keinen Link."""
    first = (os.environ.get("OAAP_INSTANCE_NAMES", "").split(",")[0] or "").strip()
    host = urlparse(first).netloc if "://" in first else first
    if not host or "/" in host:
        return ""
    return f"//{host}/v/{quote(number)}"


def mine_row(r):
    """Was /platform/feedback/mine je eigenem Vorgang sagt -- nur Eigenes, nichts Internes."""
    st, mark = r["status"], r.get("mark") or ""
    return {"status": st, "label": STATUS_LABEL.get(st, st), "mark": mark if st == "closed" else "",
            "mark_label": MARK_LABEL.get(mark, "") if st == "closed" else "",
            "answered": st in ("answered", "closed"), "title": r.get("title", ""),
            "updated": r.get("updated", ""), "url": case_url(r["number"]),
            "object": ({"kind": r["object_kind"], "id": r["object_id"]} if r.get("object_id") else None)}


def case_table(rows, queues):
    if not rows:
        return "<p class=muted>Keine Vorgänge.</p>"
    qt = {q["id"]: q["title"] for q in queues}
    tr = "".join(
        f"<tr><td><a href='/v/{quote(r['number'])}'>{esc(r['number'])}</a></td>"
        f"<td>{esc(KIND_LABEL.get(r['kind'], r['kind']))}</td>"
        f"<td>{esc(r['title'])}</td><td>{pill(r['status'], r.get('mark', ''))}</td>"
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
        # 0.7.0: Der Anzeigename kommt prozentkodiert, sobald er mehr als ASCII enthält
        # (oaap.core.identity, RFC-0040 D4: immer dekodieren) -- bis 0.6.0 stand
        # „J%C3%B6rg%20M%C3%BCller“ im Vorgang.
        name = (unquote(self.headers.get("X-OAAP-Display-Name", "")).strip()
                or self.headers.get("X-OAAP-User", "").strip() or uid)
        try:
            self.store.see_person(uid, name, self.headers.get("X-OAAP-User", "").strip())
            members = self.store.memberships(uid)
        except Exception:  # noqa: BLE001 -- ohne Ablage keine Zuständigkeit, aber eine Seite
            members = {}
        who = Who(uid, name, roles, self.authz.grants(uid), members)
        _REQ.who, _REQ.store = who, self.store
        return who

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
        _REQ.who = _REQ.store = None
        u = urlparse(self.path)
        p = u.path
        if p == "/healthz":
            return self.send(200, "ok", "text/plain")
        if p in STATIC:
            # 0.7.0: Markdown, Mermaid, SVG -- Kopie aus lib/mdview, nur diese Namen
            with open(os.path.join(HERE, "static", "md", STATIC[p]), "rb") as fh:
                return self.send(200, fh.read(), "application/javascript; charset=utf-8")
        if p == "/platform/feedback/session":
            return self.feedback_session()
        if p == "/platform/feedback/mine":
            return self.feedback_mine(parse_qs(u.query))
        if p == "/mcp":
            return self.send(405, "", "text/plain")
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
        _REQ.who = _REQ.store = None
        p = urlparse(self.path).path
        if not self.read_body():
            return self.send(413, "")
        if p == "/platform/feedback":
            return self.feedback_post()
        if p.startswith("/platform/feedback/mine/"):
            return self.feedback_mine_post(p[len("/platform/feedback/mine/"):])
        if p == "/platform/cases/submit":
            return self.backend_submit()
        if p == "/platform/cases/node":
            return self.node_post()
        if p == "/handover":
            return self.handover_in()
        if self.is_partner():
            return self.json(403, {"error": "Dieser Zugang liefert Übergaben, sonst nichts."})
        who = self.who()
        if not who:
            return self.send(401, "")
        if p == "/mcp":
            return self.mcp_post(who)
        if not self.same_origin():
            return self.send(403, page("Abgelehnt", "<p>Dieses Formular kam nicht von dieser Seite.</p>"))
        try:
            f = self.form()
            if p == "/neu":
                return self.new_post(who, f)
            if p == "/bereiche":
                return self.queues_post(who, f)
            if p == "/bereiche/mitglied":
                return self.member_post(who, f)
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

    # --- die MCP-Tür (0.4.0, RFC-0073): dieselbe Person, dieselben Regeln wie die Seite
    def mcp_post(self, who):
        # Nur JSON: ein fremdes Formular aus dem Browser (ohne CORS-Vorabfrage) kommt
        # so nicht durch; die Tür der Plattform schickt immer application/json.
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.json(415, {"error": "MCP spricht JSON."})
        try:
            msg = json.loads(self.body() or b"{}")
        except ValueError:
            return self.json(400, {"jsonrpc": "2.0", "id": None,
                                   "error": {"code": -32700, "message": "Parse error"}})
        out = mcp_door.answer(self.store, who, MCP_RULES, msg)
        if out is None:
            return self.send(202, "", "text/plain")
        return self.json(200, out)

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
        return self.json(200, {"ok": True, "kinds": list(BUTTON_KINDS), "fields": True})

    def feedback_mine(self, q):
        """0.6.0 (RFC-0101 D11): der Status eigener Vorgänge für die Seite einer App --
        damit der Chat ein Gespräch als beantwortet markieren kann. Nur, was die Person
        selbst gemeldet hat; eine fremde Nummer fehlt in der Antwort wie eine unbekannte."""
        src = self.attested()
        uid = self.headers.get("X-OAAP-User-Id", "").strip()
        if not src or not uid:
            return self.json(404, {"ok": False})
        # 0.9.0 (RFC-0103 D7/D8): je Vorgang auch Markierung, Bezug und die Adresse; ohne
        # `numbers` alle eigenen (höchstens 200, neueste zuerst) -- ein Aufruf färbt den Baum
        if "numbers" in q:
            nums = [n.strip() for n in ",".join(q.get("numbers") or []).split(",") if n.strip()]
            rows = list(self.store.status_of(nums, uid).values())
        else:
            rows = self.store.mine(uid)
        return self.json(200, {"cases": {r["number"]: mine_row(r) for r in rows},
                               "accept_days": ACCEPT_DAYS_NOW[0]})

    def feedback_mine_post(self, rest):
        """0.9.0 (RFC-0103 D10, Köder 6): „Lösung passt“ / „Passt nicht“ aus der Seite einer
        App (dem Chat), als die Person, die das Gateway bezeugt. Nur die meldende Person; für
        alle anderen gibt es den Vorgang auf diesem Weg nicht (sie nehmen die Seite)."""
        src = self.attested()
        who = self.who()
        if not src or not who:
            return self.json(404, {"ok": False})
        if not self.same_origin():
            return self.json(403, {"error": "Das kam nicht von der Seite der App."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        number, _, action = rest.partition("/")
        c = self.store.get(number) if action in ("accept", "reopen") else None
        if not c or c["reporter"] != who.id:
            return self.json(404, {"error": "Diesen Vorgang hast du nicht gemeldet."})
        try:
            doc = json.loads(self.body().decode("utf-8") or "{}")
            if not isinstance(doc, dict):
                raise Refused("JSON-Objekt erwartet.")
            if action == "accept":
                self.store.accept(c, who.id, who.name)
            else:
                self.store.not_fitting(c, who.id, who.name, doc.get("text"))
        except (Refused, ValueError) as e:
            return self.json(409 if isinstance(e, Refused) else 400,
                             {"error": str(e) if isinstance(e, Refused) else "Ungültig."})
        got = self.store.status_of([number], who.id)
        return self.json(200, {"ok": True, "case": mine_row(got[number])})

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
            # 0.6.0: ein Bereich, den die Seite nennt (der Chat: support_queue des
            # Assistenten, RFC-0101 D6); unbekannt oder ungültig -> Eingang
            queue = doc.get("queue")
            queue = queue if isinstance(queue, str) and QUEUE_ID.fullmatch(queue) else DEFAULT_QUEUE
            made = self.store.create(kind=doc.get("kind"), title=doc.get("title"),
                                body=doc.get("body") or "", reporter=who.id,
                                reporter_name=who.name, obj=doc.get("object"),
                                attested=attested, browser=doc.get("browser"),
                                allowed_kinds=BUTTON_KINDS, queue=queue,
                                subkind=doc.get("subkind"), fields=doc.get("fields"))
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

    # --- Vorgaenge vom Knoten selbst (RFC-0069 Stufe 2)
    def node_post(self):
        """Der Knoten meldet seinen Neustart-Plan: `open` legt den Vorgang
        an (mit `ref` als Bezug; dieselbe `ref` ein zweites Mal gibt den
        vorhandenen zurueck), `note` schreibt ihn fort, `close` schliesst
        ihn mit dem Beweis. Durch darf nur `platform:<diese Instanz>` --
        der Prinzipal, den die Plattform fuer genau diesen Posteingang
        gepraegt hat; sein Schluessel gilt beim Gateway nur an dieser Tuer
        dieser Site, und die Herkunft sagt, dass es diese Site war. Eine
        Sitzung, die den Pfad erreicht, ist kein Knoten."""
        src = self.attested()
        principal = self.headers.get("X-OAAP-User", "").strip()
        if not src or principal != f"platform:{src['instance']}":
            return self.json(403, {"error": "Nur der Knoten selbst, mit dem Schlüssel "
                                            "dieses Posteingangs."})
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.json(415, {"error": "JSON erwartet."})
        try:
            doc = json.loads(self.body().decode("utf-8") or "{}")
            if not isinstance(doc, dict):
                raise Refused("JSON-Objekt erwartet.")
            op = str(doc.get("op") or "")
            node = str(doc.get("node") or "").strip()[:80]
            name = f"Knoten {node}" if node else "Knoten"
            if op == "open":
                if self.store.submissions_by(src["instance"], 3600) >= SUBMISSIONS_PER_HOUR:
                    return self.json(429, {"error": "Zu viele Vorgänge des Knotens in der "
                                                    "letzten Stunde."})
                ref = str(doc.get("ref") or "").strip()[:200]
                if not ref:
                    raise Refused("ref fehlt: der Bezug des Plans.")
                have = self.store.open_by_object("reboot", ref)
                if have:
                    return self.json(200, {"ok": True, "number": have["number"],
                                           "id": have["id"], "existing": True})
                made = self.store.create(
                    kind="internal", title=doc.get("title"), body=doc.get("body") or "",
                    reporter=principal, reporter_name=name,
                    queue=str(doc.get("queue") or DEFAULT_QUEUE),
                    obj={"kind": "reboot", "id": ref},
                    attested=dict(src, submitted_by=principal, node=node))
                print(f"case {made['number']} from node {node}", flush=True)
                return self.json(201, {"ok": True, "number": made["number"], "id": made["id"]})
            c = self.store.get(str(doc.get("number") or ""))
            if not c or c["reporter"] != principal:
                return self.json(404, {"error": "Diesen Vorgang hat der Knoten nicht angelegt."})
            if op == "note":
                self.store.comment(c, principal, name, doc.get("text"))
                return self.json(200, {"ok": True, "number": c["number"]})
            if op == "close":
                closed = self.store.close_by_node(c, principal, name, doc.get("text") or "")
                return self.json(200, {"ok": True, "number": c["number"], "closed": closed})
            raise Refused("op: open, note oder close.")
        except (Refused, ValueError) as e:
            return self.json(400, {"error": str(e) if isinstance(e, Refused) else "Ungültige Meldung."})

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
        kind_word = {"handler": "Bearbeiter/in", "clearing": "Clearing"}
        people = self.store.people() if who.manager else []
        rows = []
        for q in queues:
            mem = self.store.members(q["id"])
            names = ", ".join(f"{esc(m['name'])} ({kind_word.get(m['kind'], m['kind'])})" for m in mem) or \
                '<span class=muted>noch niemand</span>'
            mine = ("bearbeiten" if who.may("triage", q["id"]) else "lesen" if who.may("read", q["id"]) else "")
            row = (f"<tr><td><code>{esc(q['id'])}</code></td><td>{esc(q['title'])}</td>"
                   f"<td>{names}</td><td>{mine}</td></tr>")
            if who.manager:
                opts = "".join(f"<option value='{esc(p['uid'])}'>{esc(p['name'])}"
                               f"{' (' + esc(p['username']) + ')' if p['username'] and p['username'] != p['name'] else ''}"
                               f"</option>" for p in people)
                rm = "".join(
                    f"<form method=post action=/bereiche/mitglied class=inline style='margin:.2rem 0'>"
                    f"<input type=hidden name=queue value='{esc(q['id'])}'><input type=hidden name=uid value='{esc(m['uid'])}'>"
                    f"<input type=hidden name=op value=remove><span>{esc(m['name'])}</span>"
                    f"<button style='background:transparent;color:var(--oaap-color-danger,#c4342f);"
                    f"border:1px solid currentColor;padding:.1rem .5rem'>entfernen</button></form>" for m in mem)
                row += (f"<tr><td></td><td colspan=3><details><summary class=muted>Zuständige pflegen</summary>{rm}"
                        f"<form method=post action=/bereiche/mitglied class=inline>"
                        f"<input type=hidden name=queue value='{esc(q['id'])}'><input type=hidden name=op value=set>"
                        f"<div><label>Person</label><select name=uid required>{opts}</select></div>"
                        f"<div><label>als</label><select name=kind><option value=handler>Bearbeiter/in</option>"
                        f"<option value=clearing>Clearing (darf auch übergeben)</option></select></div>"
                        f"<button>Eintragen</button></form>"
                        f"<p class=muted>Fehlt jemand? Die Person öffnet die App Vorgänge einmal, danach steht "
                        f"sie hier. Ein Verzeichnis aller Benutzer bekommt diese App nicht.</p></details></td></tr>")
            rows.append(row)
        rows = "".join(rows)
        body = (f"<p>Ein Bereich ist eine Zuständigkeit: ein Thema oder ein Team. Vorgänge kommen im "
                f"<b>Eingang</b> an und werden von dort in den passenden Bereich verschoben.</p>"
                f"<p>Wer einen Bereich bearbeitet, trägt der <code>tenant_admin</code> hier ein. "
                f"<b>Bearbeiter/innen</b> lesen, kommentieren, ändern den Status, verschieben und "
                f"schließen; das <b>Clearing</b> darf zusätzlich an einen Partner-Mandanten übergeben. "
                f"Der <code>tenant_admin</code> bearbeitet jeden Bereich (übergeben nur als Clearing). "
                f"In großen Mandanten geht dasselbe über <b>Rollen &amp; Rechte</b> (Vorlagen "
                f"<i>Bearbeiter/in</i>, <i>Clearing</i>, Kontext = Kennung des Bereichs); beides zählt.</p>"
                f"<p class=muted>Löschfrist: {RETENTION} Monate nach dem Schließen oder Ablehnen "
                f"wird die meldende Person aus einem Vorgang entfernt "
                f"(Einstellung <code>VORGAENGE_RETENTION_MONTHS</code> dieser Instanz).</p>"
                f"<table><tr><th>Kennung</th><th>Titel</th><th>Zuständige</th><th>Ich darf</th></tr>{rows}</table>")
        if who.manager:
            log = self.store.queue_log(15)
            if log:
                body += ("<h2>Zuletzt geändert</h2><table>" + "".join(
                    f"<tr><td class=muted>{esc(x['at'][:16].replace('T', ' '))}</td><td>{esc(x['queue'])}</td>"
                    f"<td>{esc(x['name'])} {'eingetragen als ' + kind_word.get(x['kind'], x['kind']) if x['action'] == 'set' else 'entfernt'}</td>"
                    f"<td class=muted>von {esc(x['actor_name'])}</td></tr>" for x in log) + "</table>")
            body += """<h2>Bereich anlegen</h2>
<form method=post action=/bereiche class=inline>
 <div><label for=i>Kennung</label><input id=i name=id pattern='[a-z0-9][a-z0-9-]{0,39}' required></div>
 <div><label for=t>Titel</label><input id=t name=title maxlength=80 required></div>
 <button>Anlegen</button></form>"""
        return self.send(200, page("Bereiche", body, "queues"))

    def queues_post(self, who, f):
        if not who.manager:
            raise Refused("Bereiche legt der tenant_admin an.")
        self.store.add_queue(f.get("id"), f.get("title"))
        return self.redirect("/bereiche")

    def member_post(self, who, f):
        """0.8.0 (RFC-0103 D2): Zuständige pflegt nur der tenant_admin (oder admin) --
        Zuständige tragen keine weiteren ein."""
        if not who.manager:
            raise Refused("Zuständige eines Bereichs trägt der tenant_admin ein.")
        queue, uid = f.get("queue", ""), f.get("uid", "")
        if f.get("op") == "remove":
            self.store.remove_member(queue, uid, who.id)
        else:
            self.store.set_member(queue, uid, f.get("kind", ""), who.id)
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
        if att.get("node"):
            src = (f"<div class=muted>Gemeldet vom Knoten <b>{esc(att['node'])}</b> "
                   "(Neustart-Plan der Plattform, vom Gateway beglaubigt)</div>")
        elif att.get("instance"):
            src = (f"<div class=muted>Gemeldet aus <b>{esc(att.get('app', ''))}</b> "
                   f"{esc(att.get('version', ''))}, Instanz <code>{esc(att['instance'])}</code> "
                   f"(vom Gateway beglaubigt)</div>")
        techbox = ""
        if who.may("read", c["queue"]) and tech.get("browser"):
            techbox = ("<details><summary class=muted>Technische Angaben aus dem Browser</summary><pre>"
                       + esc("\n".join(f"{k}: {v}" for k, v in tech["browser"].items()))
                       + "</pre></details>")
        if tech.get("subkind"):
            # 0.6.0: was die Seite der App mitgab -- nicht beglaubigt, und so beschriftet
            rows = "".join(
                f"<tr><th style='text-align:left;vertical-align:top;padding-right:.8rem'>{esc(k)}</th>"
                f"<td><pre style='margin:0;white-space:pre-wrap'>"
                f"{esc(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, indent=1))}</pre></td></tr>"
                for k, v in (tech.get("fields") or {}).items())
            techbox += (f"<details open><summary class=muted>Angaben der App "
                        f"(Unterart <code>{esc(tech['subkind'])}</code>, von der Seite mitgegeben, "
                        f"nicht beglaubigt)</summary><table>{rows}</table></details>")
        ent = []
        for e in c["entries"]:
            when = esc(e["at"][:16].replace("T", " "))
            who_ = esc(e["actor_name"])
            d = e["data"]
            if e["type"] == "created":
                head = f"angelegt in {esc(qt.get(d.get('queue'), d.get('queue', '')))}"
            elif e["type"] == "status":
                head = (f"Status {esc(STATUS_LABEL.get(d.get('from'), d.get('from', '')))} → "
                        f"{esc(STATUS_LABEL.get(d.get('to'), d.get('to', '')))}"
                        + (f" ({esc(MARK_LABEL[d['mark']])})" if d.get("mark") in MARK_LABEL else "")
                        + (" (passt nicht)" if d.get("mark") == "not_fitting" else ""))
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
            text = f"<div class=body data-md>{esc(e['text'])}</div>" if e["text"] else ""
            ent.append(f"<div class=card><div class=muted>{when} · {who_} · {head}</div>{text}</div>")
        forms = ""
        base = f"/v/{quote(c['number'])}"
        if c["reporter"] == who.id and c["status"] == "answered":
            # 0.9.0 (RFC-0103 D10): abnehmen oder wieder öffnen -- dieselben Regeln wie im Chat
            forms += (f"<div class=card><h2 style='margin-top:0'>Passt die Lösung?</h2>"
                      f"<form method=post action='{base}/accept' class=inline>"
                      f"<button>Lösung passt</button></form>"
                      f"<form method=post action='{base}/reopen'>"
                      f"<label>Passt nicht — was fehlt noch?</label>"
                      f"<textarea name=text maxlength=10000 required></textarea>"
                      f"<p><button>Passt nicht, wieder öffnen</button></p></form>"
                      f"<p class=muted>Ohne Rückmeldung schließt sich der Vorgang nach "
                      f"{ACCEPT_DAYS_NOW[0]} Tagen von selbst.</p></div>")
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
        # 0.7.0: Wer hier nichts umstellen kann, erfährt warum -- statt eines fehlenden Formulars.
        rights = ""
        if not nxt and c["status"] not in ("closed", "rejected") and not (
                c["reporter"] == who.id and c["status"] == "answered"):
            how = (f"Im Bereich <b>{esc(qt.get(c['queue'], c['queue']))}</b> stellen ihn die "
                   "Zuständigen dieses Bereichs um (Seite „Bereiche“) und der "
                   "<code>tenant_admin</code>"
                   + (", im Eingang auch die App-Rolle <code>admin</code>" if c["queue"] == DEFAULT_QUEUE else "")
                   + ". In großen Mandanten vergibt „Rollen &amp; Rechte“ dasselbe mit den Vorlagen "
                   "<i>Bearbeiter/in</i> und <i>Clearing</i>.")
            mine = (" Als Meldende kannst du kommentieren und, sobald eine Antwort da ist, sagen, "
                    "ob die Lösung passt." if c["reporter"] == who.id else "")
            rights = f"<div class=rights>Den Status kannst du hier nicht ändern. {how}{mine}</div>"
        toggle = ('<div style="float:right" class=seg role=group aria-label="Darstellung">'
                  '<button type=button data-md-mode=formatted aria-pressed=true>Formatiert</button>'
                  '<button type=button data-md-mode=raw aria-pressed=false>Roh</button></div>')
        body = (f"<div class=card>{toggle}<div><b>{esc(KIND_LABEL.get(c['kind'], c['kind']))}</b> · "
                f"{pill(c['status'], c.get('mark', ''))} · Bereich {esc(qt.get(c['queue'], c['queue']))} · "
                f"gemeldet von {esc(c['reporter_name'])}</div>{obj}{src}{observers}"
                f"<div class=body data-md style='margin-top:.6rem'>{esc(c['body'])}</div>"
                f"{techbox}</div>"
                + "<h2>Verlauf</h2>" + "".join(ent) + rights + forms)
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
        elif action == "accept":
            self.store.accept(c, who.id, who.name)
        elif action == "reopen":
            self.store.not_fitting(c, who.id, who.name, f.get("text", ""))
        elif action == "antwort":
            if not who.may("hand_over", c["queue"]):
                raise Refused("Antworten an einen Partner darf nur das Clearing dieses Bereichs.")
            self.store.answer(c, who.id, who.name, f.get("text", ""))
            self.kick.set()
        else:
            return self.send(404, "")
        return self.redirect(f"/v/{quote(c['number'])}")


MCP_RULES = mcp_door.Rules(
    can_see=can_see, can_comment=can_comment, refused=Refused, kinds=KINDS,
    kind_label=KIND_LABEL, status_label=STATUS_LABEL, default_queue=DEFAULT_QUEUE,
    open_statuses=OPEN,
    attest=lambda who: {"app": APP_ID, "version": VERSION, "user_id": who.id, "via": "mcp"})


def main():
    global STORE, AUTHZ, RETENTION
    os.makedirs(DATA_DIR, exist_ok=True)
    STORE = Store(os.path.join(DATA_DIR, "vorgaenge.db"))
    RETENTION = retention_months()
    ACCEPT_DAYS_NOW[0] = accept_days()
    print(f"Löschfrist: {RETENTION} Monate nach Abschluss; beantwortet ohne "
          f"Rückmeldung geschlossen nach {ACCEPT_DAYS_NOW[0]} Tagen", flush=True)
    threading.Thread(target=retention_loop, args=(RETENTION, ACCEPT_DAYS_NOW[0]), daemon=True).start()
    threading.Thread(target=outbox_loop, args=(STORE, OUTBOX_KICK), daemon=True).start()
    AUTHZ = authz_client.Authz(app=None)
    if not AUTHZ.url:
        print("OAAP_AUTHZ_URL fehlt: nur die Admins sehen den Eingang, "
              "Bereiche lassen sich nicht vergeben.", flush=True)
    print(f"vorgaenge {VERSION} on :{PORT}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
