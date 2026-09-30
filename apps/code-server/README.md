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
| `IDE_EXTENSIONS` | Weitere Open-VSX-Kennungen, eine je Zeile (z. B. `SAPSE.sap-ux-fiori-tools-extension-pack`). Werden beim Start nachinstalliert, wenn sie fehlen — best effort, das Container-Log sagt, was nicht ging. |
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
