# OAAP FleetView

Die OAAP-Flotte auf einen Blick — **strikt lesend** (RFC-0021 §3,
Spec `oaap.fleet.status` 0.3). FleetView pollt die konfigurierten
Knoten über deren `GET /fleet/status` und zeigt Ampeln,
Plattformversionen, Instanzen und alle `attention`-Merker der
Landschaft. Gehandelt wird im Portal des jeweiligen Knotens — jede
Zeile verlinkt dorthin.

## Einrichtung

1. Auf jedem zu beobachtenden Knoten einen Schlüssel ausstellen:

       sudo oaap fleet key issue fleetview@<mein-knoten>

   Der Schlüssel wird genau einmal angezeigt und kann ausschließlich
   die Status-Auskunft lesen (`revoke` wirkt sofort).

2. FleetView aus dem Store installieren und in der Instanz-Konfiguration
   (Portal → Instanzen → Konfiguration) setzen:

   - `FLEETVIEW_NODES` — eine Angabe je Zeile oder mit `;` getrennt:
     `name=https://adresse`
   - `FLEETVIEW_KEYS` *(geheim)* — je Knoten `name=schlüssel`, `;` trennt
   - `FLEETVIEW_POLL_SECONDS` — Abfragetakt, Standard 60

## Der automatische Name je Instanz (seit 0.3.0)

Jede Instanz hat einen automatischen Namen `<instanz>.<knoten>`. Der
steht **nicht** in der DNS-Sicht oben, und das ist Absicht: Automatische
Namen werden von einem Wildcard-Eintrag beantwortet, und ein Wildcard
antwortet für *jeden* Namen darunter — auch für nie installierte. Ein
DNS-Urteil darüber könnte nie etwas anderes sagen als das Urteil über
den Knotennamen selbst.

Je Instanz einen eigenen DNS-Eintrag anzulegen bleibt möglich, ist aber
nicht der Standard (Entscheidung Jörg, 2026-08-24): Das legte das ganze
Instanz-Inventar eines Knotens in DNS offen und müsste je Instanz
gepflegt und überwacht werden.

Der automatische Name bekommt darum die Frage, die tatsächlich
schiefgehen kann: **Ist die Instanz unter diesem Namen auf ihrem Knoten
erreichbar?** — Route auf dem Gateway vorhanden, App antwortet dahinter.
Der stille Fehlerfall dahinter ist real: Für eine Instanz ohne erfasste
Routen erzeugt der Knoten keine Site, und ihr automatischer Name landet
dann auf dem Auffangeintrag statt bei der App.

Ausdrücklich **keine** Aussage über TLS, über Auflösbarkeit bei einem
bestimmten Client oder über Erreichbarkeit von außen — das bleibt Sache
der Reach-Prüfungen (RFC-0015). Knoten mit Plattform < 0.1.46 liefern
das Feld nicht; die Spalte bleibt dann leer.

## Grenzen (bewusst)

- Kein Schreibweg, keine Fernsteuerung, keine Selbstheilung —
  das ist Stufe 2 (RFC-0021 Ausblick, signierte Aufträge).
- Alarmierung bleibt Sache des Monitorings (Uptime Kuma).
- Schlüssel werden nie angezeigt, nie unter /data gespeichert und
  nie geloggt; die Oberfläche nennt nur Knotennamen mit Schlüssel.

## Tests

    python3 apps/fleetview/test_fleet.py

Ohne Netz, ohne Docker, ohne Knoten.
