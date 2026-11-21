# Echtzeit-Medien (LiveKit) – Überblick

Dieser Dienst überträgt **Audio und Video in Echtzeit**, etwa für
Besprechungen. Dahinter steckt LiveKit, ein Medienserver. Die Menschen im
Mandanten sehen ihn nie direkt: Sie benutzen eine Besprechungs-App, und
die verbindet sich mit diesem Dienst.

Diese Seiten sind für die **Verwaltung** und den **Betrieb**.

## Wie die Plattform ihn nutzt

- **Alle verbinden sich zum Server**, nicht untereinander. Das löst die
  üblichen Probleme mit Routern und Firewalls und hält die Last im Rahmen.
- **Zwei Container**: der Medienserver und ein kleiner Zustandsspeicher.
  Der Speicher ist von außen und für andere Apps nicht erreichbar.
- **Steuerung über das Gateway.** Verbindungsaufbau und Steuerkanal laufen
  verschlüsselt über das Gateway der Plattform. Der Dienst prüft jede
  Anfrage selbst anhand eines signierten Zugangs, den die Besprechungs-App
  ausstellt.
- **Medien über einen festen Port**, am Gateway vorbei, für UDP und TCP auf
  derselben Nummer. Der Port ist fest, weil der Server ihn selbst bei den
  Teilnehmern bekannt gibt.
- **Schlüssel und Geheimnis** (`LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`)
  teilt sich der Dienst mit der Besprechungs-App. Damit signiert sie die
  Zugänge der Teilnehmer.

## Typische Aufgaben

- [Einrichten und freigeben](einrichten.md)
- [Häufige Fragen](faq.md)

## Bewusste Grenzen

- Ein Knoten, ein fester Medienport: Eine zweite Instanz auf demselben
  Knoten scheitert beim Freigeben des Ports – laut und mit Absicht.
- Mehrere Knoten im Verbund und Aufzeichnung auf dem Server sind nicht
  enthalten.
