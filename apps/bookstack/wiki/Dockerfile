# BookStack, gebaut über dem Abbild von linuxserver.io (RFC-0110 §4, D10):
# Es gibt kein offizielles BookStack-Abbild. Wir bauen BookStack NICHT
# selbst und ändern nichts an seinem Code -- davor liegt nur ein
# Einstiegsschritt, der die OAAP-Werte in BookStacks Einstellungen übersetzt
# (entrypoint.sh). Danach startet das Abbild wie immer (/init, s6).
#
# Version festgenagelt (care.rebuild: self, RFC-0093 §7): ein exakter Tag.
# Ein Patch-Release von BookStack ist eine neue Paketversion (RFC-0110 §6).
# Gemessen am 2026-11-20 (oaap-lab/wiki-probe) und am 2026-11-23 als Paket.
FROM lscr.io/linuxserver/bookstack:version-v26.09.3

COPY entrypoint.sh /usr/local/bin/oaap-entrypoint
RUN chmod 755 /usr/local/bin/oaap-entrypoint

ENTRYPOINT ["/usr/local/bin/oaap-entrypoint"]
