# Nur Standardbibliothek; Multi-Arch-Basis (ADR-0005).
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*
# Fester numerischer Benutzer: die Plattform macht /data fuer genau diese UID beschreibbar.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin chat
WORKDIR /srv
# ACHTUNG: jedes neue Modul einzeln hier nennen.
COPY app.py loop.py assistants.py store.py authz_client.py page.html /srv/
COPY static/ /srv/static/
USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
