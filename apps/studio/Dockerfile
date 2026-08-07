# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage und keine Abhängigkeiten, die kompiliert
# werden müssten. Das Studio kommt mit der Python-Standardbibliothek aus.
FROM python:3.13-slim

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin studio

WORKDIR /srv
COPY app.py /srv/app.py

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
