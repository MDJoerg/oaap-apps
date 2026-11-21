# Einen Knoten ansehen

Klicken Sie in der Übersicht auf den Namen eines Knotens. Die Seite zeigt:

- **Überblick** – Zustand, Lebenszeichen, Name im Verbund und fester Knotenname,
  Adresse, über welchen Eingang er meldet, Plattformversion, Betriebssystem,
  Profile, Kerndienste, veröffentlichte Namen, ob ein Neustart nötig oder geplant
  ist und wie viele Sicherheitsmeldungen offen sind.
- **Instanzen** – jede App auf dem Knoten mit Version, Kanal (Produktiv oder
  Test), Zustand, eigener Adresse und automatischem Namen.
- Instanzen, die früher auf dem Knoten liefen, stehen klein darunter.

Oben rechts führen **Kopplung** und **Portal öffnen** in das Portal des Knotens.
Dort wird gehandelt – die App Flotte selbst ändert nichts.

## Der automatische Name

Jede Instanz hat einen automatischen Namen `<instanz>.<knoten>`. Das Urteil daneben
sagt, ob die Instanz unter diesem Namen auf ihrem Knoten erreichbar ist (Route
vorhanden, App antwortet). Es sagt nichts über das Zertifikat oder über die
Erreichbarkeit von außen.

## Name im Verbund

Trägt ein Knoten im Verbund einen anderen Namen als seinen festen Knotennamen,
steht der feste Name klein in Klammern daneben.
