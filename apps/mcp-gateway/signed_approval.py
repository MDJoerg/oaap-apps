"""Unterschriebene Freigaben (RFC-0105 §7.1, mcp-gateway 0.39.0).

Eine Sicht mit `approval = signed` nimmt den schreibenden Aufruf eines Schlüssels nur mit
einer Freigabe an, die eine Vorgangs-App mit dem **Freigabeschlüssel** dieser Sicht
unterschrieben hat (HMAC-SHA256). Das Gateway prüft selbst: Unterschrift, Werkzeug, die
Argumente (SHA-256), Ablauf, und dass jede Freigabe genau einmal benutzt wird. Wer nur den
API-Schlüssel hat, kann sich keine Freigabe ausstellen -- dafür braucht es den
Freigabeschlüssel, und den hält die Vorgangs-App.

Format (Kopfzeile `X-OAAP-Approval`):

    oaap-approval.v1.<payload>.<mac>

`payload` = base64url (ohne `=`) eines JSON-Objekts
{"v": 1, "iss", "case", "plan", "tool", "args", "by", "iat", "exp", "jti"};
`mac` = base64url(HMAC-SHA256(Schlüssel, "oaap-approval.v1." + payload)).
`args` = SHA-256 (hex) der Argumente, kanonisch: JSON mit sortierten Schlüsseln, ohne
Leerzeichen, UTF-8 (`ensure_ascii=False`). Der Schlüssel: `oaap-fk-` + 64 Hex-Zeichen.
"""
import base64
import hashlib
import hmac
import json
import re
import time

PREFIX = "oaap-approval.v1."
HEADER = "X-OAAP-Approval"
KEY_RE = re.compile(r"^oaap-fk-[0-9a-f]{64}$")
TOKEN_MAX = 4096
TTL_MAX = 30 * 24 * 3600          # länger gilt keine Freigabe, was immer sie sagt


class Invalid(Exception):
    """Die Freigabe trägt nicht -- mit einem Satz."""


def canonical_args(arguments):
    return json.dumps(arguments if isinstance(arguments, dict) else {}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))


def args_hash(arguments):
    return hashlib.sha256(canonical_args(arguments).encode("utf-8")).hexdigest()


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(key, payload):
    """Für Tests und als Vorlage der Vorgangs-App: eine Freigabe ausstellen."""
    body = _b64(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":")).encode("utf-8"))
    mac = hmac.new(key.encode(), (PREFIX + body).encode(), hashlib.sha256).digest()
    return PREFIX + body + "." + _b64(mac)


def verify(key, token, tool, arguments, now=None):
    """Gibt die Angaben der Freigabe zurück oder wirft Invalid. Prüft nicht, ob sie schon
    benutzt wurde -- das hält die Datenbank (jti)."""
    now = time.time() if now is None else now
    if not key or not KEY_RE.match(key):
        raise Invalid("Für diese Sicht ist kein Freigabeschlüssel hinterlegt.")
    token = (token or "").strip()
    if not token:
        raise Invalid("Es fehlt eine unterschriebene Freigabe (X-OAAP-Approval).")
    if len(token) > TOKEN_MAX or not token.startswith(PREFIX) or token.count(".") != 3:
        raise Invalid("Die Freigabe hat nicht die Form oaap-approval.v1.<angaben>.<unterschrift>.")
    body, mac = token[len(PREFIX):].split(".")
    want = hmac.new(key.encode(), (PREFIX + body).encode(), hashlib.sha256).digest()
    try:
        got = _unb64(mac)
    except (ValueError, TypeError):
        got = b""
    if not hmac.compare_digest(want, got):
        raise Invalid("Die Unterschrift der Freigabe stimmt nicht (anderer Freigabeschlüssel oder verändert).")
    try:
        p = json.loads(_unb64(body).decode("utf-8"))
    except (ValueError, TypeError, UnicodeDecodeError):
        raise Invalid("Die Angaben der Freigabe sind kein JSON.") from None
    if not isinstance(p, dict) or p.get("v") != 1:
        raise Invalid("Die Freigabe hat eine unbekannte Fassung.")
    for f in ("tool", "args", "by", "jti"):
        if not isinstance(p.get(f), str) or not p[f]:
            raise Invalid(f"Der Freigabe fehlt „{f}“.")
    iat, exp = p.get("iat"), p.get("exp")
    if not isinstance(iat, (int, float)) or not isinstance(exp, (int, float)):
        raise Invalid("Der Freigabe fehlen Ausstellung und Ablauf.")
    if exp < now or exp - iat > TTL_MAX or iat > now + 300:
        raise Invalid("Die Freigabe ist abgelaufen oder nicht gültig datiert.")
    if p["tool"] != tool:
        raise Invalid(f"Die Freigabe gilt für „{p['tool'][:60]}“, nicht für „{tool[:60]}“.")
    if p["args"] != args_hash(arguments):
        raise Invalid("Die Argumente sind nicht die freigegebenen.")
    if len(p["jti"]) > 200 or len(p["by"]) > 200:
        raise Invalid("Die Freigabe ist zu lang.")
    return p
