# Spezialisten

KI-Spezialisten bearbeiten Vorgänge im Hintergrund (RFC-0105 Stufen 1–3).

Ein **Spezialist** ist eine festgehaltene Assistenten-Revision (`analyse@3`) mit einem
**eigenen Agenten** (RFC-0067). Er handelt mit eigenen, engen Rechten, nicht mit denen
eines Menschen. Die Regel „ein Assistent trägt keine Rechte“ gilt im Chat weiter. Der
Spezialist ist der ausdrücklich verwaltete Sonderfall.

## Was ein Lauf tut

1. Die App fragt alle 30 s die App **Vorgänge** (ab 0.13.0), welche Vorgänge an diesem
   Spezialisten dran sind. Das sind die Vorgänge, für die er verantwortlich ist und bei
   denen seit seinem letzten Eintrag etwas Neues kam.
2. Sie liest den Vorgang. Der Text der meldenden Person geht als **zitierte Daten** ans
   Modell (`<vorgang>…</vorgang>`), nie als Anweisung.
3. Werkzeuge ruft sie nur über die **eigene Sicht** des Spezialisten im MCP-Gateway auf.
   Die Sicht ist nur lesend, das Gateway lehnt alles andere ab. Das Modell erreicht sie
   über das **KI-Gateway**.
4. Jeder Schritt landet **intern** im Vorgang: Notiz, Werkzeugaufruf (zugeklappt) und am
   Ende der Grund. Die meldende Person sieht nichts davon.
5. Am Ende gibt der Lauf den Vorgang an die Zuständigen des Bereichs zurück
   (verantwortlich leer, Status Klärung). Seine Notiz sagt ihnen, was er gefunden hat.

**Grenzen:** Schritte und Minuten je Lauf, Läufe je Vorgang. Die Obergrenzen stellt der
Mandant ein (`SPEZ_MAX_*`), je Spezialist gehen sie nur niedriger. Wird eine Grenze
erreicht, endet der Lauf und gibt zurück.

**Stopp:** Am Vorgang gibt es den Knopf „Spezialist anhalten“. Der Lauf endet nach dem
laufenden Schritt. Danach arbeitet an diesem Vorgang kein Spezialist mehr, bis ihn
jemand wieder zulässt.

**Ausschalten:** Ist ein Spezialist aus, beginnt er keinen neuen Lauf.

**Schreiben nur nach Freigabe (seit 0.2.0, Stufe 2):**
- Ein Werkzeug, das etwas ändert, ruft der Lauf nie auf eigenes Wort. Woran er das
  erkennt: `oaap/effect` vom Gateway; ohne Angabe gilt „ändert“.
- Stattdessen legt er einen **Plan** vor (Werkzeug, Argumente, Begründung) und endet.
- Ein Mensch des Bereichs gibt in Vorgänge genau diesen Aufruf frei. Die Freigabe hängt
  an einem Hash.
- Der nächste Lauf führt ihn aus, mit `X-OAAP-Confirmed: yes` und
  `X-OAAP-Approved-By: <Person>`. Das MCP-Gateway verlangt beides, wenn die Sicht
  `--approval required` hat.
- Ein abgelehnter Plan läuft nicht. Ein freigegebener läuft genau einmal.
- **Seit 0.4.0 unterschrieben:** Vorgänge (ab 0.16.0) unterschreibt jede Freigabe mit
  seinem Freigabeschlüssel (Bereiche → Freigabeschlüssel, nur tenant_admin). Der Lauf
  reicht sie als `X-OAAP-Approval` weiter. Hat die Sicht `--approval signed` mit diesem
  Schlüssel (MCP-Gateway ab 0.39.0), prüft das Gateway selbst: genau dieser Aufruf, genau
  einmal. Wer nur den Schlüssel der Sicht hat, kann sich dann keine Freigabe ausstellen.
  Eine Dauerfreigabe (unten) trägt keine Unterschrift; Werkzeuge mit Dauerfreigabe
  gehören in eine Sicht mit `--approval required`.
- **Ampel des Bereichs (seit 0.4.0, D9):** Hat der Bereich in Vorgänge eine Ampel, schickt
  der Lauf sie als `X-OAAP-Light` an KI- und MCP-Gateway. Sie nehmen dann nur Modelle und
  Sichten bis zu dieser Farbe; ohne eigene gelten die Decken der Schlüssel.
- Mit dem Haken „darf Antworten an die meldende Person entwerfen“ bekommt das Modell
  zusätzlich `antwort_entwurf`. Der Entwurf wird erst öffentlich, wenn ein Mensch ihn
  veröffentlicht.

**Rückfragen (seit 0.3.0, Stufe 3):**
- Kommt der Lauf nicht weiter, bekommt das Modell das Werkzeug `rueckfrage` mit den Namen,
  die es fragen kann: die anderen Spezialisten und die Zuständigen des Bereichs.
- Der Lauf endet dann mit „wartet auf Rückmeldung“. Die Antwort weckt ihn wieder.
- Ein **befragter** Spezialist liest den Vorgang, bekommt nur die lesenden Werkzeuge seiner
  Sicht und schreibt genau eine Antwort. Er wird nie verantwortlich.
- Die Grenzen hält Vorgänge: 3 Rückfragen an Spezialisten je Vorgang, danach nur noch an
  Menschen; nach 2 Antworten von Spezialisten auf Spezialisten in Folge ist ein Mensch dran.
  Das Modell erfährt den Grund und kann einen Menschen fragen.

**Freigabe je Werkzeug aufheben (D4):**
- Im Feld „Werkzeuge ohne Freigabe“ trägt der `tenant_admin` Werkzeuge ein, die dieser
  Spezialist ohne Plan aufrufen darf. Ein `admin` ohne `tenant_admin` kann das nicht.
- Die Änderung steht unter „Änderungen“. Der Aufruf trägt am Gateway
  `X-OAAP-Approved-By: Dauerfreigabe <Person>` und im Vorgang „aufgehoben von <Person>“.

**Leiter und Aufträge (seit 0.7.0, RFC-0134 Stufe 2, Vorgänge ab 0.22.0):**
- Ein Spezialist mit dem Schalter **„leitet“** hat ein **Team**: die Namen anderer Spezialisten
  dieser App, durch Komma getrennt. Er ist typischerweise die Erstbearbeitung eines Bereichs.
- Sein Lauf bekommt das Werkzeug `auftrag` (an, auftrag) mit genau diesen Namen und endet
  nach einem Auftrag mit „wartet auf Auftrag“. Das Ergebnis weckt ihn wieder.
- Ein **Beauftragter** liest den Vorgang, arbeitet in mehreren Schritten mit **seiner ganzen
  Sicht** und schreibt genau ein Ergebnis (`task_result`). Ein änderndes Werkzeug ruft er im
  Auftrag nicht auf: Er beschreibt den nötigen Aufruf im Ergebnis, planen und freigeben lassen
  tut es der Leiter. Er wird nie verantwortlich und gibt keine Aufträge weiter (Tiefe eins).
- Grenzen: ein Auftrag zur Zeit; höchstens `Aufträge je Vorgang` (1–8) am Leiter und
  `VORGAENGE_TASKS_MAX` in Vorgänge, die kleinere gilt. Danach bekommt der Leiter das Werkzeug
  nicht mehr und muss zusammenfassen und zurückgeben. Aufträge zählen nicht als Rückfragen.
- Abschließen durch den Leiter und das Prüfmerkmal kommen mit Stufe 3.

## Einrichten (gemessen auf oaap-test, 20.11.2026)

Für jeden Spezialisten gibt es drei Dinge, jedes mit eigenem Schlüssel.

**1. Den Agenten anlegen.** Er bekommt eine Rolle aus Vorgänge, nur im Bereich.

```sh
sudo oaap authz role-add --tenant T --app vorgaenge --template case_handler --name "Spez Analyse"
sudo oaap authz collection-add --tenant T --name "Spez Analyse" --role "Spez Analyse"
sudo oaap authz agent-add analyse-ki --tenant T --collection <volle Kennung der Sammlung> \
     --context queue=analyse --instance <vorgaenge-instanz> --days 90 --label "Analyse (KI)"
```

- Das `--label` ist der Name, unter dem der Spezialist im Vorgang steht.
- `agent-add` will die **volle** Kennung der Sammlung (32 Zeichen). `collections` zeigt
  nur 8 davon.
- Der Schlüssel wird nur einmal angezeigt. Er gehört in das Feld „Schlüssel des Agenten“.

**2. Eine eigene Sicht im MCP-Gateway** mit Verfahren T, nur lesend:

```sh
docker exec -i <gateway> python3 /srv/app.py server set <quelle>-analyse \
     --address http://oaap-gateway-1:<port der quelle>/mcp --auth bearer --credential-stdin ja
docker exec <gateway> python3 /srv/app.py view set spez-analyse --ways key --max-effect read
# soll er schreiben dürfen (Stufe 2): drei Einstellungen müssen zusammenpassen --
# Sicht bis write, Sicht mit Freigabepflicht, Schlüssel bis write
docker exec <gateway> python3 /srv/app.py view set spez-analyse --max-effect write --approval required
# besser (Gateway ab 0.39.0, Vorgänge ab 0.16.0): das Gateway prüft die Unterschrift selbst --
# den Schlüssel aus Vorgänge → Bereiche → Freigabeschlüssel über stdin, nie als Argument
docker exec -i <gateway> python3 /srv/app.py view set spez-analyse --approval signed --approval-key-stdin ja
docker exec <gateway> python3 /srv/app.py key set spez-analyse --max-effect write
docker exec <gateway> python3 /srv/app.py view add spez-analyse <quelle>-analyse <werkzeug> --effect read
docker exec <gateway> python3 /srv/app.py key issue spez-analyse --views spez-analyse
```

Der Agent hängt am **Server-Eintrag**, nicht am Aufrufer: Wer den Schlüssel der Sicht
hat, handelt als dieser Agent. Deshalb bekommt jeder Spezialist eine eigene Sicht mit
eigenem Schlüssel, und den hat nur diese App.

**3. Einen Schlüssel des KI-Gateways** für das Modell (dort unter „Schlüssel“, mit der
passenden Ampel).

Danach in der App:
- Mit „Neuer Spezialist“ den Datensatz anlegen. Der Export eines Assistenten aus dem
  MCP-Gateway füllt Kennung@Revision, Anweisung und Modell.
- „Prüfen“ drücken. Dabei meldet sich der Agent bei Vorgänge an.
- In **Vorgänge** unter „Bereiche“ wählt der `tenant_admin` den Spezialisten als
  „Erstbearbeitung“ des Bereichs.
- Den Spezialisten einschalten.

Die Adressen sind die Eingänge der Instanzen über das Plattform-Gateway
(`http://oaap-gateway-1:<port>`). Den Port nennt `oaap app list`.

## Rechte

- **Pflegen** dürfen `tenant_admin` und `admin`. Lesen dürfen `keyuser`.
- Die drei Schlüssel liegen in der Datenbank der App unter `/data`. Sie werden nie
  angezeigt. Ein leeres Feld beim Speichern behält den alten Wert. Löschen verwirft sie.

## Prüfen

```sh
py -3 apps/spezialisten/test_spezialisten.py
```

Der Test fährt die echte App Vorgänge in einem eigenen Prozess hoch. Davor sitzt eine
nachgebaute Plattform-Tür, die den Agent-Schlüssel in Kopfzeilen übersetzt. Dazu kommen
ein nachgebautes MCP-Gateway und ein Modell mit Drehbuch. Er prüft unter anderem diese
Köder:
- Der Text der Meldenden steht nie in der Anweisung.
- Ein Werkzeug außerhalb der Sicht wird abgelehnt.
- Die Meldende sieht keinen Schritt.
- Die Grenze greift.
- Der Stopp greift mitten im Schritt.
- Ein falscher Schlüssel wird abgelehnt.
- Ein `keyuser` kann nicht pflegen.
- Formulare von fremder Herkunft werden abgelehnt.
