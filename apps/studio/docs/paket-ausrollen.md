# Paket prüfen und ausrollen

Hat die KI ein Paket (ZIP) abgeliefert, prüft das Studio es vorab und rollt
es auf die **Test-Instanz** aus. Auf der Seite des Vorhabens:
**Paket prüfen und ausrollen …**

## 1. Paket prüfen

ZIP hochladen. Das Studio liest das Inhaltsverzeichnis und das
`oaap-app.yaml` daraus – es entpackt **nichts** – und meldet:

- **Befunde am Manifest** gegen die Plattformregeln,
- was der Knoten beim Entpacken **ablehnen** würde: absolute Pfade, `..`,
  symbolische Links,
- die **Rahmen-Vorschau**: was sich gegenüber dem zuletzt geprüften Paket
  ändert und deshalb eine Bestätigung im Portal braucht – neue öffentliche
  Routen, neue Speicher, neue Ports am Gateway vorbei,
- **harte Ablehnungsgründe**: andere App-Kennung, unveränderte Version.

App-Kennung, Typ und Version übernimmt das Studio aus dem Manifest ins
Vorhaben, statt sie abtippen zu lassen.

**Prüfen heißt Vorschau, nicht Freigabe.** Verbindlich prüft der Knoten noch
einmal und vollständig.

## 2. Ausrollen

Für das Ausrollen brauchst Du:

- den **Deploy-Hook** der Test-Instanz (steht im Vorhaben),
- einen **Deploy-Token** der Test-Instanz – oder, wenn es die Instanz noch
  nicht gibt, eine **Anlege-Erlaubnis** (siehe
  [Deploy-Token und Anlege-Erlaubnis](deploy-token.md)).

Den Token gibst Du bei **jedem** Upload ein; das Studio speichert ihn nicht.

Das Studio geht dann den Weg in drei Phasen, genau wie die KI es täte:

| Phase | Was passiert |
|---|---|
| 1 · Anmelden | Version, Manifest, Prüfsumme und Größe gehen an den Knoten. Er prüft, **bevor** etwas übertragen wird, und gibt bei Erfolg eine Freigabe (15 Minuten, nur für diese Instanz und dieses Paket). |
| 2 · Hochladen | Das Paket geht mit der Freigabe hinüber. Der Knoten prüft erneut: Größe, Prüfsumme, und ob das Manifest in der ZIP zeichengleich zum angemeldeten ist. |
| 3 · Nachsehen | Das Studio fragt nach, wie das Ausrollen steht. |

Die Ergebnisseite zeigt jede Phase mit der Antwort des Knotens **wörtlich**
und einem Satz dazu, was zu tun ist.

## Wenn eine Bestätigung nötig ist

Erweitert ein Paket den Rahmen (neue öffentliche Route, neuer Speicher, neuer
Port am Gateway vorbei), lehnt der Knoten zunächst ab. Ein `server_admin`
bestätigt das im Portal auf der Seite der Instanz.

**Danach dasselbe Paket unverändert erneut ausrollen – die Version nicht
hochzählen.** Die Bestätigung gilt für genau das abgelehnte Manifest. Ein
hochgezähltes Paket ist ein anderes Manifest, die Bestätigung deckt es nicht,
und es dreht sich im Kreis. Die Regel „unveränderte Version wird abgelehnt“
greift hier nicht: Sie vergleicht mit dem, was *installiert* ist.

## „Ausgang unklar“

Bleibt eine Antwort aus – ein kleiner Knoten baut manchmal länger, als das
Studio wartet –, sagt das Studio „Ausgang unklar“ statt „abgelehnt“. Der
Knoten rollt derweil weiter aus; maßgeblich ist sein Protokoll im Portal.
Die Wartezeit stellt der Betreiber mit `STUDIO_DEPLOY_TIMEOUT_SECONDS` ein
(Vorgabe 180 Sekunden).

## Deployment-Zettel für die KI

Soll die KI selbst ausrollen, gib ihr den **Deployment-Zettel …** des
Vorhabens: Adressen, Ablauf als Befehle, alle Ablehnungsgründe, Grenzen. Den
Deploy-Token übergibst Du getrennt davon.

## Grenzen

- Größtes Paket: `STUDIO_MAX_PACKAGE_MB` (Vorgabe 64 MB; der Knoten nimmt
  bis 256 MB).
- Hochgeladene Pakete liegen nur vorübergehend im Studio und nicht im
  Backup. Das Paket aufzuheben ist Sache des Knotens.
- Der Knoten, auf dem das Studio läuft, muss die Adresse des Deploy-Hooks
  erreichen können.
