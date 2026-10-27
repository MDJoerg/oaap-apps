"""Zugangsdaten der Bezugsquellen, verschlüsselt unter /data (RFC-0064 D2).

Wozu: Seit Stufe 2 stehen die Schlüssel der Anbieter in der Datenbank
der App statt in der geheimen Instanz-Konfiguration. Eine Sicherung von
`/data` allein soll sie nicht preisgeben. Der Schlüssel zum Tresor kommt
deshalb aus einer **anderen** Quelle: der geheimen Variablen
`AIGW_MASTER_KEY` (im Portal per Knopf erzeugbar, `generate: token`).

Womit: Die App ist absichtlich nur Standardbibliothek, und die hat kein
AES. Gebaut ist deshalb die bekannte Form „Encrypt-then-MAC“ aus
HMAC-SHA256 allein:

- Schlüsselstrom = HMAC(enc_key, nonce ‖ zähler) — HMAC als
  Pseudozufallsfunktion im Zählermodus, ein frischer 16-Byte-Nonce je
  Verschlüsselung;
- Prüfsumme = HMAC(mac_key, version ‖ nonce ‖ geheimtext), verglichen in
  konstanter Zeit, **bevor** irgendetwas entschlüsselt wird.

`enc_key` und `mac_key` sind getrennt aus dem Hauptschlüssel abgeleitet.
Ein falscher Hauptschlüssel, ein verändertes Byte oder ein Text aus
einer anderen Instanz ergeben keinen Unsinn, sondern einen benannten
Fehler.

Was das NICHT schützt: einen Angreifer, der die laufende Instanz oder
ihre Umgebungsvariablen lesen kann. Das kann keine Verschlüsselung, die
die App selbst wieder öffnen muss.
"""
import base64
import hashlib
import hmac
import secrets

VERSION = b"v1"
MIN_MASTER = 22          # so lang wie ein `generate: token` mindestens ist


class VaultError(ValueError):
    pass


def usable(master):
    """Taugt dieser Hauptschlüssel? Ein kurzer wäre eine Scheinsicherheit."""
    return bool(master) and len(master) >= MIN_MASTER


def _keys(master):
    root = hashlib.sha256(b"oaap-ai-gateway-vault|" + master.encode("utf-8")).digest()
    return (hmac.new(root, b"enc", hashlib.sha256).digest(),
            hmac.new(root, b"mac", hashlib.sha256).digest())


def _stream(enc_key, nonce, length):
    out, counter = bytearray(), 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


def seal(master, plaintext):
    """Klartext → `v1:<base64>`. Wirft VaultError ohne tauglichen Hauptschlüssel."""
    if not usable(master):
        raise VaultError("AIGW_MASTER_KEY fehlt oder ist kürzer als "
                         f"{MIN_MASTER} Zeichen.")
    enc_key, mac_key = _keys(master)
    nonce = secrets.token_bytes(16)
    data = plaintext.encode("utf-8")
    cipher = bytes(a ^ b for a, b in zip(data, _stream(enc_key, nonce, len(data))))
    tag = hmac.new(mac_key, VERSION + nonce + cipher, hashlib.sha256).digest()
    return VERSION.decode() + ":" + base64.b64encode(nonce + cipher + tag).decode()


def open_(master, token):
    """`v1:<base64>` → Klartext. Wirft VaultError, statt Unsinn zu liefern."""
    if not usable(master):
        raise VaultError("AIGW_MASTER_KEY fehlt oder ist kürzer als "
                         f"{MIN_MASTER} Zeichen.")
    head, sep, body = (token or "").partition(":")
    if not sep or head.encode() != VERSION:
        raise VaultError("unbekanntes Tresorformat")
    try:
        raw = base64.b64decode(body, validate=True)
    except ValueError:
        raise VaultError("Tresorinhalt beschädigt") from None
    if len(raw) < 16 + 32:
        raise VaultError("Tresorinhalt beschädigt")
    nonce, cipher, tag = raw[:16], raw[16:-32], raw[-32:]
    enc_key, mac_key = _keys(master)
    expected = hmac.new(mac_key, VERSION + nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise VaultError("passt nicht zum Hauptschlüssel (anderer AIGW_MASTER_KEY "
                         "oder verändert)")
    return bytes(a ^ b for a, b in zip(cipher, _stream(enc_key, nonce, len(cipher)))).decode("utf-8")
