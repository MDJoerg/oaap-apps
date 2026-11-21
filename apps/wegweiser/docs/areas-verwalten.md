# Areas verwalten

Eine **Area** ist das erste Stück der öffentlichen Adresse (`go.example.org/r/…`).
Sie bestimmt den Typ und die Regeln aller Links darin.

## Eine Area anlegen (Verwaltung)

1. Reiter **Verwaltung** → **Neue Area**.
2. **Slug** (das Pfadstück, z. B. `r`) und **Typ** wählen – beides steht danach
   fest. Reserviert sind `admin`, `api`, `auth`, `healthz`, `manage`, `platform`,
   `static`.
3. **Titel**, **Beschreibung** und **Verantwortliche** (Benutzernamen, mit Komma;
   sie brauchen die Rolle `keyuser`).
4. Die Regeln:
   - **Area aktiv**, **Benutzer dürfen Links anlegen**, **Wunsch-Keys**,
     **PIN ist Pflicht**,
   - Länge erzeugter Keys, PIN-Mindestlänge,
   - Vorgabe-Lebensdauer und Höchstdauer für Benutzer (gilt nicht für
     Verantwortliche),
   - Höchstgröße je Datei, erlaubte Endungen,
   - **Speichertiefe** und **Aufbewahrung** der Zugriffe.
5. **Anlegen**.

Ein Klick auf eine Area in der Liste öffnet ihre Seite: dort ändern Sie die
Eigenschaften, sehen die Statistik und können die Area **löschen** – mit allen
Links, Dateien und Zugriffen. Das ist endgültig.

## Als Verantwortliche

Im Reiter **Areas** sehen Sie die Areas, die Sie betreuen. Ein Klick zeigt alle
Links der Area (auch die anderer Personen), die Regeln und die Statistik. Links
anderer öffnen, ändern und löschen dürfen Sie in Ihrer Area auch.

## Öffentliche Adressen

Unter **Verwaltung → Öffentliche Adressen** stehen die Namen, die die Plattform
dieser Instanz gibt. Sie können weitere ergänzen (eine je Zeile) und die
**Vorgabe** wählen – sie steht in Link-Adressen und QR-Codes. Gibt es keine, gilt
die Adresse, unter der die Seite gerade aufgerufen wurde.
