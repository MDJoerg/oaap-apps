# Recherche

Recherche gibt den Assistenten im Chat zwei Werkzeuge für das öffentliche Internet:

| Werkzeug | Was es tut |
| --- | --- |
| `web_search` | fragt einen Suchdienst und liefert Treffer mit Titel, Adresse und Ausschnitt |
| `web_fetch` | liest eine öffentliche Webseite als Text (höchstens 12.000 Zeichen) |

Beide Werkzeuge lesen nur. Die App hat keine eigene KI und speichert nichts außer einem
Zwischenspeicher für eine Stunde.

## Für wen

- **Alle, die im Chat arbeiten:** Ein Assistent mit Recherche kann aktuelle Informationen
  aus dem Internet suchen und mit Quellen belegen.
- **Administratoren:** Sie richten den Suchdienst ein und legen fest, welche Seiten
  gelesen werden dürfen.

## Was Sie wissen sollten

- **Suchwörter verlassen den Mandanten.** Sie gehen an einen externen Suchdienst, und die
  gelesene Seite sieht, dass sie abgerufen wurde. Schreiben Sie nichts Vertrauliches in eine
  Suche.
- Deshalb steht Recherche im Chat immer auf **rot**: Ein Gespräch mit Internetzugriff
  stellen Sie bewusst auf rot.
- Seiten im eigenen Netz und die Adressen des eigenen Servers liest die App nie.
- Suchwörter, Adressen und Inhalte landen in keinem Protokoll. Die Seite der App zeigt nur,
  wie oft jedes Werkzeug am Tag benutzt oder abgelehnt wurde.

## Weiter

- [Im Chat recherchieren](im-chat-recherchieren.md)
- [Recherche einrichten](einrichten.md)
- [Häufige Fragen](faq.md)
