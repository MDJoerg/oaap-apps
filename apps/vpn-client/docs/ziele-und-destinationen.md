# Ziele und Destinationen

Ein **Ziel** ist ein Dienst hinter dem Tunnel, den eine andere App benutzen soll.

## Ziele eintragen

In der Konfiguration unter „Ziele hinter dem Tunnel“, eines je Zeile:

```text
gaming = http://10.8.0.5:11434
nas = https://10.8.0.7/api
```

- Der Name links besteht aus Kleinbuchstaben, Ziffern und Bindestrichen.
- Rechts steht die Adresse im VPN mit Port. Nur `http` und `https`, keine
  Zugangsdaten in der Adresse.
- Für jede IP-Adresse legt die App genau eine Route in den Tunnel. Ein Ziel mit
  einem Namen statt einer IP bekommt keine eigene Route.

## Ein Ziel benutzen

Andere Apps erreichen ein Ziel nur über eine **Destination** der Plattform. Unter
**Destinationen** im Portal schlägt das Formular die Ziele als
`through:<instanz>/<ziel>` vor. An der Kommandozeile:

```bash
sudo oaap destination add gaming --target through:vpn/gaming --auth bearer --secret-stdin
sudo oaap destination bind <verbraucher-instanz> gaming
```

Den Schlüssel des Ziels trägt die Destination, nicht der VPN-Client. Wer die App
direkt über ihre Anmeldung aufruft, bekommt die Ziele nicht zu sehen.
