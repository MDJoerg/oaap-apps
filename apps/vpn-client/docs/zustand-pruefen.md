# Zustand prüfen

Die Seite der App zeigt in vier Karten:

- **Tunnel** – verbunden, verbindet, getrennt, nicht eingerichtet; seit wann, die
  Gegenstelle, die eigene Adresse im Tunnel, die Zahl der Versuche und den letzten
  Fehler.
- **Ziele** – je Ziel, ob es antwortet und seit wann. Die App prüft alle
  30 Sekunden, solange der Tunnel steht. Steht er nicht, zeigt sie keine alten
  Ergebnisse.
- **Was die Gegenseite nicht darf** – ob der Paketfilter steht und was aus der
  Datei verworfen wurde.
- **Protokoll** – die letzten Zeilen von OpenVPN.

## Den Assistenten fragen

Ein Assistent des Mandanten kann den Zustand über die MCP-Tür der App lesen
(Werkzeug `vpn_state`). Er sieht nur Namen und „antwortet / antwortet nicht“ –
keine Adressen, keine Zugangsdaten, nichts aus der Datei. Das geht nur für
Administratoren, wie die Seite.
