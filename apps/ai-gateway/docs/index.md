# KI-Gateway

Das **KI-Gateway** ist die eine Bezugsquelle für alle KI-Anfragen im Mandanten. Programme,
Werkzeuge und der Chat fragen nicht direkt bei einem Anbieter, sondern beim Gateway – mit
einem Schlüssel, der sagt, wer fragt und was er darf. Das Gateway leitet weiter, zählt den
Verbrauch und speichert **keine Inhalte**: Prompts und Antworten stehen nirgends.

## Für wen

Für die **Serververwaltung** (Rolle admin). Wer Schlüssel ausstellt, vergibt Rechenzeit und
damit Geld; deshalb sehen andere Rollen diese App nicht.

## Die Reiter

- **Übersicht:** Anfragen, Token, Erfolg und Antwortzeit der letzten 24 Stunden, Verlauf und
  Ranglisten.
- **Letzte Anfragen:** jede Anfrage als Zeile – Zeit, Schlüssel, Modellname, Quelle, Token,
  Dauer, Ausgang. Mit Suche, Filtern und Blättern.
- **Auswertung:** lange Zeiträume, gestapelt nach Projekt, Kostenstelle, Schlüssel und mehr;
  auch als Schnittstelle für ein Abrechnungssystem.
- **Schlüssel:** ausstellen, ändern, widerrufen. Ein Klick auf ein Etikett öffnet den
  Schlüssel.
- **Kohorten:** viele Schlüssel auf einmal für eine Schulung.
- **Vorlagen:** fertige Einstellungsdateien für VS Code und opencode.
- **Modelle:** die Modellnamen, nach denen Verbraucher fragen.
- **Bezugsquellen:** die Anbieter oder eigenen Rechner, die tatsächlich antworten.

## Die wichtigsten Begriffe

- **Modellname:** der Name, den ein Programm eintippt, zum Beispiel `chat-default`. Er zeigt
  auf ein oder mehrere Ziele bei Bezugsquellen.
- **Bezugsquelle:** ein OpenAI-kompatibler Dienst, etwa ein eigenes Ollama oder ein Anbieter.
- **Ampel:** sagt, was mit den Daten geschehen kann – siehe [Die Ampel](ampel.md).

## Anleitungen

- [Einen Schlüssel ausstellen](schluessel-ausstellen.md)
- [Eine Bezugsquelle anlegen](bezugsquelle-anlegen.md)
- [Einen Modellnamen anlegen](modellname-anlegen.md)
- [Eine Kohorte für eine Schulung anlegen](kohorte-anlegen.md)
- [Den Verbrauch auswerten](verbrauch-auswerten.md)
- [Häufige Fragen](faq.md)
