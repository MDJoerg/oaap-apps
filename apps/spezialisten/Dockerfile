# Nur Standardbibliothek; Multi-Arch-Basis (ADR-0005).
FROM python:3.13-slim
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*
# Fester numerischer Benutzer: die Plattform macht /data für genau diese UID beschreibbar.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin spez
WORKDIR /srv
# ACHTUNG: jedes neue Modul einzeln hier nennen.
COPY app.py runner.py store.py /srv/
COPY appframe/ /srv/appframe/
COPY mcpdoor/ /srv/mcpdoor/
USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
