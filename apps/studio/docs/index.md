# OAAP Studio – Überblick

Das Studio ist das Verzeichnis Deiner **App-Vorhaben** und der Ort, an dem
aus Deinen Antworten ein **Briefing** für eine KI entsteht. Das Briefing
enthält den fachlichen Auftrag und alle technischen Regeln, die eine App auf
dieser Plattform erfüllen muss. Du musst diese Regeln nicht kennen – das
Studio schreibt sie mit.

Auf der anderen Seite desselben Wegs nimmt das Studio das **fertige Paket**
entgegen, das aus so einem Briefing entsteht: Es prüft es vorab und rollt es
auf die Test-Instanz aus.

## Wer das Studio benutzt

- **keyuser** und **admin** sehen und pflegen Vorhaben, prüfen Pakete und
  rollen sie aus.
- **Löschen** eines Vorhabens ist `admin` vorbehalten.
- Das Studio verlangt bewusst **keine** Serververwaltungsrolle
  (`server_admin`). Das Recht zum Ausrollen ist der Deploy-Token, den Du im
  Augenblick der Handlung eingibst – siehe [Deploy-Token](deploy-token.md).

## Der Weg von der Idee zur App

1. **Vorhaben anlegen** – Name, wer es benutzt, was es können muss.
2. **Briefing erzeugen** – herunterladen, ins Projektverzeichnis legen.
3. **KI starten** und mit dem Briefing arbeiten lassen.
4. **Testen** – die KI (oder Du im Studio) rollt getestete Stände auf die
   Test-Instanz aus.
5. **Produktiv setzen** – bleibt bewusst Handarbeit eines Menschen, im
   Portal des Zielknotens.

## Die Bereiche des Studios

| Bereich | Wozu |
|---|---|
| **Vorhaben** | Liste aller Vorhaben, je Vorhaben eine Seite mit Briefing, Paket, Instanzen |
| **Onboarding** | Plattform-Briefing und Starter-Paket für Projekte, die es schon gibt |
| **Hilfe** | die Kurzfassung dieser Seiten direkt im Studio |

## Weiterlesen

- [Vorhaben anlegen und Briefing erzeugen](vorhaben-und-briefing.md)
- [Ein Projekt, das es schon gibt (Onboarding)](onboarding.md)
- [Paket prüfen und ausrollen](paket-ausrollen.md)
- [Zielknoten und Zustand der Instanzen](zielknoten-und-zustand.md)
- [Deploy-Token und Anlege-Erlaubnis](deploy-token.md)
- [Häufige Fragen](faq.md)
