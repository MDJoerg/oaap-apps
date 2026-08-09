# OAAP Store Editor

Prüft Store-Listen — gegen das Format **und gegen die Manifeste, auf die
sie zeigen**. Bauschritt 1 aus
[RFC-0013](../../../oaap-spec/rfcs/RFC-0013-store-editor.md).

**Dieser Stand schreibt nichts.** Kein Bearbeiten, kein Zurückschreiben,
keine Zugangsdaten. Das ist Absicht: Der Prüfer ist der Kern des
Werkzeugs, nicht das Formular.

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

| Art | Bedeutung |
|---------|--------------------------------------------------------------|
| Fehler | So ist die Liste nicht benutzbar — ein Knoten würde daran scheitern oder etwas Falsches tun. |
| Befund | Liste und Manifest sagen **beide** etwas, und es ist verschieden. |
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
- **`icon`** führt heute kein einziges unserer Manifeste.
- **`description`** ist in der Liste absichtlich der längere,
  redaktionelle Text; das Manifest hat nur einen kurzen. Ein Vergleich
  wäre bei allen acht Apps eine Falschmeldung.

Diese vier werden deshalb **nicht** verglichen. Das ist ein offener
Punkt am Papier, kein Versäumnis des Werkzeugs, und er gehört bei der
nächsten Fortschreibung von RFC-0012 entschieden: entweder wandern die
Felder ins Manifest, oder §1.3 nennt sie redaktionell.

## Woher das Manifest geholt wird

Über die Rohdatei-Adresse, nicht per `git clone` — eine Liste mit acht
Einträgen soll in Sekunden durch sein, nicht acht Repositories
herunterladen. Bekannt sind GitHub und Forgejo/Gitea (also auch unsere
eigene Forgejo-App). Eine Adressform, die der Prüfer nicht kennt, wird
**gemeldet statt geraten**: Eine falsch geratene Adresse liefert
entweder 404 (harmlos) oder die falsche Datei (nicht harmlos).

## Konfiguration

`STORE_EDITOR_LISTS` — zu prüfende Listen, durch Komma getrennt.
Mehrere Listen je Instanz ist RFC-0013 Entscheidung 3; dass sie hier in
einer Umgebungsvariablen stehen, ist eine Eigenheit dieses Bauschritts
(es wird ja nichts geschrieben).

Eine Liste, die noch nirgends veröffentlicht ist, lässt sich unter
**Liste einfügen** trotzdem prüfen — der Inhalt wird nirgends
gespeichert.

## Prüfen

```bash
python3 test_checker.py          # ohne Netz, ohne Docker, ohne Knoten
```

Die Prüfregeln liegen in `checker.py`, bewusst ohne Web und ohne Netz:
Es sind Entscheidungen aus RFC-0012 und RFC-0013, und die soll man ohne
laufenden Server lesen können. Das Abrufen wird hereingereicht.

## Abhängigkeit

**PyYAML**, und sonst nichts. Das Studio kommt mit der
Standardbibliothek aus; hier geht das nicht, weil fremde Manifeste
gelesen werden — ein selbstgebauter YAML-Leser, der eine Schreibweise
missversteht, wäre genau die stille Falschaussage, gegen die dieses
Werkzeug antritt. PyYAML braucht keinen Übersetzer (reines Python als
Rückfallebene), der Build auf arm64 bleibt also abhängigkeitsarm.

## Was als Nächstes kommt

- **Bauschritt 2:** die sechs redaktionellen Felder bearbeiten, Ergebnis
  als Datei.
- **Bauschritt 3:** zurückschreiben, mit den drei Betriebsarten aus
  RFC-0013 §3 — allein gepflegt, Vier-Augen, Vorschlag einreichen. Erst
  hier werden Zugangsdaten gebraucht.
