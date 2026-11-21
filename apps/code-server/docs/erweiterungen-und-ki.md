# Erweiterungen und KI

## Vorinstalliert

| Erweiterung | Wozu |
|---|---|
| Claude Code | KI-Assistent im Editor und im Terminal (`claude`) |
| ABAP Remote Filesystem | ABAP-Quelltext eines SAP-Systems wie Dateien bearbeiten |
| SAP CDS Language Support | Unterstützung für CAP und CDS |

Erweiterungen kommen aus **Open VSX**, nicht aus dem Marktplatz von
Microsoft. Die meisten bekannten Erweiterungen gibt es dort auch.

## Weitere Erweiterungen

- **Selbst**: in der Seitenleiste *Erweiterungen* suchen und installieren.
  Sie bleiben in Deinem Speicher.
- **Für alle Arbeitsplätze**: Die Verwaltung trägt sie in der Konfiguration
  der Instanz ein (`IDE_EXTENSIONS`). Fehlende werden beim nächsten Start
  nachinstalliert.

Was Du selbst entfernst oder aktualisierst, bleibt so – der Start ergänzt
nur fehlende vorinstallierte Erweiterungen.

## Claude Code anmelden

- Hat die Verwaltung einen Schlüssel hinterlegt, funktioniert `claude` im
  Terminal sofort.
- Sonst meldest Du Dich beim ersten Start mit Deinem eigenen Konto an.

## Grenzen

- **ABAP Cleaner** läuft im Browser nicht, er braucht ein echtes Fenster.
  Dafür gibt es den „Desktop im Browser (Webtop)“, falls Dein Mandant ihn
  hat.
- Erweiterungen, die nur im Marktplatz von Microsoft liegen, stehen nicht
  zur Verfügung.
