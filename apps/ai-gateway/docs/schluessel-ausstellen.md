# Einen Schlüssel ausstellen

Ein Schlüssel gehört zu **einem Verbraucher**: einem Laptop, einem Programm, einem anderen
Gateway.

1. Öffne den Reiter **Schlüssel** und klicke auf **Schlüssel ausstellen**.
2. Gib ein **Etikett** ein, das den Verbraucher benennt, zum Beispiel `laptop@anna`. Es muss
   eindeutig sein.
3. Fülle den **Kontext** aus: Verantwortlich, Projekt, Kostenstelle, Anwendung. Die Felder
   schlagen vor, was schon benutzt wird – so zählt die Auswertung gleiche Projekte zusammen.
4. Optional: nur bestimmte **Modellnamen** erlauben, ein **Budget** in Token, eine Grenze für
   Anfragen je Minute, ein Ablaufdatum.
5. Wähle die höchste erlaubte **Ampelfarbe** (Standard gelb). Kreuze **personenbezogene Daten
   freigegeben** nur an, wenn der Verbraucher solche Daten schickt – dann benutzt der
   Schlüssel nie eine rote Quelle.
6. Klicke auf **Ausstellen**.

Der Schlüsselwert erscheint **genau einmal**. Kopiere ihn sofort; gespeichert ist nur eine
Prüfsumme. Wer ihn verliert, bekommt einen neuen.

## Einen Schlüssel ändern oder widerrufen

Klicke in der Liste auf das Etikett. Auf der Seite des Schlüssels kannst du den Kontext und
die Modellnamen ändern (gilt ab der nächsten Anfrage, der Wert bleibt) oder ihn
**widerrufen** (ab sofort abgelehnt). Für einen widerrufenen oder abgelaufenen Schlüssel gibt
es dort **Neu ausstellen** mit denselben Angaben.

## Den Schlüssel im Programm eintragen

- Basis-Adresse: `https://<adresse-des-gateways>/v1`
- API-Key: der ausgestellte Schlüssel
- Modell: einer der Modellnamen
