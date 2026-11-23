# Der eigene Suchdienst der App recherche (Dienst `search`, RFC-0116 §2.2a).
# Das offizielle SearXNG-Image, nur mit den Einstellungen, die recherche
# braucht: JSON an, keine Bremse, kein Bilder-Proxy. Kein Port nach außen,
# keine Route: nur der Dienst web derselben Instanz fragt ihn.
#
# Version festgenagelt (care.rebuild: self): der Stand, der auf oaap-test
# gemessen wurde (2026-11-20). Ein Neubau holt nur denselben Stand neu.
FROM searxng/searxng:2026.11.20-cd6d28091@sha256:b513f1ad02c1b0a985d801190eafc3cbcacdf661de8b1fe7b54fa67bafe97a7e

COPY settings.yml /etc/searxng/settings.yml
COPY oaap-entrypoint.sh /usr/local/bin/oaap-entrypoint
RUN chmod 755 /usr/local/bin/oaap-entrypoint

ENTRYPOINT ["/usr/local/bin/oaap-entrypoint"]
