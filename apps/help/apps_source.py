"""Die Dokumentation der installierten Apps des Mandanten (RFC-0094 §3).

Der Knoten legt sie lesend unter /oaap/docs ab (Manifest 0.17 `docs:` der
Apps, `docs_sources: {read: true}` dieser App):

    /oaap/docs/index.json                        was gerade gilt
    /oaap/docs/<instanz-id>/<version>/<produkt>/…  die Dateien

Hier wird nur gelesen. Je Hilfsprodukt gewinnt die Produktiv-Instanz; gibt
es das Produkt nur in einer Test-Instanz, kommt dessen Doku als Vorschau
(`channel: test`, RFC-0094 D4 -- `index.visible` zeigt sie nur admin und
keyuser). Der Ordner gehört dem Knoten: kein Link wird verfolgt, kein Pfad
verlässt seinen Produktordner, jede Datei ist begrenzt.
"""
import json
import os
import stat

SOURCE = "apps"
PRIORITY = 70            # über der Plattform-Doku (50), unter eigenen Notizen (90)
TRUST = "app"
MAX_FILE = 2 * 1024 * 1024
MAX_FILES = 20000
DEFAULT_ROOT = "/oaap/docs"


def root_of(env=os.environ):
    return (env.get("HELP_DOCS_DIR") or DEFAULT_ROOT).strip()


def _kind(rel, kinds):
    """Der Dokumenttyp eines Pfads: der längste passende Eintrag gewinnt;
    ein Eintrag mit / am Ende gilt für den Ordner, sonst für genau die Datei."""
    best, size = "", -1
    for key, kind in (kinds or {}).items():
        k = str(key)
        hit = rel.startswith(k) if k.endswith("/") else rel == k
        if hit and len(k) > size:
            best, size = kind, len(k)
    return best


def _files(base):
    """(relativer Pfad, Text) jeder .md-Datei unter base, ohne Links."""
    out = []
    if not os.path.isdir(base) or os.path.islink(base):
        return out
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames
                             if not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            full = os.path.join(dirpath, fn)
            st = os.lstat(full)
            if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE:
                continue
            with open(full, encoding="utf-8", errors="replace") as f:
                out.append((os.path.relpath(full, base).replace(os.sep, "/"), f.read()))
            if len(out) >= MAX_FILES:
                return out
    return out


def read(root):
    """(Dokumente, Produkte, Zustand). Ohne Ordner oder Index: leer, mit
    einem Satz im Zustand -- nie ein Fehler, der die Hilfe anhält."""
    state = {"root": root, "ok": False, "sets": 0, "products": 0, "files": 0, "error": ""}
    idx = os.path.join(root, "index.json")
    try:
        with open(idx, encoding="utf-8") as f:
            index = json.load(f)
    except FileNotFoundError:
        state["error"] = ("keine Doku der Apps eingehängt" if not os.path.isdir(root)
                          else "noch keine App mit Doku im Mandanten")
        return [], [], state
    except (OSError, ValueError) as e:
        state["error"] = f"index.json nicht lesbar: {e}"
        return [], [], state
    sets = index.get("sets") if isinstance(index, dict) else None
    if not isinstance(sets, list):
        state["error"] = "index.json hat eine unbekannte Form"
        return [], [], state
    # Je Produkt EINE Quelle: Produktiv vor Test, sonst die zuerst genannte.
    chosen = {}
    for st in sets:
        if not isinstance(st, dict):
            continue
        for p in st.get("products") or []:
            pid = p.get("product") if isinstance(p, dict) else None
            if not pid:
                continue
            cur = chosen.get(pid)
            if cur is None or (cur[0].get("channel") == "test"
                               and st.get("channel") == "production"):
                chosen[pid] = (st, p)
    docs, products = [], []
    for pid, (st, p) in sorted(chosen.items()):
        rel_dir = str(st.get("dir") or "")
        if not rel_dir or ".." in rel_dir.split("/") or rel_dir.startswith("/"):
            continue
        base = os.path.join(root, *rel_dir.split("/"), pid)
        files = _files(base)
        if not files:
            continue
        channel = st.get("channel") or ""
        for rel, text in files:
            docs.append({"source": SOURCE, "priority": PRIORITY, "trust": TRUST,
                         "path": f"{pid}/{rel}", "text": text, "product": pid,
                         "kind": _kind(rel, p.get("kinds")) or "unspecified",
                         "readers": list(p.get("readers") or []),
                         "audience": list(p.get("audience") or ["admin"]),
                         "lang": p.get("lang") or "", "app_version": st.get("version") or "",
                         "channel": channel})
        names = {r for r, _t in files}
        entry = p.get("entry") or next((n for n in ("index.md", "README.md") if n in names), "")
        products.append({"product": pid, "title": p.get("title") or pid,
                         "color": p.get("color") or "", "lang": p.get("lang") or "",
                         "entry": f"{pid}/{entry[:-3]}" if entry.endswith(".md") and entry in names else "",
                         "version": st.get("version") or "", "channel": channel,
                         "app": st.get("app_name") or st.get("app_id") or "",
                         "instance": st.get("name") or "", "source": SOURCE,
                         "audience": list(p.get("audience") or ["admin"]),
                         "readers": list(p.get("readers") or []), "files": len(files)})
        state["files"] += len(files)
    state.update(ok=True, sets=len(sets), products=len(products),
                 updated=index.get("updated", ""))
    return docs, products, state


def stamp(root):
    """Woran die Hilfe merkt, dass der Knoten etwas geändert hat."""
    try:
        return os.stat(os.path.join(root, "index.json")).st_mtime_ns
    except OSError:
        return 0
