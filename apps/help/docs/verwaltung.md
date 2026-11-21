# Die Hilfe verwalten

Für Administratoren: der Reiter **Verwaltung**.

## Quellen

Die Tabelle zeigt, woher die Hilfe ihre Dokumente hat, und wann zuletzt abgerufen wurde:

| Quelle | Was |
| --- | --- |
| Plattform-Doku | ein Archiv über die Destination `docs-origin`; alle paar Stunden neu geholt |
| Apps | die Hilfe der installierten Apps, von der Plattform hereingereicht |
| Eigene Notizen | Texte, die Sie hier ablegen |

**Jetzt holen** ruft die Plattform-Doku sofort ab, statt auf den nächsten Abruf zu warten.

Fehlt die Destination, sagt die Hilfe das oben auf der Startseite – Administratoren mit
dem Weg zur Abhilfe. Ohne Plattform-Doku (`HELP_OAAP_DOCS=off`) braucht die Hilfe keine
Destination.

## Eigene Notizen

1. Einen Dateinamen eingeben, zum Beispiel `notizen/backup.md`.
2. Den Text in Markdown schreiben, *Speichern*.

Trägt eine Notiz im Kopf dieselbe `id` wie ein Text der Plattform, überdeckt sie ihn auf
Ihrem Server. Das Original bleibt lesbar und ist als „überdeckt“ markiert. *entfernen*
löscht die Notiz wieder.

## Einstellungen

| Einstellung | Bedeutung |
| --- | --- |
| `HELP_TITLE` | Name der Hilfe in Kopfzeile und Startseite (Standard „Hilfe“) |
| `HELP_LAYOUT` | `kacheln` oder `nüchtern` (Liste) |
| `HELP_OAAP_DOCS` | Plattform-Doku einbeziehen (`on`/`off`) |
| `HELP_REQUESTS` | Assistenten dürfen fehlende Anleitungen anfragen (`on`/`off`) |
| `HELP_PUBLIC` | nur für eine zentrale, öffentliche Hilfe: `/public/` ohne Anmeldung |

## Anfragen nach Anleitungen

Mit `HELP_REQUESTS=on` kann ein Assistent melden, dass eine Anleitung fehlt. Gleiche Anfragen
werden zu Stimmen gezählt, gespeichert ohne Adresse und ohne Person. Die Verwaltung zeigt,
wie viele offen sind.
