# Die Verwaltungsoberfläche öffnen

In der Verwaltungsoberfläche von Keycloak pflegst Du Benutzer, Gruppen und
Einstellungen eines Realms.

## So kommst Du hinein

1. Öffne im Portal die Instanz des Anmeldedienstes und dort die
   Verwaltung – oder direkt `https://auth.<knoten>/admin/`.
2. Zuerst meldest Du Dich bei der **Plattform** an. Die Oberfläche
   verlangt die Rolle `admin` im Mandanten dieser Instanz.
3. Danach meldest Du Dich bei **Keycloak** an – mit einem Verwalterkonto
   des Realms.

Du meldest Dich also zweimal an. Das ist Absicht: Die Anmeldeseiten selbst
müssen öffentlich bleiben (Keycloak *ist* die Anmeldung), die
Verwaltungsoberfläche braucht aber niemand von außen.

## Was Du dort tust – und was nicht

- **Benutzer und Gruppen pflegen**: ja, das ist Sache des Realms.
- **Rollen der Plattform vergeben**: nein. Rollen und Rechte vergibt die
  Verwaltung des Mandanten in der Plattform. Eine Gruppe aus Keycloak wirkt
  nur, wenn der Betrieb sie einer Sichtbarkeitsgruppe zuordnet.
- **Einen Realm löschen**: nur mit großer Vorsicht und nie für einen
  laufenden Mandanten – mit dem Realm verschwinden alle Konten darin. Die
  Plattform selbst löscht nie einen Realm.
