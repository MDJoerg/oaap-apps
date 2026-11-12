"""Kein Geheimnis verlässt den Knoten über den Marketplace (RFC-0077 §15.8.4).

Eine Sperre VOR jedem Speichern: Einchecken, Texte, Links, Profile, ZIP-Inhalte.
Ein Fund ist eine Ablehnung (422), die den ORT und die REGEL nennt -- nie den
Wert. Auch das Ereignisprotokoll bekommt nur Ort und Regel.

Die Sperre arbeitet mit Mustern und ist deshalb unvollständig; das Review
(§15.8.5) ist das zweite Paar Augen.
"""
import json
import re
import zipfile

# Dateinamen: die Kategorien env, vcs, keys, cloud aus RFC-0084
# (oaap-reference/platform/services/guard.py CATEGORIES) -- KOPIE. Der Test
# vergleicht sie mit dem Original, wenn das Gesamt-Repository daneben liegt.
# deploy/php/dumps fehlen absichtlich: ein Paket darf eine docker-compose.yml
# oder Beispieldaten tragen; ein Geheimnis darin finden die Inhaltsmuster.
NAME_CATEGORIES = {
    "env": r"\.env(\.[^/]*)?",
    "vcs": r"\.(git|svn|hg)(/.*)?",
    "keys": r"(id_rsa|id_ecdsa|id_ed25519|id_dsa)(\.pub)?|\.ssh/.*|[^/]*\.(pem|key|p12|pfx)",
    "cloud": (r"\.aws/.*|\.npmrc|\.pypirc|\.netrc|\.docker/config\.json|\.git-credentials"
              r"|\.htpasswd|\.htaccess"),
}
NAME_RE = re.compile("(?i)(^|/)(" + "|".join(NAME_CATEGORIES.values()) + ")$")
# .env.example ist eine Vorlage, kein Geheimnis -- wenn sie keine Werte trägt,
# findet das die Inhaltsprüfung.
NAME_OK = re.compile(r"(?i)(^|/)\.env\.(example|sample|template|dist)$")

PATTERNS = (
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("oaap-api-key", re.compile(r"\boaapk_[A-Za-z0-9]{4,}_[A-Za-z0-9_-]{16,}")),
    ("aws-access-key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws-secret", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{20,}")),
    ("github-token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("gitlab-token", re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("api-key-sk", re.compile(r"\bsk-(ant-|proj-|live_|test_)?[A-Za-z0-9_-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("url-credentials", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]{3,}@[^\s/]+")),
    ("npm-auth", re.compile(r"(?i)_authToken\s*=\s*\S{8,}")),
)

SECRET_KEY = re.compile(r"(?i)(PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|API_?KEY|CREDENTIAL|_KEY$)")
PLACEHOLDER = re.compile(r"^\{[a-z0-9_]+\}$")
TEXT_MEMBER_MAX = 1024 * 1024


class Leak(Exception):
    """Ein Fund: wo und welche Regel -- nie der Wert."""

    def __init__(self, where, rule):
        super().__init__(f"Mögliches Geheimnis in {where} (Regel „{rule}“). Der Inhalt wurde "
                         "nicht gespeichert. Bitte entfernen -- Geheimnisse gehören nie in den "
                         "Marketplace (Profile: generate: true oder ein Parameter).")
        self.where = where
        self.rule = rule


def scan_text(text, where):
    """Wirft Leak beim ersten Muster-Fund."""
    if not text:
        return
    for rule, rx in PATTERNS:
        if rx.search(text):
            raise Leak(where, rule)


def scan_value(v, where):
    """Jeder String in einer verschachtelten Struktur (Texte, Links, JSON)."""
    if isinstance(v, str):
        scan_text(v, where)
    elif isinstance(v, dict):
        for k, x in v.items():
            scan_text(str(k), f"{where}.{k}")
            scan_value(x, f"{where}.{k}")
    elif isinstance(v, list):
        for i, x in enumerate(v):
            scan_value(x, f"{where}[{i}]")


def check_name(name, where="Dateiname"):
    if NAME_RE.search(name or "") and not NAME_OK.search(name or ""):
        raise Leak(f"{where} „{_safe_name(name)}“", "Geheimnisdatei (RFC-0084)")


def _safe_name(name):
    return "".join(c for c in (name or "") if c.isprintable())[:120]


def scan_zip(path):
    """Mitglieder eines ZIP: Namen und Textinhalte (bis 1 MB je Datei)."""
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            check_name(info.filename, "Paketdatei")
            if info.file_size > TEXT_MEMBER_MAX:
                continue
            data = z.read(info)
            if b"\x00" in data[:4096]:
                continue                              # binär: nur der Name zählt
            scan_text(data.decode("utf-8", "replace"), f"Paketdatei „{_safe_name(info.filename)}“")


def scan_profile(doc):
    """Ein Aufbau-Profil (oaap.tenant-profile/1): Muster überall, dazu zwei Regeln:
    kein Parameter der Art secret, kein wörtlicher Wert für einen geheimen Schlüssel."""
    scan_value(doc, "Profil")
    for name, p in (doc.get("params") or {}).items():
        if isinstance(p, dict) and str(p.get("kind") or "").lower() == "secret":
            raise Leak(f"Profil.params.{name}", "Parameter der Art secret")
    for i, s in enumerate(doc.get("steps") or []):
        if not isinstance(s, dict) or s.get("type") != "app.config":
            continue
        key, val = str(s.get("key") or ""), s.get("value")
        if not SECRET_KEY.search(key) or s.get("generate") is True:
            continue
        if val in (None, "") or (isinstance(val, str) and PLACEHOLDER.match(val.strip())):
            continue
        raise Leak(f"Profil.steps[{i}] ({s.get('id') or key})",
                   "wörtlicher Wert für einen geheimen Schlüssel")


def scan_json_bytes(data, where):
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        scan_text(data.decode("utf-8", "replace"), where)
        return
    scan_value(doc, where)
