#!/usr/bin/env python3
"""Schreibt seed/oaap-docs.tar.gz aus einem oaap-docs-Checkout (RFC-0078 §3).

Der seed ist die Doku im Paket: Er antwortet, bis der erste Abruf über
docs-origin gelingt, und auf Knoten ohne Destination. Nur Markdown, ohne
AGENTS.md/CLAUDE.md, oberster Ordner `oaap-docs-main/` wie im Archiv von
GitHub. Byte-gleich bei gleichem Inhalt (sortiert, mtime 0, gzip ohne
Zeitstempel) -- ein neuer seed erscheint im Diff nur, wenn sich die Doku
geändert hat.

Aufruf (aus apps/help):  python3 make_seed.py ../../../oaap-docs
"""
import gzip
import io
import os
import sys
import tarfile

SKIP = {"AGENTS.md", "CLAUDE.md"}


def build(docs_dir):
    files = []
    for d, dirs, names in os.walk(docs_dir):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for n in sorted(names):
            if n.endswith(".md") and n not in SKIP:
                p = os.path.join(d, n)
                files.append((os.path.relpath(p, docs_dir).replace(os.sep, "/"), p))
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for rel, p in sorted(files):
            with open(p, "rb") as f:
                data = f.read().replace(b"\r\n", b"\n")
            ti = tarfile.TarInfo(f"oaap-docs-main/{rel}")
            ti.size, ti.mtime, ti.mode = len(data), 0, 0o644
            tf.addfile(ti, io.BytesIO(data))
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode="wb", mtime=0, filename="") as g:
        g.write(raw.getvalue())
    return out.getvalue(), len(files)


if __name__ == "__main__":
    if len(sys.argv) != 2 or not os.path.isdir(sys.argv[1]):
        sys.exit("Aufruf: python3 make_seed.py <oaap-docs-Verzeichnis>")
    data, n = build(sys.argv[1])
    here = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(here, "seed"), exist_ok=True)
    target = os.path.join(here, "seed", "oaap-docs.tar.gz")
    with open(target, "wb") as f:
        f.write(data)
    print(f"seed: {n} Dokumente, {len(data) // 1024} KB -> {target}")
