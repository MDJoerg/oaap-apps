"""Wegweiser — Optik: Stil, Seitenrahmen, kleine Bausteine.

Zwei Rahmen: `page` für angemeldete Menschen (Kopfzeile mit Benutzer,
Navigation) und `public_page` für die Welt (Zwischenseiten ohne jeden
Hinweis auf die Verwaltung). Farben und Formen folgen den
Design-Guidelines (Blau, Hexagon), wie beim KI-Gateway.
"""
import html
from datetime import datetime, timezone

esc = html.escape

STYLE = """<style>
  :root{
    --oaap-blue-950:#172554; --oaap-blue-900:#1e3a8a; --oaap-blue-700:#1d4ed8;
    --oaap-blue-600:#2563eb; --oaap-blue-100:#dbeafe;
    --oaap-bg:#f4f6fa; --oaap-surface:#fff; --oaap-text:#1f2937;
    --oaap-muted:#6b7280; --oaap-border:#e5e7eb;
    --ok:#15803d; --err:#b91c1c; --warn:#b45309;
  }
  *{box-sizing:border-box}
  body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
       margin:0;background:var(--oaap-bg);color:var(--oaap-text)}
  header.oaap{background:linear-gradient(135deg,var(--oaap-blue-900),var(--oaap-blue-950));
       color:#fff;display:flex;align-items:center;gap:1rem;flex-wrap:wrap;padding:.6rem 1.2rem}
  .brand{display:flex;align-items:center;gap:.6rem;text-decoration:none;color:#fff}
  .brand b{font-size:1.15rem;letter-spacing:.08em}
  .brand small{display:block;font-size:.62rem;opacity:.75;letter-spacing:.02em}
  nav.oaap a{color:#fff;opacity:.85;text-decoration:none;margin-right:1rem;font-size:.92rem}
  nav.oaap a:hover,nav.oaap a.on{opacity:1;text-decoration:underline}
  .userbox{display:flex;align-items:center;gap:.7rem;font-size:.9rem;margin-left:auto}
  .userbox .who{text-align:right;line-height:1.2}
  .userbox .who small{opacity:.75}
  main{max-width:64rem;margin:1.6rem auto;padding:0 1.2rem}
  main.narrow{max-width:34rem;margin-top:3rem}
  h1{font-size:1.35rem;margin:.2rem 0 1rem}
  h2{font-size:1.02rem;margin:0 0 .8rem}
  .card{background:var(--oaap-surface);border:1px solid var(--oaap-border);
       border-radius:.6rem;padding:1.4rem;box-shadow:0 1px 3px rgba(23,37,84,.06);
       margin-bottom:1.2rem}
  .card.attention{border-color:#fcd34d;background:#fffbeb}
  .card.errors{border-color:#fca5a5;background:#fef2f2}
  .card.okbox{border-color:#86efac;background:#f0fdf4}
  .badge{font-size:.72rem;padding:.15rem .55rem;border-radius:1rem;
       background:var(--oaap-blue-100);color:var(--oaap-blue-900);white-space:nowrap}
  .badge.green{background:#dcfce7;color:#166534}
  .badge.yellow{background:#fef3c7;color:#92400e}
  .badge.red{background:#fee2e2;color:#991b1b}
  .badge.off{background:#f3f4f6;color:#6b7280}
  a.btn,button{display:inline-block;padding:.6rem 1.3rem;border:0;border-radius:.4rem;
       background:var(--oaap-blue-600);color:#fff;text-decoration:none;font-size:.95rem;
       cursor:pointer;min-height:44px}
  a.btn:hover,button:hover{background:var(--oaap-blue-700)}
  button.quiet,a.btn.quiet{background:#f3f4f6;color:#374151;min-height:36px;padding:.35rem .9rem;
       font-size:.85rem}
  button.quiet:hover,a.btn.quiet:hover{background:#e5e7eb}
  button.danger{background:#fee2e2;color:#991b1b}
  button.danger:hover{background:#fecaca}
  .hint{font-size:.8rem;color:var(--oaap-muted);margin:0 0 .6rem}
  .err{color:var(--err)}.muted{color:var(--oaap-muted);font-size:.9rem}
  table{width:100%;border-collapse:collapse}
  th,td{text-align:left;padding:.55rem .5rem;border-bottom:1px solid var(--oaap-border);
       vertical-align:middle;font-size:.92rem}
  th{font-size:.78rem;text-transform:uppercase;letter-spacing:.04em;color:var(--oaap-muted)}
  td.num,th.num{text-align:right}
  code,kbd{background:#f3f4f6;border-radius:.25rem;padding:.1rem .35rem;font-size:.86rem;
       word-break:break-all}
  pre{background:#0f172a;color:#e2e8f0;padding:.9rem 1rem;border-radius:.5rem;
       overflow-x:auto;font-size:.82rem}
  form.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));
       gap:.8rem 1rem;align-items:end}
  form.grid .wide{grid-column:1/-1}
  label{display:block;font-size:.82rem;color:var(--oaap-muted);margin-bottom:.25rem}
  input[type=text],input[type=number],input[type=url],input[type=password],
  input[type=datetime-local],select,textarea{width:100%;padding:.5rem .6rem;border-radius:.4rem;
       border:1px solid var(--oaap-border);font-size:.95rem;min-height:44px;background:#fff}
  textarea{min-height:5rem}
  label.check{display:inline-flex;align-items:center;gap:.35rem;color:var(--oaap-text);
       font-size:.9rem;min-height:44px}
  .row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center}
  .linkurl{font-size:1.1rem;word-break:break-all}
  .bars td:first-child{width:40%}
  .bar{background:var(--oaap-blue-100);height:.8rem;border-radius:.2rem}
  .bar i{display:block;height:100%;background:var(--oaap-blue-600);border-radius:.2rem}
  .drop{border:2px dashed var(--oaap-border);border-radius:.6rem;padding:1.4rem;text-align:center}
  .drop.on{border-color:var(--oaap-blue-600);background:var(--oaap-blue-100)}
  progress{width:100%;height:1rem}
  footer.oaap{max-width:64rem;margin:2rem auto 1.2rem;padding:0 1.2rem;
       color:var(--oaap-muted);font-size:.8rem;display:flex;gap:.5rem;align-items:center}
  @media (max-width:640px){ .userbox .who{display:none} }
</style>"""

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'"
           "%3E%3Cpolygon points='50,4 90,27 90,73 50,96 10,73 10,27' fill='%232563eb'/%3E%3C/svg%3E")

# Ein Wegweiser: Pfahl mit zwei Schildern, im Hexagon-Rahmen.
LOGO_SVG = ('<svg viewBox="0 0 100 100" width="34" height="34" aria-hidden="true">'
            '<polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="none" stroke="#fff" '
            'stroke-width="5" stroke-linejoin="round"/>'
            '<rect x="47" y="28" width="6" height="52" fill="#fff"/>'
            '<polygon points="30,34 66,34 74,42 66,50 30,50" fill="#fff" opacity=".9"/>'
            '<polygon points="70,54 36,54 28,62 36,70 70,70" fill="#fff" opacity=".6"/></svg>')

HEX_SMALL = ('<svg viewBox="0 0 100 100" width="14" height="14" aria-hidden="true">'
             '<polygon points="50,4 90,27 90,73 50,96 10,73 10,27" fill="#2563eb"/></svg>')


def page(title, body, who, version, nav=()):
    links = "".join(f'<a href="{esc(href)}" class="{"on" if on else ""}">{esc(label)}</a>'
                    for href, label, on in nav)
    roles = ", ".join(sorted(who.roles)) if who else ""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)} — Wegweiser</title>
{STYLE}
<header class="oaap">
  <a class="brand" href="/manage">{LOGO_SVG}
    <span><b>WEGWEISER</b><small>Kurzlinks, Zählpixel, Übergaben</small></span>
  </a>
  <nav class="oaap">{links}</nav>
  <div class="userbox"><span class="who">{esc(who.display or who.name if who else "")}<br><small>{esc(roles)}</small></span></div>
</header>
<main>{body}</main>
<footer class="oaap">{HEX_SMALL} Wegweiser {esc(version)}</footer>
</html>"""


def public_page(title, body, version=""):
    """Für die Welt: kein Benutzer, keine Navigation, kein Hinweis auf mehr."""
    return f"""<!doctype html><html lang="de"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<link rel="icon" href="{FAVICON}">
<title>{esc(title)}</title>
{STYLE}
<main class="narrow">{body}</main>
</html>"""


def errors_card(errors):
    if not errors:
        return ""
    items = "".join(f"<li>{esc(e)}</li>" for e in errors)
    return f'<div class="card errors"><h2>Das ging so nicht</h2><ul>{items}</ul></div>'


def notice_card(text, kind="okbox"):
    return f'<div class="card {kind}"><p style="margin:0">{esc(text)}</p></div>' if text else ""


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
        return '<p class="muted">noch nichts</p>'
    items = sorted(counts.items(), key=lambda kv: -kv[1])
    top = max(v for _, v in items) or 1
    total = total or sum(v for _, v in items) or 1
    rows = []
    for k, v in items:
        name = (label_map or {}).get(k, k) or "—"
        rows.append(f'<tr><td>{esc(str(name))}</td><td><div class="bar"><i style="width:{100 * v / top:.0f}%"></i></div></td>'
                    f'<td class="num">{v}</td><td class="num muted">{100 * v / total:.0f} %</td></tr>')
    return f'<table class="bars">{"".join(rows)}</table>'


def day_table(by_day):
    if not by_day:
        return '<p class="muted">keine Zugriffe im Zeitraum</p>'
    top = max(d["n"] for d in by_day) or 1
    rows = "".join(f'<tr><td>{esc(d["day"])}</td><td><div class="bar"><i style="width:{100 * d["n"] / top:.0f}%"></i></div></td>'
                   f'<td class="num">{d["n"]}</td></tr>' for d in by_day)
    return f'<table class="bars">{rows}</table>'


def status_badge(link, now_iso):
    if link.get("deleted_at"):
        return '<span class="badge off">gelöscht</span>'
    if link["disabled"]:
        return '<span class="badge off">deaktiviert</span>'
    if link.get("expires_at") and link["expires_at"] < now_iso:
        return '<span class="badge yellow">abgelaufen</span>'
    if link.get("state") == "received":
        return '<span class="badge green">Datei eingegangen</span>'
    if link.get("state") in ("waiting", "receiving"):
        return '<span class="badge">wartet auf Upload</span>'
    if link.get("max_downloads") is not None and link["download_count"] >= link["max_downloads"]:
        return '<span class="badge yellow">verbraucht</span>'
    if link.get("valid_until") and link["valid_until"] < now_iso:
        return '<span class="badge yellow">Frist vorbei</span>'
    if link.get("valid_from") and link["valid_from"] > now_iso:
        return '<span class="badge">noch nicht offen</span>'
    return '<span class="badge green">aktiv</span>'


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
