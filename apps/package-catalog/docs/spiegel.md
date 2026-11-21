# Einen Marktplatz spiegeln

Der Katalog kann die Pakete eines **Marktplatzes** selbst holen, statt dass Du
sie von Hand herunter- und hochlädst.

## Einrichten

Im Portal auf der Seite dieser Instanz, Reiter **Netz & Adressen**, die
Destination **upstream** zuordnen: Ziel ist der Marktplatz, Anmeldung mit dem
API-Schlüssel eines Maschinenbenutzers dort. Der Schlüssel liegt in der
Destination, nie im Katalog. Danach erscheint der Reiter **Spiegel**.

## Was dann passiert

- Alle 15 Minuten (oder mit **Jetzt abrufen**) liest der Katalog den Feed des
  Marktplatzes.
- Jede neue Version lädt er herunter, prüft Größe, Prüfsumme, Kennung und
  Version und nimmt sie auf – nie überschreibend.
- Mit der Einstellung `auto` gibt er sie sofort frei, mit `hold` wartet sie auf
  Dich.
- Eine im Marktplatz zurückgerufene Version zieht er mit `auto` zurück; er löscht
  sie nie.

## Nur bestimmte Apps folgen

Auf einer Detailseite im Marktplatz „Teilen“ → „Link kopieren“ und den Link
unter **Aus Zwischenablage hinzufügen** einfügen. Der Katalog ruft den Link
nicht auf; er prüft nur, dass er zum angebundenen Marktplatz gehört. Wirksam
ist die Liste mit der Einstellung `CATALOG_UPSTREAM_SCOPE=followed`. Mit
**Nicht mehr folgen** nimmst Du eine App wieder von der Liste; was schon da ist,
bleibt.
