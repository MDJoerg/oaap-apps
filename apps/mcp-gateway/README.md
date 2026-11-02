# OAAP MCP-Gateway

MCP-Server hinter **einem Endpunkt je Sicht**. Ein KI-Client — Claude
Code, Copilot, ein eigener Agent — spricht mit `/mcp/<sicht>` wie mit
einem einzigen MCP-Server und erfährt nie, welcher Server dahintersteht
oder wo.

Umsetzung der Capability [`oaap.ai.mcp-gateway`
0.1](../../../oaap-spec/spec/oaap.ai.mcp-gateway.md) = RFC-0065 Stufe 1:
ein Server je Sicht, technischer Zugang (Verfahren T).

## Was diese App tut — und was ausdrücklich nicht

| tut | tut nicht |
| --- | --- |
| Tools einzeln freigeben, je Sicht | etwas zeigen, das niemand freigegeben hat |
| den freigegebenen Text festhalten, Änderungen sperren | einen geänderten Text still übernehmen |
| Argumente gegen das freigegebene Schema prüfen | Argumente oder Ergebnisse speichern — **niemals** |
| Server schützen: Gleichzeitigkeit, Raten, Zeit, Größe, Pause | eine zu große Antwort kürzen |
| je Tool-Aufruf zählen, mit Kontext-Kopie | Preise kennen — Volumen, nie Geld |
| nur die eingetragene Adresse anfahren | einer Weiterleitung folgen, Loopback oder Link-Local anfahren |

## Zwei Wege, zwei Rollen

- **`/mcp/...` ist `public`.** Die Plattform authentifiziert hier
  nichts, entfernt gefälschte Identitäts-Kopfzeilen und bremst je
  Client-Adresse (RFC-0010). Geprüft wird im Dienst: **Der
  API-Schlüssel ist die Identität**, und er nennt die Sichten, die er
  benutzen darf. Ohne genannte Sicht erreicht er nichts.
- **`/` ist `admin`.** Server, Katalog, Sichten, Schlüssel, Verbrauch,
  Auswertung und `/report/v1/…` für ein Abrechnungssystem.

## In fünf Schritten

1. **Tresorschlüssel:** Portal → Instanz → Konfiguration →
   `MCPGW_MASTER_KEY` erzeugen. Nur nötig für Server mit eigenen
   Zugangsdaten.
2. **Server eintragen:** Adresse als URL (`https://…/mcp`) oder als
   `destination:<name>` — dann setzt die Plattform die Zugangsdaten
   der Destination ein und das Gateway hält kein Geheimnis. Zugang
   `none`, `bearer`, `basic` oder `header:<Name>`, dazu eine Ampel.
3. **Katalog lesen:** Knopf auf der Seite des Servers. Danach alle
   15 Minuten von selbst (`MCPGW_CATALOG_MINUTES`).
4. **Sicht anlegen und Tools hineinlegen:** je Tool eine
   **Wirkungsklasse** — lesen, schreiben, ausführen. Was der Server
   über sein Tool sagt (`readOnlyHint`, `destructiveHint`), steht als
   Vorschlag da und wird nie durchgesetzt.
5. **Schlüssel ausstellen:** Sichten, höchste Klasse, Ampel, Budget,
   Rate, Kontext für die Abrechnung. Der Wert erscheint genau einmal,
   zusammen mit dem Befehl für Claude Code:

```bash
claude mcp add --transport http <sicht> https://<gateway>/mcp/<sicht> \
  --header "Authorization: Bearer mcp-oaap-…"
```

## Der festgehaltene Text

Eine Tool-Beschreibung geht unmittelbar in das Modell des Clients. Ein
Server, der sie nach der Freigabe ändert, kann dem Modell Anweisungen
geben („rug pull“). Deshalb:

- Clients sehen **nur den Text, der freigegeben wurde** — nie den, den
  der Server gerade meldet.
- Meldet der Server beim nächsten Lesen etwas anderes (Name, Titel,
  Beschreibung, Schemas, Hinweise — ein Fingerabdruck über alles), ist
  das Tool in jeder Sicht **gesperrt**: nicht gelistet, nicht
  aufrufbar. Die Seite der Sicht zeigt alten und neuen Text
  nebeneinander; erst die Bestätigung gibt den neuen frei.

## Schutz der Server

Je Server, in dieser Reihenfolge: Sicherungsautomat (nach *K* Fehlern
auf dem Weg eine Pause, dann ein Probeaufruf), Rate je Server und je
Server und Schlüssel, höchstens *N* gleichzeitige Aufrufe mit kurzer
Warteschlange, Zeitlimit, **Antwortgröße**. Eine zu große Antwort wird
nicht gekürzt, sondern durch einen Fehler ersetzt — ein gekürztes
Ergebnis sähe für das Modell vollständig aus.

Der Grund steht in RFC-0065 §8: Ein Agent, der in einer Schleife
zweihundertmal `read_class` ruft, ist realistisch, und einem
SAP-System, dem die Dialog-Workprozesse ausgehen, geht es dann für
alle Menschen aus.

## Kommandozeile (kopflose Knoten)

```bash
docker exec -i <container> python3 /srv/app.py server set papier \
  --address https://mcp.example/mcp --auth bearer --credential-stdin ja --light yellow
docker exec <container> python3 /srv/app.py server read papier
docker exec <container> python3 /srv/app.py view set sap-lesen --server papier --max-effect read
docker exec <container> python3 /srv/app.py view add sap-lesen read_class --effect read
docker exec <container> python3 /srv/app.py view confirm sap-lesen read_class
docker exec <container> python3 /srv/app.py key issue agent-1 --views sap-lesen --cost-center KS1
```

## Prüfen

```bash
python3 apps/mcp-gateway/test_argcheck.py   # Argumentprüfung (A1)
python3 apps/mcp-gateway/test_gateway.py    # Konformitätsliste der Spec §12, Ende zu Ende
```

Die zweite Datei stellt einen **Papier-MCP-Server** hin, der sich auf
Ansage danebenbenimmt (Weiterleitung, Ausfall, verlorene Sitzung,
geänderte Beschreibung, Übergröße, Rückfrage, fremde
Protokollversion). Er lauscht auf der LAN-Adresse des Rechners, weil
das Gateway Loopback nie anfährt.

Die Bücher (Schlüssel, Kontext, Prüfspur, Tresor, die eine Abfrage,
Grafiken) stehen in `gwcore/` — eine Kopie von
[`oaap-apps/lib/gwcore`](../../lib/gwcore/), gemeinsam mit dem
KI-Gateway. Geändert wird nur dort, dann `check-shared.py --fix`.

## Was noch fehlt

- **Stufe 2:** Bündel über mehrere Server, Umbenennen, feste Argumente
  und Grenzen, Resources und Prompts, Rückfragen vom Server an den
  Client je Sicht einstellbar (in 0.1 lehnt das Gateway sie ab).
- **Stufe 3:** Anmeldung eines Menschen über das Keycloak des
  Mandanten, Zugangsprofile, Token-Tausch (Verfahren X).
- **Stufe 4:** der Benutzer bis ins SAP-System (Verfahren P,
  RFC-0066).
- `pattern` und `format` in Schemas werden nicht durchgesetzt; die
  Seite des Servers nennt sie je Tool.
- Kein vom Server ausgehender Strom über `GET` (0.1 antwortet 405).
