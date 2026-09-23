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

## Das Rezept: ein Realm für einen Mandanten, von Hand

K3 sagt *verwalten*, nicht *besitzen*. OAAP wird Realms anlegen können
(nächster Bauschritt) — **ein von Hand angelegter Realm bleibt trotzdem
benutzbar**, und OAAP löscht nie einen Realm, den es vorgefunden hat.
Deshalb steht das Rezept hier und bleibt hier.

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

## Umzug (RFC-0041 K6)

Zieht ein Verein auf einen eigenen Knoten, sind es drei Teile:

1. das Mandantenarchiv (`oaap backup create --tenant hbvp`),
2. der Realm-Export aus Keycloak,
3. **eine Zeile**: `oaap tenant idp hbvp --issuer <neue Adresse>`.

Gemessen: Die Kennung (`sub`) überlebt Export und Import unverändert,
also überleben **alle Bindungen** den Umzug. Und die Exportdatei
enthält das Client-Geheimnis und die Passwort-Nachweise der Mitglieder
— sie ist **ein Geheimnis wie ein Backup-Archiv**: `0600`, nie im
Speicher einer Instanz, nie für eine App lesbar, nie als gewöhnlicher
Download, nach dem Umzug gelöscht.
