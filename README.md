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
oaap-store.json         Store-Liste „OAAP Plattform-Apps"
check-store.py          prüft, ob Store-Liste und die Tabelle unten
                        noch dasselbe sagen wie die Manifeste
```

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
| [Studio](apps/studio/)             | Vorhaben, KI-Briefings, Pakete prüfen und ausrollen        | 0.4.3   |
| [FleetView](apps/fleetview/)       | Lesende Übersicht über Knoten, Instanzen, Auffälligkeiten  | 0.3.2   |
| [Store Editor](apps/store-editor/) | Store-Listen gegen Format und Manifeste prüfen und pflegen | 0.3.1   |
| [LiveKit](apps/livekit/)           | WebRTC-Medienserver (wrapped) — Referenz Echtzeit-Medien   | 0.1.2   |
| [Keycloak](apps/keycloak/)         | Anmeldedienst (OIDC), ein Realm je Mandant — RFC-0041      | 0.1.4   |
| [KI-Gateway](apps/ai-gateway/)     | OpenAI-kompatibler Endpunkt: Aliasse, API-Keys, Verbrauch  | 0.2.1   |
| [Wegweiser](apps/wegweiser/)       | Kurzlinks, Zählpixel, Einmal-Übergaben unter eigener Domain; REST-API, QR-Code | 0.4.0   |
| [Ollama-Modelle](apps/ollama-models/) | Modelle sehen, holen, löschen; Anschluss ans KI-Gateway    | 0.1.0   |
| [Partnerverwaltung](apps/partnerverwaltung/) | Referenz-App digitaler Zwilling: Owner von Firma/Kontaktperson, zweiter Owner von Projekt (RFC-0031) | 0.1.2   |
| [RACI](apps/raci/)                 | Referenz-App digitaler Zwilling: Contributor `raci.assignments` (RFC-0031) | 0.1.1   |
| [Mitarbeiterverwaltung](apps/mitarbeiterverwaltung/) | Referenz-App digitaler Zwilling: unabhängiger Owner von Mitarbeiter -- erzeugt die erste echte Dublette (RFC-0031) | 0.1.0   |
| [Projekt-App](apps/projekt/)       | Referenz-App digitaler Zwilling: definiert Projekt, Partnerverwaltung legt eigene Instanzen an -- ein Typ, zwei Owner (RFC-0031) | 0.1.0   |
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
