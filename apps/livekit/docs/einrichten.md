# Einrichten und freigeben

## Voraussetzung

Ein Knoten, der von außen erreichbar ist (Profil `exposed`), und Zugang
zum Router davor.

## Schritte

1. **Installieren** – im Portal oder am Knoten wie jede andere App.
2. **Geheimnis setzen** – in der Konfiguration der Instanz
   `LIVEKIT_API_SECRET` (mindestens 32 Zeichen; der Knoten kann es
   erzeugen). `LIVEKIT_API_KEY` ist nur eine Kennung, Vorgabe `oaap`.
3. **Medienport freigeben** – im Portal auf der Karte „Direkter Port“ oder
   am Knoten:
   ```sh
   sudo oaap app endpoint allow <instanz> media
   ```
4. **Router einrichten** – UDP **und** TCP auf den genannten Port zu diesem
   Knoten weiterleiten. Die Gesundheitsseite der Instanz prüft danach, ob
   der Port von außen erreichbar ist.
5. **Besprechungs-App verbinden** – dieselbe Kennung und dasselbe Geheimnis
   in der Besprechungs-App eintragen, dazu die Adresse dieser Instanz.

## Prüfen

- Die Instanz ist gesund (Portal).
- Die Gesundheitsseite meldet den Medienport als erreichbar.
- Zwei Teilnehmer an verschiedenen Netzen sehen und hören sich in der
  Besprechungs-App.
