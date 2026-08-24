# OAAP KI-Gateway

Eine Bezugsquelle für alle KI-Verbräuche: **ein OpenAI-kompatibler
Endpunkt**, der sich bei lokalen Modellen, externen Anbietern,
Kunden-Endpunkten oder einem anderen Gateway bedient. Der Verbraucher
fragt nach einem **Zweck** (`chat-default`, `code`,
`embedding-default`) und nie nach einem Hersteller.

Umsetzung der Capability [`oaap.ai.gateway`
0.1](../../../oaap-spec/spec/oaap.ai.gateway.md) (RFC-0023).

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

## Konfiguration

Alles über deklarierte Variablen (Portal → Instanz → Konfiguration).

```text
AIGW_SUPPLIERS          eine Zeile je Bezugsquelle
  ollama=http://ollama:11434/v1 class=internal
  tsystems=https://<ai-business-hub>/v1 class=eu
  groq=https://api.groq.com/openai/v1 class=external

AIGW_SUPPLIER_KEYS      geheim — eine Zeile je Quelle, die eine braucht
  groq=<schlüssel>
  tsystems=<schlüssel>

AIGW_ALIASES            eine Zeile je Zweck
  chat-default = tsystems:<modell>, groq:<modell>
  embedding-default = ollama:nomic-embed-text
  code = tsystems:<modell> order=listed
```

**Ohne Klassenangabe gilt `external`** — unbekannte Herkunft ist nicht
souverän, und der sichere Zustand ist der Standardzustand.

**Ausgewichen wird nur innerhalb der Gruppe einer Zeile**, und dabei
gilt `internal` vor `eu` vor `external`. Wer die aufgeführte
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
  key issue laptop@joerg --classes internal,eu --aliases chat-default
sudo docker exec oaap-app-<instanz> python3 /srv/app.py key list
sudo docker exec oaap-app-<instanz> python3 /srv/app.py key revoke laptop@joerg
```

Der Wert erscheint **genau einmal**; gespeichert wird nur sein
SHA-256. Wer ihn verliert, bekommt einen neuen — nachlesen lässt er
sich nirgends (dieselbe Hygiene wie beim Deploy-Token, RFC-0019).

Voreingestellt darf ein Schlüssel `internal` und `eu`. **`external`
ist eine bewusste Zusatzerlaubnis** — Souveränität soll das sein, was
passiert, wenn niemand etwas einstellt.

Dazu je Schlüssel: Etikett, Verantwortliche(r), Kostenstelle, Projekt,
Aliass-Beschränkung, **Token-Budget** und **Anfragen je Minute**. Die
beiden letzten sind harte Grenzen — sie schützen vor einer
davonlaufenden Rechnung, was eine Rechnung hinterher nicht tut.

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
python3 apps/ai-gateway/test_gateway.py    # die Konformitätsliste der Spec §9
```

Beide laufen ohne Netz, ohne Docker und ohne Modell: Die zweite Datei
stellt eine **Papier-Bezugsquelle** hin, die sich OpenAI-kompatibel
verhält und auf Ansage ausfällt.

## Was noch fehlt (Stufe 1)

- Verkettung ist möglich (ein Gateway als Bezugsquelle eines anderen),
  aber noch nicht im Betrieb bewiesen — das ist Stufe 3 in RFC-0023.
- Mandanten-Sicht: Schlüssel tragen heute `account=default` und
  `tenant=default`. Die Felder sind da, gefüllt werden sie mit
  RFC-0022 Stufe 2.
- Kein Kostenmodell in Euro, nur Token. Preise gehören zum Vertrag mit
  der Bezugsquelle, nicht in die Plattform.
