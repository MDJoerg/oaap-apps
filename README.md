# oaap-apps — Plattform-eigene Apps

Monorepo der Apps, die **die Plattform selbst** mitbringt. Grundsatz
(vereinbart 2026-08-06): Jörg entwickelt kunden- und partnereigene Apps in
eigenen Repositories; hier entstehen die plattformeigenen Apps — und zwar
als **ganz normale OAAP-Apps nach dem App Deployment Contract**, nicht als
Portal-Innereien. Was für unsere Anwender gilt, gilt für uns auch
(Dogfooding); das Portal bleibt schlank.

## Struktur

```text
apps/<app-id>/          ein Verzeichnis je App, Name = App-ID
  oaap-app.yaml         Manifest (Pflicht)
  Dockerfile            bei App-Typ `native` (Pflicht)
  README.md             was die App tut, bewusste Entscheidungen
data_models/<id>/       ein Verzeichnis je data_models-Artefakt (RFC-0012 §8.5):
  oaap-app.yaml         Manifest -- NUR `app`+`data_model`, kein Dienst
  README.md             was der Typ ist, warum ohne App
lib/<modul>/            Quelle von Modulen, die mehrere Apps teilen
                        (gwcore: Bücher der Gateways, RFC-0065;
                        assistants: Prüfung von Assistenten;
                        mdview: Markdown mit Mermaid und SVG, formatiert/roh;
                        appframe: Rahmen und Listenbericht, RFC-0118;
                        mcpdoor: MCP-Tür einer App, RFC-0118)
oaap-store.json         Store-Liste „OAAP Plattform-Apps"
check-store.py          prüft, ob Store-Liste und die Tabelle unten
                        noch dasselbe sagen wie die Manifeste
check-shared.py         prüft, ob jede App dieselbe Kopie von lib/ trägt
check-design.py         prüft jede App gegen RFC-0118 (Name, Rahmen, Hilfe,
                        MCP-Tür, keine eigene Palette)
```

## Gemeinsame Module

Eine App wird nur aus ihrem eigenen Ordner gebaut (`docker build
apps/<id>/`), ein Paket als ZIP enthält auch nur diesen Ordner. Ein
Modul, das zwei Apps teilen, liegt deshalb **als Kopie** in jeder davon;
die Quelle ist `lib/<modul>/`. Geändert wird nur dort:

```bash
python3 lib/test_gwcore.py      # das Modul für sich
python3 check-shared.py --fix   # Kopien in die Apps schreiben
python3 check-shared.py         # meldet jede abweichende Kopie
```

Danach die Tests jeder App, die das Modul trägt, und deren Version
anheben: Eine geänderte Kopie ist eine geänderte App.

## Nach jeder Versionsanhebung

Die Version einer App steht hier an **drei** Stellen: im Manifest
(daraus wird installiert — es gilt), in `oaap-store.json` (das zeigt
die Store-Seite) und in der Tabelle unten. Dieselbe Angabe dreimal
läuft auseinander, sobald niemand sie zählt:

```bash
python3 check-store.py          # meldet, was auseinanderläuft
python3 check-store.py --fix    # zieht Store-Liste und Tabelle nach
```

Geprüft werden Version, Name, Typ, App-Klasse und die Rollen. Am
21.09.2026 hob ein Commit drei Manifeste an und ließ die Store-Liste
stehen; einen Tag später bot die Store-Seite „Aktualisieren auf
v0.4.2" über einer Instanz, die 0.4.3 lief — und nannte für FleetView
weiter die Rolle `partner`, die derselbe Commit gerade ersetzt hatte.
`released` muss von Hand nachgetragen werden, das Skript sagt bei
welcher App.

Ein `data_models`-Artefakt ist kein App-Paket im engeren Sinn (kein
Dienst, kein Dockerfile, keine Instanz) -- `oaap.data.model` 0.1 §2.8
sagt genau, was es validiert. Siehe
[`kundenzufriedenheit`](data_models/kundenzufriedenheit/) als erstes
Beispiel.

Die Struktur ist absichtlich dieselbe wie in `oaap-store`: kompatibel zu
`oaap app install --path` und die Store-Liste zeigt direkt auf die Pfade.

## Apps

| App                                | Was es tut                                                 | Version |
| ---------------------------------- | ---------------------------------------------------------- | ------- |
| [Studio](apps/studio/)             | Vorhaben, KI-Briefings, Pakete prüfen und ausrollen        | 0.8.2   |
| [FleetView](apps/fleetview/)       | Lesende Übersicht über Knoten, Instanzen, Auffälligkeiten  | 1.2.2   |
| [Store Editor](apps/store-editor/) | Store-Listen gegen Format und Manifeste prüfen und pflegen | 0.4.0   |
| [LiveKit](apps/livekit/)           | WebRTC-Medienserver (wrapped) — Referenz Echtzeit-Medien   | 0.2.0   |
| [Keycloak](apps/keycloak/)         | Anmeldedienst (OIDC), ein Realm je Mandant — RFC-0041      | 0.2.0   |
| [KI-Gateway](apps/ai-gateway/)     | OpenAI-kompatibler Endpunkt: Aliasse, API-Keys, Verbrauch  | 0.14.0   |
| [MCP-Gateway](apps/mcp-gateway/)  | MCP-Server je Sicht: Bündel, Anmeldung, Freigabe, Klassen, Schutz, Verbrauch, Vorlagen für Werkzeuge, Systemlandschaft und Multi-System-Sichten | 0.48.0   |
| [Chat](apps/chat/)                 | Plattform-Chat: Assistenten (privat/Team/Mandant), Werkzeuge als die Person, Zustimmung vor dem Schreiben — RFC-0074 | 0.24.0   |
| [Recherche](apps/recherche/)       | Websuche (SearXNG hinter einer Destination) und Webseiten als lesende MCP-Quelle; startet rot — RFC-0116 | 0.3.0   |
| [Wegweiser](apps/wegweiser/)       | Kurzlinks, Zählpixel, Einmal-Übergaben unter eigener Domain; REST-API, QR-Code | 0.6.2   |
| [Paketkatalog](apps/package-catalog/) | Freigegebene ZIP-Pakete mit Versionsverlauf; Store-Quelle des Knotens (RFC-0050); Spiegel eines Marketplace-Feeds (RFC-0077) | 0.5.0   |
| [Marketplace](apps/marketplace/)   | Digitale Assets mit Kanälen, Sichtbarkeit und Berechtigungen; Schaufenster mit Suche, Facetten, Bildern, Freischalt-Anfragen; Einchecken per API, Feed für Paketkataloge (RFC-0077 Stufen 1–2) | 0.5.2   |
| [Ollama-Modelle](apps/ollama-models/) | Modelle sehen, holen, löschen; Anschluss ans KI-Gateway    | 0.2.0   |
| [Code-Server](apps/code-server/)   | VS Code im Browser (wrapped), ein Container je Mensch — Schulungsarbeitsplatz | 0.2.0   |
| [Webtop](apps/webtop/)             | Linux-Desktop im Browser (wrapped): Eclipse/ADT, VS Code, Claude Desktop, goose, opencode | 0.2.0   |
| [VPN-Client](apps/vpn-client/)    | Tunnel als App (RFC-0060): fremdes OpenVPN in einem Container, Ziele dahinter als Destination `through`; Installation nur mit `--confirm-tun` | 0.3.0   |
| [Partnerverwaltung](apps/partnerverwaltung/) | Referenz-App digitaler Zwilling: Owner von Firma/Kontaktperson, zweiter Owner von Projekt (RFC-0031) | 0.3.2   |
| [Rollen & Rechte](apps/rollen-rechte/) | Werkzeug des Mandanten-Admins: wer darf was in welcher App — Rollen, Berechtigungspakete, Zuordnungen, Gruppen (RFC-0045 A7); privilegiert, Installation nur mit `--confirm-administer` | 0.4.2   |
| [Vorgänge](apps/vorgaenge/)       | Melden, Klären und Übergeben zwischen Mandanten (RFC-0061 Stufe 1+2): nimmt den Feedback-Knopf der Plattform an, Bereiche als Kontexte aus „Rollen & Rechte“; eine Instanz je Mandant, Löschfrist je Mandant; seit 0.3.0 kündigt der Knoten seinen Neustart hier an (RFC-0069); seit 0.12.0 nimmt sie Anfragen der Mandanten an den Betreiber an (RFC-0109); seit 0.13.0 Erstbearbeitung durch KI-Spezialisten, seit 0.14.0 Freigabe ihrer Pläne und Entwürfe, seit 0.15.0 Rückfragen (RFC-0105); seit 0.20.0 meldet sie den Status eines Vorgangs aus dem Backend einer App als adressiertes Ereignis an diese App zurück (RFC-0127); Plattform ab 0.1.201 | 0.20.0  |
| [Spezialisten](apps/spezialisten/) | KI-Spezialisten, die Vorgänge im Hintergrund bearbeiten: eigener Agent, nur lesende Werkzeuge der eigenen Sicht, jeder Schritt intern im Vorgang, Grenzen, Stopp, Rückgabe an die Zuständigen; schreibt nur nach Freigabe eines Menschen, fragt andere Spezialisten oder Menschen um Rat — RFC-0105 Stufe 1–3; braucht Vorgänge ab 0.15.0, MCP-Gateway ab 0.34.0 | 0.6.2   |
| [RACI](apps/raci/)                 | Referenz-App digitaler Zwilling: Contributor `raci.assignments` (RFC-0031) | 0.3.2   |
| [Mitarbeiterverwaltung](apps/mitarbeiterverwaltung/) | Referenz-App digitaler Zwilling: unabhängiger Owner von Mitarbeiter -- erzeugt die erste echte Dublette (RFC-0031) | 0.3.2   |
| [Projekt-App](apps/projekt/)       | Referenz-App digitaler Zwilling: definiert Projekt, Partnerverwaltung legt eigene Instanzen an -- ein Typ, zwei Owner (RFC-0031) | 0.3.2   |
| [OAAP-Hilfe](apps/help/)           | Die Doku für jede KI (RFC-0078): Suche über MCP, API und Seite, nach Rolle gefiltert; holt über die Destination `docs-origin`, Kopie im Paket; zentral zusätzlich öffentlich (`HELP_PUBLIC`); fehlende Anleitungen anfordern (`HELP_REQUESTS`) | 0.9.0   |
| [Wiki (BookStack)](apps/bookstack/) | Wiki für den Mandanten (wrapped, drei Container): Anmeldung über den Realm, Gruppen werden Rollen, Bilder geschützt wie ihre Seite — RFC-0110 Stufe 1 | 0.2.1   |
| [Kundenzufriedenheit](data_models/kundenzufriedenheit/) | `data_models`-Artefakt (kein App-Dienst): dritte Herkunftsgruppe auf Firma, gepflegt im Zwillings-Browser (RFC-0031) | 0.1.0   |

## Installation

Einzeln aus diesem Repo:

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/studio
```

Oder die ganze Liste als Store-Quelle eintragen — danach erscheinen die
Apps im Portal unter „Store" und lassen sich mit einem Klick installieren:

```sh
sudo oaap store add-source \
  https://raw.githubusercontent.com/MDJoerg/oaap-apps/main/oaap-store.json \
  --name "OAAP Plattform-Apps"
```

## Offen

- **Lizenz noch nicht entschieden** (gilt fürs ganze Programm, nicht nur
  hier): Die Store-Einträge lassen das Feld deshalb bewusst leer. →
  ADR-Kandidat.
