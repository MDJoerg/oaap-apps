# Häufige Fragen

## Ich sehe ein Asset nicht, das es geben soll.

Dann ist es für dich verborgen – der Betreiber zeigt es nur Angemeldeten oder
nur Berechtigten. Melde dich an; hilft das nicht, frag den Betreiber.

## Ich sehe das Asset, kann es aber nicht laden.

Es ist für dich gesperrt. Frag auf der Detailseite eine Freischaltung an.

## Was bedeuten die Zeichen auf den Karten?

- **frei** – jeder, der es sieht, darf es laden.
- **berechtigt** – du darfst es laden, weil du berechtigt bist.
- **freischalten** – gesperrt; du kannst anfragen.
- **beta** – es gibt nur ein Release im Kanal `beta`.
- **in Prüfung** – ein Release wartet auf die Freigabe (nur für Betreiber und
  Anbieter sichtbar).

## Was ist der Unterschied zwischen stable und beta?

Zwei Kanäle, die auf je ein Release zeigen. `stable` ist die erprobte Version,
`beta` die nächste. Welche Kanäle es gibt, legt der Betreiber fest.

## Ein Release ist zurückgerufen. Was heißt das?

Der Anbieter hat es zurückgezogen, meist mit Begründung. Es lässt sich nicht
mehr laden; die Versionsnummer wird nie wieder vergeben.

## Wie kommt eine App aus dem Marktplatz auf meinen Knoten?

Der Paketkatalog des Knotens folgt dem Feed des Marktplatzes. Der Betreiber
legt dafür einen Maschinenbenutzer mit API-Schlüssel an und berechtigt ihn
für die Assets und Kanäle; im Paketkatalog wird der Marktplatz als Quelle
eingetragen.

## Kann ein Assistent im Chat den Marktplatz durchsuchen?

Ja. Der Marktplatz ist eine Quelle für Assistenten: sie suchen und lesen
Assets – genau das, was du selbst im Katalog sehen darfst. Betreiber und
Anbieter können sich offene Anfragen zusammenfassen lassen. Entschieden wird
weiterhin auf der Seite.

## Darf ich Passwörter in eine Aufbau-Vorlage schreiben?

Nein. Die Sperre lehnt wörtliche Passwörter, Tokens und Schlüssel ab. Schreib
stattdessen `generate: true` oder einen Platzhalter.
