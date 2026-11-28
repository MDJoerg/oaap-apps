"""Wegweiser — Optik: Seitenrahmen, kleine Bausteine.

Zwei Rahmen: `page` für angemeldete Menschen -- der Rahmen der Plattform
(appframe: Kopfzeile, Benutzermenü, Reiter, Fußzeile, Kit) -- und
`public_page` für die Welt (Zwischenseiten ohne jeden Hinweis auf die
Verwaltung). Die öffentlichen Seiten behalten ihre eigene schlichte Optik
und laden nichts von der Plattform: unter `/` authentifiziert die Plattform
nichts, und eine Zwischenseite soll für sich stehen.
"""
import html
from datetime import datetime, timezone

import appframe as af

esc = html.escape

APP_NAME = "Wegweiser"

# Was der Wegweiser über das Kit hinaus braucht -- Farben nur aus dessen
# Variablen.
APP_STYLE = """
  .ww-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
       gap:.8rem 1rem;align-items:end}
  .ww-grid .wide{grid-column:1/-1}
  .ww-grid label{display:block;font-size:.82rem;color:var(--oaap-color-text-muted);margin-bottom:.25rem}
  .ww-grid input:not([type=checkbox]),.ww-grid select,.ww-grid textarea{width:100%;box-sizing:border-box}
  .ww-grid label.check{display:inline-flex;align-items:center;gap:.35rem;color:var(--oaap-color-text);font-size:.9rem}
  .ww-row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
  .ww-row form{display:inline}
  .ww-linkurl{font-size:1.1rem;word-break:break-all}
  .ww-qrbox{float:right;margin:0 0 .6rem 1rem;text-align:center;font-size:.85rem}
  .ww-qrbox img{display:block;border:1px solid var(--oaap-color-border);border-radius:6px;background:#fff;margin-bottom:.25rem}
  .oaap-card::after{content:"";display:block;clear:both}
  .ww-hostlist{margin:.2rem 0 .8rem;padding-left:1.2rem}
  .ww-bars{width:100%;border-collapse:collapse;font-size:.9em}
  .ww-bars td{padding:.25rem .4rem}
  .ww-bars td:first-child{width:40%}
  .ww-bar{background:var(--oaap-color-accent-soft);height:.8rem;border-radius:.2rem}
  .ww-bar i{display:block;height:100%;background:var(--oaap-color-primary);border-radius:.2rem}
  .ww-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(16rem,1fr));gap:1rem}
  .ww-stats h3{font-size:.95rem;margin:.4rem 0}
  textarea{min-height:4rem}
  progress{width:100%;height:1rem}
"""

# Die Zwischenseiten für die Welt: eigene, schlichte Optik, ohne Plattform.
PUBLIC_STYLE = """<style>
  :root{--ww-blue:#2563eb;--ww-blue-dark:#1d4ed8;--ww-blue-soft:#dbeafe;
        --ww-page:#f4f6fa;--ww-card:#fff;--ww-text:#1f2937;--ww-muted:#6b7280;
        --ww-line:#e5e7eb;--ww-err:#b91c1c}
  @media (prefers-color-scheme:dark){:root{--ww-page:#0f172a;--ww-card:#1e293b;--ww-text:#e5e7eb;
        --ww-muted:#9ca3af;--ww-line:#334155;--ww-blue-soft:#1e40af;--ww-err:#f87171}}
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       margin:0;background:var(--ww-page);color:var(--ww-text)}
  main{max-width:34rem;margin:3rem auto;padding:0 1.2rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  .card{background:var(--ww-card);border:1px solid var(--ww-line);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);margin-bottom:1.2rem}
  button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--ww-blue);color:#fff;font-size:.95rem;cursor:pointer;min-height:44px}
  button:hover{background:var(--ww-blue-dark)}
  .hint{font-size:.8rem;color:var(--ww-muted);margin:0 0 .6rem}
  .err{color:var(--ww-err)}.muted{color:var(--ww-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--ww-line);font-size:.92rem}
  th{font-size:.78rem;color:var(--ww-muted)}
  code{background:var(--ww-page);border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;word-break:break-all}
  label{display:block;font-size:.82rem;color:var(--ww-muted);margin:.6rem 0 .25rem}
  input[type=password]{width:100%;padding:.5rem .6rem;border-radius:.4rem;border:1px solid var(--ww-line);
       font-size:.95rem;min-height:44px;background:var(--ww-card);color:var(--ww-text)}
  form button{margin-top:.8rem}
  .drop{border:2px dashed var(--ww-line);border-radius:.6rem;padding:1.4rem;text-align:center}
  progress{width:100%;height:1rem}
</style>"""

FAVICON = af.FAVICON


def page(title, body, who, version, nav=(), obj=None, obj_title=None):
    """Eine angemeldete Seite im Rahmen der Plattform. `nav`: [(href, Text, aktiv)]
    -- daraus wird die eine Reiterleiste. Der Benutzer steht im Menü des
    Rahmens, nicht hier; `who` bleibt für die Aufrufer gleich."""
    tabs = [(href, label) for href, label, _on in nav]
    active = next((href for href, _l, on in nav if on), None)
    return af.page("" if title == APP_NAME else title, body, app=APP_NAME, version=version,
                   product="wegweiser", tabs=tabs, active=active, obj=obj,
                   obj_title=obj_title, style=APP_STYLE)


def public_page(title, body, version=""):
    """Für die Welt: kein Benutzer, keine Navigation, kein Hinweis auf mehr."""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)}</title>
{PUBLIC_STYLE}
<main>{body}</main>
</html>"""


def errors_card(errors):
    if not errors:
        return ""
    items = "".join(f"<li>{esc(e)}</li>" for e in errors)
    return af.card("Das ging so nicht", f"<ul>{items}</ul>", "oaap-danger")


def notice_card(text, kind="okbox"):
    return af.msg(text) if text else ""


def fmt_size(n):
    if n is None:
        return ""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024
    return ""


def fmt_dt(text, tz):
    """ISO-UTC aus der Datenbank → lesbare lokale Zeit."""
    if not text:
        return ""
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return text
    return dt.astimezone(tz).strftime("%d.%m.%Y %H:%M")


def fmt_day(text):
    """JJJJ-MM-TT → TT.MM.JJJJ; leer bleibt leer."""
    if not text:
        return ""
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return text


def dt_input(text, tz):
    """ISO-UTC → Wert für <input type=datetime-local>."""
    if not text:
        return ""
    try:
        dt = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return ""
    return dt.astimezone(tz).strftime("%Y-%m-%dT%H:%M")


def bars(counts, total=None, label_map=None):
    """Kleine Balkentabelle für Verteilungen — ohne Diagrammbibliothek."""
    if not counts:
        return '<p class="oaap-muted">noch nichts</p>'
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    top = max(v for _, v in items) or 1
    total = total or sum(v for _, v in items) or 1
    rows = []
    for k, v in items:
        name = (label_map or {}).get(k, k) or "—"
        rows.append(f'<tr><td>{esc(str(name))}</td><td><div class="ww-bar"><i style="width:{100 * v / top:.0f}%"></i></div></td>'
                    f'<td class="oaap-num">{v}</td><td class="oaap-num oaap-muted">{100 * v / total:.0f} %</td></tr>')
    return f'<table class="ww-bars">{"".join(rows)}</table>'


def day_table(by_day):
    if not by_day:
        return '<p class="oaap-muted">keine Zugriffe im Zeitraum</p>'
    top = max(d["n"] for d in by_day) or 1
    rows = "".join(f'<tr><td>{esc(d["day"])}</td><td><div class="ww-bar"><i style="width:{100 * d["n"] / top:.0f}%"></i></div></td>'
                   f'<td class="oaap-num">{d["n"]}</td></tr>' for d in by_day)
    return f'<table class="ww-bars">{rows}</table>'


def link_status(link, now_iso):
    """(Kennung, Wort, Badge-Art) -- eine Quelle für Badge, Filter und Sortierung."""
    if link.get("deleted_at"):
        return "deleted", "gelöscht", ""
    if link["disabled"]:
        return "disabled", "deaktiviert", ""
    if link.get("expires_at") and link["expires_at"] < now_iso:
        return "expired", "abgelaufen", "warn"
    if link.get("state") == "received":
        return "received", "Datei eingegangen", "ok"
    if link.get("state") in ("waiting", "receiving"):
        return "waiting", "wartet auf Upload", "info"
    if link.get("max_downloads") is not None and link["download_count"] >= link["max_downloads"]:
        return "used", "verbraucht", "warn"
    if link.get("valid_until") and link["valid_until"] < now_iso:
        return "window_over", "Frist vorbei", "warn"
    if link.get("valid_from") and link["valid_from"] > now_iso:
        return "not_yet", "noch nicht offen", "info"
    return "active", "aktiv", "ok"


STATUS_CHOICES = [("active", "aktiv"), ("expired", "abgelaufen"), ("disabled", "deaktiviert"),
                  ("used", "verbraucht"), ("waiting", "wartet auf Upload"),
                  ("received", "Datei eingegangen")]


def status_badge(link, now_iso):
    _k, word, kind = link_status(link, now_iso)
    return af.badge(word, kind)


RESULT_LABELS = {
    "redirected": "weitergeleitet", "pixel": "Pixel geladen", "landing": "Seite gezeigt",
    "downloaded": "heruntergeladen", "uploaded": "hochgeladen", "pin_wrong": "PIN falsch",
    "pin_locked": "PIN gesperrt", "expired": "abgelaufen", "exhausted": "verbraucht",
    "disabled": "deaktiviert", "not_found": "unbekannt",
}


# ------------------------------------------------------ öffentliche Seiten

def landing_download(link, area, url, pin_needed, error=""):
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    pin = ('<label>PIN<input type="password" name="pin" autocomplete="off" inputmode="numeric" required></label>'
           if pin_needed else "")
    once = (" Dieser Link gilt für genau einen Download." if link.get("max_downloads") == 1 else "")
    return public_page("Download", f"""<div class="card">
  <h1>{esc(link.get("title") or "Datei zum Herunterladen")}</h1>
  <table>
    <tr><th>Datei</th><td>{esc(link.get("file_name") or "")}</td></tr>
    <tr><th>Größe</th><td>{fmt_size(link.get("file_size"))}</td></tr>
    <tr><th>SHA-256</th><td><code>{esc(link.get("file_sha256") or "")}</code></td></tr>
  </table>
  <p class="hint" style="margin-top:.8rem">Der Download beginnt erst mit dem Knopf, nicht beim Öffnen dieser Seite.{once}</p>
  {err}
  <form method="post" action="{esc(url)}">{pin}
    <input type="hidden" name="action" value="download">
    <button type="submit">Herunterladen</button>
  </form>
</div>""")


def landing_redirect(link, url, error=""):
    err = f'<p class="err">{esc(error)}</p>' if error else ""
    return public_page("Weiter", f"""<div class="card">
  <h1>{esc(link.get("title") or "Dieser Link ist geschützt")}</h1>
  <p class="hint">Bitte die PIN eingeben, dann geht es weiter.</p>
  {err}
  <form method="post" action="{esc(url)}">
    <label>PIN<input type="password" name="pin" autocomplete="off" inputmode="numeric" required autofocus></label>
    <input type="hidden" name="action" value="go">
    <button type="submit">Weiter</button>
  </form>
</div>""")


def landing_upload(link, area, url, pin_needed, max_mb, extensions):
    pin = ('<label>PIN<input type="password" id="pin" autocomplete="off" inputmode="numeric" required></label>'
           if pin_needed else "")
    ext_hint = f" Erlaubt: {esc(extensions.replace(',', ', '))}." if extensions else ""
    return public_page("Upload", f"""<div class="card">
  <h1>{esc(link.get("title") or "Datei hochladen")}</h1>
  <p class="hint">Genau eine Datei, höchstens {max_mb} MB.{ext_hint} Danach ist dieser Link verbraucht.</p>
  {pin}
  <div class="drop" id="drop">
    <input type="file" id="file"><br>
    <button type="button" id="go" style="margin-top:.8rem">Hochladen</button>
  </div>
  <progress id="prog" value="0" max="100" hidden></progress>
  <p id="msg"></p>
</div>
<script>
(function(){{
  var f=document.getElementById('file'),b=document.getElementById('go'),p=document.getElementById('prog'),
      m=document.getElementById('msg'),pin=document.getElementById('pin');
  b.onclick=function(){{
    if(!f.files.length){{m.textContent='Bitte zuerst eine Datei wählen.';return;}}
    var file=f.files[0]; if(file.size>{max_mb}*1024*1024){{m.textContent='Die Datei ist zu groß.';return;}}
    var x=new XMLHttpRequest(); x.open('PUT',{url!r});
    x.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
    if(pin) x.setRequestHeader('X-Pin',pin.value);
    x.upload.onprogress=function(e){{if(e.lengthComputable){{p.hidden=false;p.value=100*e.loaded/e.total;}}}};
    x.onload=function(){{
      var d={{}}; try{{d=JSON.parse(x.responseText)}}catch(e){{}}
      if(x.status===201){{m.textContent='Danke, die Datei ist angekommen ('+(d.sha256||'')+').';b.disabled=true;f.disabled=true;}}
      else m.textContent=(d.error&&d.error.message)||('Fehler '+x.status);
    }};
    x.onerror=function(){{m.textContent='Die Verbindung ist abgebrochen.';}};
    b.disabled=true; x.send(file);
  }};
}})();
</script>""")


def gone_page(title, text):
    return public_page(title, f'<div class="card"><h1>{esc(title)}</h1><p>{esc(text)}</p></div>')


def not_found_page():
    return public_page("Nicht gefunden",
                       '<div class="card"><h1>Hier ist nichts</h1>'
                       '<p class="muted">Unter dieser Adresse gibt es nichts.</p></div>')
