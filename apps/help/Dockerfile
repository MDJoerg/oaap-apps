# Build laeuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- deshalb
# ein Multi-Arch-Basisimage. Nur Standardbibliothek; SQLite (mit FTS5) ist dabei.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts fuer genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin help

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py library.py index.py sources.py render.py mcp_door.py \
     guide_requests.py maint_door.py /srv/
# Die Doku im Paket (seed): antwortet bis zum ersten Abruf, und ohne Destination.
COPY seed/ /srv/seed/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
