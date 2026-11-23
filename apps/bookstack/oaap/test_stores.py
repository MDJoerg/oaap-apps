"""Der Ablagen-Konnektor gegen ein nachgebautes BookStack (ohne Netz, ohne Datenbank)."""
import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mcpdoor  # noqa: E402
import stores  # noqa: E402

ISS = "https://auth.example/realms/t"


class FakeWiki:
    """Zwei Personen: anna (1) sieht Buch 1, bert (2) sieht Buch 1 und 2."""

    def __init__(self):
        self.users = {"sub-anna": 1, "sub-bert": 2, "sub-noapi": 3}
        self.sees = {1: {1}, 2: {1, 2}}
        self.tokens = {}
        self.books = {
            1: {"id": 1, "name": "Handbuch", "slug": "handbuch", "contents": [
                {"type": "page", "id": 10, "slug": "willkommen", "name": "Willkommen", "priority": 1},
                {"type": "page", "id": 11, "slug": "", "name": "Neue Seite", "draft": True},
                {"type": "chapter", "id": 20, "slug": "technik", "name": "Technik",
                 "pages": [{"id": 12, "slug": "server", "name": "Server"}]}]},
            2: {"id": 2, "name": "Vorstand", "slug": "vorstand", "contents": []}}
        self.pages = {10: {"id": 10, "book_id": 1, "slug": "willkommen", "name": "Willkommen",
                           "markdown": "# Willkommen", "revision_count": 3, "editor": "markdown"},
                      12: {"id": 12, "book_id": 1, "slug": "server", "name": "Server",
                           "markdown": "", "html": "<p>x</p>", "revision_count": 1, "editor": "wysiwyg"}}
        self.calls = []

    def user_id_by_external(self, sub):
        return self.users.get(sub)

    def drop_tokens(self, label=None):
        pass

    def mint_token(self, uid, label, days=2):
        tok = f"tok-{uid}"
        self.tokens[tok] = uid
        return tok, datetime.date.today() + datetime.timedelta(days=days)

    def api(self, method, path, body=None):
        return 200, {"version": "v26.09.3"}

    def _call(self, method, path, body=None, token=None):
        uid = self.tokens[token]
        self.calls.append((uid, method, path, body))
        if uid == 3:   # Rolle ohne „Zugriff auf System-API“ (gemessen 23.11.)
            return 403, {"error": {"code": 403, "message": "The owner of the used API token does not "
                                                           "have permission to make API calls"}}
        if path.startswith("/api/books/"):
            bid = int(path.rsplit("/", 1)[1])
            if bid not in self.sees[uid] or bid not in self.books:
                return 404, {}
            return 200, json.loads(json.dumps(self.books[bid]))
        if path.startswith("/api/pages/") and method == "GET":
            p = self.pages.get(int(path.rsplit("/", 1)[1]))
            return (200, dict(p)) if p and p["book_id"] in self.sees[uid] else (404, {})
        if path.startswith("/api/pages/") and method == "PUT":
            if uid != 2:
                return 403, {}
            p = self.pages[int(path.rsplit("/", 1)[1])]
            p.update(markdown=body["markdown"], revision_count=p["revision_count"] + 1)
            return 200, dict(p)
        if path == "/api/pages" and method == "POST":
            return 200, {"id": 99, "slug": stores.slugify(body["name"]), "name": body["name"],
                         "revision_count": 1}
        if path.startswith("/api/search"):
            return 200, {"data": [
                {"type": "page", "id": 10, "book_id": 1, "slug": "willkommen", "name": "Willkommen",
                 "preview_html": {"content": "<strong>Willkommen</strong> hier"}},
                {"type": "page", "id": 50, "book_id": 2, "slug": "geheim", "name": "Geheim"}]}
        return 404, {}


def who(sub, iss=ISS):
    return {"user": sub, "idp": f"{iss} {sub}" if sub else ""}


class Tests(unittest.TestCase):
    def setUp(self):
        self.w = FakeWiki()
        self.c = stores.Connector(self.w, {"OAAP_OIDC_ISSUER": ISS})

    def test_no_person_no_call(self):
        with self.assertRaises(stores.Refused):
            self.c.list({"user": "x", "idp": ""}, "1")
        with self.assertRaises(stores.Refused):
            self.c.list(who("sub-anna", "https://other/realms/x"), "1")
        with self.assertRaises(stores.Refused):
            self.c.list(who("sub-nobody"), "1")
        self.assertEqual(self.w.calls, [])

    def test_as_the_person(self):
        self.assertEqual(len(self.c.list(who("sub-bert"), "2")["items"]), 0)
        with self.assertRaises(stores.Refused):
            self.c.list(who("sub-anna"), "2")
        self.assertEqual({c[0] for c in self.w.calls}, {1, 2})

    def test_list_skips_drafts(self):
        items = self.c.list(who("sub-anna"), "1", "/")["items"]
        self.assertEqual([(i["name"], i["kind"]) for i in items],
                         [("technik", "folder"), ("willkommen", "document")])
        self.assertEqual(self.c.list(who("sub-anna"), "1", "/technik")["items"][0]["name"], "server")

    def test_paths(self):
        for bad in ("/../x", "/.git", "/A B", "/a/b/c"):
            with self.assertRaises(stores.Refused):
                stores.split_path(bad)
        self.assertEqual(stores.split_path("/"), [])

    def test_read_and_revision(self):
        r = self.c.read(who("sub-anna"), "1", "/willkommen")
        self.assertEqual((r["revision"], r["format"], r["text"]), ("3", "markdown", "# Willkommen"))
        with self.assertRaises(stores.Refused):
            self.c.write(who("sub-bert"), "1", "/willkommen", "neu", revision="2")
        with self.assertRaises(stores.Refused):
            self.c.write(who("sub-bert"), "1", "/willkommen", "neu")
        out = self.c.write(who("sub-bert"), "1", "/willkommen", "neu", revision="3")
        self.assertEqual(out["revision"], "4")

    def test_product_refuses(self):
        with self.assertRaises(stores.Refused):
            self.c.write(who("sub-anna"), "1", "/willkommen", "neu", revision="3")

    def test_wysiwyg_not_written(self):
        with self.assertRaises(stores.Refused):
            self.c.write(who("sub-bert"), "1", "/technik/server", "x", revision="1")

    def test_create_title_must_match_path(self):
        with self.assertRaises(stores.Refused):
            self.c.write(who("sub-bert"), "1", "/neu", "x", title="Ganz anders")
        out = self.c.write(who("sub-bert"), "1", "/technik/stand", "x", title="Stand")
        self.assertTrue(out["created"])
        self.assertEqual(out["path"], "/technik/stand")
        self.assertEqual(self.w.calls[-1][3]["chapter_id"], 20)

    def test_search_confined_to_book(self):
        hits = self.c.search(who("sub-anna"), "1", "Willkommen")["hits"]
        self.assertEqual([h["path"] for h in hits], ["/willkommen"])
        self.assertEqual(hits[0]["snippet"], "Willkommen hier")

    def test_entry(self):
        self.assertEqual(self.c.entry(who("sub-anna"), "1")["path"], "/willkommen")

    def test_role_without_api_says_so(self):
        with self.assertRaises(stores.Refused) as cm:
            self.c.list(who("sub-noapi"), "1")
        self.assertIn("System-API", str(cm.exception))

    def test_door_names(self):
        d = mcpdoor.Door("t", "0")
        stores.register(d, self.c)
        names = set(d.tools)
        self.assertIn("stores_read", names)
        self.assertTrue(all(n.startswith("stores_") for n in names))
        out = d.answer({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "stores_list", "arguments": {"store": "2"}}},
                       who("sub-anna"))
        self.assertTrue(out["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
