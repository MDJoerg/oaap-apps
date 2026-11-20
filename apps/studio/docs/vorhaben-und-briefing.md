# Vorhaben anlegen und Briefing erzeugen

Ein **Vorhaben** ist eine App, die entstehen soll – mit genau den Angaben,
aus denen ein Briefing für die KI wird.

## Vorhaben anlegen

1. Im Studio auf **Vorhaben** gehen und **Vorhaben anlegen** wählen.
2. Die Felder ausfüllen. Wichtig für das Briefing sind vor allem:
   - **Name des Vorhabens**
   - **Zielanwender** – wer die App benutzt
   - **Was soll die App erreichen?** – das Ziel in Deinen Worten
   - **Was muss die erste Version können?** – der Umfang, bewusst klein
   - **App-Typ** – `native` (Quellcode, Bau auf der Plattform), `image`
     (fertiges Container-Image) oder `wrapped` (fremde Anwendung, von OAAP
     umschlossen)
   - **Weg auf die Test-Instanz** – Git oder Paket (ZIP), siehe unten
3. Speichern. Das Vorhaben steht jetzt in der Liste.

Felder wie **Repository**, **Zielknoten**, **Test-Instanz**,
**Produktiv-Instanz** und **Deploy-Hook** können später nachgetragen
werden, sobald es sie gibt.

## Status

Der Status zeigt, wo ein Vorhaben steht. Er wird von Hand gepflegt:

| Status | Bedeutung |
|---|---|
| Idee | erst einmal notiert |
| Briefing erstellt | das Briefing ist erzeugt und bei der KI |
| In Entwicklung | die KI baut |
| Im Test | es gibt einen Stand auf der Test-Instanz |
| Produktiv | die App ist produktiv gesetzt |
| Ruht | liegt auf Eis |

## Briefing erzeugen

Auf der Seite des Vorhabens **Briefing erzeugen** wählen. Das Studio baut
ein vollständiges Markdown-Dokument:

- den **fachlichen Auftrag** aus Deinen Angaben,
- alle **verbindlichen Plattformregeln**: Verweis auf den App Deployment
  Contract, Manifest, Anmeldung über das Gateway, Speicher, Oberfläche,
  Benutzer und fachliche Rechte, Zugänge ohne Browser-Anmeldung, Deploy-Hook,
- den **Zielknoten** und die Seiten beider Instanzen, sobald sie bekannt
  sind.

Mit **Als Datei herunterladen** bekommst Du `<vorhaben>-briefing.md`. Leg
die Datei ins Projektverzeichnis und starte die KI damit.

Das Briefing enthält **nichts Privilegiertes** – keinen Deploy-Token, keine
Anlege-Erlaubnis. Es darf weitergegeben werden.

## Zwei Wege auf die Test-Instanz

- **Git** – der Knoten holt sich den Stand selbst aus dem Repository.
  Richtig, wenn das Repository für den Knoten erreichbar ist und der Zugang
  Dir gehört.
- **Paket (ZIP)** – die KI liefert ein fertiges Paket ab, Du lädst es im
  Studio hoch (oder die KI rollt es selbst über den Deploy-Hook aus).
  Richtig bei einem **privaten Repository**: Die Plattform bekommt dann ein
  Paket statt eines fremden Zugangsrechts und muss nichts Fremdes aufheben.
  Geht auch ganz ohne Internet.

Wie das Ausrollen geht: [Paket prüfen und ausrollen](paket-ausrollen.md).
