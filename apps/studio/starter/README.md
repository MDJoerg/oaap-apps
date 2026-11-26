# Starter — das Paket, das ankommt

Das kleinste vollständige OAAP-Paket: ein Manifest, ein Dockerfile, eine
Datei Anwendungscode. Es tut absichtlich fast nichts — es zeigt den
**Rahmen**, in den eine eigene App hineingehört, und darunter, was die
Plattform einer App heute **zusätzlich** anbietet.

Gedacht für den Fall, dass ein Projekt schon läuft und nur noch das
Deployment fehlt: Statt aus einer Beschreibung zu erraten, wie ein
OAAP-Paket aussieht, nimmt man ein Paket, das nachweislich durch die
Prüfung geht, und ersetzt den Inhalt.

Das Studio gibt dieses Verzeichnis als `oaap-starter-<version>.zip` heraus
(Version = `app.version` im Manifest, Ordner darin gleich benannt)
(Startseite → „Briefing und Starter-Paket“), zusammen mit dem
**Plattform-Briefing**, in dem alle Regeln stehen.

## Was drin ist

| Datei | Wofür |
| --- | --- |
| `oaap-app.yaml` | das Manifest (0.23) — jede Zeile kommentiert, die optionalen Fähigkeiten auskommentiert darunter |
| `Dockerfile` | Multi-Arch-Bau, Debians Korrekturen beim Bau, fester numerischer Benutzer |
| `app.py` | die App: Identität aus den Kopfzeilen (Anker `X-OAAP-User-Id`), `/data`, `/healthz`, Anzeige der Plattformtüren, der Rahmen der Plattform (Kit `/platform/theme.css`, Rahmenskript `/platform/app.js`) |
| `docs/index.md` | die Hilfe der App (`docs:` im Manifest) — Pflicht für jede App mit Oberfläche |

## Ausprobieren

```sh
# ohne Plattform, nur um zu sehen, was passiert:
mkdir -p /tmp/starter-data
docker build -t oaap-starter .
docker run --rm -p 8000:8000 -v /tmp/starter-data:/data oaap-starter
# Die Startseite lehnt ohne Gateway-Kopfzeilen ab — so ist es gedacht:
curl -H "X-OAAP-User-Id: 0b7c…" -H "X-OAAP-User: kim" \
     -H "X-OAAP-Roles: user" http://localhost:8000/
```

Auf die Plattform kommt es wie jedes andere Paket: im Studio unter
„Paket prüfen“ hochladen, dann ausrollen — oder im Portal unter
„Test-Instanz anlegen“ → „Aus einem hochgeladenen Paket (ZIP)“. Den
**Namen der Instanz** wählt, wer sie anlegt (er muss nicht der
App-Kennung entsprechen und lässt sich später umbenennen). Gibt es die
Instanz noch nicht und soll das Studio sie anlegen, stellt ein
`server_admin` im Portal eine **Anlege-Erlaubnis** für genau diesen
Namen aus, die man statt des Deploy-Tokens einträgt.

## Was man ändern muss

1. `app.id`, `app.name`, `app.description` im Manifest — die Kennung ist
   danach unveränderlich. Der Name ohne „OAAP“, derselbe auf der Seite
   (`APP_NAME` in `app.py`) und in `docs.title`.
2. `app.version` bei **jedem** Deployment hochzählen.
3. Den Inhalt von `app.py` durch die eigene Anwendung ersetzen.
4. Von den optionalen Abschnitten genau die einkommentieren, die die App
   nutzt. Die Startseite zeigt danach, ob die Tür angekommen ist. Hält
   die App Daten, gehört eine MCP-Tür (`mcp:`) mit Lesewerkzeugen dazu.
5. `docs/` mit der eigenen Hilfe füllen: Übersicht, eine Anleitung je
   Hauptaufgabe, häufige Fragen.

Was man **nicht** ändern sollte, ohne den Grund zu kennen: den Rahmen
der Plattform (Kit, Rahmenskript, Kopfleiste und Fußzeile), den einen
HTTP-Port, den Verzicht auf einen eigenen Login, den Speicher unter
`/data`, den Gesundheitspfad, die `apt-get upgrade`-Zeile und den
nicht-privilegierten Benutzer im Dockerfile. Die Begründungen stehen als
Kommentare in den Dateien und ausführlich im App Deployment Contract.

## Ein Wort zu personenbezogenen Daten

Der Digitale Zwilling ist heute **append-only** — er hält jede Änderung
fest und kennt kein Löschen. Personenbezogene Daten (erst recht
medizinische) gehören deshalb **nicht im Klartext** hinein, sondern bleiben in der App
(unter `/data`, wo sie mit der Instanz gelöscht werden können). In den
Zwilling gehören Schlüssel, Titel, Relationen und unkritische Fakten.
