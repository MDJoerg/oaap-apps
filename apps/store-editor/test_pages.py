#!/usr/bin/env python3
"""Die Seiten des Store Editors — ohne Netz, ohne Docker, ohne Knoten.

Warum es diesen Test gibt: Beim Bau der Launchpad-Regel im Portal hat
ein Zeilenumbruch in einer Vorlage einen Satz zerrissen, den der
Klicktest am echten Knoten suchte — gefunden hat es erst der Test, der
die Seite lokal rendert. Dieselbe Vorsorge hier. Er prüft außerdem den
ganzen Weg eines Formulars: Werte hinein, Arbeitskopie heraus.

Abgerufen wird nichts: `fetch` wird ersetzt.

    python3 test_pages.py
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app  # noqa: E402
import editor as ed  # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {detail[:400]}")


LIST_URL = "https://example.invalid/oaap-store.json"
MANIFEST_URL = ("https://raw.githubusercontent.com/MDJoerg/oaap-store/"
                "main/apps/ollama/oaap-app.yaml")

PUBLISHED = {
    "store": "0.2", "id": "oaap.test", "name": "Testliste",
    "apps": [{
        "id": "ollama", "name": "Ollama", "type": "wrapped", "version": "0.9.0",
        "app_class": "frontend", "roles": ["admin", "keyuser"],
        "summary": "Sprachmodelle lokal.", "maturity": "beta", "status": "active",
        "categories": ["ai"], "links": [{"rel": "source", "url": "https://a"}],
        "package": {"git": "https://github.com/MDJoerg/oaap-store",
                    "path": "apps/ollama"},
    }],
}
MANIFEST_YAML = """
oaap_manifest: "0.2"
app:
  id: ollama
  name: Ollama
  version: 0.9.1
  type: wrapped
  class: service
routes:
  - path: /
    roles: [keyuser, admin]
"""


def fake_fetch(url, headers=None):
    if url == LIST_URL:
        return json.dumps(PUBLISHED)
    if url == MANIFEST_URL:
        return MANIFEST_YAML
    raise OSError(f"im Test nicht erreichbar: {url}")


class Fake(app.Handler):
    """Die Seiten ohne Netzwerkverbindung — nur die Antwort wird gemerkt."""

    def __init__(self):
        self.body = ""
        self.status = 200
        self.location = ""
        self.headers_sent = {}

    def send_html(self, body, status=200):
        self.body, self.status = body, status

    def redirect(self, target):
        self.location = target

    def send_response(self, code, *a):
        self.status = code

    def send_header(self, key, value):
        self.headers_sent[key] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        return self

    def write(self, data):
        self.body = data.decode("utf-8")


TMP = tempfile.mkdtemp(prefix="store-editor-test-")
app.fetch = fake_fetch
app.LISTS = [LIST_URL]
app.DATA_DIR = TMP

# Eine Quelle ohne Zugangsdaten — der oeffentliche Normalfall.
SRC = {"url": LIST_URL, "name": "", "token": 0}

try:
    print("=== der veröffentlichte Stand, noch ohne Entwurf ===")
    h = Fake()
    body = h.list_page("0", "klicktest", "keyuser")
    ok("die Liste wird angezeigt", "Testliste" in body)
    ok("ihr einziger Eintrag steht in der Tabelle", ">Ollama<" in body)
    ok("und der Prüfer meldet den Widerspruch zur Klasse",
       "Befund" in body, "Liste: frontend, Manifest: service")
    ok("ohne Entwurf steht das auch da",
       "Es gibt noch keinen Entwurf" in body)

    print("\n=== ein Abgleich ohne Fund legt keinen Entwurf an ===")
    # Sonst steht nach einem blossen „wie ist der Stand?" ein Entwurf mit
    # null Aenderungen da, und das Wort verliert seine Bedeutung.
    IDENT = {**PUBLISHED, "apps": [{**PUBLISHED["apps"][0], "version": "0.9.1",
                                    "app_class": "service"}]}
    _echt = fake_fetch

    def nur_ident(url, headers=None):
        return json.dumps(IDENT) if url == LIST_URL else _echt(url, headers)

    app.fetch = nur_ident
    Fake().regenerate_all("0", SRC)
    ok("nichts zu holen, also kein Entwurf", app.load_work(LIST_URL) is None,
       str(app.load_work(LIST_URL)))
    Fake().sync_entry("0", SRC, "ollama", "klicktest", "keyuser")
    ok("auch nicht beim Abgleich einer einzelnen App",
       app.load_work(LIST_URL) is None)
    app.fetch = _echt

    print("\n=== die Bearbeitungsseite ===")
    body = h.entry_page("0", "ollama", "klicktest", "keyuser")
    ok("die redaktionellen Felder sind da",
       'name="summary"' in body and 'name="description"' in body
       and 'name="maturity"' in body)
    ok("die erzeugten Felder stehen verriegelt da",
       'name="gen_version"' in body and "disabled" in body)
    ok("und lassen sich ausdrücklich entriegeln",
       'name="entriegelt" value="version"' in body)
    # Die Formulierung muss zusammenhängen: Ein Zeilenumbruch mittendrin
    # macht sie am echten Knoten unauffindbar — genau der Fehler, der im
    # Portal schon einmal passiert ist.
    ok("die Erklärung zum Entriegeln steht in einem Stück",
       "abweichend pflegen" in body)
    ok("das Manifest steht neben jedem erzeugten Feld",
       "Das Manifest sagt:" in body)
    ok("die Felder ohne Quelle sind frei bearbeitbar",
       'name="released"' in body and 'name="icon"' in body
       and "obwohl §1.3 sie" in body)
    ok("ein bekannter Verweis hat sein eigenes Feld",
       'name="link_source"' in body and 'value="https://a"' in body)

    print("\n=== speichern legt einen Entwurf an ===")
    h = Fake()
    h.save_entry("0", SRC, "ollama",
                 {"tun": ["speichern"], "summary": ["Neu getextet."],
                  "description": [""], "categories": ["ai", "automation"],
                  "audience": ["operator"], "tags": ["ki, lokal"],
                  "maturity": ["stable"], "status": ["active"], "license": [""],
                  "link_source": ["https://a"], "links_rest": [""],
                  "screenshots": [""], "released": ["2026-08-09"],
                  "profiles": [""], "icon": [""],
                  "pkg_git": ["https://github.com/MDJoerg/oaap-store"],
                  "pkg_path": ["apps/ollama"], "pkg_ref": [""]},
                 "klicktest", "keyuser")
    work = app.load_work(LIST_URL)
    entry = ed.entry_by_id(work["doc"], "ollama")
    ok("der Entwurf liegt im Speicher", work is not None)
    ok("der neue Text ist drin", entry["summary"] == "Neu getextet.")
    ok("die Kategorien auch", entry["categories"] == ["ai", "automation"])
    ok("Schlagwörter wurden zerlegt", entry["tags"] == ["ki", "lokal"])
    ok("ein leeres Feld wurde weggelassen statt leer behauptet",
       "description" not in entry and "license" not in entry, str(entry))
    ok("der veröffentlichte Stand bleibt als Vergleich daneben stehen",
       work["published"]["apps"][0]["summary"] == "Sprachmodelle lokal.")
    ok("nichts wurde veröffentlicht — es ist eine Datei auf dieser Instanz",
       os.path.isfile(app.work_path(LIST_URL)))

    print("\n=== der Prüfer schaut ab jetzt auf den Entwurf ===")
    doc, w, err = app.current(LIST_URL)
    ok("nicht mehr auf die Veröffentlichung",
       w is not None and doc["apps"][0]["summary"] == "Neu getextet.")

    print("\n=== aus dem Manifest übernehmen ===")
    h = Fake()
    h.regenerate_all("0", SRC)
    entry = ed.entry_by_id(app.load_work(LIST_URL)["doc"], "ollama")
    ok("die Version wird nachgezogen", entry["version"] == "0.9.1", str(entry))
    ok("und der Widerspruch zur Klasse ist weg",
       entry["app_class"] == "service", str(entry))
    ok("die redaktionelle Änderung bleibt unberührt",
       entry["summary"] == "Neu getextet.")

    print("\n=== die markierte Übersteuerung ===")
    h = Fake()
    h.save_entry("0", SRC, "ollama",
                 {"tun": ["speichern"], "summary": ["Neu getextet."],
                  "entriegelt": ["name"], "gen_name": ["Ollama (bei uns)"],
                  "categories": ["ai", "automation"], "audience": ["operator"],
                  "tags": ["ki, lokal"], "maturity": ["stable"],
                  "status": ["active"], "link_source": ["https://a"],
                  "released": ["2026-08-09"],
                  "pkg_git": ["https://github.com/MDJoerg/oaap-store"],
                  "pkg_path": ["apps/ollama"]},
                 "klicktest", "keyuser")
    work = app.load_work(LIST_URL)
    ok("die Abweichung ist markiert", work["overrides"]["ollama"] == ["name"], str(work))
    h = Fake()
    h.regenerate_all("0", SRC)
    entry = ed.entry_by_id(app.load_work(LIST_URL)["doc"], "ollama")
    ok("und überlebt die nächste Neuerzeugung",
       entry["name"] == "Ollama (bei uns)",
       "sonst nähme die Neuerzeugung eine bewusste Entscheidung "
       "stillschweigend zurück (RFC-0012 §1.3)")
    body = Fake().entry_page("0", "ollama", "klicktest", "keyuser")
    ok("die Seite zeigt sie als übersteuert an", "übersteuert" in body)

    print("\n=== die Übersicht der Änderungen ===")
    body = Fake().changes_page("0", "klicktest", "keyuser")
    ok("redaktionell und erzeugt werden getrennt gezählt",
       "redaktionell" in body and "aus dem Manifest" in body)
    ok("und die Trennung wird begründet",
       "Mengenbremse" in body,
       "RFC-0013 Frage 5: Neuerzeugung zählt für die Bremse nicht mit")
    changes = ed.diff_documents(app.load_work(LIST_URL)["published"],
                                app.load_work(LIST_URL)["doc"])
    c = ed.count_kinds(changes)
    ok("die Zählung stimmt mit den echten Änderungen überein",
       c[ed.ERZEUGT] >= 1 and c[ed.REDAKTIONELL] >= 1 and c[ed.STRUKTUR] == 0, str(c))

    print("\n=== einen Eintrag aufnehmen, bevor es sein Manifest gibt ===")
    h = Fake()
    h.add_entry("0", SRC, {"id": ["bdt-hub"],
                                "git": ["https://github.com/MDJoerg/bdt-hub"],
                                "path": ["apps/hub"], "ref": [""]},
                "klicktest", "keyuser")
    doc = app.load_work(LIST_URL)["doc"]
    ok("er steht in der Arbeitskopie", ed.entry_by_id(doc, "bdt-hub") is not None)
    ok("und der Prüfer meldet ihn als ohne Beleg — statt ihn abzulehnen",
       "Beleg" in Fake().list_page("0", "klicktest", "keyuser"),
       "RFC-0013 Frage 4")
    h = Fake()
    h.add_entry("0", SRC, {"id": ["bdt-hub"], "git": ["https://x/y"]},
                "klicktest", "keyuser")
    ok("dieselbe Kennung zweimal wird abgelehnt", "schon" in h.body)
    h = Fake()
    h.add_entry("0", SRC, {"id": ["neu-x"], "git": ["http://unsicher/y"]},
                "klicktest", "keyuser")
    ok("und ein Paket ohne https ebenso", "https://" in h.body and "muss" in h.body)

    print("\n=== die Datei ===")
    h = Fake()
    h.download("0", "klicktest", "keyuser")
    ok("sie kommt als Anhang",
       "attachment" in h.headers_sent.get("Content-Disposition", ""),
       str(h.headers_sent))
    ok("sie heißt nach der Kennung der Liste",
       "oaap.test.json" in h.headers_sent.get("Content-Disposition", ""))
    out = json.loads(h.body)
    ok("sie enthält den Entwurf, nicht die Veröffentlichung",
       out["apps"][0]["version"] == "0.9.1")
    ok("und keine Buchführung des Editors",
       "overrides" not in out and "published" not in out,
       "eine Liste ist ein Dokument nach dem Schema — die Markierungen "
       "gehören in den Editor, nicht auf fremde Knoten")

    print("\n=== der Prüfer ist der Wächter, auch beim Herunterladen ===")
    work = app.load_work(LIST_URL)
    work["doc"]["apps"].append({"id": "kaputt", "name": "", "version": "keine",
                                "package": {}})
    app.save_work(LIST_URL, work)
    h = Fake()
    h.download("0", "klicktest", "keyuser")
    ok("eine strukturell kaputte Liste gibt es nicht als Datei",
       "nicht benutzbar" in h.body and "attachment" not in
       h.headers_sent.get("Content-Disposition", ""), h.body[:200])

    print("\n=== eine einzelne App abgleichen ===")
    # Eigener Weg, nicht der Speichern-Knopf: Ein Abgleich von der
    # Listenseite aus darf die redaktionellen Texte nicht mitnehmen.
    work = app.load_work(LIST_URL)
    ed.entry_by_id(work["doc"], "ollama")["version"] = "0.0.1"
    ed.entry_by_id(work["doc"], "ollama")["summary"] = "Bleibt stehen."
    app.save_work(LIST_URL, work)
    h = Fake()
    h.sync_entry("0", SRC, "ollama", "klicktest", "keyuser")
    entry = ed.entry_by_id(app.load_work(LIST_URL)["doc"], "ollama")
    ok("die Version wird nachgezogen", entry["version"] == "0.9.1", str(entry))
    ok("der redaktionelle Text bleibt unangetastet",
       entry["summary"] == "Bleibt stehen.",
       "sonst würde ein Abgleich aus der Zeile heraus Texte löschen")
    h = Fake()
    h.sync_entry("0", SRC, "bdt-hub", "klicktest", "keyuser")
    ok("ohne abrufbares Manifest passiert nichts, und es steht dran",
       "kein_manifest" in h.location, h.location)

    print("\n=== der Nachpflege-Bericht ===")
    h = Fake()
    h.report("0", "ollama", "klicktest", "keyuser")
    ok("er kommt als Markdown-Datei",
       "markdown" in h.headers_sent.get("Content-Type", "")
       and "nachpflege-ollama.md" in h.headers_sent.get("Content-Disposition", ""),
       str(h.headers_sent))
    ok("er nennt die App und den Auftrag",
       "Manifest-Nachpflege: Ollama" in h.body and "**Auftrag:**" in h.body)
    h = Fake()
    h.report("0", "", "klicktest", "keyuser")
    ok("und es gibt ihn für die ganze Liste",
       "nachpflege-oaap.test.md" in h.headers_sent.get("Content-Disposition", "")
       and "von 3 Einträgen" in h.body, h.body[:300])
    ok("der Eintrag ohne abrufbares Manifest steht als ohne Beleg drin",
       "ohne Beleg" in h.body)

    print("\n=== Quellen: Listen aufnehmen und Zugangsdaten ===")

    class Rolle(Fake):
        def __init__(self, roles, form=None):
            super().__init__()
            self._roles, self._form = set(roles), form or {}

        def role_set(self):
            return self._roles

        def form(self):
            return self._form

    app.save_sources([{"url": LIST_URL, "name": "Testliste", "token": 0}])
    app.TOKENS = ["", "", ""]
    body = Rolle({"keyuser"}).sources_page("klicktest", "keyuser")
    ok("die Seite zeigt die eingetragene Liste", "Testliste" in body)
    ok("ein keyuser darf aufnehmen", "Liste aufnehmen" in body)
    ok("und sie sagt, wo die Schlüssel liegen und warum nicht hier",
       "server_admin" in body and "keine eigene" in body)
    nur_user = Rolle({"user"}).sources_page("klicktest", "user")
    ok("ein user darf es nicht", "Liste aufnehmen" not in nur_user
       and "Einrichtung" in nur_user)

    h = Rolle({"user"}, {"url": ["https://x.invalid/l.json"]})
    h.add_source("klicktest", "user")
    ok("und wird abgewiesen, nicht stillschweigend ignoriert",
       h.status == 403, str(h.status))
    ok("die Quelle wurde nicht angelegt", len(app.load_sources()) == 1)

    # Der Fall, fuer den es die Umschreibung gibt: Was im Browser in der
    # Adresszeile steht, ist eine HTML-Seite und keine JSON-Datei.
    h = Rolle({"keyuser"}, {
        "url": ["https://github.com/MDJoerg/bdt/blob/main/oaap-store.json"],
        "name": ["BDT"], "token": ["1"]})
    h.add_source("klicktest", "keyuser")
    neu = app.load_sources()[-1]
    ok("eine Browser-Adresse wird beim Aufnehmen umgeschrieben",
       neu["url"] == "https://raw.githubusercontent.com/MDJoerg/bdt/main/"
                     "oaap-store.json", neu["url"])
    ok("der Zugangsdaten-Platz wird gemerkt", neu["token"] == 1)
    ok("und es steht dran, dass sie noch nicht abrufbar war",
       "quelle_stumm" in h.location, h.location)
    body = Rolle({"keyuser"}).sources_page("klicktest", "keyuser")
    ok("ein Platz ohne hinterlegten Schlüssel wird als leer gezeigt",
       "Platz 1 leer" in body, body[body.find("BDT"):][:400])

    app.TOKENS = ["ghp_geheim", "", ""]
    body = Rolle({"keyuser"}).sources_page("klicktest", "keyuser")
    ok("mit Schlüssel meldet sie ihn als hinterlegt",
       "Schlüssel ist hinterlegt" in body)
    # Der Wert selbst darf NIE auf einer Seite auftauchen.
    ok("der Schlüssel selbst steht nirgends auf der Seite",
       "ghp_geheim" not in body,
       "secret heißt: eintragbar, nie zurücklesbar — auch nicht hier")
    ok("und der Editor gibt ihn für diese Quelle heraus",
       app.token_of(app.load_sources()[-1]) == "ghp_geheim")
    ok("für eine öffentliche Quelle nicht",
       app.token_of(app.load_sources()[0]) == "")

    h = Rolle({"keyuser"}, {"i": ["1"]})
    h.remove_source("klicktest", "keyuser")
    ok("entfernen geht", len(app.load_sources()) == 1)
    app.TOKENS = ["", "", ""]

    print("\n=== den Entwurf verwerfen ===")
    app.drop_work(LIST_URL)
    doc, w, err = app.current(LIST_URL)
    ok("danach gilt wieder der veröffentlichte Stand",
       w is None and doc["apps"][0]["version"] == "0.9.0")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILURES'}")
sys.exit(1 if fails else 0)
