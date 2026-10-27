"""Bezugsquellen und Modellnamen, gepflegt in der App (RFC-0064 Stufe 2).

Bis 0.3 standen beide als Textzeilen in der Instanz-Konfiguration
(`AIGW_SUPPLIERS`, `AIGW_SUPPLIER_KEYS`, `AIGW_ALIASES`). Seit 0.4 ist die
Datenbank der App die Wahrheit (D1). Die Variablen werden **einmal**
übernommen und danach nicht mehr gelesen — zwei Orte, die beide zählen,
wären zwei Wahrheiten, und früher oder später widersprechen sie sich.

Geprüft wird mit denselben Regeln wie früher die Zeilen
(`supply.make_supplier`, `supply.make_alias`): ein Weg, eine Regel.

Die Zugangsdaten einer Quelle sind nur verschlüsselt gespeichert
(vault.py, D2). Fehlt `AIGW_MASTER_KEY`, verweigert die App das
Speichern von Zugangsdaten und sagt warum — und die Übernahme aus der
Konfiguration wartet, statt Schlüssel im Klartext abzulegen oder
wegzuwerfen.
"""
import re

import store
import supply
import vault

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")
NAME_RULE = "Buchstaben, Ziffern, _ . -, höchstens 40 Zeichen"


# ------------------------------------------------------------------ Lesen

def meta(db, name):
    row = db.execute("SELECT value FROM meta WHERE name=?", (name,)).fetchone()
    return row["value"] if row else ""


def _set_meta(db, name, value):
    db.execute("INSERT INTO meta (name,value) VALUES (?,?) "
               "ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, value))


def empty(db):
    return not (db.execute("SELECT 1 FROM suppliers LIMIT 1").fetchone()
                or db.execute("SELECT 1 FROM aliases LIMIT 1").fetchone())


def supplier_rows(db):
    return list(db.execute("SELECT * FROM suppliers ORDER BY name"))


def alias_rows(db):
    return list(db.execute("SELECT * FROM aliases ORDER BY name"))


def load(db, master):
    """Baut aus der Datenbank, was das Gateway zum Routen braucht.

    Gibt `(quellen, aliasse, fehler)` zurück — dieselben Formen wie
    `supply.parse_suppliers`/`parse_aliases`. Eine Quelle, deren
    Zugangsdaten sich nicht öffnen lassen, fällt heraus und wird benannt:
    Sie ohne Schlüssel anzufragen ergäbe Abweisungen, die wie ein Fehler
    des Anbieters aussähen.
    """
    suppliers, errors = {}, []
    for row in supplier_rows(db):
        credential = ""
        if row["credential"]:
            try:
                credential = vault.open_(master, row["credential"])
            except vault.VaultError as exc:
                errors.append(f"Bezugsquelle {row['name']}: Zugangsdaten nicht lesbar — {exc}. "
                              "Neu eintragen oder den richtigen AIGW_MASTER_KEY setzen.")
                continue
        src, error = supply.make_supplier(row["name"], row["url"], row["light"], credential)
        if error:
            errors.append(error)
            continue
        suppliers[row["name"]] = src
    aliases = {}
    for row in alias_rows(db):
        alias, alias_errors = supply.make_alias(row["name"], row["targets"],
                                                row["order_listed"], suppliers)
        if alias is None:
            errors.extend(alias_errors)
            continue
        aliases[row["name"]] = alias
    return suppliers, aliases, errors


# ------------------------------------------------------------- Übernahme

def import_config(db, env, master):
    """Übernimmt die drei Konfigurationsvariablen einmal in die Datenbank.

    Gibt `(zustand, sätze)` zurück:
    - `imported`  — übernommen; `sätze` nennt Zeilen, die nicht passten
    - `blocked`   — Zugangsdaten vorhanden, aber kein tauglicher
                    AIGW_MASTER_KEY: nichts übernommen, die Konfiguration
                    gilt weiter wie bisher
    - `skipped`   — die App pflegt schon selbst, die Variablen bleiben liegen
    - `already`   — früher schon übernommen
    - `nothing`   — es gab nichts zu übernehmen
    """
    if meta(db, "imported_at"):
        return "already", []
    raw_s = env.get("AIGW_SUPPLIERS", "")
    raw_k = env.get("AIGW_SUPPLIER_KEYS", "")
    raw_a = env.get("AIGW_ALIASES", "")
    if not (raw_s.strip() or raw_a.strip()):
        return "nothing", []
    if not empty(db):
        return "skipped", []
    suppliers, s_errors = supply.parse_suppliers(raw_s, raw_k)
    if any(src["credential"] for src in suppliers.values()) and not vault.usable(master):
        return "blocked", ["AIGW_MASTER_KEY fehlt oder ist zu kurz — ohne ihn würden die "
                           "Zugangsdaten im Klartext unter /data liegen."]
    aliases, a_errors = supply.parse_aliases(raw_a, suppliers)
    now = store.now()
    for src in suppliers.values():
        sealed = vault.seal(master, src["credential"]) if src["credential"] else ""
        db.execute("INSERT INTO suppliers (name,url,light,credential,created,updated) "
                   "VALUES (?,?,?,?,?,?)", (src["name"], src["url"], src["light"], sealed, now, now))
    for alias in aliases.values():
        db.execute("INSERT INTO aliases (name,targets,order_listed,created,updated) "
                   "VALUES (?,?,?,?,?)", (alias["name"], supply.targets_text(alias),
                                          1 if alias["order_listed"] else 0, now, now))
    _set_meta(db, "imported_at", now)
    problems = s_errors + a_errors
    store.log(db, "migration", "config.import", f"{len(suppliers)} Quellen, {len(aliases)} Modellnamen",
              f"{len(problems)} Zeile(n) nicht übernommen" if problems else "vollständig")
    db.commit()
    return "imported", problems


# ------------------------------------------------------------ Schreiben

def set_supplier(db, actor, master, name, url, light, credential="", drop_credential=False):
    """Legt eine Quelle an oder ändert sie. Wirft ValueError mit einem Satz.

    `credential` leer heißt **unverändert** — der gespeicherte Wert wird
    nie zurück ins Formular geschrieben, also kann ein leeres Feld nicht
    „löschen“ bedeuten. Löschen ist ein eigenes Häkchen.
    """
    name, url, credential = (name or "").strip(), (url or "").strip(), (credential or "").strip()
    if not NAME.match(name):
        raise ValueError(f"Name der Bezugsquelle: {NAME_RULE}.")
    _src, error = supply.make_supplier(name, url, light)
    if error:
        raise ValueError(error)
    row = db.execute("SELECT * FROM suppliers WHERE name=?", (name,)).fetchone()
    sealed = row["credential"] if row else ""
    what = "unverändert" if sealed else "keine"
    if drop_credential:
        sealed, what = "", "entfernt"
    elif credential:
        if not vault.usable(master):
            raise ValueError("Zugangsdaten werden nur verschlüsselt gespeichert, und dafür fehlt "
                             "AIGW_MASTER_KEY (Portal → Instanz → Konfiguration, Knopf "
                             "„erzeugen“). Nichts gespeichert.")
        sealed, what = vault.seal(master, credential), "neu gesetzt"
    now = store.now()
    if row:
        db.execute("UPDATE suppliers SET url=?, light=?, credential=?, updated=? WHERE name=?",
                   (url.rstrip("/"), light, sealed, now, name))
    else:
        db.execute("INSERT INTO suppliers (name,url,light,credential,created,updated) "
                   "VALUES (?,?,?,?,?,?)", (name, url.rstrip("/"), light, sealed, now, now))
    # Nie der Wert, nur was mit ihm geschah.
    store.log(db, actor, "supplier.set", name,
              f"{'geändert' if row else 'neu'}: url={url.rstrip('/')} ampel={light} "
              f"zugangsdaten={what}")
    db.commit()
    return "geändert" if row else "angelegt"


def users_of_supplier(db, name):
    out = []
    for row in alias_rows(db):
        sources = [item.partition(":")[0].strip() for item in row["targets"].split(",")]
        if name in sources:
            out.append(row["name"])
    return out


def delete_supplier(db, actor, name):
    if not db.execute("SELECT 1 FROM suppliers WHERE name=?", (name,)).fetchone():
        raise ValueError(f"Eine Bezugsquelle „{name}“ gibt es nicht.")
    users = users_of_supplier(db, name)
    if users:
        # Still aus einer Gruppe zu verschwinden, ändert, wer antwortet —
        # genau das, was die Gruppe verhindern soll.
        raise ValueError(f"„{name}“ wird noch benutzt von: {', '.join(users)}. "
                         "Erst dort herausnehmen.")
    db.execute("DELETE FROM suppliers WHERE name=?", (name,))
    store.log(db, actor, "supplier.delete", name)
    db.commit()


def set_alias(db, actor, name, targets_text, order_listed):
    name = (name or "").strip()
    if not NAME.match(name):
        raise ValueError(f"Modellname: {NAME_RULE}.")
    known = {row["name"]: True for row in supplier_rows(db)}
    alias, errors = supply.make_alias(name, targets_text, order_listed, known)
    if alias is None:
        raise ValueError(" ".join(errors))
    text = supply.targets_text(alias)
    row = db.execute("SELECT 1 FROM aliases WHERE name=?", (name,)).fetchone()
    now = store.now()
    if row:
        db.execute("UPDATE aliases SET targets=?, order_listed=?, updated=? WHERE name=?",
                   (text, 1 if order_listed else 0, now, name))
    else:
        db.execute("INSERT INTO aliases (name,targets,order_listed,created,updated) "
                   "VALUES (?,?,?,?,?)", (name, text, 1 if order_listed else 0, now, now))
    store.log(db, actor, "alias.set", name,
              f"{'geändert' if row else 'neu'}: {text}"
              f"{' (wie aufgeführt)' if order_listed else ''}")
    db.commit()
    return "geändert" if row else "angelegt"


def delete_alias(db, actor, name):
    """Löscht einen Modellnamen; gibt die Schlüssel zurück, die auf ihn beschränkt waren.

    Diese Schlüssel verlieren damit den Zugang zu ihm — der Satz dazu
    gehört auf die Seite, nicht in eine Fußnote.
    """
    if not db.execute("SELECT 1 FROM aliases WHERE name=?", (name,)).fetchone():
        raise ValueError(f"Einen Modellnamen „{name}“ gibt es nicht.")
    db.execute("DELETE FROM aliases WHERE name=?", (name,))
    store.log(db, actor, "alias.delete", name)
    db.commit()
    return [row["label"] for row in store.keys(db, include_revoked=False)
            if name in [a for a in (row["aliases"] or "").split(",") if a]]
