# OAAP MCP-Gateway

MCP-Server hinter **einem Endpunkt je Sicht**. Ein KI-Client — Claude
Code, Copilot, ein eigener Agent — spricht mit `/mcp/<sicht>` wie mit
einem einzigen MCP-Server und erfährt nie, welche Server dahinterstehen
oder wo.

Umsetzung der Capability [`oaap.ai.mcp-gateway`
0.5](../../../oaap-spec/spec/oaap.ai.mcp-gateway.md) = RFC-0065 Stufen 1
bis 3 und RFC-0073 Stufen 1 und 2: Sichten bündeln Tools, Resources und
Prompts mehrerer Server; hinein mit einem Schlüssel oder als Mensch,
angemeldet am Anmeldedienst des Mandanten; zum Server technisch
(Verfahren T), als dieser Mensch über einen getauschten Token
(Verfahren X) oder zu einer App der Plattform als dieser Mensch über die
Plattform (Verfahren I).

## Was diese App tut — und was ausdrücklich nicht

| tut | tut nicht |
| --- | --- |
| Tools, Resources, Prompts einzeln freigeben, je Sicht — auch aus mehreren Servern | etwas zeigen, das niemand freigegeben hat |
| Namen der Sicht vergeben, Kollisionen abweisen | einen doppelten Namen still auflösen |
| Argumente fest vorgeben oder begrenzen (Liste, Muster `Z*`) | einen regulären Ausdruck auf fremde Eingabe loslassen |
| den freigegebenen Text festhalten, Änderungen sperren | einen geänderten Text still übernehmen |
| Argumente gegen das freigegebene Schema prüfen | Argumente oder Ergebnisse speichern — **niemals** |
| Server schützen: Gleichzeitigkeit, Raten, Zeit, Größe, Pause | eine zu große Antwort kürzen |
| je Tool-Aufruf, Resource-Lesen, Prompt zählen, mit Kontext-Kopie | Preise kennen — Volumen, nie Geld |
| nur die eingetragene Adresse anfahren | einer Weiterleitung folgen, Loopback oder Link-Local anfahren |
| Tokens eines Menschen selbst prüfen (RS256, Issuer, Audience, Ablauf) | ein Token an einen Server weiterreichen |
| bei Verfahren X ein Token nur für das Backend eintauschen | auf den technischen Zugang ausweichen, wenn der Mensch fehlt |

## Zwei Wege, zwei Rollen

- **`/mcp/...` ist `public`.** Die Plattform authentifiziert hier
  nichts, entfernt gefälschte Identitäts-Kopfzeilen und bremst je
  Client-Adresse (RFC-0010). Geprüft wird im Dienst: ein
  **API-Schlüssel** oder das **Token eines angemeldeten Menschen**. Wer
  nicht darf, bekommt immer dieselbe `401` — ob ohne Schlüssel, mit
  falschem Token oder für eine Sicht, die es nicht gibt.
- **`/.well-known/oauth-protected-resource` ist `public`.** Dort
  erfährt ein MCP-Client, bei welchem Anmeldedienst er sich anmeldet.
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
4. **Sicht anlegen und Einträge hineinlegen:** auf der Seite eines
   Servers, je Eintrag. Eine Sicht darf Einträge mehrerer Server
   enthalten. Ein Tool bekommt eine **Wirkungsklasse** — lesen,
   schreiben, ausführen. Was der Server über sein Tool sagt
   (`readOnlyHint`, `destructiveHint`), steht als Vorschlag da und wird
   nie durchgesetzt. Auf der Seite der Sicht lassen sich danach Name,
   Beschreibung, feste Argumente und Grenzen setzen.
5. **Schlüssel ausstellen:** Sichten, höchste Klasse, Ampel, Budget,
   Rate, Kontext für die Abrechnung. Der Wert erscheint genau einmal,
   zusammen mit dem Befehl für Claude Code:

```bash
claude mcp add --transport http <sicht> https://<gateway>/mcp/<sicht> \
  --header "Authorization: Bearer mcp-oaap-…"
```

## Anmeldung von Menschen (0.3)

Die Plattform zeigt einer App ihren Anmeldedienst bewusst nicht
(RFC-0041). Das MCP-Gateway ist die Ausnahme, und der Betreiber richtet
sie ein (RFC-0065 §15, D14–D16):

1. **Im Realm des Mandanten** (Keycloak), von Hand:
   - ein **vertraulicher Client** des Gateways, z. B. `mcp-gw-<instanz>`,
     ohne Anmeldefluss; für Verfahren X mit eingeschaltetem
     *Standard token exchange*;
   - ein **öffentlicher Client** für Menschen, z. B. `mcp-claude`:
     Anmeldung mit PKCE, Rücksprung nur
     `http://localhost:33418/callback`, ein **Audience-Mapper** auf den
     vertraulichen Client und ein Gruppen-Mapper (`groups`, volle Pfade).
2. **In der Konfiguration der Instanz:** `MCPGW_ISSUER` (genau wie im
   Token, z. B. `https://auth.<knoten>/realms/<mandant>`),
   `MCPGW_AUDIENCE` (der vertrauliche Client), `MCPGW_SIGNIN_CLIENT`
   (der öffentliche).
3. **Sicht auf „Anmeldung“** stellen und ein **Zugangsprofil** anlegen:
   Gruppen (`/sap-dev`, oder `*` für alle), Sichten, Kontext, Rate,
   Budget — wie ein Schlüssel, nur ohne Wert. Das Profil wird je Sicht
   gewählt; die Rate gilt je Mensch, das Budget für alle zusammen.
4. **Claude Code**, einmal je Mensch:

```bash
claude mcp add --transport http <sicht> https://<gateway>/mcp/<sicht> \
  --client-id mcp-claude --callback-port 33418
```

Claude Code öffnet den Browser, der Mensch meldet sich an, und das
Gateway prüft das Token selbst: RS256, mindestens 2048 Bit, Issuer,
Audience, Ablauf. Die Schlüssel des Issuers holt es nur von dessen
eigener Adresse. In der Messzeile steht der Mensch als `sub` mit
Issuer — nie Name oder E-Mail.

**Verfahren X (Token-Tausch):** Ein Server mit Zugang `exchange` wird
als dieser Mensch gerufen. Das Gateway tauscht dessen Token beim
Token-Endpunkt gegen eines nur für das Backend (`audience`) und reicht
das eigene Token nie weiter. Klappt der Tausch nicht, wird abgelehnt —
nie der technische Zugang. Eine Sicht mit so einem Server nimmt nur
Anmeldungen an. Den Katalog eines solchen Servers liest der Betreiber
mit eigenem Token:

```bash
echo "$TOKEN" | docker exec -i <container> python3 /srv/app.py server read <name> --token-stdin ja
```

## Quellen der Plattform (0.4) und Verfahren I (0.5)

Eine App, die im Manifest eine MCP-Tür erklärt (`mcp: {path: /mcp}`),
ist eine **Quelle**. Dieses Gateway erklärt `mcp_sources: {read: true}`
und bekommt von der Plattform die Liste der Quellen **seines Mandanten
auf diesem Knoten** (RFC-0073, Spec §3.6). Abgeglichen wird beim Start,
alle 5 Minuten, 30 s nach einem Fehler und mit `source sync`. Eine neue
Quelle wird ein Server mit ihrem Instanznamen, Ampel rot, **in keiner
Sicht**, bis der Betreiber freigibt. Verschwindet sie, bleiben ihre
Einträge stehen und sind als weg markiert.

**Verfahren I (0.5):** Nennt die Liste eine Tür, wird die Quelle mit
Zugang `platform` eingetragen. Ein angemeldeter Mensch erreicht die App
dann **als er selbst**: Das Gateway tauscht sein Token mit dem eigenen
Client (`MCPGW_AUDIENCE`, Geheimnis `MCPGW_CLIENT_SECRET`) gegen eines
für den Plattform-Client des Realms (`MCPGW_PLATFORM_AUDIENCE`) und ruft
die Tür auf der eigenen Seite mit dem Quellen-Schlüssel und diesem
Act-Token. Die Plattform prüft beides, findet die Person über ihre
Bindung und gibt der App die sechs Kopfzeilen der Person. Die App ändert
nichts. Ohne Menschen kein Aufruf, kein Rückfall auf einen technischen
Zugang. Eine Sicht mit so einem Server nimmt nur Anmeldungen an.

**Agenten (Verfahren T):** Für eine Sicht mit Schlüsseln trägt der
Betreiber dieselbe Instanz ein zweites Mal von Hand ein, mit dem
Schlüssel eines Agenten (`oaap authz agent-add`), beschränkt auf die
Quelle. Eine Quelle, der der Betreiber schon einen Zugang gegeben hat,
behält ihn.

```bash
docker exec <container> python3 /srv/app.py source list
docker exec <container> python3 /srv/app.py source sync
echo "$TOKEN" | docker exec -i <container> python3 /srv/app.py server read <quelle> --token-stdin ja
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

## Bündel: mehrere Server, eine Sicht

Ein Tool oder Prompt trägt in der Sicht einen **eigenen Namen**.
Vorgeschlagen wird der Name beim Server, wenn er in der Sicht frei ist,
sonst `<server>_<name>` — bieten zwei Server je ein `search` an, heißt
das zweite `confluence_search`. Ein Name, den es in der Sicht schon
gibt, wird beim Speichern **abgewiesen**, nie überschrieben oder
durchnummeriert. Resources behalten ihre URI; dieselbe URI aus zwei
Servern ist ebenfalls eine Kollision.

Die Beschreibung lässt sich durch einen eigenen Text ersetzen. Der
festgehaltene Text des Servers bleibt dahinter: Ändert der Server ihn,
wird der Eintrag trotzdem gesperrt.

## Feste Argumente und Grenzen

Beispiel: Ein ABAP-MCP-Server hat `search_objects(package, name)`. In
der Sicht für ein Projektteam:

```bash
docker exec <container> python3 /srv/app.py view tailor team-zdemo search_objects \
  --fixed '{"package": "ZDEMO"}' --bounds '{"name": {"glob": "Z*"}}'
```

- `package` **verschwindet** aus dem Schema, das der Client sieht; das
  Gateway setzt `ZDEMO` ein. Schickt ein Client `package` trotzdem, wird
  abgewiesen — auch mit `ZDEMO`.
- `name` darf nur mit `Z` beginnen. Das Schema des Clients trägt die
  Grenze als `pattern`, damit das Modell sie kennt; geprüft wird mit
  einem eigenen Vergleich für `*` und `?`, **nicht** mit einem
  regulären Ausdruck — der ließe sich mit der passenden Eingabe so
  füttern, dass das Gateway hängt.
- Statt eines Musters geht eine Liste: `{"mode": {"enum": ["kurz", "lang"]}}`.

Beides wird beim Speichern gegen das freigegebene Schema geprüft. Ändert
der Server später sein Schema so, dass ein festes Argument nicht mehr
passt, lässt sich der neue Text erst bestätigen, wenn der Zuschnitt
angepasst ist.

## Rückfragen vom Server an den Client

MCP erlaubt einem Server, während eines Aufrufs zurückzufragen:
**Elicitation** (den Menschen fragen: „Welcher Transportauftrag?“),
**Roots** (die Arbeitsverzeichnisse des Clients) und **Sampling** (das
Modell des Clients für sich arbeiten lassen). Je Sicht einstellbar,
jede für sich: durchreichen oder ablehnen.

- Neue Sichten: Elicitation und Roots durch, **Sampling abgelehnt** —
  damit könnte ein Server auf Kosten des Clients ein Modell benutzen
  und ihm Daten zuspielen.
- Sichten aus 0.1 lehnen weiter alles ab, bis der Betreiber es ändert.
- Durchgereicht wird nur, was die Sicht erlaubt **und** der Client
  kann; dem Server sagt das Gateway auch nur das zu.
- Die Antwort des Menschen geht durch und wird nirgends gespeichert.
- Manche Server zeigen Tools nur Clients, die Rückfragen beantworten
  können (der offizielle Referenzserver: 16 statt 20). Der Katalog wird
  deshalb mit allen drei Fähigkeiten gelesen. Liegt so ein Tool in
  einer Sicht, die die Rückfrage ablehnt, kennt der Server es beim
  Aufruf nicht und sagt das — das Gateway reicht die Antwort durch.

## Schutz der Server

Je Server — in einem Bündel der Server des aufgerufenen Eintrags —,
in dieser Reihenfolge: Sicherungsautomat (nach *K* Fehlern
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
docker exec <container> python3 /srv/app.py view set sap-lesen --max-effect read --sampling refuse
docker exec <container> python3 /srv/app.py view add sap-lesen papier read_class --effect read
docker exec <container> python3 /srv/app.py view add sap-lesen wiki search --as wiki_search
docker exec <container> python3 /srv/app.py view add sap-lesen papier 'sap://class/{name}' --kind template
docker exec <container> python3 /srv/app.py view confirm sap-lesen read_class
docker exec <container> python3 /srv/app.py view list
docker exec <container> python3 /srv/app.py key issue agent-1 --views sap-lesen --cost-center KS1
docker exec <container> python3 /srv/app.py view set sap-ich --ways signin
docker exec <container> python3 /srv/app.py profile create entwickler --groups /sap-dev --views sap-ich --cost-center KS2
echo "$GEHEIMNIS" | docker exec -i <container> python3 /srv/app.py server set a4h-ich \
  --address https://a4h.example/mcp --auth exchange --token-url destination:kc-token \
  --client-id mcp-gw-probe --audience a4h-mcp --credential-stdin ja
```

## Prüfen

```bash
python3 apps/mcp-gateway/test_argcheck.py   # Argumentprüfung (A1)
python3 apps/mcp-gateway/test_compose.py    # Namen, feste Argumente, Grenzen, Vorlagen, Umzug aus 0.1
python3 apps/mcp-gateway/test_signin.py     # Tokenprüfung: Unterschrift, Issuer, Audience, Ablauf, Schlüssel
python3 apps/mcp-gateway/test_gateway.py    # Konformitätsliste der Spec §12, Ende zu Ende
```

Die letzte Datei stellt zwei **Papier-MCP-Server** hin, die sich auf
Ansage danebenbenehmen (Weiterleitung, Ausfall, verlorene Sitzung,
geänderte Beschreibung, Übergröße, Rückfrage, fremde
Protokollversion); der zweite bietet dasselbe Tool `echo` wie der erste
und dazu Resources, eine Vorlage und einen Prompt. Sie lauschen auf der
LAN-Adresse des Rechners, weil das Gateway Loopback nie anfährt.
Daneben stehen ein dritter Papier-Server, der sagt, mit welchem Token er
gerufen wurde, und ein **Papier-Anmeldedienst** (Discovery, Schlüssel,
Token-Tausch wie Keycloak, mit eigenem RSA-Schlüssel aus
`test_rsa_keys.py`).

Die Bücher (Schlüssel, Kontext, Prüfspur, Tresor, die eine Abfrage,
Grafiken) stehen in `gwcore/` — eine Kopie von
[`oaap-apps/lib/gwcore`](../../lib/gwcore/), gemeinsam mit dem
KI-Gateway. Geändert wird nur dort, dann `check-shared.py --fix`.

## Was noch fehlt

- **MCP `2026-07-28`:** zustandslos, ohne `initialize` und Sitzungen.
  Ein Client dieser Version erkennt das Gateway an seiner Antwort als
  Server der älteren Art und fällt zurück (Spec §6.2). Die neue Version
  selbst zu sprechen ist ein Umbau.
- Abonnements von Resources und Änderungsmeldungen an den Client —
  dafür fehlt der vom Server ausgehende Strom über `GET` (0.2 antwortet
  405).
- Die Clients im Realm legt der Betreiber von Hand an; ein Auftrag des
  IdP-Konnektors folgt (RFC-0065 D15). Nur RS256, nur ein Issuer.
- **Stufe 4:** der Benutzer bis ins SAP-System (Verfahren P,
  RFC-0066).
- `pattern` und `format` in Schemas der Server werden nicht
  durchgesetzt; die Seite des Servers nennt sie je Tool.
