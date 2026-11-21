# Eine Liste prüfen

## Eine eingetragene Liste

1. Öffne die **Übersicht**. Jede Liste zeigt, wie viele Einträge sie hat,
   wie viele davon gegen ihr Manifest geprüft werden konnten, und das
   Ergebnis als farbige Marken.
2. Klicke auf die Liste. Der Reiter **Prüfung** zeigt jeden Befund mit
   App, Feld, dem Wert in der Liste und dem Wert im Manifest.
3. Mit dem Suchfeld und der Auswahl **Art** grenzt Du die Befunde ein.

## Eine Liste, die noch nicht veröffentlicht ist

Unter **Liste einfügen** fügst Du den Inhalt einer Liste (JSON) ein und
klickst **Prüfen**. Der Inhalt wird nur geprüft und **nirgends
gespeichert**. Die Manifeste holt der Prüfer trotzdem aus den Repositories,
auf die die Einträge zeigen.

## Die drei Arten von Befund

| Art | Bedeutung |
|---|---|
| **Fehler** | So ist die Liste nicht benutzbar – ein Knoten würde daran scheitern oder etwas Falsches tun. |
| **Befund** | Liste und Manifest sagen beide etwas, und es ist verschieden. |
| **Hinweis** | Auffällig, aber vielleicht Absicht – etwa eine Angabe, die das Manifest (noch) nicht belegt. |

Unbekannte Werte (etwa eine Kategorie, die der Editor nicht kennt) sind nur
ein Hinweis: Ein Knoten kommt damit zurecht, also ist die Liste nicht kaputt.

## Was verglichen wird

Name, Verpackungsart, Version, Art der App und die Rollen. Die Rollen
werden als Menge verglichen – die Reihenfolge bedeutet nichts.

**Nicht** verglichen werden die Beschreibung (in der Liste steht
absichtlich der längere Text), das Bild, das Freigabedatum und die
Knotenprofile. Das Manifest kennt diese Angaben nicht in derselben Form.
