# Rollen & Rechte

Das Werkzeug des **Mandanten-Admins** für fachliche Berechtigungen
(RFC-0045 A7, `oaap.core.authorization` 0.3 §2.10): *wer darf was, in welcher
App, und warum.* Ein Vereinsvorstand, nicht die Technik, bedient diese Seite.

| Reiter | Was man dort tut |
|---|---|
| **Personen** | Personen des Mandanten; je Person: was sie **zur Zeit** darf, **woher** (Sammlung, von Hand oder über eine Gruppe, von wem, bis wann), Zuordnungen geben und beenden |
| **Rollen** | Rollen aus den Vorlagen der installierten Apps anlegen (mit den Werten, die der Mandant einsetzt), ausblenden |
| **Sammlungen** | Rollen zu einer Sammlung bündeln (auch über mehrere Apps), ausblenden |
| **Gruppen** | Eine Gruppe des Anmeldedienstes (Pfad, z. B. `Verein/Hallenwart`) gibt eine Sammlung — bei **jeder Anmeldung** neu gelesen |
| **Protokoll** | Wer hat wann was geändert |

## Was diese App ist — und was nicht

- **Eine privilegierte Tür.** Sie bekommt einen Schlüssel (`oaap.authz.admin`),
  mit dem sie die Rechte **eines** Mandanten verwaltet. Der Betreiber muss das
  bei der Installation bestätigen. Wer den Container hat, hat diese Macht über
  *Rechte* — nicht über Plattformrollen, nicht über andere Mandanten.
- **Nie** eine Plattformrolle: sie kann `tenant_admin` oder `server_admin` weder
  vergeben noch lesen, keine Benutzer anlegen, ändern oder löschen, keine
  Deklaration registrieren.
- **Keine zweite Wahrheit.** Sie hält nichts; jede Seite fragt den Identity-Dienst
  frisch. Ohne ihn zeigt sie nichts.
- **Sie entscheidet nicht, wer verwalten darf.** Jeder Aufruf nennt die Person
  (`X-OAAP-User-Id`), und der **Identity-Dienst prüft sie selbst**. Die
  Plattformrolle `tenant_admin` bekommt eine App nie zu sehen — sie könnte es
  gar nicht lesen. Wer kein Admin ist, sieht „Kein Zugriff" und keine Namen.
- **Ausblenden statt Löschen.** Nichts wird gelöscht. Eine Sammlung lässt sich
  nur ausblenden, wenn keine gültige Zuordnung und keine Gruppe mehr darauf
  steht; eine Rolle erst, wenn keine sichtbare Sammlung sie mehr enthält. Der
  Dienst sagt, was im Weg steht.
- **Formulare nur von dieser Seite.** Eine Anfrage ohne Herkunft oder von einer
  fremden Seite wird nicht ausgeführt (der Sitzungskeks würde sonst von jeder
  fremden Seite mitgeschickt).

## Installation

```bash
sudo oaap app install https://github.com/MDJoerg/oaap-apps --path apps/rollen-rechte \
  --tenant <mandant> --confirm-administer
```

Ohne `--confirm-administer` bricht der Install **ab, bevor etwas gebaut wird**,
und sagt, was der Schlüssel kann und nie kann. Ein neues Ausrollen einer schon
freigegebenen Instanz fragt nicht noch einmal. Wird die Instanz entfernt,
schließt sich die Tür mit.

Der Mandant der Instanz ist der, dessen Rechte sie verwaltet (`--tenant`).

## Wer sieht die Seite?

Die Route lässt jeden angemeldeten `user` herein — **sehen** ist nicht
**ändern**. Wer ändern darf, entscheidet der Identity-Dienst (siehe oben). Will
der Betreiber die Kachel nur den Admins zeigen, beschränkt er die Sichtbarkeit
der Instanz (RFC-0007).

## Umgebung (von der Plattform, nie von Hand)

`OAAP_AUTHZ_URL` und `OAAP_AUTHZ_ADMIN_KEY` — beide gehören der Plattform und
sind für Betreiber nicht editierbar. Fehlt der Schlüssel, sagt die Startseite es
offen.

## Prüfen

Der Test läuft gegen den **echten** Identity-Dienst (kein Fake auf der Seite der
App) und liegt deshalb in `oaap-reference`:

```bash
python3 oaap-reference/test/test_rollen_rechte_app.py
python3 oaap-reference/test/test_authorization_admin_door.py
```

Nicht gemessen: ob Vereinsadmins die Begriffe (Rolle, Sammlung, Kontext)
verstehen — das braucht einen echten Menschen.
