"""Der Ablagen-Konnektor für BookStack (oaap.work.stores 0.1, RFC-0121 §3).

Ein Buch ist eine Ablage, ein Regal ein Bereich, ein Kapitel ein Ordner
(eine Ebene), eine Seite ein Dokument. Jeder Aufruf läuft **als die Person**:

1. Die Plattform nennt die Person mit `X-OAAP-User-Idp` ("<issuer> <sub>",
   oaap.core.identity 2.12). Das BookStack-Konto mit `external_auth_id` =
   `sub` ist diese Person -- BookStack hat es bei ihrer ersten Anmeldung
   über den Realm angelegt. Ein anderer Aussteller als der des Wikis zählt
   nicht.
2. Für dieses Konto legt der Dienst ein kurzes Token an (`act_as`, RFC-0110
   §3.1), nur im Speicher, und ruft BookStacks API damit auf. BookStack
   allein entscheidet, was die Person sieht und ändern darf.

Es gibt keinen technischen Benutzer für Inhalte (RFC-0121 §8.2): ohne Person
kein Aufruf. Pfade sind `/kapitel/seite` mit den Slugs, die BookStack in
seinen Adressen zeigt.
"""
import datetime
import json
import re
import threading

KIND = "bookstack"
PROFILE = "oaap.work.stores/1"
ABOUT = {
    "kind": KIND,
    "profile": PROFILE,
    "levels": {"area": "Regal", "store": "Buch", "folder": "Kapitel", "document": "Seite"},
    "revisions": True,          # revision_count der Seite
    "history": False,
    "folders": "one",           # Kapitel, keine Unterkapitel
    "move": False,
    "remove": False,
    "create_store": True,
    "binary": "none",           # Bilder und Anhänge zeigt Stufe 1 nicht
    "propose": False,
    "sections": False,          # die Ablagetür des Gateways schneidet Abschnitte
    "may": "read",              # BookStack beantwortet nur „lesen“ ohne Probe
    "identity": "assertion",
    "origin": "author",         # BookStack hält den Autor, keinen Vermerk je Änderung
}
SEG = re.compile(r"^[a-z0-9][a-z0-9-]{0,190}$")
MAX_TEXT = 512 * 1024


class Refused(Exception):
    """Ein Satz für die Person; geht als Ergebnis mit isError zurück."""


def split_path(path):
    """'/a/b' -> ['a', 'b']; '/' -> []. Keine '..', keine versteckten Namen."""
    p = str(path or "/").strip()
    if not p.startswith("/"):
        p = "/" + p
    parts = [x for x in p.split("/") if x != ""]
    for x in parts:
        if x in (".", "..") or x.startswith(".") or not SEG.match(x):
            raise Refused(f"Pfad {p}: „{x}“ ist kein Name, den BookStack in Adressen zeigt "
                          "(kleine Buchstaben, Ziffern, Bindestrich).")
    if len(parts) > 2:
        raise Refused(f"Pfad {p}: BookStack hat nur eine Ebene Kapitel -- /kapitel/seite.")
    return parts


def no_api(st, doc):
    """BookStack sagt 403 auch dann, wenn die Rolle die API gar nicht benutzen darf -- das ist
    etwas anderes als „nicht sichtbar“ und bekommt einen eigenen Satz (gemessen 23.11.)."""
    msg = json.dumps(doc or {})[:600].lower()
    if st == 403 and "permission to make api calls" in msg:
        raise Refused("Deine Rolle im Wiki hat das Recht „Zugriff auf System-API“ nicht. Ohne es "
                      "kann die Plattform nicht als du im Wiki arbeiten; die Wiki-Verwaltung setzt es "
                      "an der Rolle (Einstellungen → Rollen). Es öffnet nur den Weg, keine Seite.")


def slugify(name):
    s = str(name or "").strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:190]


class Connector:
    """Spricht BookStack als eine Person. `wiki` ist wiki.Wiki."""

    def __init__(self, wiki, env):
        self.wiki = wiki
        self.issuer = (env.get("OAAP_OIDC_ISSUER") or "").rstrip("/")
        self.tokens = {}          # user_id -> (token, expires)
        self.lock = threading.Lock()

    # --- wer -------------------------------------------------------------------
    def user_of(self, who):
        idp = (who.get("idp") or "").strip()
        if not who.get("user") or not idp:
            raise Refused("Ohne Person kein Aufruf: die Plattform hat keine Anmeldung am Realm "
                          "genannt (X-OAAP-User-Idp fehlt).")
        iss, _, sub = idp.rpartition(" ")
        if not sub:
            raise Refused("Die Anmeldung der Person ist unvollständig (kein sub).")
        if self.issuer and iss.rstrip("/") != self.issuer:
            raise Refused("Die Person ist an einem anderen Realm angemeldet als das Wiki -- "
                          "ihr Konto hier ist nicht bekannt.")
        uid = self.wiki.user_id_by_external(sub)
        if not uid:
            raise Refused("Du hast noch kein Konto im Wiki. Melde dich einmal im Wiki an, "
                          "dann geht es auch hier.")
        return uid

    def token_for(self, uid):
        with self.lock:
            today = datetime.date.today()
            t = self.tokens.get(uid)
            if t and (t[1] - today).days >= 1:
                return t[0]
            label = f"p{uid}"
            self.wiki.drop_tokens(label)
            tok, exp = self.wiki.mint_token(uid, label, days=2)
            self.tokens[uid] = (tok, exp)
            return tok

    def call(self, who, method, path, body=None):
        uid = self.user_of(who)
        tok = self.token_for(uid)
        st, doc = self.wiki._call(method, path, body, token=tok)
        if st == 401:   # Token weg (Datenbank zurückgespielt?): einmal neu
            with self.lock:
                self.tokens.pop(uid, None)
            st, doc = self.wiki._call(method, path, body, token=self.token_for(uid))
        no_api(st, doc)
        return st, doc

    def get(self, who, path, what):
        st, doc = self.call(who, "GET", path)
        if st in (403, 404):
            raise Refused(f"{what}: gibt es nicht oder ist für dich nicht sichtbar.")
        if st != 200:
            raise Refused(f"{what}: BookStack antwortet {st}.")
        return doc

    # --- Auflösen --------------------------------------------------------------
    def book(self, who, store):
        try:
            bid = int(str(store).strip())
        except ValueError:
            raise Refused(f"Ablage „{store}“: die Kennung eines Buchs ist eine Zahl.")
        return self.get(who, f"/api/books/{bid}", f"Buch {bid}")

    @staticmethod
    def contents(book):
        """(kapitel, seiten_ohne_kapitel) aus dem Inhalt eines Buchs; ohne Entwürfe."""
        chapters, pages = [], []
        for c in book.get("contents") or []:
            if c.get("type") == "chapter":
                chapters.append(c)
            elif c.get("type") == "page" and not c.get("draft"):
                pages.append(c)
        return chapters, pages

    def resolve(self, who, store, path):
        """-> dict(book, chapter, page, parts); chapter/page None, wenn nicht da."""
        parts = split_path(path)
        book = self.book(who, store)
        chapters, pages = self.contents(book)
        out = {"book": book, "chapter": None, "page": None, "parts": parts}
        if not parts:
            return out
        if len(parts) == 1:
            out["chapter"] = next((c for c in chapters if c.get("slug") == parts[0]), None)
            out["page"] = next((p for p in pages if p.get("slug") == parts[0]), None)
            return out
        ch = next((c for c in chapters if c.get("slug") == parts[0]), None)
        if not ch:
            raise Refused(f"Kapitel /{parts[0]} gibt es in diesem Buch nicht (oder nicht für dich).")
        out["chapter"] = ch
        cpages = ch.get("pages")
        if cpages is None:
            cpages = (self.get(who, f"/api/chapters/{ch['id']}", "Kapitel").get("pages") or [])
        out["page"] = next((p for p in cpages if p.get("slug") == parts[1] and not p.get("draft")), None)
        return out

    def address(self, book, chapter=None, page=None):
        p = "/"
        if chapter:
            p += chapter["slug"]
            if page:
                p += "/" + page["slug"]
        elif page:
            p += page["slug"]
        return p

    # --- Verben ----------------------------------------------------------------
    def about(self, who):
        st, sysdoc = self.wiki.api("GET", "/api/system")
        return {**ABOUT, "product": "BookStack",
                "version": (sysdoc or {}).get("version") if st == 200 else None}

    def areas(self, who):
        doc = self.get(who, "/api/shelves?count=500", "Regale")
        return {"areas": [{"id": str(s["id"]), "title": s.get("name"), "slug": s.get("slug")}
                          for s in doc.get("data") or []]}

    def stores(self, who, area=None, offset=0):
        offset = max(0, int(offset or 0))
        if area:
            shelf = self.get(who, f"/api/shelves/{int(area)}", f"Regal {area}")
            books = shelf.get("books") or []
            page = books[offset:offset + 100]
            total = len(books)
        else:
            doc = self.get(who, f"/api/books?count=100&offset={offset}&sort=+name", "Bücher")
            page, total = doc.get("data") or [], doc.get("total") or 0
        return {"stores": [{"id": str(b["id"]), "title": b.get("name"), "slug": b.get("slug"),
                            "description": b.get("description") or ""} for b in page],
                "offset": offset, "total": total,
                "next": offset + len(page) if offset + len(page) < total else None}

    def list(self, who, store, path="/"):
        r = self.resolve(who, store, path)
        book, parts = r["book"], r["parts"]
        chapters, pages = self.contents(book)
        if not parts:
            items = [{"name": c["slug"], "kind": "folder", "title": c.get("name")} for c in chapters]
            items += [{"name": p["slug"], "kind": "document", "title": p.get("name")} for p in pages]
        elif len(parts) == 1 and r["chapter"]:
            ch = r["chapter"]
            cpages = ch.get("pages")
            if cpages is None:
                cpages = self.get(who, f"/api/chapters/{ch['id']}", "Kapitel").get("pages") or []
            items = [{"name": p["slug"], "kind": "document", "title": p.get("name")}
                     for p in cpages if not p.get("draft")]
        else:
            raise Refused(f"{path} ist kein Ordner in diesem Buch (oder nicht für dich).")
        return {"store": str(book["id"]), "path": "/" + "/".join(parts), "items": items}

    def read(self, who, store, path):
        r = self.resolve(who, store, path)
        if not r["page"]:
            raise Refused(f"Seite {path} gibt es nicht (oder nicht für dich).")
        page = self.get(who, f"/api/pages/{r['page']['id']}", "Seite")
        md = page.get("markdown")
        out = {"store": str(r["book"]["id"]), "path": self.address(r["book"], r["chapter"], page),
               "title": page.get("name"), "revision": str(page.get("revision_count") or 0),
               "editor": page.get("editor") or "",
               "updated_by": (page.get("updated_by") or {}).get("name"),
               "updated_at": page.get("updated_at"),
               "url": (r["page"].get("url") or "")}
        if md or page.get("editor") == "markdown":
            out.update(format="markdown", text=md or "")
        else:
            out.update(format="html", text=page.get("html") or page.get("raw_html") or "")
        return out

    def write(self, who, store, path, text, revision=None, title=None):
        if not isinstance(text, str):
            raise Refused("text fehlt.")
        if len(text.encode("utf-8")) > MAX_TEXT:
            raise Refused("Der Text ist größer als 512 KB.")
        r = self.resolve(who, store, path)
        parts = r["parts"]
        if not parts:
            raise Refused("Ins Buch selbst kann man nicht schreiben -- nenne eine Seite.")
        if len(parts) == 1 and r["chapter"] and not r["page"]:
            raise Refused(f"/{parts[0]} ist ein Kapitel, keine Seite.")
        if r["page"]:
            page = self.get(who, f"/api/pages/{r['page']['id']}", "Seite")
            have = str(page.get("revision_count") or 0)
            if revision is None or str(revision) == "":
                raise Refused(f"Die Seite gibt es schon (Revision {have}): ein Schreiben nennt die "
                              "Revision, die es gelesen hat.")
            if str(revision) != have:
                raise Refused(f"Die Seite hat sich geändert: gelesen war Revision {revision}, "
                              f"jetzt ist es {have}. Erst neu lesen.")
            if page.get("editor") not in ("markdown", "", None) and not page.get("markdown"):
                raise Refused("Diese Seite wird im WYSIWYG-Editor gepflegt; Stufe 1 schreibt nur "
                              "Markdown-Seiten.")
            body = {"markdown": text}
            if title:
                body["name"] = str(title)[:255]
            st, doc = self.call(who, "PUT", f"/api/pages/{page['id']}", body)
            created = False
        else:
            if revision not in (None, "", "0", 0):
                raise Refused(f"Seite {path} gibt es nicht mehr (gelesen war Revision {revision}).")
            name = str(title or parts[-1])[:255]
            if slugify(name) != parts[-1]:
                raise Refused(f"Der Titel „{name}“ ergäbe in BookStack die Adresse "
                              f"/{slugify(name)}, nicht /{parts[-1]} -- Pfad oder Titel angleichen.")
            body = {"name": name, "markdown": text}
            if r["chapter"]:
                body["chapter_id"] = r["chapter"]["id"]
            else:
                body["book_id"] = r["book"]["id"]
            st, doc = self.call(who, "POST", "/api/pages", body)
            created = True
        if st == 403:
            raise Refused("BookStack erlaubt dir das nicht.")
        if st != 200:
            raise Refused(f"Schreiben ging nicht: BookStack antwortet {st}.")
        return {"store": str(r["book"]["id"]),
                "path": self.address(r["book"], r["chapter"], doc),
                "revision": str(doc.get("revision_count") or 1), "created": created,
                "title": doc.get("name")}

    def folder(self, who, store, path, title=None):
        parts = split_path(path)
        if len(parts) != 1:
            raise Refused("Ein Kapitel liegt direkt im Buch: /kapitel.")
        r = self.resolve(who, store, path)
        if r["chapter"] or r["page"]:
            raise Refused(f"/{parts[0]} gibt es schon.")
        name = str(title or parts[0])[:255]
        if slugify(name) != parts[0]:
            raise Refused(f"Der Titel „{name}“ ergäbe /{slugify(name)}, nicht /{parts[0]}.")
        st, doc = self.call(who, "POST", "/api/chapters", {"book_id": r["book"]["id"], "name": name})
        if st == 403:
            raise Refused("BookStack erlaubt dir das nicht.")
        if st != 200:
            raise Refused(f"Kapitel anlegen ging nicht: BookStack antwortet {st}.")
        return {"store": str(r["book"]["id"]), "path": "/" + doc.get("slug", parts[0]),
                "title": doc.get("name")}

    def search(self, who, store, query, path="/"):
        q = str(query or "").strip()
        if not q:
            raise Refused("Wonach suchen? query fehlt.")
        r = self.resolve(who, store, path)
        if r["parts"] and not r["chapter"]:
            raise Refused(f"{path} ist kein Ordner.")
        book = r["book"]
        allowed = None
        if r["chapter"]:
            ch = r["chapter"]
            cpages = ch.get("pages")
            if cpages is None:
                cpages = self.get(who, f"/api/chapters/{ch['id']}", "Kapitel").get("pages") or []
            allowed = {p["id"] for p in cpages}
        st, doc = self.call(who, "GET", "/api/search?" + _qs({"query": q + " {type:page}", "count": 100}))
        if st != 200:
            raise Refused(f"Suche ging nicht: BookStack antwortet {st}.")
        chapters, _ = self.contents(book)
        chap_of = {}
        for c in chapters:
            for p in c.get("pages") or []:
                chap_of[p["id"]] = c
        hits = []
        for h in doc.get("data") or []:
            if h.get("type") != "page" or h.get("book_id") != book["id"]:
                continue
            if allowed is not None and h["id"] not in allowed:
                continue
            c = r["chapter"] or chap_of.get(h["id"])
            hits.append({"path": self.address(book, c, h), "title": h.get("name"),
                         "snippet": _strip((h.get("preview_html") or {}).get("content") or "")[:300]})
        return {"store": str(book["id"]), "query": q, "hits": hits}

    def may(self, who, store, path="/"):
        try:
            r = self.resolve(who, store, path)
            readable = bool(r["page"] or r["chapter"] or not r["parts"])
        except Refused:
            readable = False
        return {"read": readable, "create": None, "update": None, "remove": None,
                "note": "BookStack sagt erst beim Schreiben, ob es erlaubt ist."}

    def entry(self, who, store):
        book = self.book(who, store)
        chapters, pages = self.contents(book)
        cands = sorted(pages, key=lambda p: p.get("priority") or 0)
        if cands:
            return {"store": str(book["id"]), "path": "/" + cands[0]["slug"], "title": cands[0].get("name")}
        for c in chapters:
            cp = [p for p in (c.get("pages") or []) if not p.get("draft")]
            if cp:
                return {"store": str(book["id"]), "path": f"/{c['slug']}/{cp[0]['slug']}",
                        "title": cp[0].get("name")}
        return {"store": str(book["id"]), "path": None}

    def create_store(self, who, title, area=None):
        name = str(title or "").strip()[:255]
        if not name:
            raise Refused("Ein Buch braucht einen Titel.")
        st, doc = self.call(who, "POST", "/api/books", {"name": name})
        if st == 403:
            raise Refused("BookStack erlaubt dir nicht, Bücher anzulegen.")
        if st != 200:
            raise Refused(f"Buch anlegen ging nicht: BookStack antwortet {st}.")
        if area:
            shelf = self.get(who, f"/api/shelves/{int(area)}", f"Regal {area}")
            ids = [b["id"] for b in shelf.get("books") or []] + [doc["id"]]
            self.call(who, "PUT", f"/api/shelves/{int(area)}", {"books": ids})
        return {"id": str(doc["id"]), "title": doc.get("name"), "slug": doc.get("slug")}


def _qs(d):
    import urllib.parse
    return urllib.parse.urlencode(d)


def _strip(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


# --- die MCP-Tür ---------------------------------------------------------------
STR = {"type": "string"}


def register(door, conn):
    """Meldet die Verben als Werkzeuge mit festen Namen an (oaap.work.stores/1)."""
    import mcpdoor

    def wrap(fn):
        def run(args, who):
            try:
                return fn(args, who)
            except Refused as e:
                raise mcpdoor.Refused(str(e))
        return run

    def t(name, title, desc, props, required=(), read_only=True):
        def deco(fn):
            door.tool(name, title, desc, props, required, read_only=read_only)(wrap(fn))
            return fn
        return deco

    store = {"type": "string", "description": "Kennung des Buchs (aus stores_stores)"}
    path = {"type": "string", "description": "Pfad /kapitel/seite, / für das Buch"}

    @t("stores_about", "Was dieser Konnektor kann", "Art, Fassung und Fähigkeiten (oaap.work.stores/1).", {})
    def _a(a, w):
        return conn.about(w)

    @t("stores_areas", "Regale", "Die Regale, die du sehen darfst.", {})
    def _ar(a, w):
        return conn.areas(w)

    @t("stores_stores", "Bücher", "Die Bücher, die du sehen darfst; optional in einem Regal.",
       {"area": STR, "offset": {"type": "integer"}})
    def _s(a, w):
        return conn.stores(w, a.get("area"), a.get("offset") or 0)

    @t("stores_list", "Inhalt eines Ordners", "Kapitel und Seiten im Buch oder Seiten eines Kapitels.",
       {"store": store, "path": path}, ("store",))
    def _l(a, w):
        return conn.list(w, a.get("store"), a.get("path") or "/")

    @t("stores_read", "Seite lesen", "Eine Seite ganz: Text, Revision, Titel, letzter Bearbeiter.",
       {"store": store, "path": path}, ("store", "path"))
    def _r(a, w):
        return conn.read(w, a.get("store"), a.get("path"))

    @t("stores_write", "Seite schreiben", "Legt eine Seite an oder ersetzt ihren Markdown-Text, als du; "
       "eine bestehende nur mit der gelesenen Revision.",
       {"store": store, "path": path, "text": STR, "revision": STR, "title": STR},
       ("store", "path", "text"), read_only=False)
    def _w(a, w):
        return conn.write(w, a.get("store"), a.get("path"), a.get("text"), a.get("revision"), a.get("title"))

    @t("stores_folder", "Kapitel anlegen", "Legt ein Kapitel im Buch an, als du.",
       {"store": store, "path": path, "title": STR}, ("store", "path"), read_only=False)
    def _f(a, w):
        return conn.folder(w, a.get("store"), a.get("path"), a.get("title"))

    @t("stores_search", "Suchen", "Volltext in einem Buch oder Kapitel, als du.",
       {"store": store, "query": STR, "path": path}, ("store", "query"))
    def _q(a, w):
        return conn.search(w, a.get("store"), a.get("query"), a.get("path") or "/")

    @t("stores_may", "Was darf ich hier", "Ob du diesen Pfad lesen darfst (BookStack sagt mehr erst beim Schreiben).",
       {"store": store, "path": path}, ("store",))
    def _m(a, w):
        return conn.may(w, a.get("store"), a.get("path") or "/")

    @t("stores_entry", "Einstieg", "Die Seite, mit der man in diesem Buch beginnt.",
       {"store": store}, ("store",))
    def _e(a, w):
        return conn.entry(w, a.get("store"))

    @t("stores_create_store", "Buch anlegen", "Legt ein neues Buch an, als du; optional in einem Regal.",
       {"title": STR, "area": STR}, ("title",), read_only=False)
    def _c(a, w):
        return conn.create_store(w, a.get("title"), a.get("area"))
