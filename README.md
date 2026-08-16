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
oaap-store.json         Store-Liste „OAAP Plattform-Apps"
```

Die Struktur ist absichtlich dieselbe wie in `oaap-store`: kompatibel zu
`oaap app install --path` und die Store-Liste zeigt direkt auf die Pfade.

## Apps

| App                    | Was es tut                                            | Version |
| ---------------------- | ----------------------------------------------------- | ------- |
| [Studio](apps/studio/) | Vorhaben, KI-Briefings, Pakete prüfen und ausrollen   | 0.2.1   |

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
