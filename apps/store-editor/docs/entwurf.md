# Entwurf herunterladen und verwerfen

Der Reiter **Entwurf** einer Liste zeigt, ob es eine Arbeitskopie gibt, wann
sie begonnen und zuletzt gespeichert wurde und wie viele Änderungen sie
gegenüber dem veröffentlichten Stand hat.

## Änderungen ansehen

**Änderungen ansehen** listet jede Änderung mit Vorher und Nachher. Sie ist
in drei Arten getrennt:

- **strukturell** – Einträge aufgenommen oder entfernt, ein Paket zieht um,
- **redaktionell** – Texte, Einordnung, Verweise,
- **aus dem Manifest** – vom Abgleich übernommen, nicht von Hand
  geschrieben.

## Als Datei herunterladen

**Als Datei herunterladen** liefert die Liste als JSON-Datei. Die Datei
enthält nur die Liste, nicht die Buchführung des Editors (Markierungen,
Vergleichsstand). Du veröffentlichst sie selbst, zum Beispiel per Git.

Ist die Liste strukturell kaputt, gibt es die Datei nicht. Die Seite zeigt
dann, was zu beheben ist.

## Entwurf verwerfen

**Entwurf verwerfen** löscht die Arbeitskopie nach einer Rückfrage. Danach
gilt wieder der veröffentlichte Stand.
