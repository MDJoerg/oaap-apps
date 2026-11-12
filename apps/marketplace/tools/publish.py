#!/usr/bin/env python3
"""Ein Release in den Marketplace einchecken -- oder einen Ordner abgleichen
(RFC-0077 §6, §15.8.8). Nur Standardbibliothek.

    # eine Datei einchecken
    python3 publish.py --url https://market.example --key-file ~/.oaap/market.key \\
        paket.zip --channels beta

    # einen Ordner mit market.json abgleichen: Release, Texte je Sprache, Bilder
    python3 publish.py --url https://market.example --key-file ~/.oaap/market.key \\
        sync vorlagen/handballverein

Der Schlüssel (ein Plattform-API-Schlüssel eines Maschinenbenutzers, D10)
kommt aus einer Datei oder der Umgebung OAAP_KEY, nie als Argument: er
stünde sonst in der Prozessliste.

`sync` ist idempotent: ein zweiter Lauf ohne Änderung ändert nichts. Ein Bild
wird nur hochgeladen, wenn seine Prüfsumme dort noch fehlt -- der Marketplace
entfernt Metadaten, deshalb vergleicht sync die Liste nach dem Hochladen,
nicht die Bytes. Die Geheimnis-Sperre prüft der Marketplace; ein Fund (422)
hält den Abgleich für dieses Asset an, bevor Texte oder Bilder geschrieben
werden (das Release kommt zuerst).

Ausgabe: JSON. Exit 0, wenn alles angekommen ist.
"""
import argparse
import hashlib
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


class Client:
    def __init__(self, url, key):
        self.url = url.rstrip("/")
        self.key = key

    def req(self, method, path, body=None, ctype=None):
        h = {"Authorization": "Bearer " + self.key}
        if ctype:
            h["Content-Type"] = ctype
        r = urllib.request.Request(self.url + path, data=body, method=method, headers=h)
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                return resp.status, _json(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, _json(e.read())

    def upload(self, path, fields, target="/api/v1/releases"):
        body, ctype = multipart_body(fields, "file", path)
        return self.req("POST", target, body, ctype)

    def put_json(self, path, doc):
        return self.req("PUT", path, json.dumps(doc, ensure_ascii=False).encode("utf-8"),
                        "application/json")


def _json(raw):
    try:
        return json.loads(raw or b"{}")
    except ValueError:
        return {"raw": raw[:500].decode("utf-8", "replace")}


def read_key(key_file):
    key = ""
    if key_file:
        with open(os.path.expanduser(key_file), encoding="utf-8") as f:
            key = f.read().strip()
    return key or os.environ.get("OAAP_KEY", "").strip()


def sync(c, folder):
    """Den Marketplace an den Ordner angleichen. -> (ok, Bericht)."""
    with open(os.path.join(folder, "market.json"), encoding="utf-8") as f:
        m = json.load(f)
    aid, typ = m["asset"], m.get("type", "oaap-app")
    rep = {"asset": aid, "changed": []}
    # 1. Release
    fields = {"type": typ, "channels": ",".join(m.get("channels") or []),
              "lang": m.get("lang", "en")}
    notes = m.get("notes") or {}
    if isinstance(notes, dict):
        fields["notes"] = notes.get("en", "")
        fields["notes_i18n"] = json.dumps(notes, ensure_ascii=False)
    else:
        fields["notes"] = str(notes)
    if typ == "file":
        fields["asset"], fields["version"] = aid, m["version"]
    st, d = c.upload(os.path.join(folder, m["file"]), fields)
    rep["release"] = {"status": st, **{k: d.get(k) for k in ("version", "created", "review", "channels", "error")
                                        if k in d}}
    if st not in (200, 201):
        return False, rep
    if st == 201:
        rep["changed"].append("release")
    if d.get("asset") and d["asset"] != aid:
        rep["error"] = f"market.json sagt {aid}, die Datei sagt {d['asset']}"
        return False, rep
    # 2. Listing (Texte, Schlagworte, Kategorien, Links) in einem PUT
    texts = {}
    for lang, t in (m.get("texts") or {}).items():
        t = dict(t)
        if "description_file" in t:
            with open(os.path.join(folder, t.pop("description_file")), encoding="utf-8") as f:
                t["description"] = f.read()
        texts[lang] = {k: t.get(k, "") for k in ("title", "summary", "description")}
    want = {"texts": texts}
    for k in ("tags", "categories", "landscapes", "links", "license", "publisher"):
        if k in m:
            want[k] = m[k]
    st, cur = c.req("GET", f"/api/v1/assets/{aid}")
    if st != 200:
        rep["error"] = f"Asset nicht lesbar ({st})"
        return False, rep
    diff = {}
    if texts and any(cur.get("texts", {}).get(lang) != {k: v for k, v in t.items() if v.strip()}
                     for lang, t in texts.items()):
        diff["texts"] = texts
    for k in ("tags", "categories", "landscapes", "links", "license", "publisher"):
        if k in want:
            norm = sorted(x.lower() for x in want[k]) if k in ("tags", "categories", "landscapes") else want[k]
            if cur.get(k) != norm:
                diff[k] = want[k]
    if diff:
        st, d = c.put_json(f"/api/v1/assets/{aid}", diff)
        if st != 200:
            rep["listing"] = {"status": st, "error": d.get("error")}
            return False, rep
        rep["changed"].append("listing:" + ",".join(sorted(diff)))
    # 3. Bilder: hochladen, was fehlt (Prüfsumme nach dem Bereinigen = die Antwort)
    imgs = m.get("images") or {}
    known = set(cur.get("screenshots") or []) | ({cur["icon"]} if cur.get("icon") else set())
    # lokale Prüfsumme -> Prüfsumme im Marketplace (nach dem Bereinigen); im Cache des
    # Benutzers, nicht im Vorlagenordner (der liegt oft in einem Repository)
    cache = os.path.join(os.path.expanduser("~"), ".cache", "oaap-market")
    os.makedirs(cache, exist_ok=True)
    host = "".join(ch if ch.isalnum() else "_" for ch in c.url.split("://")[-1])[:60]
    state_file = os.path.join(cache, f"{host}-{aid}.json")
    try:
        with open(state_file, encoding="utf-8") as f:
            seen = json.load(f)                     # lokale Datei-Prüfsumme -> Marketplace-Prüfsumme
    except (OSError, ValueError):
        seen = {}
    wanted = []
    plan = ([("icon", imgs["icon"])] if imgs.get("icon") else []) + \
           [("screenshot", s) for s in imgs.get("screenshots") or []]
    for role, rel in plan:
        path = os.path.join(folder, rel)
        with open(path, "rb") as f:
            local = hashlib.sha256(f.read()).hexdigest()
        remote = seen.get(local)
        if remote and remote in known and (role != "icon" or cur.get("icon") == remote):
            wanted.append(remote)
            continue
        st, d = c.upload(path, {"role": role}, f"/api/v1/assets/{aid}/images")
        if st != 201:
            rep["images"] = {"status": st, "file": rel, "error": d.get("error")}
            return False, rep
        seen[local] = d["sha256"]
        wanted.append(d["sha256"])
        rep["changed"].append(f"image:{rel}")
    if plan:
        for sha in sorted(known - set(wanted)):
            st, _ = c.req("DELETE", f"/api/v1/assets/{aid}/images/{sha}")
            if st == 200:
                rep["changed"].append(f"image-removed:{sha[:12]}")
        try:
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(seen, f, indent=1)
        except OSError:
            pass
    return True, rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="das Paket (ZIP), die Datei -- oder 'sync'")
    ap.add_argument("folder", nargs="?", help="für sync: der Ordner mit market.json")
    ap.add_argument("--url", required=True, help="der Marketplace, z. B. https://market.example")
    ap.add_argument("--key-file", help="Datei mit dem API-Schlüssel (sonst OAAP_KEY)")
    ap.add_argument("--channels", default="", help="Kanäle, z. B. beta oder beta,stable")
    ap.add_argument("--notes", default="", help="Release-Notizen")
    ap.add_argument("--type", default="oaap-app", help="Asset-Typ (oaap-app | oaap-profile | file)")
    ap.add_argument("--asset", default="", help="für --type file: die Asset-Kennung")
    ap.add_argument("--version", default="", help="für --type file: die Version")
    a = ap.parse_args(argv)
    key = read_key(a.key_file)
    if not key:
        ap.error("kein Schlüssel: --key-file oder OAAP_KEY")
    c = Client(a.url, key)
    if a.file == "sync":
        if not a.folder:
            ap.error("sync braucht einen Ordner")
        good, rep = sync(c, a.folder)
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0 if good else 1
    fields = {"type": a.type, "channels": a.channels, "notes": a.notes}
    if a.asset:
        fields["asset"] = a.asset
    if a.version:
        fields["version"] = a.version
    st, d = c.upload(a.file, fields)
    print(json.dumps(d, ensure_ascii=False, indent=1))
    if st not in (200, 201):
        print(f"publish: HTTP {st}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
