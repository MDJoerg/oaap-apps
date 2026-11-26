"""Die Klasse eines Tools in einer Sicht (mcp-gateway 0.53.0).

    python3 apps/mcp-gateway/test_effect_default.py

Gemessen in bec am 26.11.2026: `view add` ohne `--effect` legte zwei schreibende SAP-Tools
(propose_solution, add_analysis) als „lesen“ in die Sicht eines Spezialisten -- er hätte ohne
Freigabe nach SAP geschrieben. Gehalten wird jetzt:
  * ohne Angabe gilt die Klasse, die das Tool selbst meldet: `read` nur mit readOnlyHint: true,
    `execute` mit destructiveHint, sonst `write`;
  * „lesen“ für ein Tool, das sich nicht als lesend meldet, nur ausdrücklich (read_confirmed),
    beim Freigeben wie beim Zuschneiden; ein lesendes Tool geht ohne Bestätigung;
  * die Kommandozeile zeigt die Klasse und verlangt --lesend-bestaetigt ja;
  * die Einrichtungstür nimmt „read“ nur für Tools, die sich als lesend melden, und nimmt ohne
    effect die Klasse der Quelle.
"""
import contextlib
import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = tempfile.mkdtemp(prefix="mcpgw-effect-")
os.environ["MCPGW_DATA_DIR"] = DATA
os.environ["MCPGW_MASTER_KEY"] = "x" * 64
sys.path.insert(0, HERE)

import books  # noqa: E402
import guard  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


def refused(fn, *a, **kw):
    try:
        fn(*a, **kw)
    except ValueError as exc:
        return str(exc)
    return ""


db = books.connect(os.path.join(DATA, "g.db"))
LIM = guard.clean(dict(guard.DEFAULTS))
books.save_server(db, "op", "erc", "http://erc.example/mcp", "none", "yellow", LIM)
OBJ = {"type": "object"}
books.store_catalog(db, "erc", {"tool": [
    {"name": "get_incident", "inputSchema": OBJ, "annotations": {"readOnlyHint": True}},
    {"name": "propose_solution", "inputSchema": OBJ},
    {"name": "add_analysis", "inputSchema": OBJ, "annotations": {"readOnlyHint": False}},
    {"name": "delete_incident", "inputSchema": OBJ, "annotations": {"destructiveHint": True}},
    {"name": "odd", "inputSchema": OBJ, "annotations": "kaputt"},
]})
books.save_view(db, "op", "spz", "execute")


def eff(view, name):
    r = books.item_row(db, view, "tool", name)
    return r["effect"] if r else None


ok("effect_hint: read nur mit readOnlyHint true, execute mit destructiveHint, sonst write",
   [books.effect_hint(d) for d in ({"annotations": {"readOnlyHint": True}}, {}, {"annotations": {"readOnlyHint": "ja"}},
                                   {"annotations": {"destructiveHint": True}}, {"annotations": "x"}, None)]
   == ["read", "write", "write", "execute", "write", "write"])

n = GI = books.approve(db, "op", "spz", "tool", "erc", "get_incident")
ok("ein lesendes Tool ohne Angabe: read", eff("spz", n) == "read", eff("spz", n))
n = PS = books.approve(db, "op", "spz", "tool", "erc", "propose_solution")
ok("KÖDER (bec): ein Tool ohne Annotation ohne Angabe: write, nicht read", eff("spz", n) == "write", eff("spz", n))
n = books.approve(db, "op", "spz", "tool", "erc", "delete_incident")
ok("destructiveHint ohne Angabe: execute", eff("spz", n) == "execute", eff("spz", n))
n = books.approve(db, "op", "spz", "tool", "erc", "odd")
ok("kaputte Annotationen ohne Angabe: write", eff("spz", n) == "write", eff("spz", n))

msg = refused(books.approve, db, "op", "spz", "tool", "erc", "add_analysis", None, "read")
ok("KÖDER: „read“ für ein Tool mit readOnlyHint false ohne Bestätigung: Satz, nichts angelegt",
   "nicht als lesend" in msg and "--lesend-bestaetigt ja" in msg
   and not any(i["source"] == "add_analysis" for i in books.items(db, "spz", "tool")), msg)
books.save_view(db, "op", "bewusst", "execute")
n = books.approve(db, "op", "bewusst", "tool", "erc", "add_analysis", None, "read", read_confirmed=True)
ok("… ausdrücklich bestätigt (eine Person hat es gewählt): read", eff("bewusst", n) == "read", eff("bewusst", n))
n = books.approve(db, "op", "bewusst", "tool", "erc", "get_incident", None, "read")
ok("„read“ für ein lesendes Tool braucht keine Bestätigung", eff("bewusst", n) == "read")
n = books.approve(db, "op", "bewusst", "tool", "erc", "propose_solution", None, "execute")
ok("höher als die Quelle meldet geht immer (execute statt write)", eff("bewusst", n) == "execute")

name = PS
msg = refused(books.tailor_item, db, "op", "spz", "tool", name, effect="read", read_confirmed=False)
ok("KÖDER: zuschneiden auf read ohne Bestätigung (Kommandozeile): Satz, bleibt write",
   "nicht als lesend" in msg and eff("spz", name) == "write", msg)
books.tailor_item(db, "op", "spz", "tool", name, effect="read")
ok("… auf der Seite (Person wählt): read", eff("spz", name) == "read")
books.tailor_item(db, "op", "spz", "tool", name, effect="write", read_confirmed=False)
ok("zurück auf write braucht nie eine Bestätigung", eff("spz", name) == "write")
gi = GI
books.tailor_item(db, "op", "spz", "tool", gi, effect="write", read_confirmed=False)
books.tailor_item(db, "op", "spz", "tool", gi, effect="read", read_confirmed=False)
ok("ein lesendes Tool zurück auf read: ohne Bestätigung", eff("spz", gi) == "read")

# Kommandozeile und Einrichtungstür: über app (dieselbe Datenbank)
import app  # noqa: E402

app.DB = db


def src(view, source):
    return next((i["effect"] for i in books.items(db, view, "tool") if i["source"] == source), None)


def cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            rc = app.cli(list(argv))
        except SystemExit as e:
            rc = e.code
        except ValueError as e:
            print(e)
            rc = 1
    return rc, out.getvalue()


books.save_view(db, "op", "kz", "execute")
rc, out = cli("view", "add", "kz", "erc", "propose_solution")
ok("Kommandozeile ohne --effect: write, und sie sagt es", src("kz", "propose_solution") == "write"
   and "(write)" in out, out)
rc, out = cli("view", "add", "kz", "erc", "add_analysis", "--effect", "read")
ok("KÖDER: Kommandozeile --effect read für ein schreibendes Tool: abgelehnt",
   src("kz", "add_analysis") is None and "lesend-bestaetigt" in out, out)
rc, out = cli("view", "add", "kz", "erc", "add_analysis", "--effect", "read", "--lesend-bestaetigt", "ja")
ok("… mit --lesend-bestaetigt ja: read", src("kz", "add_analysis") == "read", out)
psn = next(i["name"] for i in books.items(db, "kz", "tool") if i["source"] == "propose_solution")
rc, out = cli("view", "tailor", "kz", psn, "--effect", "read")
ok("KÖDER: Kommandozeile tailor --effect read ohne Bestätigung: bleibt write",
   src("kz", "propose_solution") == "write", out)

print(f"\n{'OK: 0 Fehler' if not fails else f'{fails} Fehler'}")
sys.exit(1 if fails else 0)
