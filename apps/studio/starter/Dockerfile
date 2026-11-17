# Gebaut wird auf dem Zielknoten — und der ist mal amd64, mal arm64
# (Raspberry Pi). Deshalb ein Multi-Arch-Basisimage und nichts, was für
# eine bestimmte Architektur übersetzt werden müsste.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16). Das
# offizielle Basisimage erscheint oft erst Tage nach Debians Korrektur
# neu; mit dieser Zeile heilt ein „Neu bauen“ auf dem Knoten solche
# Meldungen. Sie gehört zum Versprechen `care.rebuild: self` im Manifest.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

# Abhängigkeiten immer fest pinnen (pip install "paket==1.2.3"), sonst
# ändert ein Neubau mehr als die Basis. Der Starter braucht keine.

# Fester numerischer Benutzer: Die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar. Läuft der Container als root
# oder unter einer anderen UID, ist /data plötzlich nicht mehr
# beschreibbar — ein Fehler, der erst im Betrieb auffällt.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin starter

WORKDIR /srv
COPY app.py /srv/

USER 10001
EXPOSE 8000
# Ungepuffert, sonst kommen die Logzeilen verspätet oder gar nicht an.
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
