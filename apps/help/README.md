# OAAP-Hilfe (`help`) — RFC-0078 Stufe 1

Statisches Hilfewissen für jede KI: die OAAP-Dokumentation (`oaap-docs`)
samt der aus dem Code erzeugten CLI-Referenz (`oaap help --markdown`) als
Volltextindex. **Keine eigene KI** (D2): `help` sucht und liefert Abschnitte
mit Herkunft, Prüfstand und Link zum Original; zusammenfassen und führen tut
die KI, die die Person ohnehin benutzt. **Kein Zustand des Knotens** (D1).

## Drei Türen, ein Kern

| Tür | Pfad | Was |
|---|---|---|
| MCP | `/mcp` (Streamable HTTP, JSON) | Tools `help_search`, `help_read`, `help_topics`; Resources `help://<quelle>/<id>`; Prompts `guide` (Schritt für Schritt), `cli` (Befehl finden) |
| API | `/api/v1/search?q=`, `/api/v1/docs/<id>[?section=]`, `/api/v1/topics[?prefix=]`, `/api/v1/status` | dieselben Antworten als JSON |
| Seite | `/`, `/doc/<quelle>/<id>` | Suche, Leseansicht mit Inhaltsverzeichnis und „Original öffnen“ |
| Verwaltung | `/admin` (nur `admin`) | Quellen, letzter Abruf, „Jetzt holen“, eigene Notizen |

Alle Türen rufen dieselben Funktionen in `library.py`; die Sichtbarkeit
(`index.visible`) steht dort, nicht in den Türen.

## Quellen

- **`oaap-docs`** (Priorität 50): ein `tar.gz` des Doku-Repositorys, geholt
  über die Destination **`docs-origin`** — beim Start, dann alle
  `HELP_FETCH_HOURS` Stunden und auf Knopfdruck. Neue Doku braucht kein
  Neuausrollen. Ein Archiv wird ganz angenommen oder ganz abgelehnt
  (abgeschnitten, `..`, Link, > 50 MB, > 5000 Einträge, Weiterleitung): dann
  antwortet der alte Index weiter, und der Fehler steht im Status.
  `AGENTS.md`/`CLAUDE.md` werden nicht aufgenommen.
- **`local`** (Priorität 90): Notizen, die ein admin auf `/admin` ablegt.
  Eine Notiz mit derselben `id` wie ein Plattformtext **überdeckt** ihn auf
  diesem Knoten; das Original bleibt als `oaap-docs:<id>` lesbar.

Der Kopf eines Dokuments (RFC-0078 §4, geprüft von
`oaap-docs/tools/check-docs.py`) liefert `id`, Titel, Zielgruppe,
Versionsbereich und Prüfstand.

## Einrichten (zentrale Instanz)

```sh
sudo oaap app install <paket> --name help
sudo oaap destination add docs-origin --target https://codeload.github.com/MDJoerg/oaap-docs
sudo oaap destination bind help docs-origin
sudo oaap app config set help HELP_WEB_TEMPLATE "https://github.com/MDJoerg/oaap-docs/blob/main/{path}"
```

Zieht das Repository um (RFC-0078 §7), ändern sich nur die Destination und
die Vorlage. Forgejo: `--target https://<host>/<org>/oaap-docs`,
`HELP_ARCHIVE_PATH=/archive/main.tar.gz`. Die Destination muss **direkt**
auf das Archiv zeigen; `help` folgt keiner Weiterleitung (sie führte an der
Destination vorbei). Bei GitHub ist das `codeload.github.com`, nicht
`github.com`.

Claude Code anbinden:

```sh
claude mcp add --transport http oaap-help https://<adresse-der-instanz>/mcp
```

## Konfiguration

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `HELP_ARCHIVE_PATH` | `/tar.gz/refs/heads/main` | Pfad des Archivs hinter `docs-origin` |
| `HELP_WEB_TEMPLATE` | leer | Link zum Original, `{path}` = Pfad im Repository; leer = keine Links |
| `HELP_FETCH_HOURS` | `6` | Abstand der Abrufe |
| `HELP_AUDIENCE_FILTER` | `off` | Zielgruppenfilter; die zentrale Instanz zeigt alles (D8), der Filter für Kundenknoten kommt mit Stufe 2 |

## Grenzen in Stufe 1

- Die Route `/` ist `public`: Das passt zur zentralen Instanz (D8). Auf einem
  Kundenknoten mit Zielgruppenfilter braucht es eine angemeldete Route — das
  löst Stufe 2.
- Noch nicht: `seed` (Doku im Paket für Knoten ohne Destination),
  `catalog`/`apps`/`marketplace` als Quellen, Bilder aus dem Archiv,
  Versionsfilter aus der Plattformversion (O1; der Client kann `version`
  mitgeben).

## Test

```sh
python3 test_help.py
```

Ohne Knoten: eine nachgebaute Destination liefert Archive; geprüft werden
alle Türen und die Messpunkte M3–M7 des RFC.
