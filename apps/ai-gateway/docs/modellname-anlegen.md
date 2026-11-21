# Einen Modellnamen anlegen

Verbraucher fragen nie nach dem Modell eines Anbieters, sondern nach einem **Modellnamen**
des Gateways, zum Beispiel `chat-default`. So kann die Quelle wechseln, ohne dass ein Programm
geändert werden muss.

## Aus einer Bezugsquelle übernehmen (am einfachsten)

1. Öffne den Reiter **Modelle**.
2. Wähle unter **Modellnamen aus einer Bezugsquelle anlegen** die Quelle und klicke auf
   **Modelle abrufen**.
3. Hake die gewünschten Modelle an, ändere bei Bedarf den vorgeschlagenen Namen und klicke auf
   **Angehakte anlegen**.

## Von Hand

1. Trage unter **Modellname anlegen** den Namen ein.
2. Trage die **Ziele** ein: `quelle:modell`, mehrere mit Komma getrennt. Fällt das erste aus,
   weicht das Gateway auf das nächste aus – nur innerhalb dieser Liste.
3. Hake **wie aufgeführt** an, wenn die Reihenfolge gelten soll; sonst gilt grün vor gelb vor
   rot.

## Testen, Eigenschaften, ändern

Klicke in der Liste auf den Modellnamen. Auf seiner Seite kannst du ihn **testen** (eine
Mini-Anfrage je Ziel), einen **Werkzeugaufruf probieren**, die **Eigenschaften** setzen
(Werkzeuge, Bilder, Kontext, Ausgabe), die Ziele ändern oder ihn löschen.

Die Ampel eines Modellnamens ist immer die **schlechteste** seiner Ziele.
