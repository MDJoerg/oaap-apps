#!/usr/bin/env python3
"""Prüfung eines Anmelde-Tokens (signin.py, Spec §2.2) — ohne Netz, mit einem Ersatz-Issuer.

Run: python3 apps/mcp-gateway/test_signin.py
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import signin  # noqa: E402
import test_rsa_keys as rk  # noqa: E402

ok = fail = 0
ISS, AUD = "https://idp.example/realms/t", "mcp-gw"


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {label}")
    else:
        fail += 1
        print(f"  FAIL  {label} {str(detail)[:300]}")


KEY = rk.keypair()
OTHER = rk.keypair()
SHORT = rk.keypair(1024)
fetches = []


def fetch():
    fetches.append(1)
    return {"keys": [rk.jwk(KEY[0], KEY[1], "k1"), rk.jwk(SHORT[0], SHORT[1], "kurz")]}


def refused(tok, keys=None, now=None):
    try:
        signin.check(tok, keys or signin.Keys(ISS, fetch), ISS, AUD, now=now)
        return None
    except signin.Invalid as exc:
        return str(exc)


keys = signin.Keys(ISS, fetch)
good = rk.token(KEY, rk.claims(ISS, AUD, groups=["/sap-dev"]))
c = signin.check(good, keys, ISS, AUD)
check("gültiges Token: angenommen, sub und Gruppen gelesen", c["sub"] == "person-1"
      and signin.groups_of(c) == {"/sap-dev"})
check("aud als Liste", signin.check(rk.token(KEY, rk.claims(ISS, ["account", AUD])), keys, ISS, AUD))

print("\n-- abgewiesen")
cases = {
    "fremder Schlüssel (gleiche kid)": rk.token(OTHER, rk.claims(ISS, AUD)),
    "alg none": rk.token(KEY, rk.claims(ISS, AUD), alg="none"),
    "alg HS256": rk.token(KEY, rk.claims(ISS, AUD), alg="HS256"),
    "anderer Issuer": rk.token(KEY, rk.claims("https://idp.example/realms/fremd", AUD)),
    "Issuer mit Schrägstrich am Ende": rk.token(KEY, rk.claims(ISS + "/", AUD)),
    "für einen anderen Empfänger (aud account)": rk.token(KEY, rk.claims(ISS, "account")),
    "ohne aud": rk.token(KEY, {k: v for k, v in rk.claims(ISS, AUD).items() if k != "aud"}),
    "abgelaufen": rk.token(KEY, rk.claims(ISS, AUD, lifetime=-60)),
    "nbf in der Zukunft": rk.token(KEY, rk.claims(ISS, AUD, nbf=int(time.time()) + 600)),
    "ohne sub": rk.token(KEY, rk.claims(ISS, AUD, sub="")),
    "Schlüssel mit 1024 Bit": rk.token(SHORT, rk.claims(ISS, AUD), kid="kurz"),
    "kein JWT": "nur.zwei",
    "Unsinn in den Teilen": "a.b.c",
}
for label, tok in cases.items():
    r = refused(tok, keys)
    check(f"{label}: {r}", r is not None)
body = good.split(".")
forged = body[0] + "." + rk.b64(json.dumps(rk.claims(ISS, AUD, sub="admin")).encode()) + "." + body[2]
check("geänderter Inhalt mit alter Unterschrift", refused(forged, keys) is not None)
check("Spielraum 30 s: eben abgelaufen geht noch",
      refused(rk.token(KEY, rk.claims(ISS, AUD, lifetime=-10)), keys) is None)

print("\n-- Schlüssel des Issuers")
fetches.clear()
k2 = signin.Keys(ISS, fetch)
for _ in range(3):
    signin.check(good, k2, ISS, AUD)
check("einmal geholt, dann gemerkt", len(fetches) == 1, fetches)
for _ in range(5):
    refused(rk.token(KEY, rk.claims(ISS, AUD), kid="erfunden"), k2)
check("unbekannte kid in der ersten Minute: kein neuer Abruf", len(fetches) == 1, fetches)
k2._last -= signin.REFETCH_S + 1
for _ in range(5):
    refused(rk.token(KEY, rk.claims(ISS, AUD), kid="erfunden"), k2)
check("eine Minute später: genau ein neuer Abruf, nicht fünf", len(fetches) == 2, fetches)
check("gleicher Ursprung", signin.same_origin("https://a.example/realms/x/certs", "https://a.example/realms/x"))
check("anderer Host", not signin.same_origin("https://evil.example/certs", "https://a.example/realms/x"))
check("anderer Port", not signin.same_origin("https://a.example:8443/c", "https://a.example/realms/x"))
check("http statt https", not signin.same_origin("http://a.example/c", "https://a.example/realms/x"))
check("Port 443 ausdrücklich = Standard", signin.same_origin("https://a.example:443/c", "https://a.example/r"))


class Disco(signin.Keys):
    """Prüft den Weg über das Discovery-Dokument, ohne Netz."""

    def __init__(self, docs):
        super().__init__(ISS)
        self.docs, self.asked = docs, []

    def _get_json(self, url):
        self.asked.append(url)
        return self.docs[url]


d = Disco({ISS + "/.well-known/openid-configuration": {"issuer": ISS, "jwks_uri": "https://evil.example/certs"}})
r = refused(good, d)
check("jwks_uri auf fremdem Ursprung: nicht angefahren", r and "anderen Ursprung" in r
      and d.asked == [ISS + "/.well-known/openid-configuration"], (r, d.asked))
d = Disco({ISS + "/.well-known/openid-configuration": {"issuer": ISS, "jwks_uri": ISS + "/protocol/openid-connect/certs"},
           ISS + "/protocol/openid-connect/certs": fetch()})
check("jwks_uri beim Issuer: angenommen", refused(good, d) is None)
d = Disco({ISS + "/.well-known/openid-configuration": {"issuer": "https://idp.example/realms/fremd",
                                                       "jwks_uri": ISS + "/certs"}})
check("Discovery nennt einen anderen Issuer: abgewiesen", "anderen Issuer" in (refused(good, d) or ""))

print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
