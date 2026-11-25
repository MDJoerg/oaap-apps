#!/usr/bin/env python3
"""Gespräche im Arbeitsraum (RFC-0121 §5) ohne Netz: Kontext, Befehle, Ablegen.

Run: python3 apps/chat/test_workspace.py
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["CHAT_DATA_DIR"] = tempfile.mkdtemp()
import app  # noqa: E402
import loop  # noqa: E402
import workspace as W  # noqa: E402

FAILED, PASSED = [], [0]


def check(label, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"  PASS  {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}  {detail}")


INFO = {"id": "verein", "title": "Vereinsarbeit", "description": "Der Verein.",
        "home": "notizen", "state": "notizen:/arbeitsraum/stand", "log": "notizen:/arbeitsraum/protokoll",
        "mounts": [{"alias": "notizen", "role": "work", "home": True, "title": "Handbuch",
                    "kind": "bookstack", "usable": True, "entry": "notizen:/willkommen"},
                   {"alias": "vorstand", "role": "source", "home": False, "title": "Vorstand",
                    "kind": "bookstack", "usable": False, "entry": None}]}

# --- Kontext
ctx = W.context_text(INFO, {"text": "# Stand\n## Offen\n- Satzung", "revision": "3"}, None)
check("Kontext nennt Ablagen mit Rolle, Einstieg und Erreichbarkeit",
      "notizen: Handbuch (bookstack, Arbeitsablage · Heimat · Einstieg notizen:/willkommen)" in ctx
      and "vorstand: Vorstand (bookstack, Quelle · für dich nicht erreichbar)" in ctx, ctx)
check("Kontext trägt den Stand, als fremder Inhalt gekennzeichnet",
      "- Satzung" in ctx and W.FOREIGN in ctx and ctx.index(W.FOREIGN) < ctx.index("- Satzung"))
check("fehlender Stand wird gesagt", "noch nicht angelegt" in W.context_text(INFO, None, None))

# --- Befehle
check("/abschluss privat: kein Protokolleintrag",
      "nichts ins Protokoll" in W.abschluss_prompt(INFO, "T", "Anna", True))
p = W.abschluss_prompt(INFO, "Satzung", "Anna", False, "23.11.2026")
check("/abschluss: Stand und Protokoll, neueste zuerst", "notizen:/arbeitsraum/protokoll" in p
      and "## 23.11.2026 – Satzung" in p and "neueste zuerst" in p)

# --- woraus gelesen
conv = {"title": "Satzung prüfen", "created": "2026-11-23T10:00:00Z", "messages": [
    {"role": "user", "content": "Lies die Satzung", "at": "t1"},
    {"role": "assistant", "content": "", "at": "t2", "tool_calls": [
        {"id": "c1", "function": {"name": "store_read", "arguments": json.dumps({"path": "vorstand:/satzung"})}},
        {"id": "c2", "function": {"name": "store_edit", "arguments": json.dumps({"path": "notizen:/x"})}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "store_read", "content": "…", "at": "t3"},
    {"role": "tool", "tool_call_id": "c2", "name": "store_edit", "content": "ok", "confirmed": True, "at": "t3"},
    {"role": "assistant", "content": "Erledigt.", "at": "t4"}]}
check("read_from nennt nur gelesene Ablagen", W.read_from(conv) == ["vorstand"], W.read_from(conv))

# --- Ablegen
call, note = W.filing(conv, "", "notizen:/arbeitsraum", "Vereinshelfer", "Anna Probe", "Vereinsarbeit", 0)
args = json.loads(call["function"]["arguments"])
check("ohne Ziel in den Heimatordner, Name = Datum + Titel",
      args["path"].startswith("notizen:/arbeitsraum/") and args["path"].endswith("-satzung-pruefen")
      and args["title"] == args["path"].rsplit("/", 1)[1], args["path"])
check("Abschrift wörtlich: Fragen, Antworten, Werkzeuge mit Ziel, Zustimmung",
      "Lies die Satzung" in args["text"] and "Erledigt." in args["text"]
      and "`store_read` → `vorstand:/satzung`" in args["text"] and "(von der Person freigegeben)" in args["text"])
check("vor dem Schreiben: welche anderen Ablagen gelesen wurden (§8.8)", "vorstand" in note, note)
_, note2 = W.filing(conv, "vorstand:/", "notizen:/arbeitsraum", "A", "P", "W", 0)
check("Ziel = die gelesene Ablage: kein Hinweis auf sie selbst", note2 == "")
try:
    W.filing(conv, "ohne-doppelpunkt", "notizen:/x", "A", "P", "W", 0)
    check("Ziel ohne alias: wird abgelehnt", False)
except W.Refused:
    check("Ziel ohne alias: wird abgelehnt", True)
shown, _ = W.confirm_view(call, note)
check("Zustimmung zeigt Ziel und Umfang, nicht das Gespräch", "Zeichen" in shown["text"]
      and "Lies die Satzung" not in json.dumps(shown))

# --- das Ablegen geht nicht ans Modell
msgs = conv["messages"] + [{"role": "assistant", "by": "chat", "content": "", "tool_calls": [call]},
                           {"role": "tool", "tool_call_id": call["id"], "name": "store_write", "content": "ok"},
                           {"role": "assistant", "by": "chat", "content": "Abgelegt: …"}]
fm = loop.for_model(msgs)
check("for_model lässt Aufruf, Ergebnis und Satz des Chats weg",
      len(fm) == len(conv["messages"]) and all("Abgelegt" not in (x.get("content") or "") for x in fm))

# --- step(): Ablegen durch den Chat, mit Zustimmung, ohne Modellzug
CALLS = []


class FakeWay:
    def __init__(self, person, assertion, assistant, light="", ws=""):
        self.ws = ws

    def rpc(self, method, params, confirmed=False):
        CALLS.append((self.ws, method, params.get("name"), confirmed))
        if params.get("name") == "workspace_info":
            return [{"result": {"structuredContent": INFO, "content": []}}]
        if params.get("name") == "store_read":
            return [{"result": {"isError": True, "content": [{"type": "text", "text": "gibt es nicht"}]}}]
        return [{"result": {"content": [{"type": "text", "text": '{"path": "x", "revision": "1"}'}]}}]

    def call(self, name, args, confirmed=False):
        CALLS.append((self.ws, "tools/call", name, confirmed))
        return '{"path": "notizen:/arbeitsraum/x", "revision": "1", "created": true}'

    def tools(self):
        return []


app.McpWay = FakeWay
MODEL = []
app.ask_model = lambda *a, **k: (MODEL.append(a[6] if len(a) > 6 else k.get("context", "")) or {"role": "assistant", "content": "ok"})
A = {"id": "helfer", "title": "Vereinshelfer", "view": "arbeit"}
c = {"id": "abcdefabcdef", "assistant": "helfer", "messages": [], "workspace": {"id": "verein", "title": "Vereinsarbeit"}}
c["messages"].append({"role": "user", "content": "Hallo", "at": "t"})
st = app.step("p1", "Anna", "assert", A, c, None)
check("erster Modellzug bekommt den Kontext des Arbeitsraums, MCP mit X-OAAP-Workspace",
      st == "done" and "Vereinsarbeit" in MODEL[-1] and all(x[0] == "verein" for x in CALLS), CALLS)
n_model = len(MODEL)
handled = app.workspace_command("p1", "Anna", "assert", A, c, "/ablegen")
st = app.step("p1", "Anna", "assert", A, c, None)
check("/ablegen fragt erst um Zustimmung, mit Hinweis und ohne den Text",
      handled and st == "confirm" and c["confirm"]["by"] == "chat"
      and "Zeichen" in c["confirm"]["arguments"]["text"], c.get("confirm"))
st = app.step("p1", "Anna", "assert", A, c, {"call": c["confirm"]["call"], "ok": True})
check("nach Zustimmung schreibt der Chat, kein Modellzug, Satz „Abgelegt“",
      st == "done" and len(MODEL) == n_model and c["messages"][-1]["content"].startswith("Abgelegt: notizen:/arbeitsraum/")
      and CALLS[-1][2] == "store_write" and CALLS[-1][3] is True, c["messages"][-1])
handled = app.workspace_command("p1", "Anna", "assert", A, c, "/stand")
check("/stand wird eine Bitte an den Assistenten", handled and c["messages"][-1]["command"] == "stand"
      and "notizen:/arbeitsraum/stand" in c["messages"][-1]["content"])
check("ohne Arbeitsraum sind /stand usw. keine eigenen Befehle",
      app.workspace_command("p1", "Anna", "assert", A, {"messages": []}, "/stand") is False)
v = app.view_of(dict(c, title="T"))
check("der Browser sieht das Ablegen ohne den Text", "- Person: Anna" not in json.dumps(v, ensure_ascii=False) and
      any(m.get("by") == "chat" for m in v["messages"]) and v["workspace"]["id"] == "verein")

# 0.24.0: Arbeitsräume als Karten, Ordnerwahl je Ablage
pg = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"), encoding="utf-8").read()
check("0.24.0: Ansicht Arbeitsräume über den Assistenten, mit eigener Adresse",
      pg.index('id="wsbtn"') < pg.index('id="manage"') and '#arbeitsraeume' in pg and 'id="wsview"' in pg)
check("0.24.0: unter dem Chat keine Auswahl mehr, nur der Hinweis", 'id="wspick"' not in pg and 'id="wschip"' in pg)
check("0.24.0: das Gespräch nimmt den Arbeitsraum der Karte", "workspace: DRAFT_WS || undefined" in pg
      and "Gespräch beginnen" in pg and "lastAssistantIn(" in pg)
check("0.24.0: Seitenleiste je Arbeitsraum, immer da",
      'id: "workspaces", title: "Arbeitsräume", icon: "workspace", open: true, always: true' in pg)
check("0.24.0: Ordner geht mit, gewählt über what=folders",
      "what=folders" in pg and "role: role.value, folder," in pg)
check("0.24.0: Ändern behält die Kennung", "WS_EDIT ? WS_EDIT.id" in pg)
check("0.24.1: „Neu…“ gesperrt, wo das Ablagesystem create_store false meldet; unbekannt bleibt offen",
      "mk.disabled = s.create_store === false" in pg and "das sagt das Ablagesystem selbst" in pg)

check("0.25.0: Karte mit ⋯ (letzte Gespräche, Ändern, Löschen), keine Knöpfe Ändern/Löschen mehr",
      'menu.push({head: "Letzte Gespräche"})' in pg and 'el("button", "edit", "Ändern")' not in pg.split("function renderWorkspaces")[1].split("function wsMountRow")[0])
check("0.25.0: nur die Heimat ausgeschrieben, sonst Summen", '"🏠 "' in pg and '"✏️ "' in pg and '"📖 "' in pg)
check("0.25.0: „ohne Protokoll“ statt „privat“, mit Erklärung", "ohne Protokoll" in pg and "NOLOG_HINT" in pg
      and '> privat</label>' not in pg)
check("0.27.1: Sendeknopf trägt die Ampel erst, wenn sie feststeht", "known = C ? !!C.light : !!DRAFT_LIGHT" in pg
      and "#go.send-red{" in pg and "--sendink:#111827" in pg)
check("0.29.0: Sendeknopf ist ein Symbol mit Beschriftung für Screenreader, Pause hat „Weitermachen“",
      'id="go" aria-label="Senden"' in pg and '<path d="M4 12l16-8-6 16-2.5-6.5z"/>' in pg
      and '"Weitermachen"' in pg and '"Mach weiter."' in pg)

print(f"\n{PASSED[0]} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
