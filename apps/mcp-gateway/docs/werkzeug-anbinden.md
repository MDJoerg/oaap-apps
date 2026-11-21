# Ein Werkzeug anbinden (Claude Code, VS Code, opencode)

## Mit Schlüssel

Nach dem Ausstellen zeigt das Gateway den Befehl für Claude Code:

```bash
claude mcp add --transport http <sicht> https://<gateway>/mcp/<sicht> \
  --header "Authorization: Bearer <schlüssel>"
```

Für eine Datei (Claude Code, VS Code, opencode) gibt es den Reiter
**Vorlagen**: Werkzeug wählen, dann die Sichten eines Schlüssels oder von
Hand, **Datei bauen**, **Herunterladen**. In der Datei steht nie der
Schlüssel selbst, sondern eine Umgebungsvariable; den Wert setzt du in
deiner Umgebung.

## Mit Anmeldung (Personen)

Ist die Anmeldung eingerichtet (Reiter **Anmeldung**), meldet sich das
Werkzeug beim Anmeldedienst des Mandanten an. Der Reiter zeigt den Befehl
für Claude Code und die Datei `.mcp.json` für VS Code.

In VS Code die Datei in den geöffneten Ordner legen, dann in `/mcp` die
Sicht wählen und „Authenticate“. Nicht über den Dialog in `/mcp`
eintragen – dem fehlt die Client-ID.

Wer kein Verwalter ist, holt dieselbe Datei auf **Meine Werkzeuge** unter
„In meinem Werkzeug einrichten“.

## Die Anmeldung einrichten

Eingerichtet wird sie im Portal auf der Seite der Instanz („Anmeldung
einrichten“). Die Plattform legt die Clients beim Anmeldedienst an und gibt
dem Gateway die Werte. Die Felder `MCPGW_*` in der Konfiguration bleiben
dafür leer.
