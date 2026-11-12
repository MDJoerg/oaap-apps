# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) -- deshalb
# ein Multi-Arch-Basisimage. Eine Abhängigkeit: PyYAML, um die Manifeste
# fremder Pakete so zu lesen, wie der Knoten sie liest (wie der Paketkatalog).
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "PyYAML==6.0.3" "segno==1.6.6"

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin market

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py db.py access.py blobs.py zipinfo.py multipart.py \
     views.py render.py images.py i18n.py profileinfo.py secretguard.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
