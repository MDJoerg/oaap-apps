#!/bin/bash
# Bereitet das Zuhause des Teilnehmers vor und startet code-server hinter
# dem OAAP-Gateway. Läuft als Benutzer `coder` (uid 1000); das Zuhause
# ist ein Storage-Mount der Instanz und beim ersten Start leer.
set -u

HOME_DIR="${HOME:-/home/coder}"
SEED=/opt/oaap/seed
CS_DATA="$HOME_DIR/.local/share/code-server"
EXT_DIR="$CS_DATA/extensions"
USER_DIR="$CS_DATA/User"
PROJECTS="$HOME_DIR/projects"

log() { echo "[oaap-ide] $*"; }

mkdir -p "$EXT_DIR" "$USER_DIR" "$PROJECTS" "$HOME_DIR/.config/code-server" \
         "$HOME_DIR/material" 2>/dev/null || true

# --- Saat: vorinstallierte Erweiterungen ------------------------------
#
# Beim allerersten Start ist das Erweiterungsverzeichnis leer: dann wird
# die ganze Saat samt ihrer extensions.json übernommen. Später kommt nur
# nach, was fehlt — eine Erweiterung, die der Teilnehmer selbst entfernt
# oder aktualisiert hat, bleibt seine Entscheidung.
if [ -d "$SEED/extensions" ]; then
  if [ -z "$(ls -A "$EXT_DIR" 2>/dev/null)" ]; then
    cp -a "$SEED/extensions/." "$EXT_DIR/" && log "Erweiterungen aus der Saat übernommen (erster Start)."
  else
    for d in "$SEED"/extensions/*/; do
      [ -d "$d" ] || continue
      base="$(basename "$d")"
      # Kennung ohne Version: "anthropic.claude-code-2.1.285" -> "anthropic.claude-code"
      ident="${base%-*}"
      if ! ls -d "$EXT_DIR/$ident"-* >/dev/null 2>&1; then
        cp -a "$d" "$EXT_DIR/$base" && log "Erweiterung nachgetragen: $base"
      fi
    done
  fi
fi

# --- Saat: Einstellungen und Begrüßung, nur wenn noch nichts da ist -----
[ -f "$USER_DIR/settings.json" ] || cp "$SEED/settings.json" "$USER_DIR/settings.json" 2>/dev/null || true
[ -f "$PROJECTS/README.md" ] || cp "$SEED/README.md" "$PROJECTS/README.md" 2>/dev/null || true

# --- sudo: erlaubt, es sei denn der Betreiber sagt nein ----------------
#
# Das offizielle Abbild gibt `coder` passwortloses sudo. Die Regel steht
# in /etc/sudoers.d/nopasswd; sie zu entfernen ist das Letzte, wofür
# sudo hier gebraucht wird. Ein Konfigurationswechsel erzeugt den
# Container neu (Runtime 2.8), also wird das bei jedem Start frisch
# entschieden.
case "$(echo "${IDE_SUDO:-ja}" | tr '[:upper:]' '[:lower:]')" in
  nein|no|false|0|aus|off)
    if sudo -n true 2>/dev/null; then
      sudo -n rm -f /etc/sudoers.d/nopasswd && log "sudo abgeschaltet (IDE_SUDO=${IDE_SUDO})."
    fi
    ;;
esac

# --- Zusätzliche Erweiterungen (IDE_EXTENSIONS) ------------------------
#
# Eine Liste, auf dem Draht mit ';' getrennt (Runtime 2.8, multiline).
# Jeder Eintrag ist entweder eine Open-VSX-Kennung (`SAPSE.vscode-cds`)
# oder die https-Adresse einer .vsix-Datei — für Erweiterungen, die nicht
# auf Open VSX liegen, aber von ihrem Hersteller selbst verteilt werden
# (ABAP cleaner: GitHub-Release, Apache-2.0). Eine .vsix wird einmal
# geholt und installiert; Merkzettel je Adresse, damit ein Neustart sie
# nicht erneut lädt (131 MB beim ABAP cleaner).
#
# Best effort: Kein Netz, keine Erweiterung — aber der Arbeitsplatz
# startet trotzdem, und die Meldung steht im Container-Log.
VSIX_CACHE="$HOME_DIR/.cache/oaap-vsix"
if [ -n "${IDE_EXTENSIONS:-}" ]; then
  mkdir -p "$VSIX_CACHE"
  IFS=';' read -r -a wanted <<< "$IDE_EXTENSIONS"
  for ext in "${wanted[@]}"; do
    ext="$(echo "$ext" | xargs)"
    [ -n "$ext" ] || continue
    case "$ext" in
      https://*.vsix|https://*.vsix\?*)
        mark="$VSIX_CACHE/$(echo -n "$ext" | sha256sum | cut -c1-16).done"
        [ -f "$mark" ] && continue
        file="$VSIX_CACHE/$(basename "${ext%%\?*}")"
        log "Hole $ext ..."
        if curl -fsSL --retry 2 -o "$file" "$ext" \
           && code-server --extensions-dir "$EXT_DIR" --user-data-dir "$CS_DATA" \
                --install-extension "$file" >/tmp/oaap-ext.log 2>&1; then
          echo "$ext" > "$mark"; rm -f "$file"
          log "Erweiterung aus $file installiert."
        else
          log "WARNUNG: $ext konnte nicht geholt oder installiert werden:"; tail -n 5 /tmp/oaap-ext.log 2>/dev/null
          rm -f "$file"
        fi
        ;;
      *)
        lc="$(echo "$ext" | tr '[:upper:]' '[:lower:]')"
        if ls -d "$EXT_DIR/$lc"-* >/dev/null 2>&1; then
          continue
        fi
        log "Installiere Erweiterung $ext ..."
        if ! code-server --extensions-dir "$EXT_DIR" --user-data-dir "$CS_DATA" \
             --install-extension "$ext" >/tmp/oaap-ext.log 2>&1; then
          log "WARNUNG: $ext konnte nicht installiert werden:"; tail -n 5 /tmp/oaap-ext.log
        fi
        ;;
    esac
  done
fi

# --- Leere Geheimnisse sind keine Geheimnisse ---------------------------
#
# Die Plattform reicht jeden deklarierten Schlüssel als Variable durch;
# ein leerer ANTHROPIC_API_KEY würde von der Claude-CLI aber als
# gesetzt gelesen und die eigene Anmeldung verhindern.
[ -n "${ANTHROPIC_API_KEY:-}" ] || unset ANTHROPIC_API_KEY
[ -n "${ANTHROPIC_BASE_URL:-}" ] || unset ANTHROPIC_BASE_URL

# --- SAP-Systemliste für ADT (0.1.2) ------------------------------------
#
# ADT for VS Code kennt RFC-Systeme nur aus einer SAP-UI-Landschaftsdatei
# (das, was SAP GUI unter SAPUILandscape.xml pflegt). Ohne SAP GUI im
# Container liest der Sprachserver den Pfad aus der Umgebungsvariablen
# SAPLOGON_LSXML_FILE (gemessen am 30.09.2026 in seinen Klassen:
# SapUiLandscapeReader.OS_ENV_VAR_SAPLOGON_LSXML_FILE). Vorrang hat das
# Kursmaterial, dann das Zuhause — beides Orte, die eine Kohorte säen
# kann, ohne ein Abbild zu bauen.
if [ -z "${SAPLOGON_LSXML_FILE:-}" ]; then
  for cand in "$HOME_DIR/material/SAPUILandscape.xml" "$HOME_DIR/.adtls/SAPUILandscape.xml"; do
    if [ -f "$cand" ]; then
      export SAPLOGON_LSXML_FILE="$cand"
      log "SAP-Systemliste für ADT: $cand"
      break
    fi
  done
fi

# --- Start -------------------------------------------------------------
#
# `--auth none`: die Anmeldung ist das Gateway. code-server selbst hätte
# nur ein zweites Passwort, das jeder Teilnehmer zusätzlich bräuchte.
# Der Original-Entrypoint kümmert sich um fixuid, ~/entrypoint.d und
# dumb-init.
exec /usr/bin/entrypoint.sh \
  --bind-addr 0.0.0.0:8080 \
  --auth none \
  --disable-telemetry \
  --disable-update-check \
  --disable-workspace-trust \
  --app-name "OAAP IDE" \
  "$PROJECTS"
