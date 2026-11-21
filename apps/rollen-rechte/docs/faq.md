# Häufige Fragen

**Ich sehe „Kein Zugriff“, obwohl ich die App öffnen kann.**
Öffnen darf jede angemeldete Person, ändern und sehen nur Mandanten-Admins. Das
prüft der Anmeldedienst bei jedem Aufruf. Bitten Sie eine Administratorin Ihres
Mandanten um das Recht.

**Die Seite sagt, es fehle ein Verwaltungsschlüssel.**
Der Betreiber des Servers hat beim Installieren nicht bestätigt, dass diese App
Rechte verwalten darf. Er installiert sie mit dieser Bestätigung neu.

**Ich habe ein Paket zugeordnet, aber die Person darf es in der App noch nicht.**
Prüfen Sie den Zeitraum (von, bis) und „Gilt für“. Kommt das Recht über eine
Gruppe, wirkt es erst beim nächsten Login der Person.

**Warum kann ich ein Paket nicht ausblenden?**
Es steht noch eine gültige Zuordnung oder eine Gruppe darauf. Beenden Sie die
Zuordnungen oder entfernen Sie die Abbildung zuerst.

**Kann ich eine Rolle löschen?**
Nein, nur ausblenden. So bleibt im Protokoll nachvollziehbar, wer wann was durfte.

**Wie lege ich eine neue Person oder einen Agenten an?**
Nicht hier. Konten legt die Plattform an; einen Agenten der Betreiber oder ein
Mandanten-Admin mit `oaap authz agent-add`.

**Kann ein Assistent die Rechte lesen?**
Ja, über die MCP-Tür der App – aber nur mit Ihren Rechten: Wer kein Mandanten-Admin
ist, bekommt auch dort keine Namen. Der Assistent kann nichts ändern.
