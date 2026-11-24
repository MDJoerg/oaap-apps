# Einrichten

## 1. Installieren und die Geheimnisse setzen

Das Paket bringt drei Container mit: das Wiki, seine Datenbank (MariaDB)
und einen kleinen Dienst `oaap`, der das Wiki für die Plattform einrichtet.

Drei Werte erzeugt das Portal beim Installieren:
`OAAP_WIKI_APP_SECRET`, `MARIADB_PASSWORD` und `OAAP_WIKI_ADMIN_PASSWORD`.
Wer über die Kommandozeile installiert, setzt sie danach selbst, je Wert:

```sh
openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n' \
  | sudo oaap app config set <instanz> MARIADB_PASSWORD
```

Ohne `MARIADB_PASSWORD` startet die Datenbank bewusst nicht. Sonst würde
sie sich ohne Kennwort einrichten, und ein später gesetztes greift nie mehr.
Das Protokoll sagt das. Wird eine Einrichtung unterbrochen, z. B. weil
zwei Werte kurz nacheinander gespeichert werden, beginnt sie beim nächsten
Start von vorn. Dabei geht nichts verloren, denn bis sie fertig ist, hat
niemand Zugriff.

## 2. Den Anmeldedienst anbinden

Im Portal: Seite der Instanz, Karte **„Anmeldung über den Realm“**, Knopf
„Anmeldung über den Realm verbinden“. Das dürfen der Betreiber und die
Verwaltung des Mandanten. Auf der Kommandozeile:

```sh
sudo oaap app idp app <anbieter> --instance <instanz>
```

legt im Realm des Mandanten einen Client für das Wiki an, mit Gruppen im
Token, und gibt der Instanz Kennung und Geheimnis. Ab dann meldet das Wiki
nur noch über den Anmeldedienst an. Das lokale Konto `admin@admin.com`
ist dann ohne Wirkung.

Ohne Client meldet sich nur dieses lokale Konto an, mit dem Kennwort aus
`OAAP_WIKI_ADMIN_PASSWORD`. Das bekannte Standardkennwort von BookStack
ersetzt der Dienst `oaap` beim ersten Start.

## 3. Administratoren

`OAAP_WIKI_ADMIN_GROUP` nennt die Gruppe des Realms, deren Mitglieder im
Wiki Administratoren sind, als vollen Pfad (z. B. `/wiki-admins`). Der
Dienst trägt sie bei jedem Start an der Rolle „Admin“ ein. Leer lässt er
die Rolle, wie sie ist.

## 4. Rollen für Gruppen

Eine Rolle je Gruppe kann man im Wiki von Hand anlegen: Einstellungen →
Rollen, Feld „Externe Authentifizierungs-ID“ = Gruppenpfad.

Oder der Aufbau des Mandanten legt sie über die **Einrichtungstür** an
(RFC-0104), ein Eintrag je Gruppe:

```json
{"kind": "role", "id": "/wiki-redaktion", "title": "Redaktion", "like": "editor"}
```

`like` ist `viewer` (Vorgabe, nur lesen) oder `editor` (lesen und
schreiben). Die neue Rolle bekommt die Rechte von BookStacks Rolle
„Viewer“ bzw. „Editor“. Eine Rolle, die es für die Gruppe schon gibt,
lässt die Tür unverändert. Entfernen nimmt nur eine Rolle weg, die noch so
ist, wie die Tür sie angelegt hat.

## 5. Als Ablage für den Chat

Damit Personen das Wiki im Chat als Teil eines Arbeitsraums nutzen, drückt
die Verwaltung auf der Seite der Instanz, Karte „MCP“, den Knopf **„Für den
Chat freigeben“**. Das MCP-Gateway des Mandanten kennt das Wiki danach als
Ablage. Jede Person hängt es im Chat selbst in einen Arbeitsraum ein und
sieht dort nur, was sie im Wiki sehen darf.

Ein Aufbau aus dem Profil `ki-arbeitsplatz` mit den Haken „mit eigenem
Anmeldedienst“ und „mit Wiki“ erledigt die Schritte 1 bis 5 von selbst:
Die Verwaltergruppe ist im Wiki Administrator, und es gibt die Rollen
„Redaktion“ (`/wiki-redaktion`) und „Lesen“ (`/wiki-lesen`). Die beiden
Gruppen legt die Verwaltung im Realm an und nimmt die Personen hinein.

## Zustand ansehen

`/oaap/status` auf der Adresse des Wikis zeigt der Verwaltung des
Mandanten, ob der Dienst `oaap` bereit ist: BookStack-Fassung, Prüfung der
Tabellen, Admin-Gruppe, Standardkennwort. Die Seite zeigt nie ein
Geheimnis.
