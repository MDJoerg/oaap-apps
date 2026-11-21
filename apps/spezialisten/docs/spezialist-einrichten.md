# Einen Spezialisten einrichten

Für Mandanten-Administratoren. Jeder Spezialist braucht drei Dinge, jedes mit eigenem
Schlüssel.

## 1. Den Agenten anlegen

Der Agent bekommt eine Rolle aus Vorgänge, nur im gewünschten Bereich:

```sh
sudo oaap authz role-add --tenant T --app vorgaenge --template case_handler --name "Spez Analyse"
sudo oaap authz collection-add --tenant T --name "Spez Analyse" --role "Spez Analyse"
sudo oaap authz agent-add analyse-ki --tenant T --collection <volle Kennung der Sammlung> \
     --context queue=analyse --instance <vorgaenge-instanz> --days 90 --label "Analyse (KI)"
```

- Das `--label` ist der Name, unter dem der Spezialist im Vorgang steht.
- Der Schlüssel wird nur einmal angezeigt. Er gehört in das Feld *Schlüssel des Agenten*.

## 2. Eine eigene Sicht im MCP-Gateway

Legen Sie im MCP-Gateway eine Sicht nur für diesen Spezialisten an, mit den Werkzeugen, die
er braucht, und einem Schlüssel nur für diese Sicht. Wer den Schlüssel der Sicht hat,
handelt als dieser Agent – deshalb bekommt jeder Spezialist seine eigene.

Soll er ändernde Werkzeuge nutzen, stellen Sie die Sicht auf **Freigabe erforderlich**
(besser: *unterschrieben*, mit dem Freigabeschlüssel aus Vorgänge → Bereiche).

## 3. Einen Schlüssel des KI-Gateways

Im KI-Gateway unter *Schlüssel* einen Schlüssel für das Modell ausstellen, mit der passenden
Ampel.

## 4. In der App

1. **Neuer Spezialist** öffnen. Tipp: Den Export des Assistenten aus dem MCP-Gateway unten
   einfügen und *Übernehmen* – das füllt Kennung@Revision, Anweisung und Modell.
2. Adressen und die drei Schlüssel eintragen, Grenzen setzen, *Speichern*.
3. Den Spezialisten öffnen und **Prüfen** drücken. Drei Haken heißen: Vorgänge, Sicht und
   Modell sind erreichbar. Dabei meldet sich der Agent bei Vorgänge an.
4. In **Vorgänge** unter *Bereiche* den Spezialisten als *Erstbearbeitung* des Bereichs
   wählen.
5. Den Spezialisten **einschalten**.

Die Adressen sind die Eingänge der Instanzen über das Plattform-Gateway
(`http://oaap-gateway-1:<port>`). Den Port nennt `oaap app list`.

## Grenzen

Schritte und Minuten je Lauf, Läufe je Vorgang. Die Obergrenzen stellt der Mandant ein;
je Spezialist gehen sie nur niedriger. Wird eine Grenze erreicht, endet der Lauf und gibt
den Vorgang zurück.
