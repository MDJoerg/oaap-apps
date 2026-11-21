"""Die MCP-Tür einer OAAP-App (RFC-0118 P7, RFC-0073).

Eine Kopie in jeder App, die eine MCP-Tür hat (check-shared.py zählt sie),
die Quelle ist `lib/mcpdoor/`. Nur Standardbibliothek.

JSON-RPC über Streamable HTTP, nur JSON-Antworten, keine Sitzung -- wie
die Türen von help, recherche und vorgaenge, aus denen sie entstand. Die
Person kommt vom MCP-Gateway (Verfahren I): dieselben X-OAAP-Kopfzeilen
wie im Browser, und ein Werkzeug prüft sie wie die Seite. Ein Werkzeug,
das schreibt, gibt es nur, wo die Seite dieselbe Handlung anbietet
(RFC-0118 §9).

Beschreibungen sind fest: eine geänderte Beschreibung sperrt das Werkzeug
im Gateway, bis jemand es neu freigibt. Was sich ändern kann, gehört ins
ERGEBNIS.

    import mcpdoor
    door = mcpdoor.Door("oaap-wegweiser", VERSION,
                        "Kurzlinks des Mandanten suchen und lesen.")

    @door.tool("wegweiser_search", "Links suchen",
               "Sucht Kurzlinks nach Wort, Bereich oder Ziel.",
               {"q": {"type": "string", "description": "Suchwort"}})
    def _search(args, who):
        return {"hits": [...]}            # dict/list → JSON-Text; str → Text

    # im Handler:
    #   if path == "/mcp": return door.serve(self)       (GET → 405, POST → Antwort)
"""
import json

MCP_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
BODY_LIMIT = 1024 * 1024


class Refused(Exception):
    """Ein Werkzeug lehnt ab: der Satz geht als Ergebnis mit isError zurück."""


def identity(headers):
    """Wer fragt -- aus den Kopfzeilen, die das Gateway setzt und sonst entfernt."""
    def h(name):
        return (headers.get(name) or "").strip()
    roles = [r.strip() for r in h("X-OAAP-Roles").split(",") if r.strip()]
    return {"user": h("X-OAAP-User"), "user_id": h("X-OAAP-User-Id"),
            "display_name": h("X-OAAP-Display-Name"), "roles": roles}


class Door:
    def __init__(self, name, version, instructions=""):
        self.name = name
        self.version = version
        self.instructions = instructions
        self.tools = {}
        self.prompts = {}

    # ------------------------------------------------------- Werkzeuge
    def tool(self, name, title, description, properties=None, required=(),
             read_only=True, destructive=False, open_world=False, when=None):
        """Dekorator: meldet ein Werkzeug an. `when()` (optional) entscheidet,
        ob es gelistet ist -- ein nicht gelistetes wird auch nicht ausgeführt."""
        spec = {"name": name, "title": title, "description": description,
                "inputSchema": {"type": "object", "properties": properties or {},
                                **({"required": list(required)} if required else {})},
                "annotations": {"title": title, "readOnlyHint": bool(read_only),
                                "destructiveHint": bool(destructive),
                                "openWorldHint": bool(open_world)}}

        def wrap(fn):
            self.tools[name] = (spec, fn, when)
            return fn
        return wrap

    def prompt(self, name, title, description, text, arguments=()):
        """Eine Vorlage für eine Hauptaufgabe; {arg} im Text wird ersetzt."""
        self.prompts[name] = {"name": name, "title": title, "description": description,
                              "arguments": [{"name": a, "required": False} for a in arguments],
                              "_text": text}

    def _listed(self):
        return [spec for spec, _fn, when in self.tools.values() if when is None or when()]

    # -------------------------------------------------------- JSON-RPC
    def answer(self, msg, who):
        """Die Antwort auf eine Nachricht; None bei einer Benachrichtigung."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not msg.get("method"):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        if "id" not in msg:
            return None
        method, params = msg["method"], msg.get("params") or {}

        def ok(result):
            return {"jsonrpc": "2.0", "id": msg["id"], "result": result}

        def err(code, text):
            return {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": code, "message": text}}

        def said(text, error=True):
            return ok({"content": [{"type": "text", "text": text}], "isError": error})

        if method == "initialize":
            asked = params.get("protocolVersion")
            caps = {"tools": {}}
            if self.prompts:
                caps["prompts"] = {}
            return ok({"protocolVersion": asked if asked in MCP_VERSIONS else MCP_VERSIONS[0],
                       "capabilities": caps,
                       "serverInfo": {"name": self.name, "version": self.version},
                       **({"instructions": self.instructions} if self.instructions else {})})
        if method == "ping":
            return ok({})
        if method == "tools/list":
            return ok({"tools": self._listed()})
        if method == "prompts/list":
            return ok({"prompts": [{k: v for k, v in p.items() if not k.startswith("_")}
                                   for p in self.prompts.values()]})
        if method == "prompts/get":
            p = self.prompts.get(params.get("name"))
            if not p:
                return err(-32602, "Unknown prompt")
            args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            text = p["_text"]
            for a in p["arguments"]:
                text = text.replace("{" + a["name"] + "}", str(args.get(a["name"]) or "").strip())
            return ok({"description": p["description"],
                       "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
        if method == "resources/list":
            return ok({"resources": []})
        if method != "tools/call":
            return err(-32601, "Method not found")
        name = params.get("name")
        entry = self.tools.get(name)
        if not entry or (entry[2] is not None and not entry[2]()):
            return err(-32602, "Unknown tool")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            result = entry[1](args, who)
        except Refused as e:
            return said(str(e))
        except Exception as e:  # noqa: BLE001 -- ein Satz, nie ein Absturz der Tür
            return said(f"Das ging schief ({type(e).__name__}).")
        if isinstance(result, str):
            return said(result, error=False)
        return ok({"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                   "isError": False})

    # ------------------------------------------------------------ HTTP
    def serve(self, handler):
        """Beantwortet /mcp auf einem BaseHTTPRequestHandler (GET oder POST)."""
        def send(code, payload=None, ctype="application/json", extra=None):
            data = b"" if payload is None else (
                payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode())
            handler.send_response(code)
            handler.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith(("application/json", "text/")) else ""))
            handler.send_header("Content-Length", str(len(data)))
            handler.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                handler.send_header(k, v)
            handler.end_headers()
            if data:
                handler.wfile.write(data)

        if handler.command != "POST":
            return send(405, b"", "text/plain", {"Allow": "POST"})
        try:
            n = int(handler.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > BODY_LIMIT:
            handler.close_connection = True
            return send(413, {"error": "Rumpf zu groß"})
        body = handler.rfile.read(n) if n else b""
        if not (handler.headers.get("Content-Type") or "").startswith("application/json"):
            return send(415, {"error": "MCP spricht JSON."})
        try:
            msg = json.loads(body or b"{}")
        except ValueError:
            return send(400, {"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}})
        out = self.answer(msg, identity(handler.headers))
        if out is None:
            return send(202, b"", "text/plain")
        return send(200, out)
