# Eine Sicht anlegen und Werkzeuge freigeben

Für die Verwaltung (Rolle `admin`).

## Sicht anlegen

1. Reiter **Sichten** → **+ Sicht anlegen**.
2. **Name**: wird Teil der Adresse (`/mcp/<name>`).
3. **Höchste Wirkungsklasse**: lesen, schreiben oder ausführen. Ein
   Werkzeug mit höherer Klasse ist in dieser Sicht nicht aufrufbar.
4. **Zugänge**: wer hinein darf – mit Schlüssel, mit Anmeldung am
   Anmeldedienst, über die Plattform (Personen aus einem Chat).

## Werkzeuge zuordnen

Auf der Seite der Sicht steht unter **Zuordnen** ein Baum aller Kataloge:
Server → Art → Eintrag.

- Haken setzen gibt frei. Der Name in der Sicht wird vorgeschlagen; die
  Wirkungsklasse wählst du daneben. Was der Server über sein Werkzeug sagt,
  ist nur ein Vorschlag.
- Haken entfernen nimmt heraus.
- **Zuordnung speichern**.

## Zuschnitt

Unter **Freigegebene Einträge** → **Zuschnitt ändern** lassen sich je
Eintrag ein eigener Name, eine eigene Beschreibung, **feste Argumente**
(verschwinden aus dem Schema, das der Client sieht) und **Grenzen**
(erlaubte Werte oder ein Muster wie `Z*`) setzen.

## Wenn ein Server seinen Text ändert

Meldet ein Server für ein freigegebenes Werkzeug einen anderen Text, ist es
in jeder Sicht gesperrt. Die Seite der Sicht zeigt alten und neuen Text
nebeneinander. Lies den neuen Text – er geht unmittelbar ins Modell – und
bestätige ihn erst dann.

## Kopieren, umbenennen, Multi-System

Auf der Seite der Sicht: **Kopieren oder umbenennen** (Freigaben gehen
mit). Liegen Server mehrerer Zielsysteme in der Sicht, lässt sie sich in
eine Multi-System-Sicht umwandeln; die Zielsysteme pflegst du im Reiter
**Systemlandschaft**.
