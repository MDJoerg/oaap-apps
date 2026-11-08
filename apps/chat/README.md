# Chat — der Plattform-Chat, erste Fassung (RFC-0074 Stufe 2)

Ein Assistent, die Werkzeuge **einer** Sicht des MCP-Gateways, ein Modell
über das KI-Gateway, der Verlauf als Datei. Das ist I-41 Schritt 4 in
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

3. Der Assistent als JSON in `CHAT_ASSISTANT` (Format `oaap.ai.assistants` 0.1):

   ```json
   {"id": "projekte", "title": "Projekt-Assistent", "view": "kp-i",
    "gateway": "<mandant>-mcpgw", "model": "standard",
    "greeting": "Hallo! …", "system": "Du bist … Erfinde nichts.",
    "visibility": {"scope": "tenant"}, "owner": "tenant"}
   ```

   Ein Feld, das nach einem Recht aussieht (`token`, `roles`, …), macht
   den Assistenten ungültig; dann läuft keine Runde.

4. `CHAT_MODEL_EXTRA` (optional): Felder, die jeder Modellzug zusätzlich
   trägt, z. B. `{"reasoning": {"effort": "none"}}` für `qwen3:8b` auf
   einer CPU (RFC-0074 §6 Befund 5).

## Prüfen

```sh
py -3 apps/chat/test_chat.py      # 37 Prüfpunkte gegen eine nachgebaute Plattform
```

Die Messung am Knoten mit echtem Modell:
`oaap-lab/assistent-probe/messung_stufe2.py`.

## Noch nicht (Stufe 3)

Mehrere Assistenten und ihre Sichtbarkeit nach Kontext (D4), `/commands`
aus den Prompts der Sicht, das Vermerken der Zustimmung im Gateway
(§5.6), Streaming der Antwort, Anhänge.
