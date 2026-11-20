# Spezialisten

KI-Spezialisten bearbeiten Vorgänge im Hintergrund (RFC-0105 Stufe 1).

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

Was Stufe 1 **nicht** kann, kommt mit den nächsten Stufen von RFC-0105:
- schreiben (Stufe 2, nur mit Freigabe),
- der meldenden Person antworten (Stufe 2, als Entwurf),
- andere fragen (Stufe 3).

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
