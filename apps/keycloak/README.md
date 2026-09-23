# Keycloak als OAAP-App — ein Realm je Mandant

Der Anmeldedienst aus **RFC-0041**. Keycloak beantwortet *wer jemand
ist*, OAAP beantwortet *was jemand darf*. Die Apps auf dem Knoten
merken von alledem nichts: Sie bekommen weiterhin nur die fünf
`X-OAAP-*`-Kopfzeilen, und der erste Satz des Deployment Contract —
*eine App baut nie eine Anmeldung* — bleibt unverändert.

## Warum das eine gewöhnliche App ist

Weil sie dann aktualisiert, gesichert, zurückgerollt und protokolliert
wird wie jede andere (RFC-0041 K2). Und weil sie ihre Datenbank als
zweiten Container selbst mitbringt (RFC-0016): **dieses RFC zwingt
keinen Knoten, das Profil `store` zu tragen** — `oaapx01` trägt es
nicht.

Die Plattform weiß dabei ausdrücklich **nicht**, dass dieser Keycloak
lokal ist. Das Anbieter-Objekt eines Mandanten ist eine URL wie jede
andere. Genau diese eine Eigenschaft macht den späteren Umzug eines
Vereins auf einen eigenen Knoten zu einer *Änderung* statt zu einem
*Projekt*.

## Die Version ist festgenagelt

`quay.io/keycloak/keycloak:26.7.4`, und das ist eine Auflage, keine
Vorsicht. K3 lässt OAAP die Realms über Keycloaks
Verwaltungsschnittstelle anlegen, und so eine Anbindung altert gegen
ein fremdes Produkt — unsichtbar, bis sie bricht. Gegen diese Fassung
ist am 22.09.2026 gemessen worden (Realm anlegen, Client anlegen,
Geheimnis abholen, Export/Import mit erhaltener Kennung). Der Server
nennt seine Fassung unter `/admin/serverinfo`, und das macht die
festgenagelte Zahl **prüfbar** statt nur aufgeschrieben.

## Installieren

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps \
  --path apps/keycloak --name auth
```

Danach im Portal auf der Instanzseite:

| Wert | Bedeutung |
| --- | --- |
| `KC_HOSTNAME` | **Pflicht.** Die öffentliche Adresse dieser Instanz, z. B. `https://auth.<knoten>`. Keycloak schreibt sie als `issuer` in jedes Token. |
| `POSTGRES_PASSWORD` | Wird erzeugt. Beide Container lesen denselben Wert. |
| `KC_BOOTSTRAP_ADMIN_USERNAME` / `..._PASSWORD` | Der erste Verwalter, nur beim allerersten Start. |

`KC_HOSTNAME` fehlt → der Container startet **nicht**, und sagt warum.
Das ist Absicht: Ein aus der Host-Kopfzeile abgeleiteter `issuer` wäre
eine Identität, die sich mit dem Namen ändert, unter dem jemand gerade
vorbeikam — und OAAP bindet seine Benutzer an genau diesen `issuer`.

## OAAP legt den Realm selbst an

Seit Referenz 0.1.122 (RFC-0041 Schritt 4). Zwei Handgriffe am Knoten,
und der Verein hat seine Tür:

```sh
sudo oaap idp add auth --url https://auth.<knoten> \
  --admin-id oaap-admin --admin-secret '<das Geheimnis des Dienstkontos>' \
  --accept-version 26.7.4
sudo oaap idp check auth
sudo oaap idp provision auth --tenant hbvp \
  --idp-label "Mit dem Vereinskonto anmelden"
```

Zu `--accept-version` siehe weiter unten — es ist nicht die Abkürzung,
für die es aussieht, sondern die Folge einer Messung.

`provision` fragt zuerst, **welche Fassung** dieser Keycloak ist, legt
dann den Realm an (oder benutzt den vorgefundenen), legt den Client an
(oder trägt nur die fehlende Rückkehradresse nach), holt das Geheimnis
und schreibt das Anbieter-Objekt des Mandanten. `--dry-run` druckt
vorher jeden Aufruf, den es machen **könnte**.

Drei Dinge, die dieser Weg **nicht** tut, und zwar mit Absicht:

- Er **löscht nichts**. Nie. Ein Realm ist die Mitgliederliste eines
  Vereins; einen Mandanten zu entfernen darf die Menschen darin nicht
  entfernen. Die Regel steht als Funktion auf dem Weg jedes Aufrufs,
  nicht als eine Zeile, die niemand geschrieben hat.
- Er **legt nichts halb an**. Passt die Fassung nicht, bricht er ab,
  bevor irgendetwas entsteht — gemessen, nicht versprochen.
- Er **nimmt, was er vorfindet**. Ein von Hand gebauter Realm bleibt
  ein von Hand gebauter Realm; OAAP benutzt ihn, wie er ist.

### Das Dienstkonto, das OAAP dafür braucht

Einmal von Hand, in der Verwaltungsoberfläche des **master**-Realms:

1. *Clients → Create client* → Client ID `oaap-admin`,
   *Client authentication:* **ein**, *Standard flow:* **aus**,
   *Service accounts roles:* **ein**.
2. *Credentials → Client secret* abholen.
3. *Service accounts roles → Assign role → Filter by realm roles* →
   **`create-realm`**, und sonst nichts.

**Warum nicht der Verwalter, der beim ersten Start entsteht?** Weil der
alles kann, was dieser Server kann — auf einer Maschine mit mehreren
Vereinen ist das jede Mitgliederliste darauf. RFC-0041 K3.4 wollte eine
Vollmacht, die **nie** die des master-Realms ist; beim Bauen zeigte
sich, dass einen Realm *anzulegen* bei Keycloak ein Akt im master-Realm
**ist**. Was bleibt, ist die Verengung, die möglich ist: ein Konto ohne
Menschen dahinter, mit genau einem Recht. Wer stattdessen ein
Benutzerkonto einträgt (`--auth password`), bekommt das jedes Mal
gesagt, wenn der Konnektor gedruckt wird.

**Was dieses Dienstkonto darf, gemessen** (23.09.2026, Keycloak 26.7.4,
`create-realm` und sonst nichts):

| Anfrage | Antwort |
| --- | --- |
| einen Realm anlegen | **201** |
| den selbst angelegten Realm und seine Clients verwalten | **200** |
| Mitglieder des master-Realms lesen | **403** |
| Clients des master-Realms lesen | **403** |
| Mitglieder eines fremden Realms lesen | **403** |
| alle Realms auflisten | **403** |
| die Fassung des Servers lesen | **geht nicht** |
| in den selbst angelegten Realms die Schalter umlegen (23.09., Schritt 6) | **204** |
| in einem fremden Realm dieselben Schalter lesen | **403** |
| die Menschen in einem selbst angelegten Realm zählen (23.09., Schritt 7) | **200** |
| die Menschen in einem fremden Realm zählen | **403** |
| in einem **eingespielten** Realm irgendetwas tun | **403** — siehe unten |

### `--accept-version`: was bei dieser Messung herauskam

Die letzte Zeile ist der Befund. `/admin/serverinfo` **antwortet** dem
Dienstkonto, nennt aber keine Fassung: Das Dokument kommt beschnitten
zurück (`profileInfo` und sonst nichts), solange die Vollmacht nicht
die eines vollen Server-Verwalters ist. Gemessen für `create-realm`
allein **und** für `create-realm` + `view-realm` — die zweite bringt
keine Fassung und kostet das Auflisten aller Realms, also jedes Vereins
auf der Maschine.

K3.1 will die festgenagelte Fassung **prüfbar**, K3.4 will die
Vollmacht **eng**. Beides zusammen gibt es bei Keycloak 26.7.4 nicht.
Was bleibt, ist der Kern von K3.1: Es entsteht nichts gegen eine
Fassung, die niemand geprüft hat. Kann OAAP sie nicht lesen, **nennt
sie ein Mensch** — und OAAP schreibt überall dazu, dass sie genannt und
nicht gelesen wurde (`oaap idp list`, `oaap idp check`,
`oaap tenant idp`, das Protokoll des Mandanten).

Eine Behauptung schlägt eine Messung dabei nie: Sagt der Server eine
Fassung, gilt sie — auch gegen den Zettel.

Wer sie lieber gelesen als genannt hätte, trägt ein Benutzerkonto ein
(`--auth password`). Das ist der ganze Unterschied zwischen den beiden
Vollmachtsformen, und er steht in der Ausgabe.

Die Vollmacht liegt danach `0600` auf dem Knoten, in einem Verzeichnis,
das **kein** Container einhängt — auch der Anmeldedienst nicht, der die
Client-Geheimnisse der Mandanten hält. Ein Dienst, der Anmeldungen
abschließt, hat mit einer Vollmacht, die Realms anlegen kann, nichts zu
tun.

## Das Rezept: ein Realm für einen Mandanten, von Hand

K3 sagt *verwalten*, nicht *besitzen*. Auch wenn OAAP es selbst kann:
**ein von Hand angelegter Realm bleibt benutzbar**, und OAAP löscht nie
einen Realm, den es vorgefunden hat. Deshalb steht das Rezept hier und
bleibt hier.

1. **Realm anlegen.** Verwaltungsoberfläche → *Create realm* →
   Name = das Mandanten-Kürzel, z. B. `hbvp`.

2. **Client anlegen.** *Clients → Create client*
   - Client type: `OpenID Connect`
   - Client ID: z. B. `oaap-<knoten>`
   - *Client authentication:* **ein** (vertraulicher Client — das ist
     die Voraussetzung dafür, dass OAAP die Signatur des Tokens nicht
     prüfen muss; siehe unten)
   - *Authentication flow:* nur `Standard flow`
   - *Valid redirect URIs:* **genau eine Adresse je Eingang**, nämlich
     die des Mandantenorts:

     ```
     https://<kürzel>.<knoten>/auth/oidc/callback
     ```

     Kein Sternchen. Die Rückkehradresse ist der einzige Ort, an dem
     der Autorisierungscode landet.

3. **Geheimnis abholen.** *Clients → <Client> → Credentials →
   Client secret*.

4. **Dem Mandanten sagen, wo seine Tür ist.** Am Knoten:

   ```sh
   sudo oaap tenant idp hbvp \
     --issuer https://auth.<knoten>/realms/hbvp \
     --client-id oaap-<knoten> \
     --client-secret '<das Geheimnis>' \
     --idp-label "Mit dem Vereinskonto anmelden"
   ```

   Das Geheimnis liegt danach `0600` auf dem Knoten, in einem
   Verzeichnis, das nur der Anmeldedienst einhängt — **nicht** in
   `tenants.json` und damit **nicht** im Mandantenarchiv.

5. **Prüfen, was ein erster Login bedeutet.**

   ```sh
   sudo oaap tenant policy hbvp
   ```

   Vorgabe ist `eingang`: eine Identität und keine Rechte. Wer das
   ändert, ist der Betreiber, nie der Mandant — auf einer Maschine mit
   mehreren Kunden öffnet sonst jemand eine Tür, die ihm nicht allein
   gehört.

## Was OAAP von einem Token liest — und was nicht

- **Gebunden wird an `(Anbieter, sub)`**, an nichts sonst. Nicht an die
  E-Mail-Adresse, nicht an den Benutzernamen. Beides wäre
  Kontoübernahme durch Namensgleichheit.
- **Keine Behauptung des Anbieters wird zu einer OAAP-Rolle.** Eine
  Realm-Gruppe darf auf eine OAAP-*Sichtbarkeitsgruppe* abgebildet
  werden, und zwar durch eine Zuordnung, die der Betreiber schreibt;
  eine nicht zugeordnete Gruppe bewirkt nichts.
- **Ein zweiter Faktor wird protokolliert, nicht erzwungen.** Keycloak
  entscheidet, ob eine Anmeldung einen zweiten Faktor braucht; OAAP
  erfährt nur, dass sie geklappt hat, und schreibt auf, was behauptet
  wurde (`amr`/`acr`).

## Die Verwaltungsoberfläche ist öffentlich erreichbar

Wie bei jedem Keycloak: Sie schützt sich mit ihrem eigenen Admin-Login.
Wer das auf einem Knoten am Internet nicht will, stellt die Instanz
hinter die Sichtbarkeits- oder Randregeln des Knotens. Die Route dieser
App ist `public`, weil Keycloak die Anmeldung **ist** — sie hinter die
Anmeldung des Gateways zu stellen wäre eine Tür, die sich selbst als
Schlüssel verlangt.

## Die zwei Schalter im Realm

Seit Referenz 0.1.123 (RFC-0041 Schritt 6). Wer sich selbst anmelden
darf und ob ein zweiter Faktor verlangt wird, sind Einstellungen des
**Realms** — die Registrierungsseite ist seine Seite, und den zweiten
Faktor prüft er. OAAP legt sie jetzt um:

```sh
sudo oaap idp settings auth --tenant hbvp --self-registration on
sudo oaap idp settings auth --tenant hbvp --second-factor required
sudo oaap idp settings auth --tenant hbvp            # nur nachsehen
```

`--dry-run` druckt auch hier vorher jeden Aufruf. Ohne Schalter
angegeben wird nur gelesen — und genau das ist der Befehl, mit dem man
nachsieht, ob jemand in der Verwaltungsoberfläche gedreht hat.

**Die Regel dahinter.** Ein Schalter, der an zwei Orten steht, hat zwei
Wahrheiten: im Realm, wo er wirkt, und im Satz des Mandanten, wo ein
Mandantenverwalter ihn liest. OAAP schreibt, liest **danach nach**, und
schreibt die *Antwort* des Realms auf — nie die eigene Anweisung. Sagt
der Realm etwas anderes, als er zugesagt hat, ist das ein lauter
Fehlschlag, und der Satz des Mandanten wird trotzdem auf die
Wirklichkeit gesetzt. Gehen die beiden auseinander, weil jemand in der
Oberfläche gedreht hat, sagt `oaap tenant idp <mandant>` das mit einem
`DIFFERENT:`-Satz, statt seine eigene Notiz zu glauben.

**Zwei Dinge, die gemessen und nicht angenommen sind** (23.09.2026,
Keycloak 26.7.4):

- **Der zweite Faktor erreicht nur, wer ab jetzt dazukommt.** Keycloak
  hängt `CONFIGURE_TOTP` als Vorgabe-Aktion an neue Mitglieder. Wer
  schon im Realm ist, wird **nicht** nachträglich gefragt — das ginge
  nur, indem man es jeder Person einzeln anhängt, und OAAP fasst keine
  Menschen an. Für die Bestandsmitglieder ist die
  Verwaltungsoberfläche der Weg. Der Befehl sagt das, bevor jemand es
  annimmt.
- **OAAP erzwingt keinen zweiten Faktor.** Der Realm tut das. Was OAAP
  tut: Es schreibt ins Protokoll des Mandanten, wenn der Realm einen
  verlangen soll und die Anmeldung keinen *nennt*. Gelesen wird dabei
  `amr` (welche Methoden), nicht `acr` (welche Stufe) — Keycloak
  antwortet auf eine gewöhnliche Passwort-Anmeldung mit `acr=1`, und
  was diese Zahl bedeutet, wird im Realm festgelegt und nicht bei OAAP.

**Und eine Ablehnung, die von hier kommt.** Weil die
Registrierungsseite dem Realm gehört, wird `--first-login role`
zusammen mit Selbstregistrierung auch dann abgelehnt, wenn nur der
**Realm** offen ist und OAAPs Notiz das Gegenteil behauptet. Die
Ablehnung sagt, welche Hälfte offen ist.

## Was hier noch fehlt

Nichts mehr an Verben. Seit 0.1.124 (RFC-0041 Schritt 7) sind alle
sechs gebaut, und `later` im Konnektor ist leer.

Ausdrücklich **nicht** fehlend, sondern abgeschworen: `users`. OAAP
legt in einem Realm keine Menschen an, ändert keine und entfernt
keine. Das steht im Konnektor unter `never` und nicht unter „noch
nicht".

## Umzug: ein Verein zieht auf einen eigenen Knoten (RFC-0041 K6)

Seit Referenz 0.1.124. Das Rezept, in der Reihenfolge, in der es
gemessen wurde (oaap-test → oaap-demo, 23.09.2026):

```sh
# --- auf dem ALTEN Knoten ---
sudo oaap idp export auth --tenant hbvp --out /root/hbvp-realm.json
sudo oaap backup create --tenant hbvp --to /root

# --- beide Dateien hinüber, wie man ein Backup bewegt ---

# --- auf dem NEUEN Knoten ---
# 1. Keycloak dort installieren (siehe oben)
# 2. den Realm einspielen — POST /admin/realms mit der Exportdatei
# 3. DEM DIENSTKONTO RECHTE AN DIESEM REALM GEBEN (siehe unten!)
sudo oaap tenant adopt /root/hbvp.tar.gz
sudo oaap idp provision auth --tenant hbvp
```

**Schritt 3 ist der, den man vergisst.** Keycloak gibt die Verwaltung
eines Realms dem, der ihn **angelegt** hat — über die Rollen des
Clients `<realm>-realm` im `master`-Realm. Einen **eingespielten**
Realm hat niemand angelegt, also hat auch das OAAP-Dienstkonto keine
Rechte darin, und `oaap idp provision` wird mit *„this credential may
not look at the clients in the realm"* abgelehnt. Das ist richtig und
es ist nicht offensichtlich. In der Verwaltungsoberfläche:
`master` → Clients → `oaap-admin` → Service account roles → die Rollen
des Clients `<realm>-realm` zuweisen.

**Was der Export ist und was nicht.** Er trägt den Realm, seine
Clients samt Geheimnis **und die Menschen mit ihren
Passwort-Nachweisen**. Er ist damit ein Geheimnis wie ein
Backup-Archiv: `0600`, nie im Datenverzeichnis der Plattform, nie für
eine App lesbar, nie als gewöhnlicher Download — und nach dem Umzug
gelöscht. `oaap idp export` sagt das jedes Mal.

**Drei Türen, und zwei davon lügen** (gemessen, 26.7.4):

| Tür | trägt die Menschen? | meldet den Fehler? |
| --- | --- | --- |
| `POST /admin/realms/{r}/partial-export` | **nein** — antwortet 200, trägt Clients und Geheimnis, keinen einzigen Menschen | nein |
| `kc.sh export` per `docker exec` im bedienenden Container | **nein** — dort stehen keine `KC_DB_*` (der Entrypoint setzt sie in seinen eigenen Prozess), also fällt es still auf sein eingebautes leeres H2 zurück und exportiert einen Realm, den nie jemand benutzt hat. Es schreibt außerdem die Konfiguration des Containers neu | nein |
| `kc.sh export` in einem **Wegwerf-Container** am selben Netz und derselben Datenbank | **ja**, mit den Passwort-Nachweisen | — |

Zwei von drei schreiben eine Datei, die wie die richtige aussieht.
Deshalb **zählt** OAAP: vorher den Realm fragen, wie viele Menschen er
hat, hinterher die Datei zählen, und bei einer Differenz die Datei
wegwerfen.

**Und die Bindungen ziehen mit um.** Eine Person hängt bei OAAP an
`(Aussteller, sub)`. Gemessen: der `sub` übersteht Export und Import
unverändert — der **Aussteller** nicht, denn der ist
`<knoten>/realms/<name>`, also die Adresse des Knotens, und genau die
wechselt beim Umzug. `oaap idp provision` hängt die Bindungen deshalb
beim Ablösen des mitgebrachten Anbieters um und sagt, wie viele. Ohne
das wäre jedes Mitglied am neuen Knoten ein Fremder: erste Anmeldung,
Eingang, Rollen weg.

**Was auf dem alten Knoten liegen bleibt.** Alles. OAAP greift nicht
auf eine Maschine, auf der es nicht läuft, und löscht nichts — auch
nicht den alten Client im umgezogenen Realm, der dort weiter steht.
Beides ist Handarbeit eines Menschen, und bis dahin gibt es den Verein
zweimal.
