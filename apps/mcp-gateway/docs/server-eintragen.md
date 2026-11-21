# Einen Server eintragen und seinen Katalog lesen

Für die Verwaltung (Rolle `admin`).

## Apps des Mandanten kommen von selbst

Apps des Mandanten mit einer MCP-Tür (etwa Hilfe, Recherche, Vorgänge)
gleicht das Gateway alle 15 Minuten mit der Plattform ab. Sie stehen im
Reiter **Server** als „Quelle der Plattform“. Eine neue Quelle startet mit
Ampel **rot** – setze sie auf die Farbe, die zu ihr passt. Mit **Quellen
jetzt einlesen** geht es sofort.

## Einen fremden Server von Hand eintragen

1. Reiter **Server** → **+ Server anlegen**.
2. **Name**: kurz, ohne Leerzeichen. Unter diesem Namen erscheint der
   Server überall.
3. **Adresse**: die URL des MCP-Servers (`https://…/mcp`) oder
   `destination:<name>`. Mit einer Destination setzt die Plattform die
   Zugangsdaten ein, und das Gateway hält kein Geheimnis.
4. **Zugang**: `none`, `bearer`, `basic` oder eine eigene Kopfzeile. Für
   Server, bei denen jede Person ihren eigenen Zugang mitbringt (Jira,
   GitHub …), wähle `personal` – dann trägt jede Person ihr Token auf
   **Meine Werkzeuge** ein.
5. **Ampel** wählen. Sie sagt, was mit Daten geschehen kann, die zu diesem
   Server gehen.
6. **Speichern**.

Für eigene Zugangsdaten braucht das Gateway den Tresorschlüssel
(`MCPGW_MASTER_KEY` in der Konfiguration der Instanz). Fehlt er, steht ein
Hinweis über der Liste.

## Den Katalog lesen

Auf der Seite des Servers (Name in der Liste anklicken) **Katalog jetzt
lesen**. Danach liest das Gateway ihn regelmäßig neu. Die Seite zeigt alle
Werkzeuge, Ressourcen und Prompts – und in welchen Sichten sie schon liegen.

## Ändern, umbenennen, löschen

Alles auf der Seite des Servers. Beim Umbenennen ziehen Katalog und
Einträge mit; die Namen in den Sichten bleiben, Clients merken nichts.
