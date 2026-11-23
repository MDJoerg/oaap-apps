# Sicherung und Update

## Ein Update geht nur vorwärts

Eine neue BookStack-Fassung baut beim ersten Start die Datenbank um. Von
25.02 auf 26.09 waren das 13 Schritte. Danach kann die alte Fassung mit
dieser Datenbank **nicht mehr arbeiten**. Gemessen: Sie bricht mit
„Table 'books' doesn't exist“ ab.

Zurück geht es deshalb nur über eine Sicherung von vor dem Update:
**die alte Paketfassung zusammen mit der alten Datenbank und den alten
Dateien.**

## Was zusammengehört

| Ablage | Inhalt |
|---|---|
| `db` | Seiten, Bücher, Rollen, Benutzer, Berechtigungen |
| `files` | hochgeladene Bilder und Anhänge, Protokolle |
| `oaap` | was die Einrichtungstür angelegt hat |

`db` und `files` sind **ein Paar**: Eine Datenbank ohne die Dateien vom
selben Zeitpunkt zeigt Bilder, die es nicht gibt. Dateien ohne Datenbank
sind unsichtbar.

## Vor jedem Update

1. Eine Sicherung der Instanz anlegen.
2. Wenn möglich zuerst auf einer Generalprobe aktualisieren (Kopie der
   Daten) und dort lesen, anmelden und ein Bild öffnen.
3. Dann die Instanz aktualisieren.

Nach dem Update prüft der Dienst `oaap` von selbst, ob BookStacks Tabellen
noch so sind, wie er sie kennt. Weicht etwas ab, legt er nichts an und
sagt es unter `/oaap/status`.

## Gemessen am 23.11.2026

Paket 0.0.9 (BookStack 25.02.5) auf 0.1.0 (BookStack 26.09.3), mit Inhalten:
13 Umbauten der Datenbank. Bücher, Seiten, Berechtigungen, Rollen samt
Gruppen, Benutzer und Bilder kamen unverändert an. Anmeldung und Rechte
der Testpersonen waren danach genau wie vorher. Die einzige Änderung:
Jede Rolle bekam von BookStack das neue Recht „Revisionen ansehen“ dazu.
