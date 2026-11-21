# Paketkatalog – Überblick

Der Paketkatalog ist das Regal des Betreibers für **App-Pakete**: ZIP-Dateien,
aus denen ein Knoten eine App baut. Du lädst ein Paket hoch, prüfst es und
**gibst eine Version frei**. Was freigegeben ist, erscheint im **Store** der
Mandanten-Administratoren; sie installieren und aktualisieren es dort wie jede
andere App.

Der Katalog liest aus einem Paket nur das Manifest (`oaap-app.yaml`). Er
entpackt nichts und führt nie Code aus.

## Wer den Paketkatalog benutzt

Nur der **Betreiber** (Rolle `admin`). Ein Paket ist Code, den ein Knoten baut
und ausführt – wer hier freigibt, bestimmt, was Mandanten installieren können.

## Die Bereiche

- **Übersicht** – alle Apps im Katalog mit neuester und freigegebener Version.
  Suche nach Name oder Kennung, Filter nach Status. Ein Klick auf eine App öffnet
  ihre Seite mit allen Versionen.
- **Seite einer App** – Versionen, Freigeben und Zurückziehen, Laden (die ZIP
  herunterladen), Löschen, „Sichtbar für“.
- **Neue Version** – eine ZIP hochladen.
- **Spiegel** – nur, wenn ein Marktplatz angebunden ist: Der Katalog holt dessen
  Pakete selbst.
- **Einrichtung** – wie der Knoten den Katalog als Store-Quelle liest.

## Anleitungen

- [Ein Paket hochladen](paket-hochladen.md)
- [Freigeben und zurückziehen](freigeben.md)
- [Sichtbar nur für bestimmte Mandanten](sichtbarkeit.md)
- [Den Katalog als Store-Quelle eintragen](store-quelle.md)
- [Einen Marktplatz spiegeln](spiegel.md)
- [Häufige Fragen](faq.md)

## Grundregeln

- **Hochgeladen heißt noch nicht freigegeben.**
- **Eine Version wird nie überschrieben.** Eine neue Fassung braucht eine neue
  Versionsnummer im Manifest.
- Im Store steht je App die **höchste freigegebene** Version, nach Zahlen
  geordnet (1.2.10 kommt nach 1.2.9).
- Der Knoten **prüft jedes Paket selbst** noch einmal (Größe, Prüfsumme, Kennung
  und Version), bevor er es entpackt.
