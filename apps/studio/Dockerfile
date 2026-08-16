# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage.
#
# Eine einzige Abhängigkeit: PyYAML. Bis 0.1 kam das Studio mit der
# Standardbibliothek aus, weil es keine fremden Dateien las. Seit 0.2
# liest es Manifeste aus hochgeladenen Paketen — und ein selbstgebauter
# YAML-Leser, der eine Schreibweise missversteht, würde ein Paket für
# gut erklären, das der Knoten später ablehnt. Dieselbe Begründung wie
# beim Store Editor. PyYAML braucht keinen Übersetzer: Ohne libyaml
# fällt es auf reines Python zurück, der Bau auf arm64 hat also nichts
# zu kompilieren.
FROM python:3.13-slim

RUN pip install --no-cache-dir "PyYAML==6.*"

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin studio

WORKDIR /srv
COPY app.py pkg.py deployer.py multipart.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
