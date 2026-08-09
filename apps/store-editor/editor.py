"""Die Bearbeitungsregeln des Store Editors (RFC-0013 Bauschritt 2).

Wie `checker.py` bewusst ohne Web, ohne Netz und ohne Dateizugriff: Was
hier steht, sind Entscheidungen aus RFC-0012 §1.2/§1.3 und RFC-0013,
und die soll man ohne laufenden Server lesen und prüfen können. Die
App reicht Manifest und Formularwerte herein und schreibt die
Arbeitskopie; hier wird nur gerechnet.

**Die drei Gruppen von Feldern**, und die dritte ist der Grund, warum
dieses Modul nicht einfach der 80-%-Regel folgt:

1. **Erzeugt** — `name`, `type`, `version`, `app_class`, `roles`. Genau
   diese fünf kann das Manifest heute belegen. Sie stehen schreib-
   geschützt da; wer sie trotzdem ändern will, entriegelt sie, und die
   Übersteuerung wird **markiert** (RFC-0012 §1.3: die nächste
   Neuerzeugung darf eine bewusste Änderung nicht stillschweigend
   zurücknehmen).
2. **Redaktionell** — die Felder, über die ein Mensch nachdenken muss.
3. **Von Hand, obwohl §1.3 sie „erzeugt" nennt** — `released`,
   `profiles`, `icon` und `package`. Das Manifest kann sie nicht
   liefern (siehe README, „Was §1.3 zu viel verspricht"). Sie hier als
   erzeugt und verriegelt darzustellen, wäre eine Unwahrheit in der
   Oberfläche: Es gäbe nichts, woraus sie je erzeugt würden. Also sind
   sie frei bearbeitbar, und die Seite sagt warum.

**Wo die Markierung liegt: in der Arbeitskopie des Editors, nicht in
der Liste.** Eine Liste ist ein Dokument nach `oaap-store.schema.json`;
die Buchführung des Editors gehört nicht hinein und nicht auf fremde
Knoten. Dieselbe Begründung wie bei der Betriebsart in RFC-0013 §3
(„stored with the list configuration in the editor, not in the list
file"). Der Preis ist ehrlich zu nennen: Wer dieselbe Liste in einem
anderen Editor öffnet, sieht die Markierungen nicht.
"""

import re

import checker as ck

# --- Feldgruppen ------------------------------------------------------

# Die fünf, die das Manifest wirklich belegt (checker.derive).
REGENERABLE = ("name", "type", "version", "app_class", "roles")

# Redaktionell: RFC-0012 §1.3 nennt sechs, das Format hat ein paar mehr.
# `description` steht in §1.3 unter „erzeugt" — als *Saatgut*: Das
# Manifest liefert einen kurzen Satz, die Liste trägt den langen Text.
# Deshalb wird sie nicht verglichen (checker.COMPARABLE) und ist hier
# redaktionell. `tags` und `license` ordnet §1.3 gar nicht ein.
EDITORIAL = ("summary", "description", "categories", "audience", "tags",
             "maturity", "status", "license", "links", "screenshots")

# Was §1.3 „erzeugt" nennt, ohne dass es eine Quelle dafür gäbe.
UNGENERATED = ("released", "profiles", "icon", "package")

LIST_FIELDS = {"categories", "audience", "tags", "roles", "profiles",
               "links", "screenshots"}

# Änderungsarten. Der Unterschied ist nicht Kosmetik: RFC-0013
# Entscheidung 5 zählt für die Mengenbremse **nur** redaktionelle und
# strukturelle Änderungen — was der Prüfer selbst aus den Manifesten
# gebildet hat, zählt nicht mit. Bauschritt 2 schreibt noch nichts,
# aber die Einteilung entsteht hier, damit die Bremse sie später
# vorfindet statt sie nachträglich erfinden zu müssen.
STRUKTUR = "struktur"
ERZEUGT = "erzeugt"
REDAKTIONELL = "redaktionell"


# --- Vokabular mit deutschen Namen ------------------------------------
# Die Werte stehen in checker.py (dort sind sie Prüfregel); hier steht,
# wie sie heißen. Ein Wert ohne Namen wird trotzdem angezeigt — die
# Liste darf unbekanntes Vokabular führen (RFC-0012 §8.1).

CATEGORY_LABEL = {
    "business": "Geschäftliches", "productivity": "Produktivität",
    "documents": "Dokumente", "communication": "Kommunikation",
    "media": "Medien", "monitoring": "Überwachung", "iot": "Geräte / IoT",
    "automation": "Automatisierung", "ai": "KI",
    "development": "Entwicklung", "security": "Sicherheit",
    "storage-backup": "Speicher & Sicherung", "infrastructure": "Infrastruktur",
}
AUDIENCE_LABEL = {
    "everyone": "alle", "operator": "Betreiber",
    "developer": "Entwickler", "expert": "Fachleute",
}
MATURITY_LABEL = {
    "alpha": "alpha — früh, kann sich noch stark ändern",
    "beta": "beta — benutzbar, aber noch in Bewegung",
    "preview": "preview — Vorschau auf das Kommende",
    "stable": "stable — fertig und gepflegt",
}
STATUS_LABEL = {
    "active": "aktiv — wird gepflegt",
    "deprecated": "abgelöst — es gibt etwas Besseres",
    "archived": "eingemottet — keine Pflege mehr",
}
FIELD_LABEL = {
    "name": "Name", "type": "Verpackungsart", "version": "Version",
    "app_class": "Art der App", "roles": "Wer darf sie benutzen",
    "released": "Freigegeben am", "profiles": "Gedacht für Knotenprofile",
    "icon": "Bild", "package": "Paket", "summary": "Ein Satz dazu",
    "description": "Beschreibung", "categories": "Kategorien",
    "audience": "Für wen", "tags": "Schlagwörter", "maturity": "Reifegrad",
    "status": "Stand", "license": "Lizenz", "links": "Verweise",
    "screenshots": "Bildschirmfotos", "id": "Kennung",
}

# Verweise: die geläufigen Beziehungen bekommen ein eigenes Feld mit
# einem Namen, den man versteht. Alles andere bleibt erhalten und
# wandert in „Weitere Verweise" — verlieren darf der Editor nichts.
KNOWN_RELS = (("homepage", "Startseite"), ("docs", "Dokumentation"),
              ("source", "Quellcode"), ("demo", "Zum Ausprobieren"),
              ("changelog", "Was sich geändert hat"))


# --- Werte lesbar machen ----------------------------------------------

def as_text(value):
    """Ein Feldwert als eine Zeile Klartext — für Übersichten."""
    if value is None or value == "":
        return ""
    if isinstance(value, list):
        if value and isinstance(value[0], dict):
            return "; ".join(
                " ".join(str(v) for v in item.values() if v) for item in value)
        return ", ".join(str(v) for v in value)
    if isinstance(value, dict):
        return " ".join(f"{k}={v}" for k, v in value.items() if v)
    return str(value)


# --- Zeilenweise Felder ------------------------------------------------

def parse_pairs(text, keys):
    """„a | b | c" je Zeile zu Objekten. Leere Zeilen fallen weg."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")]
        item = {}
        for i, key in enumerate(keys):
            if i < len(parts) and parts[i]:
                item[key] = parts[i]
        if item:
            out.append(item)
    return out


def format_pairs(items, keys):
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        vals = [str(item.get(k) or "") for k in keys]
        while vals and not vals[-1]:
            vals.pop()
        if vals:
            out.append(" | ".join(vals))
    return "\n".join(out)


def parse_words(text):
    """Komma- oder zeilengetrennt, ohne Doppelte, in der Reihenfolge."""
    raw = re.split(r"[,\n]", text or "")
    out = []
    for w in raw:
        w = w.strip()
        if w and w not in out:
            out.append(w)
    return out


def split_links(links):
    """Bekannte Beziehungen heraus, der Rest als Text — nichts geht verloren."""
    known = {rel: {"url": "", "label": ""} for rel, _ in KNOWN_RELS}
    rest = []
    for link in links or []:
        if not isinstance(link, dict):
            continue
        rel = str(link.get("rel") or "")
        if rel in known and not known[rel]["url"]:
            known[rel] = {"url": str(link.get("url") or ""),
                          "label": str(link.get("label") or "")}
        else:
            rest.append(link)
    return known, format_pairs(rest, ("rel", "url", "label"))


def merge_links(known_urls, rest_text):
    """Bekannte Beziehungen zuerst, danach die übrigen — Reihenfolge stabil."""
    out = []
    for rel, _ in KNOWN_RELS:
        url = str(known_urls.get(rel) or "").strip()
        if url:
            out.append({"rel": rel, "url": url})
    out.extend(parse_pairs(rest_text, ("rel", "url", "label")))
    return out


# --- Einträge ----------------------------------------------------------

def entry_index(doc, app_id):
    for i, e in enumerate((doc or {}).get("apps") or []):
        if isinstance(e, dict) and str(e.get("id") or "") == app_id:
            return i
    return -1


def entry_by_id(doc, app_id):
    i = entry_index(doc, app_id)
    return ((doc or {}).get("apps") or [])[i] if i >= 0 else None


def new_entry(app_id, git, path="", ref=""):
    """Ein Eintrag, bevor sein Manifest abrufbar ist (RFC-0013 Entscheidung 4).

    Bewusst nur die Kennung und der Zeiger auf das Paket: Alles andere
    holt die Neuerzeugung aus dem Manifest, sobald es erreichbar ist.
    Der Prüfer meldet solange bei jedem Lauf, dass dieser Eintrag ohne
    Beleg dasteht — genau so ist es entschieden.
    """
    pkg = {"git": git.strip()}
    if path.strip():
        pkg["path"] = path.strip().strip("/")
    if ref.strip():
        pkg["ref"] = ref.strip()
    return {"id": app_id, "name": app_id, "version": "0.0.0", "package": pkg}


def check_new_id(doc, app_id):
    """'' wenn die Kennung taugt, sonst der Grund im Klartext."""
    if not ck.APP_ID.fullmatch(app_id or ""):
        return ("Die Kennung braucht Kleinbuchstaben, Ziffern oder "
                "Bindestriche und 3 bis 40 Zeichen.")
    if entry_index(doc, app_id) >= 0:
        return "Diese Kennung steht schon in der Liste."
    return ""


def apply_values(entry, values):
    """Formularwerte übernehmen. Leer heißt: Feld entfernen.

    Ein leeres Feld als `""` stehen zu lassen wäre etwas anderes als es
    wegzulassen — die Liste behauptete dann einen leeren Namen statt
    keinen. Alle diese Felder sind im Schema optional.
    """
    changed = []
    for field, new in values.items():
        old = entry.get(field)
        empty = new in ("", [], {}, None)
        if empty and field not in entry:
            continue
        if not empty and old == new:
            continue
        changed.append({"field": field, "before": as_text(old), "after": as_text(new)})
        if empty:
            entry.pop(field, None)
        else:
            entry[field] = new
    return changed


# --- Neuerzeugung aus dem Manifest -------------------------------------

def regenerate_entry(entry, manifest, overridden=()):
    """Was das Manifest belegt, in den Eintrag — außer bei Übersteuerung.

    Das ist die 80-%-Regel in Code, und die markierte Übersteuerung ist
    die Bedingung, unter der sie überhaupt vertretbar ist: Ohne sie
    nähme jede Neuerzeugung eine bewusste redaktionelle Entscheidung
    stillschweigend zurück (RFC-0012 §1.3).
    """
    derived = ck.derive(manifest)
    changes = []
    for field in REGENERABLE:
        want = derived.get(field)
        if not want:
            continue
        if field == "roles":
            have = sorted({str(r) for r in (entry.get("roles") or [])})
            if have == sorted(want):
                continue
            new = sorted(want)
        else:
            have = str(entry.get(field) or "")
            if have == want:
                continue
            new = want
        held = field in overridden
        changes.append({"field": field, "before": as_text(entry.get(field)),
                        "after": as_text(new), "held": held})
        if not held:
            entry[field] = new
    return changes


# --- Unterschied zur veröffentlichten Liste ----------------------------

def diff_documents(published, working):
    """Was sich gegenüber dem veröffentlichten Stand ändert.

    Getrennt nach Art, weil die Mengenbremse in Bauschritt 3 genau
    diese Trennung braucht (RFC-0013 Entscheidung 5): Neuerzeugung
    zählt nicht mit, redaktionelle und strukturelle Änderungen schon.
    """
    old = {str(e.get("id") or ""): e
           for e in (published or {}).get("apps") or [] if isinstance(e, dict)}
    new = {str(e.get("id") or ""): e
           for e in (working or {}).get("apps") or [] if isinstance(e, dict)}
    out = []
    for app_id in new:
        if app_id not in old:
            out.append({"app": app_id, "field": "—", "kind": STRUKTUR,
                        "before": "", "after": "neu aufgenommen"})
    for app_id in old:
        if app_id not in new:
            out.append({"app": app_id, "field": "—", "kind": STRUKTUR,
                        "before": "war in der Liste", "after": "entfernt"})
    for app_id, entry in new.items():
        if app_id not in old:
            continue
        before = old[app_id]
        for field in sorted(set(before) | set(entry)):
            if field == "id":
                continue
            a, b = before.get(field), entry.get(field)
            if a == b:
                continue
            # Ein Paket, das umzieht, ist strukturell und nicht
            # redaktionell: Die Kennung bleibt, der Zeiger wandert —
            # die eine Form, die ein Versehen und eine Übernahme
            # gemeinsam haben (RFC-0013 Entscheidung 5).
            if field == "package":
                kind = STRUKTUR
            elif field in REGENERABLE:
                kind = ERZEUGT
            else:
                kind = REDAKTIONELL
            out.append({"app": app_id, "field": field, "kind": kind,
                        "before": as_text(a), "after": as_text(b)})
    order = {STRUKTUR: 0, REDAKTIONELL: 1, ERZEUGT: 2}
    out.sort(key=lambda c: (order.get(c["kind"], 9), c["app"], c["field"]))
    return out


def count_kinds(changes):
    return {k: sum(1 for c in changes if c["kind"] == k)
            for k in (STRUKTUR, REDAKTIONELL, ERZEUGT)}
