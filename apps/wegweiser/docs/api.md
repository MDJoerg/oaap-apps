# REST-API

Alles, was die Oberfläche kann, geht auch per Programm – mit denselben Rechten.

- Basis: `https://<adresse>/api/v1`, JSON.
- Anmeldung: die Sitzung im Browser oder ein API-Schlüssel der Plattform
  (`Authorization: Bearer oaapk_…`, im Portal unter „Zugänge“).
- Fehler: `{"error": {"message": "…", "code": "…"}}`.

| Methode und Pfad | Wer | Was |
| --- | --- | --- |
| `GET /me` | alle | Identität und nutzbare Areas |
| `GET /areas` | alle | Areas |
| `POST /areas` | admin | Area anlegen |
| `PATCH /areas/{slug}` | admin | Area ändern (Typ nicht) |
| `DELETE /areas/{slug}` | admin | Area mit allem löschen |
| `GET /areas/{slug}/stats`, `/accesses` | Verantwortliche | Auswertung, Zugriffe (`format=csv`) |
| `GET /links?area=` | alle | eigene Links; Verantwortliche mit `area=` alle der Area |
| `POST /links` | Nutzer der Area | Link anlegen |
| `GET`/`PATCH`/`DELETE /links/{id}` | Besitzer, Verantwortliche | lesen, ändern (nur die gesendeten Felder, seit 0.8.1 auch Titel und Notiz), löschen |
| `PUT`/`GET`/`DELETE /links/{id}/file` | Besitzer, Verantwortliche | Datei anhängen, holen, entfernen |
| `GET /links/{id}/qr?format=png\|svg` | Besitzer, Verantwortliche | QR-Code |
| `GET /links/{id}/stats`, `/accesses` | Besitzer, Verantwortliche | Auswertung, Zugriffe |
| `GET`/`PUT /hosts` | alle / admin | öffentliche Adressen |
| `GET /campaigns` | alle | alle Kampagnen als Baum (`path`, `depth`, `children`) mit Summen über den Teilbaum (`totals`); `archived=0` blendet archivierte aus |
| `POST /campaigns` | alle | Kampagne anlegen: `title`, `key?`, `parent?` (Id oder Kurzname), `starts_on?`, `ends_on?`, `description?` |
| `GET /campaigns/{id\|key}` | alle | Kampagne mit Summen und den eigenen Links |
| `PATCH`/`DELETE /campaigns/{id\|key}` | Ersteller, admin | ändern, archivieren (`archived`), löschen (nur leer) |
| `GET /campaigns/{id\|key}/stats?days=` | alle | Aufrufe je Tag, je Kanal (`by_channel`), je Unterkampagne (`by_child`), je eigenem Link |

Links tragen `campaign` (Id oder Kurzname; leer = keine) und `channel`
(`web`, `social`, `print`, `mail`, `chat`, `other` oder leer); die Antwort
nennt die Kampagne mit Pfad.

## Beispiel

```sh
curl -X POST https://go.example.org/api/v1/links \
  -H "Authorization: Bearer oaapk_…" -H "Content-Type: application/json" \
  -d '{"area":"r","target_url":"https://example.org/lang/und/breit","title":"Einladung"}'
```

Zeiten als ISO mit `Z` (`2030-06-01T12:00:00Z`) oder ohne Zone in der Zeitzone
der Instanz (Vorgabe Europe/Berlin).

## Kurzlinkdienst des Mandanten (ab 0.8)

Benennt die Mandantenverwaltung diese Instanz als Kurzlinkdienst
(`oaap tenant shortlinks <mandant> <instanz>`), legen andere Apps des Mandanten
Links über die Plattform an – ohne eigenen Schlüssel beim Wegweiser. Dafür gibt
es den Vertrag `oaap.work.shortlinks`:

| Ruf | Wer | Was |
|---|---|---|
| `GET /about` | alle | Vertrag, Version, Adressen; der erste Ruf der Plattform legt ihre Area an (`k`, sonst `oaap-k`) |
| `GET /stats?day=JJJJ-MM-TT` | wer die Area verantwortet (`area=`), die Plattform ihre | je Link eines Tages: Aufrufe, geschätzte Besucher, Herkunft (nur Host), Geräteklasse, Kampagne, Kanal, Besitzer |
| `GET /links?updated_since=` | wie `GET /links` | nur seitdem geänderte Links |

Die Area der Plattform ist für niemanden sonst offen und speichert auf der
Stufe **anonymous**: Herkunft, Gerät und Browser, aber keine Adresse, keinen
User-Agent und keine Sprache. Verschiedene Besucher je Tag werden über einen
Tages-Salz geschätzt, der am nächsten Tag ersetzt wird. Ruft eine App über die
Plattform, nennt das Gateway die App als **Besitzer** des Links; sie sieht und
ändert nur ihre eigenen Links.

## MCP-Tür

Für Assistenten gibt es unter `/mcp` vier Lesewerkzeuge: `wegweiser_search`,
`wegweiser_link_read`, `wegweiser_areas`, `wegweiser_campaigns` – mit denselben
Rechten wie die Seite.
