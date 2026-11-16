#!/usr/bin/env python3
"""Paketkatalog: Aufnahme, Freigabe, Liste, Zugriff (RFC-0050 Stufe 1).

Geprueft wird, was am ehesten still schiefginge:

    - ein Paket, das der Knoten ablehnen wuerde, wird schon beim Hochladen
      abgelehnt (Ausbruch aus der Paketwurzel, Verknuepfung, kein Manifest)
    - dieselbe Version zweimal ueberschreibt nie etwas
    - "hoechste freigegebene Version" ist Zahlen- und nicht Textordnung
      (1.2.10 vor 1.2.9, 1.0.0 vor 1.0.0-rc1) und enthaelt nie Unfreigegebenes
    - eine freigegebene Version loeschen verlangt einen Grund, der bleibt
    - die Liste, die die App schreibt, ist genau das, was der KNOTEN liest
      (`catalog_source.read_list`) und an einer Kopie prueft (`stage`)
    - nur der Betreiber kommt an Liste, Upload, Freigabe und Download;
      eine Anfrage von fremder Seite wird abgelehnt

Braucht kein Docker. Aufruf: python3 test_package_catalog.py
"""
import hashlib
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import app as appmod                                           # noqa: E402
import catalog as cat                                          # noqa: E402
import zipinfo                                                 # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:500]}")


def refuses(label, fn, needle=""):
    try:
        fn()
    except (zipinfo.PackageError, cat.CatalogError) as e:
        ok(label, needle.lower() in str(e).lower(), str(e))
        return
    except Exception as e:                                   # noqa: BLE001
        ok(label, False, f"{type(e).__name__}: {e}")
        return
    ok(label, False, "keine Ablehnung")


MANIFEST = """oaap_manifest: "0.2"
app:
  id: {id}
  name: {name}
  version: {version}
  type: native
  description: "Beschreibung von {id}"
services:
  web:
    build: .
    port: 80
"""
TMP = tempfile.mkdtemp(prefix="oaap-catalog-app-")


def zip_bytes(entries, symlink=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for n, d in entries.items():
            z.writestr(n, d)
        if symlink:
            zi = zipfile.ZipInfo(symlink)
            zi.external_attr = (0o120777 << 16)
            z.writestr(zi, "/etc/passwd")
    return buf.getvalue()


def pkg(app_id="website", version="0.1.0", **kw):
    return zip_bytes({"oaap-app.yaml": MANIFEST.format(
        id=app_id, name=kw.get("name", app_id.title()), version=version),
        "Dockerfile": "FROM scratch\n"})


def to_file(data):
    fd, p = tempfile.mkstemp(dir=TMP, suffix=".zip")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return p


print("=== was aufgenommen wird ===")
info = zipinfo.read(to_file(pkg("website", "0.1.2")), 10 ** 8)
ok("Manifest gelesen: Id, Name, Version, Beschreibung",
   info["app"]["id"] == "website" and info["app"]["version"] == "0.1.2"
   and info["app"]["name"] == "Website" and "Beschreibung" in info["app"]["description"])
ok("Pruefsumme und Groesse stammen aus der Datei",
   info["sha256"] == hashlib.sha256(pkg("website", "0.1.2")).hexdigest()
   and info["bytes"] == len(pkg("website", "0.1.2")))
sub = zip_bytes({"projekt/oaap-app.yaml": MANIFEST.format(id="sub", name="S", version="1.0.0")})
ok("ein einzelner Oberordner ist die Paketwurzel (wie beim Knoten)",
   zipinfo.read(to_file(sub), 10 ** 8)["app"]["id"] == "sub")
refuses("zwei Oberordner: kein Paket",
        lambda: zipinfo.read(to_file(zip_bytes({
            "a/oaap-app.yaml": "x", "b/oaap-app.yaml": "y"})), 10 ** 8), "kein oaap-app.yaml")
refuses("ohne Manifest", lambda: zipinfo.read(to_file(zip_bytes({"x.txt": "hi"})),
                                              10 ** 8), "kein oaap-app.yaml")
refuses("kein ZIP", lambda: zipinfo.read(to_file(b"das ist kein zip"), 10 ** 8), "kein zip")
refuses("leere Datei", lambda: zipinfo.read(to_file(b""), 10 ** 8), "leer")
refuses("zu gross", lambda: zipinfo.read(to_file(pkg()), 100), "erlaubt sind")
refuses("Ausbruch aus der Paketwurzel (..)",
        lambda: zipinfo.read(to_file(zip_bytes({
            "oaap-app.yaml": MANIFEST.format(id="x1", name="x", version="1.0.0"),
            "../draussen.txt": "x"})), 10 ** 8), "ablehnen")
refuses("absoluter Pfad",
        lambda: zipinfo.read(to_file(zip_bytes({
            "oaap-app.yaml": MANIFEST.format(id="x1", name="x", version="1.0.0"),
            "/etc/x": "x"})), 10 ** 8), "ablehnen")
refuses("symbolische Verknuepfung",
        lambda: zipinfo.read(to_file(zip_bytes({
            "oaap-app.yaml": MANIFEST.format(id="x1", name="x", version="1.0.0")},
            symlink="link")), 10 ** 8), "ablehnen")
refuses("Manifest ohne app-Abschnitt",
        lambda: zipinfo.read(to_file(zip_bytes({"oaap-app.yaml": "foo: 1\n"})), 10 ** 8), "app")
refuses("ungueltige App-Id",
        lambda: zipinfo.read(to_file(zip_bytes({"oaap-app.yaml": MANIFEST.format(
            id="Gross_Id", name="x", version="1.0.0")})), 10 ** 8), "app.id")
refuses("Version ist keine Version",
        lambda: zipinfo.read(to_file(zip_bytes({"oaap-app.yaml": MANIFEST.format(
            id="abc", name="x", version="neu")})), 10 ** 8), "app.version")
refuses("kaputtes YAML",
        lambda: zipinfo.read(to_file(zip_bytes({"oaap-app.yaml": "a: [1, 2\n"})), 10 ** 8),
        "nicht lesbar")

print("\n=== der Katalog ===")
DATA = tempfile.mkdtemp(prefix="oaap-catalog-data-")
C = cat.Catalog(DATA)
ok("ein leerer Katalog schreibt eine leere Liste",
   json.load(open(os.path.join(DATA, "store.json")))["apps"] == [])
for v in ("1.2.9", "1.2.10", "1.0.0-rc1", "1.0.0"):
    C.add(to_file(pkg("website", v)), 10 ** 8, "betreiber", f"Notiz {v}")
refuses("dieselbe Version noch einmal: nie ueberschrieben",
        lambda: C.add(to_file(pkg("website", "1.2.9")), 10 ** 8, "betreiber"),
        "gibt es schon")
ok("hochgeladen heisst NICHT freigegeben: die Liste ist leer",
   json.load(open(os.path.join(DATA, "store.json")))["apps"] == [])
C.set_released("website", "1.2.9", True, "betreiber")
C.set_released("website", "1.2.10", True, "betreiber")
lst = json.load(open(os.path.join(DATA, "store.json")))
ok("hoechste FREIGEGEBENE Version nach Zahlen: 1.2.10 vor 1.2.9",
   [a["version"] for a in lst["apps"]] == ["1.2.10"], lst)
C.set_released("website", "1.0.0", True, "betreiber")
C.set_released("website", "1.0.0-rc1", True, "betreiber")
ok("eine Vorabversion schlaegt die Version davor nicht (1.0.0 > 1.0.0-rc1), "
   "aber 1.2.10 bleibt vorn",
   cat.version_key("1.0.0") > cat.version_key("1.0.0-rc1")
   and cat.version_key("1.2.10") > cat.version_key("1.2.9")
   and [a["version"] for a in json.load(open(os.path.join(DATA, "store.json")))["apps"]]
   == ["1.2.10"])
C.set_released("website", "1.2.10", False, "betreiber")
ok("zurueckgezogen: die Liste faellt auf 1.2.9 zurueck",
   [a["version"] for a in json.load(open(os.path.join(DATA, "store.json")))["apps"]]
   == ["1.2.9"])
refuses("eine unbekannte Version freigeben", lambda: C.set_released(
    "website", "9.9.9", True, "betreiber"), "gibt es nicht")
rc = C.get("website", "1.0.0-rc1")
C.set_released("website", "1.0.0-rc1", False, "betreiber")
C.delete("website", "1.0.0-rc1", "", "betreiber")
ok("eine nicht (mehr) freigegebene Version darf ohne Grund gehen, die "
   "Datei ist weg",
   C.get("website", "1.0.0-rc1") is None and not os.path.exists(os.path.join(
       DATA, *rc["file"].split("/"))))
refuses("eine freigegebene Version loeschen: ohne Grund nicht",
        lambda: C.delete("website", "1.2.9", "  ", "betreiber"), "grund")
C.delete("website", "1.2.9", "enthielt ein Geheimnis", "betreiber")
ok("mit Grund geht es, und der Grund bleibt im Protokoll",
   any(e["action"] == "delete" and e["reason"] == "enthielt ein Geheimnis"
       and e["was_released"] for e in C.state["audit"]))
ok("die Liste faellt wieder zurueck (1.0.0)",
   [a["version"] for a in json.load(open(os.path.join(DATA, "store.json")))["apps"]]
   == ["1.0.0"])
C2 = cat.Catalog(DATA)
ok("ein Neustart liest den Zustand wieder (Versionen, Freigabe)",
   C2.get("website", "1.0.0")["released"] and C2.get("website", "1.2.10")
   and not C2.get("website", "1.2.10")["released"])
ok("keine liegengebliebene Zwischendatei",
   not [f for f in os.listdir(DATA) if f.endswith(".tmp")], os.listdir(DATA))

print("\n=== die Liste ist das, was der Knoten liest ===")
NODE = os.path.join(HERE, "..", "..", "..", "oaap-reference", "platform", "services")
if os.path.isdir(NODE):
    sys.path.insert(0, NODE)
    import catalog_source as node                              # noqa: E402
    C3 = cat.Catalog(tempfile.mkdtemp(prefix="oaap-catalog-node-"))
    for aid, v in (("website", "0.1.2"), ("vereinsportal", "0.0.9")):
        C3.add(to_file(pkg(aid, v)), 10 ** 8, "betreiber")
        C3.set_released(aid, v, True, "betreiber")
    doc = node.read_list(C3.dir)
    ok("der Knoten liest beide Eintraege, keiner wird als unbrauchbar verworfen",
       sorted(a["id"] for a in doc["apps"]) == ["vereinsportal", "website"], doc)
    stage = tempfile.mkdtemp(prefix="oaap-catalog-stage-")
    p = node.stage(C3.dir, doc["apps"][0], stage, 10 ** 9)
    ok("... und prueft die Kopie des Pakets gegen Groesse und Pruefsumme",
       node.sha256_file(p) == doc["apps"][0]["package"]["sha256"])
    C3.set_visible("website", ["pxx", "bec"], "betreiber")
    doc = node.read_list(C3.dir)
    web = next(a for a in doc["apps"] if a["id"] == "website")
    ok("0.4: visible_to geht in die Liste und der Knoten liest es mit",
       web.get("visible_to") == ["bec", "pxx"], web)
    ok("... eine App ohne visible_to bleibt fuer alle (Feld fehlt)",
       "visible_to" not in next(a for a in doc["apps"] if a["id"] == "vereinsportal"))
else:
    print("SKIP  Knoten-Leser nicht gefunden (nur im Gesamt-Repository pruefbar)")

print("\n=== 0.4: Sichtbar fuer (visible_to) ===")
ok("Kennungen werden gelesen, sortiert, doppelte fallen weg",
   cat.parse_labels("pxx, bec;pxx  ") == ["bec", "pxx"])
ok("leer heisst alle", cat.parse_labels("  ") == [])
refuses("eine Kennung, die der Knoten nie vergibt", lambda: cat.parse_labels("Pxx"),
        "keine Mandanten-Kennung")
C4 = cat.Catalog(tempfile.mkdtemp(prefix="oaap-catalog-vis-"))
C4.add(to_file(pkg("bdt-app", "1.0.0")), 10 ** 8, "betreiber")
C4.set_released("bdt-app", "1.0.0", True, "betreiber")
C4.set_visible("bdt-app", ["pxx"], "betreiber")
lst = json.load(open(os.path.join(C4.dir, "store.json")))
ok("store.json traegt visible_to", lst["apps"][0].get("visible_to") == ["pxx"], lst)
C4.set_visible("bdt-app", [], "betreiber")
lst = json.load(open(os.path.join(C4.dir, "store.json")))
ok("leer gesetzt: das Feld verschwindet wieder (= alle)",
   "visible_to" not in lst["apps"][0], lst)
ok("jede Aenderung steht im Protokoll",
   [e["visible_to"] for e in C4.state["audit"] if e["action"] == "visible"]
   == [["pxx"], "alle"], C4.state["audit"])
refuses("visible_to fuer eine App, die es nicht gibt",
        lambda: C4.set_visible("gibtsnicht", ["pxx"], "b"), "gibt es nicht")

print("\n=== 0.4: der Eingang des Knotens (In den Katalog uebernehmen) ===")
C5 = cat.Catalog(tempfile.mkdtemp(prefix="oaap-catalog-inbox-"))
BOX = os.path.join(C5.dir, "inbox")
os.makedirs(BOX)


def drop(data, stem=None, **meta):
    sha = hashlib.sha256(data).hexdigest()
    stem = stem or f"bdt-app-1.1.0-{sha[:12]}"
    with open(os.path.join(BOX, stem + ".zip"), "wb") as f:
        f.write(data)
    m = {"sha256": sha, "tenant": "bdt", "instance": "bdt-app-test",
         "channel": "test", "by": "joerg"}
    m.update(meta)
    with open(os.path.join(BOX, stem + ".json"), "w") as f:
        json.dump(m, f)
    return stem


def result(stem):
    with open(os.path.join(BOX, stem + ".result.json")) as f:
        return json.load(f)


ok("ein leerer Eingang: nichts zu tun", C5.ingest_inbox(10 ** 8) == 0)
st = drop(pkg("bdt-app", "1.1.0"))
ok("ein Eintrag wird bearbeitet", C5.ingest_inbox(10 ** 8) == 1)
r = C5.get("bdt-app", "1.1.0")
ok("aufgenommen, aber NICHT freigegeben", r is not None and r["released"] is False, r)
ok("mit Herkunft (Mandant, Instanz, Kanal) und dem Menschen, der es ausloeste",
   r["origin"] == {"tenant": "bdt", "instance": "bdt-app-test", "channel": "test"}
   and r["uploaded_by"] == "joerg" and "bdt/bdt-app-test" in r["note"], r)
ok("die Antwort steht fuer den Knoten bereit",
   result(st)["ok"] is True and "nicht freigegeben" in result(st)["message"], result(st))
ok("Paket und Notiz sind aus dem Eingang verschwunden",
   sorted(os.listdir(BOX)) == [st + ".result.json"], os.listdir(BOX))
ok("das Protokoll unterscheidet Uebernahme und Upload",
   C5.state["audit"][-1]["action"] == "take-over")
ok("nicht freigegeben heisst: nicht in der Liste",
   json.load(open(os.path.join(C5.dir, "store.json")))["apps"] == [])
st2 = drop(pkg("bdt-app", "1.1.0"), stem="bdt-app-1.1.0-aaaaaaaaaaaa")
C5.ingest_inbox(10 ** 8)
ok("dieselbe Version noch einmal: abgelehnt, mit Grund fuer den Knoten",
   result(st2)["ok"] is False and "gibt es schon" in result(st2)["message"], result(st2))
data = pkg("bdt-app", "1.2.0")
sha = hashlib.sha256(data).hexdigest()
st3 = drop(data, sha256="0" * 64, stem=f"bdt-app-1.2.0-{sha[:12]}")
C5.ingest_inbox(10 ** 8)
ok("Pruefsumme passt nicht zur Notiz: abgelehnt, nichts aufgenommen",
   result(st3)["ok"] is False and "Prüfsumme" in result(st3)["message"]
   and C5.get("bdt-app", "1.2.0") is None, result(st3))
st4 = drop(zip_bytes({"x.txt": "kein Manifest"}), stem="kaputt-0.0.1-bbbbbbbbbbbb")
C5.ingest_inbox(10 ** 8)
ok("ein Paket, das ein Upload ablehnen wuerde, lehnt auch der Eingang ab",
   result(st4)["ok"] is False and "oaap-app.yaml" in result(st4)["message"], result(st4))
with open(os.path.join(BOX, "nur-notiz-1.0.0-cccccccccccc.json"), "w") as f:
    json.dump({"sha256": "1" * 64}, f)
C5.ingest_inbox(10 ** 8)
ok("eine Notiz ohne Paket: abgelehnt, die Notiz ist weg",
   result("nur-notiz-1.0.0-cccccccccccc")["ok"] is False
   and not os.path.exists(os.path.join(BOX, "nur-notiz-1.0.0-cccccccccccc.json")))
with open(os.path.join(BOX, "../ausbruch.json"), "w") as f:
    f.write("{}")
with open(os.path.join(BOX, "Kein Stamm.json"), "w") as f:
    f.write("{}")
ok("eine Datei mit fremdem Namen wird nicht angefasst",
   C5.ingest_inbox(10 ** 8) == 0 and os.path.exists(os.path.join(BOX, "Kein Stamm.json")))
ok("kein Rest im tmp-Verzeichnis", os.listdir(C5.tmp_dir()) == [], os.listdir(C5.tmp_dir()))

print("\n=== der Zugang ===")
SRV = appmod.make_server(tempfile.mkdtemp(prefix="oaap-catalog-http-"), 0)
PORT = SRV.server_address[1]
threading.Thread(target=SRV.serve_forever, daemon=True).start()
ADMIN = {"X-OAAP-Roles": "admin", "X-OAAP-User": "joerg"}


def call(method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", PORT, timeout=10)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    out = (r.status, dict((k.lower(), v) for k, v in r.getheaders()), data)
    c.close()
    return out


def upload(data, headers=None, note="Notiz"):
    b = "----x7f3"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="note"\r\n\r\n{note}\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="p.zip"\r\n'
            f'Content-Type: application/zip\r\n\r\n').encode() + data + \
           f"\r\n--{b}--\r\n".encode()
    h = dict(headers or ADMIN)
    h["Content-Type"] = f"multipart/form-data; boundary={b}"
    return call("POST", "/upload", body, h)


s, _h, d = call("GET", "/healthz")
ok("/healthz antwortet ohne Anmeldung", s == 200 and b"ok" in d)
ok("ohne Rolle: kein Zugriff auf die Seite", call("GET", "/")[0] == 403)
ok("als normaler Benutzer ebenso",
   call("GET", "/", headers={"X-OAAP-Roles": "user", "X-OAAP-User": "x"})[0] == 403)
ok("ohne Rolle: kein Upload", upload(pkg("abc", "1.0.0"), {"X-OAAP-Roles": "user"})[0] == 403)
ok("ohne Rolle: kein Download", call("GET", "/download/website/1.0.0")[0] == 403)
ok("als Betreiber: die Seite", call("GET", "/", headers=ADMIN)[0] == 200)
s, h, _d = upload(pkg("meinapp", "1.0.0"))
ok("Upload als Betreiber: weiter auf die Seite mit der Meldung",
   s == 303 and "Aufgenommen" in __import__("urllib.parse").parse.unquote(h["location"]), h)
s, h, _d = upload(pkg("meinapp", "1.0.0"))
ok("dieselbe Version noch einmal: abgelehnt, mit Grund",
   s == 303 and "gibt+es+schon" in h["location"].replace("%20", "+"), h["location"])
s, h, _d = upload(b"kein zip")
ok("ein Nicht-ZIP: abgelehnt", s == 303 and "ok=1" not in h["location"], h["location"])
s, _h, d = call("GET", "/", headers=ADMIN)
ok("die Seite zeigt App und Version, die Notiz maskiert",
   b"meinapp" in d and b"1.0.0" in d)
s, h, _d = upload(pkg("xss", "1.0.0"), note="<script>alert(1)</script>")
s, _h, d = call("GET", "/", headers=ADMIN)
ok("eine Notiz mit Markup steht als Text da", b"<script>alert" not in d
   and b"&lt;script&gt;" in d)
s, h, _d = call("POST", "/release", b"app=meinapp&version=1.0.0&released=1",
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("Freigabe per Formular", s == 303 and appmod.CATALOG.get("meinapp", "1.0.0")["released"])
s, _h, _d = call("POST", "/release", b"app=meinapp&version=1.0.0&released=0",
                 dict(ADMIN, Origin="https://boese.example", Host="oaap.example",
                      **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("eine Anfrage von fremder Seite (Origin) wird abgelehnt, nichts aendert sich",
   s == 403 and appmod.CATALOG.get("meinapp", "1.0.0")["released"])
s, _h, _d = call("POST", "/release", b"app=meinapp&version=1.0.0&released=0",
                 dict(ADMIN, **{"Sec-Fetch-Site": "cross-site",
                                "Content-Type": "application/x-www-form-urlencoded"}))
ok("... ebenso nach Sec-Fetch-Site", s == 403 and appmod.CATALOG.get("meinapp", "1.0.0")["released"])
s, h, d = call("GET", "/download/meinapp/1.0.0", headers=ADMIN)
ok("Download liefert genau die hochgeladenen Bytes",
   s == 200 and d == pkg("meinapp", "1.0.0")
   and h["content-type"] == "application/zip"
   and "attachment" in h["content-disposition"], h)
ok("Download einer Version, die es nicht gibt: 404",
   call("GET", "/download/meinapp/9.9.9", headers=ADMIN)[0] == 404)
ok("Download mit ../ im Namen: 404, kein Dateizugriff",
   call("GET", "/download/..%2F..%2Fetc/passwd", headers=ADMIN)[0] == 404)
s, h, _d = call("POST", "/delete", b"app=meinapp&version=1.0.0&reason=",
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("eine freigegebene Version ohne Grund loeschen: abgelehnt, sie bleibt",
   s == 303 and "ok=1" not in h["location"] and appmod.CATALOG.get("meinapp", "1.0.0"))
s, h, _d = call("POST", "/delete", b"app=meinapp&version=1.0.0&reason=Fehlfreigabe",
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("mit Grund: weg", appmod.CATALOG.get("meinapp", "1.0.0") is None)
upload(pkg("sichtbar", "1.0.0"))
s, h, _d = call("POST", "/visible", b"app=sichtbar&visible_to=pxx%2C+bec",
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("0.4: Sichtbar fuer setzen ueber die Seite",
   s == 303 and appmod.CATALOG.visible_to("sichtbar") == ["bec", "pxx"], h)
s, h, _d = call("POST", "/visible", b"app=sichtbar&visible_to=PXX",
                dict(ADMIN, **{"Content-Type": "application/x-www-form-urlencoded"}))
ok("... eine ungueltige Kennung: abgelehnt, alter Stand bleibt",
   "ok=1" not in h["location"] and appmod.CATALOG.visible_to("sichtbar") == ["bec", "pxx"])
s, _h, _d = call("POST", "/visible", b"app=sichtbar&visible_to=",
                 {"X-OAAP-Roles": "user", "X-OAAP-User": "x",
                  "Content-Type": "application/x-www-form-urlencoded"})
ok("... ohne admin: 403", s == 403 and appmod.CATALOG.visible_to("sichtbar") == ["bec", "pxx"])
ok("kein Upload-Rest im tmp-Verzeichnis",
   os.listdir(appmod.CATALOG.tmp_dir()) == [], os.listdir(appmod.CATALOG.tmp_dir()))
SRV.shutdown()

print("\nFAILS:", fails)
sys.exit(1 if fails else 0)
