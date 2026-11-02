"""Zugangsdaten der Bezugsquellen, verschlüsselt unter /data (RFC-0064 D2).

Der Tresor selbst steht seit 0.5.1 in `gwcore/vault.py` (RFC-0065
Stufe 0), gemeinsam mit dem MCP-Gateway. Hier steht nur, was diese App
daran festlegt: der Zweck der Ableitung und der Name der Variablen.

**Der Zweck `oaap-ai-gateway-vault|` ist Teil des gespeicherten
Formats.** Wer ihn ändert, macht alle gespeicherten Zugangsdaten der
Anbieter unlesbar — test_catalog.py prüft deshalb einen Text, der vor
der Umstellung versiegelt wurde.
"""
from gwcore.vault import MIN_MASTER, VERSION, Vault, VaultError  # noqa: F401

_VAULT = Vault(b"oaap-ai-gateway-vault|", "AIGW_MASTER_KEY")

usable = _VAULT.usable
seal = _VAULT.seal
open_ = _VAULT.open_
