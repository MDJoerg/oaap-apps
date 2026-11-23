# Wiki (BookStack) – häufige Fragen

## Ich bin angemeldet, sehe aber kein einziges Buch.

Du bist in keiner Gruppe, für die es im Wiki eine Rolle gibt. Bitte die
Verwaltung, Dich in die passende Gruppe aufzunehmen (z. B.
`/wiki-redaktion`), und melde Dich danach neu am Wiki an.

## Ich bin jetzt in einer neuen Gruppe, sehe aber nichts Neues.

Das Wiki gleicht Gruppen nur bei der Anmeldung ab. Melde Dich im Wiki ab
und wieder an.

## Ich kenne die Adresse eines Bildes, bekomme es aber nicht.

Richtig so, wenn Du die Seite nicht sehen darfst, zu der das Bild gehört.
Bilder sind so geschützt wie ihre Seite.

## Das Wiki startet nicht, im Protokoll steht „MARIADB_PASSWORD fehlt“.

Die Instanz wurde ohne Datenbank-Geheimnis installiert, z. B. über die
Kommandozeile. Setze es, wie unter [Einrichten](einrichten.md)
beschrieben. Die Datenbank richtet sich dann ein und das Wiki startet.

## Wie melde ich mich an, wenn der Anmeldedienst nicht angebunden ist?

Mit `admin@admin.com` und dem Kennwort aus der Einstellung
`OAAP_WIKI_ADMIN_PASSWORD`. Das gilt nur, solange es keinen Client beim
Anmeldedienst gibt. Ändere das Kennwort danach im Wiki.

## Was ist das Konto „OAAP (Plattform)“?

Das Konto, mit dem der Dienst `oaap` Rollen anlegt und einrichtet. Es
schreibt keine Seiten. Lösche es nicht; fehlt es, legt der Dienst es beim
nächsten Start neu an.

## Kann ich auf eine ältere Fassung zurück?

Nur mit einer Sicherung von vor dem Update, siehe
[Sicherung und Update](sicherung-und-update.md).
