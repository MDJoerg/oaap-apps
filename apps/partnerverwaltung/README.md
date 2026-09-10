# OAAP Partnerverwaltung

Die **erste Referenz-App** fuer den digitalen Zwilling (RFC-0031,
Bauplan Schritt 4). Owner von `Customer` und `Person` -- legt beide an,
verknuepft eine Person mit einem Kunden ueber `isContactOf` (gueltig ab
einem Datum), und erfasst Anrufe/Aufgaben. Genau das Szenario, das
RFC-0031 SS9 als Schritt 1 des Konformitaets-Szenarios nennt: *"Partner
management (owner) creates Muller GmbH and Anna, relates them with
isContactOf valid from 2019."*

| tut                                                          | tut nicht                                              |
| ------------------------------------------------------------ | ------------------------------------------------------- |
| Kunden und Personen als **Owner** anlegen (eigene Kern-Gruppe) | eine eigene Kopie der Daten halten                      |
| Person mit Kunde verknuepfen, **gueltig ab** einem Datum      | Dubletten erkennen oder zusammenfuehren (Schritt 4.6)   |
| Anrufe/Aufgaben auf einem Objekt erfassen                     | eine Liste/Suche ueber alle Kunden bieten (twin 0.1 hat keine) |
| jeder anderen App ihre Objekte als **Referenz** anbieten (RACI) | fremde Gruppen lesen oder schreiben                    |

## Warum es diese App gibt

`oaap.data.twin` 0.1 baut RFC-0031 SS9's eigenes Minimum (Schritte 1-3),
aber ein Dienst ohne einen einzigen Aufrufer ist unbewiesen. Diese App
und [RACI](../raci/) sind die Aufrufer -- nicht Beispielcode, sondern
echte, kleine Apps mit vollstaendigem Manifest (`data_model`,
`contributes`, `consumes`), die auf `oaap-test` gegen das echte Postgres
laufen und damit `oaap.data.model`/`oaap.data.twin`s eigene Reife
(`beta`) erst moeglich machen (siehe deren SS7 Maturity).

## Der Zwilling ist die einzige Quelle

Diese App haelt unter ihrem deklarierten Mount **nur** die IDs der
Objekte, die sie selbst angelegt hat (`id` + `type`, kein Titel, kein
Attribut) -- ohne das gaebe es keinen Weg, "meine Kunden" auf einer
Seite zu zeigen, weil `oaap.data.twin` 0.1 noch keine Liste oder Suche
kennt (`/twin/references` ist RFC-0031 SS1 als spaeter benannt). Jede
Seite liest Titel, Attribute und Relationen **frisch** vom Zwilling;
der Fallstrick aus dem Bauplan ("wer den Kundennamen in die eigene
Tabelle schreibt, hat den Zwilling umgangen und merkt es erst beim
Umbenennen") trifft diese App nicht, weil nichts Umbenennbares in der
eigenen Ablage steht.

## Referenz, nicht Kopie

Jede Objektseite zeigt die eigene ID in Klartext -- genau das, was
RACI (oder jede andere Instanz, die `Customer`/`Person` konsumiert)
braucht, um auf dasselbe Objekt zu zeigen, ohne es abzuschreiben (D6:
"the reference, and nothing else, without a specification").

## Zugang zum Zwilling

`OAAP_TWIN_URL`/`OAAP_PLATFORM_KEY` sind plattformeigen (RFC-0031
SS2.2) -- diese App liest sie nur, setzt sie nie. Fehlen sie (kein
`store`-Profil auf dem Knoten, oder eine Generalprobe -- RFC-0030), sagt
die Startseite das offen statt einen Fehler zu zeigen.

## Rollen

Die Route laesst jeden angemeldeten `user` herein; 0.1 unterscheidet
innerhalb der App noch nicht zwischen Lesen und Anlegen -- das ist eine
Frage fuer den Zwillings-Browser (Schritt 5), nicht fuer eine
Referenz-App.

## Pruefen

```bash
python3 apps/partnerverwaltung/test_twin_client.py
```

Gegen einen lokalen Wegwerf-HTTP-Server, der `oaap.data.twin`s Form
nachbildet -- ohne Docker, ohne Postgres, ohne Knoten. Das echte
Zusammenspiel (Owner legt an, Person wird verknuepft, RACI liest und
schreibt) gehoert auf `oaap-test`, wie bei jeder anderen `oaap.data.*`
-Faehigkeit dieses Programms.
