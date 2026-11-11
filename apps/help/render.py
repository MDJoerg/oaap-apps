"""Markdown → HTML für die Leseseite (RFC-0078 §9, §11).

Erst wird ALLES escaped, dann bekommen wenige Formen ihre Tags:
Überschriften, Absätze, Listen, Zitate, Tabellen, Codeblöcke, `code`,
**fett**, *kursiv*, Links. Rohes HTML aus einer Quelle erscheint als Text,
nie als Element (M7). Links: http(s) nach außen mit rel=noopener,
relative Links auf ein anderes Dokument derselben Quelle zeigen auf dessen
Leseseite, alles andere (javascript:, data: …) wird zu Text.
"""
import html
import posixpath
import re
from urllib.parse import quote

from index import slug

INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
ULIST = re.compile(r"^(\s*)[-*+]\s+(.*)$")
OLIST = re.compile(r"^(\s*)\d+[.)]\s+(.*)$")
TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _link(label, target, doc_path, link_for):
    t = html.unescape(target)
    if re.match(r"^https?://", t, re.I):
        return (f'<a href="{html.escape(t, quote=True)}" rel="noopener noreferrer" '
                f'target="_blank">{label}</a>')
    if t.startswith("#"):
        return f'<a href="{html.escape(t, quote=True)}">{label}</a>'
    if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I):
        return label                           # javascript:, data:, mailto: … nicht
    path, _, frag = t.partition("#")
    if path.endswith(".md") and doc_path is not None:
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(doc_path), path))
        if not rel.startswith(".."):
            href = link_for(rel) + (("#" + quote(frag)) if frag else "")
            return f'<a href="{html.escape(href, quote=True)}">{label}</a>'
    return label


def inline(text, doc_path=None, link_for=lambda p: "#"):
    codes = []

    def keep(m):
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    s = INLINE_CODE.sub(keep, html.escape(text, quote=False))
    s = LINK.sub(lambda m: _link(m.group(1), m.group(2), doc_path, link_for), s)
    s = BOLD.sub(r"<strong>\1</strong>", s)
    s = ITALIC.sub(r"<em>\1</em>", s)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], s)


def _cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def to_html(md, doc_path=None, link_for=lambda p: "#"):
    lines = md.replace("\r\n", "\n").split("\n")
    out, i, para = [], 0, []
    il = lambda t: inline(t, doc_path, link_for)  # noqa: E731

    def flush():
        if para:
            out.append("<p>" + il(" ".join(para)) + "</p>")
            para.clear()

    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*(```|~~~)", line):
            flush()
            fence = line.strip()[:3]
            lang = line.strip()[3:].strip()
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                buf.append(lines[i])
                i += 1
            cls = f' class="lang-{html.escape(lang, quote=True)}"' if re.fullmatch(r"[\w-]+", lang or "") else ""
            out.append(f"<pre><code{cls}>" + html.escape("\n".join(buf), quote=False) + "</code></pre>")
            i += 1
            continue
        m = HEADING.match(line)
        if m:
            flush()
            n, text = len(m.group(1)), m.group(2)
            out.append(f'<h{n} id="{html.escape(slug(text), quote=True)}">{il(text)}</h{n}>')
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        if re.match(r"^\s*(-{3,}|\*{3,})\s*$", line):
            flush()
            out.append("<hr>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            buf = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + to_html("\n".join(buf), doc_path, link_for) + "</blockquote>")
            continue
        if "|" in line and i + 1 < len(lines) and TABLE_SEP.match(lines[i + 1]):
            flush()
            head = _cells(line)
            i += 2
            rows = []
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            t = ["<table><thead><tr>" + "".join(f"<th>{il(c)}</th>" for c in head) + "</tr></thead><tbody>"]
            t += ["<tr>" + "".join(f"<td>{il(c)}</td>" for c in r) + "</tr>" for r in rows]
            out.append("".join(t) + "</tbody></table>")
            continue
        if ULIST.match(line) or OLIST.match(line):
            flush()
            ordered = bool(OLIST.match(line))
            tag = "ol" if ordered else "ul"
            items = []
            while i < len(lines):
                mm = (OLIST if ordered else ULIST).match(lines[i])
                if mm:
                    items.append(mm.group(2))
                elif lines[i].startswith(("  ", "\t")) and lines[i].strip() and items:
                    items[-1] += " " + lines[i].strip()
                else:
                    break
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{il(x)}</li>" for x in items) + f"</{tag}>")
            continue
        para.append(line.strip())
        i += 1
    flush()
    return "\n".join(out)
