# OAAP Projekt-App

Die **dritte** der zweiten Welle (RFC-0031, Bauplan Schritt 4). Owner
von `Projekt`, Consumer von `Firma` (Referenz, D6). Genau das Beispiel
aus dem Zielbild: *"Projekte als Beispiel fuer 'gleiches Datenobjekt,
verschiedene Herkunft' -- erzeugt von einer eigenen Projekt-App
und/oder in der Kunden-/Partnerverwaltung ... ein Typ, viele Owner,
jede Instanz mit ihrer Herkunft."*

| tut                                                          | tut nicht                                              |
| ------------------------------------------------------------ | ------------------------------------------------------- |
| `Projekt` **definieren** (Herkunft `app:projekt`)             | Partnerverwaltung eine zweite Definition abverlangen    |
| Projekte als **Owner** anlegen, verknuepft mit einer Firma    | Firmen selbst anlegen oder ihre Kern-Gruppe lesen/schreiben |
| Firma nur als **Referenz** lesen (D6)                         | eine Liste/Suche ueber alle Kunden bieten (twin 0.1 hat keine) |

## Der geteilte Typ, ohne RFC-0031 D1 zu verletzen

D1 sagt: nur die Herkunft aendert ihre eigenen Typen. Diese App ist die
einzige, die `Projekt` unter `data_model.object_types` **definiert**.
[Partnerverwaltung](../partnerverwaltung/) bindet sich in ihrem
eigenen Manifest nur an den schon registrierten Typ
(`contributes: [{type: Projekt, role: owner}]`, ohne ihn neu zu
definieren) und legt auf ihrer Firma-Seite eigene Projekt-Instanzen
an. Geprueft am echten Code
([`platform/services/twin/app.py`](../../../oaap-reference/platform/services/twin/app.py)):
die Owner-Pruefung beim Anlegen fragt nur, ob DIESE Instanz
`{type: Projekt, role: owner}` in ihren EIGENEN Bindungen hat --
niemals, ob sie den Typ auch definiert hat. Zwei unabhaengige Apps
koennen also Projekt-Objekte anlegen, jedes mit seiner eigenen
Herkunft am Objekt (RFC-0031 §3.3) -- der "geteilte digitale Zwilling"
aus dem Zielbild, keine Ausnahme, kein Sonderfall im Code.

## Zugang zum Zwilling, Rollen, Prüfen

Wie jede andere Referenz-App: `OAAP_TWIN_URL`/`OAAP_PLATFORM_KEY`
plattformeigen, jeder angemeldete `user`.

```bash
python3 apps/projekt/test_twin_client.py
```

Das echte Zusammenspiel (Projekt-App UND Partnerverwaltung legen je
ein Projekt auf derselben Firma an) gehoert auf `oaap-test`.

## Design-Kontrakt (RFC-0035 Teil A)

Bindet `/platform/theme.css` ein, Mini-Kopfzeile (D4) statt eigener
Marke.
