# Spezialisten

Ein **Spezialist** ist ein KI-Assistent, der Vorgänge im Hintergrund bearbeitet. Er hat
einen eigenen Agenten mit engen Rechten nur in seinen Bereichen – er handelt nie mit den
Rechten eines Menschen.

## Was ein Spezialist tut

1. Alle 30 Sekunden fragt die App bei **Vorgänge**, welche Vorgänge an einem Spezialisten
   dran sind.
2. Er liest den Vorgang. Der Text der meldenden Person ist für ihn Material, keine
   Anweisung.
3. Er nutzt nur die Werkzeuge seiner eigenen Sicht im MCP-Gateway und erreicht das Modell
   über das KI-Gateway.
4. Jeden Schritt schreibt er **intern** in den Vorgang: Notizen, Werkzeugaufrufe, am Ende
   den Grund. Die meldende Person sieht davon nichts.
5. Am Ende gibt er den Vorgang an die Zuständigen des Bereichs zurück.

Was etwas ändert, legt er einem Menschen zur **Freigabe** vor. Kommt er nicht weiter, stellt
er eine **Rückfrage** an einen anderen Spezialisten oder einen Menschen des Bereichs.

## Für wen

- **Mandanten-Administratoren** legen Spezialisten an, prüfen und schalten sie.
- **Key-User** sehen, welche Spezialisten es gibt und wie ihre Läufe endeten.
- Wer in Vorgänge arbeitet, sieht die Schritte eines Spezialisten im Vorgang und gibt
  seine Pläne frei.

## Die Seiten

| Reiter | Inhalt |
| --- | --- |
| Übersicht | alle Spezialisten mit Zustand, Assistent und Modell |
| Läufe | jeder Lauf an einem Vorgang, mit Filter nach Spezialist und Ende |
| Neuer Spezialist | anlegen (nur Administratoren) |
| Änderungen | wer wann was geändert hat (nur Administratoren) |

Ein Klick auf einen Spezialisten öffnet seine Seite mit *Prüfen*, *Ein-/Ausschalten*,
*Löschen* und dem Reiter *Bearbeiten*.

Alles, was ein Spezialist entscheidet, ist mit **KI** markiert.

## Weiter

- [Einen Spezialisten einrichten](spezialist-einrichten.md)
- [Läufe verfolgen](laeufe-verfolgen.md)
- [Freigaben und Rückfragen](freigaben-und-rueckfragen.md)
- [Häufige Fragen](faq.md)
