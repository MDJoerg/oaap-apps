"""Ein Paket aufnehmen: Prüfsumme, Größe, Manifest -- ohne etwas zu entpacken.

Der Katalog führt nie Code aus und entpackt nie mehr als einen Eintrag:
`oaap-app.yaml`. Alles andere (das sichere Entpacken, RFC-0019 §5) ist
Sache des Knotens beim Installieren. Hier wird nur geprüft, woran der
Knoten später ohnehin scheitern würde, damit die Ablehnung beim
Hochladen kommt und nicht erst beim Installieren.

Wer hier das letzte Wort hat: der Knoten. Ein Eintrag im Katalog heißt
"hochgeladen und lesbar", nicht "wird installiert".

PyYAML, wie beim Studio: hier werden FREMDE Manifeste gelesen, und ein
selbstgebauter Leser, der eine Schreibweise missversteht, erzeugt genau
die stille Falschaussage, gegen die diese Prüfung antritt.
"""
import hashlib
import os
import re
import zipfile

import yaml

MANIFEST_NAME = "oaap-app.yaml"
MAX_ENTRIES = 20000
MAX_UNCOMPRESSED = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


class PackageError(Exception):
    """Das Paket kann nicht aufgenommen werden; der Text ist für Menschen."""


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def package_root(names):
    """Archivwurzel oder einziger Oberordner -- die Regel des Knotens."""
    if MANIFEST_NAME in names:
        return ""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        top = tops.pop()
        if f"{top}/{MANIFEST_NAME}" in names:
            return f"{top}/"
    return None


def _unsafe(infos):
    out = []
    for i in infos:
        n = i.filename
        mode = (i.external_attr >> 16) & 0xF000
        if (n.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", n) or "\\" in n
                or ".." in n.split("/")
                or mode in (0xA000, 0x6000, 0x2000, 0x1000, 0xC000)):
            out.append(n)
    return out


def read(path, max_bytes):
    """{"sha256", "bytes", "app": {id, name, version, description, type}}.

    Wirft PackageError mit lesbarer Begründung.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        raise PackageError("Die Datei ist nicht lesbar.")
    if size == 0:
        raise PackageError("Die Datei ist leer.")
    if size > max_bytes:
        raise PackageError(
            f"Das Paket ist {size / (1024 * 1024):.1f} MB groß -- erlaubt sind "
            f"{max_bytes // (1024 * 1024)} MB.")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > MAX_ENTRIES:
                raise PackageError(f"Das Archiv enthält {len(infos)} Dateien "
                                   f"-- höchstens {MAX_ENTRIES} werden gelesen.")
            if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
                raise PackageError("Entpackt wäre das Archiv größer als 1 GiB.")
            bad = _unsafe(infos)
            if bad:
                raise PackageError(
                    "Das Archiv enthält absolute Pfade, „..“ oder "
                    "Verknüpfungen (" + ", ".join(sorted(bad)[:3])
                    + "): der Knoten würde es beim Entpacken ablehnen.")
            names = [i.filename for i in infos]
            root = package_root(names)
            if root is None:
                raise PackageError(f"Im Archiv steht kein {MANIFEST_NAME} -- "
                                   "weder in der Wurzel noch in einem "
                                   "einzelnen Oberordner.")
            info = zf.getinfo(root + MANIFEST_NAME)
            if info.file_size > MAX_MANIFEST_BYTES:
                raise PackageError(f"{MANIFEST_NAME} ist zu groß für ein Manifest.")
            raw = zf.read(root + MANIFEST_NAME)
    except zipfile.BadZipFile:
        raise PackageError("Die Datei ist kein ZIP-Archiv.")
    try:
        m = yaml.safe_load(raw.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise PackageError(f"{MANIFEST_NAME} ist nicht lesbar: {str(e)[:120]}")
    app = (m or {}).get("app") if isinstance(m, dict) else None
    if not isinstance(app, dict):
        raise PackageError(f"{MANIFEST_NAME} hat keinen Abschnitt „app“.")
    app_id, version = app.get("id"), app.get("version")
    if not isinstance(app_id, str) or not ID_RE.match(app_id):
        raise PackageError("app.id fehlt oder ist keine gültige Kennung "
                           "(Kleinbuchstaben, Ziffern, Bindestrich).")
    if not isinstance(version, str) or not VERSION_RE.match(version):
        raise PackageError("app.version fehlt oder ist keine Version wie 1.2.3.")
    name = app.get("name") if isinstance(app.get("name"), str) else app_id
    desc = app.get("description") if isinstance(app.get("description"), str) else ""
    return {"sha256": sha256_file(path), "bytes": size,
            "app": {"id": app_id, "name": name[:120], "version": version,
                    "description": desc[:2000],
                    "type": app.get("type") if isinstance(app.get("type"), str) else ""}}
