# Vorgänge (RFC-0061 Stufen 1 und 2; RFC-0069 Stufe 2 seit 0.3.0)

Melden, Klären und Übergeben im Mandanten. **Eine Instanz je Mandant.**
Plattform ab **0.1.199** für den Knopf: Erst diese Version reserviert
`/platform/*` auf den Adressen jeder Instanz und leitet `/platform/feedback`
hierher weiter. Für die Backend-Tür ab **0.1.201**, empfohlen **0.1.202**
(der Schlüssel der Tür wird dort selbst erneuert und beim Entfernen
zurückgezogen, RFC-0062).

Schritt-für-Schritt für Betreiber und Mandanten:
[Szenario „Vorgänge im Mandanten“](https://github.com/MDJoerg/oaap-docs/blob/main/szenarien/vorgaenge-im-mandanten.md).

**Eingang je Kanal:** Eine App meldet in den Eingang ihres eigenen Kanals.
Eine Test-App fällt ersatzweise auf den Produktions-Eingang zurück, eine
Produktions-App nie auf einen Test-Eingang.

## Was die App annimmt

1. **Meldungen über den Feedback-Knopf.** Jede App des Mandanten bindet
   ein:

   ```html
   <script src="/platform/feedback.js" defer></script>
   ```

   Das Script kommt vom Gateway, nicht von der App. Es fragt zuerst
   `/platform/feedback/session`; ohne Sitzung oder ohne `vorgaenge` im
   Mandanten zeigt es nichts. Sein Formular geht an `/platform/feedback`
   auf dem Ursprung der App. Das Gateway leitet es unverändert an diese
   Instanz weiter und setzt dabei die **Herkunft**, die die App nicht
   fälschen kann:
   - `X-OAAP-Source-Instance`
   - `X-OAAP-Source-App`
   - `X-OAAP-Source-Version`
   - `X-OAAP-Source-Tenant`

   Auf jeder anderen Anfrage streicht das Gateway diese Kopfzeilen. Fehlen
   sie, lehnt die App die Meldung ab.
2. **Interne Vorgänge**, angelegt unter „Neuer Vorgang“.

Für Apps mit eigener Kopfleiste:

- `data-oaap-feedback="api-only"` am Script-Tag blendet den schwebenden
  Knopf aus.
- `window.oaapFeedback.open({object: {kind, id}})` öffnet denselben Dialog.
- Den Objektbezug liest das Script erst beim Öffnen. Er kommt aus
  `data-oaap-object="team:<uuid>"` am Script-Tag oder an einem anderen
  Element.

## Bereiche und Rechte

Geklärt wird in **Bereichen**. Ein Bereich ist ein **Kontext** im Sinn von
RFC-0045 („Zuständigkeitsbereich“):

1. Ein Admin des Mandanten legt den Bereich unter **Bereiche** an, zum
   Beispiel `mb` „Mannschaft mB“.
2. In **Rollen & Rechte** vergibt der Mandant ihn: App *Vorgänge*,
   Vorlage *Bearbeiter/in* (`case_handler`) oder *Clearing*
   (`case_clearing`), Kontext = die Kennung des Bereichs.

| Wer | darf |
| --- | --- |
| die meldende Person | ihre Vorgänge sehen und kommentieren, eine Antwort schließen, wieder öffnen |
| Bearbeiter/in eines Bereichs | sehen, kommentieren, Status ändern, verschieben, schließen |
| frühere Bereiche (Beobachter) | lesen und kommentieren |
| Admins des Mandanten (Plattformrolle `admin`) | den Bereich `eingang` bearbeiten und Bereiche anlegen |

Den Bereich `eingang` gibt es ab der Installation. Ein Vorgang ohne
Bereich landet dort. Die Admins sehen ihn, weil sonst niemand den ersten
Vorgang sähe (RFC-0061 §2.2).

Statuswerte:

- `neu`
- `Klärung`
- `in Arbeit`
- `beantwortet`
- `geschlossen`
- `abgelehnt`, nur mit Begründung
- `wieder offen`
- `übergeben`: Nur eine freigegebene Übergabe setzt diesen Status, das
  Statusformular bietet ihn nicht an.

`Abstimmung` (§7) kommt mit ihrer Stufe.

## Vorgänge aus dem Backend einer App (seit 0.2.0, RFC-0061 §3.2)

Eine App, deren Manifest `cases: {submit: true}` erklärt (Manifest 0.9,
Plattform ab 0.1.201), bekommt bei der Installation zwei Variablen:

- `OAAP_CASES_URL`: die Tür `/platform/cases/submit` auf dem **eigenen**
  Gateway-Port der App,
- `OAAP_CASES_KEY`: einen Schlüssel, der nur an dieser Tür dieser Instanz
  gilt.

```http
POST $OAAP_CASES_URL
Authorization: Bearer $OAAP_CASES_KEY
Content-Type: application/json

{"kind": "internal", "title": "Bewerbung Hallenzeit Mo 18 Uhr", "body": "…",
 "queue": "eingang", "object": {"kind": "team", "id": "<uuid>"},
 "on_behalf_of": {"user_id": "<X-OAAP-User-Id>", "name": "Anna Spielerin"}}
```

Die Antwort ist `201 {"ok": true, "number": "V-…", "id": "…"}`.

- **Herkunft und `submitted_by: app:<instanz>`** setzt das Gateway; sie
  sind beglaubigt.
- **`on_behalf_of`** ist eine Angabe der App, keine Beglaubigung. So ist
  es auch gespeichert, unter `tech.asserted`. Die genannte Person sieht
  den Vorgang unter „Von mir gemeldet“.
- **Ohne `on_behalf_of`** meldet die App selbst.
- **Grenze:** höchstens 600 Vorgänge je Instanz und Stunde.

## Vorgänge vom Knoten selbst (seit 0.3.0, RFC-0069 Stufe 2)

Plant der Knoten einen Neustart (`oaap.core.updates` 2.7: ein Kernel wartet
und ein Fenster ist gesetzt, oder der Betreiber plant von Hand), legt die
Plattform in **jedem** Posteingang des Knotens einen Vorgang an — einen je
Plan, im Bereich `eingang`, Art „Intern“, gemeldet vom „Knoten *name*“. Jedes
Verschieben (ein Schutz kam, ein Schutz ging, überstimmt, überfällig, „startet
jetzt“) wird als Kommentar fortgeschrieben; nach dem Neustart schließt die
Plattform den Vorgang mit dem Beweis („Neustart erfolgt, der Knoten läuft
wieder“), eine Absage schließt ihn mit der Absage. Niemand muss etwas tun;
wer den Eingang liest, weiß Bescheid.

Die Tür ist `/platform/cases/node` auf der **eigenen** Site dieser Instanz.
Dort lässt das Gateway nur den Schlüssel des Prinzipals
`platform:<diese Instanz>` durch, den die Plattform für genau diesen
Posteingang prägt und 30 Tage vor Ablauf erneuert (Scope
`oaap.cases.node:<instanz>`); die App prüft denselben Prinzipal gegen die
vom Gateway gesetzte Herkunft noch einmal. Ein Mensch mit Sitzung kommt dort
nicht durch, ein Backend-Schlüssel (`oaap.cases.submit`) auch nicht.

```http
POST /platform/cases/node          (Authorization: Bearer <Schlüssel des Knotens>)
{"op": "open",  "ref": "kernel:…:2026-11-06T10:00:00Z", "node": "oaap-test",
 "title": "Neustart des Knotens oaap-test: 2026-11-08 04:15 CET (Pflege)", "body": "…"}
  → 201 {"ok": true, "number": "V-…"}   (dieselbe ref noch einmal: 200, "existing": true)
{"op": "note",  "number": "V-…", "node": "oaap-test", "text": "Verschoben auf …"}
{"op": "close", "number": "V-…", "node": "oaap-test", "text": "Neustart erfolgt …"}
```

`note` und `close` gelten nur für Vorgänge, die der Knoten selbst angelegt
hat (404 sonst). `close` schließt aus jedem offenen Status — es ist kein
Bearbeitungsschritt, sondern ein Tatbestand — und lässt einen schon
beendeten Vorgang in Ruhe. Was der Vorgang **nicht** trägt: Kernel-Versionen;
die stehen in `oaap status` und im Flottenstatus. Begründungen trägt er nur
in den Worten des Betreibers (Plan von Hand).

## Aufträge eines Leiters (seit 0.22.0, RFC-0134 Stufe 2)

Ein leitender Spezialist (App Spezialisten ab 0.7.0) gibt anderen Spezialisten Aufträge.
Die Tür ist dieselbe wie für Rückfragen (`/api/spez/`), die Regeln hält diese App:

| Aufruf | Wer | Regel |
|---|---|---|
| `GET /api/spez/case/<n>` | jeder Spezialist, der den Vorgang lesen darf | dazu `taskable` (nur für den Verantwortlichen: alle anderen Spezialisten, nie Menschen), `tasks_used`, `tasks_max`, `tasks_open` (Aufträge an mich) |
| `POST /api/spez/case/<n>/task` `{to, text, run, assistant}` | nur der verantwortliche Spezialist | ein Auftrag zur Zeit, höchstens `VORGAENGE_TASKS_MAX` (1–8, Standard 8) je Vorgang, nicht nach einem Stopp; Eintrag `task` (intern, mit Nummer), Unterstatus `waiting_task`, der Beauftragte wird als `tasked` beteiligt und darf damit lesen |
| `POST /api/spez/case/<n>/answer` `{consult: seq, text}` | nur der Beauftragte, nur einmal | Eintrag `task_result` (intern); ist nichts mehr offen, zurück auf „in Arbeit“, und der Leiter ist wieder dran |

Aufträge stehen in derselben Tabelle wie Rückfragen (Spalte `kind`), zählen aber nicht zu
deren Grenzen. Ein Beauftragter ist nie verantwortlich und kann deshalb weder Schritte
schreiben noch Aufträge geben (Tiefe eins).

## Schließen durch den Leiter, Prüfung (seit 0.23.0, RFC-0134 Stufe 3)

Liefert ein anderes System einen Vorgang durch ein Tor, kann der leitende Spezialist ihn
selbst schließen. Ob er darf, entscheidet diese App, nicht das Modell:

| Aufruf | Wer | Regel |
|---|---|---|
| `GET /api/spez/case/<n>` | jeder Spezialist, der lesen darf | dazu `from_gate`, `proofs` (gelungene Aufrufe nach einer Freigabe oder unter aufgehobener Freigabe: Belege), `review_reasons`, `reviewing` (ich soll prüfen) |
| `POST /api/spez/case/<n>/close` `{text, proof \| no_write, run, assistant}` | nur der verantwortliche Spezialist | nur Vorgänge aus einem Tor; nichts offen (Rückfrage, Auftrag, Plan); Beleg = gelungener Aufruf NACH der Freigabe in diesem Vorgang, sonst `no_write` mit Begründung (dann zur Prüfung); Status `closed`, Markierung „durch die Leitung“, der Text ist öffentlich (das liest das Tor als Antwort) |
| `POST /api/spez/case/<n>/mark` `{reason}` | nur der verantwortliche Spezialist | zur Prüfung, Grund „vom Leiter“ |
| `POST /api/spez/case/<n>/review` `{result: ok \| rework \| none, text}` | nur der prüfende Spezialist des Bereichs, einmal je Markierung | `none` = kein Urteil, dann prüfen die Menschen |

**Zur Prüfung** kommt ein Vorgang durch den Leiter, von Hand (Formular am Vorgang) und von
selbst: geschlossen ohne Änderung, eine Grenze des Laufs, ein Aufruf unter aufgehobener
Freigabe und als **Stichprobe** der Vorgänge, die der Leiter mit Beleg schließt (je Bereich,
Standard 10 %, Seite des Bereichs → *Prüfung*). Dort trägt der tenant_admin auch einen
prüfenden Spezialisten ein; er bekommt markierte Vorgänge, sobald niemand mehr daran arbeitet,
und nie die eigenen. Die Übersicht hat die Ansicht *zur Prüfung*. Die Prüfung (Menschen mit
„Bearbeiten“ im Bereich oder der Prüfer) ist „in Ordnung“ oder „Nacharbeit“; Nacharbeit
öffnet einen beendeten Vorgang wieder, der Leiter bleibt verantwortlich und ist wieder dran.
Merkmal und Prüfung sind intern.

## Lieferungen durch ein Tor (seit 0.21.0, RFC-0135 Stufe 1)

Ein anderes System (SAP per SM59, ein Node-RED-Flow, ein Formular) liefert an
`https://<Ort des Mandanten>/gate/<name>`. Die Plattform prüft den Zugang
(Basic mit dem Schlüssel als Passwort oder Bearer) und reicht die Lieferung an
`/platform/cases/gate` dieser Instanz weiter, mit Kopfzeilen, die kein Client
setzen kann: welches Tor, welcher Zugang (`X-OAAP-User` = `X-OAAP-Gate-Access`),
welche Feldliste und welcher Prozess (Bereich, Art).

- Geprüft wird gegen **dieselbe Feldliste**, aus der die Plattform die Vorlagen
  macht (`gates.py`, Kopie aus oaap-reference). JSON oder Formularfelder;
  unbekannte Felder und fehlende Pflichtfelder werden mit Namen abgelehnt (422).
  Zeichensatz UTF-8, oder ISO-8859-1 mit `charset=iso-8859-1` im Content-Type.
- Der Vorgang steht im Bereich des Prozesses, mit dem Zugang als Absender
  („SAP A4H 001 (Tor sap-support)“) und den technischen Feldern.
- **Dieselbe `ref` zweimal ist ein Vorgang**, je Zugang, auch nach dem
  Schließen.
- `GET /gate/<name>/<ref>` liest Nummer, Status und die letzte Antwort der
  Bearbeitenden (nie Internes).
- Antwort als JSON, oder mit `?answer=text` bzw. `Accept: text/plain` als
  Zeilen `number=…`, `ref=…` (Fehler: `error=…`).

Eingerichtet wird das Tor auf dem Knoten:

```
sudo oaap gate process sap-support --tenant bec --queue sap-support --kind question
sudo oaap gate add sap-support --tenant bec --process sap-support
sudo oaap gate access sap-support --tenant bec --label "SAP A4H 001"
```

## Übergabe an einen anderen Mandanten (seit 0.2.0, RFC-0061 §5)

> Ein Vorgang verlässt seinen Mandanten nie. Eine Übergabe schon: eine
> freigegebene Kopie, die beim Empfänger ein neuer Vorgang wird.

**Einrichten**, einmal je Partner und auf beiden Seiten, unter *Partner*
(nur Admins):

1. Der **Empfänger** legt im Portal unter *Zugänge* einen
   Maschinenzugang an: Rolle `user`, beschränkt auf seine
   `vorgaenge`-Instanz. Er gibt dem Absender den Schlüssel und trägt den
   Namen des Zugangs bei sich als *eingehenden Zugang* ein, mit dem
   Bereich, in dem die Übergaben landen.
2. Der **Absender** trägt die Adresse der Vorgänge-App des Empfängers und
   diesen Schlüssel ein.
3. Für **Antworten** gilt dasselbe in umgekehrter Richtung.

**Übergeben** darf nur, wer im Bereich die Aktivität `hand_over` hat, also
die Vorlage *Clearing*:

1. *Entwurf öffnen*: Betreff und Text sind änderbar. Man wählt die
   Kommentare, die mitgehen, und ob technische Angaben mitgehen (App,
   Version, Seite, Browser, letzter Fehler).
2. *Vorschau*: zeigt **genau die Bytes**, die gehen.
3. *Freigeben und senden*: Das Dokument ist an die Vorschau gebunden. Ein
   anderer Text wird abgelehnt.

Danach steht der Vorgang auf `übergeben`. Das Dokument liegt im
**Ausgang**, bis der Partner quittiert. Die App wiederholt mit wachsendem
Abstand, höchstens stündlich. Der Empfänger ignoriert eine zweite
Zustellung (`handover_id`).

**Was nie mitgeht:**
- die meldende Person,
- Benutzer-IDs, Rollen und Namen von Bearbeitenden,
- Kommentare, die nicht gewählt sind.

Die App prüft das fertige Dokument gegen eine Liste verbotener Schlüssel,
in jeder Tiefe.

**Der Empfänger** speichert das Dokument unverändert, mit Hash, und legt
daraus einen eigenen Vorgang an. Als Meldender steht dort der Partner.
Seine **Antwort** gibt ebenfalls ein Mensch mit `hand_over` frei. Beim
Absender wird sie ein Eintrag „Antwort des Partners“, und der Vorgang
geht von `übergeben` auf `beantwortet`. Antworten darf nur der Partner,
an den übergeben wurde.

## KI-Spezialisten (seit 0.13.0, RFC-0105 Stufe 1)

**0.23.1 (Befund in bec, 26.11.):** Eine Zuweisung an einen Spezialisten weckt ihn. Bisher
zählte für „ist etwas Neues da“ nur ein Eintrag, kein Rollenwechsel. Ein Spezialist, der einen
Vorgang schon einmal bearbeitet hatte, sah deshalb eine neue Zuweisung nicht und brauchte einen
Kommentar. Jetzt zählt ein Rollenwechsel **an ihn durch jemand anderen**. Andere Rollenwechsel,
auch seine eigene Rückgabe, zählen weiter nicht.

Ein Spezialist ist ein Agent mit eigenen Rechten. Die App **Spezialisten** führt seine
Läufe aus. Für Vorgänge ist er eine Person wie jede andere: Er liest, was seine
Zuweisung erlaubt, zum Beispiel „Bearbeiter/in“ im Bereich `analyse`. Schreiben darf er
nur, wo er verantwortlich ist.

- **Erstbearbeitung:** Unter „Bereiche“ wählt der `tenant_admin` je Bereich einen
  Spezialisten. Neue und dorthin verschobene Vorgänge bekommen ihn als Verantwortlichen.
  Das wird intern vermerkt. Zur Auswahl steht er, sobald die App Spezialisten einmal für
  ihn gelaufen ist.
- **Tür für den Lauf** (`/api/spez/…`, nur JSON, als der Agent):
  - `GET turn`: welche Vorgänge an ihm dran sind, mit dem letzten Eintrag und der Zahl
    seiner Läufe.
  - `GET case/<nr>`: der Vorgang mit Verlauf.
  - `POST case/<nr>/entry`: ein Schritt `note`, `tool` oder `end`, immer intern.
  - `POST case/<nr>/give-back`: verantwortlich leer, Status Klärung.
- **Dunkler Chat:** Die Schritte stehen im Reiter „Intern“ mit der Marke **KI**. Die
  Werkzeugaufrufe sind zugeklappt, jeder Schritt nennt Lauf und `assistent@rev`. Die
  meldende Person sieht keinen davon.
- **Stopp:** Wer den Bereich bearbeitet, kann am Vorgang „Spezialist anhalten“ drücken.
  Danach nimmt Vorgänge vom Spezialisten nur noch das Ende des Laufs an, bis jemand ihn
  wieder zulässt.
- **Freigabe (seit 0.14.0, Stufe 2):** Ein Spezialist kann einen schreibenden Aufruf
  (`plan`) oder eine Antwort an die meldende Person (`draft`) nur vorlegen.
  - Vorgänge rechnet den Hash über Werkzeug und Argumente bzw. über den Text selbst aus.
  - Der Vorgang wechselt auf **wartet auf Freigabe**.
  - Wer den Bereich bearbeitet, drückt „Freigeben“ oder „Ablehnen“, bei einem Entwurf
    „Veröffentlichen“ oder „Verwerfen“. Der Spezialist selbst kann das nie, und über
    einen Schritt wird nur einmal entschieden.
  - Ist der Plan nachträglich verändert, wird er nicht freigegeben.
  - Ein freigegebener Entwurf wird öffentlich: im Namen des Spezialisten, mit der
    freigebenden Person.
  - Danach steht der Vorgang wieder auf „in Arbeit“, und der Spezialist ist dran. Er
    führt genau den freigegebenen Aufruf aus.
  - „Wartet auf Freigabe“ steht nicht im Statusformular.
- **Rückfragen (seit 0.15.0, Stufe 3):** Wer verantwortlich ist (ein Spezialist oder ein
  Mensch) oder den Bereich bearbeitet, kann einen anderen Spezialisten oder einen
  Zuständigen des Bereichs fragen.
  - Die Frage und die Antwort sind intern. Der Vorgang steht so lange auf **wartet auf
    Rückmeldung**; der Status kehrt von selbst zurück, wenn alle Rückfragen beantwortet sind.
  - Die Befragten lesen den Vorgang, solange er offen ist, und antworten genau einmal. Sie
    werden nie verantwortlich, ändern keinen Status und schreiben sonst nichts.
  - Ein befragter Mensch findet den Vorgang unter „Meine“ und antwortet auf der Seite.
  - Grenzen: höchstens 3 Rückfragen an Spezialisten je Vorgang; nach 2 Antworten von
    Spezialisten auf Spezialisten in Folge ist ein Mensch dran.
  - Ob jemand ein Spezialist ist, merkt sich die App, wenn er die Tür der Spezialisten
    benutzt (`/api/spez/turn`).

## MCP-Tür (seit 0.4.0, RFC-0073/RFC-0074)

Unter `/mcp` (Manifest `mcp: {path: /mcp}`) ist Vorgänge eine MCP-Quelle für
das MCP-Gateway des Mandanten — und damit für den Chat. Die Tür antwortet als
die Person, die die Plattform durchreicht, mit **denselben Prüfungen wie die
Seite** (`can_see`, `can_comment`, Rechte aus „Rollen & Rechte“); einen
zweiten Regelsatz gibt es nicht.

| Werkzeug | Wirkung | Was |
|---|---|---|
| `list_cases` | lesen | von mir gemeldete und die meiner Bereiche (Standard: offene) |
| `get_case` | lesen | ein Vorgang mit Verlauf, wie die Seite ihn zeigt — ohne die technischen Angaben aus dem Browser |
| `list_queues` | lesen | die Bereiche (für `create_case`) |
| `create_case` | schreiben | einen Vorgang melden; Herkunft `via: mcp` vermerkt |
| `comment_case` | schreiben | kommentieren, wer es auf der Seite darf |

**Seit 0.5.0** bringt `get_case` eine Ansicht mit (MCP Apps, `ui://vorgaenge/case`, RFC-0075): eine Karte des Vorgangs mit Verlauf und Kommentarfeld, das über den Chat `comment_case` ruft. Sie lädt nichts von außen (leere CSP) und setzt Daten nur als Text ein. `resources/templates/list` antwortet leer. Seit 0.5.1 nimmt die Tür eine Nummer auch ohne `V-` an (ein Modell lässt es gern weg), und die Beschreibung von `get_case` nennt das richtige Beispiel.

Partner-Zugänge bekommen die Tür nicht (403). Nur `application/json`, also
kein Formular aus einem fremden Browser-Fenster.

## Übersicht und Reiter (seit 0.11.0, RFC-0103 Stufe 4)

- **Übersicht als Listenbericht:** Eine Tabelle mit allem, was die Person
  sehen darf: ihre Bereiche und Beobachter-Bereiche, was ihr zugewiesen ist
  oder wofür sie verantwortlich ist, woran sie je beteiligt war, und ihre
  eigenen Meldungen.
  - **Spalten:** Nummer, Betreff (mit Art), Status in seiner Farbe,
    Bereich, verantwortlich, zugewiesen, letzte Änderung. Die Farben sind
    die der Punkte im Chat: rot = die Meldende ist dran, grün =
    beantwortet, grau = erledigt, gelb = offen.
  - **Filter:** `view` (leer = alles, `mine` = gemeldet, verantwortlich
    oder zugewiesen, `assigned` = zugewiesen oder verantwortlich),
    `queue`, `status` (`open` als Vorgabe, `all` oder ein einzelner
    Status). `?alle=1` von früher heißt `status=all`.
  - **Sortieren:** ein Klick auf den Spaltenkopf, `sort=-updated` als
    Vorgabe. Filter und Sortierung stehen in der Adresse, eine Ansicht kann
    ein Lesezeichen sein; die Seite braucht kein Skript.
  - **Wer bearbeitet:** Verantwortlich und zugewiesen sehen nur die
    Bearbeitenden. Auf einer nur eigenen Meldung bleiben die Spalten leer,
    und sortiert wird nach dem, was in der Zelle steht, damit auch die
    Reihenfolge nichts verrät.
- **Reiter am Vorgang:** Überblick (Beschreibung, Status, Bereich, die
  Rollen und alle Formulare dieser Person), Verlauf (öffentliche Einträge,
  Kommentar), Intern (nur für die Leser interner Kommentare), Technik (nur,
  wenn es Angaben gibt), Übergabe (nur fürs Clearing).
  - Die Reiter sind Anker mit CSS (`#verlauf`, `#intern`, …), ohne Skript.
    Die Adresse behält den Reiter. Nach einem Kommentar geht es zurück in
    den Reiter, in dem er steht.
- **`/platform/feedback/mine`** nennt zusätzlich `assigned` (Zahl der
  offenen Vorgänge, die der Person zugewiesen sind oder für die sie
  verantwortlich ist), `assigned_url` und `overview_url` (Links in die
  Übersicht). Keine dieser Vorgänge selbst: Der Chat listet nur eigene
  Meldungen.

## Arbeit im Vorgang (seit 0.10.0, RFC-0103 Stufe 3)

- **Verantwortlich:** eine Zuständige des Bereichs (Liste auf „Bereiche“
  oder ein Grant aus „Rollen & Rechte“). Setzen dürfen Zuständige und der
  `tenant_admin`. Verschieben leert das Feld; der neue Bereich entscheidet.
- **Zugewiesen:** jede Person, die die App einmal geöffnet hat. Sie liest,
  kommentiert und ändert den Status, aber sie verschiebt nicht, schließt
  nicht, lehnt nicht ab, übergibt nicht und weist nicht weiter zu.
- **Beteiligt:** Wer je verantwortlich oder zugewiesen war, liest den
  Vorgang weiter (auch interne Kommentare), ändert und kommentiert aber
  nichts mehr. Jeder Wechsel steht im Verlauf, als interner Eintrag.
- **Unterstatus von „in Arbeit“** (fest, nicht je Mandant), mit denselben
  Wegen wie „in Arbeit“:
  - `clarify_internal` (in Klärung intern);
  - `clarify_external` (in Klärung extern);
  - `waiting_test` (wartet auf Test);
  - `waiting_reporter`: für die meldende Person „Rückfrage an dich“, im
    Chat ein roter Punkt. Ihr nächster Kommentar setzt den Vorgang von
    selbst zurück auf „in Arbeit“.
- **Interne Kommentare:** Für Bearbeitende ist „intern“ vorbelegt (auch an
  der MCP-Tür, ohne Angabe von `internal`).
  - Lesen dürfen sie der Bereich, die Beobachter-Bereiche, alle je
    Beteiligten und der `tenant_admin`.
  - Die meldende Person sieht sie nirgends: nicht auf der Seite, nicht über
    `/platform/feedback/mine`, nicht an der MCP-Tür.
  - Bei einer Übergabe geht ein interner Kommentar nur mit, wenn er im
    Entwurf angehakt ist. Im Entwurf steht er als „(intern)“.
- **Übersicht:** Ein eigener Abschnitt „Mir zugewiesen oder von mir
  verantwortet“. `list_cases` an der MCP-Tür nimmt diese Vorgänge mit.

## Rückweg in den Chat, „Lösung passt“ (seit 0.9.0, RFC-0103 Stufe 2)

- **Link:** `GET /platform/feedback/mine` nennt je eigenem Vorgang `url`,
  gebaut aus dem ersten öffentlichen Namen der Instanz
  (`OAAP_INSTANCE_NAMES`) als `//host/v/<Nummer>`, ohne Schema: der
  Browser nimmt das der Seite, auf der der Link steht.
- **Alle eigenen:** Ohne `numbers` liefert `/mine` alle eigenen Meldungen
  (höchstens 200, neueste zuerst) mit Bezug (`object`), Betreff, Status,
  Markierung und letzter Änderung. Der Chat färbt damit seine Gespräche.
  Nur Eigenes, nie etwas von anderen.
- **Abnehmen:** Bei einem beantworteten Vorgang sieht die meldende Person
  „Lösung passt“ (geschlossen mit der Markierung `accepted`) und „Passt
  nicht“ (wieder offen, Kommentar Pflicht), auf der Seite und im Chat. Der
  Chat geht über `POST /platform/feedback/mine/<Nummer>/accept` bzw.
  `…/reopen` auf seiner eigenen Adresse. Das Gateway bezeugt die Person,
  die App nimmt es nur von der meldenden Person an; für alle anderen gibt
  es den Vorgang auf diesem Weg nicht (404).
- **Schließen über das Statusformular:** Die meldende Person schließt
  nicht mehr dort, sonst fehlte die Markierung. Schließen Bearbeitende,
  bleibt die Markierung leer. Wiederöffnen leert sie.
- **Ohne Rückmeldung:** Ein beantworteter Vorgang ohne jede Bewegung
  schließt sich nach `VORGAENGE_ACCEPT_DAYS` Tagen (Vorgabe 14) mit der
  Markierung `no_response`, nie `accepted`. Geprüft wird im Takt der
  Löschfrist (alle sechs Stunden).

## tenant_admin und Zuständige je Bereich (seit 0.8.0, RFC-0103 Stufe 1)

- **tenant_admin:** Er bearbeitet jeden Bereich: lesen, kommentieren,
  Status ändern, verschieben, schließen, Bereiche anlegen, Zuständige
  pflegen. Übergeben darf er nur als Clearing, weil dabei Daten den
  Mandanten verlassen. Bis 0.7.0 galt nur die App-Rolle `admin`, und nur
  im Eingang. RFC-0061 §2.2 hatte das anders beschrieben.
- **Zuständige je Bereich:** Der tenant_admin pflegt sie auf der Seite
  „Bereiche“, als Bearbeiter/in oder als Clearing (darf zusätzlich
  übergeben). Für einen kleinen Mandanten braucht es so kein „Rollen &
  Rechte“. Grants von dort zählen weiter, beide Wege zusammen.
- **Wer pflegt:** Zuständige tragen keine weiteren ein. Änderungen stehen
  unter „Zuletzt geändert“.
- **Auswahl der Personen:** Gewählt wird aus den Personen, die die App
  schon einmal geöffnet haben. Ein Verzeichnis aller Benutzer bekommt
  diese App nicht, das hat nur „Rollen & Rechte“ mit eigenem Schlüssel.
  Fehlt jemand, öffnet die Person die App einmal.

## Darstellung, Rechte-Hinweis, OAAP-Rahmen (seit 0.7.0)

- **Anzeigename:** Die App dekodiert ihn jetzt (RFC-0040 D4). Was 0.6.0
  noch kodiert gespeichert hat („J%C3%B6rg%20M%C3%BCller“), korrigiert die
  App einmal beim Start.
- **Text als Markdown:** Beschreibung und Einträge erscheinen formatiert,
  mit Tabellen, Mermaid-Diagrammen und ```svg als Bild. Oben rechts
  schaltet „Formatiert / Roh“ für die ganze Seite um; der Browser merkt
  sich die Wahl. Ohne JavaScript bleibt der rohe Text stehen. Es ist
  dieselbe Darstellung wie im Chat (`static/md/`, Kopie aus `lib/mdview`).
- **Wer den Status ändern darf:** Wer hier nichts umstellen darf, liest
  den Grund. Im Eingang braucht es die App-Rolle `admin`; `tenant_admin`
  reicht nicht, er vergibt `admin` aber im Portal. In anderen Bereichen
  braucht es die Vorlage Bearbeiter/in oder Clearing aus „Rollen &
  Rechte“.
- **Kopfzeile im OAAP-Design (oaap-design §5.1):** Sie zeigt Marke und
  App, rechts das Benutzermenü. Darin stehen der Name, „angemeldet als“,
  „Passwort ändern“ (nur mit Passwort, laut `/auth/whoami`) und
  „Abmelden“ als POST an `/auth/logout`. Danach kann sich eine andere
  Person anmelden.
- **Fußzeile:** Sie zeigt die Version und die eigenen Bereiche
  (bearbeiten oder lesen).

## Unterart, Felder und Bereich vom Feedback-Weg (seit 0.6.0, RFC-0101 Stufe 3)

`/platform/feedback` nimmt zusätzlich an:

| Feld | was |
|---|---|
| `subkind` | eine Kennung, z. B. `chat-answer` (Kleinbuchstaben, Ziffern, Bindestrich, ≤ 40) |
| `fields` | nur mit `subkind`: flache Schlüssel (`a-z0-9_`), JSON-Werte bis vier Ebenen, ≤ 60 Schlüssel, ≤ 40000 Bytes |
| `queue` | ein Bereich dieser Instanz; unbekannt oder ungültig → Eingang |

- **Getrennt gespeichert:** Felder liegen getrennt von dem, was das
  Gateway beglaubigt. Die Seite des Vorgangs zeigt sie als „Angaben der
  App … nicht beglaubigt“, für die Bearbeitenden und die Meldende.
- **Was nicht mitreist:** Eine Übergabe an einen Partner nimmt sie nicht
  mit. Die Löschfrist entfernt sie mit den übrigen technischen Angaben.
- **Status der eigenen Vorgänge:** `GET /platform/feedback/mine?numbers=V-…,V-…`
  (höchstens 50) liefert je Nummer Status, Wort und `answered`. Das gilt
  nur für Vorgänge, die die Person selbst gemeldet hat. Eine fremde
  Nummer fehlt wie eine unbekannte. Damit markiert der Chat ein beantwortetes
  „Nicht zufrieden“ (RFC-0101 D11).
- **Keine Vorlagen:** Das ist keine Vorlage nach RFC-0061 §6. Pflichtfelder
  je Unterart prüft die App noch nicht.

## Was die App nicht tut (noch)

- **Keine Benachrichtigung.** Der Posteingang ist die Übersicht.
- **Kein Schlüssel für eine KI-Sitzung** (§5.4). Eine Antwort gibt immer
  ein Mensch frei (Q7).
- **Keine Abstimmung** (§7) und **keine Vorlagen** (§6).

## Löschfrist (Q6, seit 0.1.1)

Ist ein Vorgang seit **12 Monaten** geschlossen oder abgelehnt, entfernt
die App die meldende Person daraus. Der Vorgang selbst bleibt.

- **Was entfernt wird:** die Person im Vorgang und in ihren eigenen
  Einträgen (sie heißt danach „entfernt“), ihre Benutzer-ID und Rollen in
  den technischen Angaben und alles, was der Browser mitgeschickt hat.
- **Was bleibt:** Herkunft (App, Instanz, Version), Bezug, Betreff,
  Beschreibung und was Bearbeitende geschrieben haben.
- **Freitext:** Steht ein Name im Text, entfernt ihn die Frist nicht.
- **Verlauf:** Die Entfernung steht als Eintrag „Löschfrist“ im Verlauf.
- **Wiederöffnen:** Ein wieder geöffneter Vorgang hält die Frist an. Sie
  läuft ab dem nächsten Abschluss neu.

Die Frist ist je Instanz einstellbar, also je Mandant:
`VORGAENGE_RETENTION_MONTHS`, ganze Monate ab 1, Vorgabe 12. Die App prüft
beim Start und alle sechs Stunden.

## Ablage

SQLite unter `/data/vorgaenge.db` (Q4). Einträge werden nur angehängt.
Ein Vorgang wird nicht gelöscht, sondern geschlossen.

## Test

```bash
python3 apps/vorgaenge/test_app.py
python3 apps/vorgaenge/test_mcp.py
python3 apps/vorgaenge/test_stage2.py   # zwei Mandanten, Übergabe und Antwort, Backend
python3 apps/vorgaenge/test_gate.py     # Lieferungen durch ein Tor (RFC-0135)
python3 apps/vorgaenge/test_tasks.py    # Aufträge eines Leiters (RFC-0134 Stufe 2)
python3 apps/vorgaenge/test_close_review.py   # Schließen durch den Leiter, Prüfung (Stufe 3)
```

Der Test startet einen echten Server und setzt die Gateway-Kopfzeilen
selbst. Vor allem prüft er, was ohne diese Kopfzeilen passiert.
