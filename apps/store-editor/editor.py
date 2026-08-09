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

# Was §1.3 „erzeugt" nennt, ohne dass eine Neuerzeugung es liefern
# könnte. Bei dreien fehlt dem Manifest-Schema das Feld; bei `icon`
# liegt es anders — siehe ICON_HINDERNIS.
UNGENERATED = ("released", "profiles", "icon", "package")

ICON_HINDERNIS = (
    "Das Manifest kennt `app.icon` sehr wohl. Das Hindernis sind die "
    "Bezugspunkte: In der Liste gilt ein Bildpfad relativ zur Liste "
    "(RFC-0012 §1.1, damit kein Knoten beim Öffnen der Store-Seite einen "
    "fremden Server anruft), im Manifest relativ zum Paket. Eine "
    "Neuerzeugung müsste die Datei also aus dem App-Repository in das "
    "Listen-Repository kopieren — das kann erst ein Bauschritt, der "
    "schreibt.")

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


# --- Quellen: welche Listen dieser Editor pflegt -----------------------
#
# Die Liste der Listen liegt in der Ablage des Editors, die
# **Zugangsschlüssel** dagegen in der Instanz-Konfiguration (RFC-0013,
# Entscheidung vom 09.08.2026, Form A). Eine Quelle nennt deshalb nur
# die **Nummer eines Platzes**, nie einen Schlüssel — der Editor
# bekommt den Wert vom Betriebssystem und schreibt ihn nirgends hin.
#
# Warum überhaupt Plätze statt beliebig vieler Schlüssel: Die Schlüssel
# sind im Manifest deklariert und damit fest. Das ist eine echte
# Obergrenze — sie ist hier **sichtbar** statt versteckt, und genau das
# war die Begründung: Erscheint eine vierte private Liste, ist das der
# Beleg für eine allgemeine Lösung, statt sie vorher zu erraten.

TOKEN_SLOTS = 3


def normalise_source(url):
    """Was ein Mensch einfügt, in die Adresse, die abgerufen wird.

    Ein Browser zeigt eine Datei unter `…/blob/main/…`; das ist eine
    HTML-Seite, keine JSON-Datei. Wer sie aus der Adresszeile kopiert,
    bekäme sonst 'das ist keine gültige JSON-Datei' und keinen Hinweis
    darauf, was er falsch gemacht hat.
    """
    url = str(url or "").strip()
    m = re.match(r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$", url)
    if m:
        owner, repo, ref, path = m.groups()
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
    m = re.match(r"^https://([^/]+)/([^/]+)/([^/]+)/src/branch/([^/]+)/(.+)$", url)
    if m:
        host, owner, repo, ref, path = m.groups()
        return f"https://{host}/{owner}/{repo}/raw/branch/{ref}/{path}"
    return url


def check_new_source(sources, url):
    """'' wenn die Adresse taugt, sonst der Grund im Klartext."""
    if not url.startswith("https://"):
        return "Die Adresse muss mit https:// beginnen."
    if any(s.get("url") == url for s in sources or []):
        return "Diese Liste ist schon eingetragen."
    return ""


def source_forge(url):
    """Der Anbieter, für den ein Zugangsschlüssel dieser Quelle gilt."""
    return ck.forge_of(url)


# --- Nachpflege-Bericht ------------------------------------------------
#
# Ein Auftrag an die KI, die eine App betreut: „Dein Manifest schweigt
# zu Dingen, die der Katalog über Dich behauptet — trag sie nach."
#
# Warum das die richtige Richtung ist: Das Manifest gehört dem, der die
# App gebaut hat; die Liste ist nur der Katalog. Wo beide etwas sagen,
# gewinnt das Manifest. Ein Katalog, der Behauptungen ohne Deckung
# führt, ist genau der Zustand, gegen den dieses Werkzeug antritt.

# Was das Manifest im Format 0.2 heute tragen kann:
# (Schlüssel unter `app:`, Feld im Katalog als Quelle, Hinweis).
# Die Liste ist kurz, und genau das ist der Befund.
MANIFEST_CARRIES = (
    ("name", "name", ""),
    ("version", "version", ""),
    ("type", "type", ""),
    ("class", "app_class", ""),
    # Der EINE Satz, nicht der lange Text: Das Manifest trägt die kurze
    # Fassung (sie steht später an der Instanz), der Katalog den langen
    # redaktionellen Text. Die Quelle ist deshalb `summary`.
    ("description", "summary", "Die kurze Fassung — der lange Text bleibt "
                               "im Katalog."),
    # Ohne Wert: Im Katalog gilt ein Bildpfad relativ zur LISTE, im
    # Manifest relativ zum PAKET. Den Pfad kann nur die App nennen.
    ("icon", "", "Pfad relativ zum Paket."),
)

# Was der Katalog führt und das Manifest-Schema (noch) nicht kennt.
# Ob diese Felder ins Manifest wandern sollen, ist eine offene
# Entscheidung — sie steht in RFC-0014.
#
# `description` steht hier, obwohl das Manifest ein Feld dieses Namens
# hat: Das ist der EINE Satz an der Instanz, im Katalog steht der lange
# Text. Für den gibt es im Manifest keinen Platz — und ohne diese Zeile
# fiele ausgerechnet das längste Stück Text aus dem Bericht heraus.
MANIFEST_MISSING = ("summary", "description", "categories", "audience", "tags",
                    "maturity", "status", "license", "links", "screenshots",
                    "profiles", "released")


def yaml_value(value):
    """Ein Wert als YAML — konservativ in Anführungszeichen."""
    if isinstance(value, list):
        return "[" + ", ".join(yaml_value(v) for v in value) + "]"
    text = str(value)
    if (not text or text.strip() != text
            or text[0] in "&*!|>%@`-?:{}[]#,\"'"
            or any(c in text for c in ':#\n"')):
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"'
                                                        ).replace("\n", " ") + '"'
    return text


def _rows(pairs):
    if not pairs:
        return ""
    out = ["| Feld | Was der Katalog sagt |", "| --- | --- |"]
    out += [f"| `{f}` | {v} |" for f, v in pairs]
    return "\n".join(out) + "\n"


def pflegebericht(entry, manifest, manifest_url="", why="", list_name="",
                  marks=(), erzeugt_am=""):
    """Ein Bericht in Markdown: Was dem Manifest dieser App fehlt.

    Gedacht zum Weiterreichen an die KI, die die App betreut. Deshalb
    steht der Auftrag oben, die Begründung darunter und ein
    einsetzbarer YAML-Block dabei — nicht nur eine Mängelliste.
    """
    app_id = str(entry.get("id") or "?")
    name = str(entry.get("name") or app_id)
    fmt = str((manifest or {}).get("oaap_manifest") or "")
    app_block = (manifest or {}).get("app") or {}

    # Nachzupflegen ist nur, wozu das Manifest **schweigt**. Sagen
    # beide etwas und es ist verschieden, dann ist der Katalog schuld
    # und nicht die App — das meldet der Prüfer, und geflickt wird es
    # hier, nicht dort. Ein Bericht, der einer fremden KI aufträgt,
    # eine veraltete Version aus unserem Katalog zu übernehmen, wäre
    # schlimmer als gar keiner.
    tragbar, abweichend = [], []
    for key, quelle, hinweis in MANIFEST_CARRIES:
        katalog = entry.get(quelle) if quelle else entry.get("icon")
        if katalog in (None, "", [], {}):
            continue
        says = app_block.get(key)
        if quelle and quelle in marks:
            abweichend.append((quelle, as_text(katalog), as_text(says)))
        elif not says:
            tragbar.append({"key": key, "quelle": quelle, "hinweis": hinweis,
                            "wert": entry.get(quelle) if quelle else None})

    # Was Abschnitt 1 schon verbraucht hat, darf in Abschnitt 2 nicht
    # noch einmal auftauchen — `summary` wandert als `app.description`
    # ins Manifest und fehlt dort dann nicht mehr.
    verbraucht = {t["quelle"] for t in tragbar if t["quelle"]}
    verbraucht |= {q for q, _, _ in abweichend}
    fehlend = [(f, as_text(entry.get(f))) for f in MANIFEST_MISSING
               if f not in verbraucht and entry.get(f) not in (None, "", [], {})]

    lines = [f"# Manifest-Nachpflege: {name} (`{app_id}`)", ""]
    if not tragbar and not fehlend:
        lines += ["Nichts zu tun: Das Manifest deckt alles, was der Katalog "
                  "über diese App behauptet.", ""]
    else:
        lines += [
            "**Auftrag:** Ergänze das Manifest `oaap-app.yaml` dieser App um "
            "die unten genannten Angaben, soweit das Format sie kennt, und "
            "prüfe die übrigen.", "",
            "**Warum:** Das Manifest gehört dem, der die App gebaut hat — der "
            "Katalog ist nur ein Verzeichnis. Wo beide etwas sagen, gilt das "
            "Manifest. Steht eine Angabe **nur** im Katalog, behauptet dieser "
            "mehr, als das Paket belegt; das ist genau der Zustand, den dieser "
            "Bericht beenden soll.", ""]

    lines += ["## Woher dieser Bericht kommt", "",
              f"- App-Kennung: `{app_id}`"]
    if list_name:
        lines.append(f"- Katalog: {list_name}")
    if manifest_url:
        lines.append(f"- Manifest: {manifest_url}")
    lines.append(f"- Manifest-Format: {fmt or 'unbekannt'}")
    if erzeugt_am:
        lines.append(f"- Erzeugt am {erzeugt_am} vom OAAP Store Editor")
    lines.append("")

    if not manifest:
        lines += ["## Das Manifest war nicht abrufbar", "",
                  f"Grund: {why or 'unbekannt'}", "",
                  "Solange das so bleibt, steht **jede** Angabe dieses "
                  "Eintrags ohne Beleg da. Das ist zulässig — ein Eintrag "
                  "darf entstehen, bevor sein Manifest erreichbar ist —, "
                  "aber es bleibt eine Behauptung.", ""]

    if tragbar:
        lines += ["## 1. Das kann das Manifest heute tragen — bitte ergänzen",
                  "",
                  "| Im Manifest | Woher | Wert | Hinweis |",
                  "| --- | --- | --- | --- |"]
        for t in tragbar:
            lines.append(
                f"| `app.{t['key']}` | "
                f"{'`' + t['quelle'] + '` im Katalog' if t['quelle'] else '—'} | "
                f"{as_text(t['wert']) or '(bitte selbst eintragen)'} | "
                f"{t['hinweis'] or ''} |")
        block = ["", "```yaml"]
        if fmt == "0.1" and any(t["key"] == "class" for t in tragbar):
            block += ['# `class` gibt es erst ab Manifest 0.2 — deshalb zuerst '
                      'die Formatangabe anheben.', 'oaap_manifest: "0.2"']
        block.append("app:")
        for t in tragbar:
            if t["wert"] in (None, "", [], {}):
                block.append(f"  # {t['key']}: …   # {t['hinweis']}")
            else:
                block.append(f"  {t['key']}: {yaml_value(t['wert'])}")
        block.append("```")
        lines += block + [""]
        lines += ["Ein höheres MINOR ist gefahrlos: Ein Knoten, der ein Feld "
                  "nicht kennt, ignoriert es und lehnt die App nicht ab "
                  "(RFC-0012 §8.2 — strenges Schema, toleranter Betrieb).", ""]
        if any(t["key"] == "icon" for t in tragbar):
            lines += ["Zum Bild: " + ICON_HINDERNIS + " Der Katalog führt "
                      f"heute `{as_text(entry.get('icon'))}` — dieser Pfad "
                      "gilt dort relativ zur Liste und passt so **nicht** ins "
                      "Manifest.", ""]

    if fehlend:
        lines += ["## 2. Das führt der Katalog, das Manifest-Format kennt es "
                  "noch nicht", ""]
        lines.append(_rows(fehlend))
        lines += ["**Hier ist nichts zu tun** — das ist ein offener Punkt an "
                  "der Spezifikation, kein Versäumnis dieser App. RFC-0012 "
                  "§1.3 führt einen Teil davon als aus dem Manifest erzeugt, "
                  "ohne dass das Manifest-Schema die Felder hätte. Ob sie "
                  "dorthin wandern, entscheidet RFC-0014; bis dahin bleiben "
                  "sie redaktionell im Katalog.", ""]

    if abweichend:
        lines += ["## 3. Bewusst abweichend — bitte NICHT angleichen", "",
                  "| Feld | Im Katalog | Im Manifest |", "| --- | --- | --- |"]
        lines += [f"| `{f}` | {have} | {says or '—'} |"
                  for f, have, says in abweichend]
        lines += ["",
                  "Diese Felder hat der Katalogpfleger ausdrücklich "
                  "entriegelt. Die Abweichung ist gewollt und überlebt jede "
                  "Neuerzeugung (RFC-0012 §1.3).", ""]

    return "\n".join(lines).rstrip() + "\n"


NICHTS_ZU_TUN = "Nichts zu tun:"


def sammelbericht(reports, list_name, erzeugt_am=""):
    """Die Berichte einer ganzen Liste in einer Datei.

    Jeder Abschnitt bleibt für sich lesbar: Wer eine einzelne App
    betreut, soll seinen Teil herausschneiden können, ohne den Rest zu
    brauchen.
    """
    offen = [t for _, t in reports if NICHTS_ZU_TUN not in t]
    head = ["# Manifest-Nachpflege für " + str(list_name), ""]
    if erzeugt_am:
        head += [f"Erzeugt am {erzeugt_am} vom OAAP Store Editor.", ""]
    head += [f"{len(offen)} von {len(reports)} Einträgen haben etwas offen.",
             "",
             "Jeder Abschnitt ist für sich lesbar und kann einzeln an die KI "
             "gegeben werden, die die betreffende App betreut.", "", "---", ""]
    body = []
    for _, text in reports:
        body.append("#" + text if text.startswith("# ") else text)
        body.append("---\n")
    return "\n".join(head) + "\n".join(body)
