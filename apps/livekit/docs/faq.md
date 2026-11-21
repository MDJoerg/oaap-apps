# Echtzeit-Medien (LiveKit) – häufige Fragen

## Teilnehmer verbinden sich, sehen und hören sich aber nicht.

Fast immer erreicht der Medienstrom den Server nicht. Prüfe:

- Ist der Medienport der Instanz freigegeben (Portal, „Direkter Port“)?
- Leitet der Router **UDP und TCP** auf diesen Port zum Knoten weiter?
- Meldet die Gesundheitsseite den Port als erreichbar?

## Die Besprechungs-App meldet einen ungültigen Zugang.

Kennung oder Geheimnis passen nicht zusammen. In der Besprechungs-App
müssen genau `LIVEKIT_API_KEY` und `LIVEKIT_API_SECRET` dieser Instanz
stehen. Nach einer Änderung beide Apps neu starten.

## Kann ich zwei Instanzen auf einem Knoten betreiben?

Nein. Der Medienport ist fest; die zweite Freigabe scheitert mit einer
klaren Meldung.

## Warum ist die Steuerung „öffentlich“?

Der Dienst prüft jede Anfrage selbst anhand eines signierten Zugangs. Eine
zusätzliche Anmeldung am Gateway wäre hier die falsche Schranke; die
Mengenbremse des Gateways gilt trotzdem.

## Wird etwas aufgezeichnet?

Nein. Eine Aufzeichnung auf dem Server ist nicht enthalten.
