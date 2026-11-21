# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- Multi-Arch-Basisimage.
# Nur Standardbibliothek.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin recherche

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py fetch.py search.py mcp_door.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
