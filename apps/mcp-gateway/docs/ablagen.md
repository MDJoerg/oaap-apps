# Ablagen und Arbeitsräume

Für die Verwaltung des Mandanten und für alle, die mit Ablagen arbeiten.

## Worum es geht

Viele Menschen legen ihre Arbeit in anderen Systemen ab: in einem Wiki-Buch,
einem Git-Repository, einem Projektordner. Ein **Arbeitsraum** bündelt solche
Ablagen für ein Gespräch mit einer KI. Jede Ablage bekommt einen kurzen
**Alias** (`notizen`, `satzung`) und eine **Rolle**:

- **Quelle** – nur lesen und suchen;
- **Arbeitsablage** – auch schreiben.

Genau eine Arbeitsablage ist die **Heimat**. Dort liegen im Ordner
`/arbeitsraum` der Stand der Arbeit (`stand`) und das Protokoll
(`protokoll`).

Jeder Zugriff läuft **als du**, direkt am System. Was du dort sehen und
ändern darfst, entscheidet das System allein. Ein Arbeitsraum gibt dir kein
Recht dazu und nimmt dir keins.

## Ablagesystem eintragen (Verwaltung)

1. Das System braucht einen **Konnektor**. Das Wiki (BookStack) bringt ihn
   mit: Nach der Installation erscheint die Wiki-Instanz unter „Server“ als
   Quelle der Plattform.
2. Gib dem Server eine Ampel. Neue Quellen beginnen bei rot; ein internes
   Wiki ist meist gelb.
3. Reiter **Ablagen** → „Ablagesystem eintragen“: Name (z. B. `wiki`), Titel,
   Typ `bookstack`, als Konnektor den Server der Wiki-Instanz, Ampel.
4. Unten auf derselben Seite schaltest du die **Ablagetür** für die Zugänge
   ein, die sie benutzen sollen, etwa das Profil, über das der Chat kommt.
   Sie ist überall aus, bis du sie einschaltest. Ein Schlüssel ohne Person
   bekommt nie Ablagen.

Auf der Kommandozeile geht dasselbe:

```sh
docker exec <gateway> python3 /srv/app.py store set wiki --kind bookstack \
  --server <wiki-instanz> --title "Vereinswiki" --light yellow
docker exec <gateway> python3 /srv/app.py key stores <profil> an
```

## Die Werkzeuge im Arbeitsraum

Läuft ein Gespräch in einem Arbeitsraum, bekommt die KI zusätzlich zu ihrer
Sicht diese Werkzeuge:

| Werkzeug | Wozu |
|---|---|
| `workspace_info` | welche Ablagen eingehängt sind, wo Stand und Protokoll liegen |
| `store_list` | einen Ordner ansehen |
| `store_read` | ein Dokument lesen, auch nur einen Abschnitt oder Zeilen |
| `store_outline` | die Überschriften eines Dokuments |
| `store_search` | in einer Ablage suchen |
| `store_write` | ein Dokument anlegen oder ganz ersetzen |
| `store_edit` | einen Teil ändern: einen Abschnitt ergänzen, ersetzen, löschen |
| `store_folder` | einen Ordner anlegen |

Die Adresse ist immer `alias:/pfad`, zum Beispiel `notizen:/arbeitsraum/stand`.
Die schreibenden Werkzeuge gibt es nur mit einer Arbeitsablage.

Was die Tür verhindert, bevor etwas am System ankommt:

- in eine **Quelle** schreiben;
- eine Ablage ansprechen, die nicht eingehängt ist, oder mit `..` aus ihr
  herausgehen;
- ein Dokument ändern, das sich seit dem Lesen geändert hat. Die KI muss es
  dann erst neu lesen;
- ein langes Dokument versehentlich auf weniger als die Hälfte kürzen.

Was die KI aus einer Ablage liest, ist für sie fremder Inhalt: Daten, keine
Anweisungen.

## Wenn etwas nicht geht

- **„Deine Rolle im Wiki hat das Recht ‚Zugriff auf System-API‘ nicht“**:
  Die Wiki-Verwaltung setzt es an der Rolle (im Wiki: Einstellungen →
  Rollen). Das Recht öffnet nur den Weg; welche Bücher du siehst, bleibt
  gleich.
- **„Du hast noch kein Konto im Wiki“**: Melde dich einmal im Wiki selbst
  an. Danach geht es auch im Arbeitsraum.
- **„Die Ablagetür ist für diesen Zugang nicht eingeschaltet“**: Das schaltet
  die Verwaltung im Reiter „Ablagen“ ein.
- **„… ist für die Ampel dieses Gesprächs nicht freigegeben“**: Die Ablage ist
  strenger eingestuft als das Gespräch.
