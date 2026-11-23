# Im Arbeitsraum arbeiten

Ein **Arbeitsraum** bündelt deine Ablagen, etwa ein Buch im Wiki, für deine
Gespräche. Ein Gespräch im Arbeitsraum beginnt mit dem **Stand** deiner
Arbeit und nicht bei null. Was die KI dort liest oder schreibt, tut sie
**als du**: Was du in einer Ablage nicht darfst, darf sie dort auch nicht.

## Einen Arbeitsraum anlegen

1. Unten neben „Assistent“ auf **Arbeitsräume …** klicken.
2. Einen Titel geben, dann je Ablage wählen:
   - das System (z. B. das Vereinswiki);
   - die Ablage darin (die Liste zeigt nur, was du dort sehen darfst);
   - einen kurzen **Alias**, z. B. `notizen`;
   - eine **Rolle**: *Arbeitsablage* (lesen und schreiben) oder *Quelle*
     (nur lesen).
3. Genau eine Arbeitsablage ist die **Heimat**. Dort legt der Chat im Ordner
   `/arbeitsraum` den Stand (`stand`) und das Protokoll (`protokoll`) ab.

Fehlt die Auswahl der Systeme ganz, hat die Verwaltung noch kein Ablagesystem
im MCP-Gateway eingetragen.

## Ein Gespräch im Arbeitsraum beginnen

Wähl vor der ersten Nachricht unten einen **Assistenten** und einen
**Arbeitsraum**. Der Assistent braucht eine Sicht; ein Assistent ganz ohne
Werkzeuge geht hier nicht.

- **Ampel**: Ein Arbeitsraum hat die Ampel seiner strengsten Ablage. Ist er
  gelb und dein Gespräch grün, fragt der Chat nach der Ampel, sobald du den
  Arbeitsraum wählst. Grün ist dann nicht wählbar; du bestätigst mit dem
  Haken, dass du nichts Sensibles eingibst.
- **privat**: Ein privates Gespräch bekommt am Ende keinen Protokolleintrag,
  und sein Titel erscheint für niemanden sonst. Was du in eine Ablage
  schreibst, steht dort trotzdem unter deinem Namen.

Vor der ersten Antwort liest der Chat, welche Ablagen eingehängt sind, den
Stand und die letzten Einträge des Protokolls, und gibt das der KI mit. Die
Ablagen selbst liest die KI erst, wenn sie etwas braucht. Adressen sehen so
aus: `notizen:/arbeitsraum/stand`.

## Befehle im Arbeitsraum

| Befehl | Was geschieht |
|---|---|
| `/stand` | Die KI liest den Stand und schlägt vor, was sich geändert hat. Du bestätigst jede Änderung. |
| `/abschluss` | Wie `/stand`, dazu ein Eintrag oben im Protokoll: Datum, Titel, was sich geändert hat, welche Dokumente geschrieben wurden. Im privaten Gespräch ohne Protokoll. |
| `/ablegen` | Der Chat schreibt **das ganze Gespräch wörtlich** als eine Seite in den Heimatordner, mit Datum und Uhrzeit im Namen. Das macht der Chat selbst, ohne die KI. |
| `/ablegen notizen:/ordner` | dasselbe in einen anderen Ordner einer Arbeitsablage |

Vor dem Ablegen zeigt der Chat, wohin es geht. Er nennt auch die anderen
Ablagen, aus denen das Gespräch gelesen hat: Was dort stand, kann jetzt in
der Zielablage landen.

## Gut zu wissen

- Lesen geht ohne Rückfrage. Schreiben läuft erst nach deiner Zustimmung.
  Sieh dir den Vorschlag an, bevor du „Ausführen“ drückst: Ein kleines Modell
  formuliert manchmal mehr um, als du wolltest.
- Eine Quelle kann niemand über den Chat beschreiben; das lehnt schon das
  MCP-Gateway ab.
- Arbeitsräume liegen im MCP-Gateway, nicht im Chat. Ein anderer Chat des
  Mandanten sieht dieselben.
