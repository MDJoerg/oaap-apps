# Marketplace (`marketplace`) — RFC-0077 Stufen 1 und 2

Digitale Assets mit **Releases** (unveränderlich, nach SHA-256 abgelegt) und
**Kanälen** (`stable`, `beta`, global, D12). OAAP-Pakete sind ein Asset-Typ
(`oaap-app`), daneben gibt es `file` (PDF, Bilder, Texte, ZIP).

**Sehen ist nicht Bekommen.** Für eine Person oder Maschine ist ein Asset
*verborgen* (404), *sichtbar, gesperrt* oder *beziehbar*. Beziehbar ist es,
wenn sein Kanal frei ist oder eine **Berechtigung** (Benutzer oder Gruppe ×
Asset × Kanäle) besteht. Ein neues Asset startet *sichtbar, gesperrt* (D11).
Die Regel steht an genau einer Stelle: `access.py`.

## Wege

| Pfad | Route | Für |
|---|---|---|
| `/`, `/assets/<id>`, `/d/<sha256>`, `/api/public/v1/assets` | öffentlich | was `anyone` sehen darf |
| `/my/…` | angemeldet | dieselben Seiten mit Identität |
| `/api/v1/…` | angemeldet / API-Schlüssel | Suche, Details, Einchecken, Kanäle, Rückruf |
| `/feed/v1/oaap-apps?channel=stable`, `/feed/v1/files/<sha256>` | angemeldet / API-Schlüssel | Empfänger, z. B. ein Paketkatalog im Spiegelmodus |
| `/admin` | admin | Sichtbarkeit, freie Kanäle, Berechtigungen, Rechte zum Veröffentlichen, Empfänger |

## Schaufenster (Stufe 2)

- **Übersicht** (`/` öffentlich, `/my/` angemeldet): Suche über Name,
  Beschreibung und Schlagworte (Volltext, ohne Umlaute, als Präfix),
  Facetten Typ, Kategorie, Schlagwort, Kanal, Karten mit Icon, Version,
  Herausgeber und Zustand (*frei*, *berechtigt*, *freischalten*, *beta*).
  Gezählt wird erst, was die Person sehen darf.
- **Detailseite:** Icon, Laden je Kanal, Screenshots, Beschreibung in
  Markdown (rohes HTML erscheint als Text), Fakten (Versionen, Größe,
  Herausgeber, Lizenz, Links, Schlagworte), Versionsverlauf mit Notizen.
- **Freischalt-Anfrage:** Angemeldete fragen auf der Detailseite an (eine
  offene je Person und Asset). Der Betreiber schaltet unter `/admin` frei
  (das erzeugt die Berechtigung) oder lehnt mit einer Antwort ab, die die
  Person auf der Detailseite sieht.
- **Bearbeiten** (`/admin/assets/<id>`): Titel, Kurzbeschreibung,
  Beschreibung, Kategorien, Schlagworte, Herausgeber, Lizenz, Links, Bilder.
  Bilder nur PNG/JPEG/WebP (am Inhalt erkannt), höchstens 5 MB, bis zu
  8 Screenshots, kein SVG. Ein Bild ist so sichtbar wie sein Asset.
  Veröffentlicher können per API: `POST /api/v1/assets/<id>/images`
  (`file`, `role=icon|screenshot`), `DELETE /api/v1/assets/<id>/images/<sha256>`,
  `PUT /api/v1/assets/<id>` mit `tags`, `categories`, `links`, `license`,
  `publisher`.

## Einchecken (eine KI oder ein Skript)

1. Im Portal einen **Maschinenbenutzer** anlegen (z. B. `ci`) und unter
   **Zugänge** einen API-Schlüssel ausstellen (D10).
2. Im Marketplace unter `/admin` „Veröffentlichen dürfen“: Muster (`hbvp`,
   `oaap-*`), Benutzer `ci`, optional Kanäle.
3. Einchecken:

```sh
python3 tools/publish.py --url https://market.example --key-file ~/.oaap/ci.key \
    hbvp-0.0.222.zip --channels beta
```

Dieselbe Version mit denselben Bytes: `200`, nichts Neues. Mit anderen Bytes:
`409`. Eine Version wird nie wiederverwendet, auch nach einem Rückruf nicht.

Weitere Aufrufe (alle mit `Authorization: Bearer <schlüssel>`):

```sh
POST   /api/v1/assets/<id>/releases/<v>/channels/stable     # in stable legen
DELETE /api/v1/assets/<id>/releases/<v>/channels/beta       # aus beta nehmen
POST   /api/v1/assets/<id>/releases/<v>/yank  {"reason": "…"}  # zurückrufen
PUT    /api/v1/assets/<id>  {"title": …, "summary": …, "description": …}
```

Sichtbarkeit und freie Kanäle setzt nur der Betreiber (`/admin` oder `PUT`
als admin).

## Einen Knoten beliefern

Auf dem Knoten des Empfängers: Maschinenbenutzer im Marketplace (z. B.
`node-oaapx02`) mit API-Schlüssel, Berechtigung für die Assets und Kanäle.
Im Paketkatalog des Knotens die Destination `upstream` binden (Ziel = der
Marketplace, Authentifizierung `bearer` mit diesem Schlüssel). Der Katalog
folgt dem Feed (siehe `apps/package-catalog/README.md`).

## Konfiguration

| Schlüssel | Vorgabe | Bedeutung |
|---|---|---|
| `MARKET_NAME` | `Marketplace` | Name im Kopf und im Feed |
| `MARKET_MAX_FILE_MB` | `256` | größte Datei |
| `MARKET_CHANNELS` | `stable,beta` | Kanäle |
| `MARKET_TYPES` | `oaap-app,file` | eingeschaltete Typen |
| `MARKET_FILE_EXTENSIONS` | `zip,pdf,png,jpg,txt,md` | Endungen für `file` |

Daten: `/data/market.db` (SQLite) und `/data/blobs/` — im Backup des Knotens.

## Noch nicht (spätere Stufen)

S3, MCP, Statistikseiten (Stufe 3); Partner, eigene Asset-Typen, Signaturen
(Stufe 4).

## Tests

```sh
python3 test_marketplace.py
python3 test_storefront.py
```
