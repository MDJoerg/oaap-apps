# Einrichten

Voraussetzung ist eine **Zentrale** der Flotte, die in einen Mandanten schreibt
(an der Zentrale: `sudo oaap fleet hub twin <mandant>`, meist `oaap-ops`).

1. Installieren Sie die App **Flotte** auf der Zentrale **in genau diesen
   Mandanten**. Die Installation verbindet die App mit dem Zwilling und stellt
   ihr dabei einen eigenen Zugang aus. Diesen Zugang zeigt die App nirgends an.
2. Fertig. Die einzige Einstellung ist der **Lesetakt** in Sekunden (Standard 60,
   erlaubt 30–3600).

## Gut zu wissen

- Auf einem Server, der keine Zentrale ist, verweigert die Installation die
  Verbindung zum Zwilling.
- In einem anderen Mandanten der Zentrale gelingt die Installation, aber die
  Liste bleibt leer: Jeder Mandant liest nur seinen eigenen Zwilling. Ein
  Kundenmandant sieht so nie die Flotte.
- Alarmiert wird von der Zentrale (Verteiler und Benachrichtigungen), nicht von
  dieser App.
