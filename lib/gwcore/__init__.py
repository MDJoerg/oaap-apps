"""Gemeinsame Bücher der Gateways (RFC-0065 Stufe 0).

KI-Gateway und MCP-Gateway führen dieselbe Art Bücher: API-Schlüssel nur
als SHA-256, ein Kontext je Schlüssel, eine Kopie dieses Kontexts in
jeder Messzeile, ein Tresor für Zugangsdaten, eine Prüfspur und **eine**
Abfrage hinter Grafiken und Report-Schnittstelle. Stünde das in jeder
App einzeln, liefe es eines Tages auseinander — zwei Tresore, zwei
Abfragen, die sich widersprechen.

- `vault`   — Zugangsdaten verschlüsselt unter /data
- `context` — Kontextfelder, Etiketten, die Kopie in der Messzeile
- `ledger`  — Schlüssel ausstellen/finden/widerrufen, Prüfspur, Rate
- `report`  — die eine Abfrage, Zeiträume, CSV
- `charts`  — SVG-Grafiken aus genau dieser Abfrage

**Diese Dateien sind Kopien.** Die Quelle liegt in `oaap-apps/lib/gwcore/`;
eine App wird nur aus ihrem eigenen Ordner gebaut und sieht `lib/` nicht.
Geändert wird nur an der Quelle, danach `python3 check-shared.py --fix`
im Wurzelordner von oaap-apps. Eine abweichende Kopie meldet
`check-shared.py` als Fehler.

Nur Standardbibliothek, wie die Apps selbst.
"""
