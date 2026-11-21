# Ein Paket hochladen

1. Reiter **Neue Version** öffnen.
2. Die ZIP-Datei wählen. Optional eine **Notiz** dazuschreiben, z. B. was sich
   geändert hat.
3. **Hochladen**. Danach landest Du auf der Seite der App; die neue Version steht
   dort als **nicht freigegeben**.

## Was abgelehnt wird

Der Katalog nimmt nichts auf, was der Knoten beim Entpacken ablehnen würde:

- Pfade mit `..` oder absolute Pfade in der ZIP,
- Verknüpfungen (Symlinks),
- ein fehlendes oder ungültiges Manifest,
- eine Version, die nicht die Form `x.y.z` hat,
- eine Version, die es schon gibt – sie wird nie überschrieben,
- ein Paket über der Höchstgröße (Standard 256 MB).

Die Meldung oben auf der Seite sagt, was nicht passt.

## Ohne Herunter- und Hochladen

Ein Paket, das schon auf einer Instanz läuft (etwa einer Test-Instanz), kommt
direkt aus dem Portal hierher: auf der Instanz, Reiter **Deployment**, „In den
Katalog übernehmen“. In der Übersicht trägt es das Zeichen **vom Knoten**. Auch
dieses Paket ist zuerst **nicht freigegeben**.
