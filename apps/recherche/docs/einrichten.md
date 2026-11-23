# Recherche einrichten

Für Administratoren. Recherche bringt ihren Suchdienst mit; zu tun bleibt die Freigabe im
MCP-Gateway.

## 1. Suchdienst

Seit Version 0.3.0 läuft in jeder Recherche-Instanz ein eigener Suchdienst (SearXNG) mit.
Er hat keinen Port nach außen und keine Route; nur die App selbst fragt ihn. Einzurichten
ist nichts.

Einen **anderen** Suchdienst nehmen Sie, indem Sie ihn als Destination zuordnen. Die
Destination gilt dann vor dem eigenen:

```
oaap destination bind <instanz> <destination> --as search
```

Der andere Suchdienst muss JSON liefern (SearXNG: `search.formats: [html, json]`). In einem
**geschlossenen Mandanten** ist die Destination der einzige Weg: der eigene Suchdienst käme
dort nicht ins Internet und wird darum nie gefragt.

Die Seite der App zeigt unter *Einstellungen*, welcher Suchdienst gilt.

## 2. App installieren

Die App installiert das Rezept *recherche* bei Bedarf mit, oder der Betreiber installiert sie
aus dem Katalog.

## 3. Im MCP-Gateway freigeben

1. Quellen einlesen – Recherche erscheint mit `web_search` und `web_fetch`.
2. Eine Sicht `recherche` mit beiden Werkzeugen anlegen.
3. Ein Zugangsprofil des Chats für diese Sicht mit Ampel **rot**.
4. Einen Assistenten mit dieser Sicht anlegen.

Schritte 2 bis 4 erledigt auch das Rezept *recherche* (Portal → Mandant → Rezepte).

## Einstellungen

| Einstellung | Bedeutung |
| --- | --- |
| `RECHERCHE_SEARCH_URL` | Adresse des eigenen Suchdienstes (Vorgabe `http://search:8080`); `off` = nur über die Destination |
| `RECHERCHE_FETCH` | `on`/`off` – Webseiten lesen; aus: nur die Suche |
| `RECHERCHE_ALLOW` | nur diese Domains liest `web_fetch` (Komma-Liste, leer = alle öffentlichen) |
| `RECHERCHE_DENY` | diese Domains liest `web_fetch` nie |

In einem **geschlossenen Mandanten** gibt es `web_fetch` nicht, und die Suche geht nur über
eine dort erlaubte Destination.
