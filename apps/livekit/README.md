# LiveKit auf OAAP — Echtzeit-Medien (Referenz)

Referenz-Implementierung der **Echtzeit-Medien-Capability** (RFC-0017). Die
Fähigkeit gehört der Plattform; LiveKit ist die austauschbare Umsetzung — so
wie Caddy die Referenz fürs Gateway ist, ohne dass „Gateway = Caddy" in einer
Spec steht.

## Was es ist

Ein **SFU** (Selective Forwarding Unit): Clients verbinden sich *zu ihm*,
statt untereinander ein Mesh aufzuspannen. Damit ist NAT per Konstruktion
gelöst und die Last wächst linear statt quadratisch. Genau die Empfehlung aus
der bdt-hub-Antwort — kein TURN-Relay, sondern ein SFU als OAAP-App.

## Aufbau

- **Zwei Container** (RFC-0016): `livekit` (der Server) und `redis` (Zustand).
  Beide liegen auf dem privaten Instanznetz und finden sich über den
  Dienstnamen. `redis` hat **keine Route und keinen Endpunkt** — von außen und
  von anderen Apps nie erreichbar.
- **Signaling** (Steuerkanal + WebSocket) läuft als `public`-Route über das
  Gateway und bekommt dort TLS/WSS. `public`, weil LiveKit jede Anfrage selbst
  per signiertem Token prüft; die Mengenbremse (RFC-0010) greift weiter.
- **Medien** laufen über **einen festen Port** (`fixed: true`, RFC-0017 §5.1),
  UDP-Medien und TCP-Rückfallebene auf derselben Nummer (`both`). Der Port ist
  fest, weil ein Medienserver ihn selbst bei seinen Clients bewirbt
  (ICE-Kandidaten tragen die Nummer) — ein still umvergebener Port bräche die
  Verbindung.

## Der Server erzeugt seine Konfiguration beim Start

`entrypoint.sh` baut die LiveKit-Konfiguration aus den OAAP-Umgebungsvariablen
und übergibt sie als `--config-body` (nichts wird ins Dateisystem geschrieben):

- `LIVEKIT_API_SECRET` (Pflicht, `secret: true`, ≥ 32 Zeichen) und
  `LIVEKIT_API_KEY` (Kennung) → das Schlüsselpaar, mit dem die Meeting-App ihre
  Client-Tokens signiert.
- `OAAP_ENDPOINT_PORT` → der feste Medienport (`rtc.udp_port` = `rtc.tcp_port`).
- `rtc.use_external_ip: true` → LiveKit ermittelt die öffentliche Adresse per
  STUN und bewirbt sie in den ICE-Kandidaten.

## Betrieb (server_admin)

1. Installieren (Knoten mit Profil `exposed`).
2. In der Instanz-Konfiguration `LIVEKIT_API_SECRET` setzen (≥ 32 Zeichen);
   optional `LIVEKIT_API_KEY`.
3. Den Medienport freigeben: Portal-Karte „Direkter Port" oder
   `sudo oaap app endpoint allow <instanz> media`.
4. Auf dem Router die genannte Freigabe einrichten (UDP **und** TCP auf den
   festen Port → dieser Knoten). Die Gesundheitsseite prüft danach die
   Erreichbarkeit (RFC-0015 Q4).
5. Schlüssel und Geheimnis derselben Meeting-App geben, die die Tokens erzeugt.

## Bewusste Grenzen

- Ein Knoten, ein fester Medienport: zwei LiveKit-Instanzen auf demselben
  Knoten können sich den Port nicht teilen — die zweite Freigabe scheitert
  laut (das ist Absicht).
- Skalierung über mehrere Knoten (LiveKit-Cluster) ist eine spätere Stufe;
  Redis ist dafür schon vorgesehen.
- Server-seitige Aufzeichnung (Egress) ist nicht enthalten.
