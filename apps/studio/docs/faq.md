# Studio – häufige Fragen

## Ich sehe das Studio nicht in meinem Portal.

Das Studio ist für die Rollen **keyuser** und **admin** freigegeben. Ohne
eine dieser Rollen zeigt das Portal die Kachel nicht, und das Gateway lässt
Dich nicht hinein. Die Rolle vergibt die Verwaltung des Mandanten.

## Ich kann ein Vorhaben nicht löschen.

Löschen ist `admin` vorbehalten. Als `keyuser` kannst Du den Status auf
„Ruht“ setzen.

## Das Studio sagt „geprüft“, der Knoten lehnt trotzdem ab.

Die Prüfung im Studio ist eine **Vorschau**. Verbindlich prüft der Knoten
noch einmal und vollständig. Seine Antwort steht wörtlich auf der
Ergebnisseite – sie gilt.

## Abgelehnt wegen „unveränderter Version“.

Der Knoten nimmt keine Version an, die schon installiert ist. Die KI muss
`app.version` im Manifest hochzählen. **Ausnahme:** nach einer Bestätigung
im Portal – dann dasselbe Paket unverändert erneut ausrollen (siehe
[Paket prüfen und ausrollen](paket-ausrollen.md)).

## Abgelehnt, weil der Rahmen erweitert wird.

Das Paket bringt eine neue öffentliche Route, einen neuen Speicher oder
einen Port am Gateway vorbei. Ein `server_admin` bestätigt das im Portal auf
der Seite der Instanz. Danach dasselbe Paket ohne Änderung noch einmal
ausrollen.

## „Ausgang unklar“ – hat es geklappt?

Das Studio hat nicht lange genug auf eine Antwort gewartet. Der Knoten rollt
meist trotzdem fertig aus. Im Portal des Zielknotens auf der Seite der
Instanz nachsehen – das Protokoll dort ist maßgeblich.

## Die Instanz gibt es noch nicht. Woher bekomme ich einen Token?

Gar nicht – ein Token gehört zu einer bestehenden Instanz. Für die erste
Fassung stellt ein `server_admin` eine **Anlege-Erlaubnis** aus (siehe
[Deploy-Token und Anlege-Erlaubnis](deploy-token.md)).

## Warum merkt sich das Studio meinen Token nicht?

Absicht. So kann niemand, der an die Daten des Studios kommt, damit
ausrollen, und hinter jedem Ausrollen steht ein Mensch.

## Der Zustand der Instanzen fehlt auf der Seite des Vorhabens.

Für den Zielknoten ist kein Flotten-Schlüssel hinterlegt, oder der Knoten
hat nicht geantwortet. Das Einrichten steht unter
[Zielknoten und Zustand der Instanzen](zielknoten-und-zustand.md). Ohne
Schlüssel funktioniert alles andere unverändert.

## Die Instanz steht als „unbekannt“ da.

Der Zielknoten kennt keine Instanz mit dem eingetragenen Namen – meist ein
Tippfehler im Vorhaben oder die Instanz ist umgezogen. Den Namen im Vorhaben
prüfen.

## Die Links zeigen auf den falschen Server.

Das Studio leitet den Zielknoten aus dem Deploy-Hook ab, sonst aus dem Feld
„Zielknoten (Adresse)“, sonst vermutet es den eigenen Knoten. Den Deploy-Hook
oder das Feld eintragen.

## Darf ich das Briefing oder das Starter-Paket weitergeben?

Ja. Beide enthalten weder Token noch Erlaubnis noch Adressen einer Instanz.

## Das Paket ist zu groß.

Das Studio nimmt bis `STUDIO_MAX_PACKAGE_MB` an (Vorgabe 64 MB). Der
Betreiber kann den Wert in der Konfiguration der Studio-Instanz erhöhen, bis
256 MB.

## Kann eine KI im Chat Auskunft über unsere Vorhaben geben?

Ja, lesend: Die Assistenten des Mandanten können über die MCP-Tür des
Studios die Vorhaben suchen, eines lesen und sein Briefing abrufen – mit
denselben Rechten wie Du auf der Seite (keyuser und admin). Anlegen,
ändern oder ausrollen können sie nichts; der Deploy-Token bleibt bei Dir.

## Wo ist die Seite „Hilfe“ im Studio geblieben?

Die Hilfe steht jetzt an einer Stelle: in diesen Seiten. Du erreichst sie
oben rechts über **Hilfe**; die alte Adresse `/hilfe` zeigt dieselben
Seiten.
