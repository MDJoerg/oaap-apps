# Einen Eintrag bearbeiten

1. Öffne die Liste und dort den Reiter **Einträge**.
2. Klicke auf die App. Ihre Seite zeigt oben die Prüfung nur für diesen
   Eintrag und darunter das Formular.
3. Ändere, was Du ändern willst, und klicke **Speichern**.

Die erste Änderung legt einen **Entwurf** an – eine Arbeitskopie auf dieser
Instanz. Der veröffentlichte Stand bleibt daneben stehen und dient als
Vergleich. Solange es einen Entwurf gibt, prüft der Editor den Entwurf und
nicht mehr die Veröffentlichung.

## Redaktionelle Felder

Kurzer Satz, Beschreibung, Kategorien, Zielgruppe, Reifegrad, Stand,
Schlagwörter, Lizenz, Verweise und Bildschirmfotos. Über diese Felder muss
ein Mensch nachdenken; sie kommen nicht aus dem Manifest. Ein leeres Feld
heißt „weglassen“, nicht „leer behaupten“.

Bildpfade gelten **relativ zur Liste**. Sonst würde jeder Knoten, der die
Store-Seite öffnet, einen fremden Server anrufen.

## Felder aus dem Manifest

Name, Verpackungsart, Version, Art der App und Rollen gehören dem Paket. Sie
stehen gesperrt da und werden aus dem Manifest übernommen. Willst Du eines
davon bewusst anders pflegen, hake **abweichend pflegen** an. Die
Abweichung wird dann **markiert** und bleibt beim nächsten Abgleich stehen.
Ohne Markierung würde jeder Abgleich Deine Entscheidung wieder zurücknehmen.

Die Markierungen liegen im Editor, nicht in der Liste. Wer dieselbe Liste in
einem anderen Editor öffnet, sieht sie nicht.

## Mit dem Manifest abgleichen

- **Abgleichen** oben auf der Seite einer App holt nur die Felder aus dem
  Manifest und lässt Deine Texte unberührt.
- **Speichern und mit dem Manifest abgleichen** speichert zuerst das
  Formular und gleicht dann ab.
- **Alle Einträge abgleichen** (Reiter **Entwurf** der Liste) macht das für
  die ganze Liste. Markierte Felder bleiben dabei unberührt.

Ein Abgleich, der nichts findet, legt keinen Entwurf an.

## Einen Eintrag aufnehmen oder entfernen

- **Eintrag aufnehmen** (Reiter **Entwurf**) braucht nur die Kennung der
  App und das Git-Repository. Alles Weitere holt der Abgleich. Ein Eintrag
  darf entstehen, bevor sein Manifest abrufbar ist; der Prüfer meldet dann
  bei jedem Lauf, dass er ohne Beleg dasteht.
- **Eintrag entfernen** steht auf der Seite der App und fragt noch einmal
  nach. Entfernt wird er nur aus der Arbeitskopie.
