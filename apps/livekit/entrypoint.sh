#!/bin/sh
# Erzeugt die LiveKit-Konfiguration aus den OAAP-Umgebungsvariablen und
# startet den Server. Nichts wird ins Dateisystem geschrieben — die
# Konfiguration geht als --config-body direkt an den Prozess.
set -eu

# Das Geheimnis MUSS gesetzt sein (LiveKit verlangt >= 32 Zeichen). Ohne es
# gibt es keine gültigen Schlüssel und der Server soll gar nicht erst
# vorgeben zu laufen — die Meldung sagt dem Betreiber, was zu tun ist.
if [ -z "${LIVEKIT_API_SECRET:-}" ]; then
  echo "LIVEKIT_API_SECRET fehlt: In der Instanz-Konfiguration ein Geheimnis" >&2
  echo "mit mindestens 32 Zeichen setzen (Portal -> diese Instanz)." >&2
  exit 1
fi

API_KEY="${LIVEKIT_API_KEY:-oaap}"
# Fester Endpunkt (RFC-0017 §5.1): host == container == OAAP_ENDPOINT_PORT.
# Vor der Freigabe ist die Variable nicht gesetzt; dann der Manifest-Wert.
MEDIA_PORT="${OAAP_ENDPOINT_PORT:-8280}"
REDIS_ADDR="${LIVEKIT_REDIS:-redis:6379}"

# Kurz auf den Mitdienst redis warten (RFC-0016), damit LiveKit nicht
# crash-loopt, falls redis beim Start noch nicht offen ist. Best effort:
# kommt redis nicht, startet LiveKit trotzdem und der Neustart holt es nach.
RHOST="${REDIS_ADDR%%:*}"
RPORT="${REDIS_ADDR##*:}"
i=0
while [ "$i" -lt 15 ]; do
  if nc -z "$RHOST" "$RPORT" 2>/dev/null; then break; fi
  i=$((i + 1))
  sleep 1
done

CONFIG="port: 7880
log_level: info
rtc:
  udp_port: ${MEDIA_PORT}
  tcp_port: ${MEDIA_PORT}
  use_external_ip: true
redis:
  address: ${REDIS_ADDR}
keys:
  ${API_KEY}: ${LIVEKIT_API_SECRET}"

exec /livekit-server --config-body "$CONFIG"
