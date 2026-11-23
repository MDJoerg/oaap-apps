"""Die Einrichtungstür gegen ein nachgebautes BookStack (nur die Rollen-API).

    python3 -m unittest test_setup     (im Ordner oaap/)
"""
import json
import os
import tempfile
import unittest

import setup_door as sd

EDITOR = ["page-view-all", "page-update-all", "book-view-all"]
VIEWER = ["page-view-all", "book-view-all"]


class FakeWiki:
    def __init__(self):
        self.roles = {
            1: {"id": 1, "display_name": "Admin", "system_name": "admin", "external_auth_id": "", "permissions": ["all"]},
            2: {"id": 2, "display_name": "Editor", "system_name": "", "external_auth_id": "", "permissions": EDITOR},
            3: {"id": 3, "display_name": "Viewer", "system_name": "", "external_auth_id": "", "permissions": VIEWER},
        }
        self.next = 10
        self.calls = []

    def api(self, method, path, body=None):
        self.calls.append((method, path))
        if method == "GET" and path.startswith("/api/roles?"):
            return 200, {"data": [{k: v for k, v in r.items() if k != "permissions"} for r in self.roles.values()]}
        if path.startswith("/api/roles/"):
            rid = int(path.rsplit("/", 1)[1])
            if rid not in self.roles:
                return 404, {}
            if method == "GET":
                return 200, dict(self.roles[rid])
            if method == "DELETE":
                del self.roles[rid]
                return 204, None
        if method == "POST" and path == "/api/roles":
            rid, self.next = self.next, self.next + 1
            self.roles[rid] = {"id": rid, "system_name": "", **body}
            return 200, dict(self.roles[rid])
        return 500, {"unexpected": [method, path]}


def post(w, mem, msg):
    return sd.post(w, mem, json.dumps(msg).encode(), "test")[1]["results"]


class Door(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.mem = sd.Memory(os.path.join(self.dir, "setup.json"))
        self.w = FakeWiki()

    def test_key(self):
        self.assertFalse(sd.authorized({"Authorization": "Bearer x"}, {}))
        self.assertFalse(sd.authorized({"Authorization": "Bearer x"}, {"OAAP_SETUP_KEY": "y"}))
        self.assertTrue(sd.authorized({"Authorization": "Bearer y"}, {"OAAP_SETUP_KEY": "y"}))

    def test_make_then_present(self):
        item = {"kind": "role", "id": "/wiki-redaktion", "title": "Redaktion", "like": "editor"}
        dry = post(self.w, self.mem, {"v": 1, "dry": True, "items": [item]})
        self.assertEqual(dry[0]["result"], "would_make")
        self.assertFalse(any(m == "POST" for m, _ in self.w.calls))
        r1 = post(self.w, self.mem, {"v": 1, "items": [item]})
        self.assertEqual(r1[0]["result"], "made")
        made = [r for r in self.w.roles.values() if r["external_auth_id"] == "/wiki-redaktion"][0]
        self.assertEqual(made["permissions"], EDITOR)
        self.assertEqual(made["display_name"], "Redaktion")
        r2 = post(self.w, self.mem, {"v": 1, "items": [item]})
        self.assertEqual(r2[0]["result"], "present")

    def test_default_viewer_and_title(self):
        r = post(self.w, self.mem, {"v": 1, "items": [{"kind": "role", "id": "/verein/vorstand"}]})
        self.assertEqual(r[0]["result"], "made")
        made = [x for x in self.w.roles.values() if x["external_auth_id"] == "/verein/vorstand"][0]
        self.assertEqual(made["permissions"], VIEWER)
        self.assertEqual(made["display_name"], "vorstand")

    def test_existing_role_by_hand_is_present_and_never_removed(self):
        self.w.roles[7] = {"id": 7, "display_name": "Hand", "system_name": "", "external_auth_id": "/WIKI-X,andere",
                           "permissions": VIEWER}
        r = post(self.w, self.mem, {"v": 1, "items": [{"kind": "role", "id": "/wiki-x"}]})
        self.assertEqual(r[0]["result"], "present")
        r = post(self.w, self.mem, {"v": 1, "remove": [{"kind": "role", "id": "/wiki-x"}]})
        self.assertEqual(r[0]["result"], "conflict")
        self.assertIn(7, self.w.roles)

    def test_remove_only_unchanged(self):
        item = {"kind": "role", "id": "/a"}
        post(self.w, self.mem, {"v": 1, "items": [item]})
        rid = [k for k, v in self.w.roles.items() if v["external_auth_id"] == "/a"][0]
        self.w.roles[rid]["permissions"] = VIEWER + ["page-create-all"]
        r = post(self.w, self.mem, {"v": 1, "remove": [item]})
        self.assertEqual(r[0]["result"], "conflict")
        self.w.roles[rid]["permissions"] = list(VIEWER)
        r = post(self.w, self.mem, {"v": 1, "remove": [item]})
        self.assertEqual(r[0]["result"], "removed")
        r = post(self.w, self.mem, {"v": 1, "remove": [item]})
        self.assertEqual(r[0]["result"], "gone")

    def test_refusals(self):
        r = post(self.w, self.mem, {"v": 1, "items": [
            {"kind": "role", "id": "/a", "token": "x"},
            {"kind": "role", "id": "/a,/b"},
            {"kind": "role", "id": "/a", "like": "admin"},
            {"kind": "shelf", "id": "x"},
            {"kind": "role", "id": "/a", "colour": "red"}]})
        self.assertEqual([x["result"] for x in r], ["refused"] * 5)
        self.assertFalse(any(m == "POST" for m, _ in self.w.calls))

    def test_malformed(self):
        for body in (b"", b"[]", b'{"v": 2, "items": []}', b'{"v": 1}', b'{"v": 1, "items": [], "remove": []}'):
            with self.assertRaises(sd.Malformed):
                sd.post(self.w, self.mem, body, "t")

    def test_get(self):
        self.assertEqual(sd.get(self.w, "", "")[1]["kinds"], ["role"])
        self.assertEqual(sd.get(self.w, "x", "")[0], 400)
        post(self.w, self.mem, {"v": 1, "items": [{"kind": "role", "id": "/a"}]})
        self.assertTrue(sd.get(self.w, "role", "/A")[1]["ready"])
        self.assertFalse(sd.get(self.w, "role", "/b")[1]["ready"])
        self.assertEqual([i["id"] for i in sd.get(self.w, "role", "")[1]["items"]], ["/a"])


if __name__ == "__main__":
    unittest.main()
