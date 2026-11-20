# OAAP MCP-Gateway

MCP-Server hinter **einem Endpunkt je Sicht**. Ein KI-Client — Claude
Code, Copilot, ein eigener Agent — spricht mit `/mcp/<sicht>` wie mit
einem einzigen MCP-Server und erfährt nie, welche Server dahinterstehen
oder wo.

Umsetzung der Capability [`oaap.ai.mcp-gateway`
0.5](../../../oaap-spec/spec/oaap.ai.mcp-gateway.md) = RFC-0065 Stufen 1
bis 3 und RFC-0073 Stufen 1 und 2: Sichten bündeln Tools, Resources und
Prompts mehrerer Server; hinein mit einem Schlüssel oder als Person,
angemeldet am Anmeldedienst des Mandanten; zum Server technisch
(Verfahren T), als diese Person über einen getauschten Token
(Verfahren X) oder zu einer App der Plattform als diese Person über die
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
| Tokens einer Person selbst prüfen (RS256, Issuer, Audience, Ablauf) | ein Token an einen Server weiterreichen |
| bei Verfahren X ein Token nur für das Backend eintauschen | auf den technischen Zugang ausweichen, wenn die Person fehlt |

## Zwei Wege, zwei Rollen

- **`/mcp/...` ist `public`.** Die Plattform authentifiziert hier
  nichts, entfernt gefälschte Identitäts-Kopfzeilen und bremst je
  Client-Adresse (RFC-0010). Geprüft wird im Dienst: ein
  **API-Schlüssel** oder das **Token einer angemeldeten Person**. Wer
  nicht darf, bekommt immer dieselbe `401` — ob ohne Schlüssel, mit
  falschem Token oder für eine Sicht, die es nicht gibt.
- **`/.well-known/oauth-protected-resource` ist `public`.** Dort
  erfährt ein MCP-Client, bei welchem Anmeldedienst er sich anmeldet.
- **`/me` ist `user`** (0.15). Die eigene Seite der Person: ihre
  Aufrufe, Fehler in Worten (siehe unten).
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

## Anmeldung von Personen (0.3)

Die Plattform zeigt einer App ihren Anmeldedienst bewusst nicht
(RFC-0041). Das MCP-Gateway ist die Ausnahme, und der Betreiber richtet
sie ein (RFC-0065 §15, D14–D16):

1. **Im Realm des Mandanten** (Keycloak), von Hand:
   - ein **vertraulicher Client** des Gateways, z. B. `mcp-gw-<instanz>`,
     ohne Anmeldefluss; für Verfahren X mit eingeschaltetem
     *Standard token exchange*;
   - ein **öffentlicher Client** für Personen, z. B. `mcp-claude`:
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
   gewählt; die Rate gilt je Person, das Budget für alle zusammen.
4. **Claude Code**, einmal je Person:

```bash
claude mcp add --transport http <sicht> https://<gateway>/mcp/<sicht> \
  --client-id mcp-claude --callback-port 33418
```

In der **VS-Code-Erweiterung** gibt es den Befehl `claude mcp` nicht.
Dort legt man eine `.mcp.json` in den geöffneten Ordner (die
Gateway-Seite zeigt sie fertig ausgefüllt), wählt in `/mcp` die Sicht
und klickt „Authenticate“:

```json
{
  "mcpServers": {
    "<sicht>": {
      "type": "http",
      "url": "https://<gateway>/mcp/<sicht>",
      "oauth": { "clientId": "mcp-claude", "callbackPort": 33418 }
    }
  }
}
```

**Nicht** über den Dialog in `/mcp` eintragen: Er kennt keine
Client-ID. Ohne sie versucht Claude Code, sich selbst im Realm zu
registrieren (Dynamic Client Registration), und Keycloak lehnt das ab
(Policy „Trusted Hosts“). Gemessen am 07.11. mit Claude Code 2.1.319.

Claude Code öffnet den Browser, die Person meldet sich an, und das
Gateway prüft das Token selbst: RS256, mindestens 2048 Bit, Issuer,
Audience, Ablauf. Die Schlüssel des Issuers holt es nur von dessen
eigener Adresse. In der Messzeile steht die Person als `sub` mit
Issuer — nie Name oder E-Mail.

**Verfahren X (Token-Tausch):** Ein Server mit Zugang `exchange` wird
als diese Person gerufen. Das Gateway tauscht dessen Token beim
Token-Endpunkt gegen eines nur für das Backend (`audience`) und reicht
das eigene Token nie weiter. Klappt der Tausch nicht, wird abgelehnt —
nie der technische Zugang. Eine Sicht mit so einem Server nimmt nur
Anmeldungen an. Den Katalog eines solchen Servers liest der Betreiber
mit eigenem Token:

```bash
echo "$TOKEN" | docker exec -i <container> python3 /srv/app.py server read <name> --token-stdin ja
```

## Quellen der Plattform (0.4), Verfahren I (0.5), Clients von der Plattform (0.6)

Eine App, die im Manifest eine MCP-Tür erklärt (`mcp: {path: /mcp}`),
ist eine **Quelle**. Dieses Gateway erklärt `mcp_sources: {read: true}`
und bekommt von der Plattform die Liste der Quellen **seines Mandanten
auf diesem Knoten** (RFC-0073, Spec §3.6). Abgeglichen wird beim Start,
alle 5 Minuten, 30 s nach einem Fehler und mit `source sync`. Eine neue
Quelle wird ein Server mit ihrem Instanznamen, Ampel rot, **in keiner
Sicht**, bis der Betreiber freigibt. Verschwindet sie, bleiben ihre
Einträge stehen und sind als weg markiert.

**Verfahren I (0.5):** Nennt die Liste eine Tür, wird die Quelle mit
Zugang `platform` eingetragen. Eine angemeldete Person erreicht die App
dann **als er selbst**: Das Gateway tauscht sein Token mit dem eigenen
Client (`MCPGW_AUDIENCE`, Geheimnis `MCPGW_CLIENT_SECRET`) gegen eines
für den Plattform-Client des Realms (`MCPGW_PLATFORM_AUDIENCE`) und ruft
die Tür auf der eigenen Seite mit dem Quellen-Schlüssel und diesem
Act-Token. Die Plattform prüft beides, findet die Person über ihre
Bindung und gibt der App die sechs Kopfzeilen der Person. Die App ändert
nichts. Ohne Person kein Aufruf, kein Rückfall auf einen technischen
Zugang. Eine Sicht mit so einem Server nimmt nur Anmeldungen an.

**Agenten (Verfahren T):** Für eine Sicht mit Schlüsseln trägt der
Betreiber dieselbe Instanz ein zweites Mal von Hand ein, mit dem
Schlüssel eines Agenten (`oaap authz agent-add`), beschränkt auf die
Quelle. Eine Quelle, der der Betreiber schon einen Zugang gegeben hat,
behält ihn.

**Clients von der Plattform (0.6):** Die beiden Clients im Realm muss
niemand mehr von Hand anlegen. Ein `server_admin` klickt auf der
Instanzseite im Portal „Anmeldung einrichten“ oder ruft auf dem Knoten

```bash
sudo oaap idp mcp <konnektor> --instance <gateway> [--dry-run]
```

Dann legt der Konnektor, der den Realm des Mandanten angelegt hat,
`oaap-mcp-<instanz>` (vertraulich, nur Tausch) und
`oaap-mcp-<instanz>-login` (öffentlich, PKCE, Rücksprung
`http://localhost:33418/callback`) an. Die Plattform reicht Issuer,
Client-IDs, Geheimnis und Plattform-Client als `OAAP_MCP_IDP_*` herein.
Sie gelten, wo `MCPGW_*` leer ist; was von Hand gesetzt ist, gewinnt. Die
Karte „Anmeldung von Personen“ zeigt je Einstellung, woher sie kommt.
`--off` (oder „abschalten“) deaktiviert beide Clients, gelöscht wird
nichts.

```bash
docker exec <container> python3 /srv/app.py source list
docker exec <container> python3 /srv/app.py source sync
echo "$TOKEN" | docker exec -i <container> python3 /srv/app.py server read <quelle> --token-stdin ja
```

**Katalog ohne Person (0.10.0, RFC-0073 §16):** Eine Plattform ab 0.1.300
nennt je Quelle eine **Erkundungs-Tür** (`discover`). Dort liest das Gateway
den Katalog ohne angemeldete Person — gleich nachdem eine Quelle
auftaucht, alle `MCPGW_CATALOG_MINUTES` und auf Knopfdruck. Die Plattform
lässt dort nur den Verbindungsaufbau, die vier Listen und das Lesen der
Ansichten (`ui://`) durch, die Werkzeuge nennen — nie einen Aufruf. Die App sieht das Gateway als Maschinen-Benutzer `instance:<gateway>`
und zeigt ihm dessen Liste; nach dem Lesen steht „Als Gateway gelesen“.
Aufrufe laufen weiter nur als Person (Verfahren I oder A).

## Verfahren A: ein Chat der Plattform trägt die Person (0.7)

Eine Chat-App desselben Mandanten (Manifest 0.12, `mcp_client: {use: true}`)
erreicht das Gateway über eine Tür auf ihrer **eigenen** Seite,
`/platform/mcp/via/<gateway>/<sicht>` (RFC-0074). Die Plattform prüft dort den
Schlüssel des Chats und die Zusicherung der Person, die gerade mit ihm spricht,
und schickt die Anfrage mit den Kopfzeilen der Person hierher. Keycloak wird
dafür nicht gebraucht.

Im Gateway sind zwei Dinge einzurichten:

```bash
docker exec <container> python3 /srv/app.py view set kp-i --ways signin,platform
docker exec <container> python3 /srv/app.py profile create kp-chat \
  --groups @<chat-instanz> --views kp-i --max-effect write --cost-center CHAT
```

- Die Sicht braucht den Zugang `platform` (Zugänge sind eine Liste aus `key`,
  `signin`, `platform`, in dieser Reihenfolge).
- Es zahlt ein Zugangsprofil mit `@<chat-instanz>` oder `@*` (alle Chats des
  Mandanten). Ein Profil mit `*` gilt für Chats **nicht**.
- Zu einer Quelle der Plattform reicht das Gateway die Zusicherung weiter, ohne
  Token-Tausch. Ein Server mit Verfahren X ist so nicht erreichbar.
- Gemessen wird Verfahren `A`, mit der Kennung der Person, dem Chat und, wenn
  der Chat ihn nennt, dem Assistenten (`X-OAAP-Assistant`).
- **Seit 0.7.1** nennt `tools/list` je Werkzeug die Wirkungsklasse aus der
  Sicht: `_meta: {"oaap/effect": "read" | "write" | "execute"}` (ab Protokoll
  2025-06-18). Der Chat (`apps/chat`) fragt vor allem, was nicht `read` ist.
- **Seit 0.7.2** steht in der Messzeile `confirmed` = `yes`, wenn der Chat
  meldet, die Person habe den Aufruf bestätigt (`X-OAAP-Confirmed`) — eine
  Angabe des Chats, keine Erlaubnis. Und „Katalog lesen“ auf der
  Betreiberseite nimmt für eine Quelle der Plattform die Zusicherung der
  angemeldeten Betreiberin: auch ohne Keycloak lassen sich die Werkzeuge der
  eigenen Apps in eine Sicht nehmen.
- **Seit 0.7.3:** Sichten, Wirkungsklasse und Ampel eines Schlüssels oder
  Zugangsprofils lassen sich ändern („Ändern“ in der Tabelle, `app.py key set
  <etikett> --views a,b`), ohne Widerruf. Eine 403 nennt den Grund (Sicht nicht
  im Profil, Zugang fehlt, Ampel zu hoch), und die Tabelle zeigt denselben Satz
  beim Profil. Knopf „Quellen jetzt einlesen“ bei den Servern.
- **Seit 0.9.0: Betreiberseite in Reitern.** Übersicht (Kacheln, „Zu tun“ mit
  allem, was Aufmerksamkeit braucht: gesperrte oder fehlende Einträge, leere
  Sichten, ungelesene Kataloge, Zugänge, die an einer Sicht scheitern; dazu der
  Zusammenhang Server → Sicht → Zugang), Server, Sichten, Schlüssel & Profile,
  Anmeldung, Aufrufe & Prüfspur, Auswertung (`./?tab=…`). „Quellen jetzt
  einlesen“ ist ein eigener Kasten oben bei den Servern und in der Übersicht;
  Formulare zum Anlegen sind hinter „+ …“ eingeklappt. Nach einer Handlung
  zeigt die Seite den passenden Reiter.
- **Seit 0.8.0: Ansichten (MCP Apps, RFC-0075).** Nennt ein Tool mit
  `_meta.ui.resourceUri` eine Ansicht `ui://…`, liest das Gateway sie beim
  Katalog-Lesen und hält ihren **Inhalt** fest (SHA-256, dazu MIME und die
  CSP-Angaben). Wer das Tool in eine Sicht nimmt, nimmt genau diese eine
  Ansicht mit; ein Tool, dessen Ansicht sich nicht lesen lässt, wird nicht
  freigegeben. Liefert der Server später einen anderen Inhalt, geht nichts
  davon an den Client (Messzeile `ui_changed`), und nach dem nächsten
  Katalog-Lesen sind Ansicht **und Tool** gesperrt, bis jemand das Tool
  bestätigt — das bestätigt die neue Ansicht mit. Ins festgehaltene Tool
  kommt nur `_meta.ui` (`resourceUri`, `visibility`), keine anderen
  `_meta`-Angaben des Herstellers.

## Verfahren P: die Person aus dem Chat bis ins Zielsystem (0.12)

Ein MCP-Server **in SAP** (ein ICF-Dienst) soll als die Person arbeiten, die im
Chat fragt: `SY-UNAME` ist diese Person, es gelten ihre Berechtigungen. Das
Gateway hält dafür nichts. Die Plattform hat eine Destination mit
`principal_propagation` (RFC-0066). Sie nennt den SAP-Benutzer aus dem Attribut
`sap_user` der Person, stellt für genau ihn ein kurzlebiges Zertifikat der
Mandanten-CA aus und meldet ihn damit am gepinnten Ziel an. Das Gateway reicht
nur die Zusicherung weiter, die die Plattform ihm für die Person gegeben hat.

```bash
# einmal, als server_admin auf dem Knoten (P1): Destination und Bindung mit --propagate
oaap destination add sap-a4h --tenant bec --kind http --target https://<sap>:<port> \
  --auth principal_propagation --accept-sha256 <fingerabdruck>
oaap destination bind <gateway> sap-a4h --tenant bec --propagate
# im Gateway: Server mit Pfad hinter der Destination, Sicht nur für die Plattform
docker exec <container> python3 /srv/app.py server set sap \
  --address destination:sap-a4h/zmcp2/bc/access-support --auth propagate --light yellow
docker exec <container> python3 /srv/app.py view set sap --ways platform
```

- **Adresse** nur `destination:<name>[/pfad]`, **keine Zugangsdaten** (beides
  wird sonst abgewiesen). Der Pfad hängt am Ziel der Destination; `..`,
  Abfrage und Fragment gehen nicht.
- **Nur mit Person:** aus einem Chat (Verfahren A) oder auf der Betreiberseite.
  Ein Schlüssel wird abgewiesen, nie technisch ersetzt. Eine Anmeldung beim
  Anmeldedienst nimmt die Destination noch nicht an (RFC-0066 Form B), das sagt
  ein Satz. Darum gehört ein P-Server in eine Sicht mit Zugang `platform`, nicht
  in eine mit Schlüsseln.
- **Katalog lesen** (P3): auf der Betreiberseite als die angemeldete Person.
  Sie braucht dafür einen eigenen SAP-Benutzer. Einen technischen
  Katalog-Benutzer gibt es nicht; ohne Person bleibt der Katalog, wie er
  zuletzt gelesen wurde.
- **Sitzungen je Person** (P4): Eine zweite Person bekommt nie die Sitzung der
  ersten, und kein Cookie des Zielsystems wird zurückgeschickt.
- **Ablehnungen** der Destination oder von SAP (kein `sap_user`, keine
  Zuordnung des Zertifikats) gehen mit ihrem Satz an die Person: „Die
  Destination oder das Zielsystem lehnt ab (HTTP 403): …“. Gemessen als
  `refused`/`destination`; der Sicherungsautomat zählt das nicht.
- Gemessen wird Verfahren `P` mit Person, Chat und Assistent.
- **Ampel** (P5): ein Demosystem mit Demodaten `yellow`, Kundensysteme `red`.

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

## Zuordnen als Baum, Sichten kopieren und umbenennen (0.13)

Bis 0.12 kam jeder Eintrag einzeln über den Katalog eines Servers in eine
Sicht („In Sicht legen“, je Zeile Sicht und Klasse wählen) -- bei 31
Einträgen des SAP-Servers in bec mühsam (Jörg, 2026-11-18).

- **Seite einer Sicht → „Zuordnen“:** alle Kataloge als Baum
  *Server → Art → Eintrag* mit Ankreuzfeldern, wie in einem Dateibaum.
  Angekreuzt = in dieser Sicht. Haken setzen gibt frei -- dieselbe Prüfung
  wie „In Sicht legen“, Name vorgeschlagen, Wirkungsklasse daneben
  (Vorschlag aus den Angaben des Servers); Haken entfernen nimmt heraus;
  eine geänderte Klasse wird übernommen. Ein Haken an Server oder Art setzt
  alle darunter (mit Skript; ohne geht jedes Blatt einzeln). Was nicht
  freigebbar ist (Schema nach außen, Verfahren X/I/P in einer Sicht für
  Schlüssel), ist ausgegraut und nennt den Grund. Ein Eintrag, den sein
  Server nicht mehr anbietet, steht nicht im Baum und bleibt, wie er ist:
  Herausgenommen wird nur, was das Formular gezeigt hat.
- **Reiter Sichten → „Was in den Sichten liegt“:** die umgekehrte
  Richtung, *Sicht → Server → Art → Eintrag*, alles angekreuzt; Haken weg
  und „Ohne Haken herausnehmen“.
- **Kopieren oder umbenennen** (Seite der Sicht): Die Freigaben gehen mit,
  wie sie sind (derselbe festgehaltene Text, wer, wann). *Umbenennen*
  stellt gültige Schlüssel und Zugangsprofile auf den neuen Namen um und
  löscht die alte Sicht (Prüfspur `view.rename` mit den Umgestellten).
  Die Seite nennt vorher, wer den Namen nennt. **Nicht** umgestellt werden
  MCP-Clients mit der Adresse `/mcp/<alt>` und Assistenten eines Chats
  (Feld „Sicht“) -- sie stehen außerhalb des Gateways; dort den neuen
  Namen eintragen. Die alte Adresse antwortet danach wie jede unbekannte
  Sicht. Messzeilen behalten den alten Namen: sie sagen, was war.

## Server umbenennen, Sichten für den Chat (0.14)

- **Server umbenennen** (Seite des Servers): Katalog und Einträge in den
  Sichten ziehen mit, die Freigaben bleiben (derselbe Fingerabdruck). Die
  Namen der Einträge in den Sichten bleiben, wie sie sind -- auch ein
  vorgeschlagenes `<server>_<name>`; Clients merken nichts. Ein Server aus
  der Quellenliste der Plattform behält seine Herkunft (`source` =
  Instanzname) und wird beim Abgleich darüber gefunden, nicht über den
  Namen -- kein zweiter Server, „fort“ und „zurück“ wirken weiter.
  Prüfspur `server.rename`; Messzeilen behalten den alten Namen.
- **`GET /mcp/_views`** -- nur über die via-Tür der Plattform (Chat, Person
  und Zusicherung gesetzt), sonst dieselbe 401 wie jede Ablehnung. Antwort:
  die Sichten, die diese Person aus diesem Chat öffnen kann -- dieselbe
  Regel wie beim Aufruf (Profil mit `@<chat>` oder `@*`, Zugang
  „Plattform“, Ampel) --, je mit Zahl der aktiven Werkzeuge, Prompts,
  Resources, Ampel und höchster Klasse. Wer `tenant_admin`, `server_admin`
  oder `admin` ist, bekommt **alle** Sichten, die übrigen mit dem Grund
  („kein Zugangsprofil erlaubt diese Sicht“, „Zugang Plattform fehlt“ …):
  so sieht die Verwaltung, was sie noch einrichten muss. `_views` kann nie
  eine Sicht sein (Namen beginnen mit Buchstabe oder Ziffer).

## Meine Werkzeuge: `/me` für die Person (0.15)

RFC-0095 Stufe 1. Wer das Gateway benutzt, ohne es zu verwalten, sieht auf
`/me` (im Portal „Meine Werkzeuge“) die **eigenen Aufrufe der letzten 30
Tage**: Zeit, Sicht, Eintrag, Chat und Assistent, Verfahren, Ausgang,
Dauer. Voreingestellt ist „nur Probleme“, wenn es in den letzten 7 Tagen
welche gab; dazu Filter nach Sicht und Tag. Dasselbe als JSON unter
`/me/v1/calls`.

- Gezeigt werden nur Messzeilen mit genau der Kennung, die die Plattform
  als `X-OAAP-User-Id` setzt, also die Aufrufe, die sie getragen hat
  (Verfahren A, P, I). Schlüsselaufrufe haben keine Person und stehen nie
  da. Aufrufe mit eigener Anmeldung am Anmeldedienst (Claude Code) folgen,
  sobald die Plattform die Kennung aus dem Realm mitgibt (D2).
- Jeder Fehler steht **in Worten** da, mit dem, der abgelehnt hat, und mit
  einem Hinweis, etwa: „Die Destination oder das Zielsystem hat abgelehnt
  (HTTP 403). Frag die Verwaltung des Mandanten; nenne Zeit und Sicht.“
- Der Satz entsteht nur aus Codes. Dafür hat jede Messzeile seit 0.15 zwei
  Spalten mehr: `status` (HTTP-Nummer) und `door` (`gateway`, `platform`,
  `destination`, `server`). Freien Text gibt es weiterhin nicht. Zeilen von
  vor 0.15 bekommen die Tür, die ihr Ausgang nahelegt.

## Verfahren U: jede Person bringt ihren eigenen Zugang (0.16)

RFC-0095 Stufe 2. Für Server, die Personen nur an einem eigenen Token
erkennen (Jira, GitHub, Confluence, viele SaaS-Server):

1. **Betreiber:** Server mit Zugang `personal` anlegen. Einstellen, wie der
   Zugang geschickt wird (`Authorization: Bearer` oder eine Kopfzeile mit
   Präfix, etwa `X-Api-Key: Token …`), dazu ein Hinweis und ein Link für die
   Person. Zugangsdaten des Servers gibt es hier nicht, auch keinen
   Rückfall. Den Katalog liest das Gateway mit deinem eigenen Zugang: erst
   auf `/me` hinterlegen, dann „Katalog jetzt lesen“.
2. **Person:** auf `/me` unter „Meine Zugangsdaten“ den Zugang eintragen.
   Er liegt verschlüsselt im Tresor, in einem eigenen Bereich und an die
   Person gebunden, und wird nie wieder angezeigt. Ersetzen und löschen
   geht jederzeit.
3. **Aufruf:** Das Gateway schickt nur diesen Zugang mit, sonst nichts von
   der Person. Fehlt er, wird abgelehnt, bevor etwas hinausgeht, mit
   Verweis auf `/me`. Lehnt der Server ihn ab (401/403), steht auf `/me`
   „erneuern“; der Sicherungsautomat bleibt zu.
4. **Betreiber, Serverseite:** wie viele Personen einen Zugang haben (als
   Kennung der Plattform, nie mit Namen), wann gespeichert, zuletzt benutzt,
   nach 180 Tagen ohne Nutzung markiert. Löschen ja, lesen nie.

## Bereitschaft und Kennung im Realm (0.17)

RFC-0095 Stufe 3 und D2.

- **Bereitschaft:** Der Chat fragt über die via-Tür `GET /mcp/_readiness?view=…`
  und erfährt je Server, ob die Sicht für die Person eingerichtet ist:
  `ready`, `needs_credential` (eigener Zugang fehlt, mit Hinweis und Link),
  `credential_rejected` (der Server hat ihn abgelehnt). Dazu kommt die
  Adresse von `/me` aus `OAAP_INSTANCE_NAMES`. Kann die Person die Sicht
  nicht öffnen, nennt die Antwort keine Server. `/me` zeigt „vom Server
  abgelehnt – erneuern“ an der Zugangskarte.
- **Kennung im Realm (D2):** Ab Plattform 0.1.307 nennt die Plattform auf
  `/me` die Kennung der Person im Realm (`X-OAAP-User-Idp`) und sagt in
  `OAAP_IDENTITY_HEADERS` zu, dass sie diese Kopfzeile schützt. Dann zeigt
  `/me` auch Aufrufe mit eigener Anmeldung (Claude Code), und solche Aufrufe
  finden den persönlichen Zugang. Ohne die Zusage wird die Kopfzeile
  ignoriert.

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
**Elicitation** (die Person fragen: „Welcher Transportauftrag?“),
**Roots** (die Arbeitsverzeichnisse des Clients) und **Sampling** (das
Modell des Clients für sich arbeiten lassen). Je Sicht einstellbar,
jede für sich: durchreichen oder ablehnen.

- Neue Sichten: Elicitation und Roots durch, **Sampling abgelehnt** —
  damit könnte ein Server auf Kosten des Clients ein Modell benutzen
  und ihm Daten zuspielen.
- Sichten aus 0.1 lehnen weiter alles ab, bis der Betreiber es ändert.
- Durchgereicht wird nur, was die Sicht erlaubt **und** der Client
  kann; dem Server sagt das Gateway auch nur das zu.
- Die Antwort der Person geht durch und wird nirgends gespeichert.
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
alle Personen aus.

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
python3 apps/mcp-gateway/test_sources.py    # Quellen der Plattform (§3.6)
python3 apps/mcp-gateway/test_platform_settings.py  # Einstellungen von der Plattform (§3.8)
python3 apps/mcp-gateway/test_setup.py      # Einrichtungstür (RFC-0104 Stufe 2)
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

## Die Ampel des Gesprächs als Decke je Anfrage (0.21)

RFC-0101 Stufe 1, wie im KI-Gateway. `X-OAAP-Light: green|yellow|red`
verschärft die Decke von Schlüssel oder Profil für diese eine Anfrage
und lockert sie nie. Ist die Ampel der Sicht (die ihres schlechtesten
Servers) schlechter als die verlangte Farbe, kommt 403 mit einem Satz:
„das Gespräch steht auf grün, die Sicht sap ist gelb (Server a4h)“.
Die Person darf die Sicht öffnen, die Prüfung von Schlüssel und Profil
lief vorher; der Satz verrät also nichts Neues.

Geprüft wird bei **jeder** Anfrage, auch innerhalb einer offenen
Sitzung. Wechselt die Person im Chat auf grün, wird schon der nächste
Aufruf abgelehnt. Ein unbekannter Wert wird mit 400 abgelehnt. Die
Messzeile trägt die verlangte Farbe in der neuen Spalte `light` (§10
bleibt wahr: ein Wort, kein Inhalt).

## Assistenten mit Revisionen (0.22)

RFC-0097 Stufe 1. Die Assistenten der Chats liegen hier, neben den Sichten,
die sie nennen. Ein Chat liest und schreibt sie über die via-Tür als die
Person:

| Weg | was |
|---|---|
| `GET /mcp/_assistants` | die Assistenten, die diese Person benutzen darf (geltende Revision, `rev`, `state`, `manage`) |
| `GET /mcp/_assistants/<id>[?rev=n]` | eine Revision; `410`, wenn verdichtet |
| `GET /mcp/_assistants/<id>/revisions` | Nummer, Zeit, wer -- nur wer ihn pflegt |
| `POST /mcp/_assistants` | speichern = neue Revision; die Nummer vergibt das Gateway |
| `POST /mcp/_assistants/_import` | Austauschdatei, alles oder nichts; wer importiert, wird Besitzer |
| `POST /mcp/_assistants/_migrate` | die alte Datei eines Chats, Besitzer bleiben -- nur `tenant_admin` |
| `POST /mcp/_assistants/<id>/state` | `active`, `inactive`, `deleted` |

- **Sichtbarkeit** wie bisher im Chat. Die Regel steht in
  `lib/assistants` und ist in Chat und Gateway dieselbe Kopie
  (`check-shared.py`). Privat sieht nur der Besitzer; einen fremden
  privaten Assistenten findet man auch gezielt nicht (404 wie unbekannt).
  Für den ganzen Mandanten pflegt ihn nur `tenant_admin`.
- **Team-Assistenten:** Das Gateway erklärt das Rechteobjekt `assistant`
  selbst, mit denselben Vorlagen wie der Chat. Die Plattform nennt
  Rechte nur der App, die fragt. Sind die Rechte nicht lesbar, sieht
  niemand einen Team-Assistenten.
- **Aufbewahrung (D6):** Jede Revision, auf die eine Messzeile zeigt,
  bleibt, dazu immer die letzten 20. Ältere werden verdichtet: Nummer,
  Zeit und wer bleiben, der Text geht.
- **Messzeile:** `X-OAAP-Assistant: <id>@<rev>` landet getrennt in
  `assistant` und der neuen Spalte `assistant_rev`.
- Jedes Speichern steht in der Prüfspur (`assistant.save`, `.import`,
  `.migrate`, `.state`).
- Die Betreiberseite listet Assistenten noch nicht. Das kommt mit
  Stufe 2 (Auswertung je Revision).

## Sterne (0.23)

RFC-0101 Stufe 2. Personen bewerten Antworten und ganze Gespräche mit 0
bis 5 Sternen. Der Chat schickt das über die via-Tür als die Person:

| Weg | was |
|---|---|
| `POST /mcp/_assistants/<id>/ratings` | eine Bewertung setzen, ersetzen oder mit `stars: null` zurückziehen |
| `GET /mcp/_assistants/<id>/ratings` | je Revision Zahl, Verteilung, Anteil 0–2, Personen; dazu die letzten 500 -- für wer pflegt und tenant_admin |
| `GET /mcp/_assistants/_ratings` | die eigenen Bewertungen |

- **Wer:** nur ein HMAC der Person mit `/data/ratings.key` (entsteht
  beim ersten Mal, 0600). Die Kennung des Gesprächs steckt nur in einem
  zweiten HMAC. Ein Name oder eine Kennung steht nirgends.
- **Kein Inhalt ohne Haken:** Gespeichert werden nur die bekannten
  Felder, von Werkzeugen nur Name, Wirkung und Ausgang. Frage und
  Antwort nur mit Haken, höchstens 8000 Zeichen je Teil, nach 180 Tagen
  gelöscht (die Sterne bleiben).
- Eine Revision, die eine Bewertung nennt, wird nicht verdichtet.
- Die Betreiberseite zeigt die Bewertungen noch nicht. Das kommt mit der
  Auswertung je Revision (RFC-0097 Stufe 2 / RFC-0101 Stufe 4).

## Bereich für „Nicht zufrieden“ (0.24)

RFC-0101 Stufe 3. Ein Assistent darf `support_queue` tragen: den Bereich
der App Vorgänge, in den „Nicht zufrieden“ zu diesem Assistenten geht
(D6). Geprüft wird mit derselben Regel wie im Chat (`assistants/`, Kopie
aus `lib/`). Das Feld wird mit der Revision festgehalten. Der Vorgang
selbst geht nicht durch das Gateway, sondern über die Feedback-Route.

## Auswertung je Revision (0.25)

RFC-0097 Stufe 2 und RFC-0101 Stufe 4.
- **Reiter „Assistenten“** auf der Betreiberseite: je Assistent und
  Revision Aufrufe, abgewiesen, Fehler und Personen (aus den Messzeilen,
  nur gezählt), dazu Sterne und Vorgänge „Nicht zufrieden“. Außerdem ein
  Vergleich zweier Revisionen und Export je Revision.
- **Wer was sieht:** Gezeigt wird nach derselben Regel wie im Chat, mit
  der eigenen Person. Ein fremder privater Assistent fehlt ganz. Sterne
  und Vorgänge sieht, wer pflegt, und der tenant_admin.
- **Über die via-Tür:**
  - `GET /mcp/_assistants/<id>/evaluation`
  - `GET …/<id>/diff?a=&b=`
  - `POST …/<id>/cases {rev, number}` (der Chat zählt einen Vorgang;
    gespeichert nur HMACs, nie die Nummer).
- **Zeitraum:** Messzeilen reichen nur so weit zurück wie ihre
  Aufbewahrung. Die Seite sagt, seit wann.

## Wie ein Vorgang ausging (0.26)

RFC-0103 D10. `POST …/<id>/cases` nimmt zusätzlich `outcome`
(`accepted`, `reopened`, `no_response`); der letzte gilt, gezählt wird der
Vorgang weiter einmal. Die Auswertung je Revision zeigt „davon Lösung
passt“ neben den Vorgängen.

## Einrichtungstür (RFC-0104 Stufe 2)

Seit 0.27 kann ein Profil des Mandanten (etwa `ki-arbeitsplatz`) das
Gateway einrichten, ohne dass ein Mensch klickt. Das Manifest (0.18)
erklärt `setup: {path: /oaap/setup}`.
- **Wer hineinkommt:** nur wer `Authorization: Bearer <OAAP_SETUP_KEY>`
  schickt. Den Wert erzeugt die Plattform bei der Installation; keine
  Seite zeigt ihn. Der Knoten ruft über seinen internen Listener.
  Identitäts-Kopfzeilen zählen auf diesem Pfad nicht, auch nicht die
  eines `tenant_admin`. Fehlt der Schlüssel in der Umgebung, ist jede
  Antwort 403. `X-OAAP-Setup-Actor` (wer den Aufbau gestartet hat) steht
  in der Prüfspur als `setup:<name>`.
- **Aufrufe:**
  - `GET` nennt die Arten.
  - `GET ?kind=&id=` sagt `state`, `ready` und eine Notiz.
  - `POST {"v": 1, "dry": false, "items": [...]}` legt an.
  - `POST {"v": 1, "remove": [...]}` nimmt zurück.
  - Ergebnis je Eintrag: `present`, `made`, `would_make` (mit `dry`),
    `conflict`, `refused`, beim Zurücknehmen `removed` oder `gone`.
- **Arten:** Jede benutzt dieselbe Funktion wie die Betreiberseite.
  - `refresh`: Quellen einlesen und den Katalog über die Erkundungs-Tür
    lesen (wie „Quellen jetzt einlesen“ und „Katalog lesen“).
  - `server_light`: eine Ampel, nur aus dem Rot eines neuen Servers.
  - `view`: Zugänge und Tools. `{"server", "effect": "read"}` heißt: alle
    Tools des Servers mit `readOnlyHint: true`, dieselbe Regel wie bei
    der Hilfe (RFC-0094 D6).
  - `profile`: ein Zugangsprofil, etwa für `@<chat>`.
  - `assistant`: erste Revision `@1`, nur Sichtbarkeit Mandant.
- **Nie überschreiben:** Weicht etwas ab, das es unter dem Namen schon
  gibt, heißt das `conflict`. Ohne `dry` hält die Einrichtung dort an;
  was danach steht, wird nicht angefasst. Zurückgenommen wird nur, was
  die Einrichtung angelegt hat und was seither keiner geändert hat (die
  App merkt sich wer, wann und einen Digest). Zurücknehmen heißt: die
  Ampel wieder rot, die Sicht löschen, das Profil widerrufen, den
  Assistenten löschen.
- **Keine Geheimnisse:** Ein Feld `key`, `token`, `secret`, `password`
  oder `credentials` (auch verschachtelt) ergibt `refused`.

## Vorlagen für Werkzeuge (0.28, RFC-0107)

Reiter **„Vorlagen“**: die Datei, mit der ein Werkzeug Sichten dieses Gateways benutzt.

1. **Werkzeug wählen:** Claude Code (`.mcp.json`), VS Code (Copilots eigenes Format, ebenfalls
   `.mcp.json`) oder opencode (`opencode.jsonc`, Form „V2“ mit `mcp.servers`).
2. **Sichten wählen:** aus einem Schlüssel (dessen Sichten, nach derselben Regel wie ein Aufruf;
   was die Ampel des Schlüssels nicht erlaubt, steht als „ausgelassen“ mit Grund da) oder von
   Hand, je Sicht mit dem Zugang „Schlüssel“ oder „Anmeldung“.
3. **Kopieren oder herunterladen.** Darunter, wohin die Datei gehört und was vorher zu tun ist.

- **Nie ein Schlüsselwert in einer Datei.** Statt des Schlüssels steht die Umgebungsvariable
  `OAAP_MCP_KEY` da (`${OAAP_MCP_KEY}`, bei opencode `{env:OAAP_MCP_KEY}`). Den Wert zeigt nur der
  Kasten direkt nach dem Ausstellen, einmal, im `claude mcp add`-Befehl; der Kasten verweist auf
  die Vorlagen dieses Schlüssels.
- **Anmeldung:** Client-ID (`MCPGW_SIGNIN_CLIENT`) und Rücksprung-Port 33418. Ohne eingerichtete
  Anmeldung gibt es dafür keine Datei. **VS Code** bekommt vorerst nur den Schlüsselweg: Welche
  Rücksprung-Adresse VS Code schickt, ist nicht dokumentiert; der Realm nimmt nur
  `http://localhost:33418/callback`. Erst messen.
- Die Vorlagen sind Daten (`templates/*.json`), jede mit dem Datum, an dem ihr Format geprüft
  wurde. Gefüllt wird mit `gwcore.fill`, derselben Maschine wie im KI-Gateway.
- Der Reiter „Anmeldung“ zeigt seine `.mcp.json` jetzt aus der Vorlage „Claude Code“.

**Selbstbedienung:**

```bash
curl -H "Authorization: Bearer $OAAP_MCP_KEY" \
  "https://<gateway>/mcp/_client-config?target=claude-code" -o .mcp.json
```

Ziele: `claude-code`, `vscode`, `opencode`. Ein falscher oder widerrufener Schlüssel bekommt
dieselbe `401` wie überall; ein unbekanntes Ziel `400` mit der Liste; ein Schlüssel ohne Sicht,
die ein Werkzeug erreicht, `409` mit den Gründen. Der Abruf steht in der Prüfspur
(`client.config`), nicht im Verbrauch.

Auf **`/me`** steht „In meinem Werkzeug einrichten“: die Sichten mit Anmeldung, die ein
Zugangsprofil öffnet, und die Datei für Claude Code oder opencode (`/me/client-config?target=`).
Die Gruppen der Person im Realm kennt das Gateway dort nicht (die Plattform gibt Apps keine
Gruppen); bei einem Profil für bestimmte Gruppen steht deshalb „ob du dazugehörst, entscheidet
die Anmeldung“. Fehlt bei einem Server mit Verfahren U der eigene Zugang, steht „erst Zugang
hinterlegen“.

## Schreiben mit Schlüssel nur nach Freigabe (0.34, RFC-0105 Stufe 2)

Ein Schlüssel hat keinen Menschen hinter sich. Ein KI-Spezialist soll trotzdem schreiben
können, aber nie auf eigenes Wort. Dafür gibt es eine Einstellung je Sicht:

```sh
docker exec <container> python3 /srv/app.py view set spez-analyse --approval required
```

Damit gilt in dieser Sicht:
- Ein Werkzeug der Klasse `write` oder `execute` läuft über einen Schlüssel nur, wenn der
  Aufruf `X-OAAP-Confirmed: yes` **und** `X-OAAP-Approved-By: <Person>` trägt.
- Fehlt eines davon, lehnt das Gateway mit `-32003` ab. Die Messzeile hat dann den Grund
  `needs_approval` und die Tür `gateway`, der Server sieht nichts.
- Bei Erfolg steht die freigebende Person in der Messzeile (`approved_by`).
- Lesende Werkzeuge laufen wie bisher.
- Sichten ohne die Einstellung verhalten sich wie bisher, für Pipelines ändert sich nichts.

Die Angabe kommt vom Aufrufer, das Gateway prüft sie nicht. Verbindlich ist die Freigabe
in **Vorgänge**: Sie hängt dort an einem Hash über Werkzeug und Argumente, und die App
Spezialisten führt nur aus, was so freigegeben ist. Das Gateway sorgt dafür, dass ohne
diese Angabe nichts schreibt, und hält sie fest.

## Was noch fehlt

- **MCP `2026-07-28`:** zustandslos, ohne `initialize` und Sitzungen.
  Ein Client dieser Version erkennt das Gateway an seiner Antwort als
  Server der älteren Art und fällt zurück (Spec §6.2). Die neue Version
  selbst zu sprechen ist ein Umbau.
- Abonnements von Resources und Änderungsmeldungen an den Client —
  dafür fehlt der vom Server ausgehende Strom über `GET` (0.2 antwortet
  405).
- Nur RS256, nur ein Issuer.
- **Stufe 4:** der Benutzer bis ins SAP-System (Verfahren P,
  RFC-0066).
- `pattern` und `format` in Schemas der Server werden nicht
  durchgesetzt; die Seite des Servers nennt sie je Tool.
