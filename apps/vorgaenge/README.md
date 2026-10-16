# Vorgänge (RFC-0061 Stufe 1)

Melden und Klären im Mandanten. **Eine Instanz je Mandant.** Plattform ab
**0.1.199**: Erst diese Version reserviert `/platform/*` auf den Adressen
jeder Instanz und leitet `/platform/feedback` hierher weiter.

## Was die App annimmt

1. **Meldungen über den Feedback-Knopf.** Jede App des Mandanten bindet
   ein:

   ```html
   <script src="/platform/feedback.js" defer></script>
   ```

   Das Script kommt vom Gateway, nicht von der App. Es fragt zuerst
   `/platform/feedback/session`; ohne Sitzung oder ohne `vorgaenge` im
   Mandanten zeigt es nichts. Sein Formular geht an `/platform/feedback`
   auf dem Ursprung der App. Das Gateway leitet es an diese Instanz (Pfad
   `/feedback`) und setzt dabei die **Herkunft**, die die App nicht
   fälschen kann:
   - `X-OAAP-Source-Instance`
   - `X-OAAP-Source-App`
   - `X-OAAP-Source-Version`
   - `X-OAAP-Source-Tenant`

   Auf jeder anderen Anfrage streicht das Gateway diese Kopfzeilen. Fehlen
   sie, lehnt `/feedback` ab.
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

Statuswerte in Stufe 1:

- `neu`
- `Klärung`
- `in Arbeit`
- `beantwortet`
- `geschlossen`
- `abgelehnt`, nur mit Begründung
- `wieder offen`

`Abstimmung` (§7) und `übergeben` (§5) kommen mit ihren Stufen.

## Was die App nicht tut (noch)

- **Keine Übergabe** an einen anderen Mandanten oder Knoten (Stufe 2).
- **Keine Benachrichtigung.** Der Posteingang ist die Übersicht.
- **Keine Löschfrist** für Personendaten (Q6: 12 Monate nach Schließen).
  Sie gehört vor den ersten echten Mandanten.
- **Keine Vorgänge aus dem Backend einer App** (§3.2, Stufe 2).

## Ablage

SQLite unter `/data/vorgaenge.db` (Q4). Einträge werden nur angehängt.
Ein Vorgang wird nicht gelöscht, sondern geschlossen.

## Test

```bash
python3 apps/vorgaenge/test_app.py
```

Der Test startet einen echten Server und setzt die Gateway-Kopfzeilen
selbst. Vor allem prüft er, was ohne diese Kopfzeilen passiert.
