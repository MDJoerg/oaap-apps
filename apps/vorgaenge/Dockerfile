# Build laeuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- deshalb
# ein Multi-Arch-Basisimage. Nur Standardbibliothek; SQLite ist dabei.
FROM python:3.13-slim

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts fuer genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin vorgaenge

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py store.py authz_client.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
