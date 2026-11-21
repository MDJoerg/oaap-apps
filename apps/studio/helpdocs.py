"""Die Hilfe der App aus ihrem eigenen docs/-Ordner (RFC-0118 §9).

Eine Quelle: Dieselben Markdown-Dateien gehen über `docs:` an die Hilfe
des Mandanten und werden hier unter /hilfe angezeigt, wenn jemand die
alte Adresse aufruft. Nur Standardbibliothek; kann bewusst wenig --
Überschriften, Absätze, Listen, Tabellen, Codeblöcke, fett, kursiv,
Code, Verweise. Verweise auf andere .md-Dateien bleiben in /hilfe.
"""
import html
import os
import re

DOCS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*\.md$")


def esc(s):
    return html.escape(s, quote=True)


def _inline(text, base):
    parts = re.split(r"(`[^`]+`)", text)
    out = []
    for p in parts:
        if p.startswith("`") and p.endswith("`") and len(p) > 1:
            out.append(f"<code>{esc(p[1:-1])}</code>")
            continue
        s = esc(p)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)

        def link(m):
            label, href = m.group(1), html.unescape(m.group(2))
            if _NAME.match(href.split("#")[0]):
                href = base + href.split("#")[0][:-3]
            elif not href.startswith(("http://", "https://", "/", "#")):
                return label
            return f'<a href="{esc(href)}">{label}</a>'
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", link, s)
        out.append(s)
    return "".join(out)


def render(md, base="/hilfe/"):
    """Markdown → HTML (die kleine Teilmenge, die die docs/ benutzen)."""
    lines = md.replace("\r\n", "\n").split("\n")
    out, para, i = [], [], 0

    def flush():
        if para:
            out.append(f"<p>{_inline(' '.join(para), base)}</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush()
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            out.append(f"<pre><code>{esc(chr(10).join(code))}</code></pre>")
        elif not s:
            flush()
        elif m := re.match(r"^(#{1,4})\s+(.*)$", s):
            flush()
            n = len(m.group(1))
            out.append(f"<h{n}>{_inline(m.group(2), base)}</h{n}>")
        elif s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?\s*:?-{2,}", lines[i + 1].strip()):
            flush()

            def cells(row):
                return [c.strip() for c in row.strip().strip("|").split("|")]
            head = cells(s)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(cells(lines[i]))
                i += 1
            th = "".join(f"<th>{_inline(c, base)}</th>" for c in head)
            tr = "".join("<tr>" + "".join(f"<td>{_inline(c, base)}</td>" for c in r) + "</tr>"
                         for r in rows)
            out.append(f'<div class="oaap-tablewrap"><table class="oaap-table"><thead><tr>{th}</tr>'
                       f"</thead><tbody>{tr}</tbody></table></div>")
            continue
        elif re.match(r"^([-*]|\d+\.)\s+", s):
            flush()
            tag = "ol" if s[0].isdigit() else "ul"
            items = []
            while i < len(lines) and lines[i].strip():
                t = lines[i].strip()
                m2 = re.match(r"^([-*]|\d+\.)\s+(.*)$", t)
                if m2:
                    items.append(m2.group(2))
                elif items:
                    items[-1] += " " + t
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{_inline(x, base)}</li>" for x in items) + f"</{tag}>")
            continue
        elif s.startswith(">"):
            flush()
            out.append(f"<blockquote>{_inline(s.lstrip('> '), base)}</blockquote>")
        else:
            para.append(s)
        i += 1
    flush()
    return "\n".join(out)


def names():
    try:
        return sorted(n for n in os.listdir(DOCS) if _NAME.match(n))
    except OSError:
        return []


def title_of(name):
    try:
        with open(os.path.join(DOCS, name), encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return name[:-3]


def load(slug):
    """slug ohne .md ('' = index) → (Titel, HTML) oder None."""
    name = (slug or "index") + ".md"
    if not _NAME.match(name) or name not in names():
        return None
    with open(os.path.join(DOCS, name), encoding="utf-8") as f:
        md = f.read()
    return title_of(name), render(md)


def toc(current="", base="/hilfe/"):
    """Die Seitenliste der Hilfe als kleine Karte."""
    items = []
    for n in names():
        slug = n[:-3]
        href = base.rstrip("/") if slug == "index" else base + slug
        mark = ' aria-current="page"' if slug == (current or "index") else ""
        items.append(f'<li><a href="{esc(href)}"{mark}>{esc(title_of(n))}</a></li>')
    return "<ul>" + "".join(items) + "</ul>"
