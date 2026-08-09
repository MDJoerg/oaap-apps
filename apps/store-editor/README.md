# OAAP Store Editor

Prüft Store-Listen — gegen das Format **und gegen die Manifeste, auf die
sie zeigen** — und pflegt sie. Bauschritte 1 und 2 aus
[RFC-0013](../../../oaap-spec/rfcs/RFC-0013-store-editor.md).

**Dieser Stand veröffentlicht nichts.** Kein Zurückschreiben ins
Repository, keine Zugangsdaten. Bearbeitet wird eine Arbeitskopie auf
der eigenen Instanz; das Ergebnis ist eine Datei zum Herunterladen.
Der Prüfer ist der Kern des Werkzeugs, nicht das Formular — deshalb
kam er zuerst.

## Warum es das gibt

Eine Store-Liste ist kein Dokument, sondern eine Anweisung, die auf
fremden Rechnern Software installiert.

Der Anlass ist echt: Ollama stand seit dem 09.08.2026 in der
Community-Liste als `app_class: service`, sein Manifest sagte davon
nichts. Liste und Paket widersprachen sich, wochenlang, und aufgefallen
ist es nur, weil zufällig jemand die Launchpad-Regel baute. Kein Mensch
findet so etwas beim Lesen von JSON — ein Prüfer, der das Manifest
hinter jedem Eintrag holt, findet es sofort.

## Drei Arten von Befund

| Art     | Bedeutung                                                                                        |
| ------- | ------------------------------------------------------------------------------------------------ |
| Fehler  | So ist die Liste nicht benutzbar — ein Knoten würde daran scheitern oder etwas Falsches tun.     |
| Befund  | Liste und Manifest sagen **beide** etwas, und es ist verschieden.                                |
| Hinweis | Auffällig, aber vielleicht Absicht — etwa eine Behauptung, die das Manifest (noch) nicht belegt. |

Der Unterschied zwischen Befund und Hinweis ist an echten Daten
entstanden: Sieben von acht unserer Manifeste tragen noch keine Klasse,
während alle acht Listeneinträge eine behaupten. Das ist **kein**
Widerspruch, sondern eine Behauptung ohne Deckung. Wer beides gleich
behandelt, erzeugt am ersten Tag sieben Falschmeldungen und wird
ignoriert.

Unbekannte Werte im Vokabular sind ebenfalls nur ein Hinweis: Ein Knoten
toleriert sie (RFC-0012 §8.1), also darf der Editor nicht so tun, als
wäre die Liste kaputt.

## Was verglichen wird

`name`, `type`, `version`, `app_class` und `roles` — Letztere als
**Menge**, denn die Reihenfolge bedeutet nichts. (Beim Ableiten der
Regeln an den echten Listen war genau das die erste Falschmeldung.)

## Was §1.3 zu viel verspricht

RFC-0012 §1.3 führt `profiles`, `icon`, `released` und `description`
unter „erzeugt aus dem Manifest". Das kann das Manifest heute nicht
einlösen:

- **`profiles`** kennt das Manifest-Schema überhaupt nicht (RFC-0011
  beschreibt Knotenprofile, nicht App-Profile).
- **`released`** steht nirgends — es wäre das Datum des Git-Tags, das
  ein Rohabruf der Datei nicht liefert.
- **`icon`** liegt anders, und das war zunächst falsch beschrieben:
  `app.icon` **kennt** das Manifest-Schema. Das Hindernis sind die
  Bezugspunkte — in der Liste gilt ein Bildpfad relativ zur Liste
  (RFC-0012 §1.1, damit kein Knoten beim Öffnen der Store-Seite einen
  fremden Server anruft), im Manifest relativ zum Paket. Eine
  Neuerzeugung müsste die Datei kopieren; das kann erst ein
  Bauschritt, der schreibt.
- **`description`** ist in der Liste absichtlich der längere,
  redaktionelle Text; das Manifest hat nur einen kurzen. Ein Vergleich
  wäre bei allen acht Apps eine Falschmeldung.

Diese vier werden deshalb **nicht** verglichen. Das ist ein offener
Punkt am Papier, kein Versäumnis des Werkzeugs, und er gehört bei der
nächsten Fortschreibung von RFC-0012 entschieden: entweder wandern die
Felder ins Manifest, oder §1.3 nennt sie redaktionell.

Bauschritt 2 macht daraus eine sichtbare Konsequenz: `released`,
`profiles`, `icon` und `package` stehen **frei bearbeitbar** in einem
eigenen Abschnitt, der den offenen Punkt benennt. Sie als „erzeugt" zu
verriegeln wäre eine Unwahrheit in der Oberfläche — es gäbe nichts,
woraus sie je erzeugt würden.

## Wie bearbeitet wird

Die erste Änderung legt einen **Entwurf** an: eine Arbeitskopie im
Speicher der Instanz. Der veröffentlichte Stand bleibt daneben stehen
und dient als Vergleich; *Änderungen ansehen* zeigt jederzeit Eintrag
für Eintrag, Feld für Feld, was sich unterscheidet. Solange ein Entwurf
besteht, prüft der Prüfer **ihn** und nicht mehr die Veröffentlichung —
sonst wäre der Wächter für genau das blind, was gerade entsteht.

**Fünf Felder gehören dem Paket:** `name`, `type`, `version`,
`app_class`, `roles`. Sie stehen verriegelt da und kommen aus dem
Manifest. Wer eines abweichend pflegen will, hakt „abweichend pflegen"
an — die Abweichung wird dann **markiert** und überlebt die nächste
Neuerzeugung. Ohne diese Markierung nähme jede Neuerzeugung eine
bewusste redaktionelle Entscheidung stillschweigend zurück; genau das
verlangt RFC-0012 §1.3, und ohne sie wäre die 80-%-Regel nicht
vertretbar.

Die Markierungen liegen **im Editor, nicht in der Liste**. Eine Liste
ist ein Dokument nach `oaap-store.schema.json`; die Buchführung des
Editors gehört nicht hinein und schon gar nicht auf fremde Knoten.
Dieselbe Begründung wie bei der Betriebsart in RFC-0013 §3. Der Preis
ist ehrlich zu nennen: Wer dieselbe Liste in einem anderen Editor
öffnet, sieht die Markierungen nicht.

Die Änderungsübersicht trennt **strukturell**, **redaktionell** und
**aus dem Manifest übernommen**. Das ist keine Kosmetik, sondern die
Vorarbeit für die Mengenbremse in Bauschritt 3: Sie zählt nur die
ersten beiden (RFC-0013, Frage 5).

**Der Prüfer ist der Wächter, nicht der Mensch.** Was strukturell
kaputt ist, gibt es nicht als Datei. Befunde und Hinweise halten
dagegen nicht auf — ein Eintrag darf vor seinem Manifest entstehen
(RFC-0013, Frage 4), und der Prüfer sagt bei jedem Lauf, dass er ohne
Beleg dasteht.

## Abgleichen — für eine App oder für alle

*Abgleichen* holt die fünf erzeugten Felder aus dem Manifest. In der
Zeile einer App gilt es nur für sie, oberhalb der Tabelle für die ganze
Liste; übersteuerte Felder bleiben in beiden Fällen unberührt.

Der Abgleich einer einzelnen App ist bewusst ein **eigener Weg** und
nicht der Speichern-Knopf des Formulars: Über das Formular liefe er als
vollständiges Absenden, und ein leeres Feld bedeutet dort „weglassen" —
ein Abgleich aus der Zeile heraus würde die redaktionellen Texte
mitnehmen.

## Der Nachpflege-Bericht

Der Abgleich geht in eine Richtung: Die Liste folgt dem Manifest. Der
Bericht geht in die andere — er sagt, **was dem Manifest fehlt**, als
Auftrag zum Weiterreichen an die KI, die die App betreut, mit einem
einsetzbaren YAML-Block. Es gibt ihn je App und für eine ganze Liste;
im Sammelbericht bleibt jeder Abschnitt für sich lesbar.

Drei Dinge tut er bewusst **nicht**:

- Er verlangt keine Nachpflege, wo Liste und Manifest sich
  **widersprechen**. Dort ist der Katalog schuld, und eine fremde KI
  anzuweisen, unsere veraltete Version zu übernehmen, wäre schlimmer
  als gar kein Bericht.
- Er schlägt für `icon` **keinen Pfad** vor — der hat im Katalog einen
  anderen Bezugspunkt (siehe oben).
- Was das Manifest-Format noch nicht kennt, führt er getrennt und
  **ohne Auftrag**: Das ist ein offener Punkt an der Spezifikation,
  kein Versäumnis der App.

Als `app.description` schlägt er den **einen Satz** aus dem Katalog
vor (`summary`), nicht den langen Text — das Manifest trägt die kurze
Fassung, sie steht später an der Instanz.

**Der Bericht ist zugleich der Beleg für RFC-0014.** Gegen unsere acht
Apps gehalten sagt fast jede Zeile „das Format kennt dieses Feld
nicht". Ein Bericht, der nur zwei Felder überhaupt einfordern kann, ist
selbst der Befund.

## Woher das Manifest geholt wird

Über die Rohdatei-Adresse, nicht per `git clone` — eine Liste mit acht
Einträgen soll in Sekunden durch sein, nicht acht Repositories
herunterladen. Bekannt sind GitHub und Forgejo/Gitea (also auch unsere
eigene Forgejo-App). Eine Adressform, die der Prüfer nicht kennt, wird
**gemeldet statt geraten**: Eine falsch geratene Adresse liefert
entweder 404 (harmlos) oder die falsche Datei (nicht harmlos).

## Listen aufnehmen — und private Repositories

Unter **Listen und Zugang** wird eingetragen, welche Listen dieser
Editor pflegt. Aufnehmen und Entfernen brauchen `keyuser` oder `admin`:
Das ist Einrichtung, nicht Redaktion. Bearbeiten darf jeder, der auf die
App kommt.

Eine Adresse aus der Adresszeile des Browsers (`…/blob/main/…`) wird
beim Aufnehmen **umgeschrieben** — dort liegt eine HTML-Seite, nicht die
Datei. Ohne das käme „das ist keine gültige JSON-Datei" und kein Hinweis
darauf, was falsch war.

### Wo die Zugangsschlüssel liegen — und warum nicht hier

Ein privates Repository verlangt einen Schlüssel schon zum **Lesen**.
Der Editor kennt drei **Plätze**; die Schlüssel selbst trägt ein
`server_admin` im Portal ein, als `STORE_EDITOR_TOKEN_1` bis `_3`, dort
`secret: true` — eintragbar, nie zurücklesbar, auch nicht im Editor.

Diese App legt bewusst **keine eigene** Geheimnis-Ablage an. Sie würde
nachbauen, was die Plattform schon hat, und zwar schwächer geschützt.
Die feste Zahl an Plätzen ist der Preis, und sie ist **sichtbar statt
versteckt**: Braucht es einen vierten, ist genau das der Beleg dafür,
dass die Plattform zur Laufzeit hinzufügbare Geheimnisse bekommen
sollte (RFC-0013, Entscheidung Jörgs vom 09.08.2026).

**Heute nur Lesen.** Ein Schreibtoken kommt als eigenes Feld mit
Bauschritt 3 — ein Lesetoken ist kein Schreibrecht, und beides in einem
Feld gewährt mehr als nötig.

### Zwei Dinge, die beim Bauen herauskamen

**Ein privates GitHub-Repo lässt sich über `raw.githubusercontent.com`
gar nicht lesen.** Dieser Host nimmt kein Token an; es führt nur die
Inhalts-Schnittstelle hin (`api.github.com/repos/…/contents/…` mit
`Accept: application/vnd.github.raw`). Code, der gegen ein öffentliches
Repository funktioniert, liefert gegen ein privates **404** — dieselbe
Antwort wie für eine wirklich fehlende Datei, damit private
Repositories nicht erratbar sind. Forgejo/Gitea nehmen das Token
dagegen direkt auf dem Rohdatei-Pfad an.

**Ein Schlüssel geht nur an den Anbieter, für den er eingetragen ist.**
Sonst könnte eine Liste allein dadurch, dass ein Eintrag auf ein
fremdes Repository zeigt, ein Token dorthin schicken lassen. Bei einer
unbekannten Adressform wird er **gar nicht** mitgeschickt statt blind
gesetzt: Ein geratener `Authorization`-Kopf übergibt ein Geheimnis an
einen Server, den niemand geprüft hat.

## Konfiguration

`STORE_EDITOR_LISTS` — die Listen zum Start, durch Komma getrennt. Nur
noch **Saatgut**: Beim ersten Start wandern die Adressen in die
Quellenverwaltung, wo ein `keyuser` sie pflegt. Mehrere Listen je
Instanz ist RFC-0013 Entscheidung 3.

`STORE_EDITOR_TOKEN_1` bis `_3` — Lesetoken für private Listen, `secret`
(siehe oben).

Der deklarierte Speicher `entwuerfe` (Mount `/data`) hält die
Arbeitskopien. Er überlebt Neustart, Redeploy und Update wie die Daten
jeder anderen App und ist in `oaap backup create` enthalten. Eine
Instanz aus Version 0.1.0 kennt ihn noch nicht — ein erneutes Ausrollen
legt ihn an; bis dahin sagt die Startseite, dass Bearbeiten nicht geht.

Eine Liste, die noch nirgends veröffentlicht ist, lässt sich unter
**Liste einfügen** trotzdem prüfen — der Inhalt wird nirgends
gespeichert.

## Prüfen

```bash
python3 test_checker.py          # die Prüfregeln
python3 test_editor.py           # die Bearbeitungsregeln
python3 test_pages.py            # die Seiten und der Weg eines Formulars

# am laufenden Knoten, mit Portal-Anmeldung:
python3 klicktest.py ../../../oaap-reference/test/.env http://10.10.10.75 8106
```

Der private Abrufweg ist in `test_checker.py` festgehalten und nicht am
echten Fall geprüft — dafür braucht es ein privates Repository und
einen Schlüssel, und beides gehört nicht in ein Testskript.

Die ersten drei ohne Netz, ohne Docker, ohne Knoten. Die Regeln liegen in
`checker.py` und `editor.py`, bewusst ohne Web und ohne Netz: Es sind
Entscheidungen aus RFC-0012 und RFC-0013, und die soll man ohne
laufenden Server lesen können. Das Abrufen wird hereingereicht.

`test_pages.py` rendert die Seiten und schickt echte Formularwerte
durch. Den gibt es, weil im Portal schon einmal ein Zeilenumbruch in
einer Vorlage einen Satz zerrissen hat, den der Klicktest am echten
Knoten suchte. Beim ersten Lauf hat er prompt denselben Fehler in
diesem Formular gefunden.

`klicktest.py` prüft, was kein Modultest kann: dass die Anmeldung als
Gateway-Kopfzeile ankommt, dass der deklarierte Speicher wirklich
beschreibbar ist — und dass ein Formular, das der Browser abschickt,
dieselben Werte zurückbringt, die es angezeigt hat. Er liest das
Formular so aus, wie ein Browser es senden würde (keine abgeschalteten
Felder, keine nicht angehakten Kästchen), ändert genau einen Wert und
verwirft den Entwurf am Ende wieder.

## Abhängigkeit

**PyYAML**, und sonst nichts. Das Studio kommt mit der
Standardbibliothek aus; hier geht das nicht, weil fremde Manifeste
gelesen werden — ein selbstgebauter YAML-Leser, der eine Schreibweise
missversteht, wäre genau die stille Falschaussage, gegen die dieses
Werkzeug antritt. PyYAML braucht keinen Übersetzer (reines Python als
Rückfallebene), der Build auf arm64 bleibt also abhängigkeitsarm.

## Über RFC-0013 hinaus: Einträge aufnehmen und entfernen

Bauschritt 2 ist im RFC als „die sechs redaktionellen Felder
bearbeiten" beschrieben. Aufnehmen und Entfernen von Einträgen sind
hier trotzdem dabei, und der Grund ist der Anwendungsfall, der den RFC
begründet hat: Eine Liste, die uns nicht gehört — Jörgs BDT-Projekt —
fängt bei null an. Ein Editor, der nur vorhandene Einträge umtexten
kann, liefert ihrem Pfleger eine leere Datei. RFC-0013 Entscheidung 4
setzt das Aufnehmen ohnehin voraus („ein Eintrag darf entstehen, bevor
sein Manifest abrufbar ist"), und ohne Entfernen wäre ein Vertipper
beim Aufnehmen nicht mehr zu beheben.

Beides zählt in der Änderungsübersicht als **strukturell** — genau die
Kategorie, die die Mengenbremse in Bauschritt 3 mitzählt.

## Was als Nächstes kommt

**Bauschritt 3:** zurückschreiben, mit den drei Betriebsarten aus
RFC-0013 §3 — allein gepflegt, Vier-Augen, Vorschlag einreichen. Erst
hier werden Zugangsdaten gebraucht, und erst hier bekommt die
Rollentrennung aus Entscheidung 2 ihre Wirkung (`user` schlägt vor,
`keyuser` gibt frei). Vorhanden ist schon: die Änderungsübersicht
nach Art getrennt (die Mengenbremse), das Erkennen eines Pakets, das
umzieht (die Repository-Rückfrage), und der Prüfer als Wächter vor der
Ausgabe.
