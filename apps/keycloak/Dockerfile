# Wrapper um den offiziellen Keycloak-Server. Wir bauen Keycloak NICHT
# selbst — wir legen nur einen Entrypoint darüber, der die Konfiguration
# aus den OAAP-Umgebungsvariablen erzeugt, auf den Mitdienst `db`
# wartet (RFC-0016) und dann den optimierten Server startet.
#
# DIE VERSION IST FESTGENAGELT, und das ist keine Vorsicht, sondern eine
# Auflage: RFC-0041 K3 lässt OAAP die Realms über Keycloaks eigene
# Verwaltungsschnittstelle anlegen, und so eine Anbindung altert gegen
# ein fremdes Produkt — unsichtbar, bis sie bricht. `26.7.4` ist die
# Fassung, gegen die am 22.09.2026 gemessen wurde (Realm anlegen,
# Client anlegen, Geheimnis abholen, Export/Import mit erhaltenem
# `sub`). Der Server nennt seine Fassung selbst unter
# `/admin/serverinfo`, und genau das macht diese Zeile prüfbar statt
# nur aufgeschrieben.
FROM quay.io/keycloak/keycloak:26.7.4

# Der Bau-Schritt, den `start --optimized` voraussetzt. Hier statt beim
# Containerstart, weil er sonst bei jedem Neustart erneut liefe und die
# Instanz eine halbe Minute später antwortete.
USER root
RUN /opt/keycloak/bin/kc.sh build --db=postgres

COPY entrypoint.sh /entrypoint.sh
RUN chmod 755 /entrypoint.sh

USER 1000
ENTRYPOINT ["/entrypoint.sh"]
