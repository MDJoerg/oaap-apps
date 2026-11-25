# Chat — der Plattform-Chat (RFC-0074 Stufen 2 und 3)

Assistenten mit den Werkzeugen je einer Sicht des MCP-Gateways, ein Modell
über das KI-Gateway, der Verlauf als Datei. Seit 0.2.0 legen die Leute
Assistenten selbst an — privat, für ein Team oder (als `tenant_admin`) für
den ganzen Mandanten. Das ist I-41 Schritt 4 in
seiner kleinsten Form: Ein Chat ist eine App, und der Assistent hat keine
Rechte.

```text
Browser ──Sitzung──▶ Chat
Chat ──via-Tür auf der eigenen Seite──▶ MCP-Gateway ──▶ App   (als die Person, Verfahren A)
Chat ──Destination ki──▶ KI-Gateway ──▶ Modell               (Schlüssel bleibt auf der Plattform)
Chat ──via-Tür /platform/ai/via/<gw>/──▶ KI-Gateway ──▶ Modell (ab 0.16.0 ohne Destination, als die Person)
```

## Was die App zusagt

- **Jeder Werkzeugaufruf läuft als die Person, die gerade schreibt.**
  Die Sicht begrenzt die Werkzeuge, die App dahinter die Daten. Anna und
  Bert sehen im selben Chat mit demselben Assistenten verschiedene Dinge.
- **Der Browser treibt die Schleife (D7).** Jede Runde ist eine eigene
  Anfrage, also mit frischer Zusicherung der Plattform. Offene
  Werkzeugaufrufe laufen **am Anfang** einer Runde, der Modellzug danach —
  ein langsames Modell altert nie die Zusicherung, mit der ein Werkzeug
  gerufen wird. Der Chat speichert keine Zusicherung und hat keinen
  eigenen Zugang zum Gateway: ohne Zusicherung keine Runde.
- **Was etwas ändert, läuft erst nach Zustimmung, jedes Mal (D6).** Die
  Person sieht Werkzeug und Argumente und klickt *Ausführen* oder
  *Ablehnen*. Ob ein Werkzeug liest, sagt das MCP-Gateway (`_meta`
  `oaap/effect`, ab Gateway 0.7.1) — die Wirkungsklasse, die der Mandant
  in der Sicht gesetzt hat. Fehlt diese Angabe, gilt das Werkzeug als
  schreibend. Der `readOnlyHint` des Herstellers zählt nicht.
- **Der Verlauf** liegt unter `/data/people/<Kennung der Person>/`, eine
  JSON-Datei je Gespräch. Nur diese Person liest, schreibt und löscht ihn.
  Er enthält, was die Werkzeuge geantwortet haben — also Daten der
  Person; eine Sicherung von `/data` enthält sie auch.
- **Wer welchen Assistenten sieht (D4).** *privat*: nur die Person, die ihn
  angelegt hat. *Team*: wer im Team die Rolle „Assistenten des Teams
  benutzen“ (`assistant.use`) hat; pflegen darf ihn, wer „… pflegen“
  (`assistant.manage`) hat. *Mandant*: alle, angelegt nur von
  `tenant_admin`. Geprüft wird bei **jeder Runde** — wem das Recht entzogen
  wird, dessen nächste Runde läuft nicht mehr. Sind die Berechtigungen nicht
  lesbar, sieht niemand einen Team-Assistenten.
- **Die Assistenten** liegen in `/data/assistants.json`, im Austauschformat
  von `oaap.ai.assistants` — Export und Import auf der Seite
  („Assistenten …“). Ein Import gilt ganz oder gar nicht.
- **/Befehle:** Hat ein Assistent `commands: true`, bietet der Chat die
  Prompts seiner Sicht als `/name …` an (`/wochenbericht pA` oder
  `/wochenbericht projekt=pA`); der Text des Prompts wird die Nachricht.
- **Zustimmung im Gateway:** Ein bestätigter Aufruf trägt
  `X-OAAP-Confirmed: yes`; das MCP-Gateway (ab 0.7.2) schreibt es in die
  Messzeile (`confirmed`).
- **Antworten als Markdown, Diagramme mit Mermaid (seit 0.3.0).** Der
  Renderer (`static/markdown.js`, eigener Code ohne Abhängigkeiten) escaped
  den Text des Modells zuerst vollständig und verwandelt dann nur wenige
  Auszeichnungen; rohes HTML aus einer Antwort erscheint nie als HTML, Links
  nur mit http(s)/mailto. ```` ```mermaid ```` wird zum Diagramm — Mermaid
  11.4.1 liegt der App bei (`static/mermaid.min.js`, MIT, SHA-256
  `a43bc1af…6f9e`), läuft im strengen Modus und wird nur geladen, wenn eine
  Antwort ein Diagramm enthält. Nichts kommt von außen: auch ein
  geschlossener Mandant sieht die Diagramme.
- **Gespräche umbenennen und löschen** (seit 0.3.0), Knöpfe im Kopf.
- **„Einrichtung prüfen“** (seit 0.3.0, `GET /api/setup?assistant=…`): der
  Chat prüft echt, der Reihe nach — Client-Schlüssel, Gateway im Mandanten,
  die Sicht als die angemeldete Person (eine Runde), Destination `ki`,
  Modellname am KI-Gateway — und nennt beim ersten fehlenden Teil den
  nächsten Schritt (bei fehlendem Zugangsprofil mit der vermuteten Gruppe
  `@<mandant>-<app>`). Fehlt Client oder Modell, zeigt die Seite das beim
  Öffnen von selbst.
- **Layout (seit 0.5.0).** Kopfzeile (Leiste ein/aus, Gesprächstitel, Menü ⋯ mit
  Umbenennen, Löschen, Einrichtung prüfen, Vollbild, Einstellungen) und Eingabe
  (mit der Wahl des Assistenten für ein neues Gespräch) stehen fest, nur der
  Verlauf scrollt. Die Leiste lässt sich einklappen, auf schmalen Bildschirmen ist
  sie eine Schublade. Gespräche sind durchsuchbar und nach Assistent oder Zeit
  gruppiert, jede Gruppe einklappbar. Assistenten haben eine eigene Seite
  (`#assistenten`) mit Karten, Suche, Filter und „Neuer Assistent“.
  **Einstellungen** (hell/dunkel/System, Hintergrund des Verlaufs, Dichte,
  Schriftgröße, Gruppierung) gelten je Browser (localStorage) -- ohne Speicher
  gelten die Vorgaben.
- **Bedienung (seit 0.4.1).** Die eigene Frage steht sofort im Verlauf, darunter
  eine Denkanzeige. `/` öffnet eine Suchliste (Pfeiltasten, Enter/Tab, Esc): die
  Prompts der Sicht und Befehle des Chats selbst (`/neu`, `/pruefen`, `/umbenennen`,
  `/assistenten`), die im Browser laufen und nie ans Modell gehen; ein Prompt mit
  gleichem Namen geht vor. Hat der Assistent keine Befehle, sagt die Liste das.
  „Einrichtung prüfen“ antwortet in einem Dialog. Kopf- und Eingabezeile bleiben
  stehen, nur der Verlauf scrollt.
- **Ansichten von MCP Apps (seit 0.4.0, RFC-0075).** Bei Assistenten mit
  `views: true` zeigt der Chat die Ansicht, die ein Werkzeug mitbringt
  (`_meta.ui.resourceUri`), unter dem Ergebnis. Sie läuft im Sandbox-Proxy auf
  der zweiten Herkunft der Instanz (`/ui-sandbox`, Manifest 0.13, Plattform ab
  0.1.245): ohne Anmeldung und Kekse, im inneren iframe ohne
  `allow-same-origin`, mit strenger CSP und in dieser Stufe ohne fremde
  Domains. Was die Ansicht will, geht durch die Türen des Chats als die
  Person: Werkzeuge nur mit `app` in ihrer `visibility`, schreibende nur nach
  der Rückfrage des Chats; Links nur http(s); vorgeschlagener Text landet im
  Eingabefeld. Werkzeuge nur für Ansichten sieht das Modell nicht.
- **Grenzen:** höchstens sechs Modellzüge je Nachricht, ein Modellzug
  höchstens `CHAT_MODEL_TIMEOUT` Sekunden (240).

## Aufsetzen

1. Ein MCP-Gateway im Mandanten mit einer Sicht, deren Zugänge `platform`
   enthalten, und einem Zugangsprofil `@<chat-instanz>` (zahlt die Aufrufe):

   ```sh
   docker exec oaap-app-<gw> python3 /srv/app.py profile create chat \
     --groups @<mandant>-chat --views <sicht> --max-effect write --cost-center <ks>
   ```

2. Ein KI-Gateway mit einem Modellnamen (z. B. `standard`) und einem
   Schlüssel nur für diesen Namen; daraus eine Destination `ki`:

   ```sh
   sudo oaap app destination add ki --tenant <mandant> --kind http \
     --target http://<knoten>:<port>/v1 --auth bearer --secret-stdin
   sudo oaap app destination bind <mandant>-chat ki --tenant <mandant>
   ```

3. Rollen im Mandanten („Rollen & Rechte“ oder `oaap authz`): aus den
   Vorlagen `assistant_user` und `assistant_keyuser` des Chats Rollen
   bauen und Personen mit dem Team als Kontext zuordnen:

   ```sh
   oaap authz role-add --tenant <m> --app chat --template assistant_keyuser --name "Chat: pflegen"
   oaap authz collection-add --tenant <m> --name "Chat Keyuser" --role "Chat: pflegen"
   oaap authz assign --tenant <m> --user <m>.anna --collection "Chat Keyuser" --context team=service
   ```

4. Optional ein Assistent für alle aus der Konfiguration, `CHAT_ASSISTANT`
   (Format `oaap.ai.assistants`); ihn ändert niemand im Chat:

   ```json
   {"id": "projekte", "title": "Projekt-Assistent", "view": "kp-i",
    "gateway": "<mandant>-mcpgw", "model": "standard",
    "greeting": "Hallo! …", "system": "Du bist … Erfinde nichts.",
    "visibility": {"scope": "tenant"}, "owner": "tenant"}
   ```

   Ein Feld, das nach einem Recht aussieht (`token`, `roles`, …), macht
   den Assistenten ungültig; dann läuft keine Runde.

5. `CHAT_MODEL_EXTRA` (optional): Felder, die jeder Modellzug zusätzlich
   trägt, z. B. `{"reasoning": {"effort": "none"}}` für `qwen3:8b` auf
   einer CPU (RFC-0074 §6 Befund 5).

### „Keine Zusicherung der Plattform für diese Anfrage“

Jede Runde braucht die Zusicherung, die identity für die angemeldete
Person unterschreibt. Fehlt sie, hat das zwei mögliche Ursachen, die die
App nicht unterscheiden kann: Die Anfrage kam mit einem Schlüssel statt
aus einer Anmeldung, oder dem Knoten fehlt sein Zusicherungsschlüssel.
Den zweiten Fall zeigt `sudo oaap status` („Assertion key: MISSING“), und
`sudo oaap update` behebt ihn. Ab Plattform 0.1.302 hat jeder Knoten den
Schlüssel; vorher fehlte er auf Knoten ohne Principal Propagation
(Fund auf oaapx01, 18.11.2026).

## Modellname als Auswahl, Werkzeugaufrufe ausblendbar (0.6.0)

- Das Formular **Assistent** wählt den Modellnamen aus der Liste, die der
  Schlüssel der Destination `ki` erreicht (`GET api/models`, mit Ampel).
  Ein frei getippter Name scheiterte bis 0.5 erst beim ersten Gespräch
  (bec, 2026-11-18, L27). Ein Name, den der Schlüssel nicht (mehr)
  erreicht, bleibt sichtbar und wird benannt -- mit dem Hinweis, ihn im
  KI-Gateway beim Schlüssel freizugeben (dort seit 0.8.0 ohne neuen
  Schlüssel).
- **Einstellungen → Werkzeugaufrufe: zeigen / ausblenden** (I-62 W4), für
  Aufzeichnungen, Schulungen und Einsteiger. Freigaben vor dem Schreiben
  und Ansichten der Werkzeuge (MCP Apps) bleiben sichtbar. Gilt für diesen
  Browser.

## Suchhilfe für die Sicht (0.6.1)

Das Feld **Sicht** im Formular „Assistent“ schlägt beim Tippen die Sichten
vor, die du aus diesem Chat öffnen kannst (`GET api/views` → das Gateway
über die via-Tür, `/_views`, MCP-Gateway ab 0.14.0), mit Zahl der Werkzeuge
und Ampel. Als Verwaltung des Mandanten siehst du alle Sichten; die übrigen
sind als „nicht für diesen Chat“ markiert, und unter dem Feld steht der
Grund. Ein Name, den das Gateway für dich nicht kennt, wird benannt.

## Werkzeuge, die für dich noch nicht eingerichtet sind (0.7.0)

Jede Assistenten-Karte zeigt „2 Werkzeuge noch nicht eingerichtet – einrichten“,
wenn ein Server der Sicht deinen eigenen Zugang braucht (Verfahren U) oder
ihn abgelehnt hat. Der Link führt auf „Meine Werkzeuge“ des Gateways.
„Einrichtung prüfen“ nennt dieselben Server mit Hinweis und nächstem
Schritt. Der Chat fragt dafür das Gateway über die via-Tür
(`/_readiness`, MCP-Gateway ab 0.17.0). Das Gateway kennt keine
Assistenten, die Zuordnung Assistent → Sicht macht der Chat.

Behoben: Ein Assistent mit Ansichten (MCP Apps) erbte die Sitzung eines
Assistenten ohne Ansichten auf derselben Sicht und bekam dann keine
Ansichten. Ob Ansichten angesagt sind, gehört jetzt zur Sitzung.

## Wortwahl „zugeordnet“ (0.7.1)

Die Meldung ohne Destination `ki` sagt „zugeordnet“ wie das Portal und
nennt den Ort: Seite der Instanz, Reiter Netz & Adressen, Destinationen,
„Zuordnen“ (L16).

## Ampel je Gespräch (0.8.0)

RFC-0101 Stufe 1 (I-62 W1). Jedes Gespräch hat eine Ampel, die die
Person wählt: **grün** „Sensible und personenbezogene Daten“ (Vorgabe),
**gelb** „Firmeninformationen, nichts Sensibles“ oder **rot** „Nur
Öffentliches“. Die Farbe steht oben rechts und lässt sich dort ändern.
Angeboten werden nur Farben bis zur Decke des Schlüssels der
Destination `ki`.

- Der Chat schickt die Farbe als `X-OAAP-Light` an beide Gateways, bei
  jedem Modellzug und jedem Werkzeugaufruf. Die Gateways lehnen dann
  ebenfalls ab (KI-Gateway ab 0.9.0, MCP-Gateway ab 0.21.0). Das
  schützt die Person vor einem Versehen und ist keine Kontrolle über sie.
  Die Kontrolle bleibt die Decke des Schlüssels.
- Ins Rötere nur bewusst: Ein Satz („Ich gebe in diesem Gespräch nichts
  Sensibles ein.“) muss angekreuzt sein, der Server verlangt dafür
  `sure: true`. Ins Grünere braucht es nichts. Die Wechsel stehen im
  eigenen Verlauf (`lights`).
- Ist das Modell oder die Sicht des Assistenten röter als die Ampel
  (die Farben sagen die Gateways, `GET api/light`), sagt der Chat das
  offen und sperrt das Senden. Er bietet drei Wege an:
  - „Ohne Sensibles weiter“, also der bewusste Wechsel.
  - „Grüne Option erbitten“, ein Vorgang der Art `idea`.
  - „Vorab fragen, ob das erlaubt ist“, ein Vorgang der Art `question`.
- Die beiden Anfragen gehen aus dem Browser an `/platform/feedback`
  auf der Seite des Chats. Die Plattform bezeugt die Person, der Chat
  selbst hat keinen Schlüssel für Vorgänge. Ohne die App Vorgänge im
  Mandanten fehlen die Knöpfe; ein `tenant_admin` sieht dann einen Satz.
- `CHAT_DEFAULT_LIGHT` (Konfiguration) darf `yellow` vorgeben, etwa für
  Demo oder Schulung. Rot ist nie die Vorgabe.
- Gespräche von vor 0.8.0 haben keine Farbe, für sie gilt die Vorgabe.

## Assistenten im MCP-Gateway (0.9.0)

RFC-0097 Stufe 1. Die Assistenten liegen im MCP-Gateway des Mandanten
(Standard-Gateway, ab 0.22.0), mit Revisionen. Der Chat bleibt die
Laufzeit und hält die Verläufe.

- **Übernahme:** Solange die alte Datei `/data/assistants.json` noch
  Assistenten hat, bleibt alles wie bisher. Ein `tenant_admin` sieht auf
  der Seite „Assistenten“ den Knopf „Ins MCP-Gateway übernehmen“.
  Besitzer und Sichtbarkeit bleiben, die Datei wird umbenannt
  (`assistants.json.migrated-<zeit>`), nicht gelöscht. Ein Gateway vor
  0.22 kennt keine Assistenten, dann bleibt alles im Chat.
- **Kein stiller Rückfall:** Danach merkt sich der Chat
  (`/data/assistants.home`), dass seine Assistenten im Gateway liegen.
  Antwortet das Gateway nicht, bleibt nur der Assistent aus der
  Konfiguration, und die Seite sagt das. Gespeichert wird dann nichts,
  auch nicht in der Datei.
- **Revision an der Tür:** Jeder Aufruf trägt
  `X-OAAP-Assistant: <id>@<rev>`. Die Antwort des Modells im Verlauf
  trägt `rev`, für spätere Bewertungen (RFC-0101 Stufe 2).
- **Deaktivieren:** Ein deaktivierter Assistent wird nicht angeboten,
  ein laufendes Gespräch sagt es. Die Revisionen bleiben.
- `CHAT_ASSISTANT` bleibt als Rückfall, „verwaltet über die
  Konfiguration“, ohne Revisionen.
- `CHAT_ASSISTANTS_ONLY` beschränkt diesen Chat auf eine Liste von
  Assistenten, etwa einen Schulungs-Chat.
- Die Ampel aus 0.8.0 stand auch auf der Seite „Assistenten“. Das ist
  behoben.

## Sterne (0.10.0)

RFC-0101 Stufe 2. Unter jeder Schlussantwort eines Assistenten aus dem
MCP-Gateway stehen 0 bis 5 Sterne; „Gespräch bewerten“ im Menü bewertet
das ganze Gespräch. 0 heißt „unbrauchbar“.

- Die Bewertung geht als die Person ans MCP-Gateway, mit `assistant@rev`
  und den Umständen der Antwort (Modell und Ampel von damals, Sicht, je
  Werkzeug Name, Wirkung, Ausgang, Dauer). Text nur mit dem Haken „Frage
  und Antwort mitgeben“.
- Dieselbe Stelle noch einmal bewerten ersetzt, „zurückziehen“ nimmt sie
  weg. Im Verlauf der Person steht nur, was das Gateway angenommen hat.
- Assistenten aus der Konfiguration (`CHAT_ASSISTANT`) und Antworten ohne
  Revision bekommen keine Sterne (RFC-0101 D8).
- „Nicht zufrieden? An den Support geben“ kommt mit Stufe 3 (0.11.0).

## Nicht zufrieden (0.11.0)

RFC-0101 Stufe 3. Unter jeder Schlussantwort steht „Nicht zufrieden“, nach
0–2 Sternen als Angebot „Nicht zufrieden? An den Support geben“; im Menü
„Nicht zufrieden …“ für die letzte Antwort. Nur, wenn die App Vorgänge im
Mandanten antwortet (`/platform/feedback/session`).

- **Der Dialog:** Frage und Antwort vorbelegt und bearbeitbar, die
  Werkzeuge der Runde als Karten (Name, Wirkung, Ausgang), „Was hättest du
  erwartet?“ (Pflicht), „In der Dokumentation fehlt etwas“.
- **Argumente und Ergebnisse** nur für angekreuzte Karten (D10).
- **Der Weg:** Der Chat baut den Vorgang (`POST api/conv/<id>/unhappy`),
  der Browser schickt ihn an `/platform/feedback` seiner eigenen Adresse.
  Dort bezeugt die Plattform die Person. Der Chat hat keinen Schlüssel für
  Vorgänge.
- **Der Vorgang:** `question`, Unterart `chat-answer`, Bezug
  `{kind: conversation, id}`, Bereich `support_queue` des Assistenten
  (sonst Eingang). Felder: `assistant`, `rev`, `model`, `light`, `view`,
  `gateway`, `scope`, `round`, `from`, `to`, `ms`, `tools`, `expected`,
  `doc_gap`, `doc_pages`, `call_ids_note`, `chat`.
- **Hilfeseiten:** Links der Antwort, die ein Hilfe-Werkzeug derselben
  Runde geliefert hat.
- **Aufruf-Kennungen** gehen nicht mit. Dazu fehlt RFC-0098 Stufe 3, und
  der Dialog sagt das.
- **Marke:** Die Nummer bleibt im eigenen Verlauf an der Antwort. Die
  Seite fragt `/platform/feedback/mine` als die Person und zeigt
  „beantwortet“ (D11).
- **Editor:** neues Feld „Bereich für Nicht zufrieden“ (`support_queue`).
  Braucht Vorgänge ab 0.6.0. Eine ältere Instanz lehnt Unterart und Felder
  nicht ab, sie übergeht sie still. Der Vorgang landet dann im Eingang,
  ohne die Felder.

## Auswertung je Revision (0.12.0)

RFC-0097 Stufe 2 und RFC-0101 Stufe 4.
- **Wer sie sieht:** wer einen Assistenten pflegt, und der tenant_admin.
  Sie öffnen „Auswertung“ auf der Karte des Assistenten.
- **Was sie zeigt:** je Revision Aufrufe, abgewiesen, Fehler und Personen,
  die Verteilung der Sterne mit Durchschnitt, den Anteil 0–2 und die
  Vorgänge „Nicht zufrieden“. Dazu kommt „Revisionen vergleichen“.
- **Woher:** Die Zahlen kommen vom MCP-Gateway, als die Person. Der Chat
  rechnet nichts selbst.
- **Vorgänge zählen:** Nach „Nicht zufrieden“ meldet der Chat dem Gateway
  `assistant@rev` und die Nummer. Das Gateway zählt sie, ohne die Nummer
  zu speichern. Scheitert das, bleibt der Vorgang trotzdem.

## Leiste als Baum, Name und Symbol (0.17.0)

- **Obergruppen mit Symbol.** Die Leiste zeigt oben **Kalender** und darunter
  **Assistenten**. Der Kalender gliedert nach Jahr und Monat (Beginn des
  Gesprächs) und erscheint erst, wenn es Gespräche gibt; das laufende Jahr ist
  aufgeklappt, die Monate sind zu. Unter Assistenten steht, was bis 0.16 die
  Gruppierung „nach Assistent“ war. Weitere Obergruppen kommen als Eintrag in
  `TREES` in `page.html`, mit ihrem Symbol in `ICON` (Linien, 24er Raster).
  Auf- und Zuklappen merkt sich der Browser. Die Einstellung „Gespräche
  gruppieren“ entfällt.
- **Neues Gespräch über die Assistenten.** Der Knopf in der Leiste entfällt;
  „Neues Gespräch“ steht auf jeder Karte der Seite Assistenten (und `/neu`
  bleibt). Unten in der Leiste: Assistenten, Vorgänge, Einstellungen.
- **Schmal (bis 800 px) nur benutzen.** Die Seite Assistenten zeigt dort nur
  die Suche und auf jeder Karte „Neues Gespräch“ – Neu, Filter, Export,
  Import, Ändern, Löschen, Auswertung und Aktivieren bleiben dem großen
  Bildschirm.
- **Name und Symbol.** `CHAT_TITLE` (Vorgabe „Chat“) ist der Titel im Reiter,
  der Kopf der Leiste und der Name unter dem Symbol auf dem Homescreen
  (`apple-mobile-web-app-title`, `manifest.webmanifest`). Favicon als SVG,
  Homescreen-Symbole als PNG (180 für iOS ohne Transparenz, 192 und 512).
  Das Manifest wird mit Anmeldung geholt (`crossorigin="use-credentials"`),
  weil die Plattform vor jeder Route anmeldet.
- **Kontextmenü (⋯).** Jede Frage und jede Antwort und jedes Gespräch in der
  Leiste haben dasselbe Symbol; am großen Bildschirm erscheint es beim
  Überfahren, auf dem Telefon steht es immer da. An Nachrichten: **Kopieren**
  (Antworten als formatierter Text und als Markdown-Klartext, je nachdem,
  wohin man einfügt). In der Leiste: **Umbenennen** und **Löschen** – auch für
  ein Gespräch, das gerade nicht offen ist. Weitere Funktionen sind je ein
  Eintrag in `MSG_ACTIONS` bzw. `CONV_ACTIONS` in `page.html`.

## KI-Gateway ohne Schlüssel (0.16.0, RFC-0104 §4)

Der Chat braucht keine Destination `ki` mehr. Er erklärt im Manifest
`ai_client: {use: true}`; die Plattform gibt ihm dann einen Client-Schlüssel
(`OAAP_AI_CLIENT_KEY`) und die Liste der KI-Gateways seines Mandanten
(`OAAP_AI_CLIENT_URL`). Gerufen wird das KI-Gateway über eine via-Tür auf der
eigenen Seite, genau wie das MCP-Gateway: mit dem Client-Schlüssel **und**
der Zusicherung der Person dieser Anfrage (`X-OAAP-Act-Assertion`). Allein
öffnet keins von beiden etwas.

- **Welcher Weg** (der Reihe nach):
  1. Ist der Instanz eine Destination `ki` zugeordnet, gilt sie -- wie bisher.
     Eine Wahl von Hand gewinnt (`bec` bleibt, wie es ist).
  2. Sonst: genau ein KI-Gateway im Mandanten → dessen via-Tür.
  3. Mehrere: die Einstellung `CHAT_AI_GATEWAY` nennt eins. Ohne sie rät
     der Chat nicht, sondern sagt es (im Gespräch und in „Einrichtung prüfen“).
- **Was diesen Weg nimmt:** jede Modellfrage (`/chat/completions`), die
  Liste der Modellnamen im Formular „Assistent“ (`/models`), die Farben und
  die Decke für die Ampel (`/models`, `/usage`) und die Ampel des Gesprächs
  als `X-OAAP-Light`.
- **Einrichtung prüfen** nennt den Weg („Destination ki“ oder „KI-Gateway
  <name> (via-Tür, ohne Schlüssel)“). Antwortet das KI-Gateway 403, steht
  dort: „im KI-Gateway fehlt die Freigabe für diesen Chat“ -- auf der Seite
  des KI-Gateways fehlt dann eine Freigabe der Art `client` mit dem Namen
  dieser Instanz (der Aufbau aus dem Profil legt sie an).
- **Was das KI-Gateway sieht:** den Namen des Chats und die Person, nie den
  Schlüssel des Chats. Das kann es erst ab der Fassung, die RFC-0104 Stufe 2
  umsetzt; die Plattform braucht Manifest 0.18.

## Ansicht „Vorgänge“ (0.15.0, RFC-0103 Stufe 4)

- **Wo:** In der Leiste über „Assistenten“ der Eintrag „Vorgänge“ mit der
  Zahl der offenen eigenen Meldungen (nicht geschlossen, nicht abgelehnt).
  Er erscheint nur, wenn die Vorgänge antworten. Die Adresse ist
  `#vorgaenge`, die Zurück-Taste geht.
- **Was:** Eine Tabelle der **eigenen** Meldungen aus demselben Aufruf von
  `/platform/feedback/mine`, der die Punkte liefert. Spalten: Nummer (Link
  auf den Vorgang, neuer Reiter), Betreff, Assistent, Status mit Punkt,
  letzte Änderung. Sortieren mit einem Klick auf den Spaltenkopf. Filter:
  offen/alle, Status, Assistent. Ein Klick auf die Zeile öffnet das
  Gespräch.
- **Was nicht:** Vorgänge, die dir zugewiesen sind, stehen hier nicht.
  „N dir zugeordnet“ führt in die App Vorgänge; bearbeitet wird dort.

## Punkte, Link, „Lösung passt“ (0.14.0, RFC-0103 Stufe 2)

- **Punkte im Baum:** Beim Laden fragt die Seite einmal
  `/platform/feedback/mine` (alle eigenen Vorgänge) und ordnet sie über
  den Bezug `{kind: conversation, id}` den Gesprächen zu. Rot = du bist
  dran (Rückfrage oder abgelehnt), grün = beantwortet, gelb = offen, grau
  = geschlossen. Bei mehreren gewinnt der dringendste.
- **Link:** „Vorgang V-…“ öffnet den Vorgang in einem neuen Reiter, wenn
  die Vorgänge ihre Adresse nennen.
- **Lösung passt / Passt nicht:** Bei einem beantworteten Vorgang zwei
  Knöpfe. Der Browser schickt sie an `/platform/feedback/mine/…` dieser
  Seite, als die Person; der Chat hält keinen Schlüssel der Vorgänge.
  Danach sagt der Chat dem MCP-Gateway nur das Wort (`outcome`), damit die
  Auswertung der Revision „Lösung passt“ zählt.

## Anzeigename, SVG, gemeinsame Darstellung (0.13.0)

- **Anzeigename:** Der Chat dekodiert ihn jetzt. Die Plattform schickt ihn
  prozentkodiert, sobald er Umlaute enthält. Bis 0.12.0 stand
  „J%C3%B6rg%20M%C3%BCller“ im Prompt an das Modell.
- **SVG-Blöcke:** ```svg-Blöcke erscheinen als Bild. Ein `<img>` mit
  data:-Adresse kann kein Skript ausführen und nichts nachladen.
- **Gemeinsames Modul:** Markdown, Mermaid und die Ansicht liegen jetzt in
  `static/md/`, als Kopie aus `lib/mdview` (`check-shared.py`). Die App
  Vorgänge nutzt dieselbe Darstellung.

## Prüfen

```sh
py -3 apps/chat/test_chat.py      # 213 Prüfpunkte gegen eine nachgebaute Plattform
node apps/chat/test_markdown.js   # 27 Prüfpunkte: Darstellung und Sicherheit des Renderers
```

Die Messungen am Knoten mit echtem Modell:
`oaap-lab/assistent-probe/messung_stufe2.py` (23/23) und
`messung_stufe3.py` (26/26).

## Assistent ohne Sicht (0.18.0, RFC-0116 D9)

`view` ist seit `oaap.ai.assistants` 0.7 freiwillig. Ein Assistent ohne Sicht
spricht ohne Werkzeuge: der Chat fragt kein MCP-Gateway, schickt dem Modell
keine Werkzeuge, und ein erfundener Werkzeugaufruf des Modells erreicht keine
Tür (das Modell liest „gibt es in dieser Sicht nicht“). Die Einrichtung prüft
nur das Modell, die Ampel nur dessen Farbe. Im Formular bleibt das Feld
„Sicht“ dann leer. `gateway` ohne `view` wird abgelehnt (wer ein Gateway
nennt, glaubt an Werkzeuge dort), ebenso `views: true` ohne `view`.

## Noch nicht

Streaming der Antwort, Anhänge, Formulare, Sprache, mobile Ansicht als
eigene Oberfläche; Kontexte außer `team`.

## Arbeitsräume (0.22.0, RFC-0121 §5)

Ein Gespräch kann in einem Arbeitsraum des MCP-Gateways beginnen (`oaap.work.workspaces` 0.2).
Der Chat schickt dann `X-OAAP-Workspace` mit, das Gateway fügt die Ablagetür (`store_*`) zur
Sicht des Assistenten hinzu. Vor dem ersten Modellzug liest der Chat `workspace_info`, Stand
und Protokoll und gibt sie dem Modell als fremden Inhalt mit (`workspace.py`, gespeichert als
`ws_context`). `/stand` und `/abschluss` sind Bitten an den Assistenten; `/ablegen` schreibt der
Chat selbst (ein Aufruf mit `by: chat`, durch dieselbe Zustimmung, nie ans Modell). Ein
Gespräch, dessen Ampel lockerer ist als die des Arbeitsraums, beginnt nicht (409 mit
`needs_light`). Arbeitsräume anlegen und löschen: `/api/workspaces` (durchgereicht an
`/mcp/_workspaces` des Gateways, als die Person). Tests: `test_workspace.py`.

**0.24.0 (`oaap.work.workspaces` 0.4):** Arbeitsräume haben eine eigene Ansicht
(`#arbeitsraeume`, Knopf über „Assistenten“) mit einer Karte je Arbeitsraum: Ampel, Ablagen mit
Ordner und Rolle, die letzten Gespräche, Wahl des Assistenten (vorbelegt mit dem zuletzt dort
benutzten), „privat“ und **Gespräch beginnen**; „Ändern“ speichert unter derselben Kennung. Die
Leiste gruppiert nach Arbeitsraum (nicht nach Ablage, damit ein Gespräch einmal dasteht) und
zeigt jeden Arbeitsraum mit „+ Neues Gespräch“. Unter dem Gespräch steht nur noch, in welchem
Arbeitsraum es läuft. Im Dialog lässt sich jede Ablage auf einen **Ordner** einschränken,
gewählt über `GET /api/workspaces/pick?what=folders&system=&store=&path=` (MCP-Gateway ab
0.48.0); der Ordner begrenzt, was das Gespräch sieht, er ist kein Recht im Ablagesystem.
**0.24.1:** „Neu…“ ist gesperrt (mit Grund als Hinweis), wo das Ablagesystem in seiner
Selbstauskunft `create_store: false` meldet (MCP-Gateway ab 0.49.0 gibt es je System mit).
**0.25.0:** Die Karte eines Arbeitsraums folgt der Karte eines Assistenten: Titel mit Menü „⋯“
(letzte fünf Gespräche, Ändern, Löschen), Ampel, Beschreibung, nur die Heimat ausgeschrieben
(🏠), die übrigen Ablagen als Summen (✏️ Arbeitsablagen, 📖 Quellen, Namen im Hinweis), dann
Assistent und „Gespräch beginnen“. Der Haken „privat“ heißt jetzt **„ohne Protokoll“**: er
sagt nur, dass `/abschluss` nichts ins Protokoll der Heimat schreibt; das Gespräch selbst sieht
in beiden Fällen nur die Person.

## Launchpad (0.26.0)

Jede Person ordnet ihre Assistenten und Arbeitsräume selbst: **Symbol** (feste Auswahl von 24
Linien-Symbolen, nimmt die Farbe an), **Farbe** (acht Töne, als Streifen links und am Symbol) und
**Gruppe** (freier Name, höchstens 40 Zeichen). Beide Seiten sind gleich gebaut: Kachel mit Kopf
(Symbol, Titel, Menü „⋯“ mit „Darstellung …“ und der Pflege), Gruppen auf- und zuklappbar (nur
dieser Browser), darin alphabetisch, „Ohne Gruppe“ zuletzt; Suche findet auch den Gruppennamen.
Gespeichert im Chat je Person (`people/<person>/launchpad.json`), also auf jedem Gerät gleich;
andere sehen ihre eigene Anordnung. `GET /api/launchpad` liefert die Einträge und die Auswahl,
`POST /api/launchpad {kind, id, icon?, color?, group?}` setzt einen Eintrag (alles leer =
herausnehmen); unbekannte Symbole und Farben lehnt der Server ab. Die Karte eines Assistenten
hat Ändern, Auswertung, (De)aktivieren und Löschen jetzt im Menü; der Knopf heißt wie beim
Arbeitsraum „Gespräch beginnen“.

## Status, Kanban, Kalender, Startseite (0.27.0)

**Status** je Gespräch: In Bearbeitung (Vorgabe), Rückfrage, Wartet, Fertig. Ihn setzt **nur die
Person** – über den Knopf oben im Gespräch, das Menü „⋯“ in der Leiste und auf jeder Karte oder
durch Ziehen im Kanban. `POST /api/conv/<id>/status {status}`; „letzte Änderung“ bleibt dabei
unverändert (sie meint die letzte Arbeit).

**Kanban** (`#kanban`): Spalten In Bearbeitung, Rückfrage, Wartet; „Fertig“ nur mit dem Schalter
„Fertige zeigen“ (merkt sich der Browser). Am großen Bildschirm Karten ziehen, sonst Menü; Suche.

**Kalender** (`#kalender`): Woche, Monat, Jahr, ‹ Heute ›, Freitext. Ein Gespräch steht an jedem
Tag, an dem darin geschrieben wurde (↻ = fortgesetzt); der Server liefert dazu je Gespräch die
Stunden (`hours`, UTC), der Browser rechnet in seine Zeitzone um. Monat „+n mehr“ und Jahr führen
in die Woche.

**Startseite** (`#start`, statt des letzten Gesprächs, auch nach dem Löschen): „Neues Gespräch“,
dann Reihen Weiterbearbeiten, Zuletzt fertig, Letzte Arbeitsräume, Vorgänge (eigene Meldungen,
offene zuerst), Beliebt bei dir (Gespräche der letzten 30 Tage je Assistent und Arbeitsraum). Je
Breite so viele Kacheln, wie passen, dazu „mehr“; schmal fast nur Symbol und Titel. In der Leiste
stehen Start, Kanban und Kalender über Arbeitsräume und Assistenten. Exportieren und Importieren
der Assistenten liegen im Menü „⋯“.
