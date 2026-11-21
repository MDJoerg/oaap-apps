# Selbstregistrierung und zweiter Faktor

Ob sich jemand selbst ein Konto anlegen darf und ob ein zweiter Faktor
verlangt wird, sind Einstellungen des **Realms**. Die Plattform legt sie für
Dich um und schreibt auf, was der Realm danach wirklich sagt.

## Nachsehen

```sh
sudo oaap idp settings auth --tenant <mandant>
```

Ohne Schalter wird nur gelesen. So siehst Du auch, ob jemand in der
Verwaltungsoberfläche etwas gedreht hat.

## Umlegen

```sh
sudo oaap idp settings auth --tenant <mandant> --self-registration on
sudo oaap idp settings auth --tenant <mandant> --second-factor required
```

`--dry-run` zeigt vorher jeden Schritt.

## Was dabei passiert

- Die Plattform schreibt den Schalter in den Realm, **liest danach nach**
  und vermerkt die Antwort des Realms – nie nur die eigene Anweisung.
- Weicht der Realm ab, ist das ein lauter Fehler; vermerkt wird trotzdem,
  was wirklich gilt.
- `oaap tenant idp <mandant>` sagt, wenn Realm und Vermerk auseinander
  gehen.

## Zweiter Faktor

Keycloak entscheidet, ob eine Anmeldung einen zweiten Faktor braucht. Die
Plattform erfährt nur, dass die Anmeldung geklappt hat, und schreibt auf,
womit sie bestätigt wurde.
