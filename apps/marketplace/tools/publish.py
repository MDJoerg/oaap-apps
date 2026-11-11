#!/usr/bin/env python3
"""Ein Release in den Marketplace einchecken (RFC-0077 §6) -- nur Standardbibliothek.

    python3 publish.py --url https://market.example --key-file ~/.oaap/market.key \\
        paket.zip --channels beta

Der Schlüssel (ein Plattform-API-Schlüssel eines Maschinenbenutzers, D10)
kommt aus einer Datei oder der Umgebung OAAP_KEY, nie als Argument: er
stünde sonst in der Prozessliste. Das ist der Aufruf für eine KI oder
einen CI-Lauf nach der Freigabe auf dem Testknoten.

Ausgabe: die Antwort des Marketplace als JSON. Exit 0 bei 200/201.
"""
import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
import uuid


def multipart_body(fields, file_field, path):
    boundary = "----oaap" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    name = os.path.basename(path)
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data = f.read()
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
                  f'filename="{name}"\r\nContent-Type: {ctype}\r\n\r\n').encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="das Paket (ZIP) oder die Datei")
    ap.add_argument("--url", required=True, help="der Marketplace, z. B. https://market.example")
    ap.add_argument("--key-file", help="Datei mit dem API-Schlüssel (sonst OAAP_KEY)")
    ap.add_argument("--channels", default="", help="Kanäle, z. B. beta oder beta,stable")
    ap.add_argument("--notes", default="", help="Release-Notizen")
    ap.add_argument("--type", default="oaap-app", help="Asset-Typ (oaap-app | file)")
    ap.add_argument("--asset", default="", help="für --type file: die Asset-Kennung")
    ap.add_argument("--version", default="", help="für --type file: die Version")
    a = ap.parse_args(argv)
    key = ""
    if a.key_file:
        with open(os.path.expanduser(a.key_file), encoding="utf-8") as f:
            key = f.read().strip()
    key = key or os.environ.get("OAAP_KEY", "").strip()
    if not key:
        ap.error("kein Schlüssel: --key-file oder OAAP_KEY")
    fields = {"type": a.type, "channels": a.channels, "notes": a.notes}
    if a.asset:
        fields["asset"] = a.asset
    if a.version:
        fields["version"] = a.version
    body, ctype = multipart_body(fields, "file", a.file)
    req = urllib.request.Request(a.url.rstrip("/") + "/api/v1/releases", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": ctype})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            print(json.dumps(json.load(r), ensure_ascii=False, indent=1))
            return 0
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            print(json.dumps(json.loads(raw), ensure_ascii=False, indent=1))
        except ValueError:
            print(raw.decode("utf-8", "replace")[:500])
        print(f"publish: HTTP {e.code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
