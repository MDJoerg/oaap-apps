# Eine Bezugsquelle anlegen

Eine Bezugsquelle ist der Dienst, der am Ende antwortet: ein eigenes Ollama, ein Rechner mit
Grafikkarte hinter einem VPN, oder ein Anbieter wie Groq oder Anthropic.

1. Öffne den Reiter **Bezugsquellen**.
2. Trage unter **Bezugsquelle anlegen** einen kurzen **Namen** ein, zum Beispiel `groq`.
3. Trage die **Adresse** bis `/v1` ein, zum Beispiel `https://api.groq.com/openai/v1`.
4. Wähle die **Ampel**: Was kann mit den Daten dort geschehen? Ohne Angabe gilt rot.
5. Trage die **Zugangsdaten** (den API-Key des Anbieters) ein, falls nötig.
6. Klicke auf **Anlegen**.

Zugangsdaten werden verschlüsselt gespeichert und **nie wieder angezeigt**. Ändern kannst du
sie jederzeit, indem du neue einträgst.

## Prüfen, ob die Quelle antwortet

Klicke in der Liste auf den Namen und dann auf **Testen**. Das Gateway fragt die Modellliste
ab – erreichbar? Anmeldung gültig? Von dort führt **Modellnamen daraus anlegen** direkt zu den
Modellen dieser Quelle.

## Eine Quelle über die Plattform

Hat der Betreiber dieser Instanz Ziele zugeordnet (zum Beispiel einen Rechner hinter einem
VPN), stehen sie unter **Aus Destinationen der Plattform**. Die Plattform meldet sich dort an;
hier sind keine Zugangsdaten nötig.
