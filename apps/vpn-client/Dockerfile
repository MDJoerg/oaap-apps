# Build läuft auf dem Zielknoten (ADR-0005: amd64 und arm64) — deshalb
# ein Multi-Arch-Basisimage. OpenVPN, iproute2 und iptables aus Debian;
# der Dienst selbst ist nur Python-Standardbibliothek.
FROM python:3.13-slim

# Debians Sicherheitskorrekturen beim Bau einspielen (RFC-0071 §16): Das offizielle
# Basis-Abbild erscheint oft erst Tage nach Debians Korrektur neu. Mit dieser Zeile
# heilt "Neu bauen" solche Meldungen; der Knoten erkennt sie und baut ohne
# Zwischenspeicher (ab Plattform 0.1.252).
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get -y upgrade \
    && rm -rf /var/lib/apt/lists/*

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
