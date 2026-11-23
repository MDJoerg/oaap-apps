# Das Wiki als Ablage für die KI

Seit Version 0.2.0 kann eine KI im Wiki lesen und schreiben, **als die Person,
die mit ihr spricht**. Der Weg führt über das MCP-Gateway des Mandanten und
einen Arbeitsraum, in dem die Person ein Buch des Wikis eingehängt hat.

## Was das Wiki dafür mitbringt

Der Dienst `oaap` im Paket hat eine MCP-Tür unter `/oaap/mcp`: den
**Ablagen-Konnektor**. Er bietet feste Werkzeuge an: Bücher auflisten, ein
Kapitel ansehen, eine Seite lesen oder schreiben, ein Kapitel anlegen,
suchen. Das MCP-Gateway nimmt die Wiki-Instanz von selbst als Quelle auf.

| Im Arbeitsraum | im Wiki |
|---|---|
| Bereich | Regal |
| Ablage | Buch |
| Ordner | Kapitel |
| Dokument | Seite |

Ein Pfad ist `/kapitel/seite`, mit den Namen, die das Wiki in seinen
Adressen zeigt.

## Als die Person

Der Konnektor findet das Wiki-Konto der Person an ihrer Anmeldung beim
Realm. Für dieses Konto legt er ein kurzes Token an, das er nur im Speicher
hält, und arbeitet damit. Das Wiki entscheidet also selbst:

- Wer ein Buch nicht sehen darf, sieht es auch über die KI nicht.
- Wer nicht schreiben darf, bekommt eine Ablehnung.
- Jede Änderung steht unter dem Namen der Person in der Revisionsliste der
  Seite.

Es gibt keinen technischen Benutzer, der für alle liest.

## Zwei Voraussetzungen

1. **Die Person hat sich einmal im Wiki angemeldet.** Erst dann gibt es ihr
   Konto.
2. **Ihre Rolle hat das Recht „Zugriff auf System-API“.** Ohne dieses Recht
   lehnt BookStack jeden Aufruf ab. Das Recht öffnet nur den Weg; welche
   Bücher und Seiten die Rolle sieht, ändert es nicht.
   - Rollen, die die Einrichtung anlegt (Art `role`), bekommen es seit 0.2.0
     von selbst.
   - BookStacks eigene Rollen und von Hand angelegte setzt die
     Wiki-Verwaltung: Einstellungen → Rollen → Rolle öffnen → „Zugriff auf
     System-API“.

## Grenzen in dieser Fassung

- Es wird nur Markdown geschrieben. Seiten im WYSIWYG-Editor lassen sich
  lesen, aber nicht ändern.
- Bilder und Anhänge zeigt die Ablage nicht.
- Eine Seite anzulegen verlangt einen Titel, der genau zur Adresse passt
  (`/kapitel/neue-seite` ↔ Titel „Neue Seite“).
