"""Wegweiser — Tests gegen den laufenden Dienst, ohne Fremdbibliothek.

Startet die App in einem Thread auf einem freien Port mit einem
Wegwerf-Datenverzeichnis und spricht sie wie das Gateway an: Identität
als Kopfzeilen auf /manage, /admin, /api — und ohne jede Kopfzeile auf
der öffentlichen Route. Geprüft wird, was die Spezifikation im Briefing
verspricht, und vor allem das, was schiefgehen darf: der erste GET
entwertet nichts, zwei Klicks liefern einen Einmal-Download nur einmal,
fünf falsche PINs sperren, und was eine Area nicht speichern soll,
steht auch nicht in der Datenbank.

Aufruf: python3 test_wegweiser.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="wegweiser-test-")
os.environ["WEGWEISER_DATA_DIR"] = TMP
os.environ["OAAP_APP_SECRET"] = "test-geheimnis"
os.environ["WEGWEISER_MAX_UPLOAD_MB"] = "2"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app  # noqa: E402
import store  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {detail}")


server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


OPENER = urllib.request.build_opener(NoRedirect)

ADMIN = {"X-OAAP-User": "chef", "X-OAAP-User-Id": "u-admin", "X-OAAP-Roles": "admin,user",
         "X-OAAP-Display-Name": "Die%20Chefin", "X-OAAP-Email": ""}
KEYUSER = {"X-OAAP-User": "kai", "X-OAAP-User-Id": "u-kai", "X-OAAP-Roles": "keyuser,user",
           "X-OAAP-Display-Name": "Kai", "X-OAAP-Email": ""}
USER = {"X-OAAP-User": "uta", "X-OAAP-User-Id": "u-uta", "X-OAAP-Roles": "user",
        "X-OAAP-Display-Name": "Uta", "X-OAAP-Email": ""}
OTHER = {"X-OAAP-User": "olaf", "X-OAAP-User-Id": "u-olaf", "X-OAAP-Roles": "user",
         "X-OAAP-Display-Name": "Olaf", "X-OAAP-Email": ""}


def call(method, path, who=None, body=None, headers=None, form=None):
    hdrs = dict(who or {})
    hdrs.update(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif isinstance(body, (dict, list)):
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    elif body is not None:
        data = body
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=hdrs)
    try:
        with OPENER.open(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def j(raw):
    try:
        return json.loads(raw)
    except ValueError:
        return {}


# ------------------------------------------------------------ Gesundheit
print("Gesundheit und Identität")
s, h, b = call("GET", "/healthz")
check("healthz antwortet", s == 200 and j(b).get("status") == "ok")
s, h, b = call("GET", "/api/v1/me")
check("API ohne Identität → 401", s == 401, s)
s, h, b = call("GET", "/api/v1/me", USER)
check("API mit Identität → Rollen und leere Area-Liste", s == 200 and j(b)["areas"] == [] and j(b)["admin"] is False)
s, h, b = call("GET", "/manage", USER)
check("/manage rendert ohne Areas", s == 200 and b"Noch keine Area" in b)
s, h, b = call("GET", "/", None)
check("/ ohne Area → neutrale 404", s == 404 and b"Hier ist nichts" in b)

# ----------------------------------------------------------------- Areas
print("Areas (admin)")
s, h, b = call("POST", "/api/v1/areas", USER, {"slug": "r", "type": "redirect"})
check("Benutzer darf keine Area anlegen", s == 403)
s, h, b = call("POST", "/api/v1/areas", ADMIN, {"slug": "admin", "type": "redirect"})
check("reservierter Slug abgelehnt", s == 422 and "reserviert" in j(b)["error"]["message"])
s, h, b = call("POST", "/api/v1/areas", ADMIN, {"slug": "R!", "type": "redirect"})
check("ungültiger Slug abgelehnt", s == 422)
s, h, b = call("POST", "/api/v1/areas", ADMIN, {"slug": "r", "type": "teleport"})
check("unbekannter Typ abgelehnt", s == 422)

areas = {
    "r": {"slug": "r", "type": "redirect", "title": "Weiterleitungen", "responsible": ["kai"],
          "max_ttl_days": 30, "log_level": "standard"},
    "px": {"slug": "px", "type": "pixel", "title": "Zählpixel", "log_level": "full"},
    "dl": {"slug": "dl", "type": "download_window", "title": "Downloads", "responsible": "kai",
           "allowed_extensions": "pdf, txt", "max_file_mb": 1},
    "dl1": {"slug": "dl1", "type": "download_once", "title": "Einmal", "log_level": "count"},
    "up": {"slug": "up", "type": "upload_once", "title": "Uploads", "pin_required": True,
           "pin_min_length": 4, "allowed_extensions": "txt", "log_level": "minimal"},
    "geheim": {"slug": "geheim", "type": "redirect", "open_for_users": False, "responsible": ["kai"]},
}
for slug, spec in areas.items():
    s, h, b = call("POST", "/api/v1/areas", ADMIN, spec)
    check(f"Area {slug} angelegt", s == 201, b[:200])
s, h, b = call("POST", "/api/v1/areas", ADMIN, {"slug": "r", "type": "redirect"})
check("doppelter Slug abgelehnt", s == 422)
s, h, b = call("GET", "/api/v1/areas", USER)
check("Benutzer sieht nur offene Areas", sorted(a["slug"] for a in j(b)["areas"]) == ["dl", "dl1", "px", "r", "up"])
s, h, b = call("GET", "/api/v1/areas", KEYUSER)
check("Keyuser sieht auch die geschlossene, die er verantwortet",
      "geheim" in [a["slug"] for a in j(b)["areas"]] and [a for a in j(b)["areas"] if a["slug"] == "r"][0]["manager"] is True)
s, h, b = call("PATCH", "/api/v1/areas/r", ADMIN, {"key_length": 5})
check("Area ändern", s == 200 and j(b)["area"]["key_length"] == 5)
s, h, b = call("PATCH", "/api/v1/areas/r", ADMIN, {"key_length": 12})
check("Key-Länge über 8 abgelehnt", s == 422)

# ------------------------------------------------------------- Redirect
print("Weiterleitung")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "ftp://x"})
check("Ziel ohne http(s) abgelehnt", s == 422)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org/ziel", "title": "Beispiel"})
check("Benutzer legt Weiterleitung an", s == 201, b[:200])
L1 = j(b)["link"]
check("erzeugter Key hat die Länge der Area", len(L1["key"]) == 5 and L1["key"].isalnum())
check("Key ohne verwechselbare Zeichen", not set(L1["key"]) & set("0Oo1lI"))
check("Ablauf durch max_ttl der Area gesetzt", L1["expires_at"] is not None)
check("URL enthält Host der Anfrage", L1["url"] == f"http://127.0.0.1:{PORT}/r/{L1['key']}")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org", "key": "wunsch"})
check("Wunsch-Key für Benutzer abgelehnt", s == 422 and "verantwortet" in j(b)["error"]["message"])
s, h, b = call("POST", "/api/v1/links", KEYUSER, {"area": "r", "target_url": "https://example.org", "key": "Wunsch"})
check("Wunsch-Key für Verantwortlichen erlaubt", s == 201)
s, h, b = call("POST", "/api/v1/links", KEYUSER, {"area": "r", "target_url": "https://example.org", "key": "wunsch"})
check("Key-Vergleich ohne Groß/Klein: Duplikat abgelehnt", s == 422)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "geheim", "target_url": "https://example.org"})
check("geschlossene Area für Benutzer zu", s == 403)
s, h, b = call("POST", "/api/v1/links", KEYUSER, {"area": "geheim", "target_url": "https://example.org"})
check("geschlossene Area für Verantwortlichen offen", s == 201)

s, h, b = call("GET", f"/r/{L1['key']}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120", "Referer": "https://mail.example.com/x", "Accept-Language": "de-DE,de;q=0.9", "X-Forwarded-For": "203.0.113.77"})
check("öffentlicher Aufruf → 302 auf Ziel", s == 302 and h.get("Location") == "https://example.org/ziel", s)
s, h, b = call("GET", f"/r/{L1['key'].upper()}")
check("Key-Aufruf ohne Groß/Klein", s == 302)
s, h, b = call("GET", "/r/gibtsnich")
check("unbekannter Key → 404", s == 404)
s, h, b = call("GET", "/nix/abc")
check("unbekannte Area → 404", s == 404)
s, h, b = call("GET", f"/api/v1/links/{L1['id']}/stats", USER)
st = j(b)["stats"]
check("Statistik zählt zwei Weiterleitungen", st["by_result"].get("redirected") == 2, st)
check("Stufe standard: Adresse gekürzt", st["by_class"].get("desktop") == 1 and st["by_browser"].get("Chrome") == 1)
s, h, b = call("GET", f"/api/v1/links/{L1['id']}/accesses", USER)
acc = j(b)["accesses"]
check("Zugriff: gekürzte IP, Referer-Host, Sprache, kein User-Agent-Text",
      acc[-1]["ip"] == "203.0.113.0/24" and acc[-1]["referer"] == "mail.example.com"
      and acc[-1]["lang"] == "de-de" and acc[-1]["ua"] is None, acc[-1])
s, h, b = call("GET", f"/api/v1/links/{L1['id']}/accesses?format=csv", USER)
check("CSV-Export", s == 200 and "text/csv" in h.get("Content-Type", "") and b"redirected" in b)
s, h, b = call("GET", f"/api/v1/links/{L1['id']}", OTHER)
check("fremder Benutzer sieht den Link nicht", s == 404)
s, h, b = call("GET", f"/api/v1/links/{L1['id']}", KEYUSER)
check("Verantwortlicher der Area sieht ihn", s == 200)
s, h, b = call("GET", "/api/v1/links?area=r", KEYUSER)
check("Verantwortlicher sieht alle Links der Area", len(j(b)["links"]) == 2, len(j(b)["links"]))
s, h, b = call("GET", "/api/v1/links", USER)
check("Benutzer sieht nur eigene", [l["id"] for l in j(b)["links"]] == [L1["id"]])

# PIN auf Weiterleitung
s, h, b = call("PATCH", f"/api/v1/links/{L1['id']}", USER, {"pin": "4711"})
check("PIN gesetzt", s == 200 and j(b)["link"]["has_pin"] is True)
s, h, b = call("GET", f"/r/{L1['key']}")
check("mit PIN: GET zeigt Zwischenseite statt 302", s == 200 and b"PIN" in b)
s, h, b = call("POST", f"/r/{L1['key']}", form={"pin": "0000", "action": "go"})
check("falsche PIN → 403", s == 403)
s, h, b = call("POST", f"/r/{L1['key']}", form={"pin": "4711", "action": "go"})
check("richtige PIN → 302", s == 302 and h.get("Location") == "https://example.org/ziel")
for _ in range(5):
    s, h, b = call("POST", f"/r/{L1['key']}", form={"pin": "9999", "action": "go"})
check("fünf Fehlversuche → gesperrt (429)", s == 429, s)
s, h, b = call("POST", f"/r/{L1['key']}", form={"pin": "4711", "action": "go"})
check("gesperrt auch mit richtiger PIN", s == 429)
s, h, b = call("PATCH", f"/api/v1/links/{L1['id']}", USER, {"pin": ""})
check("PIN entfernt (hebt Sperre auf)", s == 200 and j(b)["link"]["has_pin"] is False)
s, h, b = call("GET", f"/r/{L1['key']}")
check("ohne PIN wieder 302", s == 302)
s, h, b = call("PATCH", f"/api/v1/links/{L1['id']}", USER, {"disabled": True})
s, h, b = call("GET", f"/r/{L1['key']}")
check("deaktiviert → 404", s == 404)
s, h, b = call("PATCH", f"/api/v1/links/{L1['id']}", USER, {"disabled": False, "expires_at": "2020-01-01T00:00:00Z"})
check("Ablauf in der Vergangenheit für Benutzer abgelehnt?", s in (200, 422))
s, h, b = call("GET", f"/api/v1/links/{L1['id']}", USER)
if j(b)["link"]["expires_at"] == "2020-01-01T00:00:00Z":
    s, h, b = call("GET", f"/r/{L1['key']}")
    check("abgelaufen → 404", s == 404)

# ---------------------------------------------------------------- Pixel
print("Zählpixel")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "px", "pin": "1234"})
check("Pixel mit PIN abgelehnt", s == 422)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "px", "title": "Newsletter 9"})
P = j(b)["link"]
s, h, b = call("GET", f"/px/{P['key']}.gif", headers={"User-Agent": "Mozilla/5.0 (iPhone) Safari", "X-Forwarded-For": "2001:db8:1234:5678::1"})
check("Pixel liefert GIF", s == 200 and h.get("Content-Type") == "image/gif" and b[:6] == b"GIF89a" and "no-store" in h.get("Cache-Control", ""))
s, h, b = call("GET", f"/px/{P['key']}")
check("Pixel auch ohne .gif", s == 200 and b[:6] == b"GIF89a")
s, h, b = call("POST", f"/px/{P['key']}", form={})
check("Pixel nimmt keinen POST", s == 404)
s, h, b = call("GET", f"/api/v1/links/{P['id']}/accesses", USER)
acc = j(b)["accesses"]
check("Stufe full: volle IPv6 und User-Agent gespeichert",
      acc[-1]["ip"] == "2001:db8:1234:5678::1" and "iPhone" in (acc[-1]["ua"] or "") and acc[-1]["ua_class"] == "mobile", acc[-1])

# ---------------------------------------------------------- Download once
print("Einmal-Download")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "dl1", "title": "Vertrag"})
D1 = j(b)["link"]
check("Einmal-Download: Höchstzahl 1 fest", D1["max_downloads"] == 1)
s, h, b = call("GET", f"/dl1/{D1['key']}")
check("ohne Datei → 404", s == 404)
content = b"PDF-Inhalt " * 1000
s, h, b = call("PUT", f"/api/v1/links/{D1['id']}/file", OTHER, content, {"X-File-Name": "vertrag.pdf"})
check("fremder Benutzer kann keine Datei anhängen", s == 404)
s, h, b = call("PUT", f"/api/v1/links/{D1['id']}/file", USER, content, {"X-File-Name": "vertrag.pdf", "Content-Type": "application/pdf"})
check("Datei angehängt", s == 201 and j(b)["link"]["file_size"] == len(content) and j(b)["link"]["file_present"] is True, b[:200])
s, h, b = call("GET", f"/dl1/{D1['key']}")
check("GET zeigt Zwischenseite, lädt nicht", s == 200 and b"Herunterladen" in b and b"vertrag.pdf" in b)
s, h, b = call("GET", f"/dl1/{D1['key']}")
check("zweiter GET entwertet nichts", s == 200)
s, h, b = call("GET", f"/api/v1/links/{D1['id']}", USER)
check("Zähler nach zwei GETs bei 0", j(b)["link"]["download_count"] == 0)

results = []
def grab():
    results.append(call("POST", f"/dl1/{D1['key']}", form={"action": "download"}))
ts = [threading.Thread(target=grab) for _ in range(4)]
[t.start() for t in ts]
[t.join() for t in ts]
got = [r for r in results if r[0] == 200]
gone = [r for r in results if r[0] == 410]
check("vier gleichzeitige Klicks: genau ein Download", len(got) == 1 and len(gone) == 3, [r[0] for r in results])
check("Download liefert die Bytes mit Dateinamen", got and got[0][2] == content and "vertrag.pdf" in got[0][1].get("Content-Disposition", ""))
s, h, b = call("GET", f"/dl1/{D1['key']}")
check("danach: 410 verbraucht", s == 410 and b"bereits abgeholt" in b)
s, h, b = call("GET", f"/api/v1/links/{D1['id']}", USER)
check("Bytes entfernt, Name bleibt", j(b)["link"]["file_present"] is False and j(b)["link"]["file_name"] == "vertrag.pdf")
s, h, b = call("GET", f"/api/v1/links/{D1['id']}/accesses", USER)
check("Stufe count: keine Zugriffszeilen, aber Zähler", j(b)["accesses"] == [])
s, h, b = call("GET", f"/api/v1/links/{D1['id']}", USER)
check("hit_count zählt trotzdem", j(b)["link"]["hit_count"] >= 6, j(b)["link"]["hit_count"])

# -------------------------------------------------------- Download window
print("Zeitraum-Download")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "dl", "max_downloads": 2, "pin": "abcd", "valid_until": "2099-01-01T00:00Z"})
check("Zeitraum-Download mit PIN, Höchstzahl 2", s == 201, b[:200])
D2 = j(b)["link"]
s, h, b = call("PUT", f"/api/v1/links/{D2['id']}/file", USER, b"x" * 10, {"X-File-Name": "virus.exe"})
check("Endung nicht erlaubt → 415", s == 415)
s, h, b = call("PUT", f"/api/v1/links/{D2['id']}/file", USER, b"x" * (1024 * 1024 + 1), {"X-File-Name": "gross.txt"})
check("über Area-Grenze (1 MB) → 413", s == 413)
s, h, b = call("PUT", f"/api/v1/links/{D2['id']}/file", USER, b"hallo", {"X-File-Name": "../../etc/passwd.txt"})
check("Dateiname wird bereinigt", s == 201 and j(b)["link"]["file_name"] == "passwd.txt")
s, h, b = call("POST", f"/dl/{D2['key']}", form={"action": "download", "pin": "falsch"})
check("Download mit falscher PIN → 403, nicht gezählt", s == 403)
s, h, b = call("POST", f"/dl/{D2['key']}", form={"action": "download", "pin": "abcd"})
check("Download 1/2", s == 200 and b == b"hallo")
s, h, b = call("POST", f"/dl/{D2['key']}", form={"action": "download", "pin": "abcd"})
check("Download 2/2", s == 200)
s, h, b = call("POST", f"/dl/{D2['key']}", form={"action": "download", "pin": "abcd"})
check("Download 3 → 410", s == 410)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "dl", "valid_from": "2099-01-01T00:00Z"})
D3 = j(b)["link"]
call("PUT", f"/api/v1/links/{D3['id']}/file", USER, b"spaeter", {"X-File-Name": "s.txt"})
s, h, b = call("GET", f"/dl/{D3['key']}")
check("noch nicht offen → 404 mit Hinweis", s == 404 and b"Noch nicht" in b)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "dl", "valid_from": "2099-01-02T00:00Z", "valid_until": "2099-01-01T00:00Z"})
check("bis vor von → 422", s == 422)

# ----------------------------------------------------------------- Upload
print("Einmal-Upload")
s, h, b = call("POST", "/api/v1/links", USER, {"area": "up"})
check("Area verlangt PIN", s == 422 and "PIN" in j(b)["error"]["message"])
s, h, b = call("POST", "/api/v1/links", USER, {"area": "up", "pin": "12"})
check("PIN zu kurz", s == 422)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "up", "pin": "2468", "title": "Bitte Angebot"})
U = j(b)["link"]
check("Upload-Link wartet", s == 201 and U["state"] == "waiting")
s, h, b = call("GET", f"/up/{U['key']}")
check("GET zeigt Upload-Seite", s == 200 and b"Hochladen" in b)
s, h, b = call("PUT", f"/up/{U['key']}", body=b"angebot", headers={"X-File-Name": "angebot.txt", "X-Pin": "0000"})
check("Upload mit falscher PIN → 403", s == 403)
s, h, b = call("PUT", f"/up/{U['key']}", body=b"angebot", headers={"X-File-Name": "angebot.pdf", "X-Pin": "2468"})
check("Upload mit falscher Endung → 415", s == 415)
s, h, b = call("PUT", f"/up/{U['key']}", body=b"angebot", headers={"X-File-Name": "angebot.txt", "X-Pin": "2468"})
check("Upload angenommen → 201 mit Prüfsumme", s == 201 and len(j(b)["sha256"]) == 64, b[:200])
s, h, b = call("PUT", f"/up/{U['key']}", body=b"nochmal", headers={"X-File-Name": "b.txt", "X-Pin": "2468"})
check("zweiter Upload → 410", s == 410)
s, h, b = call("GET", f"/up/{U['key']}")
check("Seite danach: verbraucht (410)", s == 410 and b"bereits" in b)
s, h, b = call("GET", f"/api/v1/links/{U['id']}", USER)
check("Ersteller sieht Eingang", j(b)["link"]["state"] == "received" and j(b)["link"]["file_name"] == "angebot.txt")
s, h, b = call("GET", f"/api/v1/links/{U['id']}/file", USER)
check("Ersteller holt die Datei", s == 200 and b == b"angebot")
s, h, b = call("GET", f"/api/v1/links/{U['id']}/accesses", USER)
acc = j(b)["accesses"]
check("Stufe minimal: Ergebnis ja, Adresse nein", acc and all(a["ip"] is None for a in acc) and any(a["result"] == "uploaded" for a in acc), acc[:2])

# ----------------------------------------------------------- Oberfläche
print("Oberfläche")
s, h, b = call("GET", "/manage", USER)
check("/manage listet eigene Links", s == 200 and L1["key"].encode() in b and b"Neuer Link" in b)
s, h, b = call("GET", f"/manage/links/{L1['id']}", USER)
check("Link-Seite mit URL und Statistik", s == 200 and L1["url"].encode() in b and b"Statistik" in b)
s, h, b = call("GET", f"/manage/links/{L1['id']}", OTHER)
check("fremde Link-Seite → 404", s == 404)
s, h, b = call("POST", "/manage/links", USER, form={"area": "r", "target_url": "https://example.org/form", "title": "Per Formular", "expires_at": "2030-06-01T12:00"})
check("Formular: Benutzer über max_ttl der Area → 422 mit Fehlerkarte", s == 422 and b"Das ging so nicht" in b and b"30 Tage" in b, s)
s, h, b = call("POST", "/manage/links", KEYUSER, form={"area": "r", "target_url": "https://example.org/form", "title": "Per Formular", "expires_at": "2030-06-01T12:00"})
check("Formular legt Link an → 303 zur Link-Seite", s == 303 and "/manage/links/" in h.get("Location", ""), s)
loc = h.get("Location", "").split("?")[0]
s, h, b = call("GET", loc, KEYUSER)
check("neue Link-Seite: lokale Zeit angezeigt", s == 200 and b"01.06.2030 12:00" in b)
s, h, b = call("POST", loc, KEYUSER, form={"_action": "pin", "pin": "5555"})
check("Formular setzt PIN", s == 303)
s, h, b = call("POST", loc, KEYUSER, form={"_action": "edit", "title": "Umbenannt", "target_url": "https://example.org/neu", "expires_at": "2030-06-01T12:00", "note": ""})
check("Formular bearbeitet", s == 303)
s, h, b = call("GET", loc, KEYUSER)
check("Änderung sichtbar", b"Umbenannt" in b and b"https://example.org/neu" in b)
s, h, b = call("POST", loc, KEYUSER, form={"_action": "delete"})
check("Formular löscht", s == 303)
s, h, b = call("GET", loc, KEYUSER)
check("gelöschter Link → 404", s == 404)
s, h, b = call("GET", "/manage/areas", KEYUSER)
check("Keyuser: Area-Übersicht", s == 200 and b"geheim" in b and b'href="/manage/areas/r"' in b)
s, h, b = call("GET", "/manage/areas/r", KEYUSER)
check("Keyuser: Area-Seite mit allen Links", s == 200 and b"Alle Links" in b)
s, h, b = call("GET", "/manage/areas/r", USER)
check("Benutzer: keine Area-Seite", s == 200 and b"verantworten keine" in b)
s, h, b = call("GET", "/admin", ADMIN)
check("/admin listet Areas", s == 200 and b"Neue Area" in b and b"geheim" in b)
s, h, b = call("GET", "/admin", USER)
check("/admin für Benutzer → 403 (Gateway hält ohnehin ab)", s == 403)
s, h, b = call("POST", "/admin/areas", ADMIN, form={"slug": "form", "type": "redirect", "title": "Per Formular", "responsible": "kai, uta", "active": "1", "key_length": "6", "pin_min_length": "4", "default_ttl_days": "7", "max_ttl_days": "0", "max_file_mb": "10", "log_level": "minimal", "log_retention_days": "30"})
check("Formular legt Area an", s == 303, (s, b[:300]))
s, h, b = call("GET", "/api/v1/areas/form", ADMIN)
a = j(b)["area"]
check("Area aus Formular: Kästchen ohne Kreuz = aus, Verantwortliche sortiert",
      a["open_for_users"] is False and a["custom_keys"] is False and a["responsible"] == ["kai", "uta"] and a["default_ttl_days"] == 7, a)
s, h, b = call("POST", "/admin/areas/form", ADMIN, form={"title": "Geändert", "open_for_users": "1", "active": "1", "key_length": "6", "pin_min_length": "4", "default_ttl_days": "7", "max_ttl_days": "0", "max_file_mb": "10", "log_level": "minimal", "log_retention_days": "30", "responsible": "kai"})
check("Formular ändert Area", s == 303)
s, h, b = call("POST", "/api/v1/links", USER, {"area": "form", "target_url": "https://example.org"})
check("Vorgabe-Lebensdauer 7 Tage greift", s == 201 and j(b)["link"]["expires_at"] is not None)
s, h, b = call("POST", "/admin/areas/form", ADMIN, form={"_action": "delete"})
check("Formular löscht Area", s == 303)
s, h, b = call("GET", "/api/v1/areas/form", ADMIN)
check("Area weg, Links weg", s == 404)

# ------------------------------------------------------------- Aufräumen
print("Aufräumen")
rows_before = app.DB.one("SELECT COUNT(*) n FROM accesses")["n"]
app.DB.x("UPDATE accesses SET ts='2020-01-01T00:00:00Z' WHERE area='r'")
app.DB.x("UPDATE links SET valid_until='2020-01-01T00:00:00Z' WHERE id=?", (D3["id"],))
removed_rows, removed_files = store.sweep(app.DB, app.FILES_DIR, lambda m: None)
check("alte Zugriffszeilen weg", removed_rows > 0 and app.DB.one("SELECT COUNT(*) n FROM accesses WHERE area='r'")["n"] == 0)
check("Datei des abgelaufenen Downloads weg", removed_files >= 1 and not os.path.exists(store.file_path(app.FILES_DIR, store.get_link(app.DB, D3["id"]))))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}", USER)
check("Eintrag bleibt mit Namen", j(b)["link"]["file_name"] == "s.txt" and j(b)["link"]["file_present"] is False)
s, h, b = call("DELETE", f"/api/v1/links/{D2['id']}", USER)
check("Link per API löschen", s == 200)
s, h, b = call("GET", f"/dl/{D2['key']}")
check("gelöschter Link öffentlich 404", s == 404)

# ------------------------------------------------------------ Rohbausteine
print("Bausteine")
check("short_ip v4", store.short_ip("203.0.113.77") == "203.0.113.0/24")
check("short_ip v6", store.short_ip("2001:db8:1234:5678::1") == "2001:db8:1234::/48")
check("short_ip Unsinn", store.short_ip("keine") == "")
check("UA: Vorschau-Crawler als bot", store.classify_ua("WhatsApp/2.23")[0] == "bot")
check("UA: Google-Bildproxy als bot", store.classify_ua("Mozilla/5.0 (Windows NT 5.1; rv:11.0) Gecko Firefox/11.0 (via ggpht.com GoogleImageProxy)")[0] == "bot")
check("valid_key Grenzen", store.valid_key("abc12345") and not store.valid_key("abc123456") and not store.valid_key("ab-c") and not store.valid_key("ü"))
check("safe_filename", store.safe_filename('C:\\x\\ab<c>.txt') == "abc.txt")
check("parse_when lokal → UTC", app.parse_when("2030-06-01T12:00").endswith("Z"))
check("parse_when Z", app.parse_when("2030-06-01T12:00:00Z") == "2030-06-01T12:00:00Z")
try:
    app.parse_when("gestern")
    check("parse_when Unsinn wirft", False)
except ValueError:
    check("parse_when Unsinn wirft", True)

# ------------------------------------------------------------------ QR-Code
print("QR-Code")
import struct  # noqa: E402

import qr  # noqa: E402

m = qr.encode("a")
check("QR: kürzester Text ist Version 1 mit 21 Modulen", len(m) == 21 and all(len(r) == 21 for r in m))
check("QR: Suchmuster oben links", [m[0][i] for i in range(7)] == [1] * 7 and [m[i][0] for i in range(7)] == [1] * 7
      and m[1][1] == 0 and m[3][3] == 1)
check("QR: Taktspur", [m[6][i] for i in range(8, 13)] == [1, 0, 1, 0, 1])
check("QR: 213 Byte passen (Version 10, 57 Module)", len(qr.encode("x" * 213)) == 57)
try:
    qr.encode("x" * 214)
    check("QR: 214 Byte werden abgelehnt", False)
except ValueError:
    check("QR: 214 Byte werden abgelehnt", True)
png = qr.png(m, 8)
check("QR: PNG-Signatur und Bildgröße (21+8)·8", png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (232, 232))
check("QR: SVG mit Ruhezone", 'viewBox="0 0 29 29"' in qr.svg(m))
# Orakel: dieselbe Matrix, die segno 1.6 für diese 26-Byte-Adresse mit Maske 6 erzeugt
# (26 Byte füllen Version 2 genau — dort sind beide Encoder Bit für Bit gleich)
ORACLE = ("1fd0c7f;1053841;175c85d;174fb5d;175845d;1047541;1fd557f;000e800;13f1297;1d2be3e;1b69b29;1f1742f;"
          "0d4d1e1;1031b12;1a4129f;15ab62d;105edf6;0015916;1fda151;1051b10;17547f0;17536c3;174759f;10494b7;1fd7189")
rows = [int("".join(map(str, row)), 2) for row in qr.encode("https://go.joomp.de/r/ab12", mask=6)]
check("QR: Matrix Bit für Bit wie segno (unabhängiges Orakel)", rows == [int(h, 16) for h in ORACLE.split(";")])
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr", USER)
check("QR per API: PNG", s == 200 and h.get("Content-Type") == "image/png" and b[:8] == b"\x89PNG\r\n\x1a\n" and not h.get("Content-Disposition"))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?format=svg&download=1", USER)
check("QR per API: SVG als Datei", s == 200 and str(h.get("Content-Type", "")).startswith("image/svg+xml") and b"<svg" in b
      and h.get("Content-Disposition") == f'attachment; filename="{D3["area"]}-{D3["key"]}.svg"', (s, h.get("Content-Disposition")))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?format=gif", USER)
check("QR: unbekanntes Format 422", s == 422)
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr", OTHER)
check("QR: fremder Link 404", s == 404)
s, h, b = call("GET", f"/manage/links/{D3['id']}", USER)
check("Link-Seite zeigt QR-Code", s == 200 and f"/api/v1/links/{D3['id']}/qr?format=svg".encode() in b)

# ---------------------------------------------------- Adressen (0.3)
print("Öffentliche Adressen")
hosts, errs = store.normalize_hosts("Go.joomp.de/\ngo.objid.info, go.joomp.de\nhttp://localhost:8114\n127.0.0.1:8114\nhttps://x.example/pfad")
check("Adressen: normalisiert, ohne Doppelte, http nur für localhost/127.", hosts == [
    "https://go.joomp.de", "https://go.objid.info", "http://localhost:8114", "http://127.0.0.1:8114", "https://x.example"] and not errs, (hosts, errs))
check("Adressen: kein Hostname → Fehler", store.normalize_hosts("bad host!")[1] and store.normalize_hosts("a_b.de")[1])
check("Adressen: zu viele → Fehler", store.normalize_hosts([f"h{i}.example" for i in range(21)])[1])
s, h, b = call("GET", "/api/v1/hosts", USER)
check("Adressen: ohne Eintrag gilt die Adresse der Anfrage", s == 200 and j(b) == {"hosts": [], "platform": [], "extra": [], "default": BASE, "configured": False}, b)
s, h, b = call("PUT", "/api/v1/hosts", USER, {"hosts": ["go.joomp.de"]})
check("Adressen: nur admin schreibt", s == 403)
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": ["go.joomp.de", "bad host!"]})
check("Adressen: ungültige Zeile → 422, nichts gespeichert", s == 422 and j(call("GET", "/api/v1/hosts", USER)[2])["hosts"] == [])
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": ["Go.joomp.de", "go.objid.info", "go.joomp.de"]})
check("Adressen: admin trägt zwei ein", s == 200 and j(b) == {"hosts": ["https://go.joomp.de", "https://go.objid.info"], "platform": [], "extra": ["https://go.joomp.de", "https://go.objid.info"], "default": "https://go.joomp.de", "configured": True}, b)
s, h, b = call("GET", f"/api/v1/links/{D3['id']}", USER)
L = j(b).get("link", {})
check("Link: url nutzt die erste Adresse, urls alle", L.get("url") == f"https://go.joomp.de/{D3['area']}/{D3['key']}"
      and L.get("urls") == [f"https://go.joomp.de/{D3['area']}/{D3['key']}", f"https://go.objid.info/{D3['area']}/{D3['key']}"], L.get("urls"))
s, h, b = call("GET", "/api/v1/links", USER)
check("Link-Liste: url ebenfalls", all(l["url"].startswith("https://go.joomp.de/") for l in j(b)["links"]))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr", USER)
check("QR ohne host: Code der ersten Adresse", s == 200 and b == qr.png(qr.encode(f"https://go.joomp.de/{D3['area']}/{D3['key']}"), 8))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?host=go.objid.info&scale=6", USER)
check("QR mit host: Code der gewählten Adresse", s == 200 and b == qr.png(qr.encode(f"https://go.objid.info/{D3['area']}/{D3['key']}"), 6))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?host=https://go.objid.info", USER)
check("QR mit host als Ursprung", s == 200 and b == qr.png(qr.encode(f"https://go.objid.info/{D3['area']}/{D3['key']}"), 8))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?host=evil.example", USER)
check("QR mit fremdem host: 422", s == 422 and j(b)["error"]["code"] == "validation")
s, h, b = call("GET", f"/manage/links/{D3['id']}", USER)
check("Link-Seite: Adress-Auswahl, Vorgabe gewählt", s == 200 and b'class="btn quiet on" href="/manage/links/' in b
      and f"?host=go.objid.info".encode() in b and f'id="theurl">https://go.joomp.de/{D3["area"]}/{D3["key"]}<'.encode() in b)
s, h, b = call("GET", f"/manage/links/{D3['id']}?host=go.objid.info", USER)
check("Link-Seite mit host: Adresse, QR und Downloads folgen", s == 200
      and f'id="theurl">https://go.objid.info/{D3["area"]}/{D3["key"]}<'.encode() in b
      and f"/qr?format=png&amp;scale=10&amp;download=1&amp;host=go.objid.info".encode() in b)
s, h, b = call("GET", f"/manage/links/{D3['id']}?host=evil.example", USER)
check("Link-Seite mit fremdem host: Vorgabe", s == 200 and f'id="theurl">https://go.joomp.de/{D3["area"]}/{D3["key"]}<'.encode() in b)
s, h, b = call("GET", "/admin", ADMIN)
check("Verwaltung zeigt die Adressen", s == 200 and b'<textarea name="hosts"' in b and b"https://go.joomp.de\nhttps://go.objid.info</textarea>" in b)
s, h, b = call("POST", "/admin/hosts", ADMIN, form={"hosts": "go.joomp.de\nnicht gut!"})
check("Verwaltung: ungültige Zeile bleibt im Formular, 422", s == 422 and b"nicht gut!</textarea>" in b
      and j(call("GET", "/api/v1/hosts", USER)[2])["hosts"] == ["https://go.joomp.de", "https://go.objid.info"])
s, h, b = call("POST", "/admin/hosts", ADMIN, form={"hosts": "go.objid.info"})
check("Verwaltung: Formular speichert", s == 303 and j(call("GET", "/api/v1/hosts", USER)[2])["hosts"] == ["https://go.objid.info"])
s, h, b = call("POST", "/admin/hosts", USER, form={"hosts": "x.example"})
check("Verwaltung: Formular nur admin", s == 403)
s, h, b = call("GET", f"/manage/links/{D3['id']}", USER)
check("Eine Adresse: keine Auswahl auf der Link-Seite", s == 200 and b"Adresse:" not in b and b"?host=" not in b)
s, h, b = call("POST", "/admin/hosts", ADMIN, form={"hosts": ""})
check("Verwaltung: leer = Adresse der Anfrage", s == 303 and j(call("GET", "/api/v1/hosts", USER)[2]) == {"hosts": [], "platform": [], "extra": [], "default": BASE, "configured": False})
s, h, b = call("GET", f"/api/v1/links/{D3['id']}", USER)
check("Link: url wieder aus der Anfrage", j(b)["link"]["url"] == f"{BASE}/{D3['area']}/{D3['key']}")

# ------------------------------------- Namen von der Plattform (RFC-0043, 0.4)
print("Namen von der Plattform")
check("Ohne OAAP_INSTANCE_NAMES ist die Plattform-Liste leer", app.PLATFORM_HOSTS == [])
app.PLATFORM_HOSTS = store.normalize_hosts("https://go.plattform.example,https://go.alias.example,https://wegweiser.node.example")[0]
check("Die Variable wird als Liste von Ursprüngen gelesen, Reihenfolge bleibt",
      app.PLATFORM_HOSTS == ["https://go.plattform.example", "https://go.alias.example", "https://wegweiser.node.example"])
s, h, b = call("GET", "/api/v1/hosts", USER)
check("Plattform-Namen sind die Liste, der Hauptname die Vorgabe", s == 200 and j(b)["hosts"] == app.PLATFORM_HOSTS
      and j(b)["platform"] == app.PLATFORM_HOSTS and j(b)["extra"] == [] and j(b)["default"] == "https://go.plattform.example" and j(b)["configured"], b)
s, h, b = call("GET", f"/api/v1/links/{D3['id']}", USER)
check("Link: url unter dem Hauptnamen der Plattform", j(b)["link"]["url"] == f"https://go.plattform.example/{D3['area']}/{D3['key']}")
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": ["go.extra.example", "go.alias.example"]})
check("Ergänzte Adressen kommen dahinter, ein Plattform-Name wird nicht doppelt",
      s == 200 and j(b)["hosts"] == app.PLATFORM_HOSTS + ["https://go.extra.example"] and j(b)["extra"] == ["https://go.extra.example", "https://go.alias.example"], b)
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": ["go.extra.example"], "default": "https://go.extra.example"})
check("Die Verwaltung wählt eine ergänzte Adresse als Vorgabe", s == 200 and j(b)["default"] == "https://go.extra.example"
      and j(b)["hosts"][0] == "https://go.extra.example" and "https://go.plattform.example" in j(b)["hosts"], b)
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr", USER)
check("QR folgt der gewählten Vorgabe", s == 200 and b == qr.png(qr.encode(f"https://go.extra.example/{D3['area']}/{D3['key']}"), 8))
s, h, b = call("GET", f"/api/v1/links/{D3['id']}/qr?host=go.alias.example", USER)
check("QR unter einem Plattform-Alias", s == 200 and b == qr.png(qr.encode(f"https://go.alias.example/{D3['area']}/{D3['key']}"), 8))
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": [], "default": "https://fremd.example"})
check("Eine Vorgabe außerhalb der Liste: 422", s == 422)
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": [], "default": ""})
check("Keine ergänzten, keine Wahl: Plattform-Liste, Hauptname vorn", s == 200 and j(b)["hosts"] == app.PLATFORM_HOSTS and j(b)["default"] == "https://go.plattform.example")
s, h, b = call("GET", "/admin", ADMIN)
check("Verwaltung zeigt die Plattform-Namen zum Lesen und die Vorgabe zur Wahl",
      s == 200 and b"<code>https://go.plattform.example</code>" in b and b'<select name="default">' in b
      and b'<option value="https://go.alias.example">' in b and b'<textarea name="hosts"' in b)
s, h, b = call("POST", "/admin/hosts", ADMIN, form={"hosts": "", "default": "https://go.alias.example"})
check("Verwaltung: Vorgabe per Formular", s == 303 and j(call("GET", "/api/v1/hosts", USER)[2])["default"] == "https://go.alias.example")
s, h, b = call("POST", "/admin/hosts", ADMIN, form={"hosts": "", "default": "https://fremd.example"})
check("Verwaltung: Vorgabe außerhalb der Liste bleibt im Formular, 422", s == 422 and b"Vorgabe: muss" in b)
s, h, b = call("GET", f"/manage/links/{D3['id']}", USER)
check("Link-Seite: alle Plattform-Namen wählbar, Vorgabe vorn", s == 200 and b"?host=go.plattform.example" in b
      and b"?host=wegweiser.node.example" in b and f'id="theurl">https://go.alias.example/{D3["area"]}/{D3["key"]}<'.encode() in b)
app.PLATFORM_HOSTS = []
s, h, b = call("PUT", "/api/v1/hosts", ADMIN, {"hosts": [], "default": ""})
check("Zurückgesetzt: wieder die Adresse der Anfrage", s == 200 and j(b)["default"] == BASE)

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} bestanden, {fail} fehlgeschlagen")
sys.exit(1 if fail else 0)
