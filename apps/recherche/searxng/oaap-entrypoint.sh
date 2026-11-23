#!/bin/sh
# Der Schlüssel von SearXNG (server.secret_key) entsteht beim ersten Start im
# eigenen Speicher und bleibt dort; er geht nie in die Konfiguration der App
# und nie an den Dienst web. Gemessen am 23.11.: über die Kommandozeile
# installiert, blieb ein erzeugter Konfigurationswert leer, und SearXNG lief
# dann mit leerem Schlüssel -- deshalb hier, ohne Ausnahme.
set -eu
KEYFILE=/var/cache/searxng/.oaap-secret
if [ ! -s "$KEYFILE" ]; then
    umask 077
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$KEYFILE"
fi
SEARXNG_SECRET="$(cat "$KEYFILE")"
if [ "${#SEARXNG_SECRET}" -lt 32 ]; then
    echo "oaap-searxng: kein brauchbarer Schlüssel in $KEYFILE -- Start abgebrochen" >&2
    exit 1
fi
export SEARXNG_SECRET
exec /usr/local/searxng/entrypoint.sh "$@"
