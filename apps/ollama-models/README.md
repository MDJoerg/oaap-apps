# OAAP Ollama-Modelle

Die Betriebsoberfläche zu einem Dienst, der keine hat. Ollama ist im
OAAP-KI-Stack ein tragendes Teil geworden — aber Modelle holt man auf
der Kommandozeile, und was auf der Platte liegt, sieht man gar nicht.

Diese App schließt **genau diese Lücke**, nicht mehr.

| tut                                                          | tut nicht                                          |
| ------------------------------------------------------------ | -------------------------------------------------- |
| zeigen, welche Modelle liegen — Größe, Quantisierung, Alter   | chatten (dafür gibt es Open WebUI im Store)        |
| zeigen, was **gerade im Speicher liegt**                      | Modelle im KI-Gateway verwalten (dessen Nicht-Ziel) |
| Modelle holen (mit Fortschritt) und löschen                   | Ollamas API veröffentlichen                        |
| die **Zeilen für den Anschluss ans KI-Gateway** liefern       | freien Plattenplatz erfinden, den sie nicht sieht  |

## Warum eine eigene App und nicht Teil des Gateways

Weil `oaap.ai.gateway` „kein Modell-Management über Aliasse hinaus" als
ausdrückliches Nicht-Ziel führt und RFC-0023 A5 festhält: Eine
Bezugsquelle ist ein OpenAI-kompatibler Endpunkt plus Zugangsdaten,
einen Adapter-Baukasten gibt es bewusst nicht. `/api/pull` ist
Ollama-spezifisch und wäre genau der erste Adapter gewesen.

Und nicht als zweiter Dienst im Store-Paket, weil `oaap-store` ein
Katalog **fremder** Software ist. Unser Code gehört nach `oaap-apps`;
das hält die Ollama-Verpackung dünn und aktualisierbar.

## Verbindung statt Veröffentlichung

Ollamas API kennt **keinerlei Authentifizierung** — wer sie erreicht,
kann Modelle holen, löschen und die Hardware belegen. Deshalb wird sie
nicht geroutet, sondern über eine **App-zu-App-Verbindung** erreicht
(RFC-0016): Standard ist Isolation, die Verbindung ist eine
ausdrückliche Entscheidung des Betreibers und jederzeit widerrufbar.

```bash
sudo oaap app link add <diese-instanz> <ollama-instanz>
```

Danach löst der Containername auf dem gemeinsamen Netz des Paares auf;
voreingestellt ist `http://oaap-app-ollama:11434`. Findet die App ihr
Ollama nicht, zeigt sie **genau diesen Befehl** — der fehlende Link ist
der wahrscheinlichste Grund, und eine Fehlermeldung, die die Lösung
kennt, sollte sie auch nennen.

## Rollen

Die Route lässt `keyuser` und `admin` herein; **holen und löschen darf
nur `admin`**. Ein Modell zu holen belegt Platz und Bandbreite auf dem
Knoten — das ist Serververwaltung. Die Trennung macht die App selbst
anhand der geprüften Rollen-Kopfzeile, und die Seite sagt einem
Schlüsselanwender auch, warum er die Knöpfe nicht sieht.

Jedes Holen und Löschen steht mit Urheber und Zeit im Protokoll unter
dem deklarierten Mount.

## Was die App bewusst nicht behauptet

- **Freier Plattenplatz.** Sie schaut in einen fremden Container
  hinein, nicht auf dessen Platte. Sie zeigt die Summe der Modelle und
  sagt, dass der Rest beim Knoten liegt — statt eine Zahl zu erfinden.
- **Geschwindigkeit.** Auf einem Knoten ohne Grafikkarte entscheidet
  die Modellgröße darüber, ob ein Modell benutzbar ist oder nur
  vorhanden. Die Startpunkte sind entsprechend eingeordnet, statt vier
  Namen ohne Warnung hinzustellen.

## Fortschritt ohne JavaScript

Ein Holen läuft im Hintergrund weiter, auch wenn man wegnavigiert —
geholt wird auf dem Knoten, nicht im Browser. Solange etwas läuft,
aktualisiert die Seite sich per `meta refresh` selbst; ein
Fortschrittsbalken ist kein Grund, eine Oberfläche von Skripten
abhängig zu machen.

## Prüfen

```bash
python3 apps/ollama-models/test_models.py
```

42 Prüfungen gegen ein **Papier-Ollama** — ohne Netz, ohne Docker, ohne
Modell. Abgedeckt: Rollentrennung, das nicht erreichbare Ollama als
Zustand *mit Anleitung*, Fortschritt und Fehler beim Holen, Löschen,
Protokoll mit Urheber, und dass die Gateway-Zeilen auf der Seite
stehen.
