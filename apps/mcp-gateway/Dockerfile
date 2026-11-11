# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage. Nur Standardbibliothek: Das Gateway spricht
# MCP über HTTP und führt eine SQLite-Datei; beides kann Python von Haus
# aus. Kein fremder Code heißt auch: Die Zusage „in keinem Protokoll
# stehen Argumente oder Ergebnisse" (Spec §10) ist an diesem Baum
# vollständig nachprüfbar.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mcpgw

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen, sonst startet der Container
# nicht (die Dateien werden einzeln aufgezählt, nicht per COPY . kopiert).
# test_compose.py prüft das — am 2026-11-02 fehlte compose.py, und erst
# der Knoten merkte es.
COPY app.py books.py compose.py upstream.py guard.py argcheck.py signin.py /srv/
# Die gemeinsamen Bücher der Gateways (RFC-0065 Stufe 0). Eine Kopie von
# oaap-apps/lib/gwcore — geändert wird dort, check-shared.py gleicht ab.
COPY gwcore/ /srv/gwcore/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
