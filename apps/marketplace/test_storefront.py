#!/usr/bin/env python3
"""Marketplace Stufe 2: das Schaufenster (RFC-0077 §8, §5, §12).

Geprüft wird, was neu ist und was schiefgehen kann:

    Beschreibung und Notizen sind Markdown -- aber nie aktives HTML.
    Die Suche findet über Beschreibung und Schlagworte, auch ohne Umlaute.
    Eine Facette zählt nur, was die Person sieht (ein verborgenes Asset
    ändert keine Zahl).
    Bilder: nur PNG/JPEG/WebP nach Inhalt, kein SVG; ein Bild ist so
    sichtbar wie sein Asset, nicht mehr.
    Freischalt-Anfrage: eine je Person und Asset; Ablehnen braucht eine
    Antwort; Freischalten erzeugt die Berechtigung -- und dann kann sie laden.

Aufruf: python3 test_storefront.py
"""
import io
import json
import os
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
import app as mk                                              # noqa: E402

fails = 0


def ok(label, cond, detail=""):
    global fails
    fails += not cond
    print(f"{'PASS' if cond else 'FAIL'}  {label}")
    if not cond and detail:
        print(f"      {str(detail)[:400]}")


srv = mk.make_server(tempfile.mkdtemp(prefix="market-sf-"), 0)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"
ADMIN = {"X-OAAP-User": "joerg", "X-OAAP-Roles": "server_admin,admin,user"}
CI = {"X-OAAP-User": "ci", "X-OAAP-Roles": "user"}
ANNA = {"X-OAAP-User": "anna", "X-OAAP-Roles": "user"}


class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OP = urllib.request.build_opener(NoRedir)


def call(method, path, headers=None, body=None, ctype=None):
    h = dict(headers or {})
    h.setdefault("Accept-Language", "de")
    if ctype:
        h["Content-Type"] = ctype
    try:
        with OP.open(urllib.request.Request(BASE + path, data=body, method=method, headers=h), timeout=20) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def form(path, fields, headers=ADMIN):
    return call("POST", path, headers, urllib.parse.urlencode(fields).encode(),
                "application/x-www-form-urlencoded")


def multipart(path, headers, fields, filename, data):
    bd = "----t" + uuid.uuid4().hex
    parts = [f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
             for k, v in fields.items()]
    parts.append(f'--{bd}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{bd}--\r\n".encode())
    return call("POST", path, headers, b"".join(parts), f"multipart/form-data; boundary={bd}")


def meta(aid, title, summary, description, **rest):
    """0.3: Texte (je Sprache) und Meta sind zwei Formulare."""
    form(f"/admin/assets/{aid}/texts", {"lang": "de", "title": title, "summary": summary,
                                         "description": description})
    return form(f"/admin/assets/{aid}/meta", rest)


def zip_bytes(app_id, version):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("oaap-app.yaml", f'app:\n  id: {app_id}\n  name: {app_id}\n  version: "{version}"\n')
    return buf.getvalue()


def png(seed=0):
    """Ein echtes 1x1-PNG."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"\x00" + bytes([seed % 256, 0, 0])
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


form("/admin/category", {"id": "sport", "landscape": "default", "title_en": "Sport"})
form("/admin/publisher", {"pattern": "probe-*", "kind": "user", "name": "ci", "channels": ""})
multipart("/api/v1/releases", CI, {"channels": "stable"}, "a.zip", zip_bytes("probe-verein", "1.0.0"))
multipart("/api/v1/releases", CI, {"channels": "beta"}, "b.zip", zip_bytes("probe-werkzeug", "0.1.0"))
multipart("/api/v1/releases", CI, {"channels": "stable"}, "c.zip", zip_bytes("probe-geheim", "1.0.0"))

print("Beschreibung, Schlagworte, Links -- Markdown, nie aktives HTML")
st, _, h = meta("probe-verein", "Vereinsportal", "Für Handballvereine",
                "## Funktionen\n**Spielpläne** und Ergebnisse.\n<script>alert(1)</script>\n"
                "[böse](javascript:alert(1)) [gut](https://example.org)",
                tags="Handball, Verein", categories="sport", publisher="HBSHA", license="proprietär",
                links="Webseite | https://example.org\nkaputt | javascript:alert(1)")
ok("ein Link ohne http(s) wird abgelehnt (Speichern scheitert mit Satz)",
   st == 303 and "http" in urllib.parse.unquote(h.get("Location", "")), h.get("Location"))
meta("probe-verein", "Vereinsportal", "Für Handballvereine",
     "## Funktionen\n**Spielpläne** und Ergebnisse.\n<script>alert(1)</script>\n"
     "[böse](javascript:alert(1)) [gut](https://example.org)",
     tags="Handball, Verein", categories="sport", publisher="HBSHA", license="proprietär",
     links="Webseite | https://example.org")
st, body, _ = call("GET", "/assets/probe-verein")
b = body.decode()
ok("Detailseite: Markdown wird gesetzt", "<strong>Spielpläne</strong>" in b and "<h2" in b, b[:200])
ok("…ein <script> erscheint als Text", "<script>alert" not in b and "&lt;script&gt;" in b)
ok("…ein javascript:-Link wird kein Link", 'href="javascript' not in b)
ok("Fakten: Herausgeber, Lizenz, Link", "HBSHA" in b and "proprietär" in b and 'href="https://example.org"' in b)
d = json.loads(call("GET", "/api/public/v1/assets/probe-verein")[1])
ok("die API trägt Schlagworte klein und sortiert", d["tags"] == ["handball", "verein"], d.get("tags"))

print("\nSuche")
d = json.loads(call("GET", "/api/public/v1/assets?q=spielplane")[1])
ok("„spielplane“ findet „Spielpläne“ (ohne Umlaut, Präfix)", [a["id"] for a in d["assets"]] == ["probe-verein"], d)
d = json.loads(call("GET", "/api/public/v1/assets?q=handball")[1])
ok("ein Schlagwort findet", [a["id"] for a in d["assets"]] == ["probe-verein"], d)
ok("Unsinn findet nichts", json.loads(call("GET", "/api/public/v1/assets?q=zzzz")[1])["count"] == 0)

print("\nFacetten zählen nur Sichtbares")
meta("probe-geheim", "Geheim", "", "", tags="verein", categories="sport", publisher="",
     license="", links="")
form("/admin/asset", {"asset": "probe-geheim", "mode": "principals", "free": ""})
st, body, _ = call("GET", "/?tag=verein")
b = body.decode()
ok("anonym: Filter verein zeigt 1 Asset, nicht 2", "1 Asset<" in b and "Geheim" not in b, b[b.find("Asset"):][:80])
ok("…und die Facette „verein“ zählt 1", ">verein</span><span class=muted>1<" in b)
st, body, _ = call("GET", "/my/?tag=verein", ADMIN)
ok("der Betreiber sieht beide", "2 Assets" in body.decode())
st, body, _ = call("GET", "/?channel=beta")
ok("Facette Kanal: nur beta → das Werkzeug", "probe-werkzeug" in body.decode() and "Vereinsportal" not in body.decode())

print("\nBilder")
st, d, _ = multipart("/api/v1/assets/probe-verein/images", CI, {"role": "icon"}, "icon.png", png(1))
d = json.loads(d)
icon = d.get("sha256")
ok("ein PNG als Icon (Veröffentlicher)", st == 201 and icon, d)
st, d, _ = multipart("/api/v1/assets/probe-verein/images", CI, {"role": "screenshot"}, "x.svg",
                     b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')
ok("ein SVG wird abgelehnt", st == 400, (st, d))
st, d, _ = multipart("/api/v1/assets/probe-verein/images", CI, {"role": "screenshot"}, "x.png", b"GIF89a....")
ok("ein GIF mit .png-Namen wird abgelehnt (Inhalt zählt)", st == 400, (st, d))
st, d, _ = multipart("/api/v1/assets/probe-verein/images", ANNA, {"role": "screenshot"}, "s.png", png(2))
ok("ohne Recht: 403", st == 403, st)
st, d, _ = multipart("/admin/assets/probe-verein/image", ADMIN, {"role": "screenshot"}, "s.png", png(3))
shot = mk.M.asset("probe-verein")["screenshots_list"]
ok("ein Screenshot über die Verwaltung", st == 303 and len(shot) == 1, (st, shot))
st, body, h = call("GET", f"/img/{icon}")
ok("das Icon ist öffentlich sichtbar, als image/png, inline, nosniff",
   st == 200 and h.get("Content-Type") == "image/png" and h.get("X-Content-Type-Options") == "nosniff"
   and body.startswith(b"\x89PNG"), (st, h))
ok("die Karte zeigt das Icon", f"/img/{icon}" in call("GET", "/")[1].decode())
_, gd, _ = multipart("/api/v1/assets/probe-geheim/images", ADMIN, {"role": "icon"}, "g.png", png(4))
gicon = json.loads(gd)["sha256"]
ok("das Icon eines verborgenen Assets: anonym 404", call("GET", f"/img/{gicon}")[0] == 404)
ok("…für den Betreiber 200", call("GET", f"/my/img/{gicon}", ADMIN)[0] == 200)
st, _, _ = call("DELETE", f"/api/v1/assets/probe-verein/images/{icon}", CI)
ok("Icon entfernen", st == 200 and not mk.M.asset("probe-verein")["icon"])
ok("…die Datei ist weg, weil nichts mehr auf sie zeigt", mk.B.stat(icon) is None)

print("\nFreischalt-Anfrage")
st, body, _ = call("GET", "/assets/probe-verein")
ok("anonym: „Anmelden, um eine Freischaltung anzufragen“", "Freischaltung anzufragen" in body.decode())
ok("anonym anfragen geht nicht (401)", form("/my/assets/probe-verein/request", {"channel": "stable"}, {})[0] == 401)
st, _, h = form("/my/assets/probe-verein/request", {"channel": "stable", "note": "Für unseren Verein"}, ANNA)
ok("angemeldet: Anfrage gestellt", st == 303 and "Anfrage gestellt" in urllib.parse.unquote(h["Location"]), h)
form("/my/assets/probe-verein/request", {"channel": "stable", "note": "Bitte bald"}, ANNA)
ok("eine zweite ergänzt die erste (eine je Person und Asset)", len(mk.M.requests("open")) == 1, mk.M.requests())
ok("die Verwaltung zeigt sie", "Bitte bald" in call("GET", "/admin", ADMIN)[1].decode())
rid = mk.M.requests("open")[0]["id"]
st, _, h = form("/admin/request-reject", {"id": rid, "answer": ""})
ok("ablehnen ohne Antwort: abgelehnt", "Antwort" in urllib.parse.unquote(h.get("Location", "")), h)
form("/admin/request-reject", {"id": rid, "answer": "Nur für Mitgliedsvereine"})
ok("die Person sieht die Antwort", "Nur für Mitgliedsvereine" in call("GET", "/my/assets/probe-verein", ANNA)[1].decode())
form("/my/assets/probe-verein/request", {"channel": "stable", "note": "Wir sind jetzt Mitglied"}, ANNA)
rid = mk.M.requests("open")[0]["id"]
form("/admin/request-grant", {"id": rid, "channels": "stable"})
ents = [e for e in mk.M.entitlements("probe-verein") if e["principal"] == "anna"]
ok("freischalten erzeugt die Berechtigung", len(ents) == 1 and "stable" in ents[0]["channels"], ents)
b = call("GET", "/my/assets/probe-verein", ANNA)[1].decode()
ok("…und Anna kann laden", "/my/d/" in b and "berechtigt" in b, b[:300])

print("\nVersionsverlauf mit Notizen")
multipart("/api/v1/releases", CI, {"channels": "stable", "notes": "**Neu:** Tabellen\n<b>x</b>"}, "d.zip",
          zip_bytes("probe-verein", "1.1.0"))
b = call("GET", "/assets/probe-verein")[1].decode()
ok("die Notiz steht als Markdown im Verlauf, HTML als Text",
   "<strong>Neu:</strong>" in b and "&lt;b&gt;x&lt;/b&gt;" in b, b[-600:])

print("\nDockerfile kopiert jedes Modul")
docker = open(os.path.join(HERE, "Dockerfile"), encoding="utf-8").read()
mods = sorted(f for f in os.listdir(HERE) if f.endswith(".py") and not f.startswith("test_"))
ok("jedes Modul steht im COPY", all(m in docker for m in mods), [m for m in mods if m not in docker])

srv.shutdown()
print("\nALLE PRUEFUNGEN BESTANDEN" if not fails else f"\n{fails} FEHLGESCHLAGEN")
sys.exit(1 if fails else 0)
