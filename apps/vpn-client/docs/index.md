# VPN-Client

Der VPN-Client verbindet Ihren Mandanten mit einem System hinter einem **fremden
OpenVPN-Server** – zum Beispiel einem Rechner in einem Kundennetz oder zu Hause,
für den Sie eine `.ovpn`-Datei bekommen haben.

Der Tunnel lebt nur in dieser einen App. Was dahinter liegt, wird für andere Apps
als **Destination** der Plattform erreichbar (`through:<instanz>/<name>`). Die App,
die das Ziel benutzt, merkt keinen Unterschied zu einem direkten Ziel.

## Für wen

Für **Administratoren** des Mandanten und den Betreiber des Servers. Die Seite der
App sehen nur Administratoren.

## Was die App tut

- **Hält den Tunnel** und zeigt auf ihrer Seite, ob er steht, seit wann und was
  zuletzt schiefging.
- **Traut der Gegenseite nichts.** Aus der `.ovpn`-Datei werden Routen, DNS,
  Skripte und Ähnliches entfernt und auf der Seite benannt. Ein Paketfilter lässt
  aus dem Tunnel nur Antworten auf eigene Verbindungen herein. Steht der Filter
  nicht, startet der Tunnel nicht.
- **Prüft ihre Ziele** alle 30 Sekunden und zeigt, ob jedes antwortet.
- **Meldet den Zustand** an den Server, damit die Flotte „VPN aus“ oder „Ziel
  antwortet nicht“ sieht.
- **Hält keinen Schlüssel des Ziels** – den trägt die Destination.

## Anleitungen

- [Tunnel einrichten](tunnel-einrichten.md)
- [Ziele und Destinationen](ziele-und-destinationen.md)
- [Zustand prüfen](zustand-pruefen.md)
- [Häufige Fragen](faq.md)
