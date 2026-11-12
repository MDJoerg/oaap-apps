"""Asset-Typ oaap-profile: eine Aufbau-Vorlage (RFC-0055, RFC-0079; RFC-0077 §15.8.2).

Geprüft wird, was der Marketplace braucht -- Kennung, Version, Titel, Schritte --
nicht alles, was der Knoten prüft. Der Knoten prüft das Profil beim Hochladen
selbst noch einmal: diese Prüfung ist ein Tor, nicht die Instanz.
"""
import json
import re

MAX_BYTES = 256 * 1024
SCHEMA = "oaap.tenant-profile/1"
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")


class ProfileError(Exception):
    pass


def read(path):
    """-> das Profil als dict (geprüft) oder ProfileError."""
    with open(path, "rb") as f:
        data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ProfileError(f"Eine Aufbau-Vorlage darf höchstens {MAX_BYTES // 1024} KB groß sein.")
    try:
        doc = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        raise ProfileError("Die Aufbau-Vorlage ist kein UTF-8.")
    except ValueError as e:
        raise ProfileError(f"Die Aufbau-Vorlage ist kein gültiges JSON ({e.msg}, Zeile {e.lineno}).")
    if not isinstance(doc, dict):
        raise ProfileError("Die Aufbau-Vorlage ist kein JSON-Objekt.")
    if doc.get("profile") != SCHEMA:
        raise ProfileError(f"Das Feld „profile“ muss „{SCHEMA}“ sein.")
    if not ID_RE.match(str(doc.get("id") or "")):
        raise ProfileError("„id“ fehlt oder ist keine gültige Kennung (Kleinbuchstaben, Ziffern, "
                           "Bindestrich, 3-40 Zeichen).")
    if not VERSION_RE.match(str(doc.get("version") or "")):
        raise ProfileError("„version“ fehlt oder ist keine Version wie 1.2.0 -- im Marketplace "
                           "ist sie Pflicht (sie wird zur Release-Version).")
    if not str(doc.get("title") or "").strip():
        raise ProfileError("„title“ fehlt.")
    steps = doc.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ProfileError("„steps“ fehlt oder ist leer.")
    for i, s in enumerate(steps):
        if not isinstance(s, dict) or not str(s.get("type") or "").strip():
            raise ProfileError(f"steps[{i}] hat keinen „type“.")
    return doc
