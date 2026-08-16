# OAAP Studio

Verwaltung der **Entwicklungsvorhaben**, Erzeugung der **KI-Briefings** —
und seit 0.2 die andere Seite desselben Wegs: das **fertige Paket**, das
aus so einem Briefing entsteht, prüfen und auf die Test-Instanz ausrollen.

Das Studio ist bewusst eine ganz normale OAAP-App aus dem Store und kein
Portal-Bestandteil: Unsere eigenen Funktionen gehorchen denselben Regeln
wie die Apps unserer Anwender (Dogfooding), und das Portal bleibt schlank.

## Was es kann

### Vorhaben und Briefing (seit 0.1.0)

- **Vorhaben verwalten** — Liste (Listenbericht) und Objektseite je Vorhaben,
  mit genau den Angaben, aus denen ein Briefing entsteht: Ziel, Zielanwender,
  Umfang der ersten Version, App-Typ, Status, Repository, Test-Instanz.
- **Briefing erzeugen** — auf Knopfdruck ein vollständiges Markdown-Briefing
  für die KI: fachlicher Auftrag in den Worten des Auftraggebers plus alle
  verbindlichen Plattformregeln (Contract-Verweis, Manifest, Gateway-Login,
  Storage, Oberflächenkonventionen, Deploy-Hook, Postkasten-Regeln).
  Herunterladen als `<vorhaben>-briefing.md`, ins Projektverzeichnis legen,
  KI starten.

### Pakete (seit 0.2.0, RFC-0019)

- **Paket prüfen** — die ZIP hochladen, die die KI abliefert. Das Studio
  liest das Inhaltsverzeichnis und das `oaap-app.yaml` daraus (es entpackt
  **nichts**), hält das Manifest gegen die Plattformregeln und meldet
  vorab, woran der Knoten beim Entpacken abbräche: absolute Pfade, `..`,
  Symlinks.
- **Vorhaben füllen** — App-Kennung, Typ und Version kommen aus dem
  Manifest, statt abgetippt zu werden.
- **Rahmen-Vorschau** — was sich gegenüber dem zuletzt geprüften Paket
  ändert und deshalb eine Bestätigung im Portal braucht: neue öffentliche
  Routen, neue Speicher, neue Ports am Gateway vorbei. Dazu die harten
  Ablehnungsgründe (andere App-Kennung, unveränderte Version).
- **Ausrollen** — der Drei-Phasen-Weg aus RFC-0019 §2 gegen den
  Deploy-Hook der Test-Instanz: anmelden, Freigabe abholen, hochladen.
  Ablehnungen des Knotens stehen wörtlich da, mit einem Satz dazu, was zu
  tun ist.
- **Deployment-Zettel** — das Blatt für die Projekt-KI: Adressen, Ablauf
  als Befehle, alle Ablehnungsgründe, Grenzen. Als Datei zum Herunterladen.

## Bewusste Entscheidungen

- **Keine Deploy-Token im Studio.** Diese Entscheidung aus 0.1 gilt
  wörtlich weiter — auch jetzt, wo das Studio ausrollen kann. Der Token
  wird im Portal erzeugt, gehört dem Anwender (Passwortmanager) und wird
  **bei jedem Upload einzeln eingegeben**. Das Studio hält ihn für die
  Dauer einer Anfrage und legt ihn nirgends ab: nicht in der Datenbank,
  nicht im Backup, nie in einer URL und nie in einer Logzeile (das Gateway
  protokolliert vollständige URIs samt Query-String).
  Wirkung: Im Ruhezustand gibt es hier nichts zu holen, und in jedem
  Deployment über das Studio steckt ein Mensch.
- **Kein Sonderrecht beim Ausrollen.** Das Studio läuft denselben Ablauf
  wie die Projekt-KI, gegen denselben Hook, mit demselben Token — die
  Plattform kann es von jedem anderen Client nicht unterscheiden, und alle
  Prüfungen greifen unverändert. Es ist damit **keine zweite
  Steuerungsebene**.
- **Prüfen heißt Vorschau, nicht Freigabe.** Verbindlich prüft der Knoten,
  noch einmal und vollständig. Die Oberfläche sagt das an jeder Stelle —
  ein Werkzeug, das „geprüft" sagt und dann doch abgelehnt wird, wäre
  schlimmer als eines, das gar nicht prüft.
- **Hochgeladene Pakete liegen nicht unter `/data`.** Sie sind
  Durchgangsware und würden sonst in jedem Backup dieser App landen. Das
  Artefakt aufzuheben ist Aufgabe des Knotens (RFC-0019 §4).
- **Eine Abhängigkeit: PyYAML.** 0.1 kam ohne aus. Seit 0.2 werden fremde
  Manifeste gelesen, und ein selbstgebauter YAML-Leser, der eine
  Schreibweise missversteht, würde ein Paket für gut erklären, das der
  Knoten ablehnt. PyYAML fällt ohne libyaml auf reines Python zurück — der
  Bau auf arm64 hat nichts zu kompilieren.
- **Zusätzlicher Schutz gegen fremde Formulare (CSRF).** Die Plattform
  schützt bereits über das Sitzungs-Cookie (`SameSite=Lax`, `HttpOnly`);
  die App lehnt schreibende Anfragen aus fremdem Ursprung zusätzlich selbst
  ab (`Sec-Fetch-Site`/`Origin`) — doppelter Boden, falls die App einmal
  anders betrieben wird.

## Rollen

`keyuser` und `admin` dürfen Vorhaben sehen und pflegen (Design-Guidelines:
„Studio — keyuser+"), Pakete prüfen und ausrollen; **Löschen** ist `admin`
vorbehalten. Das Gateway erzwingt den Zugang ohnehin, die App prüft
zusätzlich.

Ausdrücklich **nicht** `server_admin`: Das Recht zum Ausrollen ist der
Deploy-Token, den der Anwender im Augenblick der Handlung eingibt — nicht
eine Rolle, die das Studio zur Serververwaltung machte.

## Voraussetzungen fürs Ausrollen

- Eine **Test-Instanz** mit Deploy-Token (Portal → Instanzen →
  Instanzseite). Produktiv-Instanzen haben bewusst kein Token.
- Der Knoten muss die **Hook-Adresse** erreichen können, die im Vorhaben
  steht — das Studio ruft sie aus dem Container heraus auf. Bei einem
  öffentlichen Namen heißt das: Der Router muss die eigene öffentliche
  Adresse zurück ins Netz führen (NAT-Loopback).
- Für ZIP-Deployments ohne bestehende Instanz: Knotenprofil `dev`
  (RFC-0011) und das Anlegen im Portal.

## Installation

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/studio
```

Oder im Portal unter „Store", sobald die Plattform-App-Liste als Quelle
eingetragen ist:

```sh
sudo oaap store add-source https://raw.githubusercontent.com/MDJoerg/oaap-apps/main/oaap-store.json
```

## Konfiguration

| Schlüssel                       | Bedeutung                                                    |
| ------------------------------- | ------------------------------------------------------------ |
| `STUDIO_CONTRACT_URL`           | Adresse des App Deployment Contract (steht im Briefing)      |
| `STUDIO_GIT_BASE`               | Basis-Adresse des eigenen Git-Hostings (z. B. Forgejo)       |
| `STUDIO_MAX_PACKAGE_MB`         | Größtes Paket, Vorgabe 64 (der Knoten nimmt bis 256 MB)      |
| `STUDIO_DEPLOY_TIMEOUT_SECONDS` | Geduld beim Deployment, Vorgabe 180                          |
| `STUDIO_PORTAL_URL`             | Adresse des Portals für Verweise; leer = wird abgeleitet     |

Daten liegen unter dem deklarierten Mount `/data` (SQLite) und werden
damit von `oaap backup create` gesichert. Eine Datenbank aus 0.1 wird beim
Start um die neuen Spalten ergänzt; es geht nichts verloren.

## Prüfungen

Drei Dateien, alle ohne Docker, ohne Knoten und ohne Netz:

```sh
python3 test_pkg.py     # Pakete lesen, Manifest prüfen, Rahmen-Vorschau
python3 test_deploy.py  # Upload-Leser und Drei-Phasen-Weg
python3 test_pages.py   # das Studio am Stück, gegen einen Papierknoten
```

`test_pages.py` startet den echten Handler und eine Gegenstelle, die
denselben Ablauf spricht wie die Plattform — Formular, Upload im Fluss,
Prüfung, drei Phasen, Ergebnisseite.
