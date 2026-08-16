"""Pakete lesen und prüfen — die Studio-Stufe aus RFC-0019.

Das Studio bekommt eine **ZIP** in die Hand, wie sie eine
Projekt-KI abliefert, und beantwortet drei Fragen, **bevor** irgendetwas
zu einem Knoten geschickt wird:

1. Steckt ein Manifest drin, und wo?
2. Hält das Manifest, was die Plattform verlangt?
3. Was davon ist **rahmen-relevant** (RFC-0019 §3) — also das, wofür
   die Plattform einen Menschen verlangt statt ein Deploy-Token?

Dieses Modul entpackt **nichts**. Es liest das Inhaltsverzeichnis des
Archivs und genau einen Eintrag — `oaap-app.yaml`. Das Entpacken mit
all seinen scharfen Kanten (RFC-0019 §5) ist Sache des Knotens; das
Studio prüft die Kanten trotzdem und sagt sie vorher an, damit ein
Mensch die Ablehnung versteht, bevor er 10 MB durchs Netz schiebt.

**Wer hier das letzte Wort hat:** der Knoten. Alles hier ist eine
Vorschau. Diese Trennung ist Absicht und steht auch so in der
Oberfläche — ein Werkzeug, das „geprüft" sagt und dann doch abgelehnt
wird, wäre schlimmer als eines, das gar nicht prüft.

Abhängigkeit PyYAML, wie beim Store Editor und aus demselben Grund:
hier werden **fremde** Manifeste gelesen, und ein selbstgebauter
YAML-Leser, der eine Schreibweise missversteht, erzeugt genau die
stille Falschaussage, gegen die diese Prüfung antritt.
"""

import hashlib
import re
import zipfile

import yaml

# Prüfergebnis-Stufen — dieselbe Dreiteilung wie im Store Editor, damit
# beide Werkzeuge dasselbe meinen, wenn sie dasselbe Wort benutzen.
FEHLER = "fehler"      # so lehnt der Knoten ab
BEFUND = "befund"      # verdächtig, kostet vermutlich eine Bestätigung
HINWEIS = "hinweis"    # auffällig, vielleicht Absicht

MANIFEST_NAME = "oaap-app.yaml"

# Grenzen fürs Lesen. Der Knoten hat eigene (strengere) beim Entpacken;
# diese hier schützen das Studio selbst vor einem Archiv, das mehr
# verspricht als der Container an Speicher hat.
MAX_ENTRIES = 20000
MAX_UNCOMPRESSED = 1024 * 1024 * 1024   # 1 GiB entpackt
MAX_MANIFEST_BYTES = 512 * 1024

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CONFIG_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
MANIFEST_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")

KNOWN_MANIFEST_MAJOR = 0
KNOWN_MANIFEST_MINOR = 2
APP_TYPES = ("native", "image", "wrapped")
APP_CLASSES = ("frontend", "service")
ROLES = ("admin", "keyuser", "user", "guest", "partner", "public")
TOP_LEVEL = ("oaap_manifest", "must_understand", "app", "services", "routes",
             "storage", "config", "health", "placement", "endpoints")


class PackageError(Exception):
    """Das Archiv ist als Paket nicht lesbar — Ende der Prüfung."""


def finding(level, text, hint=""):
    return {"level": level, "text": text, "hint": hint}


# ------------------------------------------------------------- Archiv lesen

def _entries(zf):
    """Einträge ohne Verzeichnisse, in Archiv-Reihenfolge."""
    return [i for i in zf.infolist() if not i.is_dir()]


def package_root(names):
    """Wo liegt die Paketwurzel — Archivwurzel oder einziger Oberordner?

    Genau die Regel des Knotens (appctl `_pkg_root`): „Projektordner
    zippen" ist, was Leute wirklich tun, und deshalb erlaubt. Zwei
    Oberordner sind es nicht — dann ist unklar, was das Paket ist.

    Gibt das Präfix zurück (`""` oder `"ordner/"`), oder None, wenn
    kein Manifest zu finden ist.
    """
    if MANIFEST_NAME in names:
        return ""
    tops = {n.split("/", 1)[0] for n in names if "/" in n}
    if len(tops) == 1:
        top = tops.pop()
        if f"{top}/{MANIFEST_NAME}" in names:
            return f"{top}/"
    return None


def _unsafe_entries(infos):
    """Einträge, an denen der Knoten beim Entpacken abbricht (RFC-0019 §5).

    Das Studio entpackt nicht — es meldet sie, damit die Ablehnung nicht
    erst nach der Übertragung kommt und niemand rätselt, woran es lag.
    """
    absolute, traversal, links = [], [], []
    for i in infos:
        n = i.filename
        if n.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", n) or "\\" in n:
            absolute.append(n)
        elif ".." in n.split("/"):
            traversal.append(n)
        # oberes Byte der externen Attribute = Unix-Modus; 0xA000 = Symlink
        mode = (i.external_attr >> 16) & 0xF000
        if mode in (0xA000, 0x6000, 0x2000, 0x1000, 0xC000):
            links.append(n)
    return absolute, traversal, links


def read_package(path, max_bytes):
    """Ein Paket aufnehmen: Prüfsumme, Umfang, Manifest — ohne Entpacken.

    `path` ist die hochgeladene Datei auf der Platte des Studios.
    Rückgabe: dict mit `sha256`, `bytes`, `entries`, `uncompressed`,
    `root`, `manifest_bytes`, `manifest_text`, `findings`.

    Wirft `PackageError`, wenn hier nicht weiterzuarbeiten ist.
    """
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    if size == 0:
        raise PackageError("Die Datei ist leer.")
    if size > max_bytes:
        raise PackageError(
            f"Das Paket ist {size / (1024 * 1024):.1f} MB groß — erlaubt sind "
            f"{max_bytes // (1024 * 1024)} MB.")

    findings = []
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                raise PackageError(
                    f"Das Archiv ist beschädigt (fehlerhafter Eintrag: {bad}).")
            infos = _entries(zf)
            if len(infos) > MAX_ENTRIES:
                raise PackageError(
                    f"Das Archiv enthält {len(infos)} Dateien — das Studio "
                    f"liest höchstens {MAX_ENTRIES}.")
            uncompressed = sum(i.file_size for i in infos)
            if uncompressed > MAX_UNCOMPRESSED:
                raise PackageError(
                    f"Entpackt wären das {uncompressed / (1024 ** 3):.1f} GiB "
                    f"— das Studio liest höchstens "
                    f"{MAX_UNCOMPRESSED // (1024 ** 3)} GiB.")

            absolute, traversal, links = _unsafe_entries(infos)
            for group, text in (
                (absolute, "absolute Pfade"),
                (traversal, "Pfade mit „..“ (Ausbruch aus der Paketwurzel)"),
                (links, "Verknüpfungen oder Spezialdateien"),
            ):
                if group:
                    findings.append(finding(
                        FEHLER,
                        f"Das Archiv enthält {text}: "
                        + ", ".join(sorted(group)[:5])
                        + (" …" if len(group) > 5 else ""),
                        "Der Knoten bricht daran beim Entpacken ab "
                        "(RFC-0019 §5). Bitte das Paket ohne diese Einträge "
                        "neu erzeugen."))

            names = [i.filename for i in infos]
            root = package_root(names)
            if root is None:
                raise PackageError(
                    f"Im Archiv steht kein {MANIFEST_NAME} — weder in der "
                    "Wurzel noch in einem einzelnen Oberordner. Genau das "
                    "verlangt auch der Knoten.")
            entry = root + MANIFEST_NAME
            info = zf.getinfo(entry)
            if info.file_size > MAX_MANIFEST_BYTES:
                raise PackageError(
                    f"{MANIFEST_NAME} ist {info.file_size} Byte groß — das "
                    "ist kein Manifest mehr.")
            manifest_bytes = zf.read(entry)
    except zipfile.BadZipFile:
        raise PackageError(
            "Die Datei ist kein ZIP-Archiv. Erwartet wird das Paket, wie es "
            "die Projekt-KI abliefert.")

    try:
        manifest_text = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise PackageError(
            f"{MANIFEST_NAME} ist nicht UTF-8 kodiert.")

    return {
        "sha256": digest.hexdigest(),
        "bytes": size,
        "entries": len(infos),
        "uncompressed": uncompressed,
        "root": root,
        "manifest_bytes": manifest_bytes,
        "manifest_text": manifest_text,
        "findings": findings,
    }


# ------------------------------------------------------------ Manifest lesen

def parse_manifest(text):
    """YAML lesen. Wirft `PackageError` mit lesbarer Begründung."""
    try:
        m = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PackageError(f"{MANIFEST_NAME} ist kein gültiges YAML: {e}")
    if not isinstance(m, dict):
        raise PackageError(
            f"{MANIFEST_NAME} enthält kein Manifest (erwartet wird eine "
            "Zuordnung von Feldern, gefunden wurde etwas anderes).")
    return m


def _need(m, key, level=FEHLER):
    if key not in m:
        return finding(level, f"Pflichtfeld `{key}` fehlt.")
    return None


def validate(m):
    """Das Manifest gegen die Regeln halten, die der Knoten anlegt.

    Bewusst **tolerant beim Lesen, streng beim Melden**: unbekannte
    Felder sind ein Hinweis (ein Knoten ignoriert sie, RFC-0012 §8.2),
    verletzte Regeln sind Fehler. Die Reihenfolge folgt dem Manifest,
    damit die Meldungen dort stehen, wo man hinschaut.
    """
    out = []

    mv = m.get("oaap_manifest")
    if mv is None:
        out.append(finding(FEHLER, "Pflichtfeld `oaap_manifest` fehlt.",
                           "Erwartet wird die Formatversion, z. B. \"0.2\"."))
    else:
        mm = MANIFEST_VERSION_RE.match(str(mv))
        if not mm:
            out.append(finding(FEHLER,
                               f"`oaap_manifest: {mv}` ist keine Formatversion "
                               "der Art MAJOR.MINOR."))
        else:
            major, minor = int(mm.group(1)), int(mm.group(2))
            if major != KNOWN_MANIFEST_MAJOR:
                out.append(finding(
                    FEHLER, f"Manifest-Format {mv} kennt diese Plattform nicht.",
                    "Nur eine neue MAJOR-Version darf ein Knoten ablehnen "
                    "(RFC-0012 §8.2) — genau das wäre hier der Fall."))
            elif minor > KNOWN_MANIFEST_MINOR:
                out.append(finding(
                    HINWEIS, f"Manifest-Format {mv} ist neuer als das hier "
                    f"bekannte 0.{KNOWN_MANIFEST_MINOR}.",
                    "Ein Knoten liest es trotzdem und ignoriert, was er nicht "
                    "kennt. Diese Prüfung tut dasselbe."))

    for key in ("app", "services", "routes", "health"):
        f = _need(m, key)
        if f:
            out.append(f)

    unknown = [k for k in m if k not in TOP_LEVEL]
    if unknown:
        out.append(finding(HINWEIS,
                           "Unbekannte Felder auf oberster Ebene: "
                           + ", ".join(sorted(unknown)),
                           "Ein Knoten überliest sie. Häufig ist es ein "
                           "Tippfehler in einem echten Feldnamen."))

    mu = m.get("must_understand")
    if mu is not None:
        if not isinstance(mu, list) or not all(isinstance(x, str) for x in mu):
            out.append(finding(FEHLER, "`must_understand` muss eine Liste von "
                                       "Zeichenketten sein."))
        elif mu:
            out.append(finding(
                BEFUND, "Das Paket verlangt Fähigkeiten (`must_understand`): "
                + ", ".join(mu),
                "Ein Knoten, der eine davon nicht kennt, lehnt die App "
                "vollständig ab — statt sie halb verstanden zu installieren."))

    app = m.get("app")
    if isinstance(app, dict):
        for key in ("id", "name", "version", "type"):
            f = _need(app, key)
            if f:
                out.append(finding(FEHLER, f"`app.{key}` fehlt."))
        aid = app.get("id")
        if aid is not None and not ID_RE.match(str(aid)):
            out.append(finding(FEHLER, f"`app.id: {aid}` passt nicht zum "
                                       "erlaubten Muster (klein, Ziffern, "
                                       "Bindestriche, 3–40 Zeichen)."))
        ver = app.get("version")
        if ver is not None and not VERSION_RE.match(str(ver)):
            out.append(finding(FEHLER, f"`app.version: {ver}` ist keine "
                                       "Version der Art 1.2.3."))
        typ = app.get("type")
        if typ is not None and typ not in APP_TYPES:
            out.append(finding(FEHLER, f"`app.type: {typ}` ist unbekannt "
                                       f"(erlaubt: {', '.join(APP_TYPES)})."))
        cls = app.get("class")
        if cls is not None and cls not in APP_CLASSES:
            out.append(finding(FEHLER, f"`app.class: {cls}` ist unbekannt "
                                       f"(erlaubt: {', '.join(APP_CLASSES)})."))
        if cls is not None and str(m.get("oaap_manifest")) == "0.1":
            out.append(finding(BEFUND, "`app.class` gibt es erst ab "
                                       "Manifest-Format 0.2.",
                               "Entweder `oaap_manifest: \"0.2\"` setzen oder "
                               "das Feld weglassen."))
    elif app is not None:
        out.append(finding(FEHLER, "`app` ist kein Block mit Feldern."))

    services = m.get("services")
    if isinstance(services, dict):
        if not services:
            out.append(finding(FEHLER, "`services` ist leer — eine App "
                                       "braucht mindestens einen Dienst."))
        for sname, svc in services.items():
            if not NAME_RE.match(str(sname)):
                out.append(finding(FEHLER, f"Dienstname `{sname}` passt nicht "
                                           "zum erlaubten Muster."))
            if not isinstance(svc, dict):
                out.append(finding(FEHLER, f"Dienst `{sname}` ist kein Block."))
                continue
            has = [k for k in ("build", "image") if k in svc]
            if len(has) != 1:
                out.append(finding(
                    FEHLER, f"Dienst `{sname}` braucht genau eines von "
                            "`build` oder `image`"
                            + (f" (gefunden: {', '.join(has) or 'keines'})")))
            port = svc.get("port")
            if not isinstance(port, int) or not 1 <= port <= 65535:
                out.append(finding(FEHLER, f"Dienst `{sname}`: `port` fehlt "
                                           "oder ist keine Portnummer."))
    elif services is not None:
        out.append(finding(FEHLER, "`services` ist kein Block mit Diensten."))

    routes = m.get("routes")
    if isinstance(routes, list):
        if not routes:
            out.append(finding(FEHLER, "`routes` ist leer — ohne Route ist "
                                       "die App nicht erreichbar."))
        for idx, r in enumerate(routes, 1):
            if not isinstance(r, dict):
                out.append(finding(FEHLER, f"Route {idx} ist kein Block."))
                continue
            path = r.get("path")
            if not isinstance(path, str) or not path.startswith("/"):
                out.append(finding(FEHLER, f"Route {idx}: `path` fehlt oder "
                                           "beginnt nicht mit „/“."))
            roles = r.get("roles")
            if not isinstance(roles, list) or not roles:
                out.append(finding(FEHLER, f"Route {idx} ({path}): `roles` "
                                           "fehlt oder ist leer."))
            else:
                bad = [x for x in roles if x not in ROLES]
                if bad:
                    out.append(finding(
                        FEHLER, f"Route {idx} ({path}): unbekannte Rollen "
                                + ", ".join(map(str, bad)),
                        f"Erlaubt sind: {', '.join(ROLES)}."))
                if "public" in roles and len(roles) > 1:
                    out.append(finding(
                        BEFUND, f"Route {idx} ({path}) ist `public` und nennt "
                                "zusätzlich Rollen.",
                        "`public` schaltet die Anmeldung ab — die anderen "
                        "Rollen wirken dann nicht mehr."))
    elif routes is not None:
        out.append(finding(FEHLER, "`routes` ist keine Liste."))

    storage = m.get("storage")
    if isinstance(storage, list):
        for idx, s in enumerate(storage, 1):
            if not isinstance(s, dict):
                out.append(finding(FEHLER, f"Speicher {idx} ist kein Block."))
                continue
            if not NAME_RE.match(str(s.get("name", ""))):
                out.append(finding(FEHLER, f"Speicher {idx}: `name` fehlt "
                                           "oder passt nicht zum Muster."))
            mount = s.get("mount")
            if not isinstance(mount, str) or not mount.startswith("/"):
                out.append(finding(FEHLER, f"Speicher {idx}: `mount` fehlt "
                                           "oder ist kein absoluter Pfad."))
    elif storage is not None:
        out.append(finding(FEHLER, "`storage` ist keine Liste."))

    config = m.get("config")
    if isinstance(config, list):
        seen = set()
        for idx, c in enumerate(config, 1):
            if not isinstance(c, dict):
                out.append(finding(FEHLER, f"Konfiguration {idx} ist kein Block."))
                continue
            key = str(c.get("key", ""))
            if not CONFIG_KEY_RE.match(key):
                out.append(finding(FEHLER, f"Konfiguration {idx}: `key` fehlt "
                                           "oder passt nicht zum Muster."))
            elif key in seen:
                out.append(finding(FEHLER, f"Konfigurationsschlüssel `{key}` "
                                           "kommt doppelt vor."))
            seen.add(key)
            if not str(c.get("label", "")).strip():
                out.append(finding(FEHLER, f"Konfiguration `{key}`: `label` "
                                           "fehlt — er steht so im Portal."))
            if c.get("secret") and "default" in c:
                out.append(finding(
                    BEFUND, f"Konfiguration `{key}` ist ein Geheimnis und hat "
                            "einen Vorgabewert.",
                    "Ein vorgegebenes Geheimnis ist keines. Erwartet wird ein "
                    "leeres Feld, das der Betreiber füllt."))
    elif config is not None:
        out.append(finding(FEHLER, "`config` ist keine Liste."))

    health = m.get("health")
    if isinstance(health, dict):
        hp = health.get("path")
        if not isinstance(hp, str) or not hp.startswith("/"):
            out.append(finding(FEHLER, "`health.path` fehlt oder beginnt "
                                       "nicht mit „/“."))
        grace = health.get("startup_grace_seconds")
        if grace is not None and (not isinstance(grace, int)
                                  or not 0 <= grace <= 1800):
            out.append(finding(FEHLER, "`health.startup_grace_seconds` muss "
                                       "zwischen 0 und 1800 liegen."))
    elif health is not None:
        out.append(finding(FEHLER, "`health` ist kein Block."))

    eps = m.get("endpoints")
    if isinstance(eps, list):
        if len(eps) > 1:
            out.append(finding(FEHLER, "Mehr als ein `endpoints`-Eintrag — "
                                       "erlaubt ist höchstens einer "
                                       "(RFC-0015)."))
        for e in eps:
            if not isinstance(e, dict):
                out.append(finding(FEHLER, "`endpoints`-Eintrag ist kein Block."))
                continue
            for key in ("name", "protocol", "container_port", "reason"):
                if key not in e:
                    out.append(finding(FEHLER, f"Endpunkt: `{key}` fehlt."))
            if e.get("protocol") not in (None, "udp", "tcp", "both"):
                out.append(finding(FEHLER, f"Endpunkt `protocol: "
                                           f"{e.get('protocol')}` ist "
                                           "unbekannt."))
            cp = e.get("container_port")
            if cp is not None and (not isinstance(cp, int)
                                   or not 1 <= cp <= 65535):
                out.append(finding(FEHLER, "Endpunkt: `container_port` ist "
                                           "keine Portnummer."))
            if e.get("fixed"):
                if not isinstance(cp, int) or not 8200 <= cp <= 8299:
                    out.append(finding(
                        FEHLER, "Ein fester Endpunkt-Port muss zwischen 8200 "
                                "und 8299 liegen (RFC-0017 §5.1).",
                        "Bei `fixed: true` ist der Port Pflicht, nicht Wunsch "
                        "— die Freigabe scheitert laut, statt still einen "
                        "anderen zu vergeben."))
    elif eps is not None:
        out.append(finding(FEHLER, "`endpoints` ist keine Liste."))

    return out


# --------------------------------------------------------------- Zusammenfassung

def summary(m):
    """Was ein Mensch (und der Vergleich) vom Manifest braucht.

    Bewusst klein und ohne YAML-Reste: Diese Zusammenfassung wird als
    JSON beim Vorhaben abgelegt und ist die Grundlage des Vergleichs
    „was ändert dieses Paket gegenüber dem letzten?“.
    """
    app = m.get("app") if isinstance(m.get("app"), dict) else {}
    routes = m.get("routes") if isinstance(m.get("routes"), list) else []
    storage = m.get("storage") if isinstance(m.get("storage"), list) else []
    config = m.get("config") if isinstance(m.get("config"), list) else []
    eps = m.get("endpoints") if isinstance(m.get("endpoints"), list) else []
    services = m.get("services") if isinstance(m.get("services"), dict) else {}
    health = m.get("health") if isinstance(m.get("health"), dict) else {}

    return {
        "manifest_version": str(m.get("oaap_manifest", "")),
        "id": str(app.get("id", "")),
        "name": str(app.get("name", "")),
        "version": str(app.get("version", "")),
        "type": str(app.get("type", "")),
        "class": str(app.get("class", "") or "frontend"),
        "description": str(app.get("description", "") or ""),
        "must_understand": [str(x) for x in (m.get("must_understand") or [])
                            if isinstance(x, str)],
        "services": [
            {"name": str(n),
             "port": s.get("port") if isinstance(s, dict) else None,
             "from": ("build" if isinstance(s, dict) and "build" in s
                      else "image" if isinstance(s, dict) and "image" in s
                      else "")}
            for n, s in services.items()],
        "routes": [
            {"path": str(r.get("path", "")),
             "roles": [str(x) for x in (r.get("roles") or [])],
             "service": str(r.get("service", "") or "")}
            for r in routes if isinstance(r, dict)],
        "storage": [
            {"name": str(s.get("name", "")), "mount": str(s.get("mount", ""))}
            for s in storage if isinstance(s, dict)],
        "config": [
            {"key": str(c.get("key", "")), "label": str(c.get("label", "")),
             "secret": bool(c.get("secret")), "has_default": "default" in c}
            for c in config if isinstance(c, dict)],
        "endpoints": [
            {"name": str(e.get("name", "")), "protocol": str(e.get("protocol", "")),
             "container_port": e.get("container_port"),
             "fixed": bool(e.get("fixed"))}
            for e in eps if isinstance(e, dict)],
        "health": {"path": str(health.get("path", "")),
                   "grace": health.get("startup_grace_seconds")},
    }


def public_paths(s):
    return {r["path"] for r in s.get("routes", []) if "public" in r.get("roles", [])}


def storage_keys(s):
    return {(m["name"], m["mount"]) for m in s.get("storage", [])}


def endpoint_keys(s):
    return {(e["name"], e.get("protocol"), e.get("container_port"))
            for e in s.get("endpoints", [])}


def envelope_preview(previous, current):
    """Was der Knoten zu diesem Paket sagen wird (RFC-0019 §3) — Vorschau.

    `previous` ist die Zusammenfassung des zuletzt geprüften Pakets
    dieses Vorhabens (oder None). Das ist **nicht** dasselbe wie der
    installierte Stand — den kennt nur der Knoten, und er allein
    entscheidet. Deshalb heißt das hier Vorschau und nirgends Freigabe.

    Rückgabe: (hart, bestaetigung) — zwei Listen von Sätzen, in der
    Sprache der Oberfläche.
    """
    hart, bestaetigung = [], []
    if not previous:
        return hart, bestaetigung

    if previous.get("id") and current.get("id") != previous.get("id"):
        hart.append(
            f"Das Paket ist die App „{current.get('id')}“, zuletzt geprüft "
            f"war „{previous.get('id')}“ — eine Instanz gehört zu genau "
            "einer App.")
    if previous.get("version") and current.get("version") == previous.get("version"):
        hart.append(
            f"Die Version {current.get('version')} ist unverändert. Auf dem "
            "ZIP-Weg lehnt der Knoten das ab: Ohne Commit-Hash ist die "
            "Version die einzige Antwort auf „was läuft da?“.")

    neu_public = public_paths(current) - public_paths(previous)
    if neu_public:
        bestaetigung.append(
            "Ohne Anmeldung erreichbar werden: " + ", ".join(sorted(neu_public)))
    neu_ep = endpoint_keys(current) - endpoint_keys(previous)
    if neu_ep:
        bestaetigung.append(
            "Neue Endpunkte am Gateway vorbei: "
            + ", ".join(sorted(e[0] for e in neu_ep)))
    neu_st = storage_keys(current) - storage_keys(previous)
    if neu_st:
        bestaetigung.append(
            "Neue oder verschobene Speicher: "
            + ", ".join(sorted(f"{n} → {mt}" for n, mt in neu_st)))
    return hart, bestaetigung


def level_counts(findings):
    out = {FEHLER: 0, BEFUND: 0, HINWEIS: 0}
    for f in findings:
        out[f["level"]] = out.get(f["level"], 0) + 1
    return out


def inspect(path, max_bytes, previous=None):
    """Der ganze Weg: lesen, prüfen, zusammenfassen, vergleichen.

    Wirft `PackageError`, wenn das Paket nicht lesbar ist; sonst ein
    dict mit allem, was die Oberfläche und das Deployment brauchen.
    """
    p = read_package(path, max_bytes)
    m = parse_manifest(p["manifest_text"])
    findings = p.pop("findings") + validate(m)
    s = summary(m)
    hart, bestaetigung = envelope_preview(previous, s)
    p.update({
        "manifest": m,
        "summary": s,
        "findings": findings,
        "counts": level_counts(findings),
        "envelope_hard": hart,
        "envelope_confirm": bestaetigung,
        "deployable": not any(f["level"] == FEHLER for f in findings) and not hart,
    })
    return p
