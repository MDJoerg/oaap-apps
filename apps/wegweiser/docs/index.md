# Wegweiser

Der **Wegweiser** macht aus langen Adressen kurze – unter Ihrer eigenen Domain,
zum Beispiel `go.example.org/r/Ab3kX9`. Dazu kann er mehr als Weiterleiten:

| Typ der Area | Was beim Aufruf passiert |
| --- | --- |
| Weiterleitung | Der Browser geht zur Ziel-Adresse (mit PIN erst nach der Eingabe). |
| Zählpixel | Ein unsichtbares Bild; Sie sehen, wie oft es geladen wurde. |
| Einmal-Download | Eine Seite mit Knopf; die Datei lässt sich genau einmal holen. |
| Zeitraum-Download | Wie oben, aber in einem Zeitraum und auf Wunsch mehrmals. |
| Einmal-Upload | Jemand lädt Ihnen genau eine Datei hoch, danach ist der Link verbraucht. |

Einmal-Links verbraucht nie das bloße Öffnen: Mailprogramme und Messenger rufen
Links für Vorschauen auf. Erst der Knopf lädt herunter, erst das Hochladen
verbraucht einen Upload-Link.

## Für wen

- **Alle Benutzer** legen Links in offenen Areas an und sehen ihre eigenen.
- **Verantwortliche einer Area** (Rolle `keyuser`, von der Verwaltung
  eingetragen) sehen alle Links ihrer Area, vergeben Wunsch-Keys und sehen die
  Statistik der Area.
- **Die Verwaltung** (Rolle `admin`) legt Areas an, bestimmt ihre Regeln und die
  öffentlichen Adressen.

## Bereiche

- **Meine Links** – Ihre Links, durchsuchbar und filterbar; **Neuer Link** legt
  einen an.
- **Areas** – für Verantwortliche: die Areas, die Sie betreuen, mit allen Links
  und Zahlen.
- **Verwaltung** – für die Rolle admin: Areas, öffentliche Adressen, REST-API.

## Anleitungen

- [Einen Link anlegen](link-anlegen.md)
- [Dateien übergeben](uebergaben.md)
- [Statistik lesen](statistik.md)
- [Areas verwalten](areas-verwalten.md)
- [REST-API](api.md)
- [Häufige Fragen](faq.md)
