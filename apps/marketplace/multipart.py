"""Datei-Uploads lesen — `multipart/form-data`, im Fluss und begrenzt.

Warum von Hand: Ein Paket ist zweistellig megabytegroß. Es komplett in
den Speicher zu ziehen, wäre auf einem Raspi der Unterschied zwischen
„läuft“ und „läuft nicht“ — und `cgi.FieldStorage`, das das früher
erledigt hätte, gibt es in Python 3.13 nicht mehr.

Deshalb liest dieses Modul den Körper **im Fluss**: kleine Formularfelder
in den Speicher, den Datei-Anteil direkt in eine temporäre Datei, und
alles unter einer harten Obergrenze, die vor dem ersten Byte feststeht.

Bewusst eng: genau das, was die Studio-Formulare senden (ein Dateifeld,
ein paar Textfelder). Kein verschachteltes `multipart/mixed`, keine
Kodierungen im Körper — beides schickt kein Browser-Formular.
"""

import os
import re
import tempfile

# Ein Textfeld ist ein Textfeld. Was größer ist, ist ein Fehler oder ein
# Angriff — in beiden Fällen ist Abbrechen die richtige Antwort.
MAX_FIELD_BYTES = 1 << 20
CHUNK = 1 << 16

BOUNDARY_RE = re.compile(rb'boundary="?([^";]+)"?', re.I)
NAME_RE = re.compile(rb'name="([^"]*)"', re.I)
FILENAME_RE = re.compile(rb'filename="([^"]*)"', re.I)


class MultipartError(Exception):
    """Der Körper ist nicht als Formular lesbar."""


class _Body:
    """Gepufferter Blick auf den Anfrage-Körper mit hartem Deckel."""

    def __init__(self, stream, length, limit):
        self.stream = stream
        self.remaining = length
        self.limit = limit
        self.read_total = 0
        self.buf = b""

    def fill(self, want):
        while len(self.buf) < want and self.remaining > 0:
            chunk = self.stream.read(min(CHUNK, self.remaining))
            if not chunk:
                self.remaining = 0
                break
            self.remaining -= len(chunk)
            self.read_total += len(chunk)
            if self.read_total > self.limit:
                raise MultipartError("Der Upload überschreitet die erlaubte Größe.")
            self.buf += chunk
        return self.buf

    def drain(self):
        """Rest wegwerfen — damit die Verbindung wiederverwendbar bleibt."""
        while self.remaining > 0:
            chunk = self.stream.read(min(CHUNK, self.remaining))
            if not chunk:
                break
            self.remaining -= len(chunk)
        self.buf = b""


def _boundary(content_type):
    m = BOUNDARY_RE.search((content_type or "").encode("latin-1", "replace"))
    if not m:
        raise MultipartError("Dem Formular fehlt die Trennmarke (boundary).")
    return m.group(1)


def _headers(raw):
    """Kopfzeilen eines Teils → (name, dateiname oder None)."""
    name = filename = None
    for line in raw.split(b"\r\n"):
        if not line.lower().startswith(b"content-disposition:"):
            continue
        n = NAME_RE.search(line)
        f = FILENAME_RE.search(line)
        name = n.group(1).decode("utf-8", "replace") if n else None
        filename = f.group(1).decode("utf-8", "replace") if f else None
    if name is None:
        raise MultipartError("Ein Formularteil hat keinen Feldnamen.")
    return name, filename


def parse(stream, content_type, content_length, limit, tmpdir=None):
    """Lies ein `multipart/form-data`-Formular.

    Rückgabe: `(fields, files)` — `fields` bildet Feldname auf Text ab,
    `files` Feldname auf `{"filename", "path", "bytes"}`. Die temporären
    Dateien gehören danach dem Aufrufer; er muss sie löschen.
    """
    if content_length is None or content_length <= 0:
        raise MultipartError("Der Upload kam ohne Inhalt an.")
    if content_length > limit:
        raise MultipartError("Der Upload überschreitet die erlaubte Größe.")

    boundary = _boundary(content_type)
    sep = b"--" + boundary
    body = _Body(stream, content_length, limit)
    fields, files = {}, {}

    try:
        # Vorspann bis zur ersten Trennmarke
        while True:
            buf = body.fill(len(sep) + 2)
            idx = buf.find(sep)
            if idx >= 0:
                body.buf = buf[idx + len(sep):]
                break
            if body.remaining <= 0:
                raise MultipartError("Im Upload steht keine Trennmarke.")
            body.buf = buf[-(len(sep) + 2):]

        needle = b"\r\n" + sep
        while True:
            head = body.fill(2)
            if head.startswith(b"--"):     # Schlussmarke
                break
            if head.startswith(b"\r\n"):
                body.buf = head[2:]
            # Kopfzeilen des Teils
            while True:
                buf = body.fill(4096)
                end = buf.find(b"\r\n\r\n")
                if end >= 0:
                    raw, body.buf = buf[:end], buf[end + 4:]
                    break
                if body.remaining <= 0:
                    raise MultipartError("Ein Formularteil bricht mitten in "
                                         "den Kopfzeilen ab.")
            name, filename = _headers(raw)

            out = None
            written = 0
            collected = b""
            if filename is not None:
                fd, path = tempfile.mkstemp(prefix="oaap-pkg-", suffix=".upload",
                                            dir=tmpdir)
                out = os.fdopen(fd, "wb")
            try:
                while True:
                    buf = body.fill(len(needle) + CHUNK)
                    idx = buf.find(needle)
                    if idx >= 0:
                        piece, body.buf = buf[:idx], buf[idx + len(needle):]
                        done = True
                    else:
                        keep = len(needle) - 1
                        if body.remaining <= 0 and len(buf) <= keep:
                            raise MultipartError("Der Upload endet ohne "
                                                 "Schlussmarke — vermutlich "
                                                 "abgebrochen.")
                        piece, body.buf = (buf[:-keep], buf[-keep:]) if keep else (buf, b"")
                        done = False
                    if out is not None:
                        out.write(piece)
                        written += len(piece)
                    else:
                        collected += piece
                        if len(collected) > MAX_FIELD_BYTES:
                            raise MultipartError(
                                f"Das Feld „{name}“ ist zu groß.")
                    if done:
                        break
            finally:
                if out is not None:
                    out.close()

            if filename is not None:
                files[name] = {"filename": os.path.basename(filename),
                               "path": path, "bytes": written}
            else:
                fields[name] = collected.decode("utf-8", "replace")
    except Exception:
        for f in files.values():
            try:
                os.remove(f["path"])
            except OSError:
                pass
        body.drain()
        raise
    body.drain()
    return fields, files


def cleanup(files):
    """Temporäre Dateien wegräumen — in jedem Ausgang eines Handlers."""
    for f in (files or {}).values():
        try:
            os.remove(f["path"])
        except (OSError, TypeError, KeyError):
            pass
