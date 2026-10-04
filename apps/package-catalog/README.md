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

## Konfiguration

| Schlüssel | Bedeutung | Standard |
|---|---|---|
| `CATALOG_MAX_PACKAGE_MB` | größtes Paket (der Knoten nimmt höchstens 256 MB an) | 256 |

## Test

```sh
python3 test_package_catalog.py
```

Ohne Docker. Liegt das Gesamt-Repository daneben, prüft der Test auch, dass der **Knoten** die
geschriebene Liste liest (`catalog_source.read_list`) und das Paket an einer Kopie verifiziert.
