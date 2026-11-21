# Den Katalog als Store-Quelle eintragen

Der Knoten liest die freigegebenen Pakete **direkt aus dem Speicher** dieser
Instanz – ohne Netz und ohne Schlüssel. Das wird einmal eingerichtet, auf dem
Knoten:

```sh
sudo oaap store add-catalog <Name dieser Instanz>
```

Danach erscheinen die freigegebenen Apps im Store jedes
Mandanten-Administrators. Installieren und Aktualisieren gehen wie bei jeder
anderen Quelle.

Der Speicher der Instanz heißt `data` und darf nicht umbenannt werden: Der
Knoten findet die Liste und die Pakete genau dort.
