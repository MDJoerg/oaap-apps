# Läuft als letzter Schritt von MariaDBs Ersteinrichtung (docker-entrypoint-initdb.d),
# nachdem Datenbank und Benutzer angelegt sind. Die Marke sagt entrypoint.sh:
# diese Einrichtung ist vollständig. (Wird vom Abbild eingelesen, nicht ausgeführt.)
touch /var/lib/mysql/.oaap-initialized
