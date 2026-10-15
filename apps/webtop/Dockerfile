# Desktop im Browser als OAAP-App — ENTWURF 2026-10-14, noch nie gebaut.
# Abgeleitet aus brandeis-consulting/webtop-schulung (Dockerfile v5, 13.10.2026),
# ohne Branding und ohne Kursmaterial. Die Fallen aus deren UEBERGABE.md §7
# gelten hier unverändert: kein seccomp:unconfined, Eclipse-Release passend
# zu ADT, inotify-Limits des Knotens, Chromium ohne Sandbox.
#
# Basis: linuxserver.io Webtop (XFCE), Zugriff per Browser über Selkies auf Port 3000.
FROM lscr.io/linuxserver/webtop:ubuntu-xfce

# Fester Mirror statt download.php: dessen Mirror-Wahl lieferte auf oaap-demo 45-74 KB/s
# (35 Min ohne Ende), FAU liefert dort 9 MB/s (15.10.2026). Fällt der Mirror aus, steht
# die Weiterleitung als --build-arg ECLIPSE_URL=... zur Verfügung.
ARG ECLIPSE_URL="https://ftp.fau.de/eclipse/technology/epp/downloads/release/2026-06/R/eclipse-java-2026-06-R-linux-gtk-x86_64.tar.gz"
ARG ECLIPSE_RELEASE_SITE="https://download.eclipse.org/releases/2026-06"
ARG ADT_SITE="https://tools.hana.ondemand.com/latest"
ARG ABAP_CLEANER_SITE="https://sap.github.io/abap-cleaner/updatesite"
ARG NODE_MAJOR=22
ARG ARC1_VERSION=1.5.1

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Europe/Berlin

# ---------- Grundwerkzeuge ----------
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl wget ca-certificates gnupg unzip zip jq nano less htop tree \
      openssh-client python3 \
      xfce4-terminal fonts-dejavu fonts-noto-color-emoji fonts-liberation \
      libwebkit2gtk-4.1-0 libsecret-1-0 \
    && rm -rf /var/lib/apt/lists/*

# ---------- Firefox (Mozilla-Repo, kein Snap) ----------
RUN install -d -m 0755 /etc/apt/keyrings \
    && wget -qO- https://packages.mozilla.org/apt/repo-signing-key.gpg > /etc/apt/keyrings/packages.mozilla.org.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" > /etc/apt/sources.list.d/mozilla.list \
    && printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla \
    && apt-get update && apt-get install -y --no-install-recommends firefox firefox-l10n-de \
    && rm -rf /var/lib/apt/lists/*

# ---------- VS Code (Microsoft-Repo) ----------
RUN wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /etc/apt/keyrings/packages.microsoft.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" > /etc/apt/sources.list.d/vscode.list \
    && apt-get update && apt-get install -y --no-install-recommends code \
    && rm -rf /var/lib/apt/lists/*

# ---------- Node LTS + Claude Code ----------
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# ---------- Eclipse + ADT (headless per p2-Director) ----------
# Die ADT-Feature-IUs kommen dynamisch aus dem SAP-Update-Site. Passt die Auswahl
# nicht: docker run --rm <image> /opt/eclipse/eclipse -nosplash \
#   -application org.eclipse.equinox.p2.director -repository $ADT_SITE -list
# Eclipse-Release muss zu ADT passen (ADT 3.60 braucht org.eclipse.core.resources >= 3.23.200).
RUN curl -fsSL "$ECLIPSE_URL" -o /tmp/eclipse.tgz \
    && tar -xzf /tmp/eclipse.tgz -C /opt && rm /tmp/eclipse.tgz \
    && ADT_IUS=$(/opt/eclipse/eclipse -nosplash -consolelog \
         -application org.eclipse.equinox.p2.director \
         -repository "$ADT_SITE" -list 2>/dev/null \
         | grep -oE '^com\.sap\.adt[^=]*\.feature\.group' \
         | grep -viE 'hana|source' | sort -u | paste -sd, -) \
    && echo "Installiere ADT-Features: $ADT_IUS" \
    && test -n "$ADT_IUS" \
    && /opt/eclipse/eclipse -nosplash -consolelog \
         -application org.eclipse.equinox.p2.director \
         -repository "$ECLIPSE_RELEASE_SITE,$ADT_SITE" \
         -installIU "$ADT_IUS" \
    && printf -- '-Dosgi.instance.area.default=@user.home/workspace\n' >> /opt/eclipse/eclipse.ini \
    && chmod -R a+rX /opt/eclipse \
    && ln -sf /opt/eclipse/eclipse /usr/local/bin/eclipse

# ---------- Feinschliff Eclipse / VS Code (eigene Schicht, ADT-Schritt bleibt im Cache) ----------
# Eclipse: kein Sponsor-Browserfenster (Oomph), ABAP-Perspektive als Standard.
# VS Code: ohne Chromium-Sandbox — die braucht User-Namespaces, die Ubuntu-24.04-Wirte
# Containern verweigern; isoliert ist der Container selbst (Instanznetz, RFC-0016).
RUN printf -- '-Dorg.eclipse.oomph.setup.donate=false\n' >> /opt/eclipse/eclipse.ini \
    && sed -i 's|^org.eclipse.ui/defaultPerspectiveId=.*|org.eclipse.ui/defaultPerspectiveId=com.sap.adt.ui.AbapPerspective|' \
         /opt/eclipse/plugins/org.eclipse.epp.package.java_*/plugin_customization.ini \
    && sed -i 's|/usr/share/code/code|/usr/local/bin/code|' /usr/share/applications/com.microsoft.VSCode*.desktop

# ---------- Claude Desktop (apt-Repo von Anthropic, Linux-Beta) ----------
# gnome-keyring: dort speichert die App die Anmeldung (Schlüsselbund per Autostart entsperrt).
RUN curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc https://downloads.claude.ai/claude-desktop/key.asc \
    && gpg --show-keys /usr/share/keyrings/claude-desktop-archive-keyring.asc | grep -q 31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main" > /etc/apt/sources.list.d/claude-desktop.list \
    && apt-get update && apt-get install -y --no-install-recommends claude-desktop gnome-keyring \
    && rm -rf /var/lib/apt/lists/* \
    && sed -i 's|^Exec=claude-desktop|Exec=/usr/local/bin/claude-desktop|' /usr/share/applications/com.anthropic.Claude.desktop

# ---------- VS Code: Claude-Code-Erweiterung, deutsches Sprachpaket ----------
# Landet beim ersten Start per 50-oaap-init in ~/.vscode/extensions.
COPY vscode/sprachpaket.js /tmp/sprachpaket.js
RUN /usr/bin/code --no-sandbox --user-data-dir /tmp/vscode-build \
         --extensions-dir /defaults/oaap-vscode-extensions \
         --install-extension anthropic.claude-code \
         --install-extension MS-CEINTL.vscode-language-pack-de \
    && rm -rf /tmp/vscode-build \
    && node /tmp/sprachpaket.js /defaults/oaap-vscode-extensions /config/.vscode/extensions /defaults/oaap-vscode-languagepacks.json \
    && ls /defaults/oaap-vscode-extensions | grep -q '^anthropic.claude-code' \
    && chmod -R a+rX /defaults/oaap-vscode-extensions

# ---------- ABAP Cleaner (SAP, Eclipse-Plugin) ----------
RUN /opt/eclipse/eclipse -nosplash -consolelog \
         -application org.eclipse.equinox.p2.director \
         -repository "$ECLIPSE_RELEASE_SITE,$ADT_SITE,$ABAP_CLEANER_SITE" \
         -installIU com.sap.adt.abapcleaner.feature.feature.group \
    && ls /opt/eclipse/plugins | grep -q '^com.sap.adt.abapcleaner' \
    && chmod -R a+rX /opt/eclipse

# ---------- Google Chrome (Google-Repo) ----------
# Ohne Sandbox wie VS Code (bin/google-chrome). Das Paket legt seine Quelle selbst an.
RUN wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor > /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/cron.daily/google-chrome \
    && rm -f /etc/apt/sources.list.d/google-chrome.list /etc/apt/keyrings/google-chrome.gpg \
    && test -f /etc/apt/sources.list.d/google-chrome.sources \
    && sed -i 's|/usr/bin/google-chrome-stable|/usr/local/bin/google-chrome|' /usr/share/applications/google-chrome.desktop

# ---------- Helles Erscheinungsbild, Tasten für Eclipse, Firefox deutsch ----------
# Strg+F1…F12 wechseln in XFCE die Arbeitsfläche — ADT braucht sie (Strg+F3 = Aktivieren).
# Selkies-Seitenleiste: ohne gespeicherte Wahl hell statt dunkel.
COPY firefox/policies.json /etc/firefox/policies/policies.json
RUN sed -i -e 's/Greybird-dark/Greybird/' -e 's/elementary-xfce-dark/elementary-xfce/' \
         /defaults/xfce/xsettings.xml /defaults/xfce/xfwm4.xml \
    && sed -i 's/name="dark-mode" type="bool" value="true"/name="dark-mode" type="bool" value="false"/' /defaults/xfce/xfce4-panel.xml \
    && sed -i '/&lt;Primary&gt;F[0-9]*" type="string" value="workspace_[0-9]*_key"/d' \
         /etc/xdg/xfce4/xfconf/xfce-perchannel-xml/xfce4-keyboard-shortcuts.xml \
    && for f in /usr/share/selkies/*/assets/index-*.js; do \
         sed -i 's/localStorage.getItem(`theme`)||`dark`/localStorage.getItem(`theme`)||`light`/' "$f"; done \
    && chmod a+r /etc/firefox/policies/policies.json

# ---------- ARC-1: SAP-ADT-MCP-Server für Claude ----------
# Global installiert, eingetragen nur im Übungsprojekt (projekte/abap-exercises/.mcp.json).
RUN npm install -g "arc-1@${ARC1_VERSION}" --no-fund --no-audit \
    && npm cache clean --force \
    && arc1 --version

# ---------- goose (Block) und opencode (SST) — Anforderung Jörg, 15.10.2026 ----------
# Gemessen beim ersten Bau (15.10.2026): goose liegt seit v1.5x unter aaif-goose/goose
# (github.com/block/goose leitet um), das .deb installiert die Electron-App als
# /usr/lib/goose/Goose; die CLI steckt in resources/bin. Versionen als Build-Args,
# damit nichts still wandert. Start ohne Sandbox über bin/goose-desktop.
# opencode: CLI/TUI per npm `opencode-ai`; eine Linux-Desktop-Fassung ist nicht eingebaut.
ARG GOOSE_DEB_URL="https://github.com/aaif-goose/goose/releases/download/v1.54.0/goose_1.54.0_amd64.deb"
ARG OPENCODE_VERSION="1.19.0"
RUN if [ -n "$GOOSE_DEB_URL" ]; then \
      curl -fsSL "$GOOSE_DEB_URL" -o /tmp/goose.deb \
      && apt-get update && apt-get install -y --no-install-recommends /tmp/goose.deb \
      && rm -rf /var/lib/apt/lists/* /tmp/goose.deb \
      && test -x /usr/lib/goose/Goose \
      && ln -sf /usr/lib/goose/resources/bin/goose /usr/local/bin/goose-cli ; \
    else echo "goose: GOOSE_DEB_URL leer, übersprungen (Entwurf)"; fi
RUN if [ -n "$OPENCODE_VERSION" ]; then \
      npm install -g "opencode-ai@${OPENCODE_VERSION}" --no-fund --no-audit \
      && npm cache clean --force && opencode --version ; \
    else echo "opencode: OPENCODE_VERSION leer, übersprungen (Entwurf)"; fi

# ---------- Desktop-Einträge, Skeleton, Init-Skript ----------
# Standardbrowser Chrome an allen drei Stellen, die Programme abfragen (mimeapps.list,
# helpers.rc, Alternative x-www-browser) — jeweils über bin/google-chrome ohne Sandbox.
COPY desktop/*.desktop /usr/share/applications/
COPY skel/ /defaults/oaap-skel/
COPY projekte/ /defaults/oaap-projekte/
COPY init/50-oaap-init /custom-cont-init.d/50-oaap-init
COPY bin/ /usr/local/bin/
RUN chmod +x /custom-cont-init.d/50-oaap-init /usr/local/bin/code /usr/local/bin/claude-desktop \
         /usr/local/bin/google-chrome /usr/local/bin/oaap-keyring /usr/local/bin/oaap-arc1 \
         /usr/local/bin/goose-desktop \
    && chmod -R a+rX /defaults/oaap-skel /defaults/oaap-projekte \
    && sed -i 's/^WebBrowser=.*/WebBrowser=google-chrome/' /etc/xdg/xfce4/helpers.rc \
    && for a in x-www-browser gnome-www-browser; do \
         update-alternatives --install /usr/bin/$a $a /usr/local/bin/google-chrome 300 \
         && update-alternatives --set $a /usr/local/bin/google-chrome; done

# Ausgangswerte, per Instanzkonfiguration überschreibbar (oaap-app.yaml). Keine
# Passwörter: CUSTOM_USER/PASSWORD bleiben leer, das Gateway ist die Tür.
# LANG steht bewusst am Ende, damit die Eclipse/ADT-Schichten im Cache bleiben.
ENV LANG="de_DE.UTF-8" \
    LANGUAGE="de_DE:de" \
    TITLE="OAAP Desktop" \
    SELKIES_VIDEO_FULLCOLOR=true \
    SELKIES_VIDEO_STREAMING_MODE=false \
    SELKIES_VIDEO_PAINTOVER_CRF=14 \
    SELKIES_VIDEO_CRF="22,15-28" \
    SELKIES_FRAMERATE="30,15-60" \
    DESKTOP_SUDO="ja" \
    KURS_REPO="" \
    ABAP_HOST="" \
    ABAP_PORT="44300" \
    ABAP_CLIENT="001" \
    ABAP_INSECURE="false"
