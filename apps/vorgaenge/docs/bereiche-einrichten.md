# Bereiche einrichten

Für Mandanten-Administratoren. Ein **Bereich** ist eine Zuständigkeit: ein Thema oder ein
Team. Vorgänge kommen im **Eingang** an und werden von dort verteilt.

## Einen Bereich anlegen

1. Reiter **Bereiche**, unten *Bereich anlegen*.
2. Eine Kennung (Kleinbuchstaben, Ziffern, Bindestrich, z. B. `buchhaltung`) und einen Titel
   eintragen, **Anlegen**.

## Zuständige eintragen

1. In der Liste auf den Namen des Bereichs klicken.
2. Unter *Zuständige pflegen* eine Person wählen und als **Bearbeiter/in** oder **Clearing**
   eintragen. Das Clearing darf zusätzlich an Partner übergeben.

Fehlt jemand in der Auswahl? Die Person öffnet die App Vorgänge einmal, danach steht sie
dort.

In großen Mandanten geht dasselbe über **Rollen & Rechte**: App *Vorgänge*, Vorlage
*Bearbeiter/in* oder *Clearing*, Kontext = Kennung des Bereichs. Beides zählt.

## Spezialist und Ampel

Auf der Seite des Bereichs außerdem:

- **Erstbearbeitung durch einen Spezialisten**: Neue Vorgänge bekommt zuerst dieser
  KI-Spezialist. Er erscheint in der Auswahl, sobald die App Spezialisten einmal für ihn
  gelaufen ist.
- **Ampel für Spezialisten**: Bis zu welcher Farbe ein Spezialist hier Modelle und Werkzeuge
  nutzen darf. Grün ist am strengsten.

Der **Freigabeschlüssel** (Link unter der Liste) unterschreibt jede Freigabe eines geplanten
Aufrufs. Hinterlegen Sie ihn bei der Sicht des Spezialisten im MCP-Gateway – dann prüft das
Gateway jede Freigabe selbst. Behandeln Sie ihn wie ein Passwort.

## Löschfrist

Nach `VORGAENGE_RETENTION_MONTHS` Monaten (Standard 12) wird die meldende Person aus einem
geschlossenen oder abgelehnten Vorgang entfernt.
