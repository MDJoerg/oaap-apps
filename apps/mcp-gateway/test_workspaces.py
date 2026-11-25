#!/usr/bin/env python3
"""Arbeitsräume und Ablagetür (RFC-0121 Stufe 1) ohne Netz: der Konnektor ist nachgebaut.

Die Nummern K1..K13 sind die Konformitätszeilen aus RFC-0121 §11, soweit Stufe 1 sie hat.
Run: python3 apps/mcp-gateway/test_workspaces.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import books  # noqa: E402
import workspaces as W  # noqa: E402

FAILED = []
PASSED = [0]


def check(label, cond, detail=""):
    if cond:
        PASSED[0] += 1
        print(f"  PASS  {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}  {detail}")


def raises(fn, *a, **k):
    try:
        fn(*a, **k)
    except W.Invalid as e:
        return str(e)
    return None


tmp = tempfile.mkdtemp()
db = books.connect(os.path.join(tmp, "g.db"))
books.save_server(db, "test", "wiki-src", "http://wiki:8000/oaap/mcp", "none", "green", {})
books.save_server(db, "test", "git-src", "http://git:8000/mcp", "none", "yellow", {})
W.save_system(db, "test", "wiki", "Wiki", "bookstack", "wiki-src", "green")
W.save_system(db, "test", "git", "Git", "forgejo", "git-src", "green")

check("Ablagesystem nie lockerer als sein Server", W.system_light(db, W.system_row(db, "git")) == "yellow")
check("unbekannter Typ abgelehnt", raises(W.save_system, db, "t", "x", "X", "ftp", "wiki-src") is not None)

GOOD = {"id": "verein", "title": "Verein", "mounts": [
    {"alias": "notizen", "system": "wiki", "store": "1", "role": "work", "home": True},
    {"alias": "satzung", "system": "wiki", "store": "2", "folder": "/recht", "role": "source"}]}

# K7: zwei Heimaten, Heimat als Quelle
two = {**GOOD, "mounts": [dict(GOOD["mounts"][0]), dict(GOOD["mounts"][1], home=True)]}
check("K7 zwei Heimaten abgelehnt", raises(W.check, db, two, "u1") is not None)
src_home = {**GOOD, "mounts": [dict(GOOD["mounts"][0], role="source")]}
check("K7 Heimat als Quelle abgelehnt", raises(W.check, db, src_home, "u1") is not None)
check("Arbeitsraum ohne Heimat abgelehnt",
      raises(W.check, db, {**GOOD, "mounts": [GOOD["mounts"][1]]}, "u1") is not None)
check("Alias doppelt abgelehnt", raises(W.check, db, {**GOOD, "mounts": [
    GOOD["mounts"][0], dict(GOOD["mounts"][1], alias="notizen")]}, "u1") is not None)
# K12: keine Adresse, kein Zugang im Arbeitsraum
check("K12 Feld address abgelehnt", raises(W.check, db, {**GOOD, "address": "http://x"}, "u1") is not None)
check("K12 Feld token in einer Ablage abgelehnt", raises(W.check, db, {**GOOD, "mounts": [
    dict(GOOD["mounts"][0], token="geheim")]}, "u1") is not None)
check("Ordner mit .. abgelehnt", raises(W.check, db, {**GOOD, "mounts": [
    dict(GOOD["mounts"][0], folder="/a/../b")]}, "u1") is not None)

ws = W.save_ws(db, "u1", GOOD)
# Gefunden am Knoten (23.11.): books.log schreibt ohne commit -- danach geloggt, blieb die
# Transaktion offen und sperrte die Datenbank für jeden anderen Schreiber.
check("nach dem Speichern keine offene Transaktion", not db._conn.in_transaction)
check("nach Ablagesystem speichern keine offene Transaktion",
      W.save_system(db, "t", "wiki", "Wiki", "bookstack", "wiki-src", "green") and not db._conn.in_transaction)
check("gespeichert, Revision 1, Heimatordner Vorgabe", ws["rev"] == 1 and ws["home_folder"] == "/arbeitsraum")
check("ein fremder Besitzer überschreibt nicht", raises(W.save_ws, db, "u2", GOOD) is not None)
check("nur der eigene Arbeitsraum ist benutzbar",
      W.ws_for(db, "u1", "verein") is not None and W.ws_for(db, "u2", "verein") is None)
check("Ablagesystem in Benutzung nicht löschbar", raises(W.delete_system, db, "t", "wiki") is not None)
check("Ampel des Arbeitsraums = schwächste Ablage", W.ws_light(db, ws) == "green")

names = [t["name"] for t in W.tool_list(ws)]
check("schreibende Werkzeuge mit Arbeitsablage", "store_write" in names and "store_edit" in names)
ro = W.save_ws(db, "u1", {"id": "nur-lesen", "title": "x", "mounts": [
    dict(GOOD["mounts"][0])]})
check("ohne about kein Verschieben/Löschen", set(names) == set(W.TOOLS) - set(W.DECLARED), str(names))
can = {"move": True, "remove": "documents"}
names2 = [t["name"] for t in W.tool_list(ws, lambda m: can)]
check("Werkzeugliste fest benannt (Konnektor kann alles)", set(names2) == set(W.TOOLS), str(names2))
check("BookStack ohne move/remove: nicht angeboten",
      "store_move" not in [t["name"] for t in W.tool_list(ws, lambda m: {"move": False})])
check("Quelle allein: kein Verschieben, auch wenn der Konnektor es kann",
      "store_move" not in [t["name"] for t in W.tool_list(
          {"mounts": [dict(GOOD["mounts"][1], home=False)]}, lambda m: can)])
rm_def = W.DEFS["store_remove"]
check("Löschen ist schreibend und destruktiv",
      rm_def["_meta"]["oaap/effect"] == "write" and rm_def["annotations"]["destructiveHint"] is True)
eff = {t["name"]: t["_meta"]["oaap/effect"] for t in W.tool_list(ws)}
check("Wirkungsklasse für den Chat: lesen ohne, schreiben mit Zustimmung",
      eff["store_read"] == "read" and eff["workspace_info"] == "read" and eff["store_edit"] == "write")


# --- der nachgebaute Konnektor
class Fake:
    def __init__(self):
        self.docs = {("1", "/stand"): ("# Stand\n\n## Offen\n- a\n\n## Erledigt\n- b\n", 3),
                     ("2", "/recht/satzung"): ("# Satzung\n" + "x" * 50, 1),
                     ("1", "/lang"): ("# Lang\n\n" + ("Ein Absatz. " * 60), 1)}
        self.calls = []
        self.about = {"move": True, "remove": "documents"}

    def __call__(self, m, tool, a):
        self.calls.append((m["alias"], tool, dict(a)))
        key = (a.get("store"), a.get("path"))
        if tool == "stores_entry":
            return {"path": "/stand" if a["store"] == "1" else "/recht/satzung"}
        if tool == "stores_read":
            if key not in self.docs:
                raise W.Invalid("Seite gibt es nicht.")
            t, r = self.docs[key]
            return {"text": t, "revision": str(r), "format": "markdown", "title": "T"}
        if tool == "stores_write":
            t, r = self.docs.get(key, ("", 0))
            if r and a.get("revision") != str(r):
                raise W.Invalid("Revision passt nicht.")
            self.docs[key] = (a["text"], r + 1)
            return {"path": a["path"], "revision": str(r + 1), "created": r == 0}
        if tool == "stores_about":
            return dict(self.about)
        if tool == "stores_move":
            t_, r_ = self.docs.pop(key)
            self.docs[(a["store"], a["to"])] = (t_, r_)
            return {"path": a["to"], "kind": "document", "files": 1}
        if tool == "stores_remove":
            if self.docs[key][1] != int(a["revision"]):
                raise W.Invalid("Revision passt nicht.")
            del self.docs[key]
            return {"removed": True, "note": "Historie behält sie."}
        if tool == "stores_list":
            return {"items": [{"name": "stand", "kind": "document"}]}
        if tool == "stores_search":
            return {"query": a["query"], "hits": [{"path": "/recht/satzung", "title": "S"},
                                                  {"path": "/anderswo/x", "title": "nicht hier"}]}
        raise W.Invalid("?")


f = Fake()
GREEN = books.LIGHT_RANK["green"]


def run(name, args, ws_=None):
    return W.run(db, ws_ or ws, GREEN, name, args, f)


info, _ = run("workspace_info", {})
check("workspace_info nennt Stand, Protokoll, Einstieg",
      info["state"] == "notizen:/arbeitsraum/stand" and info["mounts"][1]["entry"] == "satzung:/satzung",
      str(info))
# K3: Quelle wird nicht beschrieben, der Konnektor nicht gerufen
n = len(f.calls)
msg = raises(run, "store_write", {"path": "satzung:/neu", "text": "x"})
check("K3 Schreiben in eine Quelle abgelehnt, Konnektor nicht gerufen", msg and len(f.calls) == n, msg)
# K4: Pfade und fremde Aliasse
check("K4 alias:/../x löst nicht auf", raises(run, "store_read", {"path": "notizen:/../x"}) is not None)
check("K4 nicht eingehängter Alias löst nicht auf", raises(run, "store_read", {"path": "fremd:/x"}) is not None)
check("Pfad bleibt in der Einhängung (Ordner /recht)",
      f.calls and run("store_read", {"path": "satzung:/satzung"})[0]["title"] == "T"
      and f.calls[-1][2]["path"] == "/recht/satzung")
# Lesen in Teilen
r, _ = run("store_read", {"path": "notizen:/stand", "section": "Offen"})
check("Abschnitt lesen", r["text"].startswith("## Offen") and "Erledigt" not in r["text"], r["text"])
check("Inhalt als fremd markiert", r["note"] == W.FOREIGN)
o, _ = run("store_outline", {"path": "notizen:/stand"})
check("Gliederung", [x["title"] for x in o["outline"]] == ["Stand", "Offen", "Erledigt"])
# K5: alte Revision
msg = raises(run, "store_edit", {"path": "notizen:/stand", "revision": "2", "op": "section_append",
                                 "section": "Offen", "body": "- c"})
check("K5 alte Revision abgelehnt und nennt die aktuelle", msg and "3" in msg, msg)
out, _ = run("store_edit", {"path": "notizen:/stand", "revision": "3", "op": "section_append",
                            "section": "Offen", "body": "- c"})
txt = f.docs[("1", "/stand")][0]
check("Abschnitt ergänzt, Rest unverändert", "- a\n- c" in txt and "## Erledigt\n- b" in txt, txt)
check("Revision steigt", out["revision"] == "4")
msg = raises(run, "store_edit", {"path": "notizen:/stand", "revision": "4", "op": "replace",
                                 "old": "- ", "new": "* "})
check("replace braucht genau ein Vorkommen", msg is not None)
# K6: Kürzungsschutz
msg = raises(run, "store_write", {"path": "notizen:/lang", "text": "kurz", "revision": "1"})
check("K6 Halbieren ohne shrink_ok abgelehnt", msg and "shrink_ok" in msg, msg)
out, _ = run("store_write", {"path": "notizen:/lang", "text": "kurz", "revision": "1", "shrink_ok": True})
check("K6 mit shrink_ok erlaubt", out["revision"] == "2")
out, _ = run("store_write", {"path": "notizen:/neu", "text": "# Neu"})
check("neues Dokument ohne Revision", out["created"] is True)
# Suche: Treffer außerhalb der Einhängung fallen weg
s, _ = run("store_search", {"path": "satzung:/", "query": "x"})
check("Suche bleibt in der Einhängung", [h["path"] for h in s["hits"]] == ["satzung:/satzung"], str(s))
# 0.47: verschieben und löschen
out, _ = run("store_move", {"path": "notizen:/neu", "to": "notizen:/archiv/neu"})
check("verschieben innerhalb der Ablage", out["path"] == "notizen:/archiv/neu" and out["from"] == "notizen:/neu"
      and ("1", "/archiv/neu") in f.docs and ("1", "/neu") not in f.docs, str(out))
n = len(f.calls)
msg = raises(run, "store_move", {"path": "notizen:/archiv/neu", "to": "satzung:/neu"})
check("verschieben zwischen Ablagen abgelehnt, Konnektor nicht gerufen", msg and len(f.calls) == n, msg)
msg = raises(run, "store_move", {"path": "satzung:/satzung", "to": "satzung:/x"})
check("verschieben in einer Quelle abgelehnt", msg and "Quelle" in msg and len(f.calls) == n, msg)
msg = raises(run, "store_move", {"path": "notizen:/archiv/neu", "to": "notizen:/../x"})
check("Ziel außerhalb löst nicht auf", msg is not None, msg)
msg = raises(run, "store_remove", {"path": "notizen:/archiv/neu"})
check("löschen ohne Revision abgelehnt", msg and "Revision" in msg, msg)
out, _ = run("store_remove", {"path": "notizen:/archiv/neu", "revision": "1"})
check("löschen mit Revision", out["removed"] and ("1", "/archiv/neu") not in f.docs, str(out))
f.about = {"move": False, "remove": False}
msg = raises(run, "store_move", {"path": "notizen:/stand", "to": "notizen:/x"})
check("Konnektor ohne move: abgelehnt, stores_move nicht gerufen",
      msg and "kann das nicht" in msg and f.calls[-1][1] == "stores_about", msg)
f.about = {"move": True, "remove": "documents"}

# 0.48: Ordnerwahl beim Einhängen und der Rand des Ordners
seen = []


def lister(m, tool, a):
    seen.append((tool, dict(a)))
    return {"items": [{"name": "Zeta", "kind": "folder"}, {"name": "akten", "kind": "folder", "title": "Akten"},
                      {"name": "plan.md", "kind": "document"}, {"name": ".git", "kind": "folder"},
                      {"name": "a/b", "kind": "folder"}, "kaputt"]}


pf = W.pick_folders(lister, {"alias": "git", "system": "git"}, "anna/notizen", "/projekte/")
check("Ordnerwahl: nur Ordner, sortiert, mit vollem Pfad",
      [x["path"] for x in pf["folders"]] == ["/projekte/akten", "/projekte/Zeta"]
      and pf["folders"][0]["title"] == "Akten" and pf["path"] == "/projekte", str(pf))
check("Ordnerwahl ruft stores_list mit normiertem Pfad",
      seen[-1] == ("stores_list", {"store": "anna/notizen", "path": "/projekte"}), str(seen))
check("Ordnerwahl: versteckter Ordner und Schrägstrich im Namen nicht wählbar",
      not any(x["name"] in (".git", "a/b") for x in pf["folders"]))
n = len(seen)
check("Ordnerwahl: .. abgelehnt, Konnektor nicht gerufen",
      raises(W.pick_folders, lister, {}, "s", "/a/../b") and len(seen) == n)
check("Ordnerwahl: ohne Ablage abgelehnt", raises(W.pick_folders, lister, {}, "", "/") is not None)
mnt = {"folder": "/a"}
check("Rand: /a enthält /a/x, nicht /ab", W.inside(mnt, "/a/x") and W.inside(mnt, "/a") and not W.inside(mnt, "/ab"))
check("Rand: mount_path lässt /ab unberührt", W.mount_path(mnt, "/ab/x") == "/ab/x" and W.mount_path(mnt, "/a/x") == "/x")
check("Rand: Wurzel enthält alles", W.inside({"folder": "/"}, "/irgendwo"))
# 0.49: create_store für die Auswahl
check("Auswahl: create_store nur bei ausdrücklich true",
      W.system_caps({"create_store": True}) == {"create_store": True}
      and W.system_caps({"create_store": False}) == {"create_store": False}
      and W.system_caps({"create_store": "ja"}) == {"create_store": False}
      and W.system_caps({}) == {"create_store": False})
check("Auswahl: keine Antwort heißt unbekannt, nicht nein", W.system_caps(None) == {"create_store": None})
# 0.49.1: BDT-TEAM 0.903 legt die Erklärungen in `capabilities` und antwortet mit `store`
bdt = {"kind": "bdt", "capabilities": {"create_store": True, "move": False, "remove": False}}
check("about verschachtelt: create_store gilt", W.system_caps(bdt) == {"create_store": True})
check("about verschachtelt: move/remove false bleiben false",
      not W.declares(bdt, "store_move") and not W.declares(bdt, "store_remove"))
check("about: oben gilt vor capabilities",
      W.flat_about({"move": False, "capabilities": {"move": True}})["move"] is False
      and W.flat_about({"move": True})["move"] is True and W.flat_about(None) is None)
check("Antwort mit store statt id", W.made_store({"store": "p1", "title": "Plan"}, "x") == {"id": "p1", "title": "Plan"}
      and W.made_store({"id": "o/r"}, "Neu") == {"id": "o/r", "title": "Neu"})
check("Antwort ohne Kennung ist kein Erfolg", raises(W.made_store, {"title": "x"}, "x") is not None)

# K13: Ampel
books.save_server(db, "test", "wiki-src", "http://wiki:8000/oaap/mcp", "none", "yellow", {})
msg = raises(run, "store_read", {"path": "notizen:/stand"})
check("K13 gelbe Ablage in grünem Gespräch abgelehnt", msg is not None, msg)
check("Arbeitsraum zeigt jetzt gelb", W.ws_light(db, ws) == "yellow")

# Bearbeitungsschicht direkt
t = "# A\n## X\n1\n# B\n## X\n2\n"
check("mehrdeutiger Abschnitt verlangt Oben > Unten", raises(W.find_section, t, "X") is not None)
check("Oben > Unten eindeutig", W.read_part(t, "B > X")["text"] == "## X\n2")
check("Abschnitt auch samt Rauten („# B > ## X“, gemessen am Modell)",
      W.read_part(t, "# B > ## X")["text"] == "## X\n2")
check("section_delete", W.edit(t, "section_delete", "A > X") == "# A\n# B\n## X\n2\n")
check("Überschrift im Codeblock zählt nicht", [x[1] for x in W.outline("```\n# nein\n```\n# ja")] == ["ja"])
rp = W.read_part("\n".join(str(i) for i in range(1000)), from_line=1)
check("höchstens 400 Zeilen, mit Fortsetzung", rp["to_line"] == 400 and rp["continue_at"] == 401)

print(f"\n{PASSED[0]} passed, {len(FAILED)} failed")
sys.exit(1 if FAILED else 0)
