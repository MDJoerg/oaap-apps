"""Eigenschaften der Modelle (RFC-0106 Stufe 1, Spec `oaap.ai.gateway` §3.1).

Vier Angaben, die ein Client braucht und ein Anwender selten weiß:

- `tools`   — das Modell beantwortet Werkzeugaufrufe in OpenAI-Form
- `vision`  — es nimmt Bilder in einer Anfrage
- `context` — so viele Token nimmt es als Eingabe
- `output`  — so viele Token erzeugt es höchstens je Antwort

Gespeichert wird je **Ziel** (Quelle + Modell der Quelle), denn dieselbe
Angabe gilt für jeden Modellnamen, der auf dieses Ziel zeigt. Ein
Modellname bekommt je Angabe **das schwächste seiner Ziele** — die Regel der
Ampel, auf Fähigkeiten angewandt: Ein Client muss sich auf den Wert
verlassen können, egal welches Ziel antwortet.

Jede Angabe ist gesetzt oder **unbekannt**; geraten wird nichts (D3). Jeder
Wert trägt seine Herkunft (D4): `supplier` (aus der Modellliste der Quelle),
`test` (aus „Werkzeugaufruf probieren“) oder `operator`. Ein Wert vom
Betreiber wird nie überschrieben; was die Quelle oder der Test danach
anders meldet, steht daneben (`seen`) und kann übernommen werden.
"""
import store

PROPS = ("tools", "vision", "context", "output")
BOOLS = ("tools", "vision")
INTS = ("context", "output")
ORIGINS = ("supplier", "test", "operator")
MAX_TOKENS = 100_000_000

LABEL_DE = {"tools": "Werkzeuge", "vision": "Bilder", "context": "Kontext (ein)",
            "output": "Ausgabe (aus)"}
ORIGIN_DE = {"supplier": "von der Quelle", "test": "aus dem Test", "operator": "vom Betreiber"}

# Die Tabelle `model_props` steht in store.SCHEMA (ein Weg zum Schema).


# ------------------------------------------------------------- Werte

def norm(prop, raw):
    """Ein Wert in seiner gespeicherten Form ("true"/"false" oder Ziffern).

    `None` oder "" heißt unbekannt und gibt "" zurück. Wirft ValueError mit
    einem Satz, wenn der Wert nicht passt.
    """
    if prop not in PROPS:
        raise ValueError(f"Die Eigenschaft „{prop}“ kennt das Gateway nicht "
                         f"(bekannt: {', '.join(PROPS)}).")
    if raw is None or raw == "":
        return ""
    if prop in BOOLS:
        if isinstance(raw, bool):
            return "true" if raw else "false"
        text = str(raw).strip().lower()
        if text in ("true", "ja", "1", "yes"):
            return "true"
        if text in ("false", "nein", "0", "no"):
            return "false"
        raise ValueError(f"{LABEL_DE[prop]}: ja, nein oder leer (unbekannt).")
    if isinstance(raw, bool):
        raise ValueError(f"{LABEL_DE[prop]}: eine Zahl von Token.")
    text = str(raw).strip().replace("_", "").replace(".", "").replace(" ", "")
    if text.lower().endswith("k") and text[:-1].isdigit():
        text = str(int(text[:-1]) * 1000)
    if not text.isdigit() or not 0 < int(text) <= MAX_TOKENS:
        raise ValueError(f"{LABEL_DE[prop]}: eine Zahl von Token zwischen 1 und "
                         f"{MAX_TOKENS:,}".replace(",", ".") + ".")
    return str(int(text))


def typed(prop, value):
    """Die gespeicherte Form als Python-Wert (für JSON); "" → None."""
    if value == "":
        return None
    if prop in BOOLS:
        return value == "true"
    return int(value)


def short(prop, value):
    """Für die Seite: ✓ / – / 128k / ?"""
    if value == "":
        return "?"
    if prop in BOOLS:
        return "✓" if value == "true" else "–"
    n = int(value)
    if n >= 1000 and n % 1000 == 0:
        return f"{n // 1000}k"
    if n >= 1024 and n % 1024 == 0:
        return f"{n // 1024}Ki"
    return str(n)


# ------------------------------------------------- Aus der Modellliste

def _positive(n):
    return isinstance(n, int) and not isinstance(n, bool) and 0 < n <= MAX_TOKENS


def _first_int(entry, paths):
    for path in paths:
        cur = entry
        for part in path:
            cur = cur.get(part) if isinstance(cur, dict) else None
        if _positive(cur):
            return cur
    return None


CONTEXT_PATHS = (("context_length",), ("context_window",), ("max_context_length",),
                 ("max_model_len",), ("max_input_tokens",), ("top_provider", "context_length"))
OUTPUT_PATHS = (("max_completion_tokens",), ("max_output_tokens",),
                ("top_provider", "max_completion_tokens"))
TOOL_WORDS = {"tools", "tool_use", "tool-use", "function_calling", "functions", "tool_calling"}
VISION_WORDS = {"vision", "image", "images", "image_input"}


def from_listing(entry):
    """Was ein Eintrag der Modellliste über das Modell **sagt** — nur das.

    Verschiedene Quellen nennen dieselbe Sache verschieden; gelesen wird,
    was ausdrücklich dasteht. Fehlt ein Wort in einer Fähigkeitenliste,
    folgt daraus **nicht** „nein“ — die Liste kann unvollständig sein.
    Ein ausdrückliches `false` in einem Objekt gilt dagegen.
    """
    if not isinstance(entry, dict):
        return {}
    out = {}
    n = _first_int(entry, CONTEXT_PATHS)
    if n:
        out["context"] = str(n)
    n = _first_int(entry, OUTPUT_PATHS)
    if n:
        out["output"] = str(n)
    caps = entry.get("capabilities")
    words = set()
    if isinstance(caps, list):
        words |= {str(c).lower() for c in caps if isinstance(c, str)}
    elif isinstance(caps, dict):
        for k, v in caps.items():
            k = str(k).lower()
            if isinstance(v, bool):
                if k in TOOL_WORDS:
                    out["tools"] = "true" if v else "false"
                elif k in VISION_WORDS:
                    out["vision"] = "true" if v else "false"
            elif isinstance(v, dict) and isinstance(v.get("supported"), bool):
                if k in TOOL_WORDS:
                    out["tools"] = "true" if v["supported"] else "false"
                elif k in VISION_WORDS:
                    out["vision"] = "true" if v["supported"] else "false"
    params = entry.get("supported_parameters")
    if isinstance(params, list):
        words |= {str(p).lower() for p in params if isinstance(p, str)}
    if words & TOOL_WORDS and "tools" not in out:
        out["tools"] = "true"
    if words & VISION_WORDS and "vision" not in out:
        out["vision"] = "true"
    mods = []
    arch = entry.get("architecture")
    if isinstance(arch, dict) and isinstance(arch.get("input_modalities"), list):
        mods = arch["input_modalities"]
    elif isinstance(entry.get("modalities"), dict) \
            and isinstance(entry["modalities"].get("input"), list):
        mods = entry["modalities"]["input"]
    if "image" in [str(m).lower() for m in mods] and "vision" not in out:
        out["vision"] = "true"
    if str(entry.get("type", "")).lower() == "vlm" and "vision" not in out:
        out["vision"] = "true"
    return out


def from_ollama_show(doc):
    """Was Ollamas eigener Pfad `/api/show` sagt (gemessen 20.11. auf oaap-test:
    `/v1/models` nennt dort nur Kennungen).

    Übernommen werden die Fähigkeiten (`tools`, `vision` -- nur „ja“, wie bei
    jeder Liste) und ein Kontext **nur**, wenn das Modell ein `num_ctx`
    ausdrücklich gesetzt hat. Die `context_length` in `model_info` ist, was
    das Modell könnte, nicht was Ollama bedient (es bedient mit seinem
    eigenen, oft viel kleineren `num_ctx`) -- sie zu nehmen hieße raten.
    """
    if not isinstance(doc, dict):
        return {}
    out = {}
    caps = doc.get("capabilities")
    if isinstance(caps, list):
        words = {str(c).lower() for c in caps}
        if "tools" in words:
            out["tools"] = "true"
        if "vision" in words:
            out["vision"] = "true"
    params = doc.get("parameters")
    if isinstance(params, str):
        for line in params.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit()                     and 0 < int(parts[1]) <= MAX_TOKENS:
                out["context"] = str(int(parts[1]))
    return out


# --------------------------------------------------------- Bücher

def table(db):
    """{(quelle, modell): {prop: {"value", "origin", "seen", "seen_origin"}}}"""
    out = {}
    for r in db.execute("SELECT * FROM model_props"):
        out.setdefault((r["supplier"], r["model"]), {})[r["prop"]] = {
            "value": r["value"], "origin": r["origin"], "seen": r["seen"],
            "seen_origin": r["seen_origin"]}
    return out


def note(db, supplier, model, found, origin):
    """Was Quelle oder Test meldet. Gibt die Zahl geänderter Zeilen zurück.

    Ein Wert vom Betreiber bleibt; die Meldung steht daneben (`seen`).
    Geschrieben wird nur, was sich ändert — der minütliche Test der Quellen
    hinter einer Destination soll die Datei nicht ständig anfassen.
    """
    assert origin in ("supplier", "test")
    changed = 0
    now = store.now()
    for prop, value in found.items():
        if prop not in PROPS or value == "":
            continue
        row = db.execute("SELECT * FROM model_props WHERE supplier=? AND model=? AND prop=?",
                         (supplier, model, prop)).fetchone()
        if row is None:
            db.execute("INSERT INTO model_props (supplier,model,prop,value,origin,seen,"
                       "seen_origin,updated) VALUES (?,?,?,?,?,?,?,?)",
                       (supplier, model, prop, value, origin, value, origin, now))
            changed += 1
        elif row["origin"] == "operator":
            if (row["seen"], row["seen_origin"]) != (value, origin):
                db.execute("UPDATE model_props SET seen=?, seen_origin=?, updated=? "
                           "WHERE supplier=? AND model=? AND prop=?",
                           (value, origin, now, supplier, model, prop))
                changed += 1
        elif (row["value"], row["origin"], row["seen"]) != (value, origin, value):
            db.execute("UPDATE model_props SET value=?, origin=?, seen=?, seen_origin=?, "
                       "updated=? WHERE supplier=? AND model=? AND prop=?",
                       (value, origin, value, origin, now, supplier, model, prop))
            changed += 1
    if changed:
        db.commit()
    return changed


def set_operator(db, actor, supplier, model, values):
    """Der Betreiber setzt Werte; "" heißt unbekannt (die Zeile geht).

    `values` = {prop: rohwert}; nur genannte Angaben werden angefasst.
    Wirft ValueError mit einem Satz, bevor irgendetwas geschrieben ist.
    Gibt die geänderten Angaben zurück.
    """
    clean = {prop: norm(prop, raw) for prop, raw in values.items()}
    now = store.now()
    changed = []
    for prop, value in clean.items():
        row = db.execute("SELECT * FROM model_props WHERE supplier=? AND model=? AND prop=?",
                         (supplier, model, prop)).fetchone()
        if value == "":
            if row is not None:
                db.execute("DELETE FROM model_props WHERE supplier=? AND model=? AND prop=?",
                           (supplier, model, prop))
                changed.append(f"{prop}=unbekannt")
            continue
        if row is None:
            db.execute("INSERT INTO model_props (supplier,model,prop,value,origin,updated) "
                       "VALUES (?,?,?,?,'operator',?)", (supplier, model, prop, value, now))
            changed.append(f"{prop}={value}")
        elif (row["value"], row["origin"]) != (value, "operator"):
            db.execute("UPDATE model_props SET value=?, origin='operator', updated=? "
                       "WHERE supplier=? AND model=? AND prop=?",
                       (value, now, supplier, model, prop))
            changed.append(f"{prop}={value}")
    if changed:
        store.log(db, actor, "model.props", f"{supplier}:{model}", " ".join(changed))
        db.commit()
    return changed


def take_seen(db, actor, supplier, model, prop):
    """Die Meldung der Quelle/des Tests anstelle des Betreiberwerts übernehmen."""
    row = db.execute("SELECT * FROM model_props WHERE supplier=? AND model=? AND prop=?",
                     (supplier, model, prop)).fetchone()
    if row is None or not row["seen"]:
        raise ValueError("Dazu liegt keine Meldung vor.")
    db.execute("UPDATE model_props SET value=seen, origin=seen_origin, updated=? "
               "WHERE supplier=? AND model=? AND prop=?", (store.now(), supplier, model, prop))
    store.log(db, actor, "model.props", f"{supplier}:{model}",
              f"{prop}={row['seen']} übernommen ({row['seen_origin']})")
    db.commit()


# ------------------------------------------------ Für einen Modellnamen

def weakest(prop, values):
    """Das schwächste von mehreren Zielen; "" = unbekannt.

    Ja/nein: ein „nein“ entscheidet (sicher), sonst macht ein unbekanntes
    das Ganze unbekannt, sonst „ja“. Zahlen: das kleinste, aber nur wenn
    alle bekannt sind — sonst wüsste man nicht, wie klein es wirklich ist.
    """
    if not values:
        return ""
    if prop in BOOLS:
        if "false" in values:
            return "false"
        return "" if "" in values else "true"
    if "" in values:
        return ""
    return str(min(int(v) for v in values))


def of_alias(alias, tab):
    """{prop: wert oder ""} für einen Modellnamen aus seinen Zielen."""
    targets = (alias or {}).get("targets") or []
    out = {}
    for prop in PROPS:
        vals = [tab.get((t["supplier"], t["model"]), {}).get(prop, {}).get("value", "")
                for t in targets]
        out[prop] = weakest(prop, vals)
    return out


def api_fields(values):
    """Die `oaap_*`-Felder für /v1/models — nur gesetzte (D12)."""
    return {f"oaap_{p}": typed(p, v) for p, v in values.items() if v != ""}
