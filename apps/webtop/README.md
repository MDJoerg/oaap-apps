# Webtop-Arbeitsplatz — Entwurf einer OAAP-App „Desktop im Browser"

Stand 2026-10-14. **Entwurf: ungebaut, ungemessen.** Herkunft und Bauplan:
`program/schulungsarbeitsplatz-webtop-2026-10-14.md` (Teil B, Stufen 0–2).

**Ergebnis in einem Satz (erwartet, nicht gemessen):** Der Linux-Desktop
im Browser aus `brandeis-consulting/webtop-schulung` (linuxserver Webtop,
XFCE, Selkies über WebSocket) ist eine gewöhnliche gewrappte OAAP-App wie
`apps/code-server` — ein Container je Mensch, das Gateway als Tür, zwei
Ablagen — seit Runtime 0.2.39 kann ein Dienst `shm` erklären (Stufe 0).

## Was hier liegt

| Datei | Wozu |
| --- | --- |
| `oaap-app.yaml` | das Manifest: ein Dienst auf Port 3000, Route `/` angemeldet, Ablagen `home` (`/config`) und `material` (`/schulung`), Konfiguration |
| `Dockerfile` | das Abbild nach dem Vorbild des Brandeis-Dockerfiles, **ohne Branding**: Firefox, VS Code + Claude-Code-Erweiterung + Deutsch, Node 22 + Claude Code, Eclipse 2026-06 + ADT + ABAP Cleaner, Claude Desktop, Chrome, ARC-1, **goose Desktop + CLI, opencode** (Jörg, 15.10.; Bezug beim ersten Bau zu prüfen), Starter ohne Sandbox, helles Thema, Skeleton, Init |
| `init/50-oaap-init` | läuft bei jedem Containerstart als root (linuxserver `custom-cont-init.d`): Symlink `~/Desktop/Material`, Claude Desktop ohne Hardwarebeschleunigung, Übungsprojekte, einmalig Skeleton + Starter + Git-Konfiguration + Infodatei |
| `skel/` | wird beim ersten Start ins Home kopiert: Infodatei, `.claude/CLAUDE.md`, VS-Code-Einstellungen, Autostart (Schlüsselbund, Tastatur), Terminal, leerer Schlüsselbund, Standardbrowser |
| `bin/` | `code`, `claude-desktop`, `google-chrome`, `goose-desktop` (ohne Chromium-Sandbox, mit `--disable-dev-shm-usage` zusätzlich zu `shm: 1g`, bis die Messung sagt, was reicht), `oaap-keyring`, `oaap-arc1` |
| `desktop/eclipse-adt.desktop` | Eclipse-Starter mit Adwaita (einheitlich hell) |
| `vscode/sprachpaket.js` | erzeugt beim Bau `languagepacks.json`, sonst ist VS Code erst beim zweiten Start deutsch |
| `firefox/policies.json` | Firefox deutsch und hell |
| `projekte/abap-exercises/` | Beispiel für ARC-1 im Projekt (`.mcp.json` mit Platzhaltern, nur lesend) |

## Was anders ist als bei Brandeis

| Brandeis | hier |
| --- | --- |
| `CUSTOM_USER`/`PASSWORD` je Desktop, Basic-Auth | leer — **das Gateway ist die Tür** (Forward-Auth, auch für den WebSocket; wie code-server) |
| `webtop`-CLI erzeugt Compose und Caddyfile | Instanz der Plattform; zwölf davon macht `oaap cohort` |
| ein Compose-Netz, `tn04:5173` erreichbar, `teilen` | Instanznetz je Platz (RFC-0016); „Zeigen" kommt als Vorlagenzeile (Bauplan Stufe 4) |
| `/gemeinsam` (rw alle), `/schulung` (ro) als Bind-Mounts | nur `material` (rw, je Instanz); geteilte Kohorten-Ablagen sind Bauplan Stufe 3 |
| Brandeis-Hintergrund, `TITLE=Brandeis Schulung` | neutral; `DESKTOP_TITLE` konfigurierbar; später Titel/Farben/Logo des Mandanten (RFC-0042) |
| `shm_size: 1gb` in der Compose | `services.desktop.shm: 1g` im Manifest (0.7) — Stufe 0 gebaut am 14.10. (Runtime 0.2.39, Referenz 0.1.196), am Knoten ungemessen; die Starter tragen zusätzlich `--disable-dev-shm-usage`, bis die Messung sagt, was reicht |
| `mem_limit 6g`, `cpus 2` | `oaap app resources <instanz> --memory 6g --cpus 2` (Runtime 2.18) |
| KURS-INFO nennt `tnNN` und das SAP-System | `ARBEITSPLATZ-INFO.txt` nennt Benutzer und SAP-System aus der Konfiguration; die Adresse gibt RFC-0043 |

## goose und opencode (Anforderung Jörg, 15.10.2026)

Zwei weitere KI-Agenten als Desktop-Werkzeuge neben Claude Desktop und Claude Code:

| | goose (Block) | opencode (SST) |
| --- | --- | --- |
| Form | Electron-Desktop-App + CLI | CLI/TUI (`npm i -g opencode-ai`); Desktop-Fassung: Linux-Build **in Stufe 1 prüfen** |
| Bezug im Entwurf | `.deb` aus den GitHub-Releases, Version per Build-Arg festgenagelt — **Asset-Namen beim ersten Bau prüfen** | npm, Version per Build-Arg |
| Start | `bin/goose-desktop` ohne Sandbox, `shm: 1g` | Terminal; der Desktop-Eintrag öffnet ein Terminal mit `opencode` |
| Anbieter | Anthropic, OpenAI, **OpenAI-kompatible Endpunkte** (`GOOSE_PROVIDER`, `OPENAI_BASE_URL`) | dito über `~/.config/opencode/opencode.json` |
| Für uns | beide können das KI-Gateway (RFC-0023, OpenAI-Protokoll) heute schon nutzen — Claude Code braucht dafür erst das Anthropic-Protokoll | |

Anmeldung an den Anbietern im Desktop wie bei Claude: der Browser läuft drinnen. Konfiguration als Saat je Platz, Schlüssel als Instanzgeheimnis (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`/`OPENAI_BASE_URL` im Manifest).

## Bezug und Lizenz (unverändert zu Brandeis)

- SAP ADT und ABAP Cleaner vom SAP-Update-Site / GitHub (Eclipse-Plugins, öffentlich).
- **ADT for VS Code nicht** (Microsoft-Marktplatz, SAP-Developer-Lizenz — CURRENT_STATE 205).
- Claude Desktop aus Anthropics apt-Repo (Linux-Beta), Chrome aus Googles Repo, VS Code aus Microsofts Repo.
- Kein SAP GUI for Java (nur mit S-User ladbar).

## Nächster Schritt

Stufe 0 (`shm` in der Runtime), dann dieses Verzeichnis nach
`oaap-apps/apps/webtop/` übernehmen, Store-Eintrag und README-Zeile,
`check-store.py`, erste Instanz auf `oaap-test` nach der Messliste
(Bauplan Teil D). Nichts hiervon wurde gebaut: der Dockerfile ist aus dem
Brandeis-Stand v5 abgeleitet und muss den ersten Bau noch bestehen.
