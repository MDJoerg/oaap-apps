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
- **Erste Instanz anlegen** (seit 0.2.1) — gibt es die Instanz noch nicht,
  gibt es auch keinen Deploy-Token. Dann stellt ein `server_admin` im
  Portal eine **Anlege-Erlaubnis** für genau diesen Namen aus (einmal
  verwendbar, 30 Minuten, Test-Kanal, widerrufbar) und der Anwender trägt
  sie statt des Tokens ein. Für das Studio ändert sich dabei **nichts**:
  derselbe Drei-Phasen-Weg, dieselben Prüfungen. Danach entsteht die
  Instanz, die Erlaubnis ist verbraucht, und jede weitere Fassung läuft
  über ein normales Deploy-Token.
- **„Ausgang unklar" statt falscher Ablehnung** — bleibt eine Antwort aus
  (ein kleiner Knoten baut länger, als das Studio wartet), sagt das Studio
  das, statt „abgelehnt" zu behaupten: Der Knoten rollt derweil weiter aus,
  und sein Protokoll ist das verbindliche.

### Verteilte Test-Instanzen (seit 0.3.0)

Anlass war ein echter Durchlauf: Am 23.08.2026 hat Jörg aus dem Studio auf
`oaap-demo` heraus eine Test-Instanz auf `oaapx01` angelegt und sie dort
produktiv gesetzt — über eine Knotengrenze hinweg, nur mit Portal und
Studio. Funktioniert hat das (die Anlege-Erlaubnis und das Paket reisen
ohnehin), aber das Studio wusste nichts davon: Es leitete seine
Portal-Verweise aus dem **eigenen** Hostnamen ab und zeigte damit auf
einen Knoten, auf dem die Instanz nicht liegt.

- **Der Zielknoten ist ein Begriff.** Er steht auf der Seite des
  Vorhabens, mit der Quelle dazu: aus dem **Deploy-Hook** (dorthin gehen
  die Pakete tatsächlich — was dort steht, gilt), aus dem Feld
  **„Zielknoten"** (für die Zeit, bevor es einen Hook gibt) oder als
  ausgewiesene **Vermutung** „der Knoten, auf dem dieses Studio läuft".
  Widersprechen sich Hook und Feld, gewinnt der Hook — und der
  Widerspruch wird benannt statt stillschweigend aufgelöst.
- **Zwei Instanzen je Vorhaben.** Neben der Test-Instanz steht jetzt die
  **Produktiv-Instanz**; beide verlinken auf ihre Seite im Portal *des
  Zielknotens*. Fehlt die produktive noch, sagt die Karte, wo sie
  entsteht (Portal des Zielknotens, Seite der Test-Instanz, RFC-0020) und
  wie sie üblicherweise heißt.
- **Zustand beider Instanzen** — optional, über die lesende
  Flotten-Auskunft des Zielknotens (`oaap.fleet.status`, RFC-0021):
  Version, Kanal, Ampel, veröffentlichte Adressen samt DNS-Urteil des
  Knotens und die `attention`-Einträge, die genau diese Instanzen
  betreffen. Sagt der Knoten einen anderen Kanal als erwartet, steht das
  daneben. Eine Instanz, die der Knoten **nicht kennt**, wird als solche
  ausgewiesen — nach einem Umzug oder bei einem Tippfehler ist das genau
  die Auskunft, die man braucht.
- **Zielknoten in Zettel und Briefing.** Beide Blätter werden auf einem
  fremden Rechner gelesen; sie nennen deshalb die Adresse des Knotens,
  die Seiten beider Instanzen und den Satz, dass ein Studio auf einem
  anderen Knoten daran nichts ändert.

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
- **Der Flotten-Schlüssel ist kein Bruch dieser Regel — aber er braucht
  eine Begründung.** Seit 0.3 kann das Studio einen Schlüssel *halten*:
  je Zielknoten einen Flotten-Schlüssel, um den Zustand der beiden
  Instanzen zu lesen. Die Regel oben meint das Recht, etwas zu
  **verändern**, und genau das bleibt beim Anwender — der Deploy-Token
  wird weiterhin bei jeder Handlung eingegeben. Ein Flotten-Schlüssel
  kann laut `oaap.fleet.status` §2 **ausschließlich** `GET /fleet/status`
  lesen: keine Sitzung, keine Rollen, kein anderer Weg, kein Schreibweg;
  und was er liest, sind laut §3.1 Fakten, nie Geheimnisse (keine Tokens,
  keine Konfigurationswerte, keine Quell-URLs). Er ist damit nicht mehr
  wert als ein Blick auf die Gesundheitsseite. Ihn **nicht** zu
  hinterlegen ist eine gültige Betriebsart: Dann fehlt genau diese
  Anzeige und sonst nichts.
- **Das Studio ist kein zweites FleetView.** Es pollt nicht im Takt und
  hebt keinen letzten bekannten Stand auf: Es fragt beim Aufschlagen der
  Seite nach, hält die Antwort ~30 s vor (damit ein Seitenaufbau nicht
  jedes Mal an einem fernen Knoten hängt) und sagt ehrlich, wenn es keine
  bekam. Wer die Landschaft über die Zeit beobachten will, nimmt
  FleetView (RFC-0021 §3). Der Preis dafür ist bewusste Doppelung: Beide
  Apps haben ihr eigenes `fleet.py`, weil jede App ihr eigener
  Bau-Kontext ist — eine gemeinsame Bibliothek ist notiert, aber sie
  wäre heute die vierte Baustelle für zwei Leser.
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
eine Rolle, die das Studio zur Serververwaltung machte. Dasselbe gilt für
die Anlege-Erlaubnis: Sie wird **gegeben**, nicht gehalten. Einheitliche
Regel: **Das Studio hält nie ein Recht — alles Privilegierte gibt der
Anwender im Augenblick der Handlung.**

## Voraussetzungen fürs Ausrollen

- Eine **Test-Instanz** mit Deploy-Token (Portal → Instanzen →
  Instanzseite) — oder, wenn es sie noch nicht gibt, eine
  **Anlege-Erlaubnis** aus derselben Liste. Produktiv-Instanzen haben
  bewusst kein Token.
- Der Knoten muss die **Hook-Adresse** erreichen können, die im Vorhaben
  steht — das Studio ruft sie aus dem Container heraus auf. Bei einem
  öffentlichen Namen heißt das: Der Router muss die eigene öffentliche
  Adresse zurück ins Netz führen (NAT-Loopback).
- Für ZIP-Deployments ohne bestehende Instanz: Knotenprofil `dev`
  (RFC-0011). Das Anlegen geht dann entweder im Portal oder aus dem
  Studio heraus mit einer Anlege-Erlaubnis.

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
| `STUDIO_PORTAL_URL`             | Adresse des eigenen Portals; leer = wird abgeleitet          |
| `STUDIO_FLEET_KEYS`             | geheim, optional: Flotten-Schlüssel je Zielknoten            |

`STUDIO_FLEET_KEYS` nimmt `knoten=schlüssel`-Einträge, getrennt durch `;`
oder Zeilenumbruch (`oaap.joomp.de=…`). Der Schlüssel wird **auf dem
Zielknoten** ausgestellt und hier eingetragen:

```sh
# auf dem Zielknoten, an der Maschine:
sudo oaap fleet key issue studio@oaap-demo

# auf dem Knoten, auf dem das Studio läuft:
sudo oaap app config set studio STUDIO_FLEET_KEYS \
  --append 'oaap.joomp.de=<schlüssel>'
```

`--append` (Runtime-Spec 0.2.15) hängt einen Knoten an, ohne die
bestehende geheime Liste neu eintippen zu müssen. Zurückgezeigt wird der
Wert nie; die Oberfläche weiß nur, für welche Knoten etwas hinterlegt
ist.

Daten liegen unter dem deklarierten Mount `/data` (SQLite) und werden
damit von `oaap backup create` gesichert. Eine Datenbank aus 0.1 oder 0.2
wird beim Start um die neuen Spalten ergänzt; es geht nichts verloren.
`instance` heißt in der Datenbank weiter so und meint die **Test**-Instanz
— der Name stammt aus 0.1, als es keine zweite gab; ihn umzubenennen hieße
eine Datenbank wandern lassen, ohne dass ein Anwender etwas davon hätte.

## Prüfungen

Vier Dateien, alle ohne Docker, ohne Knoten und ohne Netz:

```sh
python3 test_pkg.py     # Pakete lesen, Manifest prüfen, Rahmen-Vorschau
python3 test_deploy.py  # Upload-Leser und Drei-Phasen-Weg
python3 test_fleet.py   # Zielknoten, Flotten-Schlüssel, Vorhalten
python3 test_pages.py   # das Studio am Stück, gegen einen Papierknoten
```

`test_pages.py` startet den echten Handler und eine Gegenstelle, die
denselben Ablauf spricht wie die Plattform — Formular, Upload im Fluss,
Prüfung, drei Phasen, Ergebnisseite.
