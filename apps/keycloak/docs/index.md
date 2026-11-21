# Anmeldedienst (Keycloak) – Überblick

Der Anmeldedienst beantwortet **wer jemand ist**. Die Plattform beantwortet
**was jemand darf**. Dahinter steckt Keycloak, ein verbreiteter
Identitätsanbieter (OpenID Connect).

Diese Seiten sind für die **Verwaltung** und den **Betrieb**. Die Menschen
im Mandanten sehen vom Anmeldedienst nur die Anmeldeseite.

## Wie die Plattform ihn nutzt

- **Ein Realm je Mandant.** Ein Realm ist in Keycloak eine eigene
  Benutzerverwaltung: Mitglieder, Gruppen, Passwortregeln, zweiter Faktor.
  Jeder Mandant hat seinen eigenen, kein Mandant sieht den eines anderen.
- **Die Plattform ist Client.** Meldet sich jemand an, schickt das Gateway
  ihn zum Realm seines Mandanten und bekommt eine bestätigte Identität
  zurück.
- **Die Apps merken davon nichts.** Sie bekommen wie immer nur die Angaben
  der Plattform (wer, welche Rollen). Keine App baut eine eigene Anmeldung.
- **Rollen entstehen in der Plattform, nicht in Keycloak.** Gebunden wird an
  die feste Kennung der Person beim Anbieter – nie an E-Mail oder
  Benutzernamen. Eine Gruppe aus Keycloak wirkt nur, wenn der Betrieb sie
  ausdrücklich einer Sichtbarkeitsgruppe zuordnet.
- **Eigene Datenbank.** Der Anmeldedienst bringt seine Datenbank als
  zweiten Container mit. Sie ist von außen und für andere Apps nicht
  erreichbar.
- **Feste Version.** Keycloak ist auf eine geprüfte Fassung festgelegt, weil
  die Plattform Realms über Keycloaks Verwaltungsschnittstelle anlegt. Ein
  Wechsel der Fassung ist eine bewusste Aktualisierung.

## Typische Aufgaben

- [Einen Mandanten anbinden](mandant-anbinden.md) – Realm und Client
  anlegen lassen oder von Hand.
- [Selbstregistrierung und zweiter Faktor](schalter-im-realm.md) – die
  zwei Schalter je Realm.
- [Die Verwaltungsoberfläche öffnen](verwaltung-oeffnen.md) – Benutzer und
  Gruppen in Keycloak pflegen.
- [Häufige Fragen](faq.md)
