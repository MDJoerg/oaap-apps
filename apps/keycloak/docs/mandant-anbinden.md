# Einen Mandanten anbinden

Ziel: Die Menschen eines Mandanten melden sich über „seinen“ Realm an.

## Voraussetzungen

- Eine Instanz des Anmeldedienstes läuft, mit gesetzter öffentlicher
  Adresse (`KC_HOSTNAME`, z. B. `https://auth.<knoten>`). Ohne sie startet
  der Dienst bewusst nicht – diese Adresse steht in jedem Anmeldenachweis.
- Im **master**-Realm gibt es ein Dienstkonto für die Plattform (einmal von
  Hand, siehe unten).

## Der übliche Weg: die Plattform legt alles an

Am Knoten, als Betrieb:

```sh
sudo oaap idp add auth --url https://auth.<knoten> \
  --admin-id oaap-admin --admin-secret '<Geheimnis des Dienstkontos>' \
  --accept-version <geprüfte Fassung>
sudo oaap idp check auth
sudo oaap idp provision auth --tenant <mandant> \
  --idp-label "Mit dem Vereinskonto anmelden"
```

`provision` legt den Realm an (oder nimmt den vorhandenen), legt den Client
an, holt das Geheimnis und trägt den Anbieter beim Mandanten ein. Mit
`--dry-run` siehst Du vorher jeden Schritt.

Drei Zusagen dieses Wegs:

- Er **löscht nie** etwas. Ein Realm ist die Mitgliederliste eines
  Mandanten.
- Er **legt nichts halb an**. Passt die Fassung nicht, bricht er ab, bevor
  etwas entsteht.
- Er **nimmt, was er vorfindet**. Ein von Hand gebauter Realm bleibt, wie er
  ist.

## Das Dienstkonto (einmal)

In der Verwaltungsoberfläche des master-Realms:

1. *Clients → Create client*: Client ID `oaap-admin`, *Client
   authentication* ein, *Standard flow* aus, *Service accounts roles* ein.
2. *Credentials*: das Client-Geheimnis abholen.
3. *Service accounts roles → Assign role*: nur **create-realm**.

Bewusst nicht der erste Verwalter: Der darf alles auf diesem Server, also
jede Mitgliederliste. Das Dienstkonto sieht nur die Realms, die es selbst
angelegt hat.

## Von Hand (wenn es sein muss)

1. *Create realm* mit dem Kürzel des Mandanten als Namen.
2. *Clients → Create client*: OpenID Connect, *Client authentication* ein,
   nur *Standard flow*, als Rückkehradresse genau
   `https://<kürzel>.<knoten>/auth/oidc/callback` – kein Sternchen.
3. Das Client-Geheimnis abholen.
4. Am Knoten eintragen:
   ```sh
   sudo oaap tenant idp <mandant> \
     --issuer https://auth.<knoten>/realms/<mandant> \
     --client-id <client> --client-secret '<Geheimnis>'
   ```

## Danach prüfen

```sh
sudo oaap tenant policy <mandant>
```

Die Vorgabe ist `eingang`: Wer sich zum ersten Mal anmeldet, hat eine
Identität, aber noch keine Rechte. Rollen vergibt die Verwaltung des
Mandanten in der Plattform.
