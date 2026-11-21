# Häufige Fragen

**Warum muss ich das Gespräch auf rot stellen?**
Weil Ihre Suchwörter an einen externen Suchdienst gehen und gelesene Seiten den Abruf sehen.
Rot heißt: Daten verlassen den Mandanten. Das soll bewusst geschehen.

**Speichert die App, wonach ich gesucht habe?**
Nein. Gezählt wird nur, wie oft jedes Werkzeug am Tag benutzt oder abgelehnt wurde – ohne
Suchwort, ohne Adresse, ohne Inhalt. Nach einem Neustart ist auch die Zählung weg.

**Der Assistent sagt, eine Seite könne nicht gelesen werden. Warum?**
Die App liest nur http und https auf den üblichen Ports, nur Text, HTML und JSON bis 2 MB,
keine Seiten im eigenen Netz und keine gesperrten Domains. Die Antwort nennt den Grund in
einem Satz.

**Kann die App PDF-Dateien lesen?**
Noch nicht.

**Warum gibt es nur die Suche, aber kein Lesen von Seiten?**
Entweder hat Ihr Administrator `web_fetch` ausgeschaltet, oder Ihr Mandant ist geschlossen
und erreicht das Internet nur über freigegebene Ziele.

**„Suchdienst nicht zugeordnet“ – was tun?**
Der Administrator ordnet der App eine Destination `search` zu, siehe
[Recherche einrichten](einrichten.md).
