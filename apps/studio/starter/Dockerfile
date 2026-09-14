# Gebaut wird auf dem Zielknoten — und der ist mal amd64, mal arm64
# (Raspberry Pi). Deshalb ein Multi-Arch-Basisimage und nichts, was für
# eine bestimmte Architektur übersetzt werden müsste.
FROM python:3.13-slim

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
