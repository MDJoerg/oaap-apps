# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- deshalb
# ein Multi-Arch-Basisimage. Eine Abhängigkeit: PyYAML, um die Manifeste
# fremder Pakete so zu lesen, wie der Knoten sie liest (wie der Paketkatalog).
FROM python:3.13-slim

RUN pip install --no-cache-dir "PyYAML==6.*"

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin market

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py db.py access.py blobs.py zipinfo.py multipart.py \
     views.py render.py images.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
