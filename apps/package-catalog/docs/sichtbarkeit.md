# Sichtbar nur für bestimmte Mandanten

Normalerweise sieht **jeder Mandant** eine freigegebene App im Store. Soll eine
App nur bei einzelnen Mandanten erscheinen:

1. In der Übersicht die App öffnen.
2. Im Kasten **Sichtbar für** die Kennungen der Mandanten eintragen, durch Komma
   getrennt, z. B. `pxx, bec`.
3. **Speichern**.

Ein leeres Feld heißt: alle Mandanten. Kennungen bestehen aus Kleinbuchstaben,
Ziffern und Bindestrich; eine ungültige Kennung wird abgelehnt, und die alte Einstellung
bleibt.

Der Knoten hält sich daran auch bei der Installation: Er lehnt eine
Installation in einen nicht genannten Mandanten ab – auch für den Betreiber.
