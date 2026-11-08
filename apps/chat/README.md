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

## Prüfen

```sh
py -3 apps/chat/test_chat.py      # 68 Prüfpunkte gegen eine nachgebaute Plattform
```

Die Messungen am Knoten mit echtem Modell:
`oaap-lab/assistent-probe/messung_stufe2.py` (23/23) und
`messung_stufe3.py` (26/26).

## Noch nicht

Streaming der Antwort, Anhänge, Formulare, Sprache, mobile Ansicht als
eigene Oberfläche; Kontexte außer `team`.
