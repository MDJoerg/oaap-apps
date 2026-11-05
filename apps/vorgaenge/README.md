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
python3 apps/vorgaenge/test_stage2.py   # zwei Mandanten, Übergabe und Antwort, Backend
```

Der Test startet einen echten Server und setzt die Gateway-Kopfzeilen
selbst. Vor allem prüft er, was ohne diese Kopfzeilen passiert.
