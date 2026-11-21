# MCP-Gateway – häufige Fragen

## Ich sehe das MCP-Gateway nicht in meinem Portal.

Die Verwaltung des Gateways ist der Rolle `admin` vorbehalten – wer hier
freigibt, öffnet Systeme für KI-Agenten. Alle anderen sehen „Meine
Werkzeuge“ mit ihren eigenen Aufrufen.

## Ein Werkzeug ist im Chat verschwunden.

Meist hat der Server den Text des Werkzeugs geändert. Dann ist es gesperrt,
bis die Verwaltung den neuen Text auf der Seite der Sicht bestätigt. Die
Übersicht nennt solche Sichten unter **Zu tun**.

## Der Server hat ein neues Werkzeug – warum sieht es niemand?

Neues erscheint nie von selbst. Die Verwaltung legt es auf der Seite der
Sicht (Baum unter **Zuordnen**) in die Sicht.

## Was bedeutet „403“ mit dem Hinweis auf die Ampel?

Die Ampel der Sicht ist schlechter, als der Schlüssel oder das Profil
zulässt – etwa ein roter Server hinter einem Schlüssel „Ampel höchstens
gelb“. Abhilfe: den Server anders einstufen, ihn aus der Sicht nehmen oder
beim Schlüssel „Ampel höchstens“ anheben. Der Satz in der Ablehnung nennt
den Server, der die Ampel bestimmt.

## Ich habe meinen Schlüssel verloren.

Er lässt sich nicht nachlesen; gespeichert ist nur eine Prüfsumme. Die
Verwaltung widerruft den alten und stellt einen neuen aus.

## Speichert das Gateway, was ich frage?

Nein. Jeder Aufruf wird gezählt (Zeit, Sicht, Eintrag, Ausgang, Dauer,
Datenmenge), aber Argumente und Ergebnisse stehen in keiner Zeile und in
keiner Datei.

## Mein persönlicher Zugang wird abgelehnt.

Auf **Meine Werkzeuge** steht dann „vom Server abgelehnt – erneuern“.
Erzeuge beim Anbieter ein neues Token und trage es dort ein.

## Was ist der Unterschied zwischen Schlüssel und Zugangsprofil?

Ein Schlüssel ist ein Geheimnis für eine Maschine (Agent, Pipeline). Ein
Zugangsprofil hat keinen Wert; es sagt, welche angemeldeten Personen oder
welche Chats eine Sicht benutzen dürfen.
