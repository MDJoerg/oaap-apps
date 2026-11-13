"""Kohorten (0.6): zeitlich begrenzte Schlüssel für die Teilnehmer einer Schulung.

Der Begriff ist derselbe wie für die Schulungsserver (RFC-0046): Eine
**Kohorte** ist ein Kurs auf Zeit, ein **Sitz** ein Teilnehmer mit einer
Nummer. Die Etiketten folgen derselben Form, `<kohorte>-tn-<kennung>`
(`kurs-2026-11-tn-07`), damit Server und Schlüssel eines Teilnehmers
dieselbe Kennung tragen.

Dieses Modul rechnet nur: eine eingefügte Tabelle lesen, Kennungen
vergeben, die Tabelle mit einer Spalte für den Schlüssel zurückgeben.
Ausgestellt wird in `store.issue` — jeder Schlüssel einzeln, wie jeder
andere, mit Ablaufzeit.

Eingabe: je Zeile ein Teilnehmer, Spalten durch Tab, Semikolon oder
Komma getrennt (aus Excel kopiert ist es Tab). Mindestens eine Spalte,
der Name. Steht in der zweiten Spalte eine Kennung (`07`, `7`, `07b`),
gilt sie; sonst wird fortlaufend nummeriert. Weitere Spalten (E-Mail,
Firma, …) werden nur durchgereicht. Eine Überschriftenzeile wird erkannt
und mit zwei Spalten ergänzt zurückgegeben.

Ein widerrufener Schlüssel behält sein Etikett — es kann nie ein zweites
Mal vergeben werden (gwcore/ledger.py). Wer für einen Teilnehmer
nachgeneriert, gibt deshalb eine **abweichende Kennung** an (`07b`) oder
lässt das Gateway die nächste freie nehmen.
"""
import csv
import io
import re

COHORT_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
COHORT_RULE = ("3 bis 40 Zeichen, Kleinbuchstaben, Ziffern und Bindestrich, z. B. "
               "kurs-2026-11")
KENNUNG = re.compile(r"^(\d{1,2})([a-z]?)$")
SEAT = "tn"
MAX_ROWS = 100
HEADER_WORDS = {"name", "namen", "teilnehmer", "teilnehmerin", "teilnehmende", "vorname",
                "nachname", "nr", "nr.", "nummer", "kennung", "e-mail", "email", "mail",
                "firma", "participant", "number", "id"}


def label_of(cohort, kennung):
    return f"{cohort}-{SEAT}-{kennung}"


def normalize_kennung(text):
    """`7` → `07`, `07B` → `07b`; None, wenn es keine Kennung ist."""
    m = KENNUNG.match((text or "").strip().lower())
    if not m:
        return None
    return f"{int(m.group(1)):02d}{m.group(2)}"


def detect_sep(text):
    """Tab vor Semikolon vor Komma — Excel kopiert mit Tab, deutsches CSV mit Semikolon."""
    for sep in ("\t", ";", ","):
        if sep in text:
            return sep
    return None


def parse(text):
    """(sep, header, rows) — rows ohne Leerzeilen, jede Zeile eine Liste von Zellen."""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    sep = detect_sep(text)
    if sep:
        rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=sep)]
    else:
        rows = [[line.strip()] for line in text.split("\n")]
    rows = [r for r in rows if any(c for c in r)]
    header = None
    if rows and any(c.strip().lower() in HEADER_WORDS for c in rows[0]) \
            and not (len(rows[0]) > 1 and normalize_kennung(rows[0][1])):
        header, rows = rows[0], rows[1:]
    return sep, header, rows


def plan(rows, cohort, taken, first=1, auto_free=False):
    """Kennung und Etikett je Zeile; `taken` sind schon vergebene Etiketten.

    Gibt (plan, generated) zurück: plan ist je Zeile ein dict mit name,
    kennung, label, cells, error; generated sagt, ob mindestens eine
    Kennung vom Gateway stammt oder geändert wurde -- dann bekommt die Ausgabe
    eine Spalte mit der tatsächlichen Kennung jeder Zeile.
    """
    used = set(taken)
    out, generated = [], False
    counter = max(0, int(first))

    def next_free_number():
        nonlocal counter
        while counter <= 99:
            k = f"{counter:02d}"
            counter += 1
            if label_of(cohort, k) not in used:
                return k
        return None

    def next_free_suffix(k):
        base = k.rstrip("abcdefghijklmnopqrstuvwxyz")
        for ch in "bcdefghijklmnopqrstuvwxyz":
            if label_of(cohort, base + ch) not in used:
                return base + ch
        return None

    for cells in rows:
        name = cells[0].strip() if cells else ""
        explicit = normalize_kennung(cells[1]) if len(cells) > 1 else None
        item = {"name": name, "cells": cells, "kennung": "", "label": "", "error": "",
                "explicit": bool(explicit)}
        if not name:
            item["error"] = "ohne Namen"
        elif len(out) >= MAX_ROWS:
            item["error"] = f"mehr als {MAX_ROWS} Zeilen — eine Kohorte hat die Kennungen 00 bis 99"
        elif explicit:
            k = explicit
            if label_of(cohort, k) in used:
                if auto_free:
                    k = next_free_suffix(k)
                    if k is None:
                        item["error"] = f"Kennung {explicit} und alle Varianten sind vergeben"
                else:
                    item["error"] = (f"Kennung {explicit} ist schon vergeben — eine abweichende "
                                     f"angeben (z. B. {next_free_suffix(explicit) or explicit + 'b'}) "
                                     f"oder „nächste freie nehmen“ wählen")
            if not item["error"]:
                item["kennung"] = k
                generated = generated or k != explicit
        else:
            k = next_free_number()
            if k is None:
                item["error"] = "keine freie Nummer mehr (00 bis 99 vergeben)"
            else:
                item["kennung"], generated = k, True
        if item["kennung"]:
            item["label"] = label_of(cohort, item["kennung"])
            used.add(item["label"])
        out.append(item)
    return out, generated


def output(sep, header, planned, keys, generated):
    """Die eingefügte Tabelle, ergänzt um Kennung (wenn vergeben) und Schlüssel.

    `keys` bildet Etikett → Schlüsselwert ab; eine Zeile ohne Schlüssel
    bekommt ihren Grund in die Schlüsselspalte, damit nichts still fehlt.
    """
    sep = sep or "\t"
    width = max([len(header or [])] + [len(p["cells"]) for p in planned] + [1])
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=sep, lineterminator="\n")
    if header:
        writer.writerow(list(header) + [""] * (width - len(header))
                        + (["Kennung"] if generated else []) + ["Schlüssel"])
    for p in planned:
        cells = list(p["cells"]) + [""] * (width - len(p["cells"]))
        if generated:
            cells.append(p["kennung"])
        cells.append(keys.get(p["label"]) or f"(nicht ausgestellt: {p['error'] or 'Fehler'})")
        writer.writerow(cells)
    return buf.getvalue()
