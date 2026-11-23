#!/bin/sh
# Einstiegsschritt vor MariaDB (siehe Dockerfile). Zwei Regeln:
#
# 1. Ohne Datenbank-Geheimnis startet MariaDB nicht -- sonst richtet es sich
#    ohne Kennwort ein, und ein später gesetztes greift nie mehr.
#
# 2. Eine unvollständige Ersteinrichtung wird wiederholt. Gemessen am
#    2026-11-23: Wer zwei Werte der Instanz kurz nacheinander speichert, lässt
#    den Container zweimal neu erzeugen -- einmal mitten in MariaDBs
#    Ersteinrichtung. Zurück blieb ein Datenverzeichnis mit Systemtabellen,
#    aber ohne den Benutzer `bookstack`, und MariaDB richtet sich nur ein,
#    solange das Verzeichnis leer ist. Am ENDE der Ersteinrichtung legt
#    initdb.d/zz-oaap-done.sh deshalb eine Marke ab. Fehlt sie in einem nicht
#    leeren Verzeichnis, war die Einrichtung unvollständig -- und dann hat dort
#    nie jemand Daten geschrieben: Während der Ersteinrichtung lauscht MariaDB
#    nicht im Netz (--skip-networking). Das Verzeichnis wird geleert.
set -eu
DATA=/var/lib/mysql
MARK="$DATA/.oaap-initialized"

if [ -z "${MARIADB_PASSWORD:-}" ]; then
    echo "oaap-bookstack-db: MARIADB_PASSWORD fehlt -- MariaDB richtet sich nicht ohne Kennwort ein." >&2
    echo "Setzen: Portal -> diese Instanz -> Konfiguration (oder: oaap app config set <instanz> MARIADB_PASSWORD)." >&2
    # Nicht sofort beenden: sonst startet der Container im Sekundentakt neu.
    sleep 30
    exit 1
fi

if [ ! -e "$MARK" ] && [ -n "$(ls -A "$DATA" 2>/dev/null)" ]; then
    echo "oaap-bookstack-db: unfinished first setup found (no $MARK) -- emptying $DATA and setting up again" >&2
    find "$DATA" -mindepth 1 -delete
fi

exec docker-entrypoint.sh "$@"
