"""Bezugsquellen und Aliasse — das WAS der Spec `oaap.ai.gateway` §3/§4.

Eine Bezugsquelle ist **ein OpenAI-kompatibler HTTP-Endpunkt plus
Zugangsdaten**, mehr nicht. Lokales Modell, externer Anbieter, ein
Kunden-Endpunkt oder ein anderes Gateway sind dieselbe Sorte Sache und
unterscheiden sich in Adresse, Zugangsdaten und Klasse — nicht in Code.
Einen Adapter-Baukasten gibt es bewusst nicht (RFC-0023 A5): Eine
Steckerleiste, die vor ihrem zweiten echten Fall gebaut wird, ist eine
Vermutung im Kostüm einer Schnittstelle.

Ein **Alias** benennt einen Zweck (`chat-default`, `code`,
`embedding-default`) und zeigt auf eine oder mehrere Bezugsquellen.
Innerhalb dieser Gruppe gilt `internal` vor `eu` vor `external` —
Souveränität ist damit das, was passiert, wenn niemand etwas einstellt.
Wer es anders will, hängt `order=listed` an die Zeile.

Konfigurationsfehler sind hier **nie tödlich**: Sie werden gesammelt
und auf der Betreiber-Seite angezeigt. Eine App, die wegen eines
Tippfehlers gar nicht erst startet, ist schlimmer als eine, die sagt,
was sie nicht verstanden hat (dasselbe Muster wie FleetViews
Knotenliste).
"""

# Rangfolge der Klassen. Der Zahlenwert ist die Vorzugsreihenfolge:
# je kleiner, desto lieber. Das ist die einzige Stelle, an der
# Souveränität als Vorrang codiert ist.
CLASS_RANK = {"internal": 0, "eu": 1, "external": 2}

CLASS_LABELS = {
    "internal": "eigene Hardware",
    "eu": "souverän, EU-Rechenzentrum",
    "external": "extern",
}

# Voreinstellung für einen frisch ausgestellten Schlüssel. Bewusst OHNE
# `external`: Der sichere Zustand ist der Standardzustand, und wer eine
# Quelle außerhalb der EU nutzen will, sagt es beim Ausstellen.
DEFAULT_CLASSES = ("internal", "eu")


def _split_lines(raw):
    """Eine Angabe je Zeile oder mit ';' getrennt (Muster FLEETVIEW_NODES)."""
    out = []
    for chunk in (raw or "").replace(";", "\n").split("\n"):
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            out.append(chunk)
    return out


def parse_suppliers(raw, secrets_raw=""):
    """`name=<url> [class=internal|eu|external]` je Zeile.

    Die Zugangsdaten kommen aus einer **zweiten, geheimen** Variablen
    (`name=<schlüssel>`), damit die sichtbare Konfiguration im Portal
    lesbar bleibt und der Schlüssel nie in einer Oberfläche auftaucht.
    Eine Quelle ohne Zugangsdaten ist erlaubt — ein lokales Ollama
    verlangt keine.
    """
    creds, cred_errors = {}, []
    for line in _split_lines(secrets_raw):
        name, sep, value = line.partition("=")
        if not sep or not name.strip() or not value.strip():
            cred_errors.append("Zugangsdaten-Zeile ohne 'name=wert'")
            continue
        creds[name.strip()] = value.strip()

    suppliers, errors = {}, list(cred_errors)
    for line in _split_lines(raw):
        parts = line.split()
        name, sep, url = parts[0].partition("=")
        name, url = name.strip(), url.strip()
        if not sep or not name or not url:
            errors.append(f"Bezugsquelle ohne 'name=adresse': {line!r}")
            continue
        if not url.startswith(("http://", "https://")):
            errors.append(f"Bezugsquelle {name}: Adresse muss mit http:// oder https:// beginnen")
            continue
        cls = "external"
        bad = False
        for extra in parts[1:]:
            key, sep2, value = extra.partition("=")
            if key == "class" and sep2:
                if value in CLASS_RANK:
                    cls = value
                else:
                    errors.append(f"Bezugsquelle {name}: unbekannte Klasse {value!r} "
                                  f"(erlaubt: {', '.join(CLASS_RANK)})")
                    bad = True
            else:
                errors.append(f"Bezugsquelle {name}: unverstandene Angabe {extra!r}")
                bad = True
        if bad:
            continue
        if name in suppliers:
            errors.append(f"Bezugsquelle {name} steht doppelt in der Liste")
            continue
        suppliers[name] = {
            "name": name,
            "url": url.rstrip("/"),
            "class": cls,
            "credential": creds.get(name, ""),
        }
    for name in creds:
        if name not in suppliers:
            errors.append(f"Zugangsdaten für unbekannte Bezugsquelle {name!r}")
    return suppliers, errors


def parse_aliases(raw, suppliers):
    """`alias = quelle:modell[, quelle:modell ...] [order=listed]`.

    Die Liste ist die vom Betreiber **erklärte Ausweich-Gruppe** (Spec
    §3): Es wird ausschließlich innerhalb dieser Gruppe gewechselt.
    Stilles Ersetzen über die Gruppe hinaus ändert das Verhalten,
    manchmal so leise, dass es wochenlang niemand merkt.
    """
    aliases, errors = {}, []
    for line in _split_lines(raw):
        head, sep, tail = line.partition("=")
        name = head.strip()
        if not sep or not name:
            errors.append(f"Alias ohne 'name=quelle:modell': {line!r}")
            continue
        order_listed = False
        body = tail.strip()
        if body.endswith("order=listed"):
            order_listed = True
            body = body[: -len("order=listed")].strip().rstrip(",")
        targets, bad = [], False
        for item in body.split(","):
            item = item.strip()
            if not item:
                continue
            src, sep2, model = item.partition(":")
            src, model = src.strip(), model.strip()
            if not sep2 or not src or not model:
                errors.append(f"Alias {name}: {item!r} ist kein 'quelle:modell'")
                bad = True
                continue
            if src not in suppliers:
                errors.append(f"Alias {name}: Bezugsquelle {src!r} ist nicht konfiguriert")
                bad = True
                continue
            targets.append({"supplier": src, "model": model})
        if bad or not targets:
            if not targets and not bad:
                errors.append(f"Alias {name}: keine Bezugsquelle angegeben")
            continue
        if name in aliases:
            errors.append(f"Alias {name} steht doppelt in der Liste")
            continue
        aliases[name] = {"name": name, "targets": targets, "order_listed": order_listed}
    return aliases, errors


def candidates(alias, suppliers, allowed_classes):
    """Die erlaubten Ziele eines Alias, in der Reihenfolge des Versuchs.

    Voreingestellt nach Klasse (`internal` vor `eu` vor `external`),
    bei `order=listed` in der Reihenfolge der Konfiguration. Stabil
    sortiert, damit gleiche Klassen ihre erklärte Reihenfolge behalten.
    """
    allowed = set(allowed_classes)
    rows = []
    for target in alias["targets"]:
        src = suppliers.get(target["supplier"])
        if src and src["class"] in allowed:
            rows.append({"supplier": src, "model": target["model"]})
    if alias["order_listed"]:
        return rows
    return sorted(rows, key=lambda r: CLASS_RANK.get(r["supplier"]["class"], 9))


def blocked_classes(alias, suppliers, allowed_classes):
    """Klassen, an denen dieser Alias für diesen Schlüssel scheitert.

    Wird für die Fehlermeldung gebraucht: „nicht erreichbar" und „du
    darfst diese Klasse nicht" sind zwei verschiedene Auskünfte, und
    nur die zweite kann der Anwender selbst beheben.
    """
    allowed = set(allowed_classes)
    out = []
    for target in alias["targets"]:
        src = suppliers.get(target["supplier"])
        if src and src["class"] not in allowed and src["class"] not in out:
            out.append(src["class"])
    return out
