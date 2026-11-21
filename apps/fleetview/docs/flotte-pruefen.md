# Wie steht die Flotte?

1. Öffnen Sie die **Übersicht**.
2. Oben steht die Karte **Braucht einen Menschen**, wenn etwas zu tun ist: ein
   stummer Knoten, ein ausgefallener Kerndienst, ein Name, der woanders hinzeigt,
   ein fälliger Neustart, offene Sicherheitsmeldungen.
3. Darunter die Liste der **Knoten** mit Zustand, Plattformversion und der Zahl
   der gesunden Instanzen.

## Die Zustände

| Zustand | Bedeutung |
| --- | --- |
| Gesund | Der Knoten lebt, Kerndienste und Instanzen sind in Ordnung. Standby ist kein Fehler. |
| Auffällig | Etwas verdient einen Blick, z. B. eine Instanz mit Warnung. |
| Gestört | Ein Kerndienst oder eine Instanz ist ausgefallen. |
| Stumm | Die Zentrale hört nichts mehr von dem Knoten. |
| Unbekannt | Das Lebenszeichen ist unklar, z. B. weil die Zentrale gerade startet. |
| Entfernt | Der Knoten wurde an der Zentrale entfernt; er steht am Ende der Liste. |

Das Lebenszeichen (das Urteil der Zentrale) geht immer vor dem, was der Knoten
zuletzt über sich selbst gesagt hat.

## Filtern und Suchen

- Das Suchfeld findet Knoten nach Namen oder Version.
- Die Auswahl **Zustand** zeigt z. B. nur stumme Knoten.
- Ein Klick auf eine Spaltenüberschrift sortiert.

Die Adresse enthält den Filter – Sie können eine gefilterte Liste als Lesezeichen
speichern oder weitergeben.

**Jetzt aktualisieren** liest den Zwilling sofort, statt auf den nächsten Takt zu
warten.
