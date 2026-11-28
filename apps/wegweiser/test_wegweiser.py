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
check("Link-Seite: Adress-Auswahl, Vorgabe gewählt", s == 200 and b'class="oaap-btn primary" aria-current="true" href="/manage/links/' in b
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

# ------------------------------------- Rahmen, Listen, MCP-Tür (0.5.0, RFC-0118)
print("Rahmen, Listen, MCP-Tür")
s, h, b = call("GET", "/manage", USER)
check("angemeldete Seite: Kit und Rahmen der Plattform", s == 200 and b"/platform/theme.css" in b
      and b"/platform/app.js" in b and b'class="oaap-appname">Wegweiser<' in b)
check("... ohne eigene Palette, ohne Rollen im Kopf, ohne Großbuchstaben-Kopf",
      b"--oaap-blue-900" not in b and b"WEGWEISER" not in b and b"keyuser" not in b and b"Uta" not in b)
check("Benutzer: Reiter Meine Links und Kampagnen, kein Verwaltungsreiter",
      b">Meine Links<" in b and b">Kampagnen<" in b and b'href="/admin"' not in b)
s, h, b = call("GET", "/manage", ADMIN)
check("admin: Reiter Meine Links, Areas, Verwaltung zuletzt",
      b.index(b">Meine Links<") < b.index(b">Areas<") < b.index(b">Verwaltung<"))
s, h, b = call("GET", "/manage/new", USER)
check("Neuer Link auf eigener Seite, mit zurück", s == 200 and b'action="/manage/links"' in b and b"oaap-back" in b)
s, h, b = call("GET", "/", None)
check("öffentlich: eigene Optik, nichts von der Plattform", s == 404 and b"/platform/" not in b and b"oaap-header" not in b)
s, h, b = call("GET", f"/{D3['area']}/{D3['key']}", None)
check("öffentliche Zwischenseite unverändert ohne Rahmen", b"/platform/" not in b and b"oaap-header" not in b, s)

for n in range(60):
    call("POST", "/api/v1/links", USER, {"area": "r", "target_url": f"https://example.org/viele/{n}", "title": f"Viele {n:02d}"})
s, h, b = call("GET", "/manage", USER)
check("Listenbericht: Zählzeile und Blättern ab 50", b"1\xe2\x80\x9350 von" in b and b"offset=50" in b, b[-600:])
s, h, b = call("GET", "/manage?q=Viele+07", USER)
check("Suche über Titel", b"Viele 07" in b and b"Viele 08" not in b)
s, h, b = call("GET", "/manage?sort=titel", USER)
check("Sortierung nach Titel aufsteigend", b'aria-sort="ascending"' in b and b.index(b"Viele 00") < b.index(b"Viele 01"))
s, h, b = call("GET", "/manage?status=disabled", USER)
check("Filter Status: keine deaktivierten -> sagt es", b"Keine Treffer" in b)
s, h, b = call("GET", "/admin", ADMIN)
check("Verwaltung: Areas als Listenbericht, Neue Area als Knopf", b"oaap-filter" in b and b'href="/admin/new"' in b)
s, h, b = call("GET", "/admin/new", ADMIN)
check("Neue Area auf eigener Seite", s == 200 and b'action="/admin/areas"' in b)
s, h, b = call("POST", "/admin/areas", ADMIN, form={"slug": "admin", "type": "redirect"})
check("Neue Area mit Fehler: 422 auf der Seite Neue Area", s == 422 and b"Das ging so nicht" in b and b'action="/admin/areas"' in b)


def mcp(msg, who):
    s, h, b = call("POST", "/mcp", who, msg)
    return s, j(b) if b else {}


def tool(name, args, who):
    s, d = mcp({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": name, "arguments": args}}, who)
    r = d.get("result") or {}
    t = (r.get("content") or [{}])[0].get("text", "")
    return r.get("isError"), t


s, d = mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, USER)
names = sorted(t["name"] for t in d["result"]["tools"])
check("MCP tools/list", s == 200 and names == ["wegweiser_areas", "wegweiser_campaigns", "wegweiser_link_read", "wegweiser_search"], names)
err, t = tool("wegweiser_search", {"q": "Viele 07"}, USER)
check("wegweiser_search: eigener Link", not err and j(t.encode())["total"] == 1, t[:300])
check("... ohne PIN-Hash oder Fehlversuche", "pin_hash" not in t and "pin_failures" not in t)
err, t = tool("wegweiser_search", {"q": "Viele 07"}, OTHER)
check("wegweiser_search: fremde Person sieht ihn nicht", not err and j(t.encode())["total"] == 0, t[:200])
err, t = tool("wegweiser_search", {"area": "r", "q": "Viele 07"}, KEYUSER)
check("wegweiser_search: Verantwortlicher der Area sieht ihn", not err and j(t.encode())["total"] == 1, t[:200])
err, t = tool("wegweiser_link_read", {"link": f"{D3['area']}/{D3['key']}"}, USER)
check("wegweiser_link_read nach area/key", not err and j(t.encode())["id"] == D3["id"], t[:300])
err, t = tool("wegweiser_link_read", {"link": D3["id"]}, OTHER)
check("wegweiser_link_read: fremder Link abgelehnt wie die Seite", err is True, t)
err, t = tool("wegweiser_areas", {}, USER)
check("wegweiser_areas: nur nutzbare Areas, geheim nicht", not err and "geheim" not in t and '"r"' in t, t[:300])
err, t = tool("wegweiser_search", {}, {})
check("ohne Person: abgelehnt", err is True)
s, d = mcp({"jsonrpc": "2.0", "id": 2, "method": "prompts/list"}, USER)
check("zwei Vorlagen", len(d["result"]["prompts"]) == 2)


print("\n== Kampagnen (0.7, RFC-0144 §10)")
import sqlite3  # noqa: E402
old = os.path.join(TMP, "alt.db")
con = sqlite3.connect(old)
con.execute("CREATE TABLE links (id TEXT PRIMARY KEY, area TEXT NOT NULL, key TEXT NOT NULL, key_norm TEXT NOT NULL, "
            "created_by TEXT NOT NULL, created_at TEXT NOT NULL, deleted_at TEXT, hit_count INTEGER NOT NULL DEFAULT 0)")
con.commit()
con.close()
store.connect(old)
cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(links)")}
check("alte Datenbank: Spalten campaign_id und channel beim Start ergänzt", {"campaign_id", "channel"} <= cols, cols)

s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "Sommerfest 2027", "starts_on": "2027-06-01", "ends_on": "2027-07-15"})
C1 = j(b).get("campaign", {})
check("Kampagne anlegen: 201, Kurzname aus dem Titel", s == 201 and C1.get("key") == "sommerfest-2027", b[:300])
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "Plakate", "parent": "sommerfest-2027"})
C2 = j(b).get("campaign", {})
check("Unterkampagne über den Kurznamen der Eltern", s == 201 and C2.get("parent_id") == C1["id"]
      and C2.get("path") == ["Sommerfest 2027", "Plakate"] and C2.get("depth") == 2, b[:300])
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "Innenstadt", "parent": C2["id"]})
C3 = j(b).get("campaign", {})
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "Ebene 4", "parent": C3["id"]})
C4 = j(b).get("campaign", {})
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "Ebene 5", "parent": C4["id"]})
check("fünfte Ebene abgelehnt", s == 422 and "Ebenen" in j(b)["error"]["message"], b[:200])
s, h, b = call("POST", "/api/v1/campaigns", OTHER, {"title": "Sommerfest 2027"})
check("gleicher Titel: Kurzname bekommt -2", s == 201 and j(b)["campaign"]["key"] == "sommerfest-2027-2", b[:200])
C5 = j(b)["campaign"]
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "X", "key": "Mit Leerzeichen"})
check("Kurzname mit Leerzeichen abgelehnt", s == 422, b[:200])
s, h, b = call("POST", "/api/v1/campaigns", USER, {"title": "X", "ends_on": "2027-01-01", "starts_on": "2027-02-01"})
check("Ende vor Beginn abgelehnt", s == 422, b[:200])
s, h, b = call("PATCH", f"/api/v1/campaigns/{C1['id']}", USER, {"parent": C3["id"]})
check("Kampagne unter die eigene Unterkampagne hängen: abgelehnt", s == 422 and "selbst" in j(b)["error"]["message"], b[:200])
s, h, b = call("PATCH", f"/api/v1/campaigns/{C1['id']}", USER, {"key": "anders"})
check("Kurzname bleibt fest", s == 422, b[:200])
s, h, b = call("PATCH", f"/api/v1/campaigns/{C1['id']}", OTHER, {"title": "Fremd"})
check("fremde Person darf eine Kampagne nicht ändern", s == 403, s)
s, h, b = call("PATCH", f"/api/v1/campaigns/{C1['id']}", ADMIN, {"description": "Das Fest im Park"})
check("Verwaltung darf ändern", s == 200 and j(b)["campaign"]["description"] == "Das Fest im Park", b[:200])

s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org/fest",
                                               "title": "Plakat Marktplatz", "campaign": C3["id"], "channel": "print"})
K1 = j(b).get("link", {})
check("Link mit Kampagne und Kanal", s == 201 and K1.get("channel") == "print"
      and (K1.get("campaign") or {}).get("path") == ["Sommerfest 2027", "Plakate", "Innenstadt"], b[:400])
s, h, b = call("POST", "/api/v1/links", OTHER, {"area": "r", "target_url": "https://example.org/fest2",
                                                "title": "Olafs Post", "campaign": "sommerfest-2027", "channel": "social"})
K2 = j(b).get("link", {})
check("fremde Person ordnet ihren Link der Kampagne über den Kurznamen zu", s == 201 and K2["campaign"]["id"] == C1["id"], b[:300])
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org/x", "channel": "fax"})
check("unbekannter Kanal abgelehnt", s == 422, b[:200])
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org/x", "campaign": "gibtsnicht"})
check("unbekannte Kampagne abgelehnt", s == 422, b[:200])

for ua in ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1",) * 3:
    call("GET", f"/r/{K1['key']}", headers={"User-Agent": ua, "Referer": "https://www.instagram.com/"})
call("GET", f"/r/{K2['key']}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/120"})
s, h, b = call("GET", f"/api/v1/campaigns/{C1['key']}/stats?days=7", USER)
st = j(b).get("stats", {})
check("Zahlen der Oberkampagne zählen den ganzen Teilbaum", s == 200 and st.get("links") == 2 and st.get("recent") == 4, st)
check("... je Kanal", st.get("by_channel") == {"print": 3, "social": 1}, st.get("by_channel"))
check("... je Unterkampagne", [(k["key"], k["recent"]) for k in st.get("by_child", [])] == [("plakate", 3)], st.get("by_child"))
check("... je Link nur die eigenen", set(st.get("by_link", {})) == {K1["id"]}, st.get("by_link"))
s, h, b = call("GET", f"/api/v1/campaigns/{C1['id']}", USER)
d = j(b)
check("Kampagne lesen: Summe über den Teilbaum, fremde Links nicht in der Liste",
      d["campaign"]["totals"]["links"] == 2 and [l["id"] for l in d["links"]] == [K1["id"]], b[:400])
s, h, b = call("GET", "/api/v1/campaigns", OTHER)
tot = {c["key"]: c["totals"]["recent"] for c in j(b)["campaigns"]}
check("Liste: alle sehen alle Kampagnen mit Summen", tot.get("sommerfest-2027") == 4 and tot.get("innenstadt") == 3, tot)

s, h, b = call("DELETE", f"/api/v1/campaigns/{C1['id']}", USER)
check("Kampagne mit Unterkampagnen: nicht löschen (409)", s == 409, b[:200])
s, h, b = call("DELETE", f"/api/v1/campaigns/{C3['id']}", USER)
check("Kampagne mit Links: nicht löschen (409)", s == 409 and "archivieren" in j(b)["error"]["message"], b[:200])
s, h, b = call("PATCH", f"/api/v1/campaigns/{C2['id']}", USER, {"archived": True})
check("archivieren", s == 200 and j(b)["campaign"]["archived"] is True, b[:200])
s, h, b = call("POST", "/api/v1/links", USER, {"area": "r", "target_url": "https://example.org/y", "campaign": C2["id"]})
check("archivierte Kampagne nimmt keine neuen Links", s == 422, b[:200])
s, h, b = call("PATCH", f"/api/v1/links/{K1['id']}", USER, {"title": "Plakat Marktplatz (neu)"})
check("bestehender Link behält seine Kampagne beim Ändern", s == 200 and j(b)["link"]["campaign"]["id"] == C3["id"], b[:300])
s, h, b = call("DELETE", f"/api/v1/campaigns/{C4['id']}", USER)
check("leere Kampagne löschen", s == 200, b[:200])
s, h, b = call("DELETE", f"/api/v1/campaigns/{C5['id']}", USER)
check("fremde leere Kampagne: nicht löschen (403)", s == 403, s)

s, h, b = call("GET", "/manage/campaigns", USER)
check("Seite Kampagnen: Baum und Formular", s == 200 and "Sommerfest 2027".encode() in b and b'name="parent"' in b, s)
check("... archivierte ausgeblendet mit Hinweis", b">Plakate<" not in b and b"archivierte zeigen" in b)
s, h, b = call("POST", "/manage/campaigns", USER, form={"title": "Herbstmarkt", "parent": ""})
check("Formular legt an und leitet zur Kampagne", s == 303 and "/manage/campaigns/" in h.get("Location", ""), s)
s, h, b = call("GET", f"/manage/campaigns/{C1['id']}", USER)
check("Kampagnen-Seite: Zahlen, Kanal, Hinweis auf fremde Links",
      s == 200 and b"Neuer Link in dieser Kampagne" in b and b"Druck" in b and b"Links anderer Personen" in b, s)
check("... Bearbeiten für die Erstellerin", b'value="edit"' in b)
s, h, b = call("GET", f"/manage/campaigns/{C1['id']}", OTHER)
check("... kein Bearbeiten für andere", s == 200 and b'value="edit"' not in b)
s, h, b = call("GET", f"/manage/new?campaign={C3['id']}", USER)
check("Neuer Link aus der Kampagne: vorgewählt", f'value="{C3["id"]}" selected'.encode() in b)
s, h, b = call("POST", "/manage/links", USER, form={"area": "r", "target_url": "https://example.org/z",
                                                    "campaign": C3["id"], "channel": "mail"})
lid = h.get("Location", "").split("/manage/links/")[-1].split("?")[0]
check("Link über das Formular mit Kampagne", s == 303 and store.get_link(app.DB, lid)["channel"] == "mail"
      and store.get_link(app.DB, lid)["campaign_id"] == C3["id"], s)
s, h, b = call("GET", f"/manage/links/{lid}", USER)
check("Link-Seite nennt Kampagne mit Pfad und Kanal", "Sommerfest 2027 › Plakate › Innenstadt".encode() in b and b"Kanal E-Mail" in b)

err, t = tool("wegweiser_campaigns", {}, OTHER)
check("MCP wegweiser_campaigns: Baum mit Aufrufen", not err and any(c["key"] == "sommerfest-2027" and c["calls"] == 4
                                                                       for c in j(t.encode())["campaigns"]), t[:300])
err, t = tool("wegweiser_campaigns", {"campaign": "sommerfest-2027", "days": 7}, USER)
check("MCP: eine Kampagne je Kanal", not err and j(t.encode())["by_channel"] == {"print": 3, "social": 1}, t[:300])
err, t = tool("wegweiser_campaigns", {}, {})
check("MCP Kampagnen ohne Person: abgelehnt", err is True)


# ---------------------------------------------- RFC-0144 §3: der Vertrag für die Plattform
print("\nVertrag oaap.work.shortlinks (0.8)")
PLATFORM = {"X-OAAP-User": "platform:shortlinks", "X-OAAP-User-Id": "p-short",
            "X-OAAP-Roles": "user,admin,keyuser", "X-OAAP-Display-Name": "Plattform", "X-OAAP-Email": ""}
s, h, b = call("GET", "/api/v1/about", USER)
check("about für jeden: Vertrag und Version, noch keine Plattform-Area",
      s == 200 and j(b)["contract"] == "oaap.work.shortlinks" and j(b)["version"] == app.VERSION
      and j(b)["area"] is None, b[:200])
s, h, b = call("GET", "/api/v1/about", PLATFORM)
PA = j(b).get("area")
check("about der Plattform legt ihre Area an (k)", s == 200 and PA == "k", b[:200])
area = store.get_area(app.DB, PA)
check("... Weiterleitung, Stufe anonymous, für niemanden sonst offen",
      area["type"] == "redirect" and area["log_level"] == "anonymous" and not area["open_for_users"])
s, h, b = call("GET", "/api/v1/about", PLATFORM)
check("... ein zweiter Ruf legt keine zweite an", j(b)["area"] == "k"
      and len([a for a in store.list_areas(app.DB) if a["title"] == "Kurzlinks der Plattform"]) == 1)
s, h, b = call("POST", "/api/v1/links", PLATFORM, body={"area": "r", "target_url": "https://example.org/x"})
check("Plattform darf nicht in fremde Areas, auch mit admin im Schlüssel", s == 403, s)
s, h, b = call("GET", "/api/v1/areas", PLATFORM)
check("... und sieht keine fremden Areas", s == 200 and [a["slug"] for a in j(b)["areas"]] == ["k"], b[:200])
s, h, b = call("POST", "/api/v1/campaigns", PLATFORM, body={"title": "Flyer Winter"})
check("Plattform legt eine Kampagne an", s == 201, b[:200])
s, h, b = call("POST", "/api/v1/links", PLATFORM, body={
    "area": "k", "target_url": "https://example.org/winter", "campaign": "flyer-winter",
    "channel": "print", "owner": "website", "title": "Flyer"})
PL = j(b).get("link") or {}
check("Plattform legt einen Link mit Besitzer-Instanz an", s == 201 and PL.get("owner") == "website"
      and PL.get("channel") == "print" and (PL.get("campaign") or {}).get("key") == "flyer-winter", b[:300])
s, h, b = call("POST", "/api/v1/links", PLATFORM, body={"area": "k", "target_url": "https://e.org/", "owner": "Böse Instanz"})
check("... ein Besitzer, der kein Instanzname ist: 422", s == 422, s)
s, h, b = call("POST", "/api/v1/links", USER, body={"area": "r", "target_url": "https://example.org/u", "owner": "website"})
check("Menschen setzen keinen Besitzer (Feld überhört)", s == 201 and j(b)["link"]["owner"] == "", b[:200])
s, h, b = call("POST", "/api/v1/links", ADMIN, body={"area": "k", "target_url": "https://example.org/a"})
check("Admin darf in die Plattform-Area (verwaltet alles)", s == 201, s)
s, h, b = call("POST", "/api/v1/links", USER, body={"area": "k", "target_url": "https://example.org/a"})
check("Benutzer nicht: sie ist nicht offen", s == 403, s)

# zählen ohne Adresse
for ua, ref in (("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1", "https://www.instagram.com/p/1"),
                ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1", "https://www.instagram.com/p/1"),
                ("Mozilla/5.0 (Windows NT 10.0; Win64) Chrome/120 Safari/537.36", "")):
    call("GET", f"/k/{PL['key']}", None, headers={"User-Agent": ua, "Referer": ref, "X-Forwarded-For": "203.0.113.7",
                                                   "Accept-Language": "de-DE"})
rows = app.DB.q("SELECT * FROM accesses WHERE area='k'")
check("Stufe anonymous: drei Zeilen, keine Adresse, kein User-Agent, keine Sprache",
      len(rows) == 3 and all(r["ip"] is None and r["ua"] is None and r["lang"] is None for r in rows), [dict(r) for r in rows][:1])
check("... aber Herkunft (nur Host) und Geräteklasse",
      sorted(r["referer"] or "" for r in rows) == ["", "www.instagram.com", "www.instagram.com"]
      and {r["ua_class"] for r in rows} == {"mobile", "desktop"})
check("... und eine Tagesmarke statt der Adresse", all(r["visitor"] and len(r["visitor"]) == 12 for r in rows)
      and "203.0.113" not in json.dumps([dict(r) for r in rows]))
salt = store.get_setting(app.DB, "visitor_salt", "")
check("... das Salz gilt nur für heute", salt.startswith(store.now_iso()[:10] + ":"))
s, h, b = call("GET", f"/api/v1/areas/k/accesses?format=json", ADMIN)
check("Zugriffsliste gibt die Tagesmarke nicht heraus", s == 200 and b"visitor" not in b, b[:200])

s, h, b = call("GET", f"/api/v1/stats?day={store.now_iso()[:10]}", PLATFORM)
st = j(b) if s == 200 else {}
one = next((x for x in st.get("links", []) if x["id"] == PL["id"]), {})
check("stats?day= für die Plattform: ein Ruf, alle Links ihrer Area", s == 200 and st["area"] == "k" and one, b[:300])
check("... Aufrufe 3, Besucher 2 (zwei Geräte), Herkunft und Gerät",
      one.get("calls") == 3 and one.get("visitors") == 2 and one.get("by_referer") == {"www.instagram.com": 2}
      and one.get("by_class") == {"mobile": 2, "desktop": 1}, one)
check("... mit Kampagnen-Key, Kanal und Besitzer",
      one.get("campaign") == "flyer-winter" and one.get("channel") == "print" and one.get("owner") == "website", one)
check("... nur Zahlen: keine Adresse, keine Marke", "203.0.113" not in b.decode() and "visitor\"" not in b.decode())
s, h, b = call("GET", "/api/v1/stats?day=gestern", PLATFORM)
check("... day falsch: 422", s == 422, s)
s, h, b = call("GET", "/api/v1/stats?area=r", PLATFORM)
check("... die Plattform fragt immer ihre Area, nie eine fremde", s == 200 and j(b)["area"] == "k")
s, h, b = call("GET", "/api/v1/stats?area=k", USER)
check("... ein Benutzer bekommt die Zahlen der Plattform-Area nicht", s == 403, s)
s, h, b = call("GET", "/api/v1/stats?area=k", ADMIN)
check("... die Verwaltung schon", s == 200 and j(b)["links"], s)

# Änderungen seit
import time as _t
_t.sleep(1.1)  # die Zeitstempel sind sekundengenau
stamp = store.now_iso()
s, h, b = call("PATCH", f"/api/v1/links/{PL['id']}", PLATFORM, body={"disabled": True})
check("PATCH pausiert", s == 200 and j(b)["link"]["disabled"] is True, b[:200])
s, h, b = call("GET", f"/api/v1/links?updated_since={stamp}", PLATFORM)
ids = [x["id"] for x in j(b).get("links", [])]
check("links?updated_since= liefert nur den geänderten Link der Plattform-Area", s == 200 and ids == [PL["id"]], ids)
s, h, b = call("PATCH", f"/api/v1/links/{PL['id']}", PLATFORM, body={"disabled": False, "target_url": "https://example.org/neu"})
check("PATCH: fortsetzen und neues Ziel", s == 200 and j(b)["link"]["target_url"] == "https://example.org/neu"
      and j(b)["link"]["owner"] == "website", b[:200])
s, h, b = call("GET", "/admin/areas/k", ADMIN)
check("Admin-Seite der Plattform-Area zeigt die Stufe ohne Adresse", s == 200 and "keine Adresse".encode() in b, s)


print("\nDie Tür einer App: das Gateway nennt den Besitzer (0.8)")
VIA_A = dict(PLATFORM, **{"X-OAAP-Source-Instance": "shop"})
VIA_B = dict(PLATFORM, **{"X-OAAP-Source-Instance": "verein-website"})
s, h, b = call("POST", "/api/v1/links", VIA_A, body={"target_url": "https://example.org/shop", "owner": "website",
                                                     "channel": "social", "campaign": "flyer-winter"})
LA = (j(b) or {}).get("link") or {}
check("App über die Tür: ohne area in die Plattform-Area, Besitzer vom Gateway (nicht aus dem Feld)",
      s == 201 and LA.get("area") == "k" and LA.get("owner") == "shop", b[:300])
s, h, b = call("GET", "/api/v1/links", VIA_A)
check("... sie sieht nur ihre Links", s == 200 and [x["id"] for x in j(b)["links"]] == [LA["id"]], b[:300])
s, h, b = call("GET", f"/api/v1/links/{PL['id']}", VIA_A)
check("... einen Link einer anderen Instanz nicht (404)", s == 404, s)
s, h, b = call("PATCH", f"/api/v1/links/{PL['id']}", VIA_A, body={"target_url": "https://evil.example/"})
check("... und kann ihn nicht umleiten", s == 404 and store.get_link(app.DB, PL["id"])["target_url"] == "https://example.org/neu", s)
s, h, b = call("DELETE", f"/api/v1/links/{LA['id']}", VIA_B)
check("... eine dritte Instanz löscht ihn nicht", s == 404 and store.get_link(app.DB, LA["id"])["deleted_at"] is None, s)
s, h, b = call("PATCH", f"/api/v1/links/{LA['id']}", VIA_A, body={"target_url": "https://example.org/shop2"})
check("... die eigene ändert ihr Ziel, der Besitzer bleibt", s == 200 and j(b)["link"]["owner"] == "shop"
      and j(b)["link"]["target_url"] == "https://example.org/shop2", b[:200])
call("GET", f"/k/{LA['key']}", None, headers={"User-Agent": "Mozilla/5.0 (Android 14) Chrome/120 Mobile"})
s, h, b = call("GET", "/api/v1/stats", VIA_A)
check("stats über die Tür: nur die eigenen Links", s == 200 and [x["id"] for x in j(b)["links"]] == [LA["id"]], b[:300])
s, h, b = call("GET", f"/api/v1/links/{LA['id']}/qr?format=svg", VIA_A)
check("QR-Code über die Tür", s == 200 and b"<svg" in b[:400], b[:100])
check("... und gleich beim Anlegen (qr_svg)", "<svg" in (LA.get("qr_svg") or "")[:400])
spoof = dict(USER, **{"X-OAAP-Source-Instance": "shop"})
s, h, b = call("GET", f"/api/v1/links/{LA['id']}", spoof)
check("Eine Person mit gefälschter Quell-Kopfzeile sieht den Link nicht", s == 404, s)

server.shutdown()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{ok} bestanden, {fail} fehlgeschlagen")
sys.exit(1 if fail else 0)
