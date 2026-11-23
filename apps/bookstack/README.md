# Wiki (BookStack) als OAAP-App

Stufe 1 von **RFC-0110** („A wiki in the tenant“): BookStack hinter dem
Gateway. Menschen melden sich über den Realm des Mandanten an, die Gruppen
des Realms sind die Rollen im Wiki. Bilder und Anhänge sind geschützt wie
ihre Seite.

## Drei Container

| Dienst | Was | Ordner |
|---|---|---|
| `wiki` | BookStack auf dem Abbild von linuxserver.io, Tag `version-v26.09.3`, mit einem Einstiegsschritt davor | `wiki/` |
| `db` | MariaDB 11.4.13 mit einem Einstiegsschritt davor; keine Route | `db/` |
| `oaap` | der Keim des Adapters (RFC-0110 §3): Einrichtungstür, eigenes Konto, Standardkennwort, Admin-Gruppe | `oaap/` |

Kein Code von BookStack wird geändert. OAAP berührt BookStack nur über
seine Umgebungsvariablen, seine REST-API und eine Tabelle (`api_tokens`,
D3). Die Spalten dieser Tabelle prüft `oaap` bei jedem Start (§3.3).

## Was die Einstiegsschritte tun

- **wiki:** `APP_URL` aus dem ersten Namen der Instanz (immer https),
  `APP_KEY` stabil aus `OAAP_WIKI_APP_SECRET` abgeleitet, Datenbank =
  Mitdienst `db`, **`STORAGE_TYPE=local_secure_restricted` fest** (RFC-0110
  F5), OIDC aus `OAAP_OIDC_*`, sobald der Betreiber einen Client gibt. Er
  wartet, bis eine echte Anmeldung an der Datenbank gelingt. Das Abbild
  prüft nur den offenen Port und versucht die Migration nur einmal.
- **db:** startet nicht ohne `MARIADB_PASSWORD`; eine unvollständige
  Ersteinrichtung (Marke `.oaap-initialized` fehlt) wird geleert und
  wiederholt.

## Gemessen am 23.11.2026 auf oaap-test

Instanz `vuebung-vuebung-wiki` im Mandanten `vuebung`, Realm mit anna,
bert und carl. Bericht: `oaap-lab/wiki-probe/stufe1-paket.md`.

## Ablagen-Konnektor (0.2.0, RFC-0121)

Der Dienst `oaap` hat unter `/oaap/mcp` eine MCP-Tür (eigene Route für alle
Rollen, `mcp:` im Manifest): die Verben von `oaap.work.stores` 0.1 als
Werkzeuge `stores_*` (`stores.py`). Die Person kommt von der Plattform
(`X-OAAP-User-Idp`); ihr Konto ist das mit `external_auth_id` = `sub`, das
Token dafür entsteht bei Bedarf und bleibt im Speicher. Buch = Ablage,
Kapitel = Ordner, Seite = Dokument. Die Rollen der Einrichtungstür bekommen
„Zugriff auf System-API“ (`access-api`) dazu -- ohne es lehnt BookStack jeden
Aufruf als die Person ab. Gemessen über die Ablagetür des MCP-Gateways:
`oaap-lab/arbeitsraum-messung`, 27/27.

## Prüfen

```sh
cd oaap && python3 -m unittest test_setup test_stores
```

## Hilfe

`docs/`, deutsch, für Menschen im Wiki und für die Verwaltung (RFC-0118 §9).
