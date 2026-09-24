# Wegweiser — Kurzlinks, Zählpixel, Einmal-Übergaben

Eine Adresse der Form `https://go.example.org/<area>/<key>` wird
öffentlich aufgelöst. Die **Area** sagt, was passiert, und trägt
Voreinstellungen und Verantwortung; der **Key** ist alphanumerisch,
höchstens 8 Zeichen, Groß-/Kleinschreibung egal.

| Typ der Area | Beim Aufruf |
|---|---|
| `redirect` | 302 auf die Ziel-URL (mit PIN: Zwischenseite, dann 302) |
| `pixel` | 1×1-GIF, `no-store`; zählt „geladen", nicht „geöffnet" |
| `download_once` | Zwischenseite; erst der Knopf lädt, danach ist der Key tot |
| `download_window` | wie oben, von/bis und optional Höchstzahl an Abrufen |
| `upload_once` | Formular; genau eine Datei per PUT, danach ist der Key tot |

Einmal-Links entwertet **nie der erste GET**: Mailprogramme und
Messenger rufen Links beim Empfang für Vorschauen auf. Erst der POST
von der Zwischenseite lädt herunter, erst der PUT lädt hoch. Zwei
gleichzeitige Klicks liefern einen Einmal-Download genau einmal
(atomarer Zähler in SQLite).

## Rollen

Die Plattform kennt die Rollen fest, die App erfindet keine eigenen.

- **admin** legt Areas an, setzt Eigenschaften und trägt je Area
  Verantwortliche ein (Benutzernamen; sie brauchen die Rolle `keyuser`).
- **keyuser** verwaltet die Areas, für die er eingetragen ist: alle
  Links darin sehen, ändern, löschen, Wunsch-Keys vergeben, Statistik.
  In anderen Areas ist er ein normaler Benutzer.
- **user** legt Links in offenen Areas an und sieht nur eigene.

Links gehören an die Benutzer-UUID (`X-OAAP-User-Id`, RFC-0040).
Verantwortliche werden per Benutzername benannt, weil ein Admin keine
UUIDs kennt und die Plattform Apps kein Benutzerverzeichnis bietet.

## Routen im Manifest

| Pfad | Rolle | Inhalt |
|---|---|---|
| `/admin` | admin | Areas |
| `/manage` | user | eigene Links, Link-Seite mit Statistik, Areas (Keyuser) |
| `/api` | user | REST-API, gleiche Rechte wie die Oberfläche |
| `/` | public | `/<area>/<key>`, Zwischenseiten, sonst 404 |

Auf `/` setzt das Gateway keine Identitäts-Kopfzeilen und bremst je
Client-Adresse (RFC-0010). PIN-Sperre, Fristen und Zähler führt die App:
fünf falsche PINs sperren den Link 15 Minuten, jede weitere Sperre
verdoppelt sich. Gezählt wird je Link, nicht je Adresse.

Reservierte Area-Slugs: `admin`, `api`, `auth`, `healthz`, `manage`,
`platform`, `static`.

## Statistik und Speichertiefe

Die Speichertiefe ist eine Eigenschaft der Area (`log_level`):

| Stufe | je Zugriff gespeichert |
|---|---|
| `count` | nur der Zähler am Link |
| `minimal` | Zeitpunkt, Ergebnis |
| `standard` | dazu IP gekürzt (v4 /24, v6 /48), Referer-Host, Geräteklasse, Browser- und OS-Familie, erste Sprache |
| `full` | dazu volle IP, voller User-Agent, voller Referer, volle `Accept-Language` |

`log_retention_days` je Area (Vorgabe 90). Ein stündlicher Lauf in der
App löscht ältere Zeilen und die Bytes verfallener oder verbrauchter
Dateien; der Link-Eintrag bleibt mit Namen und Zahlen.

Ergebnisse: `redirected`, `pixel`, `landing`, `downloaded`, `uploaded`,
`pin_wrong`, `pin_locked`, `expired`, `exhausted`, `disabled`,
`not_found`.

## REST-API

Basis `/api/v1`, JSON. Anmeldung wie jede angemeldete Route: Sitzung im
Browser oder ein API-Schlüssel der Plattform
(`Authorization: Bearer oaapk_…`, RFC-0027). Fehler kommen als
`{"error": {"message": "…", "code": "…"}}` mit 401/403/404/405/409/
410/411/413/415/422.

| Methode und Pfad | Wer | Was |
|---|---|---|
| `GET /me` | alle | Identität, Rollen, nutzbare Areas |
| `GET /areas` | alle | Areas (Admin: alle mit allen Feldern; sonst nutzbare, ohne Verwaltungsfelder) |
| `POST /areas` | admin | Area anlegen: `slug`, `type`, dazu optional alle Felder unten |
| `GET /areas/{slug}` | Nutzer der Area | eine Area |
| `PATCH /areas/{slug}` | admin | Felder ändern (Typ nicht) |
| `DELETE /areas/{slug}` | admin | Area mit allen Links, Dateien, Zugriffen |
| `GET /areas/{slug}/stats?days=30` | Verantwortliche | Auswertung der Area |
| `GET /areas/{slug}/accesses?limit&offset&format=csv` | Verantwortliche | Zugriffszeilen |
| `GET /links?area=&all=1` | alle | eigene Links; Verantwortliche mit `area=` alle der Area; Admin mit `all=1` alle |
| `POST /links` | Nutzer der Area | Link anlegen (Felder unten) |
| `GET /links/{id}` | Besitzer, Verantwortliche | ein Link |
| `PATCH /links/{id}` | Besitzer, Verantwortliche | `title`, `note`, `disabled`, `expires_at`, `target_url`, `valid_from`, `valid_until`, `max_downloads`, `pin` (leer = entfernen) |
| `DELETE /links/{id}` | Besitzer, Verantwortliche | Link löschen (Datei geht mit) |
| `PUT /links/{id}/file` | Besitzer, Verantwortliche | Datei an Download-Link hängen: roher Body, Kopfzeile `X-File-Name` (URL-kodiert), `Content-Type` |
| `GET /links/{id}/file` | Besitzer, Verantwortliche | Datei holen (auch die eines eingegangenen Uploads) |
| `DELETE /links/{id}/file` | Besitzer, Verantwortliche | Datei entfernen; ein Upload-Link wartet danach wieder |
| `GET /links/{id}/stats?days=30` | Besitzer, Verantwortliche | Auswertung |
| `GET /links/{id}/accesses?limit&offset&format=csv` | Besitzer, Verantwortliche | Zugriffszeilen |

Area-Felder: `title`, `description`, `responsible` (Liste von
Benutzernamen), `open_for_users`, `active`, `key_length` (4–8),
`custom_keys`, `default_ttl_days`, `max_ttl_days` (gilt für Benutzer,
nicht für Verantwortliche; 0 = unbegrenzt), `pin_required`,
`pin_min_length`, `max_file_mb`, `allowed_extensions` (`pdf,zip`),
`log_level`, `log_retention_days`.

Link-Felder beim Anlegen: `area` (Pflicht), `key` (nur Verantwortliche,
wenn die Area Wunsch-Keys erlaubt), `title`, `note`, `pin`,
`expires_at`, `target_url` (redirect), `valid_from`, `valid_until`,
`max_downloads` (download_window). Zeiten als ISO mit `Z`
(`2030-06-01T12:00:00Z`) oder ohne Zone in der Zeitzone der Instanz
(`WEGWEISER_TZ`, Vorgabe Europe/Berlin). Die Antwort enthält `url`
(gebaut aus dem Host der Anfrage), `has_pin`, `file_present`,
`hit_count`, `download_count`, `state` (Upload: `waiting`/`received`).

Öffentliche Endpunkte je Link, ohne Anmeldung:

- `GET /<area>/<key>` — je nach Typ 302, GIF, Zwischenseite, Formular
- `POST /<area>/<key>` mit Formular `pin=…` — Weiterleitung mit PIN
  oder Download
- `PUT /<area>/<key>` mit rohem Body, `X-File-Name`, optional `X-Pin` —
  der eine Upload; Antwort 201 mit `sha256`
- Beim Zählpixel darf `.gif` oder `.png` angehängt werden

## Beispiel

```sh
# Area anlegen (admin)
curl -X POST https://go.example.org/api/v1/areas \
  -H "Authorization: Bearer oaapk_…" -H "Content-Type: application/json" \
  -d '{"slug":"r","type":"redirect","title":"Kurzlinks","responsible":["kai"]}'

# Link anlegen
curl -X POST https://go.example.org/api/v1/links \
  -H "Authorization: Bearer oaapk_…" -H "Content-Type: application/json" \
  -d '{"area":"r","target_url":"https://example.org/lang/und/breit","title":"Einladung"}'

# Datei an einen Download-Link hängen
curl -X PUT https://go.example.org/api/v1/links/<id>/file \
  -H "Authorization: Bearer oaapk_…" -H "X-File-Name: vertrag.pdf" \
  -H "Content-Type: application/pdf" --data-binary @vertrag.pdf
```

## Konfiguration

| Variable | Vorgabe | Bedeutung |
|---|---|---|
| `WEGWEISER_MAX_UPLOAD_MB` | 512 | Obergrenze je Datei; Areas dürfen weniger, nie mehr |
| `WEGWEISER_TZ` | Europe/Berlin | Zeitzone für Eingaben und Anzeige |
| `OAAP_APP_SECRET` | von der Plattform | Salz der PIN-Hashes |

Daten liegen unter `/data`: `wegweiser.db` (SQLite, WAL) und
`files/<area>/<link-id>/content`.

## Tests

```sh
python3 test_wegweiser.py
```

Startet den Dienst im Prozess und prüft Areas, alle fünf Typen, PIN und
Sperre, gleichzeitige Klicks auf einen Einmal-Download, Speichertiefen,
Formulare und den Aufräumlauf. Keine Fremdbibliothek.

## Nicht in 0.1

QR-Code je Link, E-Mail bei Upload-Eingang, Geo-Auswertung,
Mehrfach-Upload, eigene Domains je Area, Rechte an Links weitergeben.
