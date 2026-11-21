# Die Ampel

Jede Bezugsquelle trägt eine Farbe. Sie sagt nicht, wie gut oder teuer eine Quelle ist,
sondern **was mit den Daten geschehen kann**.

| Farbe | Bedeutung | Was gesendet werden darf |
| --- | --- | --- |
| grün | die Daten verlassen das Unternehmen nicht | alles; personenbezogene Daten mit Freigabe |
| gelb | sie verlassen es vielleicht, aber unter verbindlicher Zusage | Unternehmensinformationen; personenbezogene mit Freigabe |
| rot | externer Anbieter, Daten können abfließen | keine Unternehmensinformationen, keine personenbezogenen Daten |

## Was das Gateway durchsetzt

Das Gateway sieht nicht in die Anfragen. Es hält sich an das, was beim Ausstellen eines
Schlüssels gesagt wurde:

- Jeder Schlüssel hat eine **höchste Farbe**. Ziele darüber benutzt er nie.
- Ein Schlüssel mit **Freigabe für personenbezogene Daten** benutzt nie rot.
- Die Farbe eines Modellnamens ist die **schlechteste** seiner Ziele – wer von grün nach rot
  ausweichen kann, ist nicht grün.

Der Chat kann für ein Gespräch eine strengere Farbe verlangen; sie verschärft die des
Schlüssels, lockert sie aber nie.
