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
6. Seit 0.21.0 **Lieferungen durch ein Tor** (`/platform/cases/gate`,
   RFC-0135 Stufe 1): ein anderes System (SAP per SM59) liefert an
   `/gate/<name>` auf dem Ort des Mandanten; das Gateway laesst nur einen
   Zugang dieses Tors durch und sagt in Kopfzeilen, die kein Client setzen
   kann, welches Tor, welcher Zugang, welche Felder und welcher Bereich.
   Geprueft wird hier gegen dieselbe Feldliste, aus der die Vorlagen kommen
   (`gates.py`); dieselbe `ref` zweimal ist ein Vorgang, und
   `GET /gate/<name>/<ref>` liest seinen Stand.
5. Seit 0.12.0 **Anfragen eines Mandanten an den Betreiber** durch dieselbe
   Tuer (RFC-0109 §13): `object: request`, `from` nennt Mandant und Person;
   `op: read` gibt dem Knoten Status und Antworten zurueck (nie Internes).

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
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

import re
import urllib.error
import urllib.request

import appframe as af
import authz_client
# 0.21.0 (RFC-0135): Feldliste und Pruefung eines Tors -- Kopie aus oaap-reference
import gates
import mcp_door
from store import (ACCEPT_DAYS, BUTTON_KINDS, DEFAULT_QUEUE, KIND_LABEL, KINDS, MANUAL_EXCLUDED,
                   MARK_LABEL, NEXT, OPEN, QUEUE_ID, RETENTION_MONTHS, REVIEW_REASON, REVIEW_RESULT,
                   STATUS_LABEL, SUBSTATUS, TASKS_MAX, Refused, Store, canonical)

VERSION = "0.23.1"
APP_ID = "vorgaenge"
PORT = 8000
DATA_DIR = os.environ.get("VORGAENGE_DATA_DIR", "/data")

# RFC-0061 §10.2: Drosselung je Person. Das Gateway drosselt nur oeffentliche
# Routen; diese ist angemeldet, also zaehlt die App selbst.
REPORTS_PER_HOUR = 20
# §3.2: Vorgaenge aus dem Backend einer App, je meldende Instanz und Stunde
SUBMISSIONS_PER_HOUR = 600
# 0.12.0: was der Knoten an seiner Tür anlegen darf -- seinen Neustart-Plan
# (RFC-0069) und die Anfrage eines Mandanten an den Betreiber (RFC-0109 §13)
NODE_OBJECTS = ("reboot", "request")
NODE_READ_ENTRIES = 20
NODE_READ_TEXT = 2000


def node_reading(c, principal):
    """Status und Antworten eines Vorgangs des Knotens, für den Knoten."""
    replies = []
    for e in c.get("entries") or []:
        if e.get("internal") or e.get("actor") == principal:
            continue
        text = (e.get("text") or "").strip()
        data = e.get("data") or {}
        if e.get("type") == "status":
            to = data.get("to", "")
            replies.append({"at": e.get("at", ""), "by": e.get("actor_name", ""),
                            "status": to, "status_label": STATUS_LABEL.get(to, to),
                            "text": text[:NODE_READ_TEXT]})
        elif e.get("type") == "comment" and text:
            replies.append({"at": e.get("at", ""), "by": e.get("actor_name", ""),
                            "text": text[:NODE_READ_TEXT]})
    return {"ok": True, "number": c["number"], "status": c["status"],
            "status_label": STATUS_LABEL.get(c["status"], c["status"]),
            "updated": c.get("updated", ""), "replies": replies[-NODE_READ_ENTRIES:]}
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


# RFC-0127 Stufe 1.5: der Status eines Vorgangs aus dem Backend einer App geht
# an diese App zurück -- als adressiertes Ereignis über die Tür der Plattform
# (OAAP_EVENTS_URL, OAAP_EVENTS_KEY). Ohne Schlüssel: keine Ereignisse hier.
EVENTS_URL = os.environ.get("OAAP_EVENTS_URL", "")
EVENTS_KEY = os.environ.get("OAAP_EVENTS_KEY", "")
EVENTS_SECONDS = 2


def publish_event(raw):
    """(outcome, Fehlertext): 'sent' bei 202; 'refused' bei einer endgültigen
    Absage der Plattform (4xx außer 429); sonst 'retry'."""
    req = urllib.request.Request(
        EVENTS_URL.rstrip("/") + "/", data=raw.encode("utf-8"), method="POST",
        headers={"Authorization": "Bearer " + EVENTS_KEY,
                 "Content-Type": "application/cloudevents+json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return ("sent", "") if r.status == 202 else ("retry", f"HTTP {r.status}")
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read() or b"{}").get("error", "")
        except ValueError:
            detail = ""
        final = 400 <= e.code < 500 and e.code != 429
        return ("refused" if final else "retry"), f"HTTP {e.code} {detail}".strip()
    except (OSError, ValueError) as e:
        return "retry", str(e)


def flush_events(store, send=None):
    send = send or publish_event
    for row in store.event_outbox_due():
        outcome, err = send(row["doc"])
        store.event_outbox_result(row["seq"], outcome, err)
        if outcome != "sent":
            print(f"Ereignis {row['seq']} an {row['addressee']}: {outcome} {err}", flush=True)


def events_loop(store):
    while True:
        try:
            flush_events(store)
        except Exception as e:      # die Ereignisse dürfen die App nie anhalten
            print(f"Ereignisse: Lauf fehlgeschlagen: {e}", flush=True)
        time.sleep(EVENTS_SECONDS)


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


def tasks_max(raw=None):
    """0.22.0 (RFC-0134 §5): Aufträge eines Leiters je Vorgang (Einstellung des Mandanten),
    1 bis 8; sonst 8. Ein Leiter kann in der App Spezialisten nur weniger einstellen."""
    raw = os.environ.get("VORGAENGE_TASKS_MAX", "") if raw is None else raw
    try:
        n = int(str(raw).strip())
        if 1 <= n <= TASKS_MAX:
            return n
    except ValueError:
        pass
    return TASKS_MAX


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


def is_assignee(who, c):
    """0.10.0 (RFC-0103 D3): jetzt zugewiesen -- liest, kommentiert, ändert den Status;
    verschiebt, schließt, übergibt nicht und weist nicht weiter zu."""
    return bool(who.id) and c.get("assignee") == who.id


def was_involved(who, c):
    """Je verantwortlich oder zugewiesen (auch jetzt): liest weiter, interne Kommentare
    eingeschlossen -- ändern nur, solange die Rolle besteht."""
    return bool(who.id) and who.id in (c.get("involved") or ())


def handles(who, c):
    """Die Bearbeitenden: Bereich, Beobachter, je Beteiligte (RFC-0103 §4, die Leser
    interner Kommentare). Die meldende Person gehört nicht dazu -- es sei denn, sie ist
    selbst eine von ihnen."""
    return (who.may("read", c["queue"]) or any(who.may("read", o) for o in c.get("observers") or ())
            or was_involved(who, c))


def can_see(who, c):
    return c["reporter"] == who.id or handles(who, c)


def can_read_internal(who, c):
    """D5: interne Einträge -- nie für die meldende Person als solche."""
    return handles(who, c)


def can_comment(who, c):
    return (c["reporter"] == who.id or who.may("comment", c["queue"])
            or any(who.may("read", o) for o in c.get("observers") or ())
            or is_assignee(who, c))


def can_set_roles(who, c):
    """Verantwortlich und zugewiesen setzen: Zuständige des Bereichs und der
    tenant_admin -- nicht die zugewiesene Person (keine Ketten, Köder 3)."""
    return who.may("triage", c["queue"])


def visible_entries(who, c):
    internal = can_read_internal(who, c)
    return [e for e in c["entries"] if internal or not e.get("internal")]


def next_statuses(who, c):
    """Was diese Person aus dem jetzigen Status heraus anbieten darf."""
    out = []
    assigned = is_assignee(who, c)
    for s in NEXT.get(c["status"], ()):
        if s in MANUAL_EXCLUDED:
            continue
        if s == "closed":
            # 0.9.0 (D10): die meldende Person schließt mit „Lösung passt“, nicht hier --
            # sonst fehlte die Markierung accepted. 0.10.0: die Zugewiesene auch nicht.
            ok = who.may("close", c["queue"])
        elif s == "reopened":
            ok = who.may("triage", c["queue"]) or c["reporter"] == who.id or assigned
        elif s == "rejected":
            # 0.10.0: Ablehnen beendet den Vorgang wie Schließen -- nicht für Zugewiesene
            ok = who.may("triage", c["queue"])
        else:
            ok = who.may("triage", c["queue"]) or assigned
        if ok:
            out.append(s)
    return out


# ------------------------------------------------------------------ Optik

HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = {"/static/md/markdown.js": "markdown.js", "/static/md/view.js": "view.js",
          "/static/md/mermaid.min.js": "mermaid.min.js"}
_REQ = threading.local()          # wer gerade fragt -- für Reiter und Rechte-Hinweis

APP_NAME = "Vorgänge"

# Nur, was dieser App eigen ist -- Kopfleiste, Reiter, Fußzeile, Tabellen, Knöpfe und
# Dunkelmodus kommen aus dem Kit der Plattform (/platform/theme.css, RFC-0118).
STYLE = """
  [hidden]{display:none!important}
  h1{font-size:1.3rem;margin:0 0 1rem} h2{font-size:1.02rem;margin:1.4rem 0 .6rem}
  .seg{display:inline-flex;border:1px solid var(--oaap-color-border);border-radius:6px;overflow:hidden;vertical-align:middle}
  .oaap-app .seg button{background:transparent;color:var(--oaap-color-text);border:0;border-radius:0;padding:.25rem .65rem;font-size:.82rem;min-height:0}
  .oaap-app .seg button[aria-pressed=true]{background:var(--oaap-color-primary);color:var(--oaap-color-primary-text)}
  html[data-md-view=raw] .md-f{display:none} html:not([data-md-view=raw]) .md-raw[data-mounted]{display:none}
  .md-f > :first-child{margin-top:0} .md-f > :last-child{margin-bottom:0}
  .md-f table{width:auto} .md-f pre{background:var(--oaap-color-bg);padding:.5rem;border-radius:4px;overflow:auto}
  .md-f code{font-size:.88em} .md-f blockquote{margin:.4rem 0;padding-left:.7rem;border-left:3px solid var(--oaap-color-border)}
  .mermaid-src svg,.svg-src img{max-width:100%;height:auto;background:var(--oaap-color-surface);border-radius:4px}
  .rights{border-left:3px solid var(--oaap-color-warning);background:var(--oaap-color-surface);padding:.55rem .8rem;margin:.8rem 0;font-size:.9rem}
  .card{background:var(--oaap-color-surface);border:1px solid var(--oaap-color-border);border-radius:var(--oaap-radius);padding:.8rem 1rem;margin:.6rem 0}
  .card.c-ki{border-left:4px solid var(--oaap-badge-info)} .card.c-rat{border-left:4px solid var(--oaap-color-success)}
  .card.c-int{border-left:4px solid var(--oaap-color-warning)}
  .muted{color:var(--oaap-color-text-muted);font-size:.88rem}
  .err{color:var(--oaap-color-danger)} .ok{color:var(--oaap-color-success)}
  textarea{width:100%;min-height:6rem} input[type=text]{width:100%}
  pre{white-space:pre-wrap;word-break:break-word;margin:.3rem 0;font-size:.85rem}
  .body{white-space:pre-wrap}
  form.inline{display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin:.5rem 0}
  form.inline > *{flex:0 0 auto}
  label{display:block;font-size:.85rem;color:var(--oaap-color-text-muted);margin:.5rem 0 .2rem}
  .oaap-filter label{margin:0}
  .quick a{margin-right:.8rem}
  .rights-line{margin-top:2rem}
  /* Reiter des Vorgangs: Anker und CSS, ohne Skript; die Adresse behält den Reiter.
     Der Überblick steht im Dokument zuletzt und wird mit `order` nach vorn gestellt,
     damit `:target ~ #ueberblick` ihn ausblenden kann. */
  .panels{display:flex;flex-direction:column}
  .tp{display:none;scroll-margin-top:5rem} .tp:target{display:block}
  #ueberblick{display:block;order:-1} .tp:target ~ #ueberblick{display:none}
"""
HEAD = ('<script src="/static/md/markdown.js" defer></script>'
        '<script src="/static/md/view.js" defer></script>')
SCRIPTS = """<script>
addEventListener("DOMContentLoaded", function () {
  if (window.OAAPView) { OAAPView.mode(); OAAPView.mount(document); }
  document.querySelectorAll("[data-md-mode]").forEach(function (b) {
    b.onclick = function () { OAAPView.mode(b.getAttribute("data-md-mode")); };
  });
});
</script>"""


def rights_summary(who, store):
    """Ein Satz unter jeder Seite: was diese Person hier darf."""
    if who is None:
        return ""
    try:
        queues = store.queues() if store else []
    except Exception:  # noqa: BLE001 -- der Hinweis darf keine Seite verhindern
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


TAB_KEYS = {"home": "/", "new": "/neu", "queues": "/bereiche", "partners": "/partner"}


def page(title, body, tab="", *, h1=True, back="", tabs_html="", obj=None, obj_title=None):
    """Eine Seite im Rahmen der Plattform (RFC-0118): Kopfleiste, eine Reiterleiste,
    Fußzeile. Auf einer Vorgangsseite trägt die Leiste die Reiter des Vorgangs."""
    who = getattr(_REQ, "who", None)
    tabs = [("/", "Übersicht"), ("/neu", "Neuer Vorgang"), ("/bereiche", "Bereiche")]
    if who is not None and who.admin:
        tabs.append(("/partner", "Partner"))
    foot = rights_summary(who, getattr(_REQ, "store", None))
    main = (back + (f"<h1>{esc(title)}</h1>" if h1 else "") + body
            + (f"<p class='muted rights-line'>{esc(foot)}</p>" if foot else ""))
    return af.page(title, main, app=APP_NAME, version=VERSION, product=APP_ID,
                   tabs=None if tabs_html else tabs, active=TAB_KEYS.get(tab, ""), tabs_html=tabs_html,
                   obj=obj, obj_title=obj_title, style=STYLE, head=HEAD, scripts=SCRIPTS)


def status_color(status):
    """0.11.0: dieselben Farben wie die Punkte im Chat -- rot: die Meldende ist dran,
    grün: beantwortet, grau: erledigt, gelb: offen."""
    if status in ("waiting_reporter", "rejected"):
        return "red"
    return {"answered": "green", "closed": "grey"}.get(status, "yellow")


BADGE_OF = {"red": "err", "green": "ok", "yellow": "warn", "grey": ""}


def pill(status, mark="", reporter=False):
    word = "Rückfrage an dich" if reporter and status == "waiting_reporter" else STATUS_LABEL.get(status, status)
    if status == "closed" and mark in MARK_LABEL:
        word += f" ({MARK_LABEL[mark]})"
    return af.badge(word, BADGE_OF[status_color(status)])


KI = af.badge("KI", "info")


def queue_href(queue):
    """Die Seite eines Bereichs (/bereich/<kennung>) -- dort pflegt der Mandanten-
    Administrator Zuständige, Erstbearbeitung und Ampel (RFC-0118 §7: keine Formulare
    in Tabellenzeilen)."""
    return "/bereich/" + quote(queue) if queue else "/bereiche"


# 0.16.0 (RFC-0105 §7.1): der Freigabeschlüssel dieser Instanz. Mit ihm unterschreibt sie jede
# Freigabe eines geplanten Aufrufs; der tenant_admin hinterlegt ihn bei der Sicht des
# Spezialisten im MCP-Gateway (approval=signed), das dann selbst prüft. Er liegt nur hier
# (Datei 0600 im Datenverzeichnis) und dort im Tresor.
APPROVAL_PREFIX = "oaap-approval.v1."
APPROVAL_TTL = 7 * 24 * 3600


LIGHT_WORD = {"": "keine eigene", "green": "grün", "yellow": "gelb", "red": "rot"}


def approval_key_path():
    return os.path.join(DATA_DIR, "approval.key")


def approval_key(rotate=False):
    import secrets
    p = approval_key_path()
    if not rotate:
        try:
            with open(p, encoding="ascii") as fh:
                k = fh.read().strip()
            if re.fullmatch(r"oaap-fk-[0-9a-f]{64}", k):
                return k
        except OSError:
            pass
    k = "oaap-fk-" + secrets.token_hex(32)
    os.makedirs(DATA_DIR, exist_ok=True)
    fd = os.open(p + ".neu", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(k + "\n")
    os.replace(p + ".neu", p)
    return k


def sign_approval(d, now=None):
    """Die Freigabe als oaap-approval.v1.<angaben>.<unterschrift> (Format: mcp-gateway
    signed_approval.py, oaap.ai.mcp-gateway §5.6)."""
    import base64
    import hmac
    now = int(time.time() if now is None else now)
    first = (os.environ.get("OAAP_INSTANCE_NAMES", "").split(",")[0] or "").strip()
    iss = (urlparse(first).netloc if "://" in first else first) or "vorgaenge"
    args = json.dumps(d.get("arguments") or {}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    payload = {"v": 1, "iss": iss, "case": d["case"], "plan": d["plan"], "tool": d["tool"],
               "args": hashlib.sha256(args.encode("utf-8")).hexdigest(), "by": d["by"],
               "iat": now, "exp": now + APPROVAL_TTL, "jti": f"{iss}:{d['case_id']}:{d['plan']}"}

    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    body = b64(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(approval_key().encode(), (APPROVAL_PREFIX + body).encode(), hashlib.sha256).digest()
    return APPROVAL_PREFIX + body + "." + b64(mac)


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
    label = "Rückfrage an dich" if st == "waiting_reporter" else STATUS_LABEL.get(st, st)
    return {"status": st, "label": label, "mark": mark if st == "closed" else "",
            "mark_label": MARK_LABEL.get(mark, "") if st == "closed" else "",
            "answered": st in ("answered", "closed"), "title": r.get("title", ""),
            "updated": r.get("updated", ""), "url": case_url(r["number"]),
            "object": ({"kind": r["object_kind"], "id": r["object_id"]} if r.get("object_id") else None)}


def case_table(rows, queues):
    if not rows:
        return af.empty("Keine Vorgänge.")
    qt = {q["id"]: q["title"] for q in queues}
    tr = "".join(
        f"<tr><td><a href='/v/{quote(r['number'])}'>{esc(r['number'])}</a></td>"
        f"<td>{esc(KIND_LABEL.get(r['kind'], r['kind']))}</td>"
        f"<td>{esc(r['title'])}</td><td>{pill(r['status'], r.get('mark', ''))}</td>"
        f"<td>{esc(qt.get(r['queue'], r['queue']))}</td>"
        f"<td class=muted>{esc(r['updated'][:16].replace('T', ' '))}</td></tr>"
        for r in rows)
    return ("<div class=oaap-tablewrap><table class=oaap-table><thead><tr><th>Nr.</th><th>Art</th>"
            f"<th>Betreff</th><th>Status</th><th>Bereich</th><th>Geändert</th></tr></thead><tbody>{tr}</tbody></table></div>")


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
        if p == "/platform/cases/gate" or p.startswith("/platform/cases/gate/"):
            return self.gate_read(p[len("/platform/cases/gate"):].strip("/"), u.query)
        if p == "/mcp":
            return self.send(405, "", "text/plain")
        if self.is_partner():
            return self.send(403, page("Nur Übergaben", "<p>Dieser Zugang liefert Übergaben, sonst nichts.</p>"))
        who = self.who()
        if not who:
            return self.send(401, page("Nicht angemeldet", "<p>Bitte über die Plattform anmelden.</p>"))
        q = parse_qs(u.query)
        if p.startswith("/api/spez/"):
            return self.spez_get(who, p[len("/api/spez/"):])
        if p == "/":
            return self.home(who, q)
        if p == "/neu":
            return self.new_form(who)
        if p == "/bereiche":
            return self.queues_page(who)
        if p == "/bereiche/freigabeschluessel":
            return self.approval_key_page(who)
        if p.startswith("/bereich/"):
            return self.queue_page(who, unquote(p[len("/bereich/"):]).strip("/"))
        if p == "/partner":
            return self.partners_page(who)
        if p.startswith("/v/") and p.endswith("/uebergabe"):
            return self.handover_form(who, p[3:-len("/uebergabe")],
                                      (q.get("partner") or [""])[0])
        if p.startswith("/v/"):
            return self.case_page(who, p[3:], (q.get("m") or [""])[0])
        return self.send(404, page("Nicht gefunden", af.empty("Diese Seite gibt es nicht. <a href='/'>Zur Übersicht</a>")))

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
        if p in ("/platform/cases/gate", "/platform/cases/gate/"):
            return self.gate_post(urlparse(self.path).query)
        if p.startswith("/platform/cases/gate/"):
            return self.gate_send(405, {"error": "Liefern mit POST an /gate/<name>"},
                                  urlparse(self.path).query)
        if p == "/handover":
            return self.handover_in()
        if self.is_partner():
            return self.json(403, {"error": "Dieser Zugang liefert Übergaben, sonst nichts."})
        who = self.who()
        if not who:
            return self.send(401, "")
        if p == "/mcp":
            return self.mcp_post(who)
        if p.startswith("/api/spez/"):
            return self.spez_post(who, p[len("/api/spez/"):])
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
            if p == "/bereiche/spezialist":
                return self.first_responder_post(who, f)
            if p == "/bereiche/ampel":
                return self.queue_light_post(who, f)
            if p == "/bereiche/pruefung":
                return self.queue_review_post(who, f)
            if p == "/bereiche/freigabeschluessel":
                return self.approval_key_post(who, f)
            if p == "/partner":
                return self.partners_post(who, f)
            if p.startswith("/v/") and p.endswith("/uebergabe"):
                return self.handover_post(who, p[3:-len("/uebergabe")], f)
            if p.startswith("/v/"):
                return self.case_post(who, p[3:].split("/")[0], p.rsplit("/", 1)[-1], f)
        except Refused as e:
            return self.send(400, page("Nicht möglich", af.msg(str(e), error=True)
                                       + "<p><a href='javascript:history.back()'>Zurück</a></p>"))
        return self.send(404, "")

    # --- 0.13.0 (RFC-0105 Stufe 1): die Tür für den Lauf eines Spezialisten. Der Spezialist
    # ist hier eine Person wie jede andere (sein Agent, RFC-0067): er liest, was seine
    # Zuweisung erlaubt, und schreibt nur, wo er verantwortlich ist. Nur JSON (kein Formular).
    def spez_case(self, who, number):
        c = self.store.get(mcp_door.case_number(number))
        if not c or not ((can_see(who, c) and can_read_internal(who, c)) or self.store.reviewing(who.id, c)):
            raise Refused("Diesen Vorgang gibt es nicht, oder er liegt nicht in deinen Bereichen.")
        return c

    def spez_get(self, who, rest):
        try:
            if rest == "turn":
                # 0.15.0: wer diese Tür benutzt, ist ein Spezialist (Grenzen der Rückfragen, Auswahl)
                self.store.mark_specialist(who.id)
                return self.json(200, {"uid": who.id, "cases": self.store.turn(who.id)})
            if rest.startswith("case/"):
                c = self.spez_case(who, rest[5:])
                qt = {q["id"]: q["title"] for q in self.store.queues()}
                tech = c["tech"] or {}
                # Plan -> Eintrag der Freigabe; ein Beleg ist erst ein Aufruf NACH der Freigabe
                approved_plans = {e["data"].get("plan"): e["seq"] for e in c["entries"]
                                  if e["type"] == "approval" and e["data"].get("ok")}
                return self.json(200, {
                    "number": c["number"], "title": c["title"], "description": c["body"],
                    "kind": KIND_LABEL.get(c["kind"], c["kind"]), "status": c["status"],
                    "queue": c["queue"], "queue_title": qt.get(c["queue"], c["queue"]),
                    # 0.16.0 (D9): die Ampel des Bereichs -- der Lauf schickt sie als X-OAAP-Light
                    "queue_light": (self.store.queue(c["queue"]) or {}).get("light") or "",
                    "reported_by": c["reporter_name"], "created": c["created"],
                    "fields": tech.get("fields") or {}, "subkind": tech.get("subkind") or "",
                    "responsible": c["responsible"], "mine": c["responsible"] == who.id,
                    "stopped": bool(c.get("spec_stop")),
                    # 0.15.0 (§6): offene Rückfragen an mich, und wen ich fragen kann (nur verantwortlich)
                    "consults_open": [{"seq": k["seq"], "asked_by": self.store.person_name(k["asked_by"]),
                                       "question": next((e["text"] for e in c["entries"] if e["seq"] == k["seq"]), "")}
                                      for k in c["consults"] if k["target"] == who.id and not k["answered"]
                                      and k.get("kind", "consult") == "consult"],
                    # 0.22.0 (RFC-0134 §5): offene Aufträge an mich, und wem ich Aufträge geben kann
                    "tasks_open": [{"seq": k["seq"], "asked_by": self.store.person_name(k["asked_by"]),
                                    "task": next((e["text"] for e in c["entries"] if e["seq"] == k["seq"]), "")}
                                   for k in c["consults"] if k["target"] == who.id and not k["answered"]
                                   and k.get("kind") == "task"],
                    # Name UND Kennung: die App Spezialisten kennt ihre Agenten unter eigenen Namen
                    "taskable": ([{"name": p["name"], "uid": p["uid"]} for p in self.store.taskable(c, who.id)]
                                 if c["responsible"] == who.id else []),
                    "tasks_used": sum(1 for k in c["consults"] if k.get("kind") == "task"),
                    "tasks_max": tasks_max(),
                    # 0.23.0 (RFC-0134 §7/§8): ob der Leiter schließen darf, womit er es belegt,
                    # das Prüfmerkmal, und ob ich hier prüfen soll
                    "from_gate": c["object_kind"] == "gate",
                    "proofs": [{"seq": e["seq"], "tool": e["data"].get("tool") or ""} for e in c["entries"]
                               if e["type"] == "spec" and e["data"].get("kind") == "tool"
                               and ((approved_plans.get(e["data"].get("plan")) or e["seq"]) < e["seq"]
                                    or e["data"].get("lifted_by"))
                               and not e["data"].get("failed")],
                    "review_reasons": c["review_reasons"],
                    "reviewing": self.store.reviewing(who.id, c) and not c.get("review_spec"),
                    "consultable": ([{"name": p["name"], "specialist": p["specialist"]}
                                     for p in self.store.consultable(c, who.id)]
                                    if c["responsible"] == who.id else []),
                    "entries": [{"seq": e["seq"], "at": e["at"], "by": e["actor_name"],
                                 "by_me": e["actor"] == who.id, "type": e["type"], "text": e["text"],
                                 "internal": bool(e.get("internal")),
                                 **({"step": {k: e["data"].get(k) for k in ("kind", "run", "assistant", "tool", "reason",
                                                                           "arguments", "sha256", "plan", "lifted_by",
                                                                           "failed", "proof")
                                              if e["data"].get(k) is not None}} if e["type"] == "spec" else {}),
                                 **({"approval": {k: e["data"].get(k) for k in ("plan", "sha256", "ok", "kind", "token")}}
                                    if e["type"] == "approval" else {}),
                                 **({"consult": {k: e["data"].get(k) for k in ("to_name", "specialist", "consult", "n")
                                                 if e["data"].get(k) is not None}}
                                    if e["type"] in ("consult", "consult_answer", "task", "task_result") else {}),
                                 **({"review": {k: e["data"].get(k) for k in ("reason", "result", "reasons")
                                                if e["data"].get(k) is not None}}
                                    if e["type"] in ("review_mark", "review") else {})}
                                for e in c["entries"]]})
        except Refused as e:
            return self.json(404, {"error": str(e)})
        return self.json(404, {"error": "unbekannt"})

    def spez_post(self, who, rest):
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.json(415, {"error": "Nur JSON."})
        try:
            doc = json.loads(self.body() or b"{}")
            if not isinstance(doc, dict):
                raise ValueError
        except ValueError:
            return self.json(400, {"error": "Kein JSON-Objekt."})
        parts = rest.split("/")
        if len(parts) != 3 or parts[0] != "case" or parts[2] not in ("entry", "give-back", "consult", "answer", "task",
                                                                     "close", "mark", "review"):
            return self.json(404, {"error": "unbekannt"})
        try:
            c = self.spez_case(who, parts[1])
            meta = {k: str(doc.get(k)) for k in ("run", "assistant") if doc.get(k) is not None}
            if parts[2] == "consult":
                # 0.15.0 (§6): der Verantwortliche fragt -- nach Name, aus `consultable`
                name = str(doc.get("to") or "").strip()
                hit = [p for p in self.store.consultable(c, who.id) if p["name"] == name or p["uid"] == name]
                if len(hit) != 1:
                    raise Refused(f"„{name}“ kann hier nicht gefragt werden. Möglich: "
                                  + (", ".join(p["name"] for p in self.store.consultable(c, who.id)) or "niemand"))
                seq = self.store.consult(c, who.id, who.name, hit[0]["uid"], doc.get("text", ""), meta)
                return self.json(200, {"ok": True, "consult": seq, "to": hit[0]["name"],
                                       "specialist": hit[0]["specialist"]})
            if parts[2] == "task":
                # 0.22.0 (RFC-0134 §5): der Leiter gibt einen Auftrag -- nach Name, aus `taskable`
                name = str(doc.get("to") or "").strip()
                pool = self.store.taskable(c, who.id)
                hit = [p for p in pool if p["name"] == name or p["uid"] == name]
                if len(hit) != 1:
                    raise Refused(f"„{name}“ kann hier keinen Auftrag bekommen. Möglich: "
                                  + (", ".join(p["name"] for p in pool) or "niemand"))
                seq = self.store.task(c, who.id, who.name, hit[0]["uid"], doc.get("text", ""), meta,
                                      tasks_max=tasks_max())
                return self.json(200, {"ok": True, "task": seq, "to": hit[0]["name"]})
            if parts[2] == "close":
                # 0.23.0 (RFC-0134 §7): der Leiter schließt mit Beleg -- die Regeln stehen im Store
                self.store.close_by_lead(c, who.id, who.name, str(doc.get("text") or ""), doc.get("proof") or 0,
                                         str(doc.get("no_write") or ""), meta)
                return self.json(200, {"ok": True, "closed": True})
            if parts[2] == "mark":
                self.store.mark_review(c, who.id, who.name, str(doc.get("reason") or ""), by="lead")
                return self.json(200, {"ok": True})
            if parts[2] == "review":
                self.store.review(c, who.id, who.name, str(doc.get("result") or ""), str(doc.get("text") or ""),
                                  by_spec=True)
                return self.json(200, {"ok": True})
            if parts[2] == "answer":
                try:
                    seq = int(doc.get("consult") or 0)
                except (TypeError, ValueError):
                    raise Refused("Unbekannte Rückfrage.")
                self.store.answer_consult(c, who.id, who.name, seq, doc.get("text", ""), meta)
                return self.json(200, {"ok": True})
            if parts[2] == "entry":
                data = {k: doc.get(k) for k in ("run", "assistant", "tool", "arguments", "ms", "call_id",
                                                "reason", "steps", "plan", "approved_by", "lifted_by", "failed")
                        if doc.get(k) is not None}
                self.store.spec_entry(c, who.id, who.name, doc.get("kind", ""), doc.get("text", ""), data)
                return self.json(200, {"ok": True})
            self.store.give_back(c, who.id, who.name, doc.get("reason", ""), str(doc.get("run") or ""))
            return self.json(200, {"ok": True, "given_back": True})
        except Refused as e:
            return self.json(409, {"error": str(e)})

    def queue_light_post(self, who, f):
        if not who.manager:
            raise Refused("Die Ampel eines Bereichs stellt der Mandanten-Administrator ein.")
        self.store.set_queue_light(f.get("queue", ""), f.get("light", ""), who.id)
        return self.redirect(queue_href(f.get("queue", "")))

    def queue_review_post(self, who, f):
        if not who.manager:
            raise Refused("Die Prüfung eines Bereichs stellt der Mandanten-Administrator ein.")
        self.store.set_queue_review(f.get("queue", ""), f.get("share", ""), f.get("uid", ""), who.id)
        return self.redirect(queue_href(f.get("queue", "")))

    def approval_key_page(self, who, msg=""):
        """0.16.0 (RFC-0105 §7.1): der Freigabeschlüssel, nur für den tenant_admin."""
        if not who.manager:
            return self.send(403, page("Abgelehnt", "<p>Den Freigabeschlüssel sieht der Mandanten-Administrator.</p>"))
        k = approval_key()
        body = (f"{msg}<p>Mit diesem Schlüssel unterschreibt diese App jede Freigabe eines geplanten "
                f"Aufrufs eines Spezialisten. Hinterlegt bei seiner Sicht im <b>MCP-Gateway</b> (Seite der "
                f"Sicht: „Schreiben mit Schlüssel“ = <i>nur mit unterschriebener Freigabe</i>, Feld "
                f"„Freigabeschlüssel“), prüft das Gateway jede Freigabe selbst: genau dieser Aufruf, genau "
                f"einmal, höchstens {APPROVAL_TTL // 86400} Tage alt. Wer nur den Schlüssel des Spezialisten "
                f"hat, kann sich dann keine Freigabe mehr ausstellen.</p>"
                f"<p><code style='user-select:all'>{esc(k)}</code></p>"
                f"<p class=muted>Behandle ihn wie ein Passwort. Neu erzeugen macht alle noch nicht "
                f"ausgeführten Freigaben ungültig; danach im MCP-Gateway den neuen eintragen.</p>"
                f"<form method=post action=/bereiche/freigabeschluessel><button class=danger>Neu erzeugen</button></form>")
        return self.send(200, page("Freigabeschlüssel", body, "queues", back=af.back("/bereiche", "Zu den Bereichen")))

    def approval_key_post(self, who, f):
        if not who.manager:
            raise Refused("Den Freigabeschlüssel erzeugt der Mandanten-Administrator.")
        approval_key(rotate=True)
        self.store.log_queue_action("", who.id, "approval_key")
        return self.approval_key_page(who, af.msg("Neu erzeugt. Jetzt im MCP-Gateway eintragen."))

    def first_responder_post(self, who, f):
        if not who.manager:
            raise Refused("Die Erstbearbeitung eines Bereichs stellt der Mandanten-Administrator ein.")
        self.store.set_first_responder(f.get("queue", ""), f.get("uid", ""))
        return self.redirect(queue_href(f.get("queue", "")))

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
        # 0.11.0 (RFC-0103 D9): der Chat listet nur die eigenen Meldungen; was der Person
        # zugeordnet ist, ist eine Zahl mit einem Link hierher -- bearbeitet wird in den Vorgängen
        assigned = sum(1 for r in self.store.listing(person=uid) if r["status"] in OPEN)
        base = case_url("x")[:-len("/v/x")] if case_url("x") else ""
        return self.json(200, {"cases": {r["number"]: mine_row(r) for r in rows},
                               "accept_days": ACCEPT_DAYS_NOW[0], "assigned": assigned,
                               "assigned_url": f"{base}/?view=assigned" if base else "",
                               "overview_url": f"{base}/?view=mine" if base else ""})

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
            # 0.12.0 (RFC-0109 §13): eine Anfrage eines Mandanten an den
            # Betreiber. Der Knoten schreibt sie, nicht der Mandant; `from`
            # sagt, für wen (Mandant und Name der Person), und steht als
            # Absender -- beglaubigt ist nur, dass der Knoten schreibt.
            obj_kind = str(doc.get("object") or "reboot")
            if obj_kind not in NODE_OBJECTS:
                raise Refused("object: reboot oder request.")
            said = str(doc.get("from") or "").strip()[:120]
            if obj_kind == "request" and said:
                name = f"{said} (über den Knoten {node or '?'})"[:120]
            if op == "open":
                if self.store.submissions_by(src["instance"], 3600) >= SUBMISSIONS_PER_HOUR:
                    return self.json(429, {"error": "Zu viele Vorgänge des Knotens in der "
                                                    "letzten Stunde."})
                ref = str(doc.get("ref") or "").strip()[:200]
                if not ref:
                    raise Refused("ref fehlt: der Bezug des Plans.")
                have = self.store.open_by_object(obj_kind, ref)
                if have:
                    return self.json(200, {"ok": True, "number": have["number"],
                                           "id": have["id"], "existing": True})
                made = self.store.create(
                    kind="internal", title=doc.get("title"), body=doc.get("body") or "",
                    reporter=principal, reporter_name=name,
                    queue=str(doc.get("queue") or DEFAULT_QUEUE),
                    obj={"kind": obj_kind, "id": ref},
                    attested=dict(src, submitted_by=principal, node=node,
                                  **({"on_behalf": said} if obj_kind == "request" and said
                                     else {})))
                print(f"case {made['number']} from node {node}", flush=True)
                return self.json(201, {"ok": True, "number": made["number"], "id": made["id"]})
            c = self.store.get(str(doc.get("number") or ""))
            if not c or c["reporter"] != principal:
                return self.json(404, {"error": "Diesen Vorgang hat der Knoten nicht angelegt."})
            if op == "note":
                self.store.comment(c, principal, name, doc.get("text"))
                return self.json(200, {"ok": True, "number": c["number"]})
            if op == "read":
                # 0.12.0: was der Mandant auf seiner Seite „Einrichten“ sieht --
                # Status und die Antworten der Bearbeitenden, nie interne
                # Kommentare, nie die Einträge des Knotens selbst.
                return self.json(200, node_reading(c, principal))
            if op == "close":
                closed = self.store.close_by_node(c, principal, name, doc.get("text") or "")
                return self.json(200, {"ok": True, "number": c["number"], "closed": closed})
            raise Refused("op: open, note, read oder close.")
        except (Refused, ValueError) as e:
            return self.json(400, {"error": str(e) if isinstance(e, Refused) else "Ungültige Meldung."})

    # --- Lieferungen durch ein Tor (RFC-0135 Stufe 1)
    def gate_context(self):
        """Was das Gateway ueber das Tor sagt -- oder None. Durch kommt nur,
        was der Block des Tors auf dem Ort des Mandanten geschickt hat: der
        Prinzipal (vom Gateway nach der Pruefung) ist der Zugang, die Herkunft
        ist `gate:<name>`, und Feldliste und Prozess sind lesbar. Jede
        Instanz-Site streicht diese Kopfzeilen, ein Client setzt sie also nie."""
        name = self.headers.get("X-OAAP-Gate-Name", "").strip()
        principal = self.headers.get("X-OAAP-User", "").strip()
        access = self.headers.get("X-OAAP-Gate-Access", "").strip()
        src = self.attested()
        if (not name or not principal or principal != access or not src
                or src["instance"] != f"gate:{name}" or src["app"] != "gate"):
            return None
        fields = gates.unpack(self.headers.get("X-OAAP-Gate-Fields", ""))
        proc = gates.unpack(self.headers.get("X-OAAP-Gate-Process", ""))
        if not isinstance(fields, list) or not isinstance(proc, dict):
            return None
        try:
            fields = gates.check_fields(fields)
        except ValueError:
            return None
        label = unquote(self.headers.get("X-OAAP-Gate-Label", "")).strip() or principal
        return {"name": name, "principal": principal, "label": label[:80],
                "fields": fields, "process": proc, "src": src}

    def gate_send(self, code, doc, query, lines=None):
        """JSON, oder auf Wunsch Zeilen `name=wert`, die ein ABAP-Programm ohne
        Parser liest (RFC-0135 D9). Zeilenumbrueche im Wert als \\n."""
        want_text = ("answer=text" in (query or "")
                     or "text/plain" in (self.headers.get("Accept") or ""))
        if not want_text:
            return self.json(code, doc)
        rows = lines if lines is not None else [("error", doc.get("error", ""))]
        body = "".join(f"{k}={str(v).replace(chr(13), '').replace(chr(10), chr(92) + 'n')}\n"
                       for k, v in rows)
        return self.send(code, body, "text/plain; charset=utf-8")

    def gate_post(self, query):
        ctx = self.gate_context()
        if not ctx:
            return self.json(403, {"error": "Nur durch ein Tor der Plattform."})
        raw_type = self.headers.get("Content-Type") or ""
        ctype = raw_type.split(";")[0].strip().lower()
        # Gemessen 26.11.: ein Client, der „ü“ als Latin-1 schickt (%FC), wurde mit
        # einem Ersatzzeichen gespeichert, ohne dass jemand es merkte. Jetzt: UTF-8
        # streng, oder der Zeichensatz, den der Content-Type nennt (ISO-8859-1);
        # was nicht passt, wird mit einem Satz abgelehnt, nie verfälscht.
        cs = re.search(r"charset\s*=\s*\"?([A-Za-z0-9_-]+)", raw_type)
        charset = (cs.group(1).lower() if cs else "utf-8").replace("_", "-")
        if charset in ("latin1", "latin-1", "iso8859-1", "iso-8859-1"):
            charset = "iso-8859-1"
        elif charset not in ("utf-8", "utf8", "us-ascii"):
            return self.gate_send(415, {"error": f"Zeichensatz {charset}: UTF-8 oder "
                                                 "ISO-8859-1 erwartet."}, query)
        try:
            if ctype == "application/x-www-form-urlencoded":
                doc = {k: v[0] for k, v in parse_qs(self.body().decode("ascii"),
                                                    keep_blank_values=True,
                                                    encoding=charset, errors="strict").items()}
            elif ctype in ("application/json", ""):
                doc = json.loads(self.body().decode(charset) or "{}")
            else:
                return self.gate_send(415, {"error": "JSON oder Formularfelder erwartet "
                                                     f"(nicht {ctype})."}, query)
        except UnicodeDecodeError:
            return self.gate_send(400, {"error": f"Inhalt ist kein {charset.upper()}; einen "
                                                 "anderen Zeichensatz im Content-Type nennen "
                                                 "(charset=iso-8859-1)."}, query)
        except ValueError:
            return self.gate_send(400, {"error": "Inhalt nicht lesbar "
                                                 f"({ctype or 'ohne Content-Type'})."}, query)
        values, errors = gates.validate(ctx["fields"], doc)
        if errors:
            return self.gate_send(422, {"error": "abgelehnt: " + "; ".join(errors),
                                        "errors": errors}, query)
        m = gates.mapped(ctx["fields"], values)
        name, principal, proc = ctx["name"], ctx["principal"], ctx["process"]
        oid = f"{name}/{m['ref']}"
        have = self.store.by_object("gate", oid, principal)
        if have:
            return self.gate_send(200, {"ok": True, "number": have["number"], "ref": m["ref"],
                                        "status": have["status"], "existing": True}, query,
                                  [("number", have["number"]), ("ref", m["ref"])])
        if self.store.submissions_by(ctx["src"]["instance"], 3600) >= SUBMISSIONS_PER_HOUR:
            return self.gate_send(429, {"error": "Zu viele Lieferungen dieses Tors in der "
                                                 "letzten Stunde."}, query)
        kind = proc.get("kind") if proc.get("kind") in KINDS else "internal"
        queue = proc.get("queue") if QUEUE_ID.fullmatch(str(proc.get("queue") or "")) \
            else DEFAULT_QUEUE
        try:
            made = self.store.create(
                kind=kind, title=m["title"], body=m["text"], reporter=principal,
                reporter_name=f"{ctx['label']} (Tor {name})", queue=queue,
                obj={"kind": "gate", "id": oid},
                attested=dict(ctx["src"], submitted_by=principal, gate=name,
                              access=principal, access_label=ctx["label"],
                              process=str(proc.get("process") or "")),
                delivered=m["tech"])
        except Refused as e:
            return self.gate_send(422, {"error": "abgelehnt: " + str(e)}, query)
        print(f"case {made['number']} through gate {name} from {principal}", flush=True)
        return self.gate_send(201, {"ok": True, "number": made["number"], "ref": m["ref"],
                                    "status": "new"}, query,
                              [("number", made["number"]), ("ref", m["ref"])])

    def gate_read(self, ref, query):
        """`GET /gate/<name>/<ref>`: der Stand dessen, was DIESER Zugang unter
        `ref` geliefert hat -- Nummer, Status und die letzte Antwort der
        Bearbeitenden (nie Internes). Ein fremder Bezug fehlt wie ein
        unbekannter."""
        ctx = self.gate_context()
        if not ctx:
            return self.json(403, {"error": "Nur durch ein Tor der Plattform."})
        ref = unquote(ref)
        if not ref:
            return self.gate_send(405, {"error": "Liefern mit POST, lesen mit /gate/<name>/<ref>."},
                                  query)
        c = self.store.by_object("gate", f"{ctx['name']}/{ref}", ctx["principal"]) \
            if gates.REF_RE.match(ref) else None
        if not c:
            return self.gate_send(404, {"error": f"Unter ref {ref[:120]} hat dieser Zugang "
                                                 "nichts geliefert."}, query)
        r = node_reading(c, ctx["principal"])
        answer = r["replies"][-1]["text"] if r["replies"] else ""
        doc = {"ok": True, "number": c["number"], "ref": ref, "status": c["status"],
               "status_label": r["status_label"], "updated": r["updated"], "answer": answer}
        return self.gate_send(200, doc, query,
                              [("number", c["number"]), ("ref", ref), ("status", c["status"]),
                               ("updated", r["updated"]), ("answer", answer)])

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
            return self.send(403, page("Partner", "<p>Partner tragen die Administratoren des Mandanten ein.</p>",
                                       "partners"))
        queues = self.store.queues()
        rows = "".join(
            f"<tr><td><code>{esc(x['id'])}</code></td><td>{esc(x['name'])}</td>"
            f"<td>{esc(x['url']) or '–'}</td><td>{'ja' if x['out_key'] else 'nein'}</td>"
            f"<td><code>{esc(x['in_principal']) or '–'}</code></td><td>{esc(x['in_queue'])}</td>"
            f"<td>{af.badge('aktiv', 'ok') if x['active'] else af.badge('aus')}</td></tr>"
            for x in self.store.partners())
        qs = "".join(f"<option value='{esc(q['id'])}'>{esc(q['title'])}</option>" for q in queues)
        body = f"""<p>Ein Partner ist ein anderer Mandant, mit dem Vorgänge ausgetauscht werden,
auf diesem oder einem anderen Server. Was übergeben wird, ist eine freigegebene
Kopie ohne Personendaten; der Vorgang selbst bleibt hier.</p>
<ul class=muted>
<li><b>Damit Sie an ihn übergeben können:</b> Adresse seiner Vorgänge-App und der Schlüssel, den
<i>er</i> Ihnen ausgestellt hat.</li>
<li><b>Damit er Ihnen übergeben oder antworten kann:</b> Legen Sie im Portal unter <i>Zugänge</i>
einen Maschinenzugang mit Rolle <code>user</code> an, beschränkt auf diese Instanz, geben Sie ihm
den Schlüssel, und tragen Sie den Namen des Zugangs hier als <i>eingehenden Zugang</i> ein.</li>
</ul>
{('<div class=oaap-tablewrap><table class=oaap-table><thead><tr><th>Kennung</th><th>Name</th><th>Adresse</th>'
  '<th>Schlüssel</th><th>Eingehender Zugang</th><th>landet in</th><th>Zustand</th></tr></thead><tbody>' + rows
  + '</tbody></table></div>') if rows else af.empty('Noch kein Partner. Unten den ersten eintragen.')}
<h2>Partner eintragen oder ändern</h2>
<p class=muted>Zum Ändern dieselbe Kennung eintragen.</p>
<form method=post action=/partner class=oaap-card>
 <label for=i>Kennung</label><input id=i name=id pattern='[a-z0-9][a-z0-9-]{{0,39}}' required>
 <label for=n>Name</label><input type=text id=n name=name maxlength=80>
 <label for=u>Adresse seiner Vorgänge-App (leer: er übergibt nur an uns)</label>
 <input type=text id=u name=url placeholder='https://vorgaenge.dev.example.org'>
 <label for=k>Schlüssel, den er uns ausgestellt hat (leer lassen = unverändert)</label>
 <input type=password id=k name=out_key autocomplete=off>
 <label for=p>Eingehender Zugang (Name des Maschinenzugangs)</label>
 <input type=text id=p name=in_principal>
 <label for=q>Übergaben von ihm landen in</label><select id=q name=in_queue>{qs}</select>
 <p><button name=action value=save class=primary>Speichern</button>
    <button name=action value=off class=danger>Abschalten</button></p></form>"""
        return self.send(200, page("Partner", body, "partners"))

    def partners_post(self, who, f):
        if not who.admin:
            raise Refused("Partner tragen die Administratoren des Mandanten ein.")
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
            f"{' checked' if str(e['seq']) in picked else ''}> {'(intern) ' if e.get('internal') else ''}"
            f"{esc(e['at'][:16].replace('T', ' '))}: "
            f"{esc(e['text'][:120])}</label>" for e in comments) or "<p class=muted>keine</p>"
        return f"""
<p>Was hier steht, verlässt den Mandanten. Die meldende Person, Benutzer-IDs und alle Einträge,
die Sie nicht auswählen, gehen <b>nicht</b> mit. Namen im Text findet kein Filter — prüfen Sie
Betreff und Text.</p>
<form method=post action='/v/{quote(c['number'])}/uebergabe' class=oaap-card>
 <input type=hidden name=partner value='{esc(partner['id'])}'>
 <label for=t>Betreff</label><input type=text id=t name=title maxlength=200 value='{esc(title)}' required>
 <label for=b>Text</label><textarea id=b name=body maxlength=10000>{esc(body)}</textarea>
 <label><input type=checkbox name=with_tech value=1{' checked' if with_tech else ''}>
  technische Angaben mitgeben (App, Version, Seite, Browser, letzter Fehler)</label>
 <h2>Kommentare, die mitgehen</h2>{boxes}
 <p><button name=step value=preview class=primary>Vorschau</button></p></form>"""

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
                f"<button name=step value=release class=primary>Freigeben und senden</button></form>"
                f"<p><a href='/v/{quote(c['number'])}/uebergabe?partner={quote(partner['id'])}'>"
                f"Zurück zum Entwurf</a></p>")
        return self.send(200, page(f"Vorschau der Übergabe {c['number']}", body))

    # --- Seiten
    LIST_SORT = ("number", "title", "status", "queue", "responsible", "assignee", "updated")
    LIST_HEAD = (("number", "Nr."), ("title", "Betreff"), ("status", "Status"), ("queue", "Bereich"),
                 ("responsible", "Verantwortlich"), ("assignee", "Zugewiesen"), ("updated", "Geändert"))

    def home(self, who, q):
        """0.11.0 (RFC-0103 D6): die Übersicht als Listenbericht. Alles, was die Person sehen
        darf, in einer Tabelle; Filter und Sortierung stehen in der Adresse
        (`?queue=jira&status=open&sort=-updated`), damit eine Ansicht ein Lesezeichen sein
        kann und die Seite kein Skript braucht."""
        arg = lambda k, d="": (q.get(k) or [d])[0].strip()  # noqa: E731
        queues = self.store.queues()
        qt = {x["id"]: x["title"] for x in queues}
        readable = who.readable(queues)
        view = arg("view")
        view = view if view in ("mine", "assigned", "review") else ""
        # „zur Prüfung“ zeigt auch beendete Vorgänge (die meisten markierten sind geschlossen)
        status = arg("status", "all" if arg("alle") == "1" or view == "review" else "open")
        status = status if status in ("open", "all") or status in STATUS_LABEL else "open"
        queue = arg("queue")
        text = arg("q")[:200]
        try:
            offset = max(0, int(arg("offset") or 0))
        except ValueError:
            offset = 0
        sort = arg("sort", "-updated")
        key = sort.lstrip("-")
        if key not in self.LIST_SORT:
            sort, key = "-updated", "updated"
        # wer die Namen in „verantwortlich / zugewiesen“ lesen darf: die Bearbeitenden
        # (RFC-0103 §4); auf einer nur eigenen Meldung bleiben die Spalten leer
        rows, handled = {}, set()
        for part, h in ((self.store.listing(queues=readable, open_only=False) if readable else [], True),
                        (self.store.listing(person=who.id, open_only=False), True),
                        (self.store.listing(involved=who.id, open_only=False), True),
                        (self.store.listing(reporter=who.id, open_only=False), False)):
            for r in part:
                rows.setdefault(r["id"], r)
                if h:
                    handled.add(r["id"])
        names = {p["uid"]: p["name"] for p in self.store.people()}
        seen_queues = sorted({r["queue"] for r in rows.values()} | set(readable),
                             key=lambda x: qt.get(x, x).lower())

        def cell(r, k):
            if k in ("responsible", "assignee"):
                return names.get(r[k], r[k]) if r[k] and r["id"] in handled else ""
            if k == "queue":
                return qt.get(r["queue"], r["queue"])
            if k == "status":
                return STATUS_LABEL.get(r["status"], r["status"])
            return r.get(k) or ""

        out = list(rows.values())
        # 0.15.0 (RFC-0105 §6): offene Rückfragen an mich gehören zu „meine“ und „an mich“
        asked = {r["id"] for r in self.store.listing(person=who.id, open_only=True)
                 if who.id not in (r["responsible"], r["assignee"])}
        if view == "mine":
            out = [r for r in out if who.id in (r["reporter"], r["responsible"], r["assignee"]) or r["id"] in asked]
        elif view == "assigned":
            out = [r for r in out if who.id in (r["responsible"], r["assignee"]) or r["id"] in asked]
        elif view == "review":
            # 0.23.0 (RFC-0134 §8): markierte Vorgänge -- nur für die Bearbeitenden, auch beendete
            out = [r for r in out if r.get("review_mark") and r["id"] in handled]
        if queue:
            out = [r for r in out if r["queue"] == queue]
        if text:
            # RFC-0118 §7: die Suche zuerst -- Nummer, Betreff, Beschreibung, Meldende
            words = text.lower().split()
            out = [r for r in out if all(w in " ".join(str(r.get(k) or "") for k in (
                "number", "title", "body", "reporter_name")).lower() for w in words)]
        if status == "open":
            out = [r for r in out if r["status"] in OPEN]
        elif status != "all":
            out = [r for r in out if r["status"] == status]
        # sortiert wird nach dem, was in der Zelle steht -- eine leere Spalte verrät
        # über die Reihenfolge nichts
        out.sort(key=lambda r: (str(cell(r, key)).lower(), r["number"]), reverse=sort.startswith("-"))

        def link(**kw):
            p = {"q": text, "view": view, "queue": queue, "status": status, "sort": sort, "offset": 0}
            p.update(kw)
            return "/?" + urlencode({k: v for k, v in p.items() if v and not (k == "status" and v == "open")
                                     and not (k == "sort" and v == "-updated")})

        def sel(name, cur, opts):
            return (f"<select name={name} onchange='this.form.submit()'>" + "".join(
                f"<option value='{esc(v)}'{' selected' if v == cur else ''}>{esc(t)}</option>"
                for v, t in opts) + "</select>")
        # RFC-0118 §7: die Filterleiste des Kits -- Suche zuerst, dann drei Auswahlen
        form = ("<form method=get action='/' class=oaap-filter role=search>"
                f"<label>Suchen<input type=search name=q value='{esc(text)}' placeholder='Nummer, Betreff, Text'></label>"
                "<label>Ansicht" + sel("view", view, (
                    ("", "alles, was ich sehe"), ("mine", "meine (gemeldet, verantwortlich, zugewiesen)"),
                    ("assigned", "mir zugewiesen oder von mir verantwortet"),
                    ("review", "zur Prüfung"))) + "</label>"
                "<label>Bereich" + sel("queue", queue, [("", "alle Bereiche")] + [
                    (x, qt.get(x, x)) for x in seen_queues]) + "</label>"
                "<label>Status" + sel("status", status, [("open", "offen"), ("all", "alle")] + [
                    (k, w) for k, w in STATUS_LABEL.items()]) + "</label>"
                + (f"<input type=hidden name=sort value='{esc(sort)}'>" if sort != "-updated" else "")
                + "<button>Anzeigen</button>"
                + ("<a class=oaap-btn href='/'>Zurücksetzen</a>" if (text or view or queue or status != "open") else "")
                + "</form>")
        mine_now = sum(1 for r in rows.values() if r["status"] in OPEN
                       and who.id in (r["responsible"], r["assignee"]))
        to_review = sum(1 for r in rows.values() if r.get("review_mark") and r["id"] in handled
                        and who.may("triage", r["queue"]))
        quick = ("<p class='muted quick'>"
                 + (f"<a href='{esc(link(view='assigned', status='open', queue=''))}'>Mir zugewiesen oder von "
                    f"mir verantwortet ({mine_now})</a>" if mine_now else "")
                 + (f"<a href='{esc(link(view='', status='open', queue=''))}'>In meinen Bereichen</a>"
                    if readable else "")
                 + (f"<a href='{esc(link(view='review', status='all', queue=''))}'>Zur Prüfung ({to_review})</a>"
                    if to_review else "")
                 + f"<a href='{esc(link(view='mine', status='open', queue=''))}'>Von mir gemeldet</a>"
                 + f"<a href='{esc(link(view='', status='all', queue=''))}'>auch erledigte</a></p>")
        head = ""
        for k, t in self.LIST_HEAD:
            on = key == k
            nxt = ("-" + k if not sort.startswith("-") else k) if on else ("-" + k if k == "updated" else k)
            # der Pfeil ▲/▼ kommt aus dem Kit (aria-sort)
            head += (f"<th{' aria-sort=' + ('descending' if sort.startswith('-') else 'ascending') if on else ''}>"
                     f"<a href='{esc(link(sort=nxt))}'>{esc(t)}</a></th>")
        total = len(out)
        if offset >= total:
            offset = 0
        size = af.PAGE_SIZE
        shown = out[offset:offset + size]
        if shown:
            tr = "".join(
                f"<tr><td><a href='/v/{quote(r['number'])}'>{esc(r['number'])}</a></td>"
                f"<td>{esc(r['title'])} <span class=muted>{esc(KIND_LABEL.get(r['kind'], r['kind']))}</span></td>"
                f"<td>{pill(r['status'], r.get('mark', ''), r['reporter'] == who.id and r['id'] not in handled)}</td>"
                f"<td>{esc(cell(r, 'queue'))}</td><td>{esc(cell(r, 'responsible'))}</td>"
                f"<td>{esc(cell(r, 'assignee'))}</td>"
                f"<td class=muted>{esc(r['updated'][:16].replace('T', ' '))}</td></tr>"
                for r in shown)
            table = (f"<div class=oaap-tablewrap><table class='oaap-table list'><thead><tr>{head}</tr></thead>"
                     f"<tbody>{tr}</tbody></table></div>")
        elif rows:
            table = af.empty("Keine Vorgänge für diese Auswahl. <a href='/?status=all'>Auch erledigte zeigen</a>")
        else:
            table = af.empty("Noch keine Vorgänge. <a href='/neu'>Neuer Vorgang</a> legt den ersten an; "
                             "Meldungen kommen auch über den Knopf „Feedback“ in jeder App.")
        paging = ""
        if total > size:
            paging = ("<nav class=oaap-paging aria-label=Seiten>"
                      + (f"<a href='{esc(link(offset=max(0, offset - size)))}'>← Vorige {size}</a>" if offset else "")
                      + f"<span>{offset + 1}–{min(total, offset + size)} von {total}</span>"
                      + (f"<a href='{esc(link(offset=offset + size))}'>Weitere {size} →</a>"
                         if offset + size < total else "") + "</nav>")
        count = (f"<p class=oaap-countline>{total} Vorgang{'' if total == 1 else 'e'}</p>" if total <= size
                 else af.countline(offset + 1, offset + len(shown), total, "Vorgängen"))
        body = quick + form + count + table + paging
        return self.send(200, page("Vorgänge", body, "home"))

    def new_form(self, who, err=""):
        queues = self.store.queues()
        kinds = "".join(f"<option value={k}>{esc(KIND_LABEL[k])}</option>" for k in KINDS)
        qs = "".join(f"<option value='{esc(q['id'])}'>{esc(q['title'])}</option>" for q in queues)
        body = af.msg(err, error=True) + f"""
<form method=post action=/neu class=oaap-card>
 <label for=k>Art</label><select id=k name=kind>{kinds}</select>
 <label for=q>Bereich</label><select id=q name=queue>{qs}</select>
 <label for=t>Betreff</label><input type=text id=t name=title maxlength=200 required>
 <label for=b>Beschreibung</label><textarea id=b name=body maxlength=10000></textarea>
 <p><button class=primary>Anlegen</button></p></form>"""
        return self.send(200, page("Neuer Vorgang", body, "new"))

    def new_post(self, who, f):
        made = self.store.create(kind=f.get("kind"), title=f.get("title"), body=f.get("body", ""),
                            reporter=who.id, reporter_name=who.name,
                            queue=f.get("queue") or DEFAULT_QUEUE,
                            attested={"app": APP_ID, "version": VERSION,
                                      "user_id": who.id, "via": "app"})
        return self.redirect(f"/v/{quote(made['number'])}")

    KIND_WORD = {"handler": "Bearbeiter/in", "clearing": "Clearing"}

    def queues_page(self, who, msg=""):
        """Die Bereiche als Liste; gepflegt wird auf der Seite eines Bereichs (RFC-0118 §7:
        eine Zeilenaktion, keine Formulare in Tabellenzeilen)."""
        queues = self.store.queues()
        kind_word = self.KIND_WORD
        rows = []
        for q in queues:
            mem = self.store.members(q["id"])
            names = ", ".join(f"{esc(m['name'])} ({kind_word.get(m['kind'], m['kind'])})" for m in mem) or \
                '<span class=muted>noch niemand</span>'
            mine = ("bearbeiten" if who.may("triage", q["id"]) else "lesen" if who.may("read", q["id"]) else "")
            fr_name = self.store.person_name(q["first_responder"]) if q.get("first_responder") else ""
            if fr_name:
                names += f" · {KI} {esc(fr_name)} (Erstbearbeitung)"
            if q.get("light"):
                names += f" · Ampel {LIGHT_WORD.get(q['light'], q['light'])}"
            title = (f"<a href='{esc(queue_href(q['id']))}'>{esc(q['title'])}</a>" if who.manager
                     else esc(q["title"]))
            rows.append(f"<tr><td>{title}</td><td class=oaap-minor><code>{esc(q['id'])}</code></td>"
                        f"<td>{names}</td><td>{mine}</td></tr>")
        body = (msg + f"<p>Ein Bereich ist eine Zuständigkeit: ein Thema oder ein Team. Vorgänge kommen im "
                f"<b>Eingang</b> an und werden von dort in den passenden Bereich verschoben.</p>"
                f"<p>Wer einen Bereich bearbeitet, trägt der Mandanten-Administrator auf der Seite des "
                f"Bereichs ein. <b>Bearbeiter/innen</b> lesen, kommentieren, ändern den Status, verschieben und "
                f"schließen; das <b>Clearing</b> darf zusätzlich an einen Partner-Mandanten übergeben. "
                f"Der Mandanten-Administrator bearbeitet jeden Bereich (übergeben nur als Clearing). "
                f"In großen Mandanten geht dasselbe über <b>Rollen &amp; Rechte</b> (Vorlagen "
                f"<i>Bearbeiter/in</i>, <i>Clearing</i>, Kontext = Kennung des Bereichs); beides zählt.</p>"
                f"<p class=muted>Löschfrist: {RETENTION} Monate nach dem Schließen oder Ablehnen "
                f"wird die meldende Person aus einem Vorgang entfernt "
                f"(Einstellung <code>VORGAENGE_RETENTION_MONTHS</code> dieser Instanz).</p>"
                + ("<div class=oaap-tablewrap><table class=oaap-table><thead><tr><th>Bereich</th>"
                   "<th class=oaap-minor>Kennung</th><th>Zuständige</th><th>Ich darf</th></tr></thead><tbody>"
                   + "".join(rows) + "</tbody></table></div>" if rows else af.empty("Noch kein Bereich.")))
        if who.manager:
            log = self.store.queue_log(15)
            if log:
                body += ("<h2>Zuletzt geändert</h2><div class=oaap-tablewrap><table class=oaap-table><tbody>" + "".join(
                    f"<tr><td class=muted>{esc(x['at'][:16].replace('T', ' '))}</td><td>{esc(x['queue'])}</td>"
                    f"<td>{esc(x['name']) if x['action'] in ('set', 'remove') else ''} {'eingetragen als ' + kind_word.get(x['kind'], x['kind']) if x['action'] == 'set' else 'entfernt' if x['action'] == 'remove' else 'Ampel ' + LIGHT_WORD.get(x['kind'] if x['kind'] != '-' else '', x['kind']) if x['action'] == 'light' else 'Freigabeschlüssel neu erzeugt' if x['action'] == 'approval_key' else 'Prüfung: Stichprobe ' + esc(x['kind']) if x['action'] == 'review' else esc(x['action'])}</td>"
                    f"<td class=muted>von {esc(x['actor_name'])}</td></tr>" for x in log) + "</tbody></table></div>")
            body += """<h2>Bereich anlegen</h2>
<form method=post action=/bereiche class=inline>
 <div><label for=i>Kennung</label><input id=i name=id pattern='[a-z0-9][a-z0-9-]{0,39}' required></div>
 <div><label for=t>Titel</label><input id=t name=title maxlength=80 required></div>
 <button class=primary>Anlegen</button></form>
<p class=muted>Unterschriebene Freigaben für Spezialisten: <a href=/bereiche/freigabeschluessel>Freigabeschlüssel</a>.</p>"""
        return self.send(200, page("Bereiche", body, "queues"))

    def queue_page(self, who, qid):
        """Die Seite eines Bereichs: Zuständige, Erstbearbeitung durch einen Spezialisten
        und die Ampel -- nur für den Mandanten-Administrator, wie die Formulare selbst."""
        q = self.store.queue(qid)
        if not q or not who.manager:
            return self.send(404, page("Nicht gefunden", af.empty("Diesen Bereich gibt es nicht. "
                                                                "<a href='/bereiche'>Zu den Bereichen</a>"), "queues"))
        kind_word = self.KIND_WORD
        people = self.store.people()
        mem = self.store.members(q["id"])
        fr_name = self.store.person_name(q["first_responder"]) if q.get("first_responder") else ""
        opts = "".join(f"<option value='{esc(p['uid'])}'>{esc(p['name'])}"
                       f"{' (' + esc(p['username']) + ')' if p['username'] and p['username'] != p['name'] else ''}"
                       f"</option>" for p in people)
        members = "".join(
            f"<li><form method=post action=/bereiche/mitglied class=inline style='margin:.2rem 0'>"
            f"<input type=hidden name=queue value='{esc(q['id'])}'><input type=hidden name=uid value='{esc(m['uid'])}'>"
            f"<input type=hidden name=op value=remove><span>{esc(m['name'])} ({kind_word.get(m['kind'], m['kind'])})</span>"
            f"<button class=danger>entfernen</button></form></li>" for m in mem) or "<li class=muted>noch niemand</li>"
        body = (af.card("Zuständige pflegen",
                        f"<ul style='list-style:none;padding:0;margin:0'>{members}</ul>"
                        f"<form method=post action=/bereiche/mitglied class=inline>"
                        f"<input type=hidden name=queue value='{esc(q['id'])}'><input type=hidden name=op value=set>"
                        f"<div><label>Person</label><select name=uid required>{opts}</select></div>"
                        f"<div><label>als</label><select name=kind><option value=handler>Bearbeiter/in</option>"
                        f"<option value=clearing>Clearing (darf auch übergeben)</option></select></div>"
                        f"<button class=primary>Eintragen</button></form>"
                        f"<p class=muted>Fehlt jemand? Die Person öffnet die App Vorgänge einmal, danach steht "
                        f"sie hier. Ein Verzeichnis aller Benutzer bekommt diese App nicht.</p>")
                + af.card("Erstbearbeitung durch einen Spezialisten",
                          f"<form method=post action=/bereiche/spezialist class=inline>"
                          f"<input type=hidden name=queue value='{esc(q['id'])}'>"
                          f"<div><label>Spezialist (KI)</label><select name=uid><option value=''>keiner</option>"
                          + "".join(f"<option value='{esc(p['uid'])}'{' selected' if p['uid'] == q.get('first_responder') else ''}>"
                                    f"{esc(p['name'])}</option>" for p in people)
                          + f"</select></div><button>Speichern</button></form>"
                          f"<p class=muted>Neue und hierher verschobene Vorgänge bekommt zuerst dieser Spezialist "
                          f"als Verantwortlichen. Er arbeitet im Hintergrund, schreibt nur intern und gibt "
                          f"an die Zuständigen zurück. Zur Auswahl steht er, sobald die App Spezialisten einmal für "
                          f"ihn gelaufen ist.</p>")
                + af.card("Ampel für Spezialisten",
                          f"<form method=post action=/bereiche/ampel class=inline>"
                          f"<input type=hidden name=queue value='{esc(q['id'])}'>"
                          f"<div><label>Ampel</label><select name=light>"
                          + "".join(f"<option value='{v}'{' selected' if v == (q.get('light') or '') else ''}>"
                                    f"{LIGHT_WORD.get(v, 'keine eigene')}</option>" for v in ("", "green", "yellow", "red"))
                          + f"</select></div><button>Speichern</button></form>"
                          f"<p class=muted>Ein Spezialist schickt diese Ampel an das KI- und das MCP-Gateway. Sie "
                          f"nehmen dann nur Modelle und Sichten bis zu dieser Farbe – grün ist am strengsten. "
                          f"Ohne eigene gelten die Decken seiner Schlüssel. Unterschriebene Freigaben: "
                          f"<a href=/bereiche/freigabeschluessel>Freigabeschlüssel</a>.</p>")
                + af.card("Prüfung",
                          f"<form method=post action=/bereiche/pruefung class=inline>"
                          f"<input type=hidden name=queue value='{esc(q['id'])}'>"
                          f"<div><label>Stichprobe (%)</label><input name=share type=number min=0 max=100 "
                          f"value='{int(q.get('review_share') or 0)}' style='width:6rem'></div>"
                          f"<div><label>Prüfender Spezialist (KI)</label><select name=uid><option value=''>keiner – "
                          f"nur Menschen</option>"
                          + "".join(f"<option value='{esc(p['uid'])}'{' selected' if p['uid'] == q.get('reviewer') else ''}>"
                                    f"{esc(p['name'])}</option>" for p in people if p["uid"] in self.store.specialists())
                          + f"</select></div><button>Speichern</button></form>"
                          f"<p class=muted>Von den Vorgängen, die ein leitender Spezialist schließt, geht dieser Anteil "
                          f"zufällig zur Prüfung – zusätzlich zu denen, die er selbst markiert, die ohne Änderung "
                          f"geschlossen wurden, die an eine Grenze stießen oder in denen etwas ohne Einzelfreigabe "
                          f"lief. Prüfen können die Zuständigen und, wenn eingetragen, ein Spezialist; er prüft nie "
                          f"seine eigene Arbeit. „Nacharbeit“ öffnet den Vorgang wieder.</p>"))
        head = (f"<div class=oaap-objhead><h1>{esc(q['title'])}</h1>"
                + af.facts([("Kennung", f"<code>{esc(q['id'])}</code>"),
                            ("Zuständige", str(len(mem))),
                            ("Erstbearbeitung", f"{KI} {esc(fr_name)}" if fr_name else "keine"),
                            ("Ampel", esc(LIGHT_WORD.get(q.get("light") or "", "keine eigene"))),
                            ("Stichprobe", f"{int(q.get('review_share') or 0)} %")]) + "</div>")
        return self.send(200, page(q["title"], head + body, "queues", h1=False,
                                   back=af.back("/bereiche", "Zu den Bereichen"),
                                   obj=f"Bereich:{q['id']}", obj_title=q["title"]))

    def queues_post(self, who, f):
        if not who.manager:
            raise Refused("Bereiche legt der Mandanten-Administrator an.")
        self.store.add_queue(f.get("id"), f.get("title"))
        return self.redirect("/bereiche")

    def member_post(self, who, f):
        """0.8.0 (RFC-0103 D2): Zuständige pflegt nur der tenant_admin (oder admin) --
        Zuständige tragen keine weiteren ein."""
        if not who.manager:
            raise Refused("Zuständige eines Bereichs trägt der Mandanten-Administrator ein.")
        queue, uid = f.get("queue", ""), f.get("uid", "")
        if f.get("op") == "remove":
            self.store.remove_member(queue, uid, who.id)
        else:
            self.store.set_member(queue, uid, f.get("kind", ""), who.id)
        return self.redirect(queue_href(queue))

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
        if att.get("node") and c["object_kind"] == "request":
            src = (f"<div class=muted>Anfrage an den Betreiber, geschrieben vom Knoten "
                   f"<b>{esc(att['node'])}</b> für <b>{esc(att.get('on_behalf', ''))}</b> "
                   "(beglaubigt ist der Knoten, nicht die Person)</div>")
        elif att.get("node"):
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
                        f"nicht beglaubigt)</summary><table class=oaap-table>{rows}</table></details>")
        ent, ent_int = [], []
        names = {p["uid"]: p["name"] for p in self.store.people()}
        decided = {e["data"].get("plan"): e for e in c["entries"] if e["type"] == "approval"}
        specs = self.store.specialists()
        consults = {k["seq"]: k for k in c.get("consults") or []}
        for e in visible_entries(who, c):
            # 0.11.0 (D6): Öffentliches im Reiter „Verlauf“, Internes im Reiter „Intern“
            box = ent_int if e.get("internal") else ent
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
            elif e["type"] == "spec":
                # 0.13.0 (RFC-0105 §4): der „dunkle Chat“ -- Notizen offen, Werkzeugaufrufe zugeklappt
                k = d.get("kind")
                label = {"note": "Notiz", "tool": "Werkzeug " + esc(d.get("tool") or ""),
                         "plan": "plant " + esc(d.get("tool") or ""), "draft": "Entwurf an die Meldende",
                         "end": "Lauf beendet" + (f" ({esc(SPEC_END.get(d.get('reason'), d.get('reason') or ''))})"
                                                 if d.get("reason") else "")}.get(k, "Schritt")
                meta = esc(" · ".join(x for x in (str(d.get("assistant") or ""), ("Lauf " + str(d["run"])) if d.get("run") else "") if x))
                body = (f"<details><summary class=muted>Aufruf und Ergebnis</summary><pre>{esc(e['text'])}</pre></details>"
                        if k == "tool" else f"<div class=body data-md>{esc(e['text'])}</div>")
                if k == "plan":
                    body += (f"<pre>{esc(d.get('tool') or '')} {esc(json.dumps(d.get('arguments') or {}, ensure_ascii=False, indent=1))}</pre>"
                             f"<div class=muted>SHA-256 {esc((d.get('sha256') or '')[:16])}…</div>")
                if k in ("plan", "draft"):
                    dec = decided.get(e["seq"])
                    if dec:
                        body += (f"<div class=muted>{'freigegeben' if dec['data'].get('ok') else 'abgelehnt'} von "
                                 f"{esc(dec['actor_name'])}{(': ' + esc(dec['text'])) if dec['text'] else ''}</div>")
                    elif who.may("triage", c["queue"]) and who.id != e["actor"]:
                        yes = "Veröffentlichen" if k == "draft" else "Freigeben"
                        body += (f"<form method=post action='/v/{quote(c['number'])}/freigabe' class=inline>"
                                 f"<input type=hidden name=seq value='{e['seq']}'>"
                                 f"<input name=text placeholder='Bemerkung (optional)' maxlength=2000>"
                                 f"<button name=ok value=1 class=primary>{yes}</button> "
                                 f"<button name=ok value=0>"
                                 f"{'Verwerfen' if k == 'draft' else 'Ablehnen'}</button></form>")
                box.append(f"<div class='card c-ki'><div class=muted>{when} · {who_} "
                           f"{KI} · {label}{(' · ' + meta) if meta else ''}</div>{body}</div>")
                continue
            elif e["type"] == "approval":
                box.append(f"<div class=card><div class=muted>{when} · {who_} · "
                           f"{'gibt frei' if d.get('ok') else 'lehnt ab'}: "
                           f"{'Entwurf' if d.get('kind') == 'draft' else 'geplanten Aufruf'}"
                           f"{(' – ' + esc(e['text'])) if e['text'] else ''}</div></div>")
                continue
            elif e["type"] in ("consult", "consult_answer"):
                # 0.15.0 (§6): Rückfrage und Antwort -- intern
                ai = " " + KI if e["actor"] in specs else ""
                if e["type"] == "consult":
                    k = consults.get(e["seq"]) or {}
                    head = (f"fragt {esc(d.get('to_name') or '')}"
                            + (" " + KI if d.get("specialist") else "")
                            + (" · beantwortet" if k.get("answered") else " · <b>offen</b>"))
                    form_ = ""
                    if k and not k["answered"] and k["target"] == who.id and c["status"] not in ("closed", "rejected"):
                        form_ = (f"<form method=post action='/v/{quote(c['number'])}/rat'>"
                                 f"<input type=hidden name=seq value='{e['seq']}'>"
                                 f"<textarea name=text maxlength=10000 required placeholder='Deine Antwort (intern)'></textarea>"
                                 f"<p><button>Antworten</button></p></form>")
                else:
                    head = "antwortet auf die Rückfrage"
                box.append(f"<div class='card c-rat'><div class=muted>{when} · {who_}{ai} · {head}</div>"
                           f"<div class=body data-md>{esc(e['text'])}</div>{form_ if e['type'] == 'consult' else ''}</div>")
                continue
            elif e["type"] in ("task", "task_result"):
                # 0.22.0 (RFC-0134 §5): Auftrag des Leiters und sein Ergebnis -- intern
                if e["type"] == "task":
                    k = consults.get(e["seq"]) or {}
                    head = (f"Auftrag {d.get('n') or ''} an {esc(d.get('to_name') or '')} {KI}"
                            + (" · erledigt" if k.get("answered") else " · <b>offen</b>"))
                else:
                    head = "Ergebnis des Auftrags"
                box.append(f"<div class='card c-rat'><div class=muted>{when} · {who_} {KI} · {head}</div>"
                           f"<div class=body data-md>{esc(e['text'])}</div></div>")
                continue
            elif e["type"] in ("review_mark", "review"):
                # 0.23.0 (RFC-0134 §8): Prüfmerkmal und Prüfung -- intern
                if e["type"] == "review_mark":
                    head = "zur Prüfung markiert: " + esc(REVIEW_REASON.get(d.get("reason"), d.get("reason") or ""))
                else:
                    head = "Prüfung: " + esc(REVIEW_RESULT.get(d.get("result"), "kein Urteil"))
                ai = " " + KI if e["actor"] in specs else ""
                text_ = f"<div class=body data-md>{esc(e['text'])}</div>" if e["text"] else ""
                box.append(f"<div class='card c-int'><div class=muted>{when} · {who_}{ai} · {head}</div>{text_}</div>")
                continue
            elif e["type"] == "spec_stop":
                box.append(f"<div class=card><div class=muted>{when} · {who_} · Spezialist "
                           f"{'angehalten' if d.get('on') else 'wieder zugelassen'}</div></div>")
                continue
            elif e["type"] == "role":
                word = self.store.ROLE_WORD.get(d.get("role"), d.get("role", ""))
                head = (f"{word}: {esc(names.get(d.get('to'), d.get('to')))}" if d.get("to")
                        else f"{word}: niemand mehr")
                box.append(f"<div class=card><div class=muted>{when} · {who_} · {head}</div></div>")
                continue
            else:
                head = "Kommentar"
            if e.get("internal"):
                head += " " + af.badge("intern", "warn")
            text = f"<div class=body data-md>{esc(e['text'])}</div>" if e["text"] else ""
            box.append(f"<div class='card{' c-int' if e.get('internal') else ''}'>"
                       f"<div class=muted>{when} · {who_} · {head}</div>{text}</div>")
        forms = comment_form = intern_form = hand = ""
        base = f"/v/{quote(c['number'])}"
        # 0.13.0 (RFC-0105 §5): anhalten / wieder zulassen -- wer den Bereich bearbeitet
        if who.may("triage", c["queue"]) and c["status"] not in ("closed", "rejected") and (
                c.get("spec_stop") or any(e["type"] == "spec" for e in c["entries"])):
            on = not c.get("spec_stop")
            forms += (f"<div class=card><form method=post action='{base}/spezstop' class=inline>"
                      f"<input type=hidden name=on value='{1 if on else 0}'>"
                      f"<button>{'Spezialist anhalten' if on else 'Spezialist wieder zulassen'}</button></form>"
                      f"<span class=muted> {'Der Lauf endet nach dem laufenden Schritt; danach arbeitet hier kein Spezialist, bis ihn jemand wieder zulässt.' if on else 'An diesem Vorgang ist der Spezialist angehalten.'}</span></div>")
        # 0.15.0 (§6): eine Rückfrage stellen -- wer verantwortlich ist oder den Bereich bearbeitet
        if (who.may("triage", c["queue"]) or c.get("responsible") == who.id) and c["status"] not in ("closed", "rejected"):
            pool = self.store.consultable(c, who.id)
            if pool:
                opts = "".join(f"<option value='{esc(p['uid'])}'>{esc(p['name'])}{' (KI)' if p['specialist'] else ''}</option>"
                               for p in pool)
                forms += (f"<div class=card><form method=post action='{base}/rueckfrage'>"
                          f"<label>Rückfrage an</label><select name=uid>{opts}</select>"
                          f"<label>Frage (intern; die Befragten antworten einmal und werden nicht verantwortlich)</label>"
                          f"<textarea name=text maxlength=10000 required></textarea>"
                          f"<p><button>Rückfrage stellen</button></p></form></div>")
        # 0.23.0 (RFC-0134 §8): prüfen, wenn markiert; markieren, wenn nicht -- wer den Bereich bearbeitet
        if who.may("triage", c["queue"]):
            reasons = ", ".join(REVIEW_REASON.get(x, x) for x in c["review_reasons"])
            if c["review_reasons"]:
                forms += (f"<div class=card><h2 style='margin-top:0'>Zur Prüfung ({esc(reasons)})</h2>"
                          f"<form method=post action='{base}/pruefung'>"
                          f"<label>Ergebnis der Prüfung (intern; bei Nacharbeit Pflicht)</label>"
                          f"<textarea name=text maxlength=10000></textarea>"
                          f"<p><button name=result value=ok class=primary>In Ordnung</button> "
                          f"<button name=result value=rework>Nacharbeit</button></p></form>"
                          f"<p class=muted>„Nacharbeit“ öffnet einen beendeten Vorgang wieder; der Leiter ist dann "
                          f"wieder dran.</p></div>")
            else:
                forms += (f"<div class=card><form method=post action='{base}/pruefmarke' class=inline>"
                          f"<input name=text required maxlength=2000 placeholder='Grund' style='width:20rem'>"
                          f"<button>Zur Prüfung markieren</button></form></div>")
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
            # 0.10.0 (D5): für Bearbeitende ist „intern“ vorbelegt -- ein interner Kommentar,
            # der bei der Meldenden landet, wiegt schwerer als der umgekehrte Fehler
            tick = ("<label style='display:inline;color:inherit'><input type=checkbox name=internal "
                    "value=1 checked> intern (die meldende Person sieht ihn nicht)</label> "
                    if can_read_internal(who, c) else "")
            comment_form = (f"<h2>Kommentar</h2><form method=post action='{base}/comment'>"
                            f"<textarea name=text maxlength=10000 required></textarea>"
                            f"<p>{tick}<button>Kommentieren</button></p></form>")
            if can_read_internal(who, c):
                intern_form = (f"<h2>Interner Kommentar</h2><form method=post action='{base}/comment'>"
                               f"<input type=hidden name=internal value=1>"
                               f"<textarea name=text maxlength=10000 required></textarea>"
                               f"<p><button>Intern kommentieren</button></p></form>")
        if can_set_roles(who, c) and c["status"] not in ("closed", "rejected"):
            forms += self._roles_form(c, base)
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
            hand += (f"<h2>Übergeben</h2><form method=get action='{base}/uebergabe' class=inline>"
                      f"<select name=partner>{opts}</select><button>Entwurf öffnen</button></form>")
        got = self.store.received_from(c)
        if got and got[1] and who.may("hand_over", c["queue"]):
            hand += (f"<h2>Antwort an {esc(got[1]['name'])}</h2>"
                      f"<p class=muted>Verlässt den Mandanten. Keine Namen von Personen.</p>"
                      f"<form method=post action='{base}/antwort'>"
                      f"<textarea name=text maxlength=10000 required></textarea>"
                      f"<p><button>Antwort senden</button></p></form>")
        out = self.store.outbox_for_case(c["id"]) if who.may("read", c["queue"]) else []
        for o in out:
            state = (f"quittiert {esc(o['acked'][:16].replace('T', ' '))}" if o["acked"]
                     else f"offen, Versuch {o['attempts']}"
                     + (f" · {esc(o['last_error'])}" if o["last_error"] else ""))
            hand += (("<h2>Ausgang</h2>" if o is out[0] else "")
                      + f"<div class=muted><code>{esc(o['handover_id'][:8])}</code> · {state}</div>")
        observers = (f"<div class=muted>Beobachtet von: {esc(', '.join(qt.get(o, o) for o in c['observers']))}</div>"
                     if c["observers"] else "")
        # 0.10.0 (D3): verantwortlich, zugewiesen, beteiligt -- nur für die Bearbeitenden
        if handles(who, c):
            rs, asg = c.get("responsible"), c.get("assignee")
            former = [names.get(u, u) for u in c.get("involved") or () if u not in (rs, asg)]
            observers += (f"<div class=muted>Verantwortlich: <b>{esc(names.get(rs, rs)) if rs else '–'}</b>"
                          f" · Zugewiesen: <b>{esc(names.get(asg, asg)) if asg else '–'}</b>"
                          + (f" · Beteiligt: {esc(', '.join(former))}" if former else "") + "</div>")
        # 0.7.0: Wer hier nichts umstellen kann, erfährt warum -- statt eines fehlenden Formulars.
        rights = ""
        if not nxt and c["status"] not in ("closed", "rejected") and not (
                c["reporter"] == who.id and c["status"] == "answered"):
            how = (f"Im Bereich <b>{esc(qt.get(c['queue'], c['queue']))}</b> stellen ihn die "
                   "Zuständigen dieses Bereichs um (Seite „Bereiche“) und der "
                   "Mandanten-Administrator"
                   + (", im Eingang auch die Administratoren der App" if c["queue"] == DEFAULT_QUEUE else "")
                   + ". In großen Mandanten vergibt „Rollen &amp; Rechte“ dasselbe mit den Vorlagen "
                   "<i>Bearbeiter/in</i> und <i>Clearing</i>.")
            mine = (" Als Meldende kannst du im Reiter <a href='#verlauf'>Verlauf</a> kommentieren und, "
                    "sobald eine Antwort da ist, sagen, ob die Lösung passt." if c["reporter"] == who.id else "")
            if c["reporter"] == who.id and c["status"] == "waiting_reporter":
                mine = (" <b>Rückfrage an dich:</b> antworte im Reiter <a href='#verlauf'>Verlauf</a> mit "
                        "einem Kommentar, dann geht der Vorgang von selbst zurück „in Arbeit“.")
            if was_involved(who, c) and not is_assignee(who, c):
                mine += (" Du warst an diesem Vorgang beteiligt und liest ihn weiter; ändern kann, "
                         "wer ihn jetzt bearbeitet.")
            rights = f"<div class=rights>Den Status kannst du hier nicht ändern. {how}{mine}</div>"
        toggle = ('<div style="float:right" class=seg role=group aria-label="Darstellung">'
                  '<button type=button data-md-mode=formatted aria-pressed=true>Formatiert</button>'
                  '<button type=button data-md-mode=raw aria-pressed=false>Roh</button></div>')
        overview = (f"<div class=card>{toggle}<div><b>{esc(KIND_LABEL.get(c['kind'], c['kind']))}</b> · "
                    f"{pill(c['status'], c.get('mark', ''), c['reporter'] == who.id)}"
                    + (" " + af.badge("zur Prüfung", "warn") if c["review_reasons"] and handles(who, c) else "")
                    + f" · Bereich "
                    f"{esc(qt.get(c['queue'], c['queue']))} · "
                    f"gemeldet von {esc(c['reporter_name'])}</div>{obj}{src}{observers}"
                    f"<div class=body data-md style='margin-top:.6rem'>{esc(c['body'])}</div></div>"
                    + rights + forms)
        # 0.11.0 (RFC-0103 D6): Reiter als Anker mit CSS -- ohne Skript, die Adresse behält
        # den Reiter. „Intern“ nur für seine Leser, „Übergabe“ nur fürs Clearing.
        panels = [("verlauf", f"Verlauf ({len(ent)})", "".join(ent) or "<p class=muted>Noch keine Einträge.</p>")]
        panels[0] = (*panels[0][:2], panels[0][2] + comment_form)
        if can_read_internal(who, c):
            panels.append(("intern", f"Intern ({len(ent_int)})",
                           ("".join(ent_int) or "<p class=muted>Keine internen Einträge.</p>")
                           + "<p class=muted>Die meldende Person sieht diesen Reiter nicht.</p>" + intern_form))
        if techbox:
            panels.append(("technik", "Technik", techbox))
        if hand:
            panels.append(("uebergabe", "Übergabe", hand))
        tabs = "".join(f"<a href='#{k}'>{esc(t)}</a>" for k, t, _ in [("ueberblick", "Überblick", "")] + panels)
        # RFC-0118 §6: die Reiter des Vorgangs SIND die eine Leiste unter der Kopfleiste.
        # Den aktiven hebt CSS hervor (wo der Browser :has kann; sonst ohne Hervorhebung).
        on = ("color:var(--oaap-color-primary);border-bottom-color:var(--oaap-color-primary);font-weight:600")
        hl = ("<style>" + "".join(f"body:has(#{k}:target) nav.oaap-tabs a[href='#{k}']{{{on}}}" for k, _, _ in panels)
              + f"body:not(:has(.tp:target)) nav.oaap-tabs a[href='#ueberblick'],"
              f"body:has(#ueberblick:target) nav.oaap-tabs a[href='#ueberblick']{{{on}}}</style>")
        bar = f"<nav class=oaap-tabs aria-label='Reiter des Vorgangs'>{tabs}</nav>"
        body = (hl + "<div class=panels>"
                + "".join(f"<section class=tp id={k} aria-label='{esc(t)}'>{h}</section>" for k, t, h in panels)
                + f"<section class=tp id=ueberblick aria-label='Überblick'>{overview}</section></div>")
        return self.send(200, page(f"{c['number']}: {c['title']}", body, back=af.back("/", "Zurück zur Übersicht"),
                                   tabs_html=bar, obj=f"Vorgang:{c['number']}", obj_title=c["title"]))

    def _roles_form(self, c, base):
        """0.10.0 (D3): verantwortlich = ein Zuständiger des Bereichs (aus der Liste auf
        „Bereiche“), zugewiesen = jede Person, die die App kennt."""
        people = self.store.people()
        members = {m["uid"] for m in self.store.members(c["queue"])}

        def opts(pool, cur):
            o = "<option value=''>– niemand –</option>"
            return o + "".join(f"<option value='{esc(p['uid'])}'{' selected' if p['uid'] == cur else ''}>"
                               f"{esc(p['name'])}</option>" for p in pool)
        resp_pool = [p for p in people if p["uid"] in members or p["uid"] == c.get("responsible")]
        return (f"<h2>Zuständig für diesen Vorgang</h2>"
                f"<form method=post action='{base}/rolle' class=inline><input type=hidden name=role value=responsible>"
                f"<div><label>Verantwortlich (aus dem Bereich)</label><select name=uid>"
                f"{opts(resp_pool, c.get('responsible'))}</select></div><button>Setzen</button></form>"
                f"<form method=post action='{base}/rolle' class=inline><input type=hidden name=role value=assignee>"
                f"<div><label>Zugewiesen (liest, kommentiert, ändert den Status)</label><select name=uid>"
                f"{opts(people, c.get('assignee'))}</select></div><button>Zuweisen</button></form>"
                f"<p class=muted>Verschieben leert „verantwortlich“. Wer einmal verantwortlich oder "
                f"zugewiesen war, liest den Vorgang weiter.</p>")

    def target_who(self, uid):
        """Die Rechte einer ANDEREN Person (für „verantwortlich“): ihre Zuständigkeiten
        und Grants, ohne Plattformrollen -- die kennt die App nur von der fragenden."""
        return Who(uid, uid, set(), self.authz.grants(uid), self.store.memberships(uid))

    def case_post(self, who, number, action, f):
        c = self.store.get(number)
        if not c or not can_see(who, c):
            return self.send(404, page("Nicht gefunden", ""))
        if action == "comment":
            if not can_comment(who, c):
                raise Refused("Kommentieren dürfen hier nur die Meldende und der Bereich.")
            internal = f.get("internal") == "1"
            if internal and not can_read_internal(who, c):
                raise Refused("Intern kommentieren können nur die Bearbeitenden.")
            self.store.comment(c, who.id, who.name, f.get("text"), internal)
            return self.redirect(f"/v/{quote(c['number'])}#{'intern' if internal else 'verlauf'}")
        elif action == "rolle":
            if not can_set_roles(who, c):
                raise Refused("Verantwortlich und zugewiesen setzen die Zuständigen des Bereichs "
                              "und der Mandanten-Administrator.")
            role, uid = f.get("role", ""), (f.get("uid") or "").strip()
            if role == "responsible" and uid and not self.target_who(uid).may("triage", c["queue"]):
                raise Refused("Verantwortlich ist ein Zuständiger dieses Bereichs (Seite „Bereiche“). "
                              "Für jemand anderen: zuweisen.")
            self.store.set_role(c, role, uid, who.id, who.name)
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
        elif action == "freigabe":
            # 0.14.0 (RFC-0105 §7.1/§7.2): wer den Bereich bearbeitet, nie der Spezialist selbst
            if not who.may("triage", c["queue"]):
                raise Refused("Freigeben darf, wer den Bereich bearbeitet.")
            try:
                seq = int(f.get("seq") or 0)
            except ValueError:
                raise Refused("Unbekannter Schritt.")
            self.store.decide(c, seq, f.get("ok") == "1", who.id, who.name, f.get("text", ""),
                              signer=sign_approval)
        elif action == "rueckfrage":
            # 0.15.0 (§6): wer verantwortlich ist oder den Bereich bearbeitet
            handler = who.may("triage", c["queue"])
            if not handler and c.get("responsible") != who.id:
                raise Refused("Rückfragen stellt, wer verantwortlich ist oder den Bereich bearbeitet.")
            self.store.consult(c, who.id, who.name, f.get("uid", ""), f.get("text", ""), as_handler=handler)
        elif action == "rat":
            try:
                seq = int(f.get("seq") or 0)
            except ValueError:
                raise Refused("Unbekannte Rückfrage.")
            self.store.answer_consult(c, who.id, who.name, seq, f.get("text", ""))
        elif action == "pruefmarke":
            if not who.may("triage", c["queue"]):
                raise Refused("Zur Prüfung markiert, wer den Bereich bearbeitet.")
            self.store.mark_review(c, who.id, who.name, f.get("text", ""), by="person")
        elif action == "pruefung":
            if not who.may("triage", c["queue"]):
                raise Refused("Prüfen darf, wer den Bereich bearbeitet.")
            self.store.review(c, who.id, who.name, f.get("result", ""), f.get("text", ""))
        elif action == "spezstop":
            if not who.may("triage", c["queue"]):
                raise Refused("Anhalten darf, wer den Bereich bearbeitet.")
            self.store.set_spec_stop(c, f.get("on") == "1", who.id, who.name)
        elif action == "antwort":
            if not who.may("hand_over", c["queue"]):
                raise Refused("Antworten an einen Partner darf nur das Clearing dieses Bereichs.")
            self.store.answer(c, who.id, who.name, f.get("text", ""))
            self.kick.set()
        else:
            return self.send(404, "")
        return self.redirect(f"/v/{quote(c['number'])}")


SPEC_END = {"done": "fertig", "gave_back": "zurückgegeben", "limit": "Grenze erreicht",
            "stopped": "angehalten", "error": "Fehler", "needs_approval": "wartet auf Freigabe",
            "closed": "abgeschlossen durch die Leitung"}


MCP_RULES = mcp_door.Rules(
    can_see=can_see, can_comment=can_comment, can_read_internal=can_read_internal,
    refused=Refused, kinds=KINDS,
    kind_label=KIND_LABEL, status_label=STATUS_LABEL, default_queue=DEFAULT_QUEUE,
    open_statuses=OPEN,
    attest=lambda who: {"app": APP_ID, "version": VERSION, "user_id": who.id, "via": "mcp"})


def main():
    global STORE, AUTHZ, RETENTION
    os.makedirs(DATA_DIR, exist_ok=True)
    STORE = Store(os.path.join(DATA_DIR, "vorgaenge.db"), events=bool(EVENTS_URL and EVENTS_KEY))
    RETENTION = retention_months()
    ACCEPT_DAYS_NOW[0] = accept_days()
    print(f"Löschfrist: {RETENTION} Monate nach Abschluss; beantwortet ohne "
          f"Rückmeldung geschlossen nach {ACCEPT_DAYS_NOW[0]} Tagen", flush=True)
    threading.Thread(target=retention_loop, args=(RETENTION, ACCEPT_DAYS_NOW[0]), daemon=True).start()
    threading.Thread(target=outbox_loop, args=(STORE, OUTBOX_KICK), daemon=True).start()
    if STORE.events:
        threading.Thread(target=events_loop, args=(STORE,), daemon=True).start()
    AUTHZ = authz_client.Authz(app=None)
    if not AUTHZ.url:
        print("OAAP_AUTHZ_URL fehlt: nur die Admins sehen den Eingang, "
              "Bereiche lassen sich nicht vergeben.", flush=True)
    print(f"vorgaenge {VERSION} on :{PORT}", flush=True)
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
