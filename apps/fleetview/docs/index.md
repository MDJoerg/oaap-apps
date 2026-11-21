# Flotte

Die App **Flotte** zeigt alle Server Ihres Verbunds auf einen Blick: welche Knoten
es gibt, ob sie gesund sind, welche Plattformversion sie haben und welche Apps auf
ihnen laufen.

Sie ist **strikt lesend**. Gehandelt wird im Portal des jeweiligen Knotens,
gekoppelt im Portal der Zentrale – jede Zeile verlinkt dorthin.

## Für wen

Für **Administratoren** und **Dienstleister** (Rolle `support`), die die
Landschaft betreuen.

## Woher die Daten kommen

Die Zentrale der Flotte schreibt jeden gekoppelten Knoten und seine Instanzen in
den Zwilling ihres Mandanten (meist `oaap-ops`). Die App liest von dort, jede
Minute. Ein neu gekoppelter Knoten erscheint von selbst, unter dem Namen, den er
im Portal unter „Gesundheit“ trägt.

Ist der Zwilling einmal nicht lesbar, zeigt die App den zuletzt gelesenen Stand
und den Fehler daneben.

## Bereiche

- **Übersicht** – was einen Menschen braucht, dann die Liste der Knoten.
- **Instanzen** – alle App-Instanzen der Flotte, durchsuchbar.
- Ein Klick auf einen Knoten öffnet seine Seite mit allen Angaben und Instanzen.

## Anleitungen

- [Wie steht die Flotte?](flotte-pruefen.md)
- [Einen Knoten ansehen](knoten-ansehen.md)
- [Eine Instanz finden](instanz-finden.md)
- [Einrichten](einrichten.md)
- [Häufige Fragen](faq.md)
