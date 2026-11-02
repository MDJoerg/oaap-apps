"""Der Kontext eines Schlüssels und seine Kopie in der Messzeile (RFC-0064 §3).

Der Kontext ist das, wonach ausgewertet und außerhalb verrechnet wird:
Verantwortliche, Konto, Projekt, Kostenstelle, Anwendung und bis zu fünf
freie Etiketten. `tenant` gehört nicht dazu — den füllt RFC-0022, nicht
der Betreiber von Hand.

Jede Messzeile trägt eine **Kopie** dieses Kontexts (`ctx_*`), wie ein
Beleg die Kontierung behält, mit der er gebucht wurde: Eine später
geänderte Kostenstelle verschiebt keine alten Monate.
"""
import json
import re

CONTEXT_FIELDS = ("owner", "account", "project", "cost_center", "application", "labels")
MAX_LABELS = 5
MAX_TEXT = 120
LABEL_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")

# Spalte der Messzeile → Spalte des Schlüssels. Reihenfolge = Reihenfolge
# beim Schreiben (snapshot_values).
SNAPSHOT = (
    ("ctx_owner", "owner"),
    ("ctx_account", "account"),
    ("ctx_project", "project"),
    ("ctx_cost_center", "cost_center"),
    ("ctx_application", "application"),
    ("ctx_tenant", "tenant"),
    ("ctx_labels", "labels"),
)

# Die Spalten, die eine Messtabelle für die Kopie braucht. `ctx_source`:
# `snapshot` = beim Aufruf mitgeschrieben; `backfilled` = Zeile von vor
# der Kopie, einmalig mit dem damals aktuellen Kontext des Schlüssels
# gefüllt. Eine Rekonstruktion, die sagt, dass sie eine ist.
USAGE_COLUMNS = tuple((col, "TEXT NOT NULL DEFAULT ''") for col, _ in SNAPSHOT) + (
    ("ctx_source", "TEXT NOT NULL DEFAULT ''"),)


def parse_labels(text):
    """`name=wert`, getrennt durch Komma oder Zeilenumbruch → JSON-Text.

    Wirft ValueError mit einem Satz, der den Fehler behebt. Leer ist
    erlaubt und ergibt ''.
    """
    labels = {}
    for part in re.split(r"[,\n]", text or ""):
        part = part.strip()
        if not part:
            continue
        name, sep, value = part.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not LABEL_NAME.match(name):
            raise ValueError(f"Etikett „{part}“ hat nicht die Form name=wert "
                             "(Name: Buchstaben, Ziffern, _ . -, höchstens 32 Zeichen).")
        if len(value) > MAX_TEXT:
            raise ValueError(f"Der Wert von „{name}“ ist länger als {MAX_TEXT} Zeichen.")
        labels[name] = value
    if len(labels) > MAX_LABELS:
        raise ValueError(f"Höchstens {MAX_LABELS} freie Etiketten je Schlüssel, "
                         f"angegeben waren {len(labels)}.")
    return json.dumps(labels, ensure_ascii=False, sort_keys=True) if labels else ""


def labels_text(stored):
    """Die gespeicherte Form zurück in `name=wert, name=wert`."""
    try:
        doc = json.loads(stored) if stored else {}
    except ValueError:
        return ""
    return ", ".join(f"{k}={v}" for k, v in sorted(doc.items()))


def labels_of(stored):
    """Die gespeicherte Form als Wörterbuch — für Antworten an Maschinen."""
    try:
        return json.loads(stored) if stored else {}
    except ValueError:
        return {}


def clean(fields):
    """Prüft die genannten Kontextfelder; fehlende bleiben weg (für Teil-Änderungen)."""
    out = {}
    for name in CONTEXT_FIELDS:
        if name not in fields:
            continue
        if name == "labels":
            out[name] = parse_labels(fields[name])
            continue
        value = (fields[name] or "").strip()
        if len(value) > MAX_TEXT:
            raise ValueError(f"„{name}“ ist länger als {MAX_TEXT} Zeichen.")
        if name == "account":
            value = value or "default"
        out[name] = value
    return out


def snapshot_columns():
    return [col for col, _ in SNAPSHOT] + ["ctx_source"]


def snapshot_values(key):
    """Die Kopie aus der Schlüsselzeile **vom Beginn der Anfrage**, passend zu snapshot_columns()."""
    return [key[src] for _, src in SNAPSHOT] + ["snapshot"]


def backfill(db, usage_table="usage", keys_table="keys"):
    """Füllt Messzeilen ohne Kopie einmal mit dem heutigen Kontext ihres Schlüssels.

    Genauer geht es nicht: Der damalige Kontext wurde nie aufgeschrieben.
    Gibt die Zahl der betroffenen Zeilen zurück; die Prüfspur schreibt der
    Aufrufer.
    """
    sets = ",".join(
        f"{col}=COALESCE((SELECT {src} FROM {keys_table} "
        f"WHERE {keys_table}.id={usage_table}.key_id),'')" for col, src in SNAPSHOT)
    cur = db.execute(f"UPDATE {usage_table} SET {sets},ctx_source='backfilled' "
                     "WHERE ctx_source=''")
    return cur.rowcount
