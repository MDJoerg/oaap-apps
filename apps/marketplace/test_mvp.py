#!/usr/bin/env python3
"""Marketplace 0.3: Stufe 2 abgeschlossen -- MVP Aufbau-Vorlagen (RFC-0077 §15.8).

Geprüft wird, was neu ist und was schiefgehen kann:

    Migration 0.2 -> 0.3: Texte nach de, freie Kategorien nach default, oaap fest.
    Aufbau-Vorlage (oaap-profile): Kennung und Version aus dem Profil.
    Die Sperre: Profil mit wörtlichem Passwort, Text mit oaapk_-Schlüssel, ZIP mit .env --
    je 422, mit Ort und Regel, nie mit dem Wert; auch das Protokoll ohne Wert.
    Bild-Metadaten: EXIF (mit GPS) ist nach dem Hochladen weg.
    Review an: ein neues Release zählt erst nach der Freigabe; vier Augen; eskalieren;
    der Betreiber entscheidet. Listing-Änderungen öffnen einen Fall.
    Landschaften und Kategorien: Facetten; default gilt übergreifend; Zählen nach can_see.
    Sprachen: Accept-Language, Rückfall mit Hinweis, ?lang= wird gemerkt.
    Teilen: QR-Code und kanonischer Link.
    Partner light: eigene Assets ja, fremde nein; Antwort mit Link.

Aufruf: python3 test_mvp.py   (setzt MARKET_REVIEW=on für diesen Lauf)
"""
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["MARKET_REVIEW"] = "on"
import app as mk                                              # noqa: E402
import db                                                     # noqa: E402
import secretguard                                            # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


# ---------------------------------------------------------------- Migration --
print("Migration 0.2 -> 0.3")
mig = tempfile.mkdtemp(prefix="market-mig-")
con = sqlite3.connect(os.path.join(mig, "market.db"))
for s in db.SCHEMA:
    con.execute(s)
for s in db.SCHEMA_2:
    con.execute(s)
con.execute("PRAGMA user_version=2")
con.execute("INSERT INTO asset(id, type, title, summary, description, visibility, free_channels, "
            "created_at, created_by, categories) VALUES ('alt','oaap-app','Altes Paket','Kurz',"
            "'Lang','{\"mode\":\"anyone\"}','[\"stable\"]',0,'x','[\"werkzeug\"]')")
con.execute("INSERT INTO release(asset, version, notes, created_at, created_by) VALUES "
            "('alt','1.0.0','',0,'x')")
con.execute("INSERT INTO placement VALUES ('alt','1.0.0','stable',0,'x')")
con.commit()
con.close()
m2 = db.Market(os.path.join(mig, "market.db"))
alt = m2.asset("alt")
ok("N1 alte Texte liegen unter de", alt["texts_map"].get("de", {}).get("title") == "Altes Paket", alt["texts_map"])
ok("N1 freie Kategorie wird Kategorie der Landschaft default",
   [(c["id"], c["landscape"]) for c in m2.categories()] == [("werkzeug", "default")])
ok("N1 bestehende OAAP-App liegt in oaap", alt["landscapes_list"] == ["oaap"])
ok("N1 altes Release zählt weiter (freigegeben)", m2.current("alt", "stable") is not None)
try:
    m2.delete_landscape("oaap", "t")
    ok("N1 oaap ist fest", False)
except db.Refused as e:
    ok("N1 oaap ist fest", e.status == 409)

# ---------------------------------------------------------------- Server -----
srv = mk.make_server(tempfile.mkdtemp(prefix="market-mvp-"), 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "server_admin,admin,user"}
CI = {"X-OAAP-User": "ci", "X-OAAP-Roles": "user"}
AI = {"X-OAAP-User": "pruef-ki", "X-OAAP-Roles": "user"}
HB = {"X-OAAP-User": "hbsha", "X-OAAP-Roles": "user"}
VEREIN = {"X-OAAP-User": "tsv", "X-OAAP-Roles": "user", "X-OAAP-Groups": "kunden-hb"}


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OP = urllib.request.build_opener(NoRedir)


def call(method, path, headers=None, body=None, ctype=None):
    h = dict(headers or {})
    if ctype:
        h["Content-Type"] = ctype
    try:
        with OP.open(urllib.request.Request(BASE + path, data=body, method=method, headers=h), timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def jcall(method, path, headers=None, doc=None):
    st, body, h = call(method, path, headers, json.dumps(doc).encode() if doc is not None else None,
                       "application/json" if doc is not None else None)
    try:
        return st, json.loads(body or b"{}"), h
    except ValueError:
        return st, {"raw": body[:300]}, h


def form(path, fields, headers=ADMIN):
    return call("POST", path, headers, urllib.parse.urlencode(fields, doseq=True).encode(),
                "application/x-www-form-urlencoded")


def multipart(path, headers, fields, filename, data):
    bd = "----t" + uuid.uuid4().hex
    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    st, body, h = call("POST", path, headers, b"".join(parts), f"multipart/form-data; boundary={bd}")
    try:
        return st, json.loads(body or b"{}"), h
    except ValueError:
        return st, {"raw": body[:300]}, h


def profile(pid="probe-verein", version="1.0.0", **extra):
    doc = {"profile": "oaap.tenant-profile/1", "id": pid, "version": version,
           "title": "Probeverein", "description": "Ein Mandant zum Messen.",
           "params": {"label": {"kind": "label", "required": True}},
           "steps": [{"id": "tenant", "type": "tenant.create", "label": "{label}"},
                     {"id": "push", "type": "app.config", "name": "x", "key": "HIB_FAN_VAPID_PRIVATE",
                      "generate": True}]}
    doc.update(extra)
    return json.dumps(doc, ensure_ascii=False).encode()


def zip_with(app_id, version, extra=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("oaap-app.yaml", f'app:\n  id: {app_id}\n  name: {app_id}\n  version: "{version}"\n')
        for n, d in (extra or {}).items():
            z.writestr(n, d)
    return buf.getvalue()


def png(seed=0, text_chunk=b""):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"\x00" + bytes([seed % 256, 0, 0])
    tx = chunk(b"tEXt", text_chunk) if text_chunk else b""
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + tx
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_with_exif():
    """Ein minimales JPEG-Gerüst mit APP1/Exif, das GPS-Text trägt, und einem Kommentar."""
    exif = b"Exif\x00\x00" + b"GPSLatitude 52.1234 GPSLongitude 11.4321"
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    com = b"\xff\xfe" + (len(b"geheimer Kommentar") + 2).to_bytes(2, "big") + b"geheimer Kommentar"
    app0 = b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sos = b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00" + b"\x12\x34\x56" + b"\xff\xd9"
    return b"\xff\xd8" + app0 + app1 + com + sos


# ------------------------------------------------------------- Landschaften --
print("\nLandschaften und Kategorien")
form("/admin/landscape", {"id": "bdt", "title_en": "BDT", "description_en": "Business data twin",
                          "title_de": "BDT", "description_de": "Geschäftsdaten-Zwilling"})
form("/admin/category", {"id": "setup-templates", "landscape": "oaap", "title_en": "Setup templates",
                         "title_de": "Aufbau-Vorlagen"})
form("/admin/category", {"id": "sample-projects", "landscape": "bdt", "title_en": "Sample projects",
                         "title_de": "Musterprojekte"})
form("/admin/category", {"id": "docs", "landscape": "default", "title_en": "Documentation",
                         "title_de": "Dokumentation"})
st, tax, _ = jcall("GET", "/api/public/v1/taxonomy")
ok("N7 Taxonomie: oaap fest, bdt, drei Kategorien",
   [x["id"] for x in tax["landscapes"]] == ["oaap", "bdt"] and len(tax["categories"]) == 3
   and tax["landscapes"][0]["fixed"], tax)
st, _, h = form("/admin/landscape-remove", {"id": "oaap"})
ok("N7 oaap lässt sich nicht löschen", "fest" in urllib.parse.unquote(h.get("Location", "")), h)

form("/admin/publisher", {"pattern": "probe-*", "kind": "user", "name": "ci", "channels": ""})
form("/admin/publisher", {"pattern": "hb-*", "kind": "user", "name": "hbsha", "channels": "stable"})
form("/admin/reviewer", {"kind": "user", "name": "pruef-ki"})

# ------------------------------------------------------------- Aufbau-Vorlage --
print("\nAufbau-Vorlage und Sperre")
st, d, _ = multipart("/api/v1/releases", CI, {"type": "oaap-profile", "channels": "stable"},
                     "p.json", profile())
ok("N2 Aufbau-Vorlage eingecheckt: Kennung und Version aus dem Profil, in Prüfung",
   st == 201 and d.get("asset") == "probe-verein" and d.get("version") == "1.0.0"
   and d.get("review") == "pending", (st, d))
a = mk.M.asset("probe-verein")
ok("N2 Typ oaap-profile, Landschaft oaap", a["type"] == "oaap-profile" and a["landscapes_list"] == ["oaap"])
st, d, _ = multipart("/api/v1/releases", CI, {"type": "oaap-profile"}, "p.json", profile())
ok("N2 dieselben Bytes: 200, nichts Neues", st == 200 and d.get("created") is False, (st, d))

SECRET_PW = "Sommer2026!geheim"
bad = profile("probe-pw", steps=[{"id": "x", "type": "app.config", "name": "a", "key": "DB_PASSWORD",
                                  "value": SECRET_PW}])
st, d, _ = multipart("/api/v1/releases", CI, {"type": "oaap-profile"}, "p.json", bad)
ok("N3 Profil mit wörtlichem Passwort: 422", st == 422, (st, d))
ok("N3 …die Antwort nennt Ort und Regel", "steps[0]" in d.get("error", "") and "wörtlicher Wert" in d.get("error", ""), d)
ok("N3 …aber nie den Wert", SECRET_PW not in json.dumps(d), d)
ok("N3 …und nichts wurde angelegt", mk.M.asset("probe-pw") is None)
ok("N3 Parameter-Platzhalter ist erlaubt",
   multipart("/api/v1/releases", CI, {"type": "oaap-profile"}, "p.json",
             profile("probe-param", steps=[{"id": "x", "type": "app.config", "name": "a",
                                            "key": "SMTP_PASSWORD", "value": "{smtp}"}]))[0] == 201)
KEY = "oaapk_k7f3a91c_" + "Z" * 30
st, d, _ = jcall("PUT", "/api/v1/assets/probe-verein", CI,
                 {"texts": {"en": {"description": f"Use the key {KEY} to connect."}}})
ok("N3 Beschreibung mit oaapk_-Schlüssel: 422", st == 422 and "oaap-api-key" in d.get("error", ""), (st, d))
ok("N3 …ohne den Wert", KEY not in json.dumps(d))
st, d, _ = multipart("/api/v1/releases", CI, {"channels": "stable"}, "z.zip",
                     zip_with("probe-zip", "1.0.0", {".env": "API_TOKEN=abc"}))
ok("N3 ZIP mit .env: 422 (Geheimnisdatei)", st == 422 and "RFC-0084" in d.get("error", ""), (st, d))
st, d, _ = multipart("/api/v1/releases", CI, {"channels": "stable"}, "z.zip",
                     zip_with("probe-zip", "1.0.0", {"conf/key.txt": "-----BEGIN RSA PRIVATE KEY-----\nMII"}))
ok("N3 ZIP mit privatem Schlüssel im Text: 422", st == 422 and "private-key" in d.get("error", ""), (st, d))
ok("N3 ZIP mit .env.example ist erlaubt",
   multipart("/api/v1/releases", CI, {"channels": "stable"}, "z.zip",
             zip_with("probe-zip", "1.0.0", {".env.example": "API_TOKEN="}))[0] == 201)
events = json.dumps(mk.M.events(200), ensure_ascii=False)
ok("N3 das Protokoll kennt Ort und Regel, nie den Wert",
   "gate-refused" in events and SECRET_PW not in events and KEY not in events)

print("\nBild-Metadaten")
st, d, _ = multipart("/api/v1/assets/probe-verein/images", CI, {"role": "screenshot"}, "s.jpg", jpeg_with_exif())
ok("N4 JPEG mit EXIF angenommen", st == 201, (st, d))
with mk.B.open(d["sha256"]) as f:
    stored = f.read()
ok("N4 gespeichert ohne EXIF/GPS und ohne Kommentar",
   b"GPSLatitude" not in stored and b"Exif" not in stored and b"geheimer" not in stored and stored.startswith(b"\xff\xd8"))
ok("N4 JFIF und Bilddaten bleiben", b"JFIF" in stored and stored.endswith(b"\x12\x34\x56\xff\xd9"))
st, d, _ = multipart("/api/v1/assets/probe-verein/images", CI, {"role": "icon"}, "i.png",
                     png(1, b"Author\x00Max Mustermann"))
with mk.B.open(d["sha256"]) as f:
    ok("N4 PNG-Textchunk (Autor) entfernt", b"Mustermann" not in f.read())

# ------------------------------------------------------------- Review --------
print("\nReview (vier Augen)")
ok("N5 vor der Freigabe: öffentlich keine Version", "1.0.0" not in call("GET", "/assets/probe-verein")[1].decode())
ok("N5 …kein Download in der API", not json.loads(call("GET", "/api/public/v1/assets/probe-verein")[1])["channels"])
st, cases, _ = jcall("GET", "/api/v1/reviews", AI)
rel_case = next((c for c in cases.get("cases", []) if c["kind"] == "release" and c["asset"] == "probe-verein"), None)
ok("N5 die Prüf-KI sieht den Fall mit Texten, Dateien und Bildern",
   st == 200 and rel_case and rel_case.get("files") and rel_case.get("texts") is not None, cases)
st, blob, _ = call("GET", rel_case["files"][0]["url"].replace(BASE, ""), AI)
ok("N5 …und darf die Datei des wartenden Releases laden", st == 200 and b"probe-verein" in blob)
ok("N5 Anonyme sehen /api/v1/reviews nicht", jcall("GET", "/api/v1/reviews", CI)[0] == 404)
form("/admin/reviewer", {"kind": "user", "name": "ci"})        # ci ist jetzt auch Prüfer ...
st, d, _ = jcall("POST", f"/api/v1/reviews/{rel_case['id']}", CI, {"verdict": "approve"})
ok("N5 …aber nicht für den eigenen Fall (403)", st == 403 and "Vier Augen" in d.get("error", ""), (st, d))
st, d, _ = jcall("POST", f"/api/v1/reviews/{rel_case['id']}", AI,
                 {"verdict": "escalate", "summary": "Kein Modell angebunden",
                  "findings": [{"severity": "warn", "text": "Englischer Text fehlt"}]})
ok("N5 die Prüf-KI eskaliert", st == 200 and d.get("state") == "escalated", (st, d))
st, d, _ = jcall("POST", f"/api/v1/reviews/{rel_case['id']}", AI, {"verdict": "approve"})
ok("N5 …eskaliert entscheidet nur noch der Mensch (409)", st == 409, (st, d))
ok("N5 die Verwaltung zeigt den eskalierten Fall",
   "Kein Modell angebunden" in call("GET", "/admin/reviews", ADMIN)[1].decode())
form("/admin/review", {"id": rel_case["id"], "verdict": "approve", "summary": "geprüft"})
ok("N5 nach der Freigabe durch den Betreiber zählt das Release",
   mk.M.current("probe-verein", "stable") is not None)
ok("N5 …öffentlich sichtbar (Version in den Technischen Details)",
   "1.0.0" in call("GET", "/assets/probe-verein")[1].decode())

st, d, _ = multipart("/api/v1/releases", CI, {"type": "oaap-profile", "channels": "stable"}, "p.json",
                     profile(version="1.1.0"))
case2 = next(c for c in jcall("GET", "/api/v1/reviews", AI)[1]["cases"] if c["version"] == "1.1.0")
jcall("POST", f"/api/v1/reviews/{case2['id']}", AI, {"verdict": "reject",
                                                     "findings": [{"severity": "block", "text": "Schritt lädt aus fremder Quelle"}]})
ok("N5 abgelehnt: der Kanal bleibt auf 1.0.0", mk.M.current("probe-verein", "stable")["version"] == "1.0.0")
b = call("GET", "/my/assets/probe-verein", CI)[1].decode()
ok("N5 der Einsteller sieht die Version mit Prüfstand", "1.1.0" in b and "rejected" in b, b[-800:])
ok("N5 Anonyme sehen 1.1.0 nicht", "1.1.0" not in call("GET", "/assets/probe-verein")[1].decode())

st, d, _ = jcall("PUT", "/api/v1/assets/probe-verein", CI,
                 {"texts": {"en": {"title": "Probe club", "summary": "A tenant for measuring"},
                            "de": {"title": "Probeverein", "summary": "Ein Mandant zum Messen"}},
                  "categories": ["setup-templates", "docs"], "tags": ["verein"]})
ok("N6 Listing-Änderung durch den Einsteller: sofort sichtbar", st == 200, (st, d))
lc = [c for c in jcall("GET", "/api/v1/reviews", AI)[1]["cases"] if c["kind"] == "listing"]
ok("N6 …und ein Listing-Fall ist offen", len(lc) == 1 and "texts" in lc[0]["detail"], lc)
jcall("POST", f"/api/v1/reviews/{lc[0]['id']}", AI, {"verdict": "approve"})
ok("N6 die Prüf-KI gibt frei", mk.M.review(lc[0]["id"])["state"] == "approved")
st, d, _ = jcall("PUT", "/api/v1/assets/probe-verein", CI, {"categories": ["gibt-es-nicht"]})
ok("N6 eine unbekannte Kategorie wird abgelehnt", st == 400, (st, d))
old = mk.M._q("SELECT id FROM review ORDER BY id LIMIT 1")[0]["id"]
mk.M._x([("UPDATE review SET state='open', created_at=0 WHERE id=?", (old,))])
ok("N5 ein Fall über der Frist gilt als eskaliert (beim Lesen berechnet)",
   any(r["id"] == old for r in mk.M.reviews("escalated")))
mk.M._x([("UPDATE review SET state='approved' WHERE id=?", (old,))])

# ------------------------------------------------------------- Facetten ------
print("\nFacetten und Startseite")
multipart("/api/v1/releases", CI, {"type": "file", "asset": "probe-bdt", "version": "1.0.0", "channels": "stable"},
          "projekt.json", b'{"name": "Musterprojekt"}')
r = next(c for c in jcall("GET", "/api/v1/reviews", AI)[1]["cases"] if c["asset"] == "probe-bdt")
jcall("POST", f"/api/v1/reviews/{r['id']}", AI, {"verdict": "approve"})
jcall("PUT", "/api/v1/assets/probe-bdt", ADMIN, {"landscapes": ["bdt"], "categories": ["sample-projects", "docs"],
                                                 "texts": {"en": {"title": "BDT sample"}}, "featured": True})
form("/admin/asset", {"asset": "probe-bdt", "mode": "anyone", "free": "stable"})
form("/admin/asset", {"asset": "probe-verein", "mode": "anyone", "free": "stable"})
st, d, _ = jcall("GET", "/api/public/v1/assets?ls=bdt")
ok("N7 Facette Landschaft bdt: nur das BDT-Asset", [x["id"] for x in d["assets"]] == ["probe-bdt"], d)
st, d, _ = jcall("GET", "/api/public/v1/assets?cat=docs")
ok("N7 übergreifende Kategorie docs: Assets beider Landschaften",
   sorted(x["id"] for x in d["assets"]) == ["probe-bdt", "probe-verein"], d)
st, d, _ = jcall("GET", "/api/public/v1/assets?content=template")
ok("N7 Facette Inhalt: Aufbau-Vorlagen", sorted(x["id"] for x in d["assets"]) == ["probe-param", "probe-verein"]
   or [x["id"] for x in d["assets"]] == ["probe-verein"], d)
b = call("GET", "/?ls=bdt&all=1", {"Accept-Language": "de"})[1].decode()
ok("N7 Kategorie-Facette unter bdt: Musterprojekte und Dokumentation, keine Aufbau-Vorlagen",
   "Musterprojekte" in b and "Dokumentation" in b and ">Aufbau-Vorlagen<" not in b)
form("/admin/asset", {"asset": "probe-bdt", "mode": "principals", "free": ""})
st, d, _ = jcall("GET", "/api/public/v1/assets?cat=docs")
ok("N7 verborgen ändert keine Zahl: docs anonym nur noch 1", [x["id"] for x in d["assets"]] == ["probe-verein"], d)
form("/admin/asset", {"asset": "probe-bdt", "mode": "anyone", "free": "stable"})
home = call("GET", "/", {"Accept-Language": "de"})[1].decode()
ok("N7 Startseite: Empfohlen, Landschaften, Neu", "Empfohlen" in home and "Landschaften" in home and ">Neu<" in home, home[:300])
call("GET", "/d/" + mk.M.current("probe-verein", "stable")["files"][0]["sha256"])
home = call("GET", "/", {"Accept-Language": "de"})[1].decode()
ok("N7 …und Meistgeladen nach einem Download", "Meistgeladen" in home)

# ------------------------------------------------------------- Sprachen ------
print("\nSprachen")
b = call("GET", "/assets/probe-verein", {"Accept-Language": "de-DE,de;q=0.9"})[1].decode()
ok("N8 Accept-Language de: deutscher Titel und deutsche Oberfläche", "Probeverein" in b and "Technische Details" in b)
b = call("GET", "/assets/probe-verein", {"Accept-Language": "en"})[1].decode()
ok("N8 en: englischer Titel und Oberfläche", "Probe club" in b and "Technical details" in b)
jcall("PUT", "/api/v1/assets/probe-bdt", ADMIN, {"texts": {"en": {"title": ""}, "de": {"title": "BDT-Muster"}}})
b = call("GET", "/assets/probe-bdt", {"Accept-Language": "en"})[1].decode()
ok("N8 fehlt en: Rückfall auf de mit Hinweis", "BDT-Muster" in b and "Only available in Deutsch" in b, b[:200])
st, body, h = call("GET", "/assets/probe-verein?lang=de", {"Accept-Language": "en"})
ok("N8 ?lang=de schlägt den Browser und setzt das Cookie",
   "Probeverein" in body.decode() and "market_lang=de" in h.get("Set-Cookie", ""), h.get("Set-Cookie"))
b = call("GET", "/assets/probe-verein", {"Accept-Language": "en", "Cookie": "market_lang=de"})[1].decode()
ok("N8 das Cookie wird gemerkt", "Probeverein" in b)

# ------------------------------------------------------------- Teilen --------
print("\nTeilen")
b = call("GET", "/assets/probe-verein", {"Accept-Language": "de", "Host": "market.example",
                                         "X-Forwarded-Proto": "https"})[1].decode()
ok("N9 kanonischer Link https://market.example/assets/probe-verein", "https://market.example/assets/probe-verein" in b)
ok("N9 QR-Code als SVG und ein Kopierknopf", "<svg" in b and "data-copy=" in b)
try:
    import segno
    q = segno.make("https://market.example/assets/probe-verein", error="m")
    ok("N9 der QR-Code ist der des Links", q.svg_inline(scale=4, border=2, dark="#000", light="#fff") in b)
except ImportError:
    ok("N9 segno vorhanden", False)
st, js, h = call("GET", "/static/market.js")
ok("N9 Skript von der eigenen Adresse (CSP script-src 'self')", st == 200 and "clipboard" in js.decode()
   and "script-src 'self'" in call("GET", "/")[2].get("Content-Security-Policy", ""))
ok("N9 Technische Details: Download-Link der Vorlage",
   "/d/" + mk.M.current("probe-verein", "stable")["files"][0]["sha256"] in b)

# ------------------------------------------------------------- Partner -------
print("\nPartner light")
multipart("/api/v1/releases", HB, {"type": "oaap-profile", "channels": "stable"}, "p.json",
          profile("hb-cloud", steps=[{"id": "t", "type": "tenant.create", "label": "{label}"}]))
r = next(c for c in jcall("GET", "/api/v1/reviews", AI)[1]["cases"] if c["asset"] == "hb-cloud")
jcall("POST", f"/api/v1/reviews/{r['id']}", AI, {"verdict": "approve"})
form("/my/assets/hb-cloud/request", {"channel": "stable", "note": "Wir wollen einen Cloud-Zugang"}, VEREIN)
ok("N10 die Anfrage steht beim Partner", "Cloud-Zugang" in call("GET", "/my/manage", HB)[1].decode())
ok("N10 …nicht bei ci", "Cloud-Zugang" not in call("GET", "/my/manage", CI)[1].decode())
rid = mk.M.requests("open", "hb-cloud")[0]["id"]
st, _, h = form("/my/manage/request", {"id": rid, "do": "grant", "answer": "Willkommen! Einladung: https://hsp.example/anfrage?t=abc",
                                       "channels": "stable"}, HB)
ok("N10 der Partner schaltet frei", st == 303 and "Freigeschaltet" in urllib.parse.unquote(h.get("Location", "")), h)
b = call("GET", "/my/assets/hb-cloud", VEREIN)[1].decode()
ok("N10 der Verein sieht die Antwort mit dem Link als Link",
   'href="https://hsp.example/anfrage?t=abc"' in b and "/my/d/" in b, b[:300])
st, _, h = form("/my/manage/grant", {"asset": "hb-cloud", "kind": "group", "name": "kunden-hb", "channels": "beta"}, HB)
ok("N10 einen Kanal außerhalb seiner Rechte darf der Partner nicht vergeben",
   "beta" in urllib.parse.unquote(h.get("Location", "")), h)
st, _, h = form("/my/manage/grant", {"asset": "probe-verein", "kind": "group", "name": "kunden-hb"}, HB)
ok("N10 für ein fremdes Asset nichts", "nur für Assets" in urllib.parse.unquote(h.get("Location", "")), h)
form("/my/manage/grant", {"asset": "hb-cloud", "kind": "group", "name": "kunden-hb", "channels": "stable"}, HB)
ok("N10 Gruppe kunden-hb berechtigt", any(e["principal"] == "kunden-hb" for e in mk.M.entitlements("hb-cloud")))

print("\nSperre: Namensliste wie RFC-0084")
guard = os.path.join(HERE, "..", "..", "..", "oaap-reference", "platform", "services", "guard.py")
if os.path.exists(guard):
    src = open(guard, encoding="utf-8").read()
    import importlib.util
    spec = importlib.util.spec_from_file_location("guard_ref", guard)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    ok("die vier kopierten Kategorien gleichen dem Original",
       all(secretguard.NAME_CATEGORIES[k] == g.CATEGORIES[k][0] for k in secretguard.NAME_CATEGORIES))
else:
    print("      (oaap-reference liegt nicht daneben -- Vergleich übersprungen)")

srv.shutdown()
print("\nALLE PRUEFUNGEN BESTANDEN" if not fails else f"\n{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
