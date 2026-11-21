# Recherche einrichten

Für Administratoren. Recherche braucht einen Suchdienst und eine Freigabe im MCP-Gateway.

## 1. Suchdienst bereitstellen

Recherche fragt eine SearXNG-Instanz. Sie muss JSON liefern (`search.formats: [html, json]`)
und darf keine Bremse haben (`server.limiter: false`), weil nur die App sie fragt. Am besten
eine je Server oder Mandant – ein gemeinsamer Suchdienst für viele Mandanten wird von den
öffentlichen Suchmaschinen gebremst.

## 2. App installieren und Suchdienst zuordnen

Nach der Installation die Destination zuordnen:

```
oaap destination bind <instanz> <destination> --as search
```

Die Seite der App zeigt danach unter *Einstellungen* „Suchdienst: zugeordnet“.

## 3. Im MCP-Gateway freigeben

1. Quellen einlesen – Recherche erscheint mit `web_search` und `web_fetch`.
2. Eine Sicht `recherche` mit beiden Werkzeugen anlegen.
3. Ein Zugangsprofil des Chats für diese Sicht mit Ampel **rot**.
4. Einen Assistenten mit dieser Sicht anlegen.

Schritte 2 bis 4 erledigt auch das Rezept *recherche* (Portal → Mandant → Rezepte).

## Einstellungen

| Einstellung | Bedeutung |
| --- | --- |
| `RECHERCHE_FETCH` | `on`/`off` – Webseiten lesen; aus: nur die Suche |
| `RECHERCHE_ALLOW` | nur diese Domains liest `web_fetch` (Komma-Liste, leer = alle öffentlichen) |
| `RECHERCHE_DENY` | diese Domains liest `web_fetch` nie |

In einem **geschlossenen Mandanten** gibt es `web_fetch` nicht, und die Suche geht nur über
eine dort erlaubte Destination.
