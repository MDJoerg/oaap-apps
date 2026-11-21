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
| `GET`/`PATCH`/`DELETE /links/{id}` | Besitzer, Verantwortliche | lesen, ändern, löschen |
| `PUT`/`GET`/`DELETE /links/{id}/file` | Besitzer, Verantwortliche | Datei anhängen, holen, entfernen |
| `GET /links/{id}/qr?format=png\|svg` | Besitzer, Verantwortliche | QR-Code |
| `GET /links/{id}/stats`, `/accesses` | Besitzer, Verantwortliche | Auswertung, Zugriffe |
| `GET`/`PUT /hosts` | alle / admin | öffentliche Adressen |

## Beispiel

```sh
curl -X POST https://go.example.org/api/v1/links \
  -H "Authorization: Bearer oaapk_…" -H "Content-Type: application/json" \
  -d '{"area":"r","target_url":"https://example.org/lang/und/breit","title":"Einladung"}'
```

Zeiten als ISO mit `Z` (`2030-06-01T12:00:00Z`) oder ohne Zone in der Zeitzone
der Instanz (Vorgabe Europe/Berlin).

## MCP-Tür

Für Assistenten gibt es unter `/mcp` drei Lesewerkzeuge: `wegweiser_search`,
`wegweiser_link_read`, `wegweiser_areas` – mit denselben Rechten wie die Seite.
