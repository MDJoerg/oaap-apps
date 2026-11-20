# Deploy-Token und Anlege-Erlaubnis

**Das Studio hält nie ein Recht.** Alles, was etwas verändert, gibst Du im
Augenblick der Handlung ein.

## Deploy-Token

Der Deploy-Token ist ein Schlüssel: Er erlaubt, die **Test**-Instanz neu
auszurollen.

- Er wird im **Portal des Zielknotens** auf der Seite der Test-Instanz
  erzeugt und **einmal** angezeigt.
- Er gehört **Dir** – ein Passwortmanager ist der richtige Ort.
- Produktiv-Instanzen haben bewusst **keinen** Token. Produktiv wird im
  Portal gesetzt, von einem Menschen.

**Das Studio speichert ihn nicht.** Beim Ausrollen gibst Du den Token bei
jedem Upload einzeln ein. Das Studio hält ihn für die Dauer dieser einen
Anfrage und legt ihn nirgends ab: nicht in der Datenbank, nicht im Backup,
nie in einer Adresse, nie in einem Protokoll. So steckt in jedem Ausrollen
über das Studio ein Mensch, und im Ruhezustand gibt es im Studio nichts zu
holen.

**Token verloren oder verraten?** Im Portal auf der Seite der Instanz
widerrufen und einen neuen erzeugen. Auf der Maschine tut
`sudo oaap app token revoke <instanz>` dasselbe.

## Anlege-Erlaubnis – die erste Instanz

Gibt es die Test-Instanz noch nicht, gibt es auch keinen Deploy-Token. Dann
stellt ein `server_admin` im Portal des Zielknotens unter **Instanzen** eine
**Anlege-Erlaubnis** für genau diesen Instanznamen aus:

- einmal verwendbar,
- 30 Minuten gültig,
- nur für den Test-Kanal,
- widerrufbar.

Du trägst sie im Studio **statt des Tokens** ein. Für den Ablauf ändert sich
nichts: dieselben drei Phasen, dieselben Prüfungen. Danach gibt es die
Instanz, die Erlaubnis ist verbraucht, und jede weitere Fassung läuft über
einen normalen Deploy-Token.

Für ein ZIP-Paket ohne bestehende Instanz muss der Zielknoten das Profil
`dev` haben.
