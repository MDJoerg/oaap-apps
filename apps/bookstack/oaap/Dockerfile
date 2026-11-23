# Der Dienst `oaap` im Paket bookstack: Einrichtungstür und Keim des Adapters
# (RFC-0110 §3). Multi-Arch-Basis (ADR-0005: amd64 und arm64).
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau (RFC-0071 §16).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

# Zwei Abhängigkeiten, exakt festgelegt (care.rebuild: self): MariaDB sprechen
# (PyMySQL, reines Python) und bcrypt für BookStacks Token-Geheimnisse.
COPY requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin oaap

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln, nicht COPY .).
COPY app.py wiki.py setup_door.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
