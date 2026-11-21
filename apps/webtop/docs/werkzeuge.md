# Die Werkzeuge

| Werkzeug | So startest Du es | Hinweis |
|---|---|---|
| **Eclipse ADT** | Verknüpfung auf dem Desktop | mit ABAP Cleaner; Arbeitsbereich `~/workspace` |
| **VS Code** | Verknüpfung oder `code` im Terminal | deutsch eingerichtet |
| **Claude Code** | `claude` im Terminal, in VS Code über das Claude-Symbol | Anmeldung mit Deinem Konto oder über einen Schlüssel der Instanz |
| **Claude Desktop** | Verknüpfung auf dem Desktop | Anmeldung mit Deinem claude.ai-Konto; der Browser dafür läuft im Desktop |
| **goose** | Verknüpfung oder `goose` im Terminal | KI-Agent; Anbieter in der App wählen |
| **opencode** | `opencode` im Terminal | KI-Agent; Anbieter in `~/.config/opencode/opencode.json` |
| **Google Chrome** | Standardbrowser | auch für Deine eigene Web-App |
| **Firefox** | Verknüpfung auf dem Desktop | |

## Eclipse mit einem SAP-System verbinden

1. Eclipse starten.
2. *File → New → ABAP Project → New System Connection*.
3. Hostname, Port und Mandant stehen in **ARBEITSPLATZ-INFO.txt** auf dem
   Desktop, wenn der Betreiber sie eingetragen hat.
4. Anmelden mit Deinem SAP-Benutzer. Das Passwort fragt Eclipse; es steht
   nirgends in einer Datei.

## KI-Agenten mit dem KI-Gateway

goose und opencode können statt eines eigenen Kontos das KI-Gateway Deines
Mandanten nutzen, wenn der Betreiber dessen Adresse in der Konfiguration
eingetragen hat. Frage im Zweifel die Verwaltung.

## ARC-1 im Beispielprojekt

Im Projekt `~/projekte/abap-exercises` ist ARC-1 als Werkzeug für Claude
Code vorbereitet – nur lesend. Damit kann die KI ABAP-Objekte des
SAP-Systems nachschlagen, aber nichts ändern.
