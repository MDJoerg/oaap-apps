# Wrapper um das offizielle code-server-Abbild (coder/code-server).
# Wir bauen VS Code nicht selbst — wir legen Werkzeuge, vorinstallierte
# Erweiterungen und einen Entrypoint darüber, der die Konfiguration aus
# den OAAP-Umgebungsvariablen zieht.
#
# DIE VERSION IST FESTGENAGELT: 4.139.1 ist die Fassung vom 26.09.2026,
# gegen die diese App gemessen wurde. Erweiterungen kommen von Open VSX,
# und ob eine bestimmte Erweiterung mit einer bestimmten VS-Code-Fassung
# zusammenarbeitet, ändert sich mit beiden Seiten — eine Zahl, die man
# sieht, statt eines `latest`, das über Nacht anders ist. Prüfbar im
# Terminal: `code-server --version`.
FROM ghcr.io/coder/code-server:4.139.1

USER root

# Werkzeuge, die ein SAP-/KI-Kurs in der ersten Stunde braucht und die
# sonst jeder Teilnehmer einzeln nachinstallierte: Node.js für CAP,
# Fiori-Tools und die Claude-CLI; Python für Skripte; zip/unzip/jq
# fürs Material. Debian 13 liefert Node 20 — für neuere Fassungen
# ist `sudo` im Container da (IDE_SUDO) oder das Kursmaterial.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      nodejs npm python3 python3-venv python3-pip \
      jq zip unzip less nano vim-tiny openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Saat: Einstellungen, Begrüßung und vorinstallierte Erweiterungen liegen
# im Abbild unter /opt/oaap/seed und werden beim ERSTEN Start ins Zuhause
# kopiert (der Entrypoint tut das). Direkt ins Zuhause zu installieren
# hilft nicht: /home/coder ist ein Storage-Mount und beim ersten Start
# leer, es würde alles verdecken, was das Abbild dort abgelegt hat.
COPY seed/ /opt/oaap/seed/
COPY entrypoint.sh /usr/local/bin/oaap-entrypoint.sh
RUN chmod 755 /usr/local/bin/oaap-entrypoint.sh \
 && mkdir -p /opt/oaap/seed/extensions \
 && chown -R 1000:1000 /opt/oaap/seed

USER 1000

# Die drei Erweiterungen, um die es in Jörgs Test geht — aus Open VSX,
# zur Bauzeit, damit der Start einer Teilnehmer-Instanz keine Minute
# im Netz hängt. Alle drei am 30.09.2026 auf Open VSX gefunden:
# Anthropic.claude-code 2.1.285, murbani.vscode-abap-remote-fs 2.10.2,
# SAPSE.vscode-cds 10.1.1. Das Fiori-Tools-Paket
# (SAPSE.sap-ux-fiori-tools-extension-pack) ist groß und kursabhängig —
# per IDE_EXTENSIONS nachladbar.
RUN code-server \
      --extensions-dir /opt/oaap/seed/extensions \
      --user-data-dir /tmp/oaap-build-userdata \
      --install-extension Anthropic.claude-code \
      --install-extension murbani.vscode-abap-remote-fs \
      --install-extension SAPSE.vscode-cds \
 && rm -rf /tmp/oaap-build-userdata

ENTRYPOINT ["/usr/local/bin/oaap-entrypoint.sh"]
