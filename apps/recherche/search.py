"""web_search über SearXNG hinter der Destination `search` (RFC-0116 §2.2, D3, §8).

Wohin die Suche geht (RFC-0116 §2.2a, recherche 0.3.0):
  1. die Destination `search`, wenn der Betreiber eine gebunden hat
     (OAAP_DESTINATION_SEARCH_URL) -- eine gemietete oder eigene Suche; in einem
     geschlossenen Mandanten der einzige Weg hinaus (RFC-0068);
  2. sonst der eigene SearXNG-Dienst `search` im Netz der Instanz
     (RECHERCHE_SEARCH_URL, Vorgabe http://search:8080) -- aber nie in einem
     geschlossenen Mandanten: dort käme er nicht hinaus, und eine Suche, die
     still nichts findet, ist die schlechtere Antwort als ein Satz.

Aus der Messung von Stufe 0 (§8.3): Fallen Suchmaschinen aus (CAPTCHA, Zeitüberschreitung),
sagt das Ergebnis es in einem Satz; sind dabei keine Treffer übrig, ist das eine
Ablehnung mit Satz, kein „nichts gefunden“.
"""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode

TIMEOUT = 20
MAX_QUERY = 300
LANG = re.compile(r"^(all|[a-z]{2}(-[A-Z]{2})?)$")
SITE = re.compile(r"^[a-z0-9.-]{1,200}\.[a-z]{2,}$")


class Refused(Exception):
    def __init__(self, text, reason):
        super().__init__(text)
        self.text, self.reason = text, reason


OWN_DEFAULT = "http://search:8080"


def destination(env):
    return (env.get("OAAP_DESTINATION_SEARCH_URL") or "").strip()


def own_service(env):
    """Adresse des eigenen SearXNG-Dienstes, oder "" (abgeschaltet, geschlossener Mandant)."""
    if (env.get("OAAP_EGRESS") or "").strip() == "closed":
        return ""
    v = env.get("RECHERCHE_SEARCH_URL")
    v = OWN_DEFAULT if v is None else v.strip()
    return "" if v.lower() in ("", "off", "aus") else v


def backend(env):
    """(Adresse, Art): Art ist "destination", "eigen" oder "" (keiner)."""
    d = destination(env)
    if d:
        return d, "destination"
    o = own_service(env)
    return (o, "eigen") if o else ("", "")


def search(env, query, count=5, language="", site="", opener=urllib.request.urlopen):
    base, _kind = backend(env)
    if not base:
        raise Refused("Dieser Instanz ist kein Suchdienst zugeordnet: keine Destination search und "
                      "kein eigener Suchdienst (im geschlossenen Mandanten gibt es ihn nicht). Der "
                      "Betreiber bindet einen: oaap destination bind <instanz> <destination> "
                      "--as search.", "no-destination")
    q = str(query or "").strip()
    if not q:
        raise Refused("query fehlt.", "query")
    if len(q) > MAX_QUERY:
        raise Refused(f"query ist länger als {MAX_QUERY} Zeichen.", "query")
    try:
        count = max(1, min(10, int(count or 5)))
    except (TypeError, ValueError):
        count = 5
    language = str(language or "").strip()
    if language and not LANG.match(language):
        raise Refused("language ist ein Sprachkürzel wie de, en oder de-DE (oder all).", "language")
    site = str(site or "").strip().lower()
    if site:
        if not SITE.match(site):
            raise Refused("site ist ein Domainname wie heise.de.", "site")
        q = f"site:{site} {q}"
    params = {"q": q, "format": "json", "pageno": 1, "safesearch": 1}
    if language:
        params["language"] = language
    url = base.rstrip("/") + "/search?" + urlencode(params)
    # §8.4: SearXNG schreibt sonst für jede Anfrage eine Warnung ins eigene Protokoll.
    req = urllib.request.Request(url, headers={"Accept": "application/json",
                                               "X-Real-IP": "127.0.0.1",
                                               "User-Agent": "oaap-recherche/0.3"})
    try:
        with opener(req, timeout=TIMEOUT) as r:
            raw = r.read(4_000_000)
    except urllib.error.HTTPError as e:
        said = ""
        try:
            said = e.read(600).decode("utf-8", "replace").strip()
        except OSError:
            pass
        if e.code == 403 and said:
            # die Destination der Plattform lehnt ab (z. B. geschlossener Mandant) -- ihr Satz
            raise Refused(f"Der Suchdienst ist für diese Instanz gesperrt: {said[:300]}", "refused") from None
        if e.code == 429:
            raise Refused("Der Suchdienst bremst gerade (429). Gleich noch einmal versuchen.",
                          "throttled") from None
        raise Refused(f"Der Suchdienst antwortet mit {e.code}"
                      + (f": {said[:200]}" if said and len(said) < 200 else ".") , "status") from None
    except (urllib.error.URLError, OSError) as e:
        raise Refused(f"Der Suchdienst ist nicht erreichbar ({getattr(e, 'reason', e)}).",
                      "connect") from None
    try:
        doc = json.loads(raw)
    except ValueError:
        raise Refused("Der Suchdienst hat kein JSON geliefert. Ist dort format json erlaubt "
                      "(SearXNG: search.formats)?", "format") from None
    if not isinstance(doc, dict):
        raise Refused("Der Suchdienst hat eine unerwartete Antwort geliefert.", "format")
    hits, seen = [], set()
    for r in doc.get("results") or []:
        if not isinstance(r, dict) or not isinstance(r.get("url"), str):
            continue
        u = r["url"]
        if u in seen or not u.startswith(("http://", "https://")):
            continue
        seen.add(u)
        hits.append({"title": str(r.get("title") or "")[:300], "url": u[:2000],
                     "snippet": re.sub(r"\s+", " ", str(r.get("content") or "")).strip()[:500],
                     "source": str(r.get("engine") or "")[:40]})
        if len(hits) >= count:
            break
    failed = []
    for x in doc.get("unresponsive_engines") or []:
        if isinstance(x, (list, tuple)) and x:
            failed.append(f"{x[0]} ({x[1]})" if len(x) > 1 and x[1] else str(x[0]))
        elif isinstance(x, str):
            failed.append(x)
    note = ("Weniger Quellen diesmal: " + ", ".join(failed) + ".") if failed else ""
    if not hits and failed:
        raise Refused("Keine Treffer, weil Suchmaschinen ausgefallen sind: " + ", ".join(failed)
                      + ". Das heißt nicht, dass es nichts gibt -- gleich noch einmal versuchen.",
                      "engines")
    out = {"query": str(query).strip(), "count": len(hits), "results": hits}
    if site:
        out["site"] = site
    if note:
        out["note"] = note
    if not hits:
        out["hint"] = "Keine Treffer: andere oder weniger Wörter versuchen."
    return out
