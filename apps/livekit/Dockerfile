# Wrapper um den offiziellen LiveKit-Server. Wir bauen LiveKit NICHT selbst —
# wir legen nur einen Entrypoint darüber, der die Konfiguration aus den
# OAAP-Umgebungsvariablen erzeugt (Einzelport-Medien, Redis, Schlüssel) und
# den festen Medienport aus OAAP_ENDPOINT_PORT übernimmt (RFC-0017 §5.1).
#
# Das Image ist Alpine-basiert und Multi-Arch (amd64 + arm64), passt also zu
# ADR-0005 und zum Build-auf-dem-Zielknoten. Version bewusst gepinnt.
FROM livekit/livekit-server:v1.13.5

# nc für einen kurzen Warteschritt auf den Mitdienst redis (RFC-0016
# Mehr-Container): livekit startet als primärer Dienst zuerst, redis danach —
# ohne Warten würde LiveKit beim Start crash-loopen, bis redis offen ist.
RUN apk add --no-cache netcat-openbsd

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Ersetzt den Standard-Entrypoint ["/livekit-server"]; unser Skript baut die
# Konfiguration und ruft am Ende /livekit-server auf.
ENTRYPOINT ["/entrypoint.sh"]
