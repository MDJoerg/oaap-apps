# Anmelden und Rollen

## Zwei Türen, eine Anmeldung

1. **Die Plattform** lässt nur Menschen des Mandanten mit der Rolle
   `user`, `keyuser` oder `admin` zum Wiki durch. Partner und Gäste nicht.
2. **Das Wiki** meldet Dich danach selbst beim Anmeldedienst des Mandanten
   an. Weil Du dort schon angemeldet bist, siehst Du keine zweite
   Anmeldeseite.

Beim ersten Mal legt das Wiki Dein Konto an. Gebunden wird an Deine feste
Kennung beim Anmeldedienst, nicht an E-Mail oder Namen.

## Gruppen werden Rollen

Im Anmeldedienst gehörst Du zu Gruppen, z. B. `/wiki-redaktion`. Im Wiki
gibt es Rollen. Eine Rolle gilt für Dich, wenn ihre **„Externe
Authentifizierungs-ID“** genau Dein Gruppenpfad ist.

| Gruppe im Anmeldedienst | Rolle im Wiki | Darf (Beispiel) |
|---|---|---|
| `/wiki-redaktion` | Redaktion | Bücher und Seiten lesen und schreiben |
| `/wiki-vorstand` | Vorstand | lesen, dazu das Buch „Vorstand“ |
| `/wiki-admins` | Admin | alles, auch die Einstellungen |
| keine Wiki-Gruppe | keine | nichts – das Wiki bleibt leer |

Was eine Rolle darf, stellt die Verwaltung im Wiki ein. Ein Buch nur
bestimmten Rollen zu zeigen, geht am Buch selbst: „Berechtigungen“.

## Wann eine Änderung ankommt

Gruppen gleicht das Wiki **bei jeder Anmeldung** ab, Hinzufügen wie
Wegnehmen. Kommst Du in eine neue Gruppe, gilt die Rolle ab Deiner
nächsten Anmeldung am Wiki. Nimmt die Verwaltung Dir eine Gruppe weg, gilt
das ebenfalls erst ab der nächsten Anmeldung. Wer sofort aussperren muss,
entzieht im Wiki zusätzlich die Rolle am Konto.
