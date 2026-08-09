# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage.
#
# Eine einzige Abhängigkeit: PyYAML. Das Studio kommt mit der
# Standardbibliothek aus, hier geht das nicht — dieses Werkzeug liest
# FREMDE Manifeste, und ein selbstgebauter YAML-Leser, der eine
# Schreibweise missversteht, würde genau die Art stiller Falschaussage
# erzeugen, gegen die der Editor antritt. PyYAML braucht keinen
# Übersetzer: Ohne libyaml fällt es auf reines Python zurück, der Build
# auf arm64 hat also nichts zu kompilieren.
FROM python:3.13-slim

RUN pip install --no-cache-dir "PyYAML==6.*"

# Fester numerischer Benutzer: die Plattform macht deklarierte Mounts
# für genau diese UID beschreibbar (Contract-Garantie 4). Dieser
# Bauschritt schreibt zwar nichts und deklariert deshalb auch keinen
# Mount — der Benutzer bleibt trotzdem, weil root im Container nichts
# zu suchen hat.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin editor

WORKDIR /srv
COPY app.py checker.py /srv/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
