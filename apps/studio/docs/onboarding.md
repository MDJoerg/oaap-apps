# Ein Projekt, das es schon gibt (Onboarding)

Wenn eine Anwendung bereits entwickelt wird und nur noch der Weg auf die
Plattform fehlt, brauchst Du dafür **kein Vorhaben**. Unter **Onboarding**
(Kopfzeile des Studios) gibt es zwei Dateien zum Herunterladen.

## Plattform-Briefing

`oaap-plattform-briefing.md` enthält alle verbindlichen Regeln der
Plattform, ohne fachlichen Auftrag und ohne Adressen:

- Gateway und Identität (wer angemeldet ist, steht in Kopfzeilen)
- das Manifest `oaap-app.yaml`
- Oberfläche und Sprache
- Benutzer und fachliche Rechte
- Zugänge für Programme und Geräte ohne Browser-Anmeldung
- was ein Paket erfüllen muss und wie es in drei Phasen ankommt

Gib es der KI, die schon an dem Projekt arbeitet. Der Plattformteil ist
**wortgleich** derselbe Text, der in jedem Vorhaben-Briefing steht – beide
Blätter können nicht auseinanderlaufen.

## Starter-Paket

`oaap-starter-<version>.zip` ist das kleinste vollständige Paket:
kommentiertes Manifest, Dockerfile und eine kleine App, die die Identität
aus den Kopfzeilen liest, unter `/data` speichert und `/healthz`
beantwortet. Es ist die Vorlage zum Umbauen.

Das Starter-Paket besteht die Prüfung des Studios **ohne einen einzigen
Befund** – wenn ein eigenes Paket Befunde hat, lohnt der Vergleich mit dem
Starter.

## Was die Dateien nicht enthalten

Beide Dateien enthalten **keinen Deploy-Token, keine Anlege-Erlaubnis und
keine Adresse einer Instanz**. Sie dürfen weitergegeben werden, ohne dass
damit ein Zugang weitergegeben wird. Adresse und Token gehören zu einer
bestimmten Instanz und werden getrennt übergeben – zum Beispiel über den
**Deployment-Zettel** eines Vorhabens (siehe
[Paket prüfen und ausrollen](paket-ausrollen.md)).
