# Nur Standardbibliothek; Multi-Arch-Basis (ADR-0005).
FROM python:3.13-slim
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
