"""Die Prüfregeln des Store Editors (RFC-0013 Bauschritt 1).

Bewusst ohne Web, ohne Netz und ohne Zustand: Was hier steht, sind
Entscheidungen aus RFC-0012 und RFC-0013, und die soll man ohne
laufenden Server lesen und prüfen können. Das Abrufen wird
hereingereicht (`fetch`), damit die Prüfungen offline laufen.

Warum es diesen Prüfer überhaupt gibt: Eine Store-Liste ist kein
Dokument, sondern eine Anweisung, die auf fremden Rechnern Software
installiert. Ollama stand am 09.08.2026 in unserer Liste als
Hintergrunddienst, sein Manifest sagte davon nichts — der Widerspruch
war wochenlang unsichtbar. Kein Mensch findet so etwas beim Lesen von
JSON.

**Die Strenge liegt im Schema** (`oaap-spec/schema/oaap-store.schema.json`),
das für Autorenwerkzeuge und CI die Autorität bleibt. Hier steht eine
Nachbildung seiner Regeln in Code — dasselbe Verfahren, das
`appctl.validate_manifest` für das Manifest-Schema anwendet, und aus
demselben Grund: keine Abhängigkeit, die auf einem Zielknoten aufgelöst
werden müsste.
"""

import re
from urllib.parse import urlparse

# --- Vokabular aus RFC-0012 §1.2. Unbekannte Werte sind KEIN Fehler:
# Ein Knoten toleriert sie (§8.1), also darf der Editor nicht so tun,
# als wäre die Liste kaputt. Sie werden gemeldet, nicht abgelehnt.
CATEGORIES = {"business", "productivity", "documents", "communication",
              "media", "monitoring", "iot", "automation", "ai",
              "development", "security", "storage-backup", "infrastructure"}
APP_CLASSES = {"frontend", "service"}
AUDIENCES = {"everyone", "operator", "developer", "expert"}
MATURITIES = {"alpha", "beta", "preview", "stable"}
STATUSES = {"active", "deprecated", "archived"}
ROLES = {"admin", "keyuser", "user", "guest", "partner", "public"}

SEMVER = re.compile(r"^\d+\.\d+\.\d+([-+][0-9A-Za-z.-]+)?$")
APP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Drei Schweregrade, und der Unterschied ist der Kern des Werkzeugs.
FEHLER = "fehler"      # die Liste ist so nicht benutzbar
BEFUND = "befund"      # Liste und Manifest widersprechen sich
HINWEIS = "hinweis"    # auffällig, aber vielleicht Absicht
LEVELS = (FEHLER, BEFUND, HINWEIS)


def finding(level, app_id, field, text, in_list="", in_manifest=""):
    return {"level": level, "app": app_id, "field": field, "text": text,
            "list": str(in_list), "manifest": str(in_manifest)}


# --------------------------------------------------------------- Manifest holen

def raw_manifest_url(package):
    """Wo liegt `oaap-app.yaml` zu diesem Paket? ('' + Grund, wenn unklar)

    Absichtlich kein `git clone`: Der Prüfer soll eine Liste mit acht
    Einträgen in Sekunden durchsehen können, nicht acht Repositories
    herunterladen. Bekannt sind die zwei Formen, die uns betreffen —
    GitHub und Forgejo/Gitea (also auch unsere eigene Forgejo-App).
    Alles andere wird gemeldet, nicht geraten: Eine falsch geratene
    Adresse liefert entweder 404 (harmlos) oder die falsche Datei
    (nicht harmlos).
    """
    git = str((package or {}).get("git") or "").strip()
    if not git:
        return "", "kein Git-Repository angegeben"
    ref = str((package or {}).get("ref") or "").strip() or "main"
    path = str((package or {}).get("path") or "").strip().strip("/")
    inner = (path + "/" if path else "") + "oaap-app.yaml"
    u = urlparse(git.rstrip("/").removesuffix(".git"))
    if not u.scheme.startswith("http"):
        return "", f"mit dieser Adressform kann der Prüfer nichts anfangen: {git}"
    parts = [p for p in u.path.split("/") if p]
    if len(parts) < 2:
        return "", f"aus der Adresse lässt sich kein Repository lesen: {git}"
    owner, repo = parts[0], parts[1]
    if u.netloc.endswith("github.com"):
        return (f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{inner}", "")
    # Forgejo/Gitea liefern Rohdateien unter /raw/branch/<ref>/… — die
    # Form gilt auch für einen Tag, der Pfadname heißt nur so.
    return (f"https://{u.netloc}/{owner}/{repo}/raw/branch/{ref}/{inner}", "")


# ------------------------------------------------------- ableitbare Felder

def derive(manifest):
    """Was ein Listeneintrag aus dem Manifest übernehmen KANN.

    Kleiner als RFC-0012 §1.3 verspricht, und das ist ein Befund am
    Papier, kein Versäumnis hier (siehe README, „Was §1.3 zu viel
    verspricht"): `profiles` kennt das Manifest-Schema überhaupt nicht,
    `released` steht nirgends, `icon` führt heute kein einziges unserer
    Manifeste, und `description` ist in der Liste absichtlich der
    längere, redaktionelle Text. Verglichen wird deshalb nur, was
    wirklich beidseitig existiert.
    """
    app = (manifest or {}).get("app") or {}
    roles = set()
    for rt in (manifest or {}).get("routes") or []:
        roles |= {str(r) for r in (rt.get("roles") or [])}
    return {
        "id": str(app.get("id") or ""),
        "name": str(app.get("name") or ""),
        "type": str(app.get("type") or ""),
        "version": str(app.get("version") or ""),
        "app_class": str(app.get("class") or ""),
        "roles": sorted(roles),
    }


# Felder, die verglichen werden, mit ihrer Erklärung im Klartext.
COMPARABLE = {
    "name": "Der Name der App",
    "type": "Die Verpackungsart",
    "version": "Die Version",
    "app_class": "Die Art der App (Oberfläche oder Hintergrunddienst)",
    "roles": "Wer die App benutzen darf",
}


def compare_entry(entry, manifest):
    """Liste gegen Manifest — drei Ausgänge je Feld, nicht zwei.

    Der dritte Ausgang ist der, den echte Daten erzwungen haben: Heute
    tragen sieben von acht unserer Manifeste noch keine Klasse, während
    alle acht Listeneinträge eine behaupten. Das ist **kein**
    Widerspruch, sondern eine Behauptung ohne Deckung — ein Hinweis,
    kein Befund. Ein Befund ist nur, wenn beide etwas sagen und es
    verschieden ist.
    """
    out = []
    d = derive(manifest)
    app_id = str(entry.get("id") or "?")

    if d["id"] and d["id"] != app_id:
        out.append(finding(
            FEHLER, app_id, "id",
            "Die Kennung im Manifest ist eine andere — das Paket gehört "
            "nicht zu diesem Eintrag.", app_id, d["id"]))

    for field, label in COMPARABLE.items():
        want, have = d.get(field), entry.get(field)
        if field == "roles":
            # Reihenfolge ist bedeutungslos; verglichen werden Mengen.
            # (Beim Ableiten der Regeln an den echten Listen war genau
            # das die erste falsche Meldung.)
            want_s = set(want or [])
            have_s = {str(r) for r in (have or [])}
            if not want_s:
                continue
            if not have_s:
                out.append(finding(HINWEIS, app_id, field,
                                   f"{label} steht nicht in der Liste, "
                                   "das Manifest sagt es aber.",
                                   "—", ", ".join(sorted(want_s))))
            elif want_s != have_s:
                out.append(finding(BEFUND, app_id, field,
                                   f"{label} weicht ab.",
                                   ", ".join(sorted(have_s)),
                                   ", ".join(sorted(want_s))))
            continue
        have = str(have or "")
        if not want:
            if have:
                out.append(finding(
                    HINWEIS, app_id, field,
                    f"{label} steht in der Liste, das Manifest sagt dazu "
                    "nichts — die Liste behauptet also mehr, als das Paket "
                    "belegt.", have, "—"))
            continue
        if not have:
            out.append(finding(HINWEIS, app_id, field,
                               f"{label} fehlt in der Liste.", "—", want))
        elif have != want:
            out.append(finding(BEFUND, app_id, field,
                               f"{label} weicht ab.", have, want))
    return out


# ------------------------------------------------------------ Struktur prüfen

def check_structure(doc):
    """Nachbildung der Regeln aus `oaap-store.schema.json`."""
    out = []
    fmt = str((doc or {}).get("store") or (doc or {}).get("format") or "")
    if not re.fullmatch(r"\d+\.\d+", fmt):
        out.append(finding(FEHLER, "", "store",
                           'Die Liste nennt kein Format ("store": "0.2").',
                           fmt or "—", ""))
    elif fmt.split(".")[0] != "0":
        out.append(finding(FEHLER, "", "store",
                           "Diese Liste ist eine Hauptversion, die der "
                           "Editor nicht liest.", fmt, "0.x"))
    apps = (doc or {}).get("apps")
    if not isinstance(apps, list):
        out.append(finding(FEHLER, "", "apps",
                           "Die Liste enthält keine Sammlung 'apps'."))
        return out

    seen = {}
    for e in apps:
        if not isinstance(e, dict):
            out.append(finding(FEHLER, "", "apps", "Ein Eintrag ist kein Objekt."))
            continue
        app_id = str(e.get("id") or "")
        if not APP_ID.fullmatch(app_id):
            out.append(finding(FEHLER, app_id or "?", "id",
                               "Kennung fehlt oder ist unzulässig "
                               "(Kleinbuchstaben, Ziffern, Bindestrich, 3–40).",
                               app_id or "—", ""))
        if app_id in seen:
            out.append(finding(FEHLER, app_id, "id",
                               "Diese Kennung kommt in der Liste mehrfach vor "
                               "— welcher Eintrag gilt, wäre Zufall."))
        seen[app_id] = True
        if not str(e.get("name") or "").strip():
            out.append(finding(FEHLER, app_id, "name", "Der Name fehlt."))
        ver = str(e.get("version") or "")
        if not SEMVER.fullmatch(ver):
            out.append(finding(FEHLER, app_id, "version",
                               "Keine gültige Version (z. B. 1.2.3).", ver or "—", ""))
        rel = str(e.get("released") or "")
        if rel and not ISO_DATE.fullmatch(rel):
            out.append(finding(FEHLER, app_id, "released",
                               "Kein Datum in der Form JJJJ-MM-TT.", rel, ""))
        pkg = e.get("package") or {}
        git = str(pkg.get("git") or "")
        if not git:
            out.append(finding(FEHLER, app_id, "package",
                               "Kein Paket angegeben — der Eintrag ist nicht "
                               "installierbar."))
        elif not git.startswith("https://"):
            out.append(finding(FEHLER, app_id, "package",
                               "Das Paket muss über https:// erreichbar sein.",
                               git, ""))
        path = str(pkg.get("path") or "")
        if ".." in path.split("/"):
            out.append(finding(FEHLER, app_id, "package",
                               "Der Pfad im Repository darf nicht aus ihm "
                               "herausführen.", path, ""))
        # Bilder: nur relativ zum Repository der Liste (RFC-0012 §6) —
        # sonst ruft jeder Knoten, der die Store-Seite öffnet, einen
        # Server auf, den niemand ausgewählt hat.
        imgs = [("icon", e.get("icon") or "")]
        imgs += [("screenshots", (s or {}).get("src") or "")
                 for s in (e.get("screenshots") or []) if isinstance(s, dict)]
        for field, src in imgs:
            src = str(src)
            if not src:
                continue
            if "://" in src or src.startswith("//"):
                out.append(finding(FEHLER, app_id, field,
                                   "Bilder dürfen nur aus dem Repository der "
                                   "Liste kommen, nicht von einem fremden "
                                   "Server.", src, ""))
            elif ".." in src.split("/") or src.startswith("/"):
                out.append(finding(FEHLER, app_id, field,
                                   "Der Bildpfad führt aus dem Repository "
                                   "heraus.", src, ""))
        for field, vocab in (("app_class", APP_CLASSES), ("maturity", MATURITIES),
                             ("status", STATUSES)):
            v = str(e.get(field) or "")
            if v and v not in vocab:
                out.append(finding(HINWEIS, app_id, field,
                                   "Wert außerhalb des bekannten Vokabulars — "
                                   "ein Knoten toleriert ihn, deshalb nur ein "
                                   "Hinweis.", v, " | ".join(sorted(vocab))))
        for field, vocab in (("categories", CATEGORIES), ("audience", AUDIENCES),
                             ("roles", ROLES)):
            for v in (e.get(field) or []):
                if str(v) not in vocab:
                    out.append(finding(HINWEIS, app_id, field,
                                       "Wert außerhalb des bekannten "
                                       "Vokabulars.", str(v),
                                       " | ".join(sorted(vocab))))
    return out


# ------------------------------------------------------------- ganze Prüfung

def check_document(doc, fetch=None, load_yaml=None):
    """Struktur, dann jeder Eintrag gegen sein Manifest.

    `fetch(url) -> str` und `load_yaml(text) -> dict` werden
    hereingereicht: So läuft die Prüfung im Test ohne Netz, und die App
    entscheidet, wie sie abruft.
    """
    findings = check_structure(doc)
    checked = unreachable = 0
    apps = (doc or {}).get("apps")
    if isinstance(apps, list) and fetch:
        for e in apps:
            if not isinstance(e, dict):
                continue
            app_id = str(e.get("id") or "?")
            url, why = raw_manifest_url(e.get("package") or {})
            if not url:
                unreachable += 1
                findings.append(finding(HINWEIS, app_id, "package",
                                        f"Das Manifest ist nicht abrufbar: {why} "
                                        "— dieser Eintrag bleibt ungeprüft."))
                continue
            try:
                text = fetch(url)
            except Exception as exc:                       # noqa: BLE001
                unreachable += 1
                findings.append(finding(
                    HINWEIS, app_id, "package",
                    f"Das Manifest war nicht abrufbar ({type(exc).__name__}) "
                    "— dieser Eintrag bleibt ungeprüft. Solange das so "
                    "bleibt, steht die Behauptung dieses Eintrags ohne "
                    "Beleg da.", url, ""))
                continue
            try:
                manifest = (load_yaml or (lambda t: None))(text)
            except Exception as exc:                       # noqa: BLE001
                unreachable += 1
                findings.append(finding(FEHLER, app_id, "package",
                                        f"Das Manifest ist nicht lesbar: {exc}",
                                        url, ""))
                continue
            if not isinstance(manifest, dict):
                unreachable += 1
                findings.append(finding(FEHLER, app_id, "package",
                                        "Unter dieser Adresse liegt kein "
                                        "Manifest.", url, ""))
                continue
            checked += 1
            findings.extend(compare_entry(e, manifest))
    counts = {lvl: sum(1 for f in findings if f["level"] == lvl) for lvl in LEVELS}
    return {
        "findings": findings,
        "counts": counts,
        "entries": len(apps) if isinstance(apps, list) else 0,
        "checked": checked,
        "unreachable": unreachable,
        "ok": counts[FEHLER] == 0 and counts[BEFUND] == 0,
    }
