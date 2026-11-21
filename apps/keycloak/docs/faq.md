# Anmeldedienst (Keycloak) – häufige Fragen

## Der Dienst startet nicht.

Meist fehlt `KC_HOSTNAME` in der Konfiguration der Instanz. Der Dienst
startet dann bewusst nicht und sagt das im Protokoll. Trage die öffentliche
Adresse ein, z. B. `https://auth.<knoten>`, und starte neu.

## Die Konsole lädt, zeigt aber nur „Manage realms“.

Die Konsole fragt nach dem Laden mit ihrem eigenen Keycloak-Zugang nach,
was Du darfst. Steht davor eine ältere Fassung dieser App, scheitert diese
Frage. Aktualisiere die Instanz auf die aktuelle Fassung.

## Ich komme nicht in die Verwaltungsoberfläche.

Sie verlangt zuerst die Plattform-Anmeldung mit der Rolle `admin` im
Mandanten der Instanz, danach ein Verwalterkonto bei Keycloak. Fehlt eins
von beiden, bleibt die Tür zu.

## Jemand hat sich angemeldet, sieht aber nichts.

Richtig so: Ein erster Login bringt eine Identität, aber noch keine Rechte
(Vorgabe `eingang`). Die Verwaltung des Mandanten vergibt die Rollen in der
Plattform.

## Kann ich die E-Mail-Adresse eines Kontos ändern, ohne dass es seine Rechte verliert?

Ja. Die Plattform bindet an die feste Kennung beim Anbieter, nicht an E-Mail
oder Benutzernamen.

## Warum ist die Version festgelegt?

Die Plattform legt Realms über Keycloaks Verwaltungsschnittstelle an. Gegen
eine ungeprüfte Fassung könnte diese Anbindung unbemerkt brechen. Eine neue
Fassung kommt deshalb als bewusste Aktualisierung dieser App.

## Was steht in der Sicherung?

Die Datenbank des Anmeldedienstes mit allen Realms, also auch die Konten
der Mandanten. Die Client-Geheimnisse der Plattform liegen getrennt auf dem
Knoten, nicht im Mandantenarchiv.
