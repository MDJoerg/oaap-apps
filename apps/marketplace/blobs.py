"""Dateien nach ihrem Inhalt ablegen (RFC-0077 §9).

Der Schlüssel einer Datei ist ihr SHA-256. Zwei Releases mit derselben
Datei legen sie einmal ab. Stufe 1 liegt lokal unter
`/data/blobs/ab/cd/<sha256>` -- im Speicher der Instanz, also im Backup
des Knotens. Stufe 3 setzt S3 hinter dieselbe Schnittstelle.
"""
import hashlib
import os
import re
import shutil

SHA_RE = re.compile(r"^[0-9a-f]{64}$")


class BlobStore:
    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "tmp"), exist_ok=True)

    def _path(self, sha):
        if not SHA_RE.match(sha or ""):
            raise ValueError("kein SHA-256")
        return os.path.join(self.root, sha[:2], sha[2:4], sha)

    def tmp_dir(self):
        return os.path.join(self.root, "tmp")

    def put_file(self, path):
        """Eine fertige Datei (z. B. aus dem Upload) aufnehmen -> (sha, size).
        Die Datei wird VERSCHOBEN; liegt der Inhalt schon da, wird sie gelöscht."""
        h = hashlib.sha256()
        size = 0
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
                size += len(block)
        sha = h.hexdigest()
        dest = self._path(sha)
        if os.path.exists(dest):
            os.remove(path)
        else:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(path, dest)
        return sha, size

    def open(self, sha):
        return open(self._path(sha), "rb")

    def stat(self, sha):
        try:
            return os.path.getsize(self._path(sha))
        except (OSError, ValueError):
            return None

    def delete(self, sha):
        try:
            os.remove(self._path(sha))
        except (OSError, ValueError):
            pass
