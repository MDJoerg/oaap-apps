# OAAP RACI

Die **zweite Referenz-App** fuer den digitalen Zwilling (RFC-0031,
Bauplan Schritt 4) -- der Contributor zu
[Partnerverwaltung](../partnerverwaltung/)s Owner. Liest `Customer` und
`Person` nur als Referenz (D6), schreibt `responsible`-Relationen in
ihre EIGENE Gruppe `raci.assignments` auf einem Kunden, den sie weder
besitzt noch anlegt. Genau RFC-0031 SS9 Schritt 3: *"RACI (contributor)
reads both as references, writes `responsible` relations into
`raci.assignments` on Muller GmbH."*

| tut                                                      | tut nicht                                                    |
| --------------------------------------------------------- | -------------------------------------------------------------- |
| einen Kunden per ID laden (Referenz, keine Suche)         | Kunden oder Personen ANLEGEN (das ist Partnerverwaltung)       |
| `responsible` in die eigene Gruppe `raci.assignments` schreiben | `crm.core`/`crm.contact` lesen oder schreiben              |
| die Zuweisung sofort frisch vom Zwilling zeigen            | eine eigene Kopie der Namen/IDs halten                          |

## Referenz, nicht Besitz

RACI besitzt **weder** `Customer` noch `Person` -- es konsumiert beide
(D6: "the reference, and nothing else"). Was RACI besitzt, ist sein
EIGENES Gruppentyp `raci.assignments` auf dem fremden Objekttyp
`Customer` -- D1s "others attach their own group types" wortwoertlich:
eine App darf einem Typ, den eine andere App geschaffen hat, eine
eigene Gruppe anhaengen, ohne den Typ selbst zu veraendern, und ohne
dessen eigene Kern-Gruppe je zu beruehren. `oaap.data.twin` erzwingt
das am Dienst selbst (RFC-0031 SS3.3, "nobody else writes there") --
diese App verlaesst sich nicht darauf, brav zu bleiben.

## Keine Suche, keine Ablage

`oaap.data.twin` 0.1 hat keine Liste und keine Suche
(`/twin/references` ist RFC-0031 SS1 als spaeter benannt) -- deshalb
tippt oder pastet man die Kunde-/Mitarbeiter-ID, die
[Partnerverwaltung](../partnerverwaltung/)s Objektseite zeigt. RACI
haelt selbst **nichts** vor: keine Datei, kein Mount, keine Liste
"zuletzt benutzt". Jede Seite fragt den Zwilling frisch -- der einzige
Weg, mit dem eine Umbenennung bei Muller GmbH nicht irgendwo als alter
Name uebrig bleibt.

## Zugang zum Zwilling

`OAAP_TWIN_URL`/`OAAP_PLATFORM_KEY` sind plattformeigen (RFC-0031
SS2.2) -- diese App liest sie nur, setzt sie nie. Fehlen sie (kein
`store`-Profil, oder eine Generalprobe -- RFC-0030), sagt die
Startseite das offen statt einen Fehler zu zeigen.

## Rollen

Die Route laesst jeden angemeldeten `user` herein.

## Pruefen

```bash
python3 apps/raci/test_twin_client.py
```

Gegen einen lokalen Wegwerf-HTTP-Server, der `oaap.data.twin`s Form
nachbildet -- ohne Docker, ohne Postgres, ohne Knoten. Das echte
Zusammenspiel mit Partnerverwaltung (Kunde/Person anlegen,
`isContactOf` verknuepfen, hier laden und `responsible` zuweisen)
gehoert auf `oaap-test`.
