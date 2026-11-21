# Schlüssel und Zugangsprofile vergeben

Für die Verwaltung (Rolle `admin`). Reiter **Schlüssel & Profile**.

## Schlüssel für einen Agenten

1. **+ Schlüssel ausstellen oder Zugangsprofil anlegen**, Art **Schlüssel**.
2. **Etikett**: benennt den Verbraucher (etwa `pipeline-nacht`).
3. **Sichten**, durch Komma getrennt.
4. **Höchste Wirkungsklasse**, **Ampel höchstens**, auf Wunsch ein
   **Budget** (Aufrufe) und eine **Rate** je Minute.
5. Kontext für die Auswertung: Verantwortlich, Projekt, Kostenstelle …
6. **Ausstellen**. Der Wert erscheint **genau einmal**, mit dem Befehl für
   Claude Code. Gespeichert ist nur eine Prüfsumme; wer ihn verliert,
   bekommt einen neuen.

## Zugangsprofil für Personen

Ein Zugangsprofil hat keinen Wert. Es gilt für Personen, die auf einem von
zwei Wegen kommen:

- **Selbst am Gateway angemeldet** (etwa Claude Code mit Anmeldung):
  Gruppen `*` für alle, die sich anmelden dürfen, oder volle Pfade wie
  `/sap-dev`. Die Sicht braucht den Zugang „Anmeldung“.
- **Von einem Chat der Plattform hergetragen**: `@<chat-instanz>` oder
  `@*` für jeden Chat des Mandanten. Die Sicht braucht den Zugang
  „Plattform“. Ein `*` gilt für einen Chat nicht.

## Ändern und widerrufen

Etikett in der Liste anklicken. Auf der Seite des Schlüssels stehen
Sichten, Grenzen und Gruppen zum Ändern und **Widerrufen**. Ein Widerruf
gilt sofort.

Passt ein Schlüssel nicht zu einer seiner Sichten (falscher Zugang, Ampel
zu streng), steht der Grund in Worten in der Liste und unter **Zu tun** in
der Übersicht.
