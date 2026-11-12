"""Sprachen (RFC-0077 §15.8.3): Englisch ist die Basis, Deutsch die zweite Sprache
der Oberfläche. Inhalte (Titel, Texte, Notizen) können jede Sprache tragen.

Gewählt wird: ?lang= (dann im Cookie market_lang gemerkt -- ohne Identität),
sonst das Cookie, sonst Accept-Language, sonst die Vorgabe des Betreibers.
Die Verwaltung (/admin) bleibt deutsch: sie ist die Werkbank des Betreibers.
"""
import re

UI_LANGS = ("en", "de")
LANG_NAMES = {"en": "English", "de": "Deutsch", "fr": "Français", "es": "Español",
              "it": "Italiano", "nl": "Nederlands", "pl": "Polski"}

S = {
    "sign_in": ("Sign in", "Anmelden"),
    "overview": ("Overview", "Übersicht"),
    "admin": ("Administration", "Verwaltung"),
    "manage": ("My assets", "Meine Assets"),
    "search_ph": ("Search: name, description, tag", "Suchen: Name, Beschreibung, Schlagwort"),
    "search": ("Search", "Suchen"),
    "assets_n": ("{n} assets", "{n} Assets"),
    "asset_1": ("1 asset", "1 Asset"),
    "nothing": ("Nothing found.", "Nichts gefunden."),
    "no_filters": ("No filters.", "Keine Filter."),
    "f_ls": ("Landscape", "Landschaft"),
    "f_cat": ("Category", "Kategorie"),
    "f_content": ("Content", "Inhalt"),
    "f_tag": ("Tag", "Schlagwort"),
    "f_channel": ("Channel", "Kanal"),
    "c_app": ("OAAP app", "OAAP-App"),
    "c_template": ("Setup template", "Aufbau-Vorlage"),
    "c_pdf": ("PDF", "PDF"),
    "c_json": ("JSON", "JSON"),
    "c_image": ("Image", "Bild"),
    "c_text": ("Text", "Text"),
    "c_archive": ("Archive", "Archiv"),
    "c_file": ("File", "Datei"),
    "no_release": ("no release yet", "noch kein Release"),
    "b_free": ("free", "frei"),
    "b_locked": ("unlock", "freischalten"),
    "b_entitled": ("entitled", "berechtigt"),
    "b_operator": ("operator", "Betreiber"),
    "b_review": ("in review", "in Prüfung"),
    "featured": ("Featured", "Empfohlen"),
    "landscapes": ("Landscapes", "Landschaften"),
    "new": ("New", "Neu"),
    "popular": ("Most downloaded", "Meistgeladen"),
    "show_all": ("Show all", "Alle anzeigen"),
    "download": ("Download", "Laden"),
    "request": ("Request access", "Freischaltung anfragen"),
    "share": ("Share", "Teilen"),
    "copy_link": ("Copy link", "Link kopieren"),
    "copied": ("Copied", "Kopiert"),
    "share_hint": ("Scan or copy the link. Operators paste it into their package catalog "
                   "(„Add from clipboard“).",
                   "Scannen oder Link kopieren. Betreiber fügen ihn in ihrem Paketkatalog ein "
                   "(„Aus Zwischenablage hinzufügen“)."),
    "tab_overview": ("Overview", "Übersicht"),
    "tab_versions": ("Versions", "Versionen"),
    "tab_tech": ("Technical details", "Technische Details"),
    "description": ("Description", "Beschreibung"),
    "no_description": ("No description.", "Keine Beschreibung."),
    "links": ("Links", "Links"),
    "only_in": ("Only available in {lang}.", "Nur auf {lang} vorhanden."),
    "version": ("Version", "Version"),
    "channels": ("Channels", "Kanäle"),
    "notes": ("Notes", "Notizen"),
    "yanked": ("withdrawn", "zurückgerufen"),
    "id": ("Identifier", "Kennung"),
    "type": ("Type", "Typ"),
    "publisher": ("Publisher", "Herausgeber"),
    "license": ("Licence", "Lizenz"),
    "files": ("Files", "Dateien"),
    "size": ("Size", "Größe"),
    "share_link": ("Link for package catalogs", "Link für Paketkataloge"),
    "dl_link": ("Download link", "Download-Link"),
    "tags": ("Tags", "Schlagworte"),
    "locked_anon": ("Locked. <a href=\"{href}\">Sign in</a> to request access.",
                    "Gesperrt. <a href=\"{href}\">Anmelden</a>, um eine Freischaltung anzufragen."),
    "req_open": ("Your request of {date} is open. A new message adds to it.",
                 "Deine Anfrage vom {date} ist offen. Eine neue Nachricht ergänzt sie."),
    "req_rejected": ("Declined on {date}:", "Abgelehnt am {date}:"),
    "req_granted": ("Granted on {date}.", "Freigeschaltet am {date}."),
    "req_note_ph": ("What do you need it for? (optional)", "Wofür brauchst du es? (optional)"),
    "req_send": ("Request", "Anfragen"),
    "channel": ("Channel", "Kanal"),
    "in_review": ("Version {v} is in review and not yet published.",
                  "Version {v} ist in Prüfung und noch nicht veröffentlicht."),
    "not_found": ("Not found", "Nicht gefunden"),
    "please_sign_in": ("Please sign in.", "Bitte anmelden."),
    "language": ("Language", "Sprache"),
}


def t(ui, key, **kw):
    pair = S.get(key)
    if not pair:
        return key
    s = pair[1] if ui == "de" else pair[0]
    return s.format(**kw) if kw else s


def ui_lang(lang):
    return lang if lang in UI_LANGS else "en"


LANG_RE = re.compile(r"^[a-z]{2}$")


def choose(query_lang, cookie_header, accept, default="en"):
    """-> (Sprache, ob ein Cookie zu setzen ist)."""
    if query_lang and LANG_RE.match(query_lang):
        return query_lang, True
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "market_lang" and LANG_RE.match(v):
            return v, False
    for item in (accept or "").split(","):
        code = item.split(";")[0].strip().lower()[:2]
        if LANG_RE.match(code):
            return code, False
    return default, False
