# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage. Nur Standardbibliothek: Das Gateway spricht
# MCP über HTTP und führt eine SQLite-Datei; beides kann Python von Haus
# aus. Kein fremder Code heißt auch: Die Zusage „in keinem Protokoll
# stehen Argumente oder Ergebnisse" (Spec §10) ist an diesem Baum
# vollständig nachprüfbar.
FROM python:3.13-slim

# Fester numerischer Benutzer: die Plattform macht die deklarierten
# Mounts für genau diese UID beschreibbar (Contract-Garantie 4).
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin mcpgw

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen, sonst startet der Container
# nicht (die Dateien werden einzeln aufgezählt, nicht per COPY . kopiert).
# test_compose.py prüft das — am 2026-11-02 fehlte compose.py, und erst
# der Knoten merkte es.
COPY app.py books.py compose.py upstream.py guard.py argcheck.py signin.py /srv/
# Die gemeinsamen Bücher der Gateways (RFC-0065 Stufe 0). Eine Kopie von
# oaap-apps/lib/gwcore — geändert wird dort, check-shared.py gleicht ab.
COPY gwcore/ /srv/gwcore/

USER 10001
EXPOSE 8000
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
