#!/usr/bin/env python3
"""Der Store Editor an einem laufenden Knoten — im Browser, nicht im Modul.

Was hier geprüft wird, prüft `test_pages.py` nicht: dass die Anmeldung
als Gateway-Kopfzeile ankommt, dass die Seiten hinter dem Portal
erreichbar sind, dass der deklarierte Speicher wirklich beschreibbar
ist — und dass ein Formular, das der Browser abschickt, dieselben Werte
zurückbringt, die es angezeigt hat.

Der letzte Punkt ist der eigentliche Grund: Ein Feld, das beim Rendern
richtig aussieht, aber beim Absenden verlorengeht, sieht auf keiner
Seite falsch aus. Deshalb liest dieser Test das Formular so aus, wie
ein Browser es abschicken würde, ändert genau einen Wert und schaut,
was zurückkommt.

Der Entwurf, den er anlegt, wird am Ende wieder verworfen.

    python3 klicktest.py ../../../oaap-reference/test/.env http://10.10.10.75
"""
import html.parser
import http.cookiejar
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, ".env")
HOST = (sys.argv[2] if len(sys.argv) > 2 else "http://10.10.10.75").rstrip("/")
PORT = sys.argv[3] if len(sys.argv) > 3 else "8106"
BASE = f"{HOST}:{PORT}"

env = {}
with open(ENV_FILE, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
fails = []


def ok(label, cond, detail=""):
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond:
        fails.append(label)
        if detail:
            print(f"      {detail[:400]}")


def get(url):
    with op.open(url, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace"), r.geturl(), r.headers


def post(url, fields):
    """fields: Liste von (name, wert) — mehrfach vorkommende Namen erlaubt."""
    data = urllib.parse.urlencode(fields).encode()
    with op.open(url, data=data, timeout=90) as r:
        return r.status, r.read().decode("utf-8", "replace"), r.geturl()


class FormReader(html.parser.HTMLParser):
    """Liest ein Formular so aus, wie ein Browser es abschicken würde.

    Also: keine abgeschalteten Felder, keine nicht angehakten Kästchen,
    bei einer Auswahlliste der ausgewählte Eintrag. Sonst prüfte der
    Test etwas anderes als das, was am Knoten tatsächlich passiert.
    """

    def __init__(self):
        super().__init__()
        self.fields = []
        self._area = None
        self._select = None
        self._opt = None
        self._opt_text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "disabled" in a:
            if tag == "textarea":
                self._area = None
            return
        if tag == "input" and a.get("name"):
            if a.get("type") == "checkbox" and "checked" not in a:
                return
            self.fields.append((a["name"], a.get("value", "")))
        elif tag == "textarea" and a.get("name"):
            self._area = a["name"]
            self._text = ""
        elif tag == "select" and a.get("name"):
            self._select = a["name"]
            self._chosen = ""
        elif tag == "option" and self._select:
            self._opt = a.get("value", "")
            if "selected" in a:
                self._chosen = self._opt

    def handle_data(self, data):
        if self._area is not None:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "textarea" and self._area is not None:
            self.fields.append((self._area, self._text))
            self._area = None
        elif tag == "select" and self._select:
            self.fields.append((self._select, self._chosen))
            self._select = None


def read_form(body):
    r = FormReader()
    r.feed(body)
    return r.fields


print("=== Anmeldung am Portal ===")
get(f"{HOST}/auth/login")
st, body, url = post(f"{HOST}/auth/login",
                     [("username", env["OAAP_PORTAL_KLICKTEST_USER"]),
                      ("password", env["OAAP_PORTAL_KLICKTEST_PASSWORD"])])
ok("Anmeldung am Portal", "/auth/login" not in url, url)

print("\n=== die Startseite des Editors ===")
st, body, url, _ = get(f"{BASE}/")
ok("Seite kommt", st == 200, f"{st} {url}")
ok("die Anmeldung kommt als Gateway-Kopfzeile an — kein eigener Login",
   env["OAAP_PORTAL_KLICKTEST_USER"] in body and "Passwort" not in body)
ok("beide Listen stehen da",
   body.count('href="/liste/') >= 4, str(body.count('href="/liste/')))
ok("und der Speicher ist da", "Kein Speicher" not in body,
   "sonst hätte die Instanz keinen /data-Mount — Bearbeiten ginge nicht")
ok("die Seite erklärt, was ein Entwurf ist",
   "Arbeitskopie auf dieser Instanz" in body and "Bauschritt 3" in body)

idx = None
for i in (0, 1):
    st, b, _, _ = get(f"{BASE}/liste/{i}")
    if "OAAP Plattform-Apps" in b:
        idx, listbody = i, b
        break
ok("die Plattform-Liste ist erreichbar", idx is not None)
if idx is None:
    sys.exit(1)

print(f"\n=== Liste {idx}: OAAP Plattform-Apps ===")
ok("noch kein Entwurf", "Es gibt noch keinen Entwurf" in listbody)
ok("die Einträge stehen in der Tabelle",
   "store-editor" in listbody and "studio" in listbody)
ok("der Prüfer läuft mit", "Was geprüft wurde" in listbody or
   "Keine Beanstandung" in listbody)
ok("Aufnehmen ist möglich", 'action="/liste/' in listbody and
   "Eintrag aufnehmen" in listbody)

print("\n=== die Bearbeitungsseite ===")
st, form_body, url, _ = get(f"{BASE}/liste/{idx}/eintrag/store-editor")
ok("Seite kommt", st == 200, f"{st} {url}")
ok("redaktionelle Felder sind bearbeitbar",
   'name="summary"' in form_body and 'name="maturity"' in form_body)
ok("die erzeugten Felder sind verriegelt",
   'name="gen_version"' in form_body and "disabled" in form_body)
ok("und lassen sich ausdrücklich entriegeln",
   'name="entriegelt" value="version"' in form_body)
ok("das Manifest steht daneben", "Das Manifest sagt:" in form_body)
ok("die Felder ohne Quelle im Manifest sind frei",
   'name="released"' in form_body and 'name="profiles"' in form_body)

fields = read_form(form_body)
names = {n for n, _ in fields}
ok("der Browser würde die verriegelten Felder gar nicht mitschicken",
   not any(n.startswith("gen_") for n in names), str(sorted(names)))
before = dict(fields)
ok("das Formular trägt die Werte der Liste",
   before.get("summary", "").startswith("Store-Listen prüfen"), before.get("summary"))

print("\n=== speichern legt einen Entwurf an ===")
PROBE = "Klicktest — dieser Satz wird gleich wieder verworfen."
sent = [(n, PROBE if n == "summary" else v) for n, v in fields]
sent.append(("tun", "speichern"))
st, saved, url = post(f"{BASE}/liste/{idx}/eintrag/store-editor", sent)
ok("es wurde gespeichert", "Gespeichert" in saved, url)
ok("der neue Text steht im Formular", PROBE in saved)

after = dict(read_form(saved))
unchanged = [n for n in before
             if n not in ("summary",) and before[n] != after.get(n, "")]
ok("alle anderen Felder kommen unverändert zurück", not unchanged,
   "; ".join(f"{n}: {before[n]!r} -> {after.get(n)!r}" for n in unchanged))

st, listbody2, _, _ = get(f"{BASE}/liste/{idx}")
ok("die Liste zeigt jetzt einen Entwurf", "Entwurf" in listbody2)
ok("und sagt, dass er nicht veröffentlicht ist", "nicht veröffentlicht" in listbody2)

print("\n=== die Änderungsübersicht ===")
st, chg, _, _ = get(f"{BASE}/liste/{idx}/aenderungen")
ok("Seite kommt", st == 200)
ok("die Änderung steht drin", PROBE in chg)
ok("sie ist als redaktionell eingeordnet", "redaktionell" in chg)
ok("die Trennung wird begründet", "Mengenbremse" in chg)

print("\n=== die Datei ===")
st, datei, _, headers = get(f"{BASE}/liste/{idx}/datei")
ok("sie kommt als Anhang",
   "attachment" in (headers.get("Content-Disposition") or ""),
   str(headers.get("Content-Disposition")))
doc = json.loads(datei)
entry = [e for e in doc["apps"] if e["id"] == "store-editor"][0]
ok("sie enthält den bearbeiteten Stand", entry["summary"] == PROBE)
ok("und keine Buchführung des Editors",
   "overrides" not in doc and "published" not in doc)
ok("sie ist eine gültige Liste", doc.get("store") and doc.get("apps"))

print("\n=== aus den Manifesten übernehmen ===")
st, _, _ = post(f"{BASE}/liste/{idx}/uebernehmen", [])
st, listbody3, _, _ = get(f"{BASE}/liste/{idx}")
ok("es lief durch", st == 200)
st, datei2, _, _ = get(f"{BASE}/liste/{idx}/datei")
entry = [e for e in json.loads(datei2)["apps"] if e["id"] == "store-editor"][0]
ok("die redaktionelle Änderung blieb unberührt", entry["summary"] == PROBE,
   str(entry.get("summary")))
# Die erwartete Version wird aus dem Manifest gelesen und nicht
# hier eingetragen: Eine fest verdrahtete Zahl macht den Test bei
# jeder Versionsanhebung rot, ohne dass etwas kaputt wäre.
with urllib.request.urlopen(
        "https://raw.githubusercontent.com/MDJoerg/oaap-apps/main/"
        "apps/store-editor/oaap-app.yaml", timeout=30) as r:
    manifest_version = re.search(r"^\s*version:\s*(\S+)",
                                 r.read().decode("utf-8"), re.M).group(1)
ok("die Version stimmt mit dem Manifest überein",
   entry["version"] == manifest_version,
   f'Liste {entry.get("version")!r}, Manifest {manifest_version!r}')

print("\n=== eine einzelne App abgleichen ===")
st, _, _ = post(f"{BASE}/liste/{idx}/abgleich", [("id", "store-editor")])
st, datei3, _, _ = get(f"{BASE}/liste/{idx}/datei")
entry = [e for e in json.loads(datei3)["apps"] if e["id"] == "store-editor"][0]
ok("er lief für genau diesen Eintrag", entry["version"] == "0.2.1", str(entry))
ok("und hat den redaktionellen Text nicht mitgenommen",
   entry["summary"] == PROBE,
   "ein Abgleich aus der Zeile heraus darf keine Texte löschen")

print("\n=== der Nachpflege-Bericht ===")
st, bericht, _, headers = get(f"{BASE}/liste/{idx}/eintrag/store-editor/bericht")
ok("er kommt als Markdown-Datei",
   "markdown" in (headers.get("Content-Type") or "")
   and "nachpflege-store-editor.md" in (headers.get("Content-Disposition") or ""),
   str(headers.get("Content-Disposition")))
ok("er nennt den Auftrag zuerst", "**Auftrag:**" in bericht)
ok("und für die eigene App gibt es tatsächlich etwas zu tun",
   "app." in bericht, bericht[:300])
st, sammel, _, headers = get(f"{BASE}/liste/{idx}/bericht")
ok("es gibt ihn auch für die ganze Liste",
   "Manifest-Nachpflege für" in sammel and "Einträgen haben etwas offen" in sammel)
# Der Beleg fuer RFC-0014: Fast jede Zeile sagt 'das Format kennt das
# Feld nicht'. Ein Bericht, der kaum etwas einfordern kann, IST der Befund.
ok("und er belegt, was RFC-0014 behauptet",
   "kennt es noch nicht" in sammel, sammel[:400])

print("\n=== Hilfe und Gesundheit ===")
st, hilfe, _, _ = get(f"{BASE}/hilfe")
ok("die Hilfe erklärt die Markierung",
   "markiert" in hilfe and "Neuerzeugung" in hilfe)
st, hz, _, _ = get(f"{BASE}/healthz")
ok("Gesundheitspfad antwortet", st == 200 and hz.strip() == "ok")

print("\n=== aufräumen: den Entwurf verwerfen ===")
post(f"{BASE}/liste/{idx}/verwerfen", [])
st, listbody4, _, _ = get(f"{BASE}/liste/{idx}")
ok("der Entwurf ist weg", "Es gibt noch keinen Entwurf" in listbody4)
st, back, _, _ = get(f"{BASE}/liste/{idx}/eintrag/store-editor")
ok("und es gilt wieder der veröffentlichte Stand", PROBE not in back)

print(f"\n{'ALLE PRUEFUNGEN BESTANDEN' if not fails else str(len(fails)) + ' FEHLER'}")
for f in fails:
    print(f"  - {f}")
sys.exit(1 if fails else 0)
