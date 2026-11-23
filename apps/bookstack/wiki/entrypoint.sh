#!/bin/sh
# Einstiegsschritt vor dem BookStack-Abbild (siehe Dockerfile).
# Übersetzt die Werte der Instanz in BookStacks Einstellungen und startet
# dann /init des Abbilds. Schreibt nichts ins Dateisystem.
set -eu

die() { echo "oaap-bookstack: $*" >&2; exit 1; }

# 1. Die öffentliche Adresse. BookStack baut daraus jede Verknüpfung UND die
#    Rücksprungadresse für den Anmeldedienst. Immer https: hinter dem Gateway
#    spricht der Knoten http, der Browser sieht https.
if [ -z "${APP_URL:-}" ]; then
    [ -n "${OAAP_INSTANCE_NAMES:-}" ] || die "keine Adresse: weder APP_URL noch OAAP_INSTANCE_NAMES gesetzt"
    first=${OAAP_INSTANCE_NAMES%%,*}
    host=${first#*://}
    host=${host%%/*}
    export APP_URL="https://${host}"
fi

# 2. Der Anwendungsschlüssel. BookStack (Laravel) verlangt 32 Byte als
#    base64:… -- wir leiten ihn stabil aus dem erzeugten Geheimnis ab, damit
#    der Betreiber nie ein Format kennen muss.
[ -n "${OAAP_WIKI_APP_SECRET:-}" ] || die "OAAP_WIKI_APP_SECRET fehlt (Instanz-Konfiguration)"
APP_KEY=$(php -r 'echo "base64:".base64_encode(hash("sha256", getenv("OAAP_WIKI_APP_SECRET"), true));')
export APP_KEY

# 3. Die Datenbank ist der Mitdienst `db` (RFC-0016), mit denselben Angaben,
#    mit denen MariaDB sich einrichtet -- eine Angabe statt zweier.
[ -n "${MARIADB_PASSWORD:-}" ] || die "MARIADB_PASSWORD fehlt (Instanz-Konfiguration)"
export DB_HOST="${OAAP_WIKI_DB_HOST:-db}"
export DB_PORT=3306
export DB_DATABASE="${MARIADB_DATABASE:-bookstack}"
export DB_USERNAME="${MARIADB_USER:-bookstack}"
export DB_PASSWORD="$MARIADB_PASSWORD"

# Warten, bis eine echte ANMELDUNG gelingt -- nicht nur, bis der Port offen
# ist. Gemessen am 2026-11-23: MariaDB öffnet den Port schon während seiner
# Ersteinrichtung; das Abbild prüft nur den Port, versucht die Migration
# EINMAL ("Host … is not allowed to connect") und BookStack bleibt ohne
# Tabellen, bis jemand den Container neu startet.
i=0
until php -r 'try { new PDO("mysql:host=".getenv("DB_HOST").";port=3306;dbname=".getenv("DB_DATABASE"), getenv("DB_USERNAME"), getenv("DB_PASSWORD"), [PDO::ATTR_TIMEOUT => 3]); } catch (Throwable $e) { exit(1); }' 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -eq 1 ] || [ $((i % 12)) -eq 0 ]; then
        echo "oaap-bookstack: waiting for the database ($DB_HOST) to accept $DB_USERNAME ..."
    fi
    [ "$i" -lt 120 ] || die "the database did not accept $DB_USERNAME within 10 minutes"
    sleep 5
done

# 4. Bilder und Anhänge nur für wen die Seite sehen darf -- vom ersten Start
#    an und nicht abschaltbar (RFC-0110 §4, Befund F5: Bilder, die im offenen
#    Modus hochgeladen wurden, bleiben offen, auch nach dem Umschalten).
export STORAGE_TYPE=local_secure_restricted

# 5. Anmeldung über den Realm des Mandanten (RFC-0091 §4, Manifest
#    `identity.oidc_client`), sobald der Betreiber der Instanz einen Client
#    gegeben hat. Die Gruppen des Realms sind die Rollen im Wiki: eine Rolle
#    gilt, wenn ihre „Externe Authentifizierungs-ID“ der Gruppenpfad ist
#    (z. B. /wiki-redaktion). Abgeglichen wird bei jeder Anmeldung,
#    auch der Entzug (Befund F1).
if [ -n "${OAAP_OIDC_ISSUER:-}" ] && [ -n "${OAAP_OIDC_CLIENT_ID:-}" ] \
        && [ -n "${OAAP_OIDC_CLIENT_SECRET:-}" ]; then
    export AUTH_METHOD=oidc
    export AUTH_AUTO_INITIATE="${OAAP_WIKI_AUTO_LOGIN:-true}"
    export OIDC_NAME="${OAAP_WIKI_LOGIN_LABEL:-Anmeldedienst}"
    export OIDC_ISSUER="${OAAP_OIDC_ISSUER%/}"
    export OIDC_ISSUER_DISCOVER=true
    export OIDC_CLIENT_ID="$OAAP_OIDC_CLIENT_ID"
    export OIDC_CLIENT_SECRET="$OAAP_OIDC_CLIENT_SECRET"
    export OIDC_DISPLAY_NAME_CLAIMS=name
    export OIDC_USER_TO_GROUPS=true
    export OIDC_GROUPS_CLAIM=groups
    export OIDC_REMOVE_FROM_GROUPS=true
    echo "oaap-bookstack: sign-in through the tenant's realm ($OIDC_ISSUER, client $OIDC_CLIENT_ID)"
else
    export AUTH_METHOD=standard
    echo "oaap-bookstack: no client of the tenant's realm yet -- only the local administrator signs in"
fi

# Die OAAP-Geheimnisse braucht BookStack nicht: nicht an /init weiterreichen
# (s6 legt die Umgebung sonst als Dateien in /run/s6/container_environment ab).
unset OAAP_WIKI_APP_SECRET OAAP_OIDC_CLIENT_SECRET OAAP_WIKI_ADMIN_PASSWORD \
      OAAP_SETUP_KEY MARIADB_ROOT_PASSWORD 2>/dev/null || true

exec /init "$@"
