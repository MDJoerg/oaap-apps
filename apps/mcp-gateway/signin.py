"""Anmeldung eines Menschen: ein Token des Anmeldediensts prüfen (Spec §2.2, RFC-0065 §15).

Das Gateway ist eine geschützte Ressource genau **eines** Issuers — des
Anmeldedienstes des Mandanten, den der Betreiber einträgt (D14). Ein
Token wird hier vollständig geprüft, in dieser Reihenfolge:

1. Form: drei Teile, Kopf mit `alg` = `RS256` und `kid`. Kein anderes
   Verfahren — nie `none`, nie ein symmetrisches (dessen Schlüssel wäre
   ein Geheimnis, das wir gar nicht haben dürften).
2. Unterschrift gegen den Schlüssel des Issuers mit dieser `kid`,
   mindestens 2048 Bit.
3. `iss` genau gleich, `aud` enthält unsere Kennung (RFC 8707),
   `exp`/`nbf`/`iat` mit 30 Sekunden Spielraum, `sub` vorhanden.

RS256 von Hand, mit `pow()`: Die Standardbibliothek kennt kein RSA, und
für das **Prüfen** einer Unterschrift (PKCS #1 v1.5, RFC 8017 §8.2.2)
genügt eine Potenz und ein Vergleich mit der erwarteten Kodierung.
Geheimnisse sind dabei keine im Spiel — es wird nur geprüft, nie
unterschrieben.

Die Schlüssel des Issuers kommen aus seinem Discovery-Dokument; die
dort genannte `jwks_uri` wird **nur** angefahren, wenn sie denselben
Ursprung hat wie der Issuer (A4: nie eine Adresse anfahren, die erst
eine Antwort nennt).

Kein Token und kein Teil davon landet in einer Meldung, einem Protokoll
oder einer Messzeile (Spec §10).
"""
import base64
import hashlib
import hmac
import json
import threading
import time
from urllib.parse import urlsplit

import upstream

LEEWAY = 30
MIN_BITS = 2048
REFETCH_S = 60
# DigestInfo für SHA-256 (RFC 8017 §9.2, Anmerkung 1).
SHA256_PREFIX = bytes.fromhex("3031300d060960864801650304020105000420")


class Invalid(Exception):
    """Das Token taugt nicht. Der Grund ist für Betreiber und Tests — nie für die Antwort (Spec §2)."""


def b64(part):
    part = part + "=" * (-len(part) % 4)
    return base64.urlsafe_b64decode(part.encode("ascii"))


def b64int(part):
    return int.from_bytes(b64(part), "big")


def looks_like_token(value):
    return value.count(".") == 2 and not value.startswith("mcp-oaap-")


def verify_rs256(n, e, signed, signature):
    """RSASSA-PKCS1-v1_5 mit SHA-256 prüfen. `n`, `e` als Zahlen, `signed` und `signature` als Bytes."""
    k = (n.bit_length() + 7) // 8
    if n.bit_length() < MIN_BITS:
        raise Invalid("Schlüssel kürzer als 2048 Bit")
    if len(signature) != k:
        raise Invalid("Unterschrift hat die falsche Länge")
    s = int.from_bytes(signature, "big")
    if s >= n:
        raise Invalid("Unterschrift außerhalb des Schlüssels")
    em = pow(s, e, n).to_bytes(k, "big")
    t = SHA256_PREFIX + hashlib.sha256(signed).digest()
    if k < len(t) + 11:
        raise Invalid("Schlüssel zu kurz")
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    if not hmac.compare_digest(em, expected):
        raise Invalid("Unterschrift stimmt nicht")


def same_origin(a, b):
    pa, pb = urlsplit(a), urlsplit(b)
    port = lambda p: p.port or (443 if p.scheme == "https" else 80)  # noqa: E731
    return (pa.scheme, (pa.hostname or "").lower(), port(pa)) == \
        (pb.scheme, (pb.hostname or "").lower(), port(pb))


class Keys:
    """Die Schlüssel eines Issuers: einmal geholt, gemerkt, bei unbekannter `kid` höchstens einmal je Minute neu."""

    def __init__(self, issuer, fetch=None):
        self.issuer = issuer.rstrip("/")
        self._fetch = fetch or self._fetch_http
        self._keys = {}
        self._last = 0.0
        self._lock = threading.Lock()
        self.error = ""

    def _get_json(self, url):
        status, body = upstream.plain("GET", url, headers={"Accept": "application/json"})
        if status != 200:
            raise Invalid(f"{url} antwortet mit HTTP {status}")
        try:
            return json.loads(body)
        except ValueError:
            raise Invalid(f"{url} liefert kein JSON") from None

    def _fetch_http(self):
        disco = self._get_json(self.issuer + "/.well-known/openid-configuration")
        if disco.get("issuer", "").rstrip("/") != self.issuer:
            raise Invalid("Das Discovery-Dokument nennt einen anderen Issuer")
        jwks_uri = disco.get("jwks_uri") or ""
        if not same_origin(jwks_uri, self.issuer):
            raise Invalid(f"jwks_uri liegt auf einem anderen Ursprung als der Issuer ({jwks_uri}); "
                          "das Gateway fährt sie nicht an")
        return self._get_json(jwks_uri)

    def get(self, kid):
        with self._lock:
            if kid in self._keys:
                return self._keys[kid]
            # Höchstens ein Abruf je Minute — auch wenn noch nichts geladen ist: Erfundene
            # Tokens sollen den Anmeldedienst nicht über das Gateway mit Anfragen überziehen.
            if self._last and time.monotonic() - self._last < REFETCH_S:
                raise Invalid("unbekannte kid" if self._keys else
                              f"Schlüssel des Issuers nicht lesbar: {self.error or 'gerade versucht'}")
            self._last = time.monotonic()
            try:
                doc = self._fetch()
            except (Invalid, upstream.UpstreamError) as exc:
                self.error = str(exc)
                raise Invalid(f"Schlüssel des Issuers nicht lesbar: {exc}") from None
            found = {}
            for jwk in doc.get("keys") or ():
                if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and jwk.get("kid") \
                        and jwk.get("use", "sig") == "sig" and jwk.get("n") and jwk.get("e"):
                    found[jwk["kid"]] = (b64int(jwk["n"]), b64int(jwk["e"]))
            self._keys = found
            self.error = ""
            if kid not in found:
                raise Invalid("unbekannte kid")
            return found[kid]


def check(token, keys, issuer, audience, now=None):
    """Prüft ein Token vollständig. Gibt die Claims zurück; wirft Invalid."""
    now = time.time() if now is None else now
    try:
        head_b, body_b, sig_b = token.split(".")
        head = json.loads(b64(head_b))
        claims = json.loads(b64(body_b))
        signature = b64(sig_b)
    except (ValueError, TypeError):
        raise Invalid("kein JWT") from None
    if not isinstance(head, dict) or not isinstance(claims, dict):
        raise Invalid("kein JWT")
    if head.get("alg") != "RS256":
        raise Invalid(f"Verfahren {head.get('alg')!r} wird nicht angenommen")
    if not isinstance(head.get("kid"), str):
        raise Invalid("keine kid")
    n, e = keys.get(head["kid"])
    verify_rs256(n, e, f"{head_b}.{body_b}".encode("ascii"), signature)
    if claims.get("iss") != issuer:
        raise Invalid("anderer Issuer")
    aud = claims.get("aud")
    auds = aud if isinstance(aud, list) else [aud]
    if not audience or audience not in auds:
        raise Invalid("nicht an dieses Gateway adressiert")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + LEEWAY <= now:
        raise Invalid("abgelaufen")
    for field in ("nbf", "iat"):
        value = claims.get(field)
        if isinstance(value, (int, float)) and value - LEEWAY > now:
            raise Invalid(f"{field} liegt in der Zukunft")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]:
        raise Invalid("kein sub")
    return claims


def exchange(token_url, client_id, secret, subject_token, audience, timeout=15):
    """Verfahren X (Spec §3.5, RFC 8693): das Token des Menschen gegen eines für `audience` tauschen.

    Gibt (token, lebensdauer_s) zurück; wirft Invalid (abgelehnt) oder
    UpstreamError (nicht erreichbar). Weder das alte noch das neue Token
    noch die Antwort des Anmeldedienstes kommen in eine Meldung.
    """
    from urllib.parse import urlencode
    body = urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                      "subject_token": subject_token,
                      "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
                      "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
                      "audience": audience}).encode("ascii")
    basic = base64.b64encode(f"{client_id}:{secret}".encode("utf-8")).decode("ascii")
    status, raw = upstream.plain("POST", token_url, body=body, timeout=timeout, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
        "Authorization": "Basic " + basic, "Content-Length": str(len(body))})
    try:
        doc = json.loads(raw)
    except ValueError:
        doc = {}
    if status != 200 or not isinstance(doc.get("access_token"), str):
        # Nur der Fehlercode des Anmeldediensts (RFC 6749 §5.2), nie sein Text: Er könnte
        # Teile der Anfrage wiederholen.
        code = doc.get("error") if isinstance(doc.get("error"), str) else ""
        raise Invalid(f"HTTP {status}" + (f", {code[:40]}" if code else ""))
    lifetime = doc.get("expires_in")
    return doc["access_token"], int(lifetime) if isinstance(lifetime, (int, float)) else 60


def groups_of(claims):
    found = claims.get("groups")
    return {g for g in found if isinstance(g, str)} if isinstance(found, list) else set()
