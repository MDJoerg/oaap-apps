# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage.
#
# Eine Abhängigkeit: PyYAML, aus demselben Grund wie beim Studio — hier
# werden fremde Manifeste gelesen, und ein selbstgebauter YAML-Leser würde
# ein Paket für lesbar erklären, das der Knoten anders versteht.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "PyYAML==6.0.3"

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin catalog

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen, sonst startet der Container
# nicht (die Dateien werden einzeln aufgezählt, nicht per COPY . kopiert).
COPY app.py catalog.py zipinfo.py multipart.py upstream.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
