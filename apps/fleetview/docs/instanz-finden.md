# Eine Instanz finden

Der Reiter **Instanzen** listet alle App-Instanzen der ganzen Flotte.

- Suchen Sie nach dem Namen der Instanz, der App oder des Knotens.
- Filtern Sie nach **Zustand** (z. B. nur gestörte) oder **Kanal** (Produktiv
  oder Test).
- Ein Klick auf den Knoten öffnet dessen Seite.

Ab 50 Einträgen blättern Sie mit **Weitere 50**.

## Den Assistenten fragen

Ein Assistent des Mandanten kann die Flotte über die MCP-Tür der App lesen, mit
denselben Rechten wie die Seite (nur `admin` und `support`):

- `fleetview_nodes` – die Knoten mit Zustand und was einen Menschen braucht,
- `fleetview_node_read` – ein Knoten mit seinen Instanzen,
- `fleetview_instances` – die Instanzen der Flotte, durchsuchbar.

Fragen Sie zum Beispiel: „Wie steht die Flotte?“ oder „Wo läuft die App chat?“
