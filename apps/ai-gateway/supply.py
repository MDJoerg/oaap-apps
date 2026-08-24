"""Bezugsquellen und Aliasse — das WAS der Spec `oaap.ai.gateway` §3/§4.

Eine Bezugsquelle ist **ein OpenAI-kompatibler HTTP-Endpunkt plus
Zugangsdaten**, mehr nicht. Lokales Modell, externer Anbieter, ein
Kunden-Endpunkt oder ein anderes Gateway sind dieselbe Sorte Sache und
unterscheiden sich in Adresse, Zugangsdaten und **Ampelfarbe** — nicht
in Code.
Einen Adapter-Baukasten gibt es bewusst nicht (RFC-0023 A5): Eine
Steckerleiste, die vor ihrem zweiten echten Fall gebaut wird, ist eine
Vermutung im Kostüm einer Schnittstelle.

Ein **Alias** benennt einen Zweck (`chat-default`, `code`,
`embedding-default`) und zeigt auf eine oder mehrere Bezugsquellen.
Innerhalb dieser Gruppe gilt grün vor gelb vor rot — Souveränität ist
damit das, was passiert, wenn niemand etwas einstellt. Wer es anders
will, hängt `order=listed` an die Zeile.

Konfigurationsfehler sind hier **nie tödlich**: Sie werden gesammelt
und auf der Betreiber-Seite angezeigt. Eine App, die wegen eines
Tippfehlers gar nicht erst startet, ist schlimmer als eine, die sagt,
was sie nicht verstanden hat (dasselbe Muster wie FleetViews
Knotenliste).
"""

# Die Ampel: Was kann mit den Daten geschehen, die hier hineingehen?
# Der Zahlenwert ist die Vorzugsreihenfolge (je kleiner, desto lieber)
# UND die Schwere (je größer, desto gefährlicher). Beides fällt
# zusammen, und genau deshalb ist es eine Ampel und keine Landkarte:
# Wo ein Rechner steht, ist der Grund für eine Farbe, nie ihre Bedeutung.
LIGHT_RANK = {"green": 0, "yellow": 1, "red": 2}

LIGHT_LABELS = {
    "green": "grün — Daten verlassen das Unternehmen nicht",
    "yellow": "gelb — verlassen es vielleicht, aber unter Zusage",
    "red": "rot — externer Anbieter, Daten können abfließen",
}

LIGHT_RULES = {
    "green": "Personenbezogene Daten nur mit Freigabe.",
    "yellow": "Unternehmensinformationen erlaubt; personenbezogene Daten nur mit Freigabe.",
    "red": "Keine Unternehmensinformationen, keine personenbezogenen Daten.",
}

# Voreinstellung für eine Bezugsquelle ohne Angabe: das Schlechteste.
# Unbekannte Herkunft ist nicht souverän, und der sichere Zustand ist
# der Standardzustand.
DEFAULT_LIGHT = "red"

# Voreinstellung für einen frisch ausgestellten Schlüssel: bis gelb.
# Rot ist eine bewusste Zusatzerlaubnis.
DEFAULT_CEILING = "yellow"


def worse(a, b):
    """Die schlechtere zweier Farben — die Regel, die Ketten ehrlich hält."""
    return a if LIGHT_RANK.get(a, 9) >= LIGHT_RANK.get(b, 9) else b


def allows(ceiling, light):
    """Darf ein Schlüssel mit dieser Obergrenze diese Farbe benutzen?"""
    return LIGHT_RANK.get(light, 9) <= LIGHT_RANK.get(ceiling, -1)


def _split_lines(raw):
    """Eine Angabe je Zeile oder mit ';' getrennt (Muster FLEETVIEW_NODES)."""
    out = []
    for chunk in (raw or "").replace(";", "\n").split("\n"):
        chunk = chunk.strip()
        if chunk and not chunk.startswith("#"):
            out.append(chunk)
    return out


def parse_suppliers(raw, secrets_raw=""):
    """`name=<url> [light=green|yellow|red]` je Zeile.

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
        light = DEFAULT_LIGHT
        bad = False
        for extra in parts[1:]:
            key, sep2, value = extra.partition("=")
            if key == "light" and sep2:
                if value in LIGHT_RANK:
                    light = value
                else:
                    errors.append(f"Bezugsquelle {name}: unbekannte Ampelfarbe {value!r} "
                                  f"(erlaubt: {', '.join(LIGHT_RANK)})")
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
            "light": light,
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


def candidates(alias, suppliers, ceiling, personal_data=False):
    """Die erlaubten Ziele eines Alias, in der Reihenfolge des Versuchs.

    Voreingestellt nach Ampel (grün vor gelb vor rot), bei
    `order=listed` in der Reihenfolge der Konfiguration. Stabil
    sortiert, damit gleiche Farben ihre erklärte Reihenfolge behalten.

    `personal_data` ist die **Freigabe** des Schlüssels für
    personenbezogene Daten — und schließt rot aus. Nicht, weil rot
    grundsätzlich verboten wäre, sondern weil das Gateway nicht
    hineinsehen darf und deshalb eine Anfrage nicht von der anderen
    unterscheiden kann. Eine Regel, die davon abhinge, dass wir
    personenbezogene Daten erkennen, wäre ein Versprechen, das wir
    nicht halten können.
    """
    rows = []
    for target in alias["targets"]:
        src = suppliers.get(target["supplier"])
        if not src or not allows(ceiling, src["light"]):
            continue
        if personal_data and src["light"] == "red":
            continue
        rows.append({"supplier": src, "model": target["model"]})
    if alias["order_listed"]:
        return rows
    return sorted(rows, key=lambda r: LIGHT_RANK.get(r["supplier"]["light"], 9))


def alias_light(alias, suppliers, ceiling, personal_data=False):
    """Die Farbe, die dieser Schlüssel bei diesem Alias **wirklich** bekäme.

    Die schlechteste unter den erreichbaren Zielen — ein Alias, der von
    einem grünen Modell auf einen roten Anbieter ausweichen kann, ist
    nicht grün. Ohne diese Rechnung wäre die Ampel eine Beruhigung
    statt einer Auskunft.
    """
    rows = candidates(alias, suppliers, ceiling, personal_data)
    if not rows:
        return ""
    light = "green"
    for row in rows:
        light = worse(light, row["supplier"]["light"])
    return light


def blocked_reasons(alias, suppliers, ceiling, personal_data=False):
    """Warum ein Alias für diesen Schlüssel leer ausgeht.

    „nicht erreichbar“ und „du darfst diese Farbe nicht“ sind zwei
    verschiedene Auskünfte, und nur die zweite kann jemand beheben.
    """
    out = []
    for target in alias["targets"]:
        src = suppliers.get(target["supplier"])
        if not src:
            continue
        if personal_data and src["light"] == "red":
            reason = ("rot ist für einen Schlüssel mit Freigabe für "
                      "personenbezogene Daten nie erlaubt")
        elif not allows(ceiling, src["light"]):
            reason = f"{src['light']} liegt über der Obergrenze {ceiling}"
        else:
            continue
        if reason not in out:
            out.append(reason)
    return out
