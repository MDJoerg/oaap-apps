# Store-Editor – Überblick

Der Store-Editor prüft und pflegt **Store-Listen**: die Kataloge, aus denen
ein Knoten Apps zur Installation anbietet. Eine solche Liste ist kein
gewöhnliches Dokument. Sie ist eine Anweisung, die auf fremden Rechnern
Software installiert. Darum prüft der Editor jede Liste nicht nur gegen ihr
Format, sondern **gegen die Manifeste, auf die ihre Einträge zeigen**.

Ein Beispiel, warum das nötig ist: Eine App stand wochenlang als
Hintergrunddienst in einer Liste, ihr eigenes Manifest sagte davon nichts.
Beim Lesen der Liste findet so einen Widerspruch kein Mensch – der Prüfer
findet ihn sofort.

## Wer den Store-Editor benutzt

- **user**, **keyuser** und **admin** dürfen Listen prüfen und Einträge
  bearbeiten. Bearbeitet wird nur eine Arbeitskopie auf dieser Instanz.
- Listen **aufnehmen oder entfernen** ist Einrichtung und braucht
  **keyuser** oder **admin**.
- Die Zugangsschlüssel für private Listen trägt ein Serververwalter im
  Portal ein, nicht hier.

## Die Bereiche

| Bereich | Wozu |
|---|---|
| **Übersicht** | alle eingetragenen Listen mit ihrem Prüfergebnis |
| **Liste einfügen** | eine Liste prüfen, die noch nirgends veröffentlicht ist |
| **Listen und Zugang** | Listen aufnehmen und sehen, welcher Schlüsselplatz gilt |

Ein Klick auf eine Liste öffnet ihre Seite mit den Reitern **Einträge**,
**Prüfung** und **Entwurf**.

## Was der Editor nicht tut

Er **veröffentlicht nichts**. Er schreibt nicht ins Repository zurück. Das
Ergebnis Deiner Arbeit ist eine **Datei zum Herunterladen**, die Du selbst
veröffentlichst. Eine Liste, die strukturell kaputt ist, gibt der Editor
nicht als Datei heraus.

## Weiterlesen

- [Eine Liste prüfen](liste-pruefen.md)
- [Einen Eintrag bearbeiten](eintrag-bearbeiten.md)
- [Entwurf herunterladen und verwerfen](entwurf.md)
- [Der Nachpflege-Bericht](nachpflege-bericht.md)
- [Listen und Zugangsdaten einrichten](listen-und-zugang.md)
- [Häufige Fragen](faq.md)
