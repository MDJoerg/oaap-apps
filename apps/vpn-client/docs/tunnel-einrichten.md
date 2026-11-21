# Tunnel einrichten

So richten Sie den VPN-Client im Portal ein.

1. **Installieren.** Im Store den VPN-Client installieren. Das Recht auf ein
   Tunnelgerät kann nur der Betreiber des Servers bestätigen – dafür setzt er
   beim Installieren ein Häkchen.
2. **Datei hochladen.** Auf der Seite der Instanz unter **Konfiguration** die
   `.ovpn`-Datei hochladen. Das Portal speichert sie verdeckt.
3. **Zugang eintragen**, falls die Gegenstelle Benutzer und Passwort verlangt:
   `benutzer:passwort`.
4. **Ziele eintragen**, eines je Zeile, siehe
   [Ziele und Destinationen](ziele-und-destinationen.md).
5. **Neu starten** und auf der Seite der App nachsehen, ob der Tunnel
   „verbunden“ zeigt.

## An der Kommandozeile (Betreiber)

```bash
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/vpn-client \
     --name vpn --confirm-tun
sudo oaap app config set vpn VPN_CONFIG "$(base64 -w0 client.ovpn)"
sudo oaap app config set vpn VPN_AUTH          # benutzer:passwort, verdeckt abgefragt
sudo oaap app config set vpn VPN_TARGETS "gaming = http://10.8.0.5:11434"
sudo oaap app restart vpn
```
