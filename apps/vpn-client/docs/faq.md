# Häufige Fragen

**Die Seite zeigt „kein Tunnelgerät“.**
Die App wurde ohne Tunnelrecht installiert, oder es ist eine Generalprobe – die
bekommt nie einen Tunnel. Der Betreiber installiert die App mit Tunnelrecht neu.

**Die Seite zeigt „nicht eingerichtet“.**
Die `.ovpn`-Datei fehlt oder ist nicht lesbar. Unter „Letzter Fehler“ steht, was
genau fehlt.

**Die Seite zeigt „Paketfilter fehlt“.**
Ohne Paketfilter startet die App keinen Tunnel – mit Absicht. Bitte den Betreiber
des Servers informieren.

**Der Tunnel steht, aber ein Ziel antwortet nicht.**
Prüfen Sie Adresse und Port des Ziels und ob der Dienst dort läuft. Ein Ziel mit
einem Namen statt einer IP-Adresse bekommt keine eigene Route.

**Warum werden Zeilen aus meiner .ovpn-Datei verworfen?**
Die Gegenseite soll nicht in den Server hineinregieren: Routen, DNS, Skripte und
die Fernsteuerung von OpenVPN werden entfernt. Die Seite nennt, was verworfen wurde.

**Kann ich WireGuard benutzen?**
Diese Version kennt nur OpenVPN.
