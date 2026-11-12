#!/usr/bin/env python3
"""Werkzeuge (RFC-0077 §15.8.5, §15.8.8): publish.py sync und review.py.

    sync legt Release, Texte je Sprache, Kategorien und Bilder an; ein zweiter Lauf
    ändert nichts; ein Geheimnis in einem Text hält den Abgleich an (422).
    review.py ohne Modell gibt nie frei -- es eskaliert, mit Befunden.

Aufruf: python3 test_tools.py
"""
import json
import os
import struct
import sys
import tempfile
import threading
import urllib.request
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.environ["MARKET_REVIEW"] = "on"
os.environ["HOME"] = os.environ["USERPROFILE"] = tempfile.mkdtemp(prefix="market-home-")
import app as mk                                              # noqa: E402
import publish                                                # noqa: E402
import review                                                 # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


srv = mk.make_server(tempfile.mkdtemp(prefix="market-tools-"), 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


class HeaderClient(publish.Client):
    """Im Test statt des API-Schlüssels die Kopfzeilen, die die Plattform setzen würde."""

    def __init__(self, url, user, roles="user"):
        super().__init__(url, "x")
        self.user, self.roles = user, roles

    def req(self, method, path, body=None, ctype=None):
        h = {"X-OAAP-User": self.user, "X-OAAP-Roles": self.roles}
        if ctype:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.url + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, publish._json(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, publish._json(e.read())


admin = HeaderClient(BASE, "joerg", "server_admin,admin,user")
mk.M.set_category("setup-templates", "oaap", {"en": {"title": "Setup templates"},
                                              "de": {"title": "Aufbau-Vorlagen"}}, by="t")
mk.M.add_publisher("user", "ci", "handballverein", [], "t")
ci = HeaderClient(BASE, "ci")


def png(seed):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Software\x00Kamera")
            + chunk(b"IDAT", zlib.compress(b"\x00" + bytes([seed, 0, 0]))) + chunk(b"IEND", b""))


folder = tempfile.mkdtemp(prefix="tpl-")
prof = {"profile": "oaap.tenant-profile/1", "id": "handballverein", "version": "1.2.0",
        "title": "Handballverein", "description": "Vereinsmandant.",
        "steps": [{"id": "tenant", "type": "tenant.create", "label": "{label}"}]}
open(os.path.join(folder, "handballverein.json"), "w", encoding="utf-8").write(json.dumps(prof))
open(os.path.join(folder, "README.en.md"), "w", encoding="utf-8").write("# Handball club\n\nA tenant with portal.\n")
open(os.path.join(folder, "README.de.md"), "w", encoding="utf-8").write("# Handballverein\n\nEin Mandant mit Portal.\n")
for n, s in (("icon.png", 1), ("1.png", 2), ("2.png", 3)):
    open(os.path.join(folder, n), "wb").write(png(s))
market = {"asset": "handballverein", "type": "oaap-profile", "file": "handballverein.json",
          "channels": ["stable"], "categories": ["setup-templates"], "tags": ["handball", "verein"],
          "license": "proprietary", "links": [{"label": "Docs", "url": "https://example.org/hb"}],
          "texts": {"en": {"title": "Handball club", "summary": "Tenant for a handball club",
                           "description_file": "README.en.md"},
                    "de": {"title": "Handballverein", "summary": "Mandant für einen Handballverein",
                           "description_file": "README.de.md"}},
          "notes": {"en": "First release", "de": "Erste Version"},
          "images": {"icon": "icon.png", "screenshots": ["1.png", "2.png"]}}
open(os.path.join(folder, "market.json"), "w", encoding="utf-8").write(json.dumps(market))

print("publish.py sync")
good, rep = publish.sync(ci, folder)
ok("N2 erster Lauf: Release, Listing, drei Bilder", good and "release" in rep["changed"]
   and any(c.startswith("listing") for c in rep["changed"])
   and len([c for c in rep["changed"] if c.startswith("image:")]) == 3, rep)
a = mk.M.asset("handballverein")
ok("N2 Texte en und de, Beschreibung aus der Datei",
   a["texts_map"]["en"]["title"] == "Handball club" and "Ein Mandant" in a["texts_map"]["de"]["description"])
ok("N2 Kategorie, Schlagworte, Icon, zwei Screenshots",
   a["categories_list"] == ["setup-templates"] and a["tags_list"] == ["handball", "verein"]
   and a["icon"] and len(a["screenshots_list"]) == 2)
rel = mk.M.release("handballverein", "1.2.0")
ok("N2 Notizen je Sprache, Release in Prüfung", rel["notes_map"].get("de") == "Erste Version"
   and rel["review"] == "pending")
good, rep2 = publish.sync(ci, folder)
ok("N2 zweiter Lauf ändert nichts", good and rep2["changed"] == [], rep2)
os.remove(os.path.join(folder, "2.png"))
market["images"]["screenshots"] = ["1.png"]
open(os.path.join(folder, "market.json"), "w", encoding="utf-8").write(json.dumps(market))
good, rep3 = publish.sync(ci, folder)
ok("N2 ein entferntes Bild verschwindet auch im Marketplace",
   good and len(mk.M.asset("handballverein")["screenshots_list"]) == 1, rep3)
market["texts"]["en"]["summary"] = "Use oaapk_k7f3a91c_" + "Q" * 24
open(os.path.join(folder, "market.json"), "w", encoding="utf-8").write(json.dumps(market))
good, rep4 = publish.sync(ci, folder)
ok("N3 ein Schlüssel im Text: sync hält an (422), nichts geschrieben",
   not good and rep4.get("listing", {}).get("status") == 422
   and "oaapk_" not in json.dumps(mk.M.asset("handballverein")["texts_map"]), rep4)

print("\nreview.py")
mk.M.add_reviewer("user", "pruef-ki", "t")
st, d = HeaderClient(BASE, "pruef-ki").req("GET", "/api/v1/reviews?state=open")
case = next(c for c in d["cases"] if c["kind"] == "release")
v = review.judge(case, [("handballverein.json", json.dumps(prof))], None)
ok("ohne Modell: eskalieren, nie freigeben", v["verdict"] == "escalate", v)
case_bad = dict(case, texts={"de": {"title": "x", "description": "Ruf an: 0171 2345678"}})
v = review.judge(case_bad, [], None)
ok("feste Prüfung: EN fehlt, Telefonnummer gefunden",
   any("Englisch" in f["text"] for f in v["findings"]) and any("phone" in f["text"] for f in v["findings"]), v)


class FakeModel:
    pass


review.ask_model = lambda *a, **k: {"verdict": "approve", "summary": "ok",
                                    "findings": [{"severity": "block", "text": "x"}]}
v = review.judge(case, [], ("http://m", "", "m"))
ok("Modell sagt approve, aber ein Befund block: eskalieren", v["verdict"] == "escalate", v)
review.ask_model = lambda *a, **k: {"verdict": "approve", "summary": "ok", "findings": []}
v = review.judge(case, [], ("http://m", "", "m"))
ok("Modell approve ohne block: approve", v["verdict"] == "approve", v)

srv.shutdown()
print("\nALLE PRUEFUNGEN BESTANDEN" if not fails else f"\n{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
