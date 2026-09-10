# OAAP Mitarbeiterverwaltung

Die **zweite Welle** der digitalen-Zwilling-Referenz-Apps (RFC-0031,
Bauplan Schritt 4). Owner von `Mitarbeiter` -- legt Mitarbeiter mit
E-Mail/Telefon/Abteilung an. Genau das Beispiel aus dem Zielbild
(`program/zielbild-datenplattform.md`, Runde 1, Punkt 5): *"eine
einfache Mitarbeiterverwaltung."*

| tut                                                    | tut nicht                                                |
| ------------------------------------------------------- | --------------------------------------------------------- |
| Mitarbeiter als **Owner** anlegen (eigene Kern-Gruppe `hr.core`) | Partnerverwaltung kennen, lesen oder konsumieren           |
| eine eigene, kleine Ablage nur fuer die eigenen IDs      | eine eigene Kopie der Titel/Attribute halten               |
|                                                          | Dubletten erkennen oder zusammenfuehren (Schritt 5)         |

## Warum es diese App gibt -- absichtlich die falsche Nachbarschaft

Diese App weiss **nichts** von [Partnerverwaltung](../partnerverwaltung/)
und liest/schreibt auch nichts von ihr. Das ist keine Luecke, sondern
der Bauplan selbst: *"Mitarbeiterverwaltung -- zweiter Owner von Person
-> erste echte Dublette, erster Merge."* Legt jemand hier "Anna" als
Mitarbeiter an, waehrend Partnerverwaltung "Anna" bereits als
Kontaktperson kennt, existiert die eine echte Person danach als **zwei
Zwillings-Objekte**, zwei Typen, ohne dass eine App von der anderen
weiss -- exakt das Problem, das RFC-0031s eigene Motivation nennt:
"vier Apps, vier Meinungen, was ein Kunde ist." Das Aufloesen
(Merge/Unmerge) ist eine Aufgabe des Zwillings-Browsers (Schritt 5);
diese App **erzeugt** den Fall, sie **loest** ihn nicht.

## Ein Fund beim Schreiben des Manifests: Attribut-Schluessel sind ein flaches, geteiltes Wort

`Email`/`Phone` sind bereits von Partnerverwaltung registriert
(`app:partnerverwaltung`) -- ein zweiter `attribute_types`-Eintrag mit
demselben Schluessel waere dieselbe Kollision wie `Customer`/`Person`
(RFC-0031 D1). Diese App benutzt beide Woerter trotzdem in `hr.core`s
Attributliste, ohne sie neu zu registrieren: `oaap.data.model` 0.1
prueft die Mitglieder einer Gruppe nicht gegen die Registrierung --
eine Gruppe kann heute jeden Schluessel nennen. Das ist informelle
Wortwiederverwendung, keine erklaerte Referenz (die gibt es nur ueber
`consumes`, und nur fuer Objekttypen) -- absichtlich unverändert
gelassen, weil es genau die Realitaet einer kleinen Firma ohne
Zwilling zeigt: zwei Systeme nennen ein Feld gleich, ohne dass es
irgendwo festgeschrieben ist. Siehe `oaap-app.yaml`s Kommentar.

## Der Zwilling ist die einzige Quelle

Wie Partnerverwaltung: nur `id` + `type` der selbst angelegten
Mitarbeiter liegen unter dem deklarierten Mount, kein Titel, kein
Attribut. Jede Seite liest frisch vom Zwilling.

## Design-Kontrakt (RFC-0035 Teil A)

Bindet `/platform/theme.css` ein, benutzt dessen Variablen mit lokalen
Fallback-Werten, und traegt die Mini-Kopfzeile aus D4 (App-Name links,
Link zurueck zum Portal rechts) statt einer eigenen Marke -- die erste
App, die nach dem neuen Kontrakt gebaut wurde (RFC-0035 D6: die
naechste neue App, kein Nachbau der bestehenden).

## Zugang zum Zwilling

`OAAP_TWIN_URL`/`OAAP_PLATFORM_KEY` sind plattformeigen -- diese App
liest sie nur. Fehlen sie, sagt die Startseite das offen.

## Rollen

Jeder angemeldete `user`; 0.1 unterscheidet noch nicht zwischen Lesen
und Anlegen.

## Pruefen

```bash
python3 apps/mitarbeiterverwaltung/test_twin_client.py
```

Gegen einen lokalen Wegwerf-HTTP-Server, der `oaap.data.twin`s Form
nachbildet -- ohne Docker, ohne Postgres, ohne Knoten. Das echte
Zusammenspiel (und der Dublette-Fall mit Partnerverwaltung) gehoert auf
`oaap-test`.
