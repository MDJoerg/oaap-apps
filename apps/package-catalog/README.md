# Paketkatalog

Freigegebene ZIP-Pakete mit Versionsverlauf (RFC-0050, Stufen 1 und 2). Der Betreiber lädt Pakete
hoch, gibt Versionen frei und kann jede gespeicherte ZIP wieder herunterladen. Freigegebenes macht
der Knoten zur **Store-Quelle**: Mandanten-Administratoren installieren und aktualisieren es im
Store, wahlweise direkt in Produktion oder mit einer Test-Instanz.

Die App liest nur `oaap-app.yaml` aus einer ZIP, **entpackt nichts und führt nie Code aus**.

## Einrichten (Betreiber, auf dem Knoten)

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/package-catalog --name katalog
sudo oaap store add-catalog katalog --name "Betreiber-Katalog"
```

`add-catalog` trägt **keine Adresse** ein, sondern verweist auf diese Instanz: Der Knoten liest
`store.json` und die Pakete direkt aus ihrem Speicher `data` (der Name des Speichers darf deshalb
nicht geändert werden). Es gibt kein Netz, keinen Schlüssel und nichts, was von außen erreichbar wäre.

## Speicher

| Pfad in `/data` | Inhalt |
|---|---|
| `packages/<app-id>/<version>-<sha12>.zip` | die Pakete, wie hochgeladen |
| `state.json` | Versionen, Notizen, Freigabe, Protokoll |
| `store.json` | die Liste für den Knoten: je App-Id die **höchste freigegebene** Version mit `package{zip, sha256, size}` |

## Regeln

- **Nur `admin`** (Manifest, und die App prüft noch einmal). Ein Paket ist Code, den ein Knoten baut.
- **Hochgeladen ≠ freigegeben.** Was nicht freigegeben ist, steht nicht in `store.json`.
- **Eine Version wird nie überschrieben.**
- **Nicht aufgenommen** wird, was der Knoten beim Entpacken ablehnen würde: Pfade mit `..`,
  absolute Pfade, Verknüpfungen, fehlendes oder ungültiges Manifest, Version keine `x.y.z`.
- Die höchste Version ist nach **Zahlen** geordnet (`1.2.10` vor `1.2.9`, `1.0.0` vor `1.0.0-rc1`).
- Eine **freigegebene** Version zu löschen verlangt einen Grund (bleibt im Protokoll).
- Der Knoten **prüft jedes Paket selbst**: Größe und SHA-256 einer eigenen Kopie, kein Folgen von
  Verknüpfungen, App-Id und Version gegen das Manifest im Paket. Die Datei `store.json` ist eine
  Behauptung, die er nachprüft — kein Befehl.

## Spiegel eines Marketplace (seit 0.2, RFC-0077 §7.2)

Bindet der Betreiber die Destination **`upstream`** (Ziel = ein Marketplace,
Authentifizierung `bearer` mit dem API-Schlüssel eines Maschinenbenutzers
dort), folgt der Katalog dessen Feed: alle 15 Minuten oder mit „Jetzt
abrufen“. Für jede Version, die er noch nicht hat, lädt er das Paket über
die Destination, prüft Größe und SHA-256 gegen den Feed und Id und Version
gegen das Manifest im Paket, und nimmt es dann mit den Regeln oben auf
(nie überschreiben). Mit `auto` gibt er es frei, mit `hold` wartet es auf
den Betreiber. Eine im Marketplace **zurückgerufene** Version wird mit
`auto` zurückgezogen (Grund im Protokoll), nie gelöscht; laufende Instanzen
bleiben unberührt. Der Schlüssel liegt in der Destination, nie im Katalog.
Ohne Destination ist der Katalog genau 0.1. Hochladen von Hand geht weiter.

## Konfiguration

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `CATALOG_MAX_PACKAGE_MB` | größtes Paket (der Knoten nimmt höchstens 256 MB an) | 256 |
| `CATALOG_UPSTREAM_CHANNEL` | Spiegel: Kanal, dem der Katalog folgt | stable |
| `CATALOG_UPSTREAM_RELEASE` | Spiegel: `auto` (beim Eintreffen freigeben) oder `hold` | auto |
| `CATALOG_UPSTREAM_INTERVAL_MIN` | Spiegel: Abstand der Abrufe | 15 |
| `CATALOG_UPSTREAM_SCOPE` | Spiegel: `all` (alles Beziehbare) oder `followed` (nur die Folgen-Liste) | all |

## Aus Zwischenablage hinzufügen (seit 0.3, RFC-0077 §15.8.7)

Im Marketplace „Teilen“ → „Link kopieren“, hier unter „Spiegel“ einfügen. Der
Katalog **ruft den Link nicht auf**: er liest daraus Asset-Kennung und Kanal und
vergleicht die Adresse mit der, die der angebundene Marketplace über die
Destination von sich sagt (Feld `origin` im Feed, ab Marketplace 0.3). Passt sie,
kommt die App auf die Folgen-Liste und wird über die Destination geholt. Ein
fremder Marketplace wird abgelehnt, mit dem Schritt für den Betreiber. Ist die App
für diesen Knoten noch nicht beziehbar (der Anbieter hat noch nicht
freigeschaltet), steht sie auf der Liste und kommt beim ersten Abruf danach.
Wirksam mit `CATALOG_UPSTREAM_SCOPE=followed`.

## Test

```sh
python3 test_package_catalog.py
python3 test_upstream.py      # Spiegel, mit dem echten Marketplace daneben
```

Ohne Docker. Liegt das Gesamt-Repository daneben, prüft der Test auch, dass der **Knoten** die
geschriebene Liste liest (`catalog_source.read_list`) und das Paket an einer Kopie verifiziert.
