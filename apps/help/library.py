"""Der Kern von help: Quellen holen, Index bauen, Antworten formen.

Seite, API und MCP-Tür rufen DIESELBEN drei Funktionen -- `search`,
`read`, `topics` -- und bekommen dieselben Felder. Die Sichtbarkeit
(`index.visible`) steht in diesen drei Funktionen, nicht in den Türen
(RFC-0078 §6: eine Prüfung hinter jeder Tür).

Links zum Original entstehen beim Antworten aus einer Vorlage (§7), nie
im Index: eine geänderte Vorlage ändert sofort jeden Link.
"""
import datetime
import json
import os
import threading
from urllib.parse import quote

import apps_source
import guide_requests
import index as ix
import sources as src

ARCHIVE_SOURCE = "oaap-docs"
# Die Doku im Paket (RFC-0078 §3 `seed`): antwortet, bis der erste Abruf
# gelingt, und auf einem Knoten ohne Destination (M10).
SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed", "oaap-docs.tar.gz")
LOCAL_SOURCE = "local"
APPS_SOURCE = apps_source.SOURCE
PRIORITY = {ARCHIVE_SOURCE: 50, APPS_SOURCE: apps_source.PRIORITY, LOCAL_SOURCE: 90}
TRUST = {ARCHIVE_SOURCE: "platform", APPS_SOURCE: apps_source.TRUST, LOCAL_SOURCE: "local"}
# RFC-0094 §5: die Plattform-Doku ist ein Hilfsprodukt unter anderen. Typ
# nach Ordner, solange ein Dokument selbst keinen nennt.
OAAP_PRODUCTS = {
    "oaap": {"title": "OAAP-Plattform", "color": "#2b5f8a"},
    "oaap-cli": {"title": "OAAP-Kommandozeile", "color": "#4a4a46"},
    "notizen": {"title": "Eigene Notizen", "color": "#8a5a00"},
}
OAAP_KINDS = (("cli/", "cli"), ("szenarien/", "tutorial"), ("faq/", "faq"),
              ("betrieb/", "admin-guide"))


def oaap_product_kind(path):
    product = "oaap-cli" if path.startswith("cli/") else "oaap"
    kind = next((k for pre, k in OAAP_KINDS if path.startswith(pre)), "")
    return product, kind


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Library:
    def __init__(self, data_dir, env=os.environ):
        self.data_dir = data_dir
        self.env = env
        os.makedirs(src.local_dir(data_dir), exist_ok=True)
        self.index = ix.Index(os.path.join(data_dir, "index.db"))
        self.archive_file = os.path.join(data_dir, "archive.tar.gz")
        self.state_file = os.path.join(data_dir, "fetch.json")
        self.lock = threading.Lock()
        self.requests = guide_requests.Requests(data_dir, env)

    # --- Einstellungen (Konfiguration der Instanz) ---------------------
    @property
    def archive_path(self):
        return (self.env.get("HELP_ARCHIVE_PATH") or "/tar.gz/refs/heads/main").strip()

    @property
    def public_base(self):
        """Die eigene öffentliche Adresse (RFC-0043: OAAP_INSTANCE_NAMES, die erste ist die
        kanonische) -- für klickbare Links auf die Leseseite (W5, 0.5.0). Leer ohne Namen."""
        names = [n.strip() for n in (self.env.get("OAAP_INSTANCE_NAMES") or "").split(",")]
        first = next((n for n in names if n.startswith(("https://", "http://"))), "")
        return first.rstrip("/")

    def link(self, doc, anchor=""):
        """Die Leseseite dieses Dokuments in der Hilfe selbst, für eine angemeldete Person."""
        base = self.public_base
        if not base:
            return ""
        return (f"{base}/doc/{quote(doc['source'])}/{quote(doc['id'])}"
                + (f"#{quote(anchor)}" if anchor else ""))

    @property
    def web_template(self):
        return (self.env.get("HELP_WEB_TEMPLATE") or "").strip()

    @property
    def oaap_docs(self):
        """HELP_OAAP_DOCS (RFC-0094 §5): off = die Plattform-Doku samt seed
        bleibt draußen, die Destination docs-origin darf ungebunden sein."""
        return (self.env.get("HELP_OAAP_DOCS") or "on").strip().lower() != "off"

    @property
    def docs_root(self):
        return apps_source.root_of(self.env)

    @property
    def max_bytes(self):
        try:
            return int(self.env.get("HELP_MAX_BYTES") or src.MAX_BYTES)
        except ValueError:
            return src.MAX_BYTES

    # --- Abruf und Bau ----------------------------------------------------
    def state(self):
        try:
            with open(self.state_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _write_state(self, st):
        tmp = self.state_file + ".new"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_file)

    def _archive_docs(self, data=None):
        """Die Dokumente des zuletzt angenommenen Archivs -- oder, solange es
        keines gibt, die des seed im Paket. Gibt (Dokumente, Herkunft)."""
        if data is not None:
            return src.read_archive(data), "fetch"
        for path, origin in ((self.archive_file, "fetch"), (self.seed, "seed")):
            try:
                with open(path, "rb") as f:
                    return src.read_archive(f.read()), origin
            except (OSError, src.Refused):
                continue
        return [], ""

    @property
    def seed(self):
        return self.env.get("HELP_SEED_FILE") or SEED

    def _documents(self, archive_docs):
        """(Dokumente, Hilfsprodukte) aus Plattform-Doku, App-Doku und Notizen."""
        docs, products = [], []
        if self.oaap_docs:
            for path, text in archive_docs:
                product, kind = oaap_product_kind(path)
                docs.append({"source": ARCHIVE_SOURCE, "priority": PRIORITY[ARCHIVE_SOURCE],
                             "trust": TRUST[ARCHIVE_SOURCE], "path": path, "text": text,
                             "product": product, "kind": kind, "lang": "de"})
        local = src.read_local(self.data_dir)
        docs += [{"source": LOCAL_SOURCE, "priority": PRIORITY[LOCAL_SOURCE],
                  "trust": TRUST[LOCAL_SOURCE], "path": p, "text": t, "product": "notizen"}
                 for p, t in local]
        app_docs, app_products, app_state = apps_source.read(self.docs_root)
        docs += app_docs
        self._apps_state = app_state
        present = {d["product"] for d in docs}
        for pid, meta in OAAP_PRODUCTS.items():
            if pid in present:
                products.append(dict(meta, product=pid, source=(LOCAL_SOURCE if pid == "notizen"
                                                                else ARCHIVE_SOURCE),
                                     entry="", version="", channel="", app="OAAP"))
        products += app_products
        return docs, products

    def _build(self, archive_docs):
        docs, products = self._documents(archive_docs)
        ix.build(self.index.path, docs, products)
        self._apps_stamp = apps_source.stamp(self.docs_root)
        return docs

    def refresh(self):
        """Holt das Archiv und baut den Index neu. Scheitert der Abruf oder
        wird das Archiv abgelehnt, bleibt der alte Index -- und der Fehler
        steht im Zustand (§3.1, M4). Gibt den neuen Zustand zurück."""
        if not self.oaap_docs:
            # Ohne Plattform-Doku kein Abruf: nur App-Doku und Notizen.
            self.rebuild()
            st = self.state()
            st[ARCHIVE_SOURCE] = {"at": now(), "ok": True, "error": "",
                                  "skipped": "HELP_OAAP_DOCS=off", "files": 0}
            self._write_state(st)
            return st
        with self.lock:
            st = self.state()
            entry = {"at": now(), "ok": False, "error": "", "sha256": "", "files": 0}
            try:
                data = src.fetch(src.destination_url(self.env), self.archive_path,
                                 max_bytes=self.max_bytes)
                docs = src.read_archive(data)
                self._build(docs)
                tmp = self.archive_file + ".new"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, self.archive_file)
                entry.update(ok=True, sha256=src.sha256(data), files=len(docs))
                st["last_good"] = entry
            except src.Refused as e:
                entry["error"] = str(e)
                # Noch nie etwas angenommen: dann antwortet der seed (M10),
                # statt dass der Knoten ohne Doku dasteht.
                if not os.path.exists(self.archive_file) and not st.get("answering") == "seed":
                    docs, origin = self._archive_docs()
                    if origin == "seed":
                        self._build(docs)
                        st["answering"] = "seed"
            if entry["ok"]:
                st["answering"] = "fetch"
            st[ARCHIVE_SOURCE] = entry
            st[APPS_SOURCE] = getattr(self, "_apps_state", None) or apps_source.read(self.docs_root)[2]
            st["documents"] = self.index.count()
            self._write_state(st)
            return st

    def rebuild(self):
        """Neu bauen aus dem zuletzt angenommenen Archiv (oder seed), der
        App-Doku und den Notizen -- ohne neuen Abruf. Nach einer Änderung an
        `local` und wenn der Knoten die App-Doku geändert hat."""
        with self.lock:
            archive, origin = self._archive_docs() if self.oaap_docs else ([], "")
            self._build(archive)
            st = self.state()
            if origin:
                st["answering"] = origin
            st[LOCAL_SOURCE] = {"at": now(), "ok": True,
                                "files": len(src.read_local(self.data_dir))}
            st[APPS_SOURCE] = getattr(self, "_apps_state", {})
            st["documents"] = self.index.count()
            self._write_state(st)

    rebuild_local = rebuild

    def apps_changed(self):
        """Hat der Knoten die App-Doku geändert, seit zuletzt gebaut wurde?"""
        return apps_source.stamp(self.docs_root) != getattr(self, "_apps_stamp", None)

    # --- Antworten ---------------------------------------------------------
    def url(self, doc, anchor=""):
        if doc["source"] != ARCHIVE_SOURCE or not self.web_template:
            return ""
        u = self.web_template.replace("{path}", doc["path"])
        return u + (f"#{anchor}" if anchor else "")

    def _meta(self, doc, version=None):
        out = {"id": doc["id"], "title": doc["title"], "source": doc["source"],
               "product": doc.get("product", ""), "kind": doc.get("kind", ""),
               "readers": doc.get("readers", []), "app_version": doc.get("app_version", ""),
               "channel": doc.get("channel", ""),
               "trust": doc["trust"], "url": self.url(doc), "link": self.link(doc),
               "checked_against": doc["checked_against"], "checked_on": doc["checked_on"],
               "applies_from": doc["applies_from"], "applies_until": doc["applies_until"],
               "audience": doc["audience"], "lang": doc["lang"], "tags": doc["tags"]}
        a = ix.applies(doc, version)
        if a is not None:
            out["applies_to_version"] = a
        return out

    def search(self, query, *, roles, limit=8, source=None, tags=None, version=None,
               product=None, kind=None):
        limit = max(1, min(int(limit or 8), ix.MAX_LIMIT))
        hits = []
        for h in self.index.search(query, limit=limit, source=source, tags=tags,
                                   product=product or None, kind=kind or None):
            d = h["doc"]
            if not ix.visible(d, roles):
                continue
            m = self._meta(d, version)
            m.update(section=h["section"], anchor=h["anchor"], snippet=h["snippet"],
                     url=self.url(d, h["anchor"]), link=self.link(d, h["anchor"]))
            hits.append(m)
        # Lücke (§15.4): kein Abschnitt trägt alle Wörter -- ohne Personenbezug gezählt.
        if query and not self.index.strict(query):
            try:
                self.requests.record_gap(query)
            except Exception:
                pass
        # Ein Text, der für die genannte Version nicht stimmt, rückt nach
        # hinten (zentrale Instanz: markieren, nicht weglassen -- §8).
        hits.sort(key=lambda m: m.get("applies_to_version") is False)
        return hits[:limit]

    def read(self, ref, *, roles, section=None, version=None):
        """Das Dokument (oder ein Abschnitt) mit Metadaten; None, wenn es
        fehlt ODER nicht sichtbar ist -- beides gleich beantwortet (§11)."""
        d = self.index.get(ref)
        if not d or not ix.visible(d, roles):
            return None
        out = self._meta(d, version)
        body = d["body"]
        if section:
            want = ix.slug(section)
            parts = [s for s in ix.sections(body) if s[0] == want or ix.slug(s[1]) == want]
            if not parts:
                return None
            anchor, heading, text = parts[0]
            body = f"## {heading}\n\n{text}"
            out["section"], out["url"] = heading, self.url(d, anchor)
            out["link"] = self.link(d, anchor)
        out["markdown"] = body
        out["path"] = d["path"]
        out["sections"] = [{"anchor": a, "heading": h} for a, h, _t in ix.sections(d["body"]) if a]
        # Überdeckt (§3): wer antwortet hier, und welche Fassungen gibt es noch.
        out["shadowed_by"] = d["shadowed_by"]
        out["other_versions"] = [f"{s}:{d['id']}" for s in d["shadows"]]
        return out

    def topics(self, prefix="", *, roles, product=None):
        tree = {}
        for d in self.index.docs(prefix=prefix or ""):
            if product and d["product"] != product:
                continue
            if ix.visible(d, roles):
                tree.setdefault(d["source"], []).append(
                    {"id": d["id"], "title": d["title"], "tags": d["tags"],
                     "product": d["product"], "kind": d["kind"]})
        return tree

    def products(self, *, roles):
        """Die Hilfsprodukte, die diese Person sieht, mit Zahl der Dokumente
        je Typ (RFC-0094 §5). Ein Produkt ohne sichtbares Dokument fehlt."""
        counts = {}
        for d in self.index.docs():
            if ix.visible(d, roles):
                c = counts.setdefault(d["product"], {})
                c[d["kind"]] = c.get(d["kind"], 0) + 1
        out = []
        for p in self.index.products():
            c = counts.get(p.get("product"))
            if not c:
                continue
            out.append(dict(p, documents=sum(c.values()),
                            kinds={k: c[k] for k in ix.KINDS if k in c}))
        return out

    def resources(self, *, roles):
        return [{"uri": f"help://{d['source']}/{d['id']}", "name": d["title"],
                 "mimeType": "text/markdown"}
                for d in self.index.docs() if ix.visible(d, roles)]

    # --- Anfragen (RFC-0078 §15) --------------------------------------------
    def covered_by(self, text):
        """Ein vorhandenes Dokument, in dem ein Abschnitt alle Wörter trägt."""
        hits = self.index.strict(text)
        return hits[0] if hits else None

    def doc_exists(self, doc_id):
        return bool(doc_id) and self.index.get(doc_id) is not None
