# Zielknoten und Zustand der Instanzen

## Der Zielknoten

Das Studio muss **nicht** auf demselben Server laufen wie die App, um die es
sich kümmert. Der **Zielknoten** eines Vorhabens ist die Plattform, auf der
seine Instanzen leben. Die Seite des Vorhabens zeigt ihn mit seiner Quelle:

1. aus dem **Deploy-Hook** – dorthin gehen die Pakete tatsächlich, das gilt;
2. aus dem Feld **Zielknoten (Adresse)** – für die Zeit, bevor es einen
   Hook gibt;
3. als ausgewiesene **Vermutung** „der Knoten, auf dem dieses Studio läuft“.

Widersprechen sich Hook und Feld, gewinnt der Hook, und das Studio sagt den
Widerspruch dazu.

## Zwei Instanzen je Vorhaben

Ein Vorhaben hat eine **Test-Instanz** und eine **Produktiv-Instanz**, beide
auf demselben Zielknoten. Die Übernahme nach Produktiv reicht *dieselben
Bytes* weiter, die getestet wurden – das geht nur dort, wo sie liegen.

Das Studio verlinkt beide auf ihre Seite im Portal **des Zielknotens** und,
wenn der Knoten ihn nennt, auf den automatischen Namen der Instanz
(`<instanz>.<knoten>`). Eine eigene, registrierte Adresse gewinnt in der
Anzeige.

Fehlt die Produktiv-Instanz noch, sagt die Karte, wo sie entsteht: im Portal
des Zielknotens, auf der Seite der Test-Instanz. Findet das Studio auf dem
Knoten eine produktive Instanz, die im Vorhaben nicht eingetragen ist, zeigt
es sie als **Fund** und bittet um den Eintrag.

Über die Knotengrenze reisen die **Pakete**: Ein Deploy-Token oder eine
Anlege-Erlaubnis aus dem Portal des Zielknotens gilt im Studio genauso,
egal wo das Studio steht.

## Zustand der Instanzen (optional)

Ist für den Zielknoten ein **Flotten-Schlüssel** hinterlegt, zeigt die Seite
des Vorhabens, was der Knoten über beide Instanzen sagt: Version, Kanal,
Ampel, veröffentlichte Adressen und offene Punkte, die einen Menschen
brauchen. Kennt der Knoten eine Instanz nicht – nach einem Umzug oder bei
einem Tippfehler im Namen –, steht das ausdrücklich da.

Ohne Schlüssel fehlt genau diese Anzeige und sonst nichts.

### Flotten-Schlüssel einrichten (Betreiber)

Der Schlüssel wird **auf dem Zielknoten** ausgestellt:

```sh
sudo oaap fleet key issue studio@<knoten-des-studios>
```

und auf dem Knoten, auf dem das Studio läuft, in die Konfiguration
eingetragen:

```sh
sudo oaap app config set studio STUDIO_FLEET_KEYS --append '<zielknoten>=<schlüssel>'
```

Mehrere Zielknoten werden mit `;` oder Zeilenumbruch getrennt. Der Wert ist
geheim und wird nie zurückgezeigt.

Ein Flotten-Schlüssel kann **ausschließlich** diese eine Zustandsauskunft
lesen – keine Sitzung, keine Rollen, kein Schreibweg –, und die Auskunft
enthält Fakten, nie Geheimnisse. Das Recht zu *handeln* bleibt beim
Anwender: Der Deploy-Token wird weiterhin bei jedem Upload eingegeben.

Das Studio fragt beim Aufschlagen der Seite nach und hält die Antwort etwa
30 Sekunden vor. Es beobachtet nicht über die Zeit und alarmiert nicht –
dafür gibt es **FleetView**.
