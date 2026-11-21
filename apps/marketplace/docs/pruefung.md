# Prüfung mit vier Augen

Mit eingeschalteter Prüfung (Einstellung `MARKET_REVIEW=on`) zählt ein neues
Release erst, wenn eine **zweite** Person oder eine Prüf-KI es freigegeben hat.
Bis dahin sehen es nur der Betreiber und wer in das Asset einstellen darf –
mit dem Zeichen **in Prüfung**.

## Wer prüft

Prüfer trägt der Betreiber unter **Verwaltung → Prüfungen → Prüfer** ein, zum
Beispiel den Maschinenbenutzer einer Prüf-KI. **Wer einstellt, prüft nicht
selbst.**

## Der Ablauf

1. Ein neues Release wird eingecheckt; die Geheimnis-Sperre hat es schon
   geprüft. Es entsteht ein Fall.
2. Der Prüfer holt offene Fälle über die Schnittstelle, sieht Texte, Bilder
   und Dateien und urteilt: freigeben, ablehnen oder eskalieren.
3. Was die Prüf-KI eskaliert oder was länger als die eingestellte Frist
   (Standard 24 Stunden) offen ist, entscheidet der Betreiber: unter
   **Prüfungen** den Fall öffnen, Begründung schreiben, **Freigeben** oder
   **Ablehnen**.

Änderungen an Texten und Bildern sind sofort sichtbar und werden danach
geprüft. Was eine KI entschieden hat, steht im Fall mit ihrem Namen.
