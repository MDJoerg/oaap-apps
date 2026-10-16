# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage. OpenVPN, iproute2 und iptables aus Debian;
# der Dienst selbst ist nur Python-Standardbibliothek.
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        openvpn iproute2 iptables \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
# ACHTUNG: Jedes neue Modul muss hier stehen (einzeln aufgezählt).
COPY app.py vpnconf.py /srv/

# Kein USER: OpenVPN richtet das Tunnelgerät ein und setzt den Paketfilter.
# Mehr als /dev/net/tun und NET_ADMIN hat der Container nicht (Laufzeit 2.23).
EXPOSE 8080
ENV PYTHONUNBUFFERED=1
CMD ["python3", "/srv/app.py"]
