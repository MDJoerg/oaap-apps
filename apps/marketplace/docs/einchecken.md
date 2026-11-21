# Ein Asset einchecken

Assets kommen über die Schnittstelle in den Marktplatz – von einem Skript
oder einer KI, nicht über ein Formular.

## Vorbereitung (einmal)

1. Im Portal einen **Maschinenbenutzer** anlegen (zum Beispiel `ci`) und unter
   **Zugänge** einen API-Schlüssel ausstellen.
2. In der Verwaltung unter **Rechte → Veröffentlichen dürfen** ein Muster für
   die Kennung (zum Beispiel `oaap-*`) und diesen Benutzer eintragen.

## Einchecken

```sh
python3 tools/publish.py --url https://markt.example --key-file ~/.oaap/ci.key \
    meine-app-1.2.0.zip --channels beta
```

- Dieselbe Version mit denselben Bytes noch einmal: nichts Neues.
- Dieselbe Version mit anderen Bytes: abgelehnt. Eine Version wird nie
  wiederverwendet, auch nach einem Rückruf nicht.
- Kennung und Version einer OAAP-App oder Aufbau-Vorlage kommen aus dem Paket
  selbst; bei einer Datei gibst du beides an.

## Einen Ordner abgleichen

```sh
python3 tools/publish.py --url https://markt.example --key-file ~/.oaap/ci.key \
    sync vorlagen/handballverein
```

`market.json` im Ordner beschreibt das Asset: Datei, Kanäle, Landschaften,
Kategorien, Schlagworte, Texte je Sprache, Notizen, Bilder. Ein zweiter Lauf
ändert nichts.

## Die Geheimnis-Sperre

Vor jedem Speichern prüft der Marktplatz Pakete, Texte und Vorlagen auf
Geheimnisse: Schlüsseldateien, Tokens, Passwörter als wörtliche Werte. Findet
er etwas, lehnt er ab und nennt Ort und Regel – nie den Wert selbst. In einer
Aufbau-Vorlage steht statt eines Passworts `generate: true` oder ein
Platzhalter.
