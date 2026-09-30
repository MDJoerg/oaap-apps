# Code-Server als OAAP-App — VS Code im Browser, ein Container je Mensch

Der erste Baustein der **Schulungslandschaft** (Ideenspeicher
30.09.2026): Teilnehmer aus größeren Unternehmen dürfen auf ihren
Laptops nichts installieren, also stellen wir den Arbeitsplatz — VS
Code läuft auf dem Knoten, im Browser sieht man ihn nur. Dieselbe App
taugt für jeden, der eine vorbereitete Entwicklungsumgebung ohne
Installation braucht.

## Warum das eine gewöhnliche App ist

Weil OAAP dann alles Weitere schon kann: eine Instanz je Teilnehmer mit
eigener Ablage, eigenem Instanznetz (RFC-0016) und eigener Adresse
(`<instanz>.<mandant>.<knoten>`); Anmeldung am Gateway statt eines
zweiten Passworts; Sichtbarkeit je Instanz über Gruppen (Runtime 2.7),
sodass jeder Teilnehmer im Startfeld nur *seinen* Arbeitsplatz sieht;
Sicherung, Neuaufsetzen (`--purge` + neu installieren) und Abräumen mit
den vorhandenen Befehlen.

Die **Coder-Plattform** (vom selben Hersteller) haben wir bewusst nicht
genommen: Sie ist selbst eine Plattform mit eigenen Benutzern, eigenem
Proxy und Docker-Socket — eine zweite Ausgabe der Arbeit, die OAAP hier
tut, und der Docker-Socket bräche die Isolation aus RFC-0016.

## Was drin ist

| | |
| --- | --- |
| Basis | `ghcr.io/coder/code-server:4.139.1` (Debian 13), **festgenagelt** — prüfbar mit `code-server --version` im Terminal |
| Benutzer | `coder` (uid 1000), `sudo` ohne Passwort — abschaltbar (`IDE_SUDO`) |
| Werkzeuge | git, Node.js 20 + npm, Python 3, jq, zip/unzip, ssh-Client |
| Erweiterungen | aus **Open VSX** (nicht Microsoft-Marktplatz), vorinstalliert: `Anthropic.claude-code`, `murbani.vscode-abap-remote-fs`, `SAPSE.vscode-cds` |
| Anmeldung | das Gateway (`--auth none` in code-server); Route `/` für `user`, `keyuser`, `admin` |
| Ablage | `home` → `/home/coder` (Projekte, Einstellungen, Erweiterungen); `material` → `/home/coder/material` (Kursmaterial, getrennt) |
| Gesundheit | `/healthz`, 90 s Anlaufzeit |

Die Erweiterungen werden **zur Bauzeit** nach `/opt/oaap/seed` geholt
und beim ersten Start ins Zuhause kopiert. Direkt ins Zuhause zu
installieren ginge nicht: `/home/coder` ist ein Storage-Mount und beim
ersten Start leer. Nachträglich fehlende Saat-Erweiterungen werden bei
jedem Start ergänzt; was der Teilnehmer selbst entfernt oder
aktualisiert hat, bleibt seine Sache.

## Installieren

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps \
  --path apps/code-server --name ide-07 --channel test --tenant kurs1
```

Der Bau zieht das Basis-Abbild und drei Erweiterungen aus dem Netz und
dauert beim ersten Mal einige Minuten; jede weitere Instanz derselben
Version nutzt das gebaute Abbild.

| Konfiguration | Bedeutung |
| --- | --- |
| `IDE_EXTENSIONS` | Weitere Erweiterungen, eine je Zeile: eine Open-VSX-Kennung (z. B. `SAPSE.sap-ux-fiori-tools-extension-pack`) oder seit 0.1.1 die https-Adresse einer `.vsix`-Datei, die der Hersteller selbst verteilt (ABAP cleaner: `https://github.com/SAP/abap-cleaner/releases/latest/download/abapcleaner-vscode-linux.gtk.x86_64.vsix`). Werden beim Start nachinstalliert, wenn sie fehlen — best effort, das Container-Log sagt, was nicht ging. Eine `.vsix` wird einmal geholt und gemerkt. |
| `IDE_SUDO` | `ja` (Standard) oder `nein`. Bei `nein` entfernt der Start die sudo-Regel. |
| `ANTHROPIC_API_KEY` | Optional, geheim. Erreicht Terminal und Claude-Plugin als Umgebungsvariable; leer heißt: der Teilnehmer meldet sich selbst an. |
| `ANTHROPIC_BASE_URL` | Optional, für ein späteres Anthropic-kompatibles Gateway (RFC-0023). |
| `TZ` | Zeitzone, Standard `Europe/Berlin`. |

Eine Instanz auf einen Teilnehmer beschränken:

```sh
sudo oaap app visibility ide-07 groups kurs1-tn07
```

Der Teilnehmer trägt die Gruppe `kurs1-tn07` an seinem Benutzer; der
Trainer sieht als `tenant_admin` ohnehin alles.

## Im Browser

- Ein Terminal ist ein WebSocket; das Gateway trägt ihn durch die
  Forward-Auth (RFC-0010). Ein Firmen-Proxy, der WebSockets über 443
  nicht durchlässt, ist die eine Stelle, an der dieses Szenario scheitern
  kann — vom echten Firmennetz aus messen, nicht vermuten.
- Der Browser fängt einige Tastenkürzel ab (`Strg+W`). code-server bietet
  „App installieren" (PWA) an; das braucht keine Rechte auf dem Laptop.
- Ein Entwicklungsserver im Terminal (etwa CAP auf 4004) ist unter
  `…/proxy/4004/` derselben Adresse erreichbar — durch dieselbe Anmeldung.

## Was zur Schulung noch fehlt (bewusst nicht in dieser App)

Alles davon ist Plattformarbeit, nicht Sache dieser App — gesammelt im
Ideenspeicher unter „Schulungsumgebung im Browser":

- das Werkzeug, das X Benutzer und X Instanzen mit Präfixen anlegt,
  Nachzügler ergänzt, eine Instanz neu aufsetzt und alles abräumt (die
  „Kohorte", Kandidat RFC-0046);
- das Füllen von `material` aus einem Verzeichnis des Trainers;
- Ressourcengrenzen je Instanz (die Referenz setzt heute keine);
- Benutzer löschen statt nur deaktivieren;
- ein KI-Schlüssel je Teilnehmer mit Budget.

## Gemessen (oaap-test, Referenz 0.1.143, 2026-09-30)

Installiert aus dem lokalen Paket als `ide-test` (Kanal test, Port 8115).
Der erste Bau zog Basis-Abbild und Erweiterungen; das Abbild ist 2,3 GB
groß, der Container braucht im Leerlauf rund 160 MB RAM.

| Was | Ergebnis |
| --- | --- |
| Erster Start | Log: „Erweiterungen aus der Saat übernommen"; `code-server 4.139.1`, Node v20.19.2, Python 3.13.5, `sudo -n true` als `coder` erfolgreich |
| Erweiterungen im Zuhause | claude-code 2.1.285, vscode-abap-remote-fs 2.10.2, vscode-cds 10.1.1 — plus deren Abhängigkeiten larshp.vscode-abap und hudakf.cds, die Open VSX mitzog |
| `/healthz` | `{"status":"alive"}` mit 200 durchs Gateway; ohne Sitzung `303 → /auth/login` |
| Seite | nach Anmeldung `200`, das Workbench-HTML, Weiterleitung auf `?folder=/home/coder/projects` |
| **WebSocket** | Upgrade-Anfrage mit Sitzung: **`101`**; dieselbe ohne Sitzung: `303`. Die Forward-Auth trägt den Terminal-Kanal |
| `IDE_EXTENSIONS` | `MS-CEINTL.vscode-language-pack-de` gesetzt → Container neu erzeugt, Log „Installiere Erweiterung …", danach `ms-ceintl.vscode-language-pack-de-1.131.0` im Zuhause, Gesundheit wieder `alive` |
| Sichtbarkeit je Teilnehmer | zwei Wegwerf-Benutzer mit Rolle `user`: `ide-tn01` (Gruppe `ide-tn01`) und `ide-tn02` (keine Gruppe). `visibility groups ide-tn01`: tn01 `200` und im Arbeitsplatz, **tn02 `403` schon bei der Anmeldung**. Nach `visibility all`: tn02 `200`. Beide Probe-Benutzer danach deaktiviert |
| Storage | `…/tenants/<uuid>/instances/<id>/storage/home → /home/coder` und `…/storage/material → /home/coder/material`, beide dem Abbild-Benutzer 1000 übereignet |

Nicht gemessen, weil es einen echten Browser und Menschen braucht: das
Terminal tippen, das Claude-Plugin anmelden, ABAP Remote FS gegen ein
SAP-System, und der Weg durch einen Firmen-Proxy.

**Nachtrag 0.1.1 (gleicher Tag):** `IDE_EXTENSIONS` mit der
GitHub-Adresse des ABAP cleaner gesetzt → Log „Hole …", „… installiert",
danach `saposs.abap-cleaner-1.29.0` (188 MB, bringt seine Laufzeit im
Ordner `binary` selbst mit; kein Java im Abbild nötig), Merkzettel in
`~/.cache/oaap-vsix`, Gesundheit `alive`. Nur x86_64: SAP baut keine
aarch64-Fassung, auf einem Raspberry Pi bliebe der Eintrag eine Warnung
im Log.

## Auf oaapx01 mit echtem TLS (30.09.2026)

Instanz `ide` (Kanal test, Port 8119) aus demselben Paket; der Knoten
hat ihr nach RFC-0043 von selbst `https://ide.oaap.joomp.de/` gegeben
(Let's-Encrypt-Zertifikat, ausgestellt beim ersten Aufruf). Ohne
Sitzung `303 → /auth/login`, WebSocket-Anfrage ohne Sitzung ebenfalls
303. Das ist die Adresse, an der Webviews (Claude-Fenster, Markdown-
Vorschau, CDS-Ansichten) im echten Browser zu messen sind.

## SAP-Erweiterungen, die nicht auf Open VSX liegen (gemessen 30.09.)

**ABAP cleaner (`saposs.abap-cleaner`) — im Browser nicht möglich.**
Sein Manifest sagt `extensionKind: ["ui"]` („nur Desktop"), und der Grund
steht dahinter: Die Erweiterung startet eine Eclipse-/SWT-Anwendung mit
eigener Java-Laufzeit (`binary/abapcleaner`, JustJ-JRE 21). Headless im
Container gestartet: `SWT OS.java Error … Failed to load swt-pi3`, Exit
13 — sie braucht ein Fenster. Auch die Umschaltung `remote.extensionKind`
auf `workspace` ändert daran nichts. Alternative für Formatierung ohne
Fenster wäre ABAP cleaners Kommandozeile in einem eigenen Werkzeug,
nicht diese Erweiterung.

**ADT for VS Code (`sapse.adt-vscode` 1.1.2) — technisch läuft es.**
Das Paket ist vom Microsoft-Marktplatz als `linux-x64`-VSIX beziehbar
(gzip-verpackt, 97 MB). `code-server --install-extension` nimmt es,
`extensionKind: ["workspace"]` passt zum Server. Beim ersten Befehl
(„ABAP: New Destination…") startet ein Eclipse-Sprachserver `adt-ls`
mit **mitgebrachter SapMachine-JRE 21** — kein Java im Abbild nötig —,
die Statusleiste zeigt „ABAP: Running", der Assistent bietet RFC
(On-Premise/Private Cloud) oder HTTP (BTP/Public Cloud). Der Sprachserver
kostet rund 230 MB RSS, der Container mit ADT, CDS und Claude lag bei
1,9 GB. Die Erweiterung liest `appName`/`uiKind` nur für ihre
Diagnose, sie verweigert code-server nicht. **Offen und nicht unsere
Entscheidung:** SAP verteilt sie nur über den Microsoft-Marktplatz unter
SAP-Developer-Lizenz, und dessen Bedingungen erlauben den Bezug nur für
Microsofts eigene Produkte. Gegen ein SAP-System wurde nicht verbunden.

### ADT: Destinationen und der eingebettete MCP-Server (30.09., oaapx01)

**Destinationen liest ADT aus `~/.adtls/destinations.json`**, nicht aus
SAP Logon; die Datei legt der Sprachserver beim ersten Start leer an
(`{"formatVersion":"1.0","destinations":[]}`). Deshalb ist „ABAP: Add
Destination as Folder to Workspace…" anfangs leer — sie listet nur, was
schon da ist. Anlegen tut „**ABAP: New Destination…**" (Assistent: RFC
für On-Premise/Private Cloud, HTTP für BTP/Public Cloud), Bearbeiten
„**ABAP: Open destinations.json**". Aus den Klassen des Sprachservers
gelesen (`ILsDestinationData`), nicht aus einer vom Assistenten
geschriebenen Datei: ein Eintrag hat `id`, `protocol` (`rfc` | `http`)
und `properties` mit `systemId`, `client`, `user`, `language`,
`applicationServer`, `systemNumber`, `messageServer`,
`messageServerPort`, `group`, `gatewayServer`, `gatewayServerPort`,
`sapRouterString`, `sncType`, `ssoEnabled`, `systemUrl`,
`authenticationKind`. Für eine Kohorte heißt das: **die Datei wird je
Teilnehmer gesät** (Material/Vorlage), kein eigenes VSIX nötig; Passwörter
gehören nicht hinein, die fragt ADT beim Anmelden.

**MCP-Server:** Einstellung `adt.mcpServer.enabled` (Standard aus), Port
`adt.mcpServer.port` (2236), das Token erzeugt der Server selbst und
schreibt es in `adt.mcpServer.token`. Technisch ein Jetty-HTTP-Server
auf `localhost:<port>/mcp` **im Container**, mit Token-Filter und
DNS-Rebinding-Schutz — erreichbar also für Claude Code im selben
Container, nicht vom Laptop. Start läuft als LSP-Anfrage
`adtLs/mcp/startMCPServer` an den Sprachserver; scheitert sie, zeigt die
Erweiterung den Grund nur als Warnmeldung im Fenster (`PortNotAvailable`
ist einer der Gründe, die der Kern kennt). Im Fehlerprotokoll des
Sprachservers stand am 30.09. kein Startversuch, nur „ADT MCP Server is
not running"; die Einstellung war nicht gesetzt. Sie steht in der
Instanz `ide` jetzt auf `true`. *Nachtrag:* Jörg hatte ihn selbst
eingeschaltet; er lief an, stoppte nach Sekunden — und läuft seit dem
nächsten Anlauf (Log: „MCP Server started successfully on port 2236",
20 Werkzeuge). Dazwischen steht zweimal „Invalid host:
ide.oaap.joomp.de": der DNS-Rebinding-Schutz weist Anfragen ab, die
nicht mit `Host: localhost` kommen — der Server ist nur für Klienten
**im Container** gedacht.

**Gemessene Form einer HTTP-Destination** (vom Assistenten geschrieben):

```json
{"id": "ABAP_CLOUD_TRIAL", "protocol": "http",
 "properties": {"authenticationKind": "reentranceticket",
                "systemUrl": "https://<instanz>.abap.<region>.hana.ondemand.com"}}
```

**„Add Destination as Folder to Workspace" hat funktioniert — nur
unsichtbar.** VS Code kann einem Ein-Ordner-Fenster keinen zweiten
Ordner geben, ohne daraus einen **unbenannten Mehr-Ordner-Arbeitsbereich**
zu machen; der liegt unter `User/Workspaces/Untitled-….code-workspace`
und enthält `abap:/repotree-v1/ABAP_CLOUD_TRIAL`. Wird das Fenster dann
mit der Adresse `?folder=/home/coder/projects` neu geladen, ist er weg.
Abhilfe: Arbeitsbereich speichern (Datei → Arbeitsbereich speichern
unter…) und über `?workspace=<pfad>` öffnen. In `ide` liegt dafür
`~/projects/abap-trial.code-workspace`, Adresse
`https://ide.oaap.joomp.de/?workspace=/home/coder/projects/abap-trial.code-workspace`.
Für eine Kohorte: der Arbeitsbereich ist Teil der Saat, und die
Startadresse zeigt auf ihn statt auf den Ordner.

**RFC-Destination: „No system configurations found."** Der Sprachserver
sagt es im Fehlerprotokoll genauer: *„Local and global SAP UI Landscape
files not found. Use SAP GUI to configure them or use System Connections
preferences to set them manually."* ADT kennt RFC-Systeme nur aus der
Landschaftsdatei von SAP GUI (`SAPUILandscape.xml`); der Assistent
bietet keine manuelle Eingabe. Zwei Wege ohne SAP GUI, beide aus den
Klassen des Sprachservers gelesen (`SapUiLandscapeReader`,
`DestinationModelPreferences`):

1. **Umgebungsvariable `SAPLOGON_LSXML_FILE`** mit dem Pfad der Datei —
   seit 0.1.2 setzt der Entrypoint sie selbst, wenn
   `~/material/SAPUILandscape.xml` oder `~/.adtls/SAPUILandscape.xml`
   existiert. Das ist der Weg für Kohorten: die Datei wird gesät.
2. **Eclipse-Voreinstellung** des Sprachservers, Knoten
   `com.sap.adt.destinations.model`: `overrideXmlLocations=true`,
   `xmlLocalPath=<pfad>`, `xmlGlobalPath=` — als `.prefs`-Datei unter
   `<adtWorkspace>/.metadata/.plugins/org.eclipse.core.runtime/.settings/`.
   Das Arbeitsverzeichnis hängt am VS-Code-Arbeitsbereich, also je
   Arbeitsbereich eine Datei. In `ide` am 30.09. für beide vorhandenen
   gesetzt, zusammen mit einer Beispiel-Landschaftsdatei
   (`~/.adtls/SAPUILandscape.xml`, System `A4H`,
   `a4h.example.invalid:3200`); wirkt nach dem nächsten Start des
   Sprachservers (Fenster neu laden). **Ob der Assistent das Beispiel
   dann listet, ist noch nicht gemessen.**

*Nachtrag:* Jörg meldet den RFC-Weg als funktionierend; jeder neue
VS-Code-Arbeitsbereich bekommt ein neues Sprachserver-Arbeitsverzeichnis
und braucht die `.prefs`-Datei erneut (am 30.09. ein drittes Mal
nachgetragen) — genau der Grund, warum 0.1.2 die Umgebungsvariable nimmt.

**HTTP-Anmeldung (Reentrance-Ticket) über die Browser-Schranke.** Die
Anmeldung an einer ABAP-Cloud-Instanz läuft so: Der Sprachserver startet
im Container einen kleinen Jetty-Server auf `localhost:<zufälliger
Port>` (`AdtLogonHttpServer`), schickt den Browser zu
`…/sap/bc/adt/core/http/reentranceticket?redirect-url=http://localhost:<port>/adt/redirect`
und wartet („Waiting for logon in external browser…"). Nach der
Anmeldung leitet das SAP-System den Browser auf diese
`localhost`-Adresse mit `?reentrance-ticket=…` um — auf dem Laptop des
Teilnehmers ins Leere, denn der Server läuft im Container. Eine
Einstellung für einen anderen Rücksprung-Host gibt es nicht, und die
Erweiterung nutzt nicht `vscode.env.asExternalUri` (die VS-Code-API, die
genau diesen Fall für entfernte Umgebungen löst — das wäre die Rückmeldung
an SAP).

Die Brücke ist code-servers eigener Port-Proxy: `/proxy/<port>/…`
derselben Adresse wird im Container an `127.0.0.1:<port>` weitergereicht,
das Präfix wird entfernt, die Abfrageparameter bleiben. **Gemessen im
Container am 30.09.:** Horcher auf `127.0.0.1:36511`, Aufruf
`http://localhost:8080/proxy/36511/adt/redirect?reentrance-ticket=T1` →
`200`, der Horcher sah `/adt/redirect?reentrance-ticket=T1`; ein Horcher
nur auf `::1` wird nicht erreicht (`ECONNREFUSED`, 500). Von außen
schützt die Gateway-Sitzung den Weg. Zwei Handgriffe, beide ungemessen
gegen ein echtes SAP-System:

1. **Vorher:** in der kopierten Anmelde-Adresse den Wert von
   `redirect-url` von `http%3A%2F%2Flocalhost%3A<port>%2Fadt%2Fredirect`
   auf `https%3A%2F%2Fide.oaap.joomp.de%2Fproxy%2F<port>%2Fadt%2Fredirect`
   ändern — wenn das SAP-System einen fremden Rücksprung-Host zulässt,
   läuft alles durch.
2. **Nachher, geht immer:** die Anmeldung bis zur scheiternden
   `localhost`-Seite laufen lassen, dann in der Adresszeile
   `http://localhost:<port>` durch `https://ide.oaap.joomp.de/proxy/<port>`
   ersetzen und Enter. Das Ticket ist kurzlebig, also zügig.

Ersatzweg ohne Browser-Kunststück: die scheiternde `localhost`-Adresse
kopieren und im Terminal der IDE mit `curl` aufrufen — der Sprachserver
bekommt das Ticket ebenso.

Für die Kohorte bleibt das ein Reibungspunkt, kein Blocker: eine
Lesezeichen-Schaltfläche für Teilnehmer oder eine kleine Hilfsseite in
der IDE, die den Tausch vornimmt; die saubere Lösung liegt bei SAP.

Die Landschaftsdatei ist das Format von SAP GUI 7.40+: `Landscape` →
`Workspaces/Workspace/Item` (verweist per `serviceid`) und
`Services/Service type="SAPGUI"` mit `systemid`, `server="host:32NN"`
(Anwendungsserver) oder `msid` auf einen `Messageservers/Messageserver`
(Gruppenanmeldung), optional `routerid` auf `Routers/Router`.

## Warum das Claude-Plugin über `http://` nicht antwortet

Das Plugin ist aktiv und sein Programm liegt im Container (Log
„Claude code extension is now active", 240 MB `native-binary/claude`);
was es dreimal meldet, ist „No authentication found" — die Anmeldung kam
nie zustande. Der Grund liegt vor dem Plugin: **VS Code im Browser
braucht einen sicheren Kontext.** Webviews (auch das Claude-Fenster)
laufen über Service Worker, und die registriert ein Browser nur unter
`https://` oder `localhost` (code-server-FAQ: „Error loading webview …
Could not register service workers"). `http://10.10.10.96:8115/` ist
keins von beiden; die Zwischenablage ist aus demselben Grund
eingeschränkt.

Drei Wege, geordnet nach Aufwand:

1. **Sofort, zum Testen:** SSH-Tunnel vom Laptop,
   `ssh -L 8115:localhost:8115 oaap-admin@10.10.10.96`, dann
   `http://localhost:8115/` — `localhost` gilt als sicher.
2. **Ein Knoten mit echtem TLS:** eine externe Adresse (`oaap external`)
   auf oaap-demo oder oaapx01, z. B. `ide.oaap.joomp.de`. So sähen es
   auch die Teilnehmer.
3. **LAN-TLS nach RFC-0005** (`*.oaap.internal`, eigene Zertifikatsstelle)
   — im RFC angenommen, in der Referenz nie gebaut; Vaultwarden wartet
   seit August auf dasselbe.

Zweite Hürde danach: die Anmeldung des Plugins öffnet eine Anthropic-
Seite und erwartet den Rücksprung auf `localhost` **des Containers**,
den der Browser des Teilnehmers nicht erreicht. Die CLI bietet dafür
den Weg „Code einfügen"; für eine Schulung ist der hinterlegte
`ANTHROPIC_API_KEY` je Instanz der Weg ohne Anmeldung.
