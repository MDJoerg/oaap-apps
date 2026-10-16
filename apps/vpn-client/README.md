# VPN-Client — ein Tunnel als App (RFC-0060)

Erreicht ein System hinter einem **fremden OpenVPN-Server** — einer Tür,
die nicht uns gehört, mit Zugangsdaten, die uns jemand gegeben hat. Der
Tunnel lebt in **diesem einen Container**; was dahinter liegt, wird zur
**Destination** der Plattform (`through:<instanz>/<name>`). Die App, die
das Ziel benutzt, merkt keinen Unterschied zu einem direkten Ziel.

## Was die App tut — und nicht tut

- **Hält den Tunnel** und zeigt seinen Zustand auf `/` (nur `admin`) und in
  `/healthz` (200 nur, solange der Tunnel steht).
- **Traut der Gegenseite nichts.** Aus der `.ovpn`-Datei werden Routen,
  `redirect-gateway`, DNS, Skripte (`up`, `down`, …), `management` und
  Ähnliches entfernt und auf der Seite benannt; geschobene Routen und DNS
  werden ignoriert (`route-nopull`). Je Ziel-IP legt die App genau eine
  Route in den Tunnel. Ein Paketfilter lässt aus dem Tunnel nur Antworten
  auf eigene Verbindungen herein und verwirft alles Weitergeleitete. Steht
  der Filter nicht, startet der Tunnel nicht.
- **Bedient benannte Ziele** unter `/t/<name>/…` als schlichter
  Reverse-Proxy mit Streaming. Keine eigene Anmeldung und **kein
  Schlüssel des Ziels** — den ergänzt das Gateway aus der Destination.
  Wer über die Anmelde-Route der App kommt, bekommt die Ziele nicht
  (403).
- **Keine Generalprobe mit Tunnel** (RFC-0060 D6): ohne `/dev/net/tun`
  sagt die Seite das und versucht es nicht erneut.

## Einrichten (Betreiber, an der Kommandozeile)

```bash
# 1. Installieren -- nur der Betreiber kann das Tunnelrecht bestätigen
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/vpn-client \
     --name vpn --confirm-tun

# 2. Die .ovpn-Datei als EINE Zeile base64 (Konfigwerte sind einzeilig)
sudo oaap app config set vpn VPN_CONFIG "$(base64 -w0 client.ovpn)"
sudo oaap app config set vpn VPN_AUTH          # benutzer:passwort, verdeckt abgefragt
sudo oaap app config set vpn VPN_TARGETS "gaming = http://10.8.0.5:11434"
sudo oaap app restart vpn

# 3. Was dahinter liegt, als Destination -- mit dem Schlüssel des Ziels
sudo oaap destination add gaming --target through:vpn/gaming --auth bearer --secret-stdin
sudo oaap destination bind <verbraucher-instanz> gaming
```

Die Verbraucher-App ruft dann `OAAP_DESTINATION_GAMING_URL` auf.

## Konfiguration

| Schlüssel | Bedeutung |
| --- | --- |
| `VPN_CONFIG` | die `.ovpn`-Datei als base64, geheim |
| `VPN_AUTH` | `benutzer:passwort`, falls die Gegenstelle es verlangt, geheim |
| `VPN_TARGETS` | ein Ziel je Zeile: `name = http://adresse[:port][/basis]` |
| `VPN_KIND` | `openvpn` (WireGuard folgt als RFC-0060 Stufe 3) |
| `VPN_TIMEOUT_SECONDS` | Geduld gegenüber dem Ziel, 10–3600, Vorgabe 600 |

## Bekannte Grenzen

- `NET_ADMIN` im Container ist ein Versprechen dieser App, kein Zwang der
  Plattform; die Plattform erzwingt die Grenze des Netz-Namensraums
  (RFC-0060 §4).
- Nur HTTP-Ziele; TCP durch den Tunnel wartet auf dasselbe Übergabe-Design
  wie `via`.
- Ein Ziel unter einem Namen statt einer IP bekommt keine eigene Route.

Tests: `python3 test_vpn_client.py` (ohne Container, gegen ein echtes
HTTP-Ziel auf 127.0.0.1).
