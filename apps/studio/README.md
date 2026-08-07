# OAAP Studio

Verwaltung der **Entwicklungsvorhaben** und Erzeugung der **KI-Briefings** —
die erste Ausbaustufe des Studios (Vision: eigene Apps bauen bis No-Code).

Das Studio ist bewusst eine ganz normale OAAP-App aus dem Store und kein
Portal-Bestandteil: Unsere eigenen Funktionen gehorchen denselben Regeln
wie die Apps unserer Anwender (Dogfooding), und das Portal bleibt schlank.

## Was es kann (0.1.0)

- **Vorhaben verwalten** — Liste (Listenbericht) und Objektseite je Vorhaben,
  mit genau den Angaben, aus denen ein Briefing entsteht: Ziel, Zielanwender,
  Umfang der ersten Version, App-Typ, Status, Repository, Test-Instanz.
- **Briefing erzeugen** — auf Knopfdruck ein vollständiges Markdown-Briefing
  für die KI: fachlicher Auftrag in den Worten des Auftraggebers plus alle
  verbindlichen Plattformregeln (Contract-Verweis, Manifest, Gateway-Login,
  Storage, Oberflächenkonventionen, Deploy-Hook, Postkasten-Regeln).
  Herunterladen als `<vorhaben>-briefing.md`, ins Projektverzeichnis legen,
  KI starten.

## Bewusste Entscheidungen

- **Keine Deploy-Token im Studio.** Ein Token rollt die Test-Instanz neu aus
  — es ist ein Schlüssel. Es wird auf dem Server erzeugt
  (`sudo oaap app token create <instanz>`), einmalig angezeigt und direkt der
  KI übergeben. Das Studio speichert nur die Hook-Adresse.
- **Nur Standardbibliothek.** Der Build läuft auf dem Zielknoten (auch
  arm64); ohne Abhängigkeiten gibt es nichts aufzulösen, nichts zu
  kompilieren und keine Lieferkette.
- **Zusätzlicher Schutz gegen fremde Formulare (CSRF).** Die Plattform
  schützt bereits über das Sitzungs-Cookie (`SameSite=Lax`, `HttpOnly`);
  die App lehnt schreibende Anfragen aus fremdem Ursprung zusätzlich selbst
  ab (`Sec-Fetch-Site`/`Origin`) — doppelter Boden, falls die App einmal
  anders betrieben wird.

## Rollen

`keyuser` und `admin` dürfen Vorhaben sehen und pflegen (Design-Guidelines:
„Studio — keyuser+"); **Löschen** ist `admin` vorbehalten. Das Gateway
erzwingt den Zugang ohnehin, die App prüft zusätzlich.

## Installation

```sh
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/studio
```

Oder im Portal unter „Store", sobald die Plattform-App-Liste als Quelle
eingetragen ist:

```sh
sudo oaap store add-source https://raw.githubusercontent.com/MDJoerg/oaap-apps/main/oaap-store.json
```

## Konfiguration

| Schlüssel             | Bedeutung                                               |
| --------------------- | ------------------------------------------------------- |
| `STUDIO_CONTRACT_URL` | Adresse des App Deployment Contract (steht im Briefing) |
| `STUDIO_GIT_BASE`     | Basis-Adresse des eigenen Git-Hostings (z. B. Forgejo)  |

Daten liegen unter dem deklarierten Mount `/data` (SQLite) und werden
damit von `oaap backup create` gesichert.
