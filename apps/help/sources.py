"""Die Quellen von help (RFC-0078 §3, Stufe 1: `archive` und `local`).

`archive`: ein tar.gz eines Repository-Stands, geholt über die Destination
`docs-origin` -- die Adresse steht in der Destination, nicht hier (§7).
GitHub (codeload) und Forgejo liefern beide eines; der erste Ordner im
Archiv (`oaap-docs-main/`) wird abgeschnitten, gleich wie er heißt.

`local`: Markdown-Dateien, die ein admin auf der Seite ablegt
(/data/local); höhere Priorität, damit ein Betreiber einen Plattformtext
auf seinem Knoten berichtigen kann (§3).

Ein Archiv wird ganz angenommen oder ganz abgelehnt (§3.1): ein Pfad mit
`..`, ein absoluter Pfad, ein Link, zu groß, zu viele Dateien, abgeschnitten
-- dann bleibt der alte Index, und der Fehler steht am Abruf.
"""
import hashlib
import io
import os
import re
import tarfile
import urllib.error
import urllib.request

MAX_BYTES = 50 * 1024 * 1024
MAX_FILES = 5000
MAX_DOC = 2 * 1024 * 1024
SKIP_FILES = {"AGENTS.md", "CLAUDE.md"}
LOCAL_NAME = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,150}\.md$")


class Refused(Exception):
    """Das Archiv wird nicht angenommen; der Text sagt warum."""


def destination_url(env=os.environ):
    return (env.get("OAAP_DESTINATION_DOCS_ORIGIN_URL") or "").strip()


def fetch(base, path, max_bytes=MAX_BYTES, timeout=60):
    """Die Bytes des Archivs, nur über die Destination. Eine Weiterleitung
    wird NICHT verfolgt: sie führte an der Destination vorbei, und ein
    geschlossener Abfluss (RFC-0068) ließe sie ohnehin nicht durch."""
    if not base:
        raise Refused("Keine Destination docs-origin gebunden (Portal → Destinationen, "
                      "oder: oaap destination bind <instanz> docs-origin <ziel>).")
    url = base.rstrip("/") + "/" + path.lstrip("/")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "oaap-help"})
    try:
        with opener.open(req, timeout=timeout) as r:
            buf = io.BytesIO()
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                buf.write(chunk)
                if buf.tell() > max_bytes:
                    raise Refused(f"Archiv größer als {max_bytes // (1024 * 1024)} MB.")
            return buf.getvalue()
    except urllib.error.HTTPError as e:
        if 300 <= e.code < 400:
            raise Refused(f"Das Ziel leitet weiter ({e.code} → {e.headers.get('Location', '?')}); "
                          "die Destination muss direkt auf das Archiv zeigen.")
        raise Refused(f"Abruf scheiterte: HTTP {e.code}.")
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"Abruf scheiterte: {getattr(e, 'reason', e)}.")


def read_archive(data, max_files=MAX_FILES):
    """[(Pfad, Text)] der Markdown-Dateien, ohne den ersten Ordner. Jede
    Unregelmäßigkeit lehnt das GANZE Archiv ab."""
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        members = tf.getmembers()
    except (tarfile.TarError, EOFError, OSError, ValueError) as e:
        raise Refused(f"Kein lesbares tar.gz ({type(e).__name__}: {e}).")
    if len(members) > max_files:
        raise Refused(f"Mehr als {max_files} Einträge im Archiv.")
    out = []
    for m in members:
        name = m.name
        if name.startswith("/") or "\\" in name or ".." in name.split("/"):
            raise Refused(f"Unerlaubter Pfad im Archiv: {name!r}.")
        if m.issym() or m.islnk() or m.isdev() or m.isfifo():
            if name.endswith(".md"):
                raise Refused(f"Link oder Gerät statt Datei: {name!r}.")
            continue
        if not m.isfile() or not name.endswith(".md"):
            continue
        # Arbeitsanweisungen für Agenten im Repository sind keine Hilfe.
        if name.rsplit("/", 1)[-1] in SKIP_FILES:
            continue
        parts = name.split("/", 1)
        if len(parts) < 2 or not parts[1]:
            continue
        if m.size > MAX_DOC:
            raise Refused(f"Dokument größer als {MAX_DOC // (1024 * 1024)} MB: {name!r}.")
        try:
            raw = tf.extractfile(m).read()
        except (tarfile.TarError, EOFError, OSError) as e:
            raise Refused(f"Archiv abgeschnitten oder beschädigt bei {name!r} ({e}).")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Refused(f"Kein UTF-8: {name!r}.")
        out.append((parts[1], text))
    if not out:
        raise Refused("Das Archiv enthält kein einziges Markdown-Dokument.")
    return out


def sha256(data):
    return hashlib.sha256(data).hexdigest()


# --- local ----------------------------------------------------------------

def local_dir(data_dir):
    return os.path.join(data_dir, "local")


def read_local(data_dir):
    root = local_dir(data_dir)
    out = []
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            p = os.path.join(d, f)
            if os.path.islink(p):
                continue
            rel = os.path.relpath(p, root).replace(os.sep, "/")
            with open(p, encoding="utf-8", errors="replace") as fh:
                out.append((rel, fh.read()))
    return out


def save_local(data_dir, name, text):
    """Legt eine lokale Datei ab; der Name ist ein Pfad aus a-z0-9/_.-."""
    name = (name or "").strip().lower()
    if not name.endswith(".md"):
        name += ".md"
    if not LOCAL_NAME.match(name) or ".." in name.split("/") or "//" in name:
        raise Refused("Dateiname: a-z, 0-9, / _ . -, endet auf .md.")
    if len(text.encode("utf-8")) > MAX_DOC:
        raise Refused("Dokument zu groß.")
    p = os.path.join(local_dir(data_dir), *name.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace("\r\n", "\n"))
    return name


def delete_local(data_dir, name):
    name = (name or "").strip().lower()
    if not LOCAL_NAME.match(name) or ".." in name.split("/"):
        raise Refused("Unbekannte Datei.")
    p = os.path.join(local_dir(data_dir), *name.split("/"))
    if not os.path.isfile(p):
        raise Refused("Unbekannte Datei.")
    os.remove(p)
