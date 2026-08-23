# OAAP FleetView

Die OAAP-Flotte auf einen Blick — **strikt lesend** (RFC-0021 §3,
Spec `oaap.fleet.status` 0.1). FleetView pollt die konfigurierten
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

## Grenzen (bewusst)

- Kein Schreibweg, keine Fernsteuerung, keine Selbstheilung —
  das ist Stufe 2 (RFC-0021 Ausblick, signierte Aufträge).
- Alarmierung bleibt Sache des Monitorings (Uptime Kuma).
- Schlüssel werden nie angezeigt, nie unter /data gespeichert und
  nie geloggt; die Oberfläche nennt nur Knotennamen mit Schlüssel.

## Tests

    python3 apps/fleetview/test_fleet.py

Ohne Netz, ohne Docker, ohne Knoten.
