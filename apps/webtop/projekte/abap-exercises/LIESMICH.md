# ABAP-Übungen — ARC-1 als MCP-Server im Projekt

Dieser Ordner bringt die SAP-Anbindung für Claude Code mit: zwei MCP-Server von
[ARC-1](https://docs.arc-1-mcp.com/), eingetragen in `.mcp.json`. Platzhalter für
URL, Mandant und Zertifikatsprüfung füllt der Containerstart aus der Instanzkonfiguration
(`ABAP_HOST`, `ABAP_PORT`, `ABAP_CLIENT`, `ABAP_INSECURE`).

- **arc-1-btp** (SAP BTP ABAP Environment): Service-Key in `service-key.json` einfügen
  (steht in `.gitignore`), Claude Code neu starten; die Anmeldung öffnet den Browser des Desktops.
- **arc-1-server** (On-Premise): `SAP_USER`/`SAP_PASSWORD` in `.mcp.json` eintragen.
  Besser als Klartext im Home wäre eine **Destination** (RFC-0033 D1) — Bauplan Stufe 5.

Beide Server stehen auf dem ARC-1-Standard: nur lesen, keine Tabelleninhalte, kein freies SQL,
keine Transporte. Schreiben erst mit `SAP_ALLOW_WRITES=true`, dann nur in `SAP_ALLOWED_PACKAGES`
(Standard `$TMP`).
