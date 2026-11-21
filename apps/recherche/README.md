# Recherche

Websuche und Webseiten als **MCP-Quelle** für Assistenten (RFC-0116 Stufe 1).
Nur Standardbibliothek, keine eigene KI, kein Speicher außer einem
Zwischenspeicher im Arbeitsspeicher (eine Stunde, nach dem Hash der Adresse).

| Werkzeug | Was | Wirkung |
|---|---|---|
| `web_search` | `query` (≤ 300 Zeichen), `count` 1–10, `language`, `site` → Treffer `{title, url, snippet, source}` | lesend |
| `web_fetch` | `url` (http/https) → die Seite als Text, höchstens 12.000 Zeichen, `{url, final_url, title, fetched_at}` | lesend |

## Einrichten

1. Einen Suchdienst bereitstellen: eine SearXNG-Instanz mit `search.formats:
   [html, json]` und `server.limiter: false` (nur die App fragt sie). Eine je
   Knoten oder Mandant -- ein gemeinsames SearXNG für viele Mandanten wird von
   den öffentlichen Suchmaschinen gebremst (RFC-0116 §8.3).
2. App installieren, dann `oaap destination bind <instanz> <destination> --as search`.
3. Im MCP-Gateway: Quellen einlesen, Sicht `recherche` mit beiden Werkzeugen,
   ein Zugangsprofil des Chats für diese Sicht mit Ampel **rot**, ein Assistent
   mit `view: recherche`.

Schritte 2 und 3 in einem Lauf: das Rezept
[`recherche`](https://github.com/MDJoerg/oaap-docs/blob/main/szenarien/rezepte/recherche.json)
(Portal → Mandant → Rezepte).

## Was die App verweigert (§2.3)

- andere Schemata als http/https, andere Ports als 80/443, Zugangsdaten in der Adresse;
- Adressen im eigenen Netz (Loopback, privat, Link-local, reserviert) -- geprüft
  nach der Auflösung, und die Verbindung geht an genau die geprüfte Adresse;
- die Adressen des eigenen Knotens (aus `OAAP_INSTANCE_NAMES` aufgelöst);
- jede Umleitung wird gleich geprüft, höchstens fünf;
- mehr als 2 MB, andere Inhalte als Text, HTML, JSON (PDF später);
- Domains auf `RECHERCHE_DENY`; mit `RECHERCHE_ALLOW` alle anderen.

Jede Ablehnung ist ein Satz im Ergebnis.

## Ampel und geschlossene Mandanten

Die Quelle startet im MCP-Gateway **rot** und wird nie grün: Suchwörter gehen
an einen externen Dienst, Adressen an die gelesene Seite. Ein Gespräch mit dem
Assistenten muss im Chat bewusst auf rot gestellt werden.

In einem geschlossenen Mandanten (RFC-0068) sagt die Plattform der Instanz
`OAAP_EGRESS=closed`; dann fehlt `web_fetch` im Katalog, und `web_search` geht
nur über eine dort erlaubte Destination.

## Protokoll

Kein Suchbegriff, keine Adresse, kein Inhalt. Gezählt wird je Tag und
Werkzeug, Ablehnungen nach Grund -- auf der Seite der App und unter
`/api/status`, seit dem letzten Start.

## Tests

```
python3 test_recherche.py
```
