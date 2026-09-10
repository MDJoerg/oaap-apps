# Kundenzufriedenheit -- ein Datenmodell-Artefakt

Der **erste `data_models`-Artefakt** des Programms (RFC-0012 §8.5,
`oaap.data.model` 0.1 §2.8, RFC-0031 Bauplan Schritt 4, zweite Welle).
Kein Dienst, keine Route, keine Gesundheitspruefung -- nur ein Typ:
eine dritte Herkunftsgruppe `crm.satisfaction` auf `Firma`, neben
[Partnerverwaltung](../../apps/partnerverwaltung/)s `crm.core` und
[RACI](../../apps/raci/)s `raci.assignments`.

Genau das Beispiel aus dem Zielbild
(`program/zielbild-datenplattform.md`, Runde 1, Punkt 4): *"als
Datenmodell ohne App eine extern definierte und gepflegte
Kundenzufriedenheit auf Basis der von der App erzeugten Kunden."*

## Warum ohne App

Niemand `contributes` oder `consumes` diese Gruppe -- `oaap.data.model`
0.1 hat noch keinen Weg, in eine Gruppe zu schreiben, die keiner
Instanz gehoert. Das ist Absicht, nicht eine fehlende Zeile: die
Zufriedenheits-Punktzahl soll vom **Mandanten selbst** gepflegt
werden, im Zwillings-Browser (Schritt 5), nicht von einer App. Diese
Runde registriert nur den Typ; das Schreiben kommt mit dem Browser.

## Ein Fund beim Installieren dieser Art Paket zum ersten Mal

`oaap.data.model` 0.1 §2.8 beschrieb seit seinem eigenen Bau, was ein
solches Artefakt validiert -- aber der Installationspfad selbst
verlangte `services`/`routes`/`health.path`/`app.type` bislang
**unbedingt**, und registrierte jeden Typ unter der Herkunft
`app:<id>`, nie `model:<id>` (RFC-0031 §4). Ein Artefakt haette also
weder installiert noch unter der richtigen Herkunft gestanden. Beides
behoben, bevor dieses Paket zum ersten Mal installiert wurde --
Einzelheiten in [`oaap.data.model.md`](../../../oaap-spec/spec/oaap.data.model.md)
§2.8 und `CURRENT_STATE.md`.

## Installation

```bash
sudo oaap app install <pfad-oder-git> --path data_models/kundenzufriedenheit
```

Hinterlaesst **keine Instanz** -- kein Container, kein Port, kein
Eintrag in `oaap app list`. Der einzige bleibende Effekt ist die
Typregistrierung selbst (`oaap data model types`).

## Pruefen

```bash
python3 data_models/kundenzufriedenheit/test_manifest.py
```

Strukturelle Pruefung gegen `appctl.py`s eigene Validierung (kein
Docker, kein Postgres). Dass die Installation auf einem echten Knoten
tatsaechlich keine Instanz hinterlaesst und unter `model:` registriert,
gehoert auf `oaap-test`.
