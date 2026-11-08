"""Die MCP-Tür der Vorgänge (RFC-0073: eine App als MCP-Quelle; 0.4.0).

Ein MCP-Gateway desselben Mandanten erreicht sie über die Tür der Plattform
(Verfahren I oder A); die Plattform setzt die Kopfzeilen der PERSON, und
diese Tür antwortet mit genau den Rechten, die die Seite dieser Person gibt
-- dieselben Funktionen (`can_see`, `can_comment`, `Who.may`), kein zweiter
Regelsatz. Was die Seite nicht zeigt, zeigt die Tür nicht; die technischen
Angaben aus dem Browser zeigt sie gar nicht.

JSON-RPC über Streamable HTTP, nur JSON-Antworten, keine Sitzung.
"""
import json
import os

MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_LIST = 50

# 0.5.0 (MCP Apps, RFC-0075): get_case bringt eine Ansicht mit -- eine Karte des
# Vorgangs mit Kommentarfeld. Sie lädt nichts von außen (CSP leer) und setzt
# Daten nur als Text ein. Ein MCP-Gateway hält ihren Inhalt fest.
UI_MIME = "text/html;profile=mcp-app"
CASE_VIEW = "ui://vorgaenge/case"
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "views", "case.html"),
          encoding="utf-8") as _f:
    CASE_VIEW_HTML = _f.read()
VIEWS = {CASE_VIEW: CASE_VIEW_HTML}

TOOLS = [
    {"name": "list_cases",
     "description": "Vorgänge, die du sehen darfst: von dir gemeldete und die deiner Bereiche. "
                    "Standard nur offene.",
     "inputSchema": {"type": "object", "properties": {
         "include_closed": {"type": "boolean", "description": "auch erledigte"},
         "scope": {"type": "string", "enum": ["all", "mine", "queues"],
                   "description": "mine = von dir gemeldet, queues = in deinen Bereichen"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "get_case",
     "description": "Ein Vorgang mit Verlauf (Kommentare, Statuswechsel), z. B. 2026-0042.",
     "inputSchema": {"type": "object", "required": ["number"],
                     "properties": {"number": {"type": "string"}}},
     "annotations": {"readOnlyHint": True},
     "_meta": {"ui": {"resourceUri": CASE_VIEW}}},
    {"name": "list_queues",
     "description": "Die Bereiche des Mandanten (für create_case).",
     "inputSchema": {"type": "object", "properties": {}},
     "annotations": {"readOnlyHint": True}},
    {"name": "create_case",
     "description": "Einen Vorgang melden. Art: bug (Fehler), question (Frage), idea (Idee), "
                    "change (Änderung), internal. Einen Bereich nur angeben, wenn die Person "
                    "ihn ausdrücklich nennt -- sonst weglassen, dann landet er im Eingang. "
                    "Die vorhandenen Bereiche nennt list_queues.",
     "inputSchema": {"type": "object", "required": ["title"], "properties": {
         "title": {"type": "string", "maxLength": 200, "description": "kurzer Betreff"},
         "body": {"type": "string", "maxLength": 10000,
                  "description": "Beschreibung mit dem, was die Person gesagt hat"},
         "kind": {"type": "string", "enum": ["bug", "question", "idea", "change", "internal"]},
         "queue": {"type": "string",
                   "description": "Kennung aus list_queues; weglassen, wenn die Person keinen nennt"}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
    {"name": "comment_case",
     "description": "Einen Kommentar an einen Vorgang schreiben.",
     "inputSchema": {"type": "object", "required": ["number", "text"], "properties": {
         "number": {"type": "string"}, "text": {"type": "string", "maxLength": 10000}}},
     "annotations": {"readOnlyHint": False, "destructiveHint": False}},
]


class Rules:
    """Was die Seite weiß, gebündelt: Prüfungen, Bezeichnungen, Konstanten."""

    def __init__(self, can_see, can_comment, refused, kinds, kind_label, status_label,
                 default_queue, open_statuses, attest):
        self.can_see, self.can_comment, self.refused = can_see, can_comment, refused
        self.kinds, self.kind_label, self.status_label = kinds, kind_label, status_label
        self.default_queue, self.open = default_queue, open_statuses
        self.attest = attest


def _short(c, qt, rules):
    return {"number": c["number"], "title": c["title"], "kind": rules.kind_label.get(c["kind"], c["kind"]),
            "status": rules.status_label.get(c["status"], c["status"]),
            "queue": qt.get(c["queue"], c["queue"]), "reported_by": c["reporter_name"],
            "updated": c["updated"][:16].replace("T", " ")}


def _tool(store, who, rules, name, args):
    queues = store.queues()
    qt = {q["id"]: q["title"] for q in queues}
    if name == "list_queues":
        return [{"id": q["id"], "title": q["title"]} for q in queues]
    if name == "list_cases":
        scope = args.get("scope") or "all"
        open_only = not args.get("include_closed")
        readable = who.readable(queues)
        rows = []
        if scope in ("all", "queues") and readable:
            rows += store.listing(queues=readable, open_only=open_only)
        if scope in ("all", "mine"):
            rows += store.listing(reporter=who.id, open_only=open_only)
        seen, out = set(), []
        for c in rows:
            if c["number"] not in seen and rules.can_see(who, c):
                seen.add(c["number"])
                out.append(_short(c, qt, rules))
        out.sort(key=lambda x: x["updated"], reverse=True)
        return {"count": len(out), "cases": out[:MAX_LIST]}
    if name in ("get_case", "comment_case"):
        c = store.get(str(args.get("number") or "").strip())
        if not c or not rules.can_see(who, c):
            raise rules.refused("Diesen Vorgang gibt es nicht, oder er liegt nicht in einem "
                                "deiner Bereiche.")
        if name == "comment_case":
            if not rules.can_comment(who, c):
                raise rules.refused("Kommentieren dürfen hier nur die Meldende und der Bereich.")
            store.comment(c, who.id, who.name, args.get("text"))
            return {"number": c["number"], "commented": True}
        out = _short(c, qt, rules)
        out["description"] = c["body"]
        # Der ganze Verlauf, wie die Seite ihn jedem zeigt, der den Vorgang sieht.
        out["history"] = [{"at": e["at"][:16].replace("T", " "), "by": e["actor_name"],
                           "type": e["type"], "text": e["text"]} for e in c["entries"]]
        return out
    if name == "create_case":
        kind = args.get("kind") or "question"
        if kind not in rules.kinds:
            raise rules.refused(f"Art {kind} gibt es nicht: " + ", ".join(rules.kinds) + ".")
        queue = str(args.get("queue") or rules.default_queue)
        if queue not in qt:
            hit = [q for q, t in qt.items() if t.lower() == queue.lower()]
            if not hit:
                raise rules.refused(f"Bereich {queue} gibt es nicht: " + ", ".join(qt) + ".")
            queue = hit[0]
        made = store.create(kind=kind, title=args.get("title"), body=args.get("body") or "",
                            reporter=who.id, reporter_name=who.name, queue=queue,
                            attested=rules.attest(who))
        return {"number": made["number"], "created": True, "queue": qt[queue]}
    raise rules.refused("unbekanntes Werkzeug")


def answer(store, who, rules, msg):
    """Die JSON-RPC-Antwort auf eine Nachricht; None bei einer Benachrichtigung."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not msg.get("method"):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    if "id" not in msg:
        return None
    method, params = msg["method"], msg.get("params") or {}

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

    def err(code, text):
        return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": code, "message": text}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0],
                   "capabilities": {"tools": {}, "resources": {}},
                   "serverInfo": {"name": "vorgaenge", "version": rules.attest(who)["version"]}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "resources/list":
        return ok({"resources": [{"uri": CASE_VIEW, "name": "Vorgang als Karte",
                                  "mimeType": UI_MIME}]})
    if method == "resources/templates/list":
        # Gehört zur Fähigkeit `resources`; ohne Antwort hielt ein Gateway den ganzen
        # Katalog für unlesbar (gemessen 2026-11-08 an mcp-gateway 0.8.0).
        return ok({"resourceTemplates": []})
    if method == "resources/read":
        uri = params.get("uri")
        if uri not in VIEWS:
            return err(-32002, "Resource not found")
        return ok({"contents": [{"uri": uri, "mimeType": UI_MIME, "text": VIEWS[uri],
                                 "_meta": {"ui": {"csp": {}, "prefersBorder": True}}}]})
    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return err(-32602, "Unknown tool")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            text, failed = json.dumps(_tool(store, who, rules, name, args), ensure_ascii=False), False
        except rules.refused as e:
            text, failed = str(e), True
        return ok({"content": [{"type": "text", "text": text}], "isError": failed})
    return err(-32601, "Method not found")
