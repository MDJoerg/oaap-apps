# Der Nachpflege-Bericht

Der Abgleich geht in eine Richtung: Die Liste folgt dem Manifest. Der
Nachpflege-Bericht geht in die andere. Er sagt, **was dem Manifest fehlt**,
und ist zum Weiterreichen an die KI gedacht, die die App betreut – mit einem
YAML-Block, den sie einsetzen kann. Denn das Manifest gehört dem, der die
App gebaut hat; die Liste ist nur ein Verzeichnis.

## So bekommst Du ihn

- Für **eine App**: auf ihrer Seite **Nachpflege-Bericht**.
- Für die **ganze Liste**: Reiter **Entwurf**, **Bericht für die ganze
  Liste**.

Der Bericht kommt als Markdown-Datei.

## Was er bewusst nicht tut

- Wo Liste und Manifest sich **widersprechen**, verlangt er nichts: Dort ist
  meist die Liste veraltet.
- Für das Bild schlägt er **keinen Pfad** vor, weil der in der Liste einen
  anderen Bezugspunkt hat als im Manifest.
- Was das Manifest-Format noch nicht kennt, steht getrennt und **ohne
  Auftrag**.
