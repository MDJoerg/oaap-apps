"""Die Schleife eines Gesprächs, ohne Netz (RFC-0074 D6, D7).

Ein Gespräch ist eine Liste von Nachrichten in der Form der OpenAI-Schnittstelle
(die das KI-Gateway weiterreicht): `user`, `assistant` (Text oder
`tool_calls`), `tool` (Ergebnis zu einer `tool_call_id`). Diese Datei
entscheidet nur, WAS als Nächstes geschieht; Netz und Datei macht `app.py`.

Die Reihenfolge einer Runde ist Absicht: erst die offenen Werkzeugaufrufe
(sie brauchen die Zusicherung der Person, und die ist am Anfang einer Anfrage
am frischesten), dann der Modellzug (er braucht keine Zusicherung und darf
lange dauern). Ein Werkzeugaufruf geschieht also nie mit einer Zusicherung,
die ein langsames Modell schon gealtert hat.
"""
import json

EFFECTS = ("read", "write", "execute")
META_EFFECT = "oaap/effect"
MAX_MODEL_TURNS = 6
MAX_RESULT_CHARS = 12_000
REFUSED = "Abgelehnt: Die Person hat diesen Aufruf nicht freigegeben. Nichts wurde geändert."


def effect_of(tool):
    """Die Wirkungsklasse, die das MCP-Gateway für ein Werkzeug der Sicht nennt.

    Nur das Gateway macht ein Werkzeug zum lesenden: Fehlt seine Angabe (ein
    älteres Gateway), gilt `write` -- also nachfragen. Der `readOnlyHint` der
    Quell-App ist ein Hinweis des Herstellers, keine Freigabe des Mandanten.
    """
    meta = tool.get("_meta") if isinstance(tool.get("_meta"), dict) else {}
    eff = meta.get(META_EFFECT)
    return eff if eff in EFFECTS else "write"


def tools_for_model(mcp_tools):
    """MCP-Werkzeuge -> (Werkzeuge für das Modell, {Name: Wirkungsklasse})."""
    out, effects = [], {}
    for t in mcp_tools or []:
        if not isinstance(t, dict) or not isinstance(t.get("name"), str):
            continue
        schema = t.get("inputSchema") if isinstance(t.get("inputSchema"), dict) else {}
        schema = dict(schema) or {"type": "object", "properties": {}}
        schema.setdefault("type", "object")
        out.append({"type": "function", "function": {
            "name": t["name"], "description": (t.get("description") or "")[:1024],
            "parameters": schema}})
        effects[t["name"]] = effect_of(t)
    return out, effects


def last_user_index(msgs):
    for i in range(len(msgs) - 1, -1, -1):
        if msgs[i].get("role") == "user":
            return i
    return -1


def model_turns_since_user(msgs):
    return sum(1 for m in msgs[last_user_index(msgs) + 1:] if m.get("role") == "assistant")


def pending_calls(msgs):
    """Werkzeugaufrufe des letzten Modellzugs, zu denen noch kein Ergebnis steht."""
    for i in range(len(msgs) - 1, -1, -1):
        m = msgs[i]
        if m.get("role") == "assistant":
            done = {x.get("tool_call_id") for x in msgs[i + 1:] if x.get("role") == "tool"}
            return [c for c in (m.get("tool_calls") or []) if c.get("id") not in done]
        if m.get("role") == "user":
            return []
    return []


def call_args(call):
    """(Argumente als dict, Fehlersatz) -- das Modell liefert sie als JSON-Text."""
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw, ""
    try:
        args = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return None, "Die Argumente des Aufrufs sind kein JSON."
    if not isinstance(args, dict):
        return None, "Die Argumente des Aufrufs sind kein Objekt."
    return args, ""


def result_text(msgs):
    """Antwort der via-Tür auf tools/call -> Text für das Modell."""
    for m in msgs:
        if "error" in m:
            err = m["error"] if isinstance(m["error"], dict) else {}
            return "Fehler: " + str(err.get("message") or "unbekannt")
        res = m.get("result")
        if isinstance(res, dict):
            parts = [c.get("text", "") for c in res.get("content") or []
                     if isinstance(c, dict) and c.get("type") == "text"]
            if not parts and "structuredContent" in res:
                parts = [json.dumps(res["structuredContent"], ensure_ascii=False)]
            text = "\n".join(parts) or "(leeres Ergebnis)"
            text = ("Fehler: " + text) if res.get("isError") else text
            return text[:MAX_RESULT_CHARS]
    return "Fehler: keine Antwort des Werkzeugs."


def for_model(msgs):
    """Nur die Felder, die das Modell kennt -- eigene Vermerke bleiben in der Datei."""
    out = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            out.append({"role": "user", "content": m.get("content", "")})
        elif role == "assistant":
            x = {"role": "assistant", "content": m.get("content") or ""}
            if m.get("tool_calls"):
                x["tool_calls"] = [{"id": c["id"], "type": "function",
                                    "function": {"name": c["function"]["name"],
                                                 "arguments": c["function"].get("arguments")
                                                 if isinstance(c["function"].get("arguments"), str)
                                                 else json.dumps(c["function"].get("arguments")
                                                                 or {})}}
                                   for c in m["tool_calls"]]
            out.append(x)
        elif role == "tool":
            out.append({"role": "tool", "tool_call_id": m.get("tool_call_id"),
                        "content": m.get("content", "")})
    return out


def model_message(reply):
    """Antwort von /chat/completions -> eine Nachricht für das Gespräch, oder ValueError."""
    try:
        msg = reply["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ValueError("Das Modell antwortete ohne Nachricht.") from None
    out = {"role": "assistant", "content": msg.get("content") or ""}
    calls = []
    for n, c in enumerate(msg.get("tool_calls") or []):
        fn = c.get("function") if isinstance(c, dict) else None
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
            continue
        calls.append({"id": str(c.get("id") or f"call_{n}"), "type": "function",
                      "function": {"name": fn["name"], "arguments": fn.get("arguments") or "{}"}})
    if calls:
        out["tool_calls"] = calls
    return out
