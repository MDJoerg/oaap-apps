# MariaDB für BookStack, mit einem Einstiegsschritt davor (entrypoint.sh).
#
# Warum nicht das nackte Abbild: Gemessen am 2026-11-23 auf oaap-test --
# `oaap app install` über die Kommandozeile erzeugt die Geheimnisse
# (`generate: token`) nicht, das bietet nur das Portal an. Das nackte
# MariaDB-Abbild hat sich daraufhin beim ersten Start mit dem Benutzer
# `bookstack` OHNE Kennwort eingerichtet; ein später gesetztes Kennwort
# greift dann nie mehr (MariaDB richtet sich nur einmal ein). Der Schritt
# davor startet ohne Geheimnis gar nicht -- lieber nicht starten als falsch
# einrichten.
#
# Exakte Fassung (care.rebuild: self), gemessen am 2026-11-20.
FROM mariadb:11.4.13

COPY entrypoint.sh /usr/local/bin/oaap-db-entrypoint
COPY zz-oaap-done.sh /docker-entrypoint-initdb.d/zz-oaap-done.sh
RUN chmod 755 /usr/local/bin/oaap-db-entrypoint && chmod 644 /docker-entrypoint-initdb.d/zz-oaap-done.sh

ENTRYPOINT ["/usr/local/bin/oaap-db-entrypoint"]
CMD ["mariadbd"]
