"""Zugangsdaten verschlüsselt unter /data (RFC-0064 D2, RFC-0065 §2).

Wozu: Ein Gateway hält die Zugangsdaten seiner Gegenstellen in seiner
Datenbank. Eine Sicherung von `/data` allein soll sie nicht preisgeben.
Der Schlüssel zum Tresor kommt deshalb aus einer **anderen** Quelle:
einer geheimen Instanz-Variablen (im Portal per Knopf erzeugbar,
`generate: token`).

Womit: nur Standardbibliothek, und die hat kein AES. Gebaut ist deshalb
die bekannte Form „Encrypt-then-MAC“ aus HMAC-SHA256 allein:

- Schlüsselstrom = HMAC(enc_key, nonce ‖ zähler) — HMAC als
  Pseudozufallsfunktion im Zählermodus, ein frischer 16-Byte-Nonce je
  Verschlüsselung;
- Prüfsumme = HMAC(mac_key, version ‖ nonce ‖ geheimtext), verglichen in
  konstanter Zeit, **bevor** irgendetwas entschlüsselt wird.

`enc_key` und `mac_key` sind getrennt aus dem Hauptschlüssel abgeleitet,
und zwar mit einem **Zweck je App** (`domain`): Derselbe Hauptschlüssel
in zwei Gateways ergäbe trotzdem zwei Tresore. Ein falscher
Hauptschlüssel, ein verändertes Byte oder ein Text aus einer anderen
Instanz ergeben keinen Unsinn, sondern einen benannten Fehler.

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


def _stream(enc_key, nonce, length):
    out, counter = bytearray(), 0
    while len(out) < length:
        out += hmac.new(enc_key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        counter += 1
    return bytes(out[:length])


class Vault:
    """Ein Tresor. `domain` trennt die Apps, `env` nennt die Variable in Meldungen.

    Die Ableitung ist Teil des gespeicherten Formats: Wer `domain` einer
    bestehenden App ändert, macht alle gespeicherten Zugangsdaten
    unlesbar.
    """

    def __init__(self, domain, env):
        self.domain = domain if isinstance(domain, bytes) else domain.encode("utf-8")
        self.env = env

    def usable(self, master):
        """Taugt dieser Hauptschlüssel? Ein kurzer wäre eine Scheinsicherheit."""
        return bool(master) and len(master) >= MIN_MASTER

    def _keys(self, master):
        root = hashlib.sha256(self.domain + master.encode("utf-8")).digest()
        return (hmac.new(root, b"enc", hashlib.sha256).digest(),
                hmac.new(root, b"mac", hashlib.sha256).digest())

    def _require(self, master):
        if not self.usable(master):
            raise VaultError(f"{self.env} fehlt oder ist kürzer als "
                             f"{MIN_MASTER} Zeichen.")

    def seal(self, master, plaintext):
        """Klartext → `v1:<base64>`. Wirft VaultError ohne tauglichen Hauptschlüssel."""
        self._require(master)
        enc_key, mac_key = self._keys(master)
        nonce = secrets.token_bytes(16)
        data = plaintext.encode("utf-8")
        cipher = bytes(a ^ b for a, b in zip(data, _stream(enc_key, nonce, len(data))))
        tag = hmac.new(mac_key, VERSION + nonce + cipher, hashlib.sha256).digest()
        return VERSION.decode() + ":" + base64.b64encode(nonce + cipher + tag).decode()

    def open_(self, master, token):
        """`v1:<base64>` → Klartext. Wirft VaultError, statt Unsinn zu liefern."""
        self._require(master)
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
        enc_key, mac_key = self._keys(master)
        expected = hmac.new(mac_key, VERSION + nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise VaultError(f"passt nicht zum Hauptschlüssel (anderer {self.env} "
                             "oder verändert)")
        return bytes(a ^ b for a, b in zip(cipher, _stream(enc_key, nonce, len(cipher)))).decode("utf-8")
