# OAAP KI-Gateway

Eine Bezugsquelle für alle KI-Verbräuche: **ein OpenAI-kompatibler
Endpunkt**, der sich bei lokalen Modellen, externen Anbietern,
Kunden-Endpunkten oder einem anderen Gateway bedient. Der Verbraucher
fragt nach einem **Zweck** (`chat-default`, `code`,
`embedding-default`) und nie nach einem Hersteller.

Umsetzung der Capability [`oaap.ai.gateway`
0.3.1](../../../oaap-spec/spec/oaap.ai.gateway.md) (RFC-0023, RFC-0064).

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
- **`/` ist `admin`.** Die Betreiber-Sicht: Aliasse, Bezugsquellen,
  Schlüssel ausstellen und widerrufen, Verbrauch.

Beim Installieren fragt die Plattform einmal nach, weil `/v1` ohne
Login erreichbar wird. Das ist richtig so — und der Grund steht oben.

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
(Auswertungs-Schnittstelle: RFC-0064 Stufe 3).

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

## Prüfen

```bash
python3 apps/ai-gateway/test_supply.py     # Konfiguration und Reihenfolge
python3 apps/ai-gateway/test_catalog.py    # Tresor, Übernahme, Laden
python3 apps/ai-gateway/test_gateway.py    # die Konformitätsliste der Spec §9
```

Alle drei laufen ohne Netz, ohne Docker und ohne Modell: Die letzte Datei
stellt eine **Papier-Bezugsquelle** hin, die sich OpenAI-kompatibel
verhält und auf Ansage ausfällt.

## Was noch fehlt

- **RFC-0064 Stufe 3:** Grafiken auf der Betreiber-Seite und die
  Auswertungs-Schnittstelle `GET /report/v1/usage` für externe
  Rechnungen und Kostenberichte — beide über dieselbe Abfrage.
- **RFC-0064 Stufe 4:** Verdichtung und Aufbewahrung der Messzeilen.
- Verkettung ist möglich (ein Gateway als Bezugsquelle eines anderen),
  aber noch nicht im Betrieb bewiesen — das ist Stufe 3 in RFC-0023.
- `tenant` steht auf `default`; gefüllt wird er mit RFC-0022, nicht von
  Hand.
- Kein Kostenmodell — mit Absicht (RFC-0064): Das Gateway zählt
  Volumen, Preise gehören zum Vertrag mit der Bezugsquelle.
