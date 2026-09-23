#!/bin/bash
# Erzeugt Keycloaks Konfiguration aus den OAAP-Umgebungsvariablen und
# startet den Server. Nichts wird ins Dateisystem geschrieben.
set -eu

# --- Die eine Angabe, ohne die dieser Dienst nicht starten darf ------
#
# KC_HOSTNAME ist die öffentliche Adresse dieser Instanz, und Keycloak
# schreibt sie als `issuer` in jedes Token. OAAP bindet eine Person an
# `(Anbieter, sub)`, und der Anbieter IST dieser issuer (RFC-0041 K4).
# Würde Keycloak ihn stattdessen aus der Host-Kopfzeile jeder Anfrage
# ableiten — was es mit KC_HOSTNAME_STRICT=false täte —, dann hinge die
# Identität einer Bindung an dem Namen, unter dem jemand gerade
# vorbeikam. Das ist keine Bequemlichkeit, die man sich leisten kann.
#
# Also: lieber gar nicht starten als falsch starten, und die Meldung
# sagt, was zu tun ist.
if [ -z "${KC_HOSTNAME:-}" ]; then
  echo "KC_HOSTNAME fehlt." >&2
  echo "" >&2
  echo "Das ist die oeffentliche Adresse dieser Instanz, z. B." >&2
  echo "  https://auth.<knoten>" >&2
  echo "Keycloak schreibt sie in jedes Token ('issuer'), und OAAP bindet" >&2
  echo "die Benutzer daran. Ein aus der Anfrage abgeleiteter issuer" >&2
  echo "waere eine Identitaet, die sich mit dem Hostnamen aendert." >&2
  echo "" >&2
  echo "Setzen: Portal -> diese Instanz -> Konfiguration." >&2
  exit 1
fi

if [ -z "${POSTGRES_PASSWORD:-}" ]; then
  echo "POSTGRES_PASSWORD fehlt: In der Instanz-Konfiguration ein" >&2
  echo "Geheimnis setzen (Portal -> diese Instanz)." >&2
  exit 1
fi

DB_HOST="${KEYCLOAK_DB_HOST:-db}"
DB_PORT="${KEYCLOAK_DB_PORT:-5432}"
DB_NAME="${POSTGRES_DB:-postgres}"
DB_USER="${POSTGRES_USER:-postgres}"

# Kurz auf den Mitdienst `db` warten (RFC-0016 Mehr-Container). Ohne das
# bricht Quarkus beim Start ab, der Container startet neu, und der
# Betreiber sieht eine Neustart-Schleife statt "die Datenbank war noch
# nicht offen". Best effort: kommt sie nicht, startet Keycloak trotzdem
# und die Neustart-Regel holt es nach.
i=0
while [ "$i" -lt 30 ]; do
  if (exec 3<>"/dev/tcp/${DB_HOST}/${DB_PORT}") 2>/dev/null; then
    exec 3>&- 2>/dev/null || true
    break
  fi
  i=$((i + 1))
  sleep 1
done

export KC_DB=postgres
export KC_DB_URL="jdbc:postgresql://${DB_HOST}:${DB_PORT}/${DB_NAME}"
export KC_DB_USERNAME="${DB_USER}"
export KC_DB_PASSWORD="${POSTGRES_PASSWORD}"
# Hinter dem OAAP-Gateway: der Klartext-Port ist der interne, und die
# Frage, ob der Besucher ueber TLS kam, beantworten die Kopfzeilen, die
# das Gateway setzt. Ohne `xforwarded` schriebe Keycloak http:// in
# seine eigenen Weiterleitungen, obwohl der Browser https:// sieht.
export KC_HTTP_ENABLED=true
export KC_PROXY_HEADERS=xforwarded
export KC_HEALTH_ENABLED=true

exec /opt/keycloak/bin/kc.sh start --optimized
