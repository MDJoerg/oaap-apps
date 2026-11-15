# OAAP FleetView

Die OAAP-Flotte auf einen Blick — **strikt lesend** (RFC-0021,
RFC-0085 §10). Seit 1.0 liest FleetView die Flotte aus dem **Zwilling
seines Mandanten auf der Zentrale**. Der Flottendienst der Zentrale
schreibt jeden gekoppelten Knoten (`FleetNode`) und seine Instanzen
(`FleetInstance`) dorthin (RFC-0085 Nachtrag A2). FleetView fragt
keinen Knoten mehr selbst ab, hält keine Knotenliste und keinen
Schlüssel je Knoten:

- Ein **neu gekoppelter Knoten** erscheint von selbst.
- Der **Name** ist der, den der Knoten unter „Gesundheit“ trägt
  (Alias, RFC-0082). Der feste Knotenname steht klein daneben, solange
  beide abweichen.

Gehandelt wird im Portal des jeweiligen Knotens, gekoppelt im Portal
der Zentrale. Jede Zeile verlinkt dorthin.

## Einrichtung

Voraussetzung: eine Zentrale (Profil `fleet-hub`), die in einen
Mandanten schreibt (`sudo oaap fleet hub twin <mandant>`, meist
`oaap-ops`).

1. FleetView auf der Zentrale **in diesen Mandanten** installieren.
   Die Installation bindet die Typen `FleetNode` und `FleetInstance` und
   stellt dabei den Maschinenschlüssel der Instanz aus
   (`OAAP_PLATFORM_KEY`, `OAAP_TWIN_URL`).
   - Auf einem Knoten, der die Flotten-Typen nie registriert hat (keine
     Zentrale), verweigert die Installation die Bindung.
   - In einem **anderen Mandanten** der Zentrale gelingt die
     Installation, aber die Liste bleibt leer: Jeder Mandant liest nur
     seinen eigenen Zwilling. Ein Kundenmandant sieht so nie die Flotte
     (gemessen auf oaap-test, 15.11.2026).
2. Fertig. Einzige Einstellung: `FLEETVIEW_POLL_SECONDS`, der Lesetakt
   (Standard 60).

## Von 0.x auf 1.0

0.x fragte jeden Knoten einzeln über `GET /fleet/status` mit einem
eigenen Schlüssel ab (`FLEETVIEW_NODES`, `FLEETVIEW_KEYS`). Eine
0.x-Instanz wird **nicht aktualisiert**, sondern 1.0 wird in den
Mandanten der Flotte neu installiert. Sobald 1.0 alle Knoten zeigt,
kommt die alte Instanz weg, und die Flotten-Schlüssel der alten
Instanz werden auf den Knoten widerrufen (`sudo oaap fleet key list`,
`… revoke`).

## Zustand eines Knotens

Das **Lebenszeichen** (das Urteil der Zentrale) geht vor der
Selbstauskunft:

| Lebenszeichen | Angezeigt |
| --- | --- |
| `removed` (an der Zentrale entfernt) | Entfernt, am Ende der Liste |
| `silent` (kein `alive` mehr) | Stumm, oben unter „Braucht einen Menschen“ |
| `unknown` (Eingang getrennt, Zentrale startet) | Unbekannt |
| `up` | der schlechteste Zustand aus Kerndiensten und Instanzen; Standby ist kein Fehler |

## Der automatische Name je Instanz

Jede Instanz hat einen automatischen Namen `<instanz>.<knoten>`. Das
Urteil daneben sagt, **ob die Instanz unter diesem Namen auf ihrem
Knoten erreichbar ist**: Route auf dem Gateway vorhanden, App antwortet
dahinter. Ausdrücklich **keine** Aussage über TLS oder Erreichbarkeit
von außen (RFC-0015).

## Grenzen (bewusst)

- Kein Schreibweg, keine Fernsteuerung.
- Alarmiert wird von der Zentrale (RFC-0080), nicht von FleetView.
- Der Maschinenschlüssel wird nie angezeigt, nie unter /data
  gespeichert und nie geloggt.

## Tests

    python3 apps/fleetview/test_fleet.py

Ohne Netz, ohne Docker, ohne Zwilling (ein kleiner Schein-Zwilling auf
127.0.0.1 antwortet wie `GET /twin/objects?type=`).
