# MCP-Gateway

Das MCP-Gateway ist die Tür, durch die KI-Werkzeuge an die Systeme des
Mandanten kommen – und zwar nur an das, was ausdrücklich freigegeben ist.
Ein KI-Client (der Chat der Plattform, Claude Code, VS Code, ein eigener
Agent) spricht mit **einer Sicht** wie mit einem einzigen MCP-Server. Welche
Server dahinterstehen und wo, erfährt er nie.

## Für wen

- **Verwaltung** (Rolle `admin`): trägt Server ein, legt Sichten an, gibt
  Werkzeuge frei, stellt Schlüssel und Zugangsprofile aus, beobachtet die
  Aufrufe.
- **Alle, die über das Gateway arbeiten** (Rollen `user`, `keyuser`):
  sehen auf **Meine Werkzeuge** ihre eigenen Aufrufe der letzten 30 Tage,
  hinterlegen dort ihren persönlichen Zugang zu einem Server und holen die
  Datei für ihr Werkzeug.

## Die Begriffe

| Begriff | Bedeutung |
| --- | --- |
| **Server** | ein MCP-Server, den das Gateway erreicht – über eine Adresse, eine Destination der Plattform oder als App des Mandanten. Jeder Server hat eine **Ampel**: grün (Daten bleiben im Unternehmen), gelb (unter verbindlicher Zusage), rot (Daten können abfließen). |
| **Katalog** | was ein Server anbietet: Werkzeuge (Tools), Ressourcen, Prompts. Nichts davon ist sichtbar, bevor es in einer Sicht liegt. |
| **Sicht** | ein MCP-Endpunkt (`/mcp/<sicht>`). Sie bündelt freigegebene Einträge aus einem oder mehreren Servern, mit eigenen Namen, festen Argumenten und Grenzen. |
| **Wirkungsklasse** | lesen, schreiben oder ausführen. Die Verwaltung vergibt sie je Werkzeug; Schlüssel und Sicht haben eine höchste Klasse. |
| **Schlüssel** | ein Geheimnis für einen Agenten oder eine Pipeline. Er wird genau einmal angezeigt. |
| **Zugangsprofil** | gilt für Personen: angemeldet am Anmeldedienst des Mandanten (Gruppen `*` oder `/pfad`) oder hergetragen von einem Chat der Plattform (`@<chat>`). |
| **Zielsystem** | wohin ein Werkzeug reicht, etwa ein SAP-System. Eine Multi-System-Sicht bietet dieselben Werkzeuge für mehrere Systeme an. |

## Was das Gateway verspricht

- Es zeigt nie etwas, das niemand freigegeben hat.
- Es hält den freigegebenen Text fest. Ändert ein Server die Beschreibung
  eines Werkzeugs, ist das Werkzeug gesperrt, bis die Verwaltung den neuen
  Text bestätigt.
- Es zählt jeden Aufruf, speichert aber **nie** Argumente oder Ergebnisse.
- Zugangsdaten liegen verschlüsselt und werden nie wieder angezeigt.

## Anleitungen

- [Einen Server eintragen und seinen Katalog lesen](server-eintragen.md)
- [Eine Sicht anlegen und Werkzeuge freigeben](sicht-freigeben.md)
- [Schlüssel und Zugangsprofile vergeben](zugang-vergeben.md)
- [Ein Werkzeug anbinden (Claude Code, VS Code, opencode)](werkzeug-anbinden.md)
- [Meine Werkzeuge: eigene Aufrufe und persönlicher Zugang](meine-werkzeuge.md)
- [Häufige Fragen](faq.md)
