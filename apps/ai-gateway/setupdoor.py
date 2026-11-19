"""Die Einrichtungstür (RFC-0104 §3.1, §3.3) — was ein Aufbau in diesem Gateway anlegen darf.

Der Knoten ruft sie beim Aufbau eines Profils (Schritt `app.setup`) über
seinen internen Endpunkt; von außen liegt der Pfad unter der Route `/`
und damit hinter `admin`. Durch kommt nur, wer `OAAP_SETUP_KEY` kennt —
ein Wert, den allein die Plattform hält. Jede Identitäts-Kopfzeile wird
hier übergangen: Auch eine Serververwaltung kommt ohne den Schlüssel
nicht hinein. Fehlt der Schlüssel in der Umgebung, ist die Tür zu.

Drei Arten:

- `supplier` — eine Bezugsquelle **ohne** Zugangsdaten; die trägt ein
  Mensch auf der Seite ein. `ready` heißt: Zugangsdaten gespeichert und
  der Verbindungstest der Seite antwortet jetzt.
- `alias` — ein Modellname auf genau ein Ziel (Quelle + Modell der Quelle).
- `client` — die Freigabe für einen Chat (§4), eine Zeile der Art
  `client` in `keys` ohne benutzbaren Digest.

Die Regeln, die überall gelten:

- Nie wird überschrieben, was ein Mensch gemacht hat: Was es unter dieser
  Kennung schon gibt und anders ist, ist ein `conflict`, und es bleibt.
- Ein Feld mit einem Geheimnis-Namen wird nie angenommen (`refused`).
- Gemerkt wird je angelegtem Eintrag: wer, wann, Digest des Wunsches.
  `remove` entfernt nur, was die Einrichtung angelegt hat und was noch
  genau so ist.
"""
import hashlib
import hmac
import json
import os
import re
import threading

import catalog
import store
import supply

KINDS = ("supplier", "alias", "client")

# RFC-0104 §3.1 nennt key, token, secret, password, credentials. Dazu die
# beiden Schreibweisen, unter denen dieses Gateway selbst Zugangsdaten
# kennt (Formularfeld `credential`) und unter denen Clients sie schicken.
SECRET_FIELDS = {"key", "token", "secret", "password", "credentials", "credential", "api_key"}

FIELDS = {"supplier": {"url", "light"},
          "alias": {"supplier", "model"},
          "client": {"aliases", "ceiling", "personal_data"}}

ACTOR_CHARS = re.compile(r"[^\w.@:+-]")


class Malformed(ValueError):
    """Ein Rumpf, der nicht der Form von §3.1 folgt — 400."""


# ------------------------------------------------------------- Wer fragt

def allowed(headers, env=None):
    """Nur mit dem Einrichtungsschlüssel dieser Instanz. Ohne ihn in der Umgebung: nie."""
    want = (os.environ if env is None else env).get("OAAP_SETUP_KEY", "")
    if not want:
        return False
    header = headers.get("Authorization", "") or ""
    got = header[7:].strip() if header[:7].lower() == "bearer " else ""
    return bool(got) and hmac.compare_digest(got.encode("utf-8"), want.encode("utf-8"))


def actor_of(headers):
    """Wer den Aufbau gestartet hat, für die Prüfspur — als `setup:<wer>`."""
    raw = ACTOR_CHARS.sub("", (headers.get("X-OAAP-Setup-Actor") or "").strip())[:80]
    return "setup:" + (raw or "?")


# ------------------------------------------------------------- Der Rumpf

def parse(raw):
    """(\"apply\", items, dry) oder (\"remove\", items, False). Wirft Malformed."""
    try:
        doc = json.loads(raw or b"")
    except ValueError:
        raise Malformed("Der Rumpf ist kein JSON.") from None
    if not isinstance(doc, dict) or doc.get("v") != 1:
        raise Malformed("Erwartet wird ein Objekt mit \"v\": 1.")
    has_items, has_remove = "items" in doc, "remove" in doc
    if has_items == has_remove:
        raise Malformed("Genau eins von \"items\" oder \"remove\".")
    items = doc["items"] if has_items else doc["remove"]
    if not isinstance(items, list):
        raise Malformed("\"items\" und \"remove\" sind Listen.")
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("kind"), str) \
                or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise Malformed("Jeder Eintrag ist ein Objekt mit \"kind\" und \"id\" als Text.")
    dry = doc.get("dry", False)
    if not isinstance(dry, bool):
        raise Malformed("\"dry\" ist true oder false.")
    if has_remove and "dry" in doc:
        raise Malformed("\"dry\" gilt nur für \"items\".")
    return ("apply", items, dry) if has_items else ("remove", items, False)


def wish(item):
    """(normierter Wunsch, '') oder (None, Grund für `refused`)."""
    kind, ident = item["kind"], item["id"].strip()
    secret = sorted(k for k in item if k.lower() in SECRET_FIELDS)
    if secret:
        return None, ("Felder mit dem Namen eines Geheimnisses nimmt die Tür nie an: "
                      + ", ".join(secret) + ". Zugangsdaten trägt ein Mensch auf der Seite ein.")
    if kind not in KINDS:
        return None, f"Die Art „{kind}“ kennt dieses Gateway nicht (bekannt: {', '.join(KINDS)})."
    extra = sorted(set(item) - {"kind", "id"} - FIELDS[kind])
    if extra:
        return None, f"Unbekannte Felder für {kind}: {', '.join(extra)}."
    if not catalog.NAME.match(ident):
        return None, f"Kennung „{ident}“: {catalog.NAME_RULE}."
    if kind == "supplier":
        url, light = item.get("url"), item.get("light", supply.DEFAULT_LIGHT)
        if not isinstance(url, str) or not isinstance(light, str):
            return None, "url und light sind Text."
        _src, error = supply.make_supplier(ident, url.strip(), light)
        if error:
            return None, error
        return {"url": url.strip().rstrip("/"), "light": light}, ""
    if kind == "alias":
        src, model = item.get("supplier"), item.get("model")
        if not isinstance(src, str) or not isinstance(model, str) or not src.strip() \
                or not model.strip():
            return None, "supplier und model sind nicht leerer Text."
        if "," in src or ":" in src or "," in model:
            return None, "supplier ohne „,“ und „:“, model ohne „,“."
        return {"targets": f"{src.strip()}:{model.strip()}", "order_listed": 0}, ""
    names = item.get("aliases", [])
    ceiling = item.get("ceiling", supply.DEFAULT_CEILING)
    pbd = item.get("personal_data", False)
    if not isinstance(names, list) or not all(isinstance(n, str) and n.strip() for n in names):
        return None, "aliases ist eine Liste von Modellnamen."
    if ceiling not in supply.LIGHT_RANK:
        return None, f"ceiling kennt nur {', '.join(supply.LIGHT_RANK)}."
    if not isinstance(pbd, bool):
        return None, "personal_data ist true oder false."
    return {"aliases": ",".join(sorted({n.strip() for n in names})), "ceiling": ceiling,
            "personal_data": 1 if pbd else 0}, ""


def digest(kind, ident, norm):
    doc = dict(norm, kind=kind, id=ident)
    return hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


FIELD_DE = {"url": "Adresse", "light": "Ampel", "targets": "Ziel", "order_listed": "Reihenfolge",
            "aliases": "Modellnamen", "ceiling": "Obergrenze", "personal_data": "pbD-Freigabe"}


def differences(want, have):
    return [f"{FIELD_DE.get(n, n)}: ist „{have.get(n)}“, gewünscht „{want[n]}“"
            for n in want if have.get(n) != want[n]]


# ------------------------------------------------------------- Die Tür

class Door:
    """Die Tür eines Gateways. Was sie aus der App braucht, bekommt sie herein:

    - `db`      — die Bücher (store.connect)
    - `mode`    — () → "app" oder "config" (solange die Übernahme wartet)
    - `master`  — () → AIGW_MASTER_KEY
    - `reload`  — () → baut SUPPLIERS/ALIASES neu
    - `probe`   — (name) → Ergebnis des Verbindungstests der Seite
    - `loaded`  — () → (SUPPLIERS, ALIASES), wie das Gateway sie gerade routet
    """

    def __init__(self, db, mode, master, reload, probe, loaded):
        self.db, self.mode, self.master = db, mode, master
        self.reload, self.probe, self.loaded = reload, probe, loaded
        self._lock = threading.Lock()

    # ---- was es gibt

    def current(self, kind, ident):
        """(normierter Ist-Zustand oder None, Sperrgrund oder '', Zeile oder None)."""
        if kind == "supplier":
            row = self.db.execute("SELECT * FROM suppliers WHERE name=?", (ident,)).fetchone()
            return ({"url": row["url"], "light": row["light"]} if row else None), "", row
        if kind == "alias":
            row = self.db.execute("SELECT * FROM aliases WHERE name=?", (ident,)).fetchone()
            return ({"targets": row["targets"], "order_listed": int(row["order_listed"])}
                    if row else None), "", row
        row = self.db.execute("SELECT * FROM keys WHERE label=?", (ident,)).fetchone()
        if row is None:
            return None, "", None
        if row["kind"] != "client":
            return None, f"„{ident}“ ist das Etikett eines API-Schlüssels, keiner Freigabe.", row
        if row["revoked"]:
            return None, f"Die Freigabe „{ident}“ ist widerrufen.", row
        have = {"aliases": ",".join(sorted(a for a in (row["aliases"] or "").split(",") if a)),
                "ceiling": row["ceiling"], "personal_data": int(row["personal_data"])}
        return have, "", row

    def remembered(self, kind, ident):
        return self.db.execute("SELECT * FROM setup_items WHERE kind=? AND id=?",
                               (kind, ident)).fetchone()

    def _forget(self, kind, ident):
        self.db.execute("DELETE FROM setup_items WHERE kind=? AND id=?", (kind, ident))

    # ---- GET

    def kinds(self):
        return {"v": 1, "kinds": list(KINDS)}

    def state(self, kind, ident):
        if kind not in KINDS:
            raise Malformed(f"Die Art „{kind}“ kennt dieses Gateway nicht.")
        if not ident:
            raise Malformed("Ohne id.")
        have, block, row = self.current(kind, ident)
        doc = {"v": 1, "kind": kind, "id": ident, "state": "present" if row else "absent",
               "ready": False, "note": ""}
        if row is None:
            doc["note"] = "gibt es nicht"
            return doc
        if block:
            doc["note"] = block
            return doc
        suppliers, aliases = self.loaded()
        if kind == "supplier":
            if not row["credential"]:
                doc["note"] = ("Zugangsdaten fehlen — ein Mensch trägt sie unter Bezugsquellen "
                               "ein.")
            elif ident not in suppliers:
                doc["note"] = "Zugangsdaten nicht lesbar (AIGW_MASTER_KEY geändert?)."
            else:
                r = self.probe(ident)
                status = r.get("status")
                doc["ready"] = bool(status) and 200 <= status < 300
                doc["note"] = (f"Verbindungstest: HTTP {status} in {r.get('ms', 0)} ms"
                               if status else f"Verbindungstest: {r.get('error') or 'keine Antwort'}")
        elif kind == "alias":
            doc["ready"] = ident in aliases
            doc["note"] = "" if doc["ready"] else "Ziel nicht nutzbar (Bezugsquelle fehlt oder fehlerhaft)."
        else:
            doc["ready"] = store.usable(row)
            doc["note"] = "" if doc["ready"] else "abgelaufen"
        return doc

    # ---- POST items

    def apply(self, items, dry, actor):
        with self._lock:
            planned = {"supplier": set(), "alias": set()}
            return [self._one(item, dry, actor, planned) for item in items]

    def _missing(self, kind, norm, planned):
        """Was vorher da sein muss — für einen Modellnamen die Quelle, für eine Freigabe die Namen."""
        if kind == "alias":
            src = norm["targets"].partition(":")[0]
            known = {r["name"] for r in catalog.supplier_rows(self.db)} | planned["supplier"]
            return "" if src in known else f"Eine Bezugsquelle „{src}“ gibt es nicht."
        if kind == "client":
            known = {r["name"] for r in catalog.alias_rows(self.db)} | planned["alias"]
            gone = [n for n in norm["aliases"].split(",") if n and n not in known]
            return ("Diese Modellnamen gibt es nicht: " + ", ".join(gone) + ".") if gone else ""
        return ""

    def _one(self, item, dry, actor, planned):
        kind, ident = item["kind"], item["id"].strip()

        def out(result, note=""):
            if not dry and result in ("conflict", "refused"):
                store.log(self.db, actor, "setup." + result, f"{kind}:{ident}", note[:300])
                self.db.commit()
            return {"kind": kind, "id": ident, "result": result, "note": note}

        norm, why = wish(item)
        if why:
            return out("refused", why)
        have, block, row = self.current(kind, ident)
        if block:
            return out("conflict", block)
        if have is not None:
            diff = differences(norm, have)
            return out("conflict", "; ".join(diff)) if diff else out("present")
        why = self._missing(kind, norm, planned)
        if why:
            return out("refused", why)
        if kind in ("supplier", "alias") and self.mode() != "app":
            return out("refused", "Gesperrt, solange die Übernahme aus der Konfiguration "
                                  "wartet (AIGW_MASTER_KEY fehlt).")
        if dry:
            if kind in planned:
                planned[kind].add(ident)
            return out("would_make")
        try:
            if kind == "supplier":
                catalog.set_supplier(self.db, actor, self.master(), ident, norm["url"],
                                     norm["light"])
            elif kind == "alias":
                catalog.set_alias(self.db, actor, ident, norm["targets"], False)
            else:
                names = [n for n in norm["aliases"].split(",") if n]
                store.grant(self.db, ident, actor, names, norm["ceiling"],
                            bool(norm["personal_data"]))
        except ValueError as exc:
            return out("refused", str(exc))
        if kind in ("supplier", "alias"):
            self.reload()
        # Gemerkt wird der Ist-Zustand nach dem Anlegen, so wie `current` ihn
        # liest — genau damit vergleicht `remove` später.
        made, _block, _row = self.current(kind, ident)
        d = digest(kind, ident, made or norm)
        self.db.execute("INSERT OR REPLACE INTO setup_items (kind,id,actor,time,digest) "
                        "VALUES (?,?,?,?,?)", (kind, ident, actor, store.now(), d))
        store.log(self.db, actor, "setup.make", f"{kind}:{ident}", f"digest={d[:16]}")
        self.db.commit()
        return out("made")

    # ---- POST remove

    def remove(self, items, actor):
        with self._lock:
            return [self._drop(item, actor) for item in items]

    def _drop(self, item, actor):
        kind, ident = item["kind"], item["id"].strip()

        def out(result, note=""):
            store.log(self.db, actor, "setup." + ("remove" if result == "removed" else result),
                      f"{kind}:{ident}", note[:300])
            self.db.commit()
            return {"kind": kind, "id": ident, "result": result, "note": note}

        if kind not in KINDS:
            return out("refused", f"Die Art „{kind}“ kennt dieses Gateway nicht.")
        have, block, row = self.current(kind, ident)
        mem = self.remembered(kind, ident)
        if row is None:
            self._forget(kind, ident)
            return out("gone")
        if kind == "client" and row["kind"] == "client" and row["revoked"]:
            # Widerrufen wirkt wie entfernt; die Zeile bleibt für die Bücher.
            self._forget(kind, ident)
            return out("gone", "nur noch als widerrufene Zeile in den Büchern")
        if block:
            return out("conflict", block + " Bleibt.")
        if mem is None:
            return out("conflict", "Nicht von der Einrichtung angelegt — bleibt.")
        if digest(kind, ident, have) != mem["digest"]:
            return out("conflict", "Seit der Einrichtung von Hand geändert — bleibt.")
        if kind in ("supplier", "alias") and self.mode() != "app":
            return out("refused", "Gesperrt, solange die Übernahme aus der Konfiguration wartet.")
        note = ""
        try:
            if kind == "supplier":
                if row["credential"]:
                    return out("conflict", "Ein Mensch hat Zugangsdaten eingetragen; die löscht "
                                           "nur ein Mensch — bleibt.")
                catalog.delete_supplier(self.db, actor, ident)
            elif kind == "alias":
                users = [r["label"] for r in store.keys(self.db, include_revoked=False)
                         if ident in (r["aliases"] or "").split(",")]
                if users:
                    return out("conflict", "Noch benutzt von: " + ", ".join(users) + " — bleibt.")
                catalog.delete_alias(self.db, actor, ident)
            else:
                used = self.db.execute("SELECT 1 FROM usage WHERE key_id=? LIMIT 1",
                                       (row["id"],)).fetchone()
                if used:
                    # Verbrauch zeigt auf diese Zeile; gelöscht wäre er niemandem mehr zuzuordnen.
                    store.revoke(self.db, ident, actor)
                    note = "widerrufen statt gelöscht: es gibt schon Verbrauch"
                else:
                    self.db.execute("DELETE FROM keys WHERE id=?", (row["id"],))
        except ValueError as exc:
            return out("conflict", str(exc) + " Bleibt.")
        self._forget(kind, ident)
        if kind in ("supplier", "alias"):
            self.reload()
        return out("removed", note)
