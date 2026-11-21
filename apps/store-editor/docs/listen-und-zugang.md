# Listen und Zugangsdaten einrichten

Unter **Listen und Zugang** steht, welche Listen dieser Editor pflegt und
welcher Schlüsselplatz für jede gilt. Aufnehmen und Entfernen brauchen
**keyuser** oder **admin**.

## Eine Liste aufnehmen

1. Trage die **Adresse der Liste** ein. Eine Adresse aus der Adresszeile des
   Browsers (`…/blob/main/…`) schreibt der Editor beim Aufnehmen um – sonst
   käme eine Webseite statt der Datei an.
2. Optional einen **Namen**; sonst nimmt der Editor den aus der Liste.
3. Wähle die **Zugangsdaten**: „keine“ für ein öffentliches Repository, sonst
   einen der drei Plätze.
4. **Aufnehmen**. Der Editor ruft die Liste sofort einmal ab und sagt, ob
   das geklappt hat.

## Eine Liste entfernen

Öffne die Liste, Reiter **Entwurf**, und klicke **Liste aus dem Editor
nehmen**. Ein Entwurf dazu geht dabei verloren. Im Repository ändert das
nichts.

## Wo die Schlüssel liegen

Ein privates Repository verlangt schon zum **Lesen** einen Schlüssel. Der
Editor kennt drei **Plätze**. Die Schlüssel selbst trägt ein
Serververwalter im Portal ein, in der Konfiguration dieser Instanz
(`STORE_EDITOR_TOKEN_1` bis `_3`). Dort sind sie geheim: eintragbar, nie
zurücklesbar – auch nicht von dieser App.

Ein Schlüssel geht nur an den Anbieter, für den er eingetragen ist. Eine
Liste kann nicht dadurch, dass sie auf ein fremdes Repository zeigt, dorthin
einen Schlüssel schicken lassen.
