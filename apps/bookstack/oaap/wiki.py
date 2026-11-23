"""Der einzige Ort im Paket, der BookStack spricht (RFC-0110 §3).

Zwei Türen, und nur diese:

- **REST-API** (`http://wiki/api/...`) für alles, was BookStack über seine
  API anbietet: Rollen, Benutzer, Version.
- **Datenbank**, und zwar nur für das, was die API nicht kann (D3):
  ein Token für ein Konto anlegen und wegräumen (`api_tokens`), und lesen,
  ob das Standardkonto noch das bekannte Kennwort hat (`users`).

Bevor die Datenbank-Tür benutzt wird, prüft `check_schema` die Spalten, die
sie schreibt oder liest (§3.3). Weichen sie ab, verweigert der Dienst mit
einem Satz, der die Tabelle nennt, statt falsch zu schreiben.
"""
import datetime
import json
import os
import secrets
import threading
import urllib.error
import urllib.request

# Die Spalten, die diese Datei schreibt oder liest -- genau diese, nicht mehr.
SCHEMA = {
    "api_tokens": {"name", "token_id", "secret", "user_id", "expires_at", "created_at", "updated_at"},
    "users": {"id", "email", "password"},
}
TOKEN_PREFIX = "oaap-"
ADAPTER_EMAIL = "adapter@oaap.invalid"
ADAPTER_NAME = "OAAP (Plattform)"
DEFAULT_ADMIN_EMAIL = "admin@admin.com"
DEFAULT_ADMIN_PASSWORD = b"password"


class WikiError(Exception):
    """Ein Satz für Menschen: was nicht ging und warum."""


def _bcrypt_hash(secret):
    import bcrypt
    h = bcrypt.hashpw(secret.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")
    # Laravel prüft das Präfix: $2y$ ist dasselbe Verfahren wie $2b$.
    return "$2y$" + h[4:] if h.startswith("$2b$") else h


def _bcrypt_check(secret, hashed):
    import bcrypt
    if not hashed:
        return False
    h = hashed
    if h.startswith("$2y$"):
        h = "$2b$" + h[4:]
    try:
        return bcrypt.checkpw(secret, h.encode("ascii"))
    except ValueError:
        return False


class Wiki:
    def __init__(self, env=None):
        env = env if env is not None else os.environ
        self.base = (env.get("OAAP_WIKI_INTERNAL_URL") or "http://wiki").rstrip("/")
        self.db_args = dict(
            host=env.get("OAAP_WIKI_DB_HOST") or "db", port=3306,
            user=env.get("MARIADB_USER") or "bookstack",
            password=env.get("MARIADB_PASSWORD") or "",
            database=env.get("MARIADB_DATABASE") or "bookstack",
            connect_timeout=5, read_timeout=15, write_timeout=15, charset="utf8mb4",
        )
        self.lock = threading.RLock()
        self.schema_problem = None   # None = geprüft und gut; Text = was abweicht
        self.schema_checked = False
        self.adapter_id = None
        self.token = None            # "id:secret", nur im Speicher
        self.token_expires = None

    # --- Datenbank -----------------------------------------------------------
    def _db(self):
        import pymysql
        return pymysql.connect(**self.db_args)

    def check_schema(self):
        """§3.3: die Spalten, die wir anfassen, gibt es noch? Setzt schema_problem."""
        problems = []
        con = self._db()
        try:
            with con.cursor() as cur:
                for table, need in SCHEMA.items():
                    cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                                "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                                (self.db_args["database"], table))
                    have = {r[0] for r in cur.fetchall()}
                    if not have:
                        problems.append(f"Tabelle {table} fehlt")
                    elif need - have:
                        problems.append(f"Tabelle {table}: Spalten {', '.join(sorted(need - have))} fehlen")
        finally:
            con.close()
        self.schema_checked = True
        self.schema_problem = ("BookStacks Tabellen sind anders als erwartet ("
                               + "; ".join(problems) + ") -- der Dienst legt keine Tokens an, "
                               "bis ein Paket mit dieser BookStack-Fassung gemessen ist.") if problems else None
        return self.schema_problem

    def _require_schema(self):
        if not self.schema_checked:
            self.check_schema()
        if self.schema_problem:
            raise WikiError(self.schema_problem)

    def user_id_by_email(self, email):
        self._require_schema()
        con = self._db()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE email=%s", (email,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            con.close()

    def default_admin_unchanged(self):
        """(user_id, True) wenn das Standardkonto noch das bekannte Kennwort hat."""
        self._require_schema()
        con = self._db()
        try:
            with con.cursor() as cur:
                cur.execute("SELECT id, password FROM users WHERE email=%s", (DEFAULT_ADMIN_EMAIL,))
                row = cur.fetchone()
        finally:
            con.close()
        if not row:
            return None, False
        return row[0], _bcrypt_check(DEFAULT_ADMIN_PASSWORD, row[1])

    def mint_token(self, user_id, label, days=2):
        """Ein Token für dieses Konto (D3). Gibt "id:secret" zurück; nur im Speicher halten."""
        self._require_schema()
        tid = secrets.token_hex(16)
        sec = secrets.token_hex(24)
        expires = datetime.date.today() + datetime.timedelta(days=days)
        con = self._db()
        try:
            with con.cursor() as cur:
                cur.execute("INSERT INTO api_tokens (name, token_id, secret, user_id, expires_at, "
                            "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, NOW(), NOW())",
                            (TOKEN_PREFIX + label, tid, _bcrypt_hash(sec), user_id, expires))
            con.commit()
        finally:
            con.close()
        return f"{tid}:{sec}", expires

    def drop_tokens(self, label=None):
        """Räumt die Tokens weg, die dieser Dienst angelegt hat (§3.1)."""
        self._require_schema()
        con = self._db()
        try:
            with con.cursor() as cur:
                if label:
                    cur.execute("DELETE FROM api_tokens WHERE name=%s", (TOKEN_PREFIX + label,))
                else:
                    cur.execute("DELETE FROM api_tokens WHERE name LIKE %s", (TOKEN_PREFIX + "%",))
                n = cur.rowcount
            con.commit()
            return n
        finally:
            con.close()

    # --- REST ------------------------------------------------------------------
    def _call(self, method, path, body=None, token=None):
        tok = token or self.token
        if not tok:
            raise WikiError("der Dienst hat noch kein Token für BookStack")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": f"Token {tok}", "Accept": "application/json",
            **({"Content-Type": "application/json"} if data is not None else {})})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                doc = json.loads(raw)
            except ValueError:
                doc = {"raw": raw[:300].decode("utf-8", "replace")}
            return e.code, doc

    def api(self, method, path, body=None):
        with self.lock:
            self.ensure_token()
            st, doc = self._call(method, path, body)
            if st == 401:   # Token weg (z. B. Datenbank zurückgespielt): einmal neu
                self.token = None
                self.ensure_token()
                st, doc = self._call(method, path, body)
            return st, doc

    def ping(self):
        """Antwortet BookStack überhaupt? (/status braucht kein Token.)"""
        try:
            with urllib.request.urlopen(self.base + "/status", timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    # --- das eigene Konto ----------------------------------------------------
    def ensure_adapter(self, admin_role_id_hint=None):
        """Das Konto, mit dem dieser Dienst BookStack verwaltet: anlegen, falls es fehlt.

        Beim allerersten Start gibt es nur das Standardkonto. Mit einem
        kurzen Token für dieses Konto legt der Dienst sein eigenes an (über
        die API) und räumt das Token sofort wieder weg.
        """
        uid = self.user_id_by_email(ADAPTER_EMAIL)
        if uid:
            self.adapter_id = uid
            return uid, False
        boot_uid = self.user_id_by_email(DEFAULT_ADMIN_EMAIL)
        if not boot_uid:
            raise WikiError("weder das Konto des Dienstes noch BookStacks Standardkonto "
                            f"({DEFAULT_ADMIN_EMAIL}) gibt es -- der Dienst kann sich kein Konto anlegen")
        boot, _ = self.mint_token(boot_uid, "bootstrap", days=1)
        try:
            st, roles = self._call("GET", "/api/roles?count=500", token=boot)
            if st != 200:
                raise WikiError(f"Rollen lesen ging nicht ({st})")
            admin = [r["id"] for r in roles.get("data", []) if r.get("system_name") == "admin"]
            if not admin:
                raise WikiError("BookStack hat keine Rolle mit system_name admin")
            st, doc = self._call("POST", "/api/users", {
                "name": ADAPTER_NAME, "email": ADAPTER_EMAIL, "roles": admin,
                "password": secrets.token_urlsafe(32), "send_invite": False}, token=boot)
            if st != 200:
                raise WikiError(f"eigenes Konto anlegen ging nicht ({st}: {doc})")
            self.adapter_id = doc["id"]
            return self.adapter_id, True
        finally:
            self.drop_tokens("bootstrap")

    def ensure_token(self):
        with self.lock:
            # Gültig bis Tagesende von expires_at (eine DATE-Spalte): erneuern,
            # sobald weniger als ein voller Tag bleibt.
            today = datetime.date.today()
            if self.token and self.token_expires and (self.token_expires - today).days >= 1:
                return self.token
            if not self.adapter_id:
                self.ensure_adapter()
            self.drop_tokens("adapter")
            self.token, self.token_expires = self.mint_token(self.adapter_id, "adapter", days=2)
            return self.token
