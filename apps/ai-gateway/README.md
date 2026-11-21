# KI-Gateway

Eine Bezugsquelle für alle KI-Verbräuche: **ein OpenAI-kompatibler
Endpunkt**, der sich bei lokalen Modellen, externen Anbietern,
Kunden-Endpunkten oder einem anderen Gateway bedient. Der Verbraucher
fragt nach einem **Zweck** (`chat-default`, `code`,
`embedding-default`) und nie nach einem Hersteller.

Umsetzung der Capability [`oaap.ai.gateway`
0.5](../../../oaap-spec/spec/oaap.ai.gateway.md) (RFC-0023, RFC-0064).

## Was diese App tut — und was ausdrücklich nicht

| tut | tut nicht |
| --------------------------------------------- | ----------------------------------------------- |
| Aliasse nach Zweck auf Modelle abbilden        | Modelle verwalten, trainieren, feinabstimmen     |
| API-Schlüssel ausstellen, begrenzen, widerrufen | einen eigenen Login bauen                        |
| Verbrauch je Schlüssel zählen und zeigen       | Prompts oder Antworten speichern — **niemals**   |
| innerhalb einer erklärten Gruppe ausweichen    | stillschweigend ein anderes Modell einsetzen     |
| Zahlen mit einer Bezugsquelle abgleichen       | — jedes Gateway führt nur seine eigenen Bücher   |

## Zwei Wege, zwei Rollen

- **`/v1/...` ist `public`.** Die Plattform authentifiziert hier
  nichts, entfernt gefälschte Identitäts-Kopfzeilen und bremst je
  Client-Adresse (RFC-0010 — einmal je Anfrage, ein Strom bleibt also
  unbelastet). Geprüft wird im Dienst: **Der API-Schlüssel ist die
  Identität.** Keine Sitzung, keine Rollen, kein Kontakt zum
  Identitätsdienst. Derselbe Bau wie Deploy-Hook und Flotten-Auskunft.
- **`/` ist `admin`.** Die Betreiber-Sicht in sechs Reitern (seit 0.6,
  siehe unten): Dashboard, Letzte Anfragen, Auswertung, Schlüssel,
  Modelle, Bezugsquellen.

Beim Installieren fragt die Plattform einmal nach, weil `/v1` ohne
Login erreichbar wird. Das ist richtig so — und der Grund steht oben.

## Die Betreiber-Sicht (seit 0.6)

Sieben Reiter, Beobachten vorn, Einrichten hinten:

| Reiter | Adresse | zeigt |
| ------ | ------- | ----- |
| **Dashboard** | `/` | Kennzahlen der letzten 24 h mit Verlaufslinie (Anfragen, Token, Erfolgsquote, Antwortzeit, zuletzt), Säulen je Stunde (beantwortet/nicht beantwortet) und darunter die **aktuelle Auswertung**: Zeitfenster 1 h · 4 h · 24 h · 7 d · 30 d, Rangliste nach Aufrufen, Token oder mittlerer Dauer, Filter nach Modellname, Schlüssel und Quelle; Top 10 Modellnamen, Schlüssel, Bezugsquellen und die Ausgänge. Aktualisiert sich jede Minute. |
| **Letzte Anfragen** | `/anfragen` | die Messzeilen, neueste zuerst, filterbar nach Schlüssel, Modellname, Quelle und Ausgang (50/100/200) |
| **Auswertung** | `/auswertung` | Tage und Monate für Rechnungen, dieselbe Abfrage wie `/report/v1/usage` |
| **Schlüssel** | `/schluessel` | einzelne Schlüssel (optional mit Ende), Ausstellen, „So verbindet sich ein Client“ |
| **Kohorten** | `/kohorten` | Schlüssel für Schulungen: Tabelle einfügen, je Zeile ein Schlüssel mit Ende; verlängern, alle widerrufen |
| **Modelle** | `/modelle` | Modellnamen (Aliasse) mit **Test** je Name; **Modellnamen aus einer Bezugsquelle** per Mehrfachauswahl anlegen |
| **Bezugsquellen** | `/quellen` | Quellen mit **Verbindungstest** je Quelle und „Alle testen“, Quellen aus Destinationen der Plattform |

Das Dashboard rechnet in Stunden über dieselbe Tabelle `usage` (Modul
`dash.py`); gezeigt wird Ortszeit (`Europe/Berlin`, sonst UTC mit
Hinweis). Gespeichert bleibt UTC.

**Verbindungstest einer Bezugsquelle** („testen“): `GET …/v1/models`
mit den Zugangsdaten der Quelle, 15 s Wartezeit. Das ist ein Ping, der
auch die Anmeldung prüft, aber kein Modell rechnen lässt. Das Ergebnis
steht in Worten da:

- **erreichbar** (HTTP 2xx) mit Dauer und den Modellen der Quelle;
- **Anmeldung abgelehnt** (401/403): Der Weg steht, der Schlüssel nicht —
  bei einem anderen KI-Gateway dessen API-Schlüssel;
- **`…/models` unbekannt** (404): Adresse bis `/v1` prüfen;
- **Gegenstelle antwortet nicht** (502/503/504): Über eine Destination
  heißt das meist Tunnel nicht verbunden oder Angebot fehlt (Portal →
  Connector);
- **nicht erreichbar**: mit der Art des Fehlers (DNS, Verbindung,
  Zeitüberschreitung, TLS), nie mit Zugangsdaten.

**Test eines Modellnamens**: je Ziel eine Mini-Anfrage („ping“,
höchstens 1 Token). Sie zählt nicht in den Verbrauch dieses Gateways —
es gibt keinen Schlüssel, dem sie gehört —, die Quelle zählt sie.

## Was der Knoten erfährt (seit 0.7.0, RFC-0087 §8.2)

Das Gateway erklärt im Manifest (0.15) die Tür `assets: {path: /oaap/assets}`.
Der Knoten fragt dort bei jedem Statusdokument nach und gibt der Zentrale
weiter, was die Preisgabe-Stufe des Mandanten erlaubt:

```json
{"v": 1, "assets": [
  {"kind": "source", "name": "gaming", "state": "down", "destination": "gaming"},
  {"kind": "source", "name": "anthropic", "state": "idle"},
  {"kind": "model", "name": "chat-gaming", "state": "down", "uses": ["gaming"]}
]}
```

- **Bezugsquellen** mit Zustand und, falls ihre Adresse über eine
  Destination der Plattform führt, deren Name.
- **Modellnamen** mit den Quellen, die sie nutzen. Ein Modellname ist
  `ok`, wenn eine seiner Quellen es ist, `down`, wenn alle es sind.
- Nie: Adressen, Modellkennungen bei der Quelle, Ampel, Schlüssel,
  Kohorten, Personen.

Woher der Zustand einer Quelle kommt:

- aus **jedem echten Versuch**: jede Weiterleitung, auch die vor einem
  Ausweichen, und jeder Verbindungstest. Jede HTTP-Antwort unter 500 heißt
  „erreicht“, keine Antwort oder 5xx „nicht erreicht“;
- aus einer **Prüfung je Minute** für Quellen hinter einer Destination
  (Tunnel, VPN): `GET …/models`, rechnet nichts, kostet nichts;
- ein fremder Anbieter wird dafür **nicht** zusätzlich angefragt;
- liegt der letzte Versuch mehr als 15 Minuten zurück: `idle`. Nach einem
  Neustart steht jede Quelle auf `idle`, bis der nächste Versuch kommt.

Damit nennt die Meldung der Zentrale bei „VPN aus“ auch die Modellnamen,
die nicht mehr antworten.

## Anthropic als Bezugsquelle (seit 0.6.1)

Anthropic bietet eine OpenAI-kompatible Tür an — das Gateway braucht dafür
keine eigene Anthropic-Schnittstelle:

- **Adresse** `https://api.anthropic.com/v1`, **Zugangsdaten** der API-Key
  von Anthropic (geht als `Authorization: Bearer`), **Modelle** z. B.
  `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`.
- **temperature/top_p:** Die neuen Claude-Modelle lehnen sie mit 400 ab
  („`temperature` is deprecated for this model“), viele Clients schicken
  sie trotzdem. Für Quellen bei `api.anthropic.com` gibt das Gateway
  `temperature`, `top_p` und `top_k` **nie** weiter; für jede andere Quelle
  lässt sich das mit „temperature/top_p nicht weitergeben“ einschalten.
- **Verbindungstest und „Modelle abrufen“** schicken bei Anthropic die
  Kopfzeile `anthropic-version` mit (ohne sie antwortet die Modellliste
  mit 400) und holen alle Modelle auf einmal.
- Gemessen 13.11. auf oaapx01: Test 200 mit 15 Modellen; Anfrage mit
  `temperature` und `top_p` 200, Token-Zahlen da; Strom mit Token-Zahlen.
- Grenzen dieser Tür (Anthropic): kein Prompt-Caching, kein Reasoning in
  der Antwort, `response_format` nur als strenges JSON-Schema, keine Audio-
  und Dateianhänge. Anthropic nennt sie selbst einen Test- und
  Vergleichszugang.

## Kohorten: Schlüssel für Schulungen (seit 0.6, Spec 0.5 §2.1)

Reiter **Kohorten**. Teilnehmer aus Excel oder einer Liste einfügen —
eine Zeile je Person, Spalten mit Tab, Semikolon oder Komma getrennt:

```
Name            Nr   E-Mail          ← Überschrift wird erkannt (optional)
Anna Beispiel                        ← ohne Nummer: fortlaufend ab „erste Nummer“
Bernd Muster    07                   ← Nummer gilt
Carla Probe     07b  c@example.org   ← abweichende Kennung (Nachgenerieren)
```

- Name der Kohorte wie bei den Schulungsservern (RFC-0046), Etikett je
  Schlüssel `<kohorte>-tn-<kennung>`, also `kurs-2026-11-tn-07`.
- **gültig bis** (einschließlich, Ortszeit) gilt für alle; danach antwortet
  ein Schlüssel wie ein widerrufener (403), zählt aber nicht als
  Fehlversuch.
- Projekt (leer = Name der Kohorte), Kostenstelle, Anwendung, Budget je
  Teilnehmer, Ampel-Obergrenze, Modellnamen — für die Auswertung und als
  Grenze.
- Zurück kommt **dieselbe Tabelle mit Schlüsselspalte** (und Kennung, wo
  das Gateway sie vergeben oder geändert hat): als Seite, als Textfeld im
  Format der Eingabe zum Zurückkopieren und als CSV (Semikolon, UTF-8)
  zum Herunterladen. **Einmal** — die Seite wird nie zwischengespeichert.
- Ein Etikett wird nie zweimal vergeben, auch nach Widerruf nicht.
  Nachgenerieren: abweichende Kennung angeben (`07b`) oder „nächste freie
  nehmen“ (07 → 07b → 07c …). Eine vergebene Kennung ohne diese Wahl wird
  mit Vorschlag abgelehnt, nicht still übersprungen.
- Je Kohorte: verlängern (neues Ende für alle nicht widerrufenen, auch
  abgelaufene leben wieder auf), alle widerrufen (nur mit „wirklich“),
  einzelne Sitze widerrufen, Link in die Auswertung (Projekt).
- Der Name einer Zeile wird **Verantwortlicher** des Schlüssels und steht
  damit in Auswertung und Prüfspur. Wer das nicht will, fügt Kürzel ein.

## Modellnamen aus einer Bezugsquelle (seit 0.6)

Reiter **Modelle → „Modellnamen aus einer Bezugsquelle anlegen“**: Quelle
wählen, optional suchen, „Modelle abrufen“. Das Gateway fragt
`GET …/v1/models` und zeigt die Modelle zum Anhaken, je Modell mit einem
Namensvorschlag (ohne Herstellerpräfix, `:` → `-`:
`meta-llama/llama-3.3-70b:free` → `llama-3.3-70b-free`), der sich ändern
lässt. „Angehakte anlegen“ oder „alle gezeigten anlegen“ — je Modell ein
Modellname mit genau diesem Ziel. Vorhandene Namen werden **nie
überschrieben**; schon benutzte Modelle sind markiert. Höchstens 300
Modelle auf einmal, die Suche grenzt ein (OpenRouter nennt Hunderte).

## Bezugsquellen und Modellnamen (seit 0.4, RFC-0064 Stufe 2)

Gepflegt **auf der Betreiber-Seite**, wirksam sofort, ohne Neustart:

- **Bezugsquelle:** Name, Adresse (OpenAI-kompatibel, bis `/v1`), Ampel,
  Zugangsdaten. Die Zugangsdaten lassen sich setzen, ersetzen oder per
  Häkchen entfernen — ein leeres Feld heißt *unverändert*. Angezeigt
  werden sie nie wieder.
- **Modellname:** der Name, den ein Client eintippt, und seine Ziele
  `quelle:modell, quelle:modell` — die erklärte Ausweich-Gruppe.

Eine Quelle, die ein Modellname noch benutzt, lässt sich nicht löschen.
Wird ein Modellname gelöscht, nennt die Seite die Schlüssel, die auf ihn
beschränkt waren. Jede Änderung steht in der Prüfspur — bei
Zugangsdaten nur, *was* geschah, nie der Wert.

**Zugangsdaten liegen verschlüsselt unter `/data`.** Den Schlüssel dazu
liefert die geheime Variable **`AIGW_MASTER_KEY`** (Portal → Instanz →
Konfiguration, Knopf „erzeugen“). Er kommt bewusst aus einer anderen
Quelle als `/data`, damit eine Sicherung von `/data` allein die Keys der
Anbieter nicht preisgibt. Wird er geändert, sind gespeicherte
Zugangsdaten nicht mehr lesbar: Die Seite benennt die betroffenen
Quellen, und sie müssen neu eingetragen werden. Ohne `AIGW_MASTER_KEY`
lassen sich Quellen **ohne** Zugangsdaten (ein lokales Ollama) pflegen,
Zugangsdaten verweigert die App mit Begründung.

### Update von 0.3: einmalige Übernahme

Bis 0.3 standen Quellen und Aliasse als Zeilen in der Konfiguration
(unten). Beim ersten Start von 0.4 übernimmt die App sie **einmal** in
ihre Datenbank; danach werden die Variablen nicht mehr gelesen und
können geleert werden. Zwei Orte, die beide zählen, wären zwei
Wahrheiten.

Stehen in der Konfiguration Zugangsdaten, aber `AIGW_MASTER_KEY` fehlt,
**wartet** die Übernahme: Die Konfiguration gilt weiter wie bisher, die
Formulare sind gesperrt, und die Seite sagt, was zu tun ist. Also beim
Update: erst `AIGW_MASTER_KEY` erzeugen und speichern, dann übernimmt
die App beim Neustart alles. Nichts geht verloren, nichts landet im
Klartext.

Das alte Zeilenformat, wie es übernommen wird:

```text
AIGW_SUPPLIERS          eine Zeile je Bezugsquelle
  ollama=http://oaap-app-<ollama-instanz>:11434/v1 light=green
  tsystems=https://<ai-business-hub>/v1 light=yellow
  groq=https://api.groq.com/openai/v1 light=red

AIGW_SUPPLIER_KEYS      geheim — eine Zeile je Quelle, die eine braucht
  groq=<schlüssel>
  tsystems=<schlüssel>

AIGW_ALIASES            eine Zeile je Zweck
  chat-default = tsystems:<modell>, groq:<modell>
  embedding-default = ollama:nomic-embed-text
  code = tsystems:<modell> order=listed
```

**Ohne Angabe gilt `red`** — unbekannte Herkunft ist nicht souverän,
und der sichere Zustand ist der Standardzustand.

**Ausgewichen wird nur innerhalb der Gruppe einer Zeile**, und dabei
gilt grün vor gelb vor rot. Wer die aufgeführte
Reihenfolge will, hängt `order=listed` an. LLMs sind nicht
austauschbar wie Webserver hinter einem Lastverteiler; stilles
Ersetzen ändert das Verhalten, manchmal so leise, dass es wochenlang
niemand merkt.

Eine Zeile, die das Gateway nicht versteht, ist **nicht tödlich**: Sie
wird auf der Betreiber-Seite benannt und bleibt wirkungslos. Eine App,
die wegen eines Tippfehlers gar nicht erst startet, ist schlimmer als
eine, die sagt, was sie nicht verstanden hat.

## Schlüssel

Ausgestellt auf der Betreiber-Seite oder, für kopflose Knoten, an der
Maschine:

```bash
sudo docker exec oaap-app-<instanz> python3 /srv/app.py \
  key issue laptop@joerg --ceiling yellow --aliases chat-default
sudo docker exec oaap-app-<instanz> python3 /srv/app.py key list
sudo docker exec oaap-app-<instanz> python3 /srv/app.py key revoke laptop@joerg
```

Der Wert erscheint **genau einmal**; gespeichert wird nur sein
SHA-256. Wer ihn verliert, bekommt einen neuen — nachlesen lässt er
sich nirgends (dieselbe Hygiene wie beim Deploy-Token, RFC-0019).

Voreingestellt reicht ein Schlüssel bis **gelb**. **Rot ist eine
bewusste Zusatzerlaubnis** — Souveränität soll das sein, was passiert,
wenn niemand etwas einstellt.

Dazu je Schlüssel: Etikett, Aliass-Beschränkung, **Token-Budget** und
**Anfragen je Minute**. Die beiden letzten sind harte Grenzen — sie
schützen vor einer davonlaufenden Rechnung, was eine Rechnung hinterher
nicht tut.

### Kontext am Schlüssel (seit 0.3, RFC-0064)

Wonach ausgewertet und außerhalb verrechnet wird: **Account, Projekt,
Kostenstelle, Anwendung**, Verantwortliche(r) und bis zu **fünf freie
Etiketten** `name=wert`. Alles freier Text des Betreibers, leer erlaubt.
Änderbar jederzeit — auf der Betreiber-Seite („Kontext ändern“) oder an
der Maschine:

```bash
sudo docker exec oaap-app-<instanz> python3 /srv/app.py \
  key issue kurs-app --account acme --project ADT-Kurs \
  --cost-center KS-4711 --application webtop --labels kurs=adt-2026-11
sudo docker exec oaap-app-<instanz> python3 /srv/app.py \
  key context kurs-app --cost-center KS-9000
```

Jede Änderung steht mit altem und neuem Wert in der Prüfspur und gilt
**ab der nächsten Anfrage**. Denn jede Messzeile trägt eine **Kopie** des
Kontexts zum Zeitpunkt der Anfrage — wie ein Beleg die Kontierung behält,
mit der er gebucht wurde. Eine geänderte Kostenstelle verschiebt keine
alten Monate.

### Was gezählt wird: Volumen, nie Geld

Je Anfrage: Aufruf, Token ein/aus (wenn die Quelle sie nennt — sonst
ehrlich `ok_no_counts`, nie geschätzt) und **Bytes hoch/runter** am
Verbraucher: der Anfragerumpf, den er schickt, und der Antwortrumpf, den
er bekommt, im Strom ohne Chunk-Rahmung. Preise, Währungen, Rechnungen
gibt es hier nicht — die entstehen außerhalb aus diesen Zahlen
(Auswertungs-Schnittstelle, unten).

Messzeilen von vor 0.3 bekommen beim ersten Start einmal den Kontext,
den ihr Schlüssel **dann** hat, und tragen `ctx_source=backfilled` —
eine Rekonstruktion, die sagt, dass sie eine ist. Ihre Bytes bleiben
leer (unbekannt), nicht 0.

## Die Ampel

Jede Bezugsquelle trägt eine Farbe. Sie beschreibt nicht Güte, Tempo
oder Preis, sondern **was mit den Daten geschehen kann**:

| Farbe | Bedeutung                                                    | Was gesendet werden darf                                    |
| ----- | ------------------------------------------------------------ | ----------------------------------------------------------- |
| grün  | die Daten verlassen das Unternehmen nicht (eigene Hardware)   | alles Übrige; personenbezogene Daten **mit Freigabe**       |
| gelb  | sie verlassen es vielleicht, aber unter verbindlicher Zusage  | Unternehmensinformationen; personenbezogene **mit Freigabe** |
| rot   | externer Anbieter, Daten können abfließen                     | **keine** Unternehmensinformationen, **keine** personenbezogenen |

Wo ein Rechner steht, ist der **Grund** für eine Farbe, nie ihre
**Bedeutung** — deshalb eine Ampel und keine Landkarte.

**Durchgesetzt wird an Erklärungen, nie am Inhalt.** Das Gateway darf
nicht in die Anfrage sehen und kann personenbezogene Daten deshalb
nicht erkennen. Es prüft, was beim Ausstellen gesagt wurde — die
Obergrenze des Schlüssels und die Freigabe für personenbezogene Daten
— und hält daraus die eine Regel, die es halten kann: **ein für
personenbezogene Daten freigegebener Schlüssel benutzt nie rot.**
Alles Weitere bleibt bei dem, der den Prompt abschickt; die App ist
ehrlich über den Unterschied zwischen dem, was sie durchsetzt, und
dem, was sie dokumentiert.

**Die Farbe eines Alias ist die schlechteste seiner erreichbaren
Ziele.** Ein Alias, der von einem grünen Modell auf einen roten
Anbieter ausweichen kann, ist nicht grün — und `GET /v1/models` meldet
genau die Farbe, die *dieser Schlüssel* wirklich bekäme. Sonst wäre die
Ampel eine Beruhigung statt einer Auskunft.

Noch nicht gebaut (RFC-0023 Stufe 3): Wenn eine Bezugsquelle selbst ein
OAAP-Gateway ist, soll ihre gemeldete Farbe die konfigurierte **nach
oben begrenzen** — eine erklärte Farbe ist eine Obergrenze, nie eine
Untergrenze. Ohne diese Regel wird ein Gateway zur Wäscherei.

## Als Ersatz für LM Studio

```text
Basis-Adresse : https://<instanz>.<knoten>/v1
API-Key       : der ausgestellte Schlüssel
Modell        : chat-default   (die Liste zeigt Aliasse, keine Modellnamen)
```

`GET /v1/models` liefert genau die Aliasse, die dieser Schlüssel
benutzen darf. Deshalb funktioniert jeder OpenAI-kompatible Client
ohne Anpassung — und der Anwender erfährt nie, wer geantwortet hat.

Eigener Verbrauch: `GET /v1/usage` mit demselben Schlüssel. Jeder
sieht seinen eigenen und nur seinen eigenen.

## Die Regel, die alles trägt

**In keinem Protokoll stehen jemals Prompts oder Antworten.** Gezählt
wird, nicht mitgeschrieben: Zeit, Schlüssel, Alias, die *tatsächlich
benutzte* Quelle, Token-Zahlen, Dauer, Ausgang. Es gibt nicht einmal
eine Spalte, in die ein Prompt passen würde — was nicht gespeichert
werden kann, landet auch nicht versehentlich in einer Sicherung.

`test_gateway.py` prüft das, indem es einen unverwechselbaren Satz
durch das Gateway schickt und anschließend **jede Datei** unter dem
Mount danach durchsucht.

## Auswertung und Schnittstelle (seit 0.5, RFC-0064 Stufe 3)

**Grafiken** unter `/auswertung` (Reiter Auswertung), nur für die
Serververwaltung: Verlauf je Tag oder Monat gestapelt nach einer
Dimension, Rangliste, Anteil der Ausgänge (dort wird `ok_no_counts`
sichtbar). Filter nach Zeitraum und jeder Dimension. SVG vom Server,
kein Skript im Browser; unter jeder Grafik dieselben Zahlen als Tabelle.

**Schnittstelle** für ein Abrechnungssystem:

```
GET /report/v1/usage?from=2026-10-01&to=2026-11-01&group_by=cost_center,month
                    [&account=…&project=…&model=…][&format=csv]
GET /report/v1/keys
```

- `from` einschließlich, `to` ausschließlich, UTC (`2026-10`,
  `2026-10-01` oder `2026-10-01T00:00:00Z`).
- Dimensionen zum Gruppieren und Filtern: `account`, `project`,
  `cost_center`, `application`, `owner`, `key` (Etikett), `model`
  (Modellname), `supplier`, `outcome`, `day`, `month`. Ein Filter darf
  mehrfach stehen („einer davon“); leer heißt „ohne Angabe“.
- Je Gruppe: `calls`, `in_tokens`, `out_tokens`,
  `calls_without_token_counts`, `bytes_up`, `bytes_down`,
  `calls_without_bytes`.
- **`complete_until`** in jeder Antwort (bei CSV als Kopfzeile
  `X-OAAP-Complete-Until`): bis wann die Zahlen endgültig sind. Eine
  Messzeile bekommt ihre Zeit am **Ende** der Anfrage; ein Zeitraum ist
  30 s nach seinem Ende abgeschlossen.
- Ein **unbekannter Parameter wird mit 400 abgelehnt** — ein vertippter
  Filter würde sonst still entfallen, und der Bericht zählte alles.

**Zugang:** Grafiken und Schnittstelle lesen über **dieselbe Funktion**
(`report.usage`). Ein externes System fragt mit einem
**Plattform-API-Schlüssel** eines Maschinen-Benutzers mit Rolle `admin`
(Portal → Zugänge, RFC-0027) — nie mit einem Gateway-Schlüssel
`sk-oaap-…`; der öffnet `/report` nicht.

```bash
curl -H "Authorization: Bearer $OAAP_API_KEY"   "https://<gateway>/report/v1/usage?from=2026-10&to=2026-11&group_by=cost_center&format=csv"
```

## Prüfen

```bash
python3 apps/ai-gateway/test_supply.py     # Konfiguration und Reihenfolge
python3 apps/ai-gateway/test_catalog.py    # Tresor, Übernahme, Laden
python3 apps/ai-gateway/test_report.py     # die eine Abfrage: Zeitraum, Kontext-Kopie, CSV
python3 apps/ai-gateway/test_cohort.py     # Kohorten: Tabelle lesen, Kennungen, Ausgabe
python3 apps/ai-gateway/test_gateway.py    # die Konformitätsliste der Spec §9
python3 apps/ai-gateway/test_setup.py      # Einrichtungstür und Freigabe für einen Chat (RFC-0104)
```

Alle laufen ohne Netz, ohne Docker und ohne Modell: Die letzte Datei
stellt eine **Papier-Bezugsquelle** hin, die sich OpenAI-kompatibel
verhält und auf Ansage ausfällt.

## Gemeinsame Bücher mit dem MCP-Gateway (seit 0.5.1, RFC-0065 Stufe 0)

Schlüssel, Kontext, Prüfspur, Rate, Tresor, die eine Abfrage und die
Grafiken stehen in **`gwcore/`** — eine Kopie von
[`oaap-apps/lib/gwcore`](../../lib/gwcore/), die auch das MCP-Gateway
trägt. Die Plattform baut eine App nur aus ihrem eigenen Ordner, deshalb
Kopie statt Verweis. **Geändert wird nur in `lib/`**, danach
`python3 check-shared.py --fix` im Wurzelordner von oaap-apps;
`check-shared.py` ohne `--fix` meldet jede abweichende Kopie.

`store.py`, `vault.py`, `report.py` und `charts.py` sind geblieben, mit
derselben Schnittstelle: Sie sagen nur noch, was dieses Gateway anders
macht (Modellnamen, Ampel, Token). Verhalten unverändert — 0.5.0 und
0.5.1 liefern auf denselben Eingaben dieselben Bytes (Gegenvergleich
2026-11-02), und ein Tresorinhalt von 0.5.0 bleibt lesbar
(`test_catalog.py`).

## Viele Anfragen gleichzeitig (0.5.2)

Der Dienst beantwortet jede Anfrage in einem eigenen Thread, und bis
0.5.1 teilten sich alle Threads eine SQLite-Verbindung ohne Sperre.
Gemessen am 2026-11-02 mit 16 Threads zu je 200 Aufrufen: 15 Threads
brachen ab („bad parameter or other API misuse“), **422 von 3200
Messzeilen kamen an**. Unter Last fehlte also Verbrauch in den Büchern.
Seit 0.5.2 läuft jede Anweisung unter einer Sperre
(`gwcore.ledger.Serialized`, gefunden beim Bau des MCP-Gateways):
0 Abbrüche, 3200 von 3200. `test_report.py` prüft das und wird mit der
alten Verbindung rot.

## Modellnamen eines Schlüssels ändern (0.8.0)

Bis 0.7 ließen sich die Modellnamen eines Schlüssels nur über einen neuen
Schlüssel ändern -- und ein neuer Schlüssel heißt, das Geheimnis beim
Verbraucher zu tauschen (beim Chat: an der Destination). Gefunden in bec am
2026-11-18: ein neu angelegter Name `chat-default` war dem Schlüssel des
Chats nicht erlaubt, der Chat bekam 400 (L26).

Seit 0.8.0 hat jede gültige Schlüsselzeile **„Modellnamen ändern“**: ein
Ankreuzfeld je Modellname, nichts angekreuzt = alle. Die Änderung gilt ab
der nächsten Anfrage, das Geheimnis bleibt, die Prüfspur nennt alt und neu
(`key.aliases`). **Ampel und Freigabe für personenbezogene Daten bleiben**,
wie beim Ausstellen entschieden: sie begrenzen, wohin ein Name führen darf,
und gelten für jeden hinzugefügten. Schlüssel einer Kohorte ändern sich nur
gemeinsam (nicht hier). Auf der Kommandozeile:
`app.py key aliases <etikett> a,b` (leer = alle).

## Unerreichbare Modellnamen und das Anthropic-Häkchen (0.8.1)

- **L14:** Nennt ein Schlüssel Modellnamen, deren Ziele alle über seiner
  Obergrenze liegen (oder bei Freigabe für personenbezogene Daten nur rote),
  wird er weiter ausgestellt, aber die Seite warnt: „erreicht diese
  Modellnamen nie“, mit Grund. Dasselbe beim Ändern der Modellnamen, und
  die Schlüsselliste markiert ihn („nie erreichbar“).
- **L15:** Bei Anthropic ist „temperature/top_p nicht weitergeben“ gesperrt
  und immer an. Ein gesperrtes Feld schickt der Browser nicht mit; das
  Speichern setzte den gespeicherten Wert deshalb auf aus. Jetzt wird für
  Anthropic immer „an“ gespeichert, wie die Seite es zeigt.

## Die Ampel des Gesprächs als Decke je Anfrage (0.9.0)

RFC-0101 Stufe 1. Ein Client darf mit `X-OAAP-Light: green|yellow|red`
eine **zusätzliche Decke für diese eine Anfrage** verlangen. Der Chat
schickt so die Ampel, die die Person für ihr Gespräch gewählt hat
(„sensible Daten“ = grün). Die Decke der Anfrage ist die strengere aus
Schlüssel und Kopfzeile: `green` auf einem gelben Schlüssel heißt grün,
`red` auf einem gelben Schlüssel bleibt gelb. Die Kopfzeile kann also
nur mehr ablehnen, nie mehr erlauben.

- Sperrt nur die Ampel des Gesprächs, antwortet das Gateway mit 503 und
  dem Code `conversation_light`. Der Satz nennt beide Farben („Das
  Gespräch steht auf grün, „chat-default“ führt nur über gelb“).
- `/v1/models` bietet mit der Kopfzeile nur die Namen an, die unter
  dieser Decke erreichbar sind.
- Ein unbekannter Wert wird mit 400 `bad_light` abgelehnt, nicht stumm
  übergangen.
- Die Messzeile hat die neue Spalte `light`: die verlangte Farbe, leer
  ohne Kopfzeile. Das ist ein Wort aus einer festen Liste, kein Inhalt.

## Einrichtungstür und Freigabe für einen Chat (RFC-0104 Stufe 2)

Seit 0.10.0 (Manifest 0.18) lässt sich das Gateway beim Aufbau eines
Mandanten aus einem Profil (`ki-arbeitsplatz`) einrichten, und ein Chat
desselben Mandanten erreicht es ohne API-Schlüssel.

**Die Einrichtungstür** `setup: {path: /oaap/setup}` (Modul
`setupdoor.py`). Der Knoten ruft sie im Schritt `app.setup` über seinen
internen Endpunkt, mit `Authorization: Bearer <OAAP_SETUP_KEY>` — ein
Wert, den nur die Plattform hält — und `X-OAAP-Setup-Actor: <wer den
Aufbau gestartet hat>`. Von außen liegt der Pfad unter `/` und damit
hinter `admin`; auch dort ist die Tür ohne den Schlüssel zu, jede
Identitäts-Kopfzeile wird übergangen. Fehlt `OAAP_SETUP_KEY` in der
Umgebung, ist jeder Aufruf `403`.

| Art | Eintrag | `ready` |
| --- | --- | --- |
| `supplier` | `{"id": <name>, "url": "https://…/v1", "light"}` — **ohne** Zugangsdaten | Zugangsdaten gespeichert **und** der Verbindungstest der Seite antwortet jetzt mit 2xx |
| `alias` | `{"id": <modellname>, "supplier", "model"}` — genau ein Ziel | der Modellname ist routbar |
| `client` | `{"id": <chat-instanz>, "aliases": [...], "ceiling", "personal_data", "tenant"}` — die Freigabe; `tenant` (seit 0.10.1, optional) ist ihr Kontext „Mandant“ und steht damit in jeder Messzeile des Chats, ohne ihn `default` | nicht widerrufen, nicht abgelaufen |

- `GET` nennt die Arten, `GET ?kind=&id=` den Zustand (`present`/`absent`,
  `ready`, `note`). `POST {"v": 1, "dry": …, "items": [...]}` legt an;
  Antworten je Eintrag: `present`, `made`, `would_make`, `conflict`,
  `refused`. `POST {"v": 1, "remove": [...]}`: `removed`, `gone` oder
  `conflict`.
- **Nie überschreiben:** Gibt es unter der Kennung schon etwas, das
  anders ist, heißt das `conflict` und es bleibt. Ein gleicher Eintrag
  von Hand ist `present`, wird aber nicht übernommen.
- **Keine Geheimnisse:** Felder namens `key`, `token`, `secret`,
  `password`, `credentials`, `credential`, `api_key` → `refused`. Die
  Zugangsdaten einer Quelle trägt ein Mensch unter Bezugsquellen ein;
  das ist kein Konflikt.
- **Gemerkt** wird je angelegtem Eintrag Auslöser, Zeit und Digest
  (Tabelle `setup_items`), in der Prüfspur steht `setup:<auslöser>`.
  `remove` entfernt nur, was die Einrichtung angelegt hat und was noch so
  ist. Eine Quelle mit Zugangsdaten, ein Modellname, auf den ein gültiger
  Schlüssel beschränkt ist, und alles von Hand Geänderte bleiben
  (`conflict`). Eine Freigabe, die schon Verbrauch hat, wird widerrufen
  statt gelöscht, damit die Messzeilen zuzuordnen bleiben.

**Die Freigabe für einen Chat** (`ai_gateway: {serve: true}`). Der Chat
ruft über die via-Tür der Plattform; die Tür prüft seinen Client-Schlüssel
und die Zusicherung der Person und setzt `X-OAAP-AI-Client`,
`X-OAAP-User-Id` und `X-OAAP-User-Assertion`. Nur alle drei zusammen
zählen (wie Verfahren A beim MCP-Gateway); dann berechtigt die Zeile der
Art `client` in `keys`, deren Etikett der Name des Chats ist.

- Modellnamen, Obergrenze, pbD-Freigabe, Budget, Rate, `X-OAAP-Light` und
  Messzeile laufen über denselben Code wie bei einem Schlüssel.
- Eine Freigabe hat **keinen Schlüsselwert**: ihr Digest ist
  `client:<etikett>` statt eines SHA-256, und `store.find` gibt eine Zeile
  der Art `client` nie zurück — auch nicht für einen leeren Wert.
- `X-OAAP-AI-Client` allein (oder zwei von drei) führt auf den
  gewöhnlichen Weg über den API-Schlüssel: ohne Schlüssel `403`. Ein Chat
  ohne Freigabe bekommt dieselbe `403` wie ein unbekannter Schlüssel.
- Gezählt wird die Freigabe (`key_id` in `usage`), **nie die Person**
  (RFC-0104 §9 O1), obwohl die Tür sie mitliefert.
- Im Reiter **Schlüssel** stehen Freigaben mit dem Zeichen „Freigabe für
  Chat“: widerrufen, Modellnamen und Kontext ändern wie bei einem
  Schlüssel; „neu ausstellen“ gibt es für sie nicht.

Prüfen: `python3 apps/ai-gateway/test_setup.py` (Köder 2, 3, 5, 6, 7 aus
RFC-0104 §8).

## Eigenschaften der Modelle und Vorlagen für Clients (0.11.0, RFC-0106)

Seit 0.11.1 füllt `gwcore.fill` die Vorlagen -- dieselbe Maschine wie im MCP-Gateway (RFC-0107 D2). Sichtbar ändert sich nichts.

**Eigenschaften.** Je Modellname vier Angaben: Werkzeuge (`tools`), Bilder
(`vision`), Kontextfenster (`context`, Ein- und Ausgabe zusammen) und
größte Ausgabe (`output`). Gespeichert wird je Ziel (Quelle + Modell der
Quelle); ein Modellname mit mehreren Zielen bekommt je Angabe das
**schwächste** — wie bei der Ampel. Jede Angabe ist gesetzt oder
unbekannt; geraten wird nichts. Woher ein Wert kommt, steht daneben:

- **von der Quelle** — beim Verbindungstest oder beim Abruf der
  Modellliste, nur was die Quelle ausdrücklich sagt (`context_length`,
  `context_window`, `max_completion_tokens`, eine Fähigkeitenliste mit
  `tools`/`vision`, Eingabe-Arten mit `image` …). Fehlt ein Wort in einer
  Liste, heißt das nicht „nein“.
- **aus dem Test** — Knopf „Werkzeugaufruf probieren“ im Reiter Modelle:
  je Ziel eine kleine Anfrage mit dem harmlosen Werkzeug `now`
  (höchstens 60 Token). Nur auf Klick; nicht im Verbrauch dieses
  Gateways, die Quelle zählt die paar Token.
- **vom Betreiber** — unter „Eigenschaften“ je Ziel. Ein Betreiberwert
  wird nie überschrieben; meldet Quelle oder Test später etwas anderes,
  steht es daneben mit „übernehmen“. Ein geleertes Feld heißt
  unbekannt — meldet die Quelle etwas, gilt dann wieder ihr Wert.

`GET /v1/models` trägt die gesetzten Angaben als `oaap_tools`,
`oaap_vision`, `oaap_context`, `oaap_output` neben der Ampel; die
Asset-Tür `/oaap/assets` ebenso (ohne Präfix). Die Einrichtungstür nimmt
sie beim Eintrag `alias` an (`"context": 128000`, `"tools": true`), als
Angabe des Betreibers; einen anderen Betreiberwert überschreibt sie nicht.

**Vorlagen.** Reiter „Vorlagen“: Werkzeug wählen (VS Code, opencode),
Modellnamen aus einem Schlüssel (genau die Liste, die er unter
`/v1/models` bekommt) oder von Hand, Datei ansehen oder herunterladen.
Unbekannte Angaben fallen weg — das Werkzeug nimmt seinen eigenen
Vorgabewert, und die Seite nennt sie. Der Schlüssel steht nie in der
Datei: VS Code fragt nach `${input:oaapAiKey}`, opencode liest
`OAAP_AI_KEY` aus der Umgebung. Wer einen Schlüssel hat, holt sich seine
Datei selbst:

```
curl -H "Authorization: Bearer $OAAP_AI_KEY" \
  "https://<gateway>/v1/oaap/client-config?target=vscode" -o chatLanguageModels.json
```

Eine Vorlage ist Daten (`templates/<werkzeug>.json`) mit der Fassung des
Formats und dem Tag, an dem sie geprüft wurde. Formate von Werkzeugen
ändern sich; die eigentliche Probe ist, die Datei einmal im echten
Werkzeug zu laden.

## Was noch fehlt

- **RFC-0064 Stufe 4:** Verdichtung und Aufbewahrung der Messzeilen.
- Verkettung ist möglich (ein Gateway als Bezugsquelle eines anderen),
  aber noch nicht im Betrieb bewiesen — das ist Stufe 3 in RFC-0023.
- `tenant` steht auf `default`; gefüllt wird er mit RFC-0022, nicht von
  Hand.
- Kein Kostenmodell — mit Absicht (RFC-0064): Das Gateway zählt
  Volumen, Preise gehören zum Vertrag mit der Bezugsquelle.
