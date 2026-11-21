# Ollama ans KI-Gateway anschließen

Damit Anwender die Modelle über das KI-Gateway nutzen können, braucht das Gateway eine
Verbindung zu Ollama und zwei Zeilen Konfiguration.

1. Öffne den Reiter **Anschluss ans KI-Gateway**. Dort stehen die passenden Zeilen schon
   mit den Namen dieses Knotens.
2. Ein Betreiber verbindet am Knoten das Gateway mit Ollama:
   `sudo oaap app link add <gateway-instanz> ollama`
3. Im KI-Gateway trägst du Ollama als Bezugsquelle mit der Ampel **grün** ein und legst
   einen Modellnamen an, der auf ein Ollama-Modell zeigt.

Grün heißt: Die Daten verlassen das Unternehmen nicht. Ollamas eigene Schnittstelle wird
dabei nicht veröffentlicht, denn sie kennt keine Anmeldung.
