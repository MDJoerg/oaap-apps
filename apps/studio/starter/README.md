# OAAP Starter — das Paket, das ankommt

Das kleinste vollständige OAAP-Paket: ein Manifest, ein Dockerfile, eine
Datei Anwendungscode. Es tut absichtlich fast nichts — es zeigt den
**Rahmen**, in den eine eigene App hineingehört.

Gedacht für den Fall, dass ein Projekt schon läuft und nur noch das
Deployment fehlt: Statt aus einer Beschreibung zu erraten, wie ein
OAAP-Paket aussieht, nimmt man ein Paket, das nachweislich durch die
Prüfung geht, und ersetzt den Inhalt.

Das Studio gibt dieses Verzeichnis als `oaap-starter.zip` heraus
(Startseite → „Neues Projekt onboarden"), zusammen mit dem
**Plattform-Briefing**, in dem alle Regeln stehen.

## Was drin ist

| Datei           | Wofür                                                        |
| --------------- | ------------------------------------------------------------ |
| `oaap-app.yaml` | das Manifest — jede Zeile kommentiert                        |
| `Dockerfile`    | Multi-Arch-Bau, fester numerischer Benutzer                  |
| `app.py`        | die App: Identität aus den Kopfzeilen, `/data`, `/healthz`   |

## Ausprobieren

```sh
# ohne Plattform, nur um zu sehen, was passiert:
mkdir -p /tmp/starter-data
docker build -t oaap-starter .
docker run --rm -p 8000:8000 -v /tmp/starter-data:/data oaap-starter
# Die Startseite lehnt ohne Gateway-Kopfzeilen ab — so ist es gedacht:
curl -H "X-OAAP-User: kim" -H "X-OAAP-Roles: user" http://localhost:8000/
```

Auf die Plattform kommt es wie jedes andere Paket: im Studio unter
„Paket prüfen" hochladen, dann ausrollen. Gibt es die Instanz noch
nicht, stellt ein `server_admin` im Portal eine **Anlege-Erlaubnis** aus
und man trägt sie statt des Deploy-Tokens ein.

## Was man ändern muss

1. `app.id`, `app.name`, `app.description` im Manifest — die Kennung ist
   danach unveränderlich.
2. `app.version` bei **jedem** Deployment hochzählen.
3. Den Inhalt von `app.py` durch die eigene Anwendung ersetzen.

Was man **nicht** ändern sollte, ohne den Grund zu kennen: den einen
HTTP-Port, den Verzicht auf einen eigenen Login, den Speicher unter
`/data`, den Gesundheitspfad und den nicht-privilegierten Benutzer im
Dockerfile. Die Begründungen stehen als Kommentare in den Dateien und
ausführlich im App Deployment Contract.
