# Build laeuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- deshalb
# ein Multi-Arch-Basisimage. Nur Standardbibliothek: diese App spricht
# HTTP mit oaap.data.twin ueber das Gateway, sonst nichts.
FROM python:3.13-slim

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts fuer genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mitarbeiterui

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen, sonst startet der Container
# nicht (die Dateien werden einzeln aufgezaehlt, nicht per COPY . kopiert).
COPY app.py twin.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
