/* Markdown für den Chat (0.3.0) -- klein, ohne Abhängigkeiten, sicher zuerst.
 *
 * Text aus einem Modell ist fremder Text: Er wird ZUERST vollständig escaped,
 * erst danach werden die wenigen Auszeichnungen in HTML verwandelt. Rohes HTML
 * aus der Antwort erscheint nie als HTML. Links nur mit http(s) oder mailto.
 * ```mermaid-Blöcke werden zu einem Platzhalter, den die Seite (mit Mermaid im
 * strengen Modus) zum Diagramm macht; bis dahin und bei Fehlern steht der Code da.
 *
 * Kann: Überschriften, Absätze, Zeilenumbruch, **fett**, *kursiv*, ~~durch~~,
 * `Code`, Codeblöcke, Listen (auch nummeriert, eine Ebene Einrückung), Zitate,
 * Tabellen, Trennlinie, Links, nackte https-Adressen.
 */
(function (root) {
  "use strict";

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  // Inline: Code-Spannen zuerst herauslösen, den Rest escapen, dann auszeichnen.
  function inline(text) {
    var out = "", parts = String(text).split(/(`[^`\n]+`)/);
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (i % 2 === 1) { out += "<code>" + esc(p.slice(1, -1)) + "</code>"; continue; }
      var s = esc(p);
      // Links [Text](url) -- url schon escaped, also ohne " und <
      s = s.replace(/\[([^\]\n]+)\]\(((?:https?:\/\/|mailto:)[^\s)]+)\)/g, function (_, t, u) {
        return '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + t + "</a>";
      });
      // nackte https-Adressen, die nicht schon in einem Link stehen
      s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+[^\s<).,;:!?])/g, function (m, pre, u) {
        return pre + '<a href="' + u + '" target="_blank" rel="noopener noreferrer">' + u + "</a>";
      });
      s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/__([^_\n]+)__/g, "<strong>$1</strong>")
        .replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>")
        .replace(/(^|[^_\w])_([^_\n]+)_(?!\w)/g, "$1<em>$2</em>")
        .replace(/~~([^~\n]+)~~/g, "<del>$1</del>");
      out += s;
    }
    return out;
  }

  function isTableSep(line) {
    return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(line);
  }

  function cells(line) {
    var t = line.trim();
    if (t.charAt(0) === "|") t = t.slice(1);
    if (t.charAt(t.length - 1) === "|") t = t.slice(0, -1);
    return t.split("|").map(function (c) { return c.trim(); });
  }

  function render(src) {
    var lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
    var html = [], i = 0, para = [];

    function flush() {
      if (para.length) { html.push("<p>" + para.map(inline).join("<br>") + "</p>"); para = []; }
    }

    while (i < lines.length) {
      var line = lines[i], m;
      // Codeblock
      if ((m = /^\s*```\s*([\w+-]*)\s*$/.exec(line))) {
        flush();
        var lang = m[1].toLowerCase(), body = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) { body.push(lines[i]); i++; }
        i++;
        var code = body.join("\n");
        if (lang === "mermaid") {
          html.push('<div class="mermaid-src" data-src="' + esc(code) + '"><pre><code>' + esc(code) +
                    "</code></pre></div>");
        } else {
          html.push("<pre><code" + (lang ? ' class="lang-' + esc(lang) + '"' : "") + ">" + esc(code) +
                    "</code></pre>");
        }
        continue;
      }
      if (/^\s*$/.test(line)) { flush(); i++; continue; }
      if ((m = /^(#{1,6})\s+(.*)$/.exec(line))) {
        flush();
        var n = Math.min(6, m[1].length + 2);           // # -> h3: eine Antwort ist kein Dokument
        html.push("<h" + n + ">" + inline(m[2].replace(/\s+#+\s*$/, "")) + "</h" + n + ">");
        i++; continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flush(); html.push("<hr>"); i++; continue; }
      // Tabelle: Kopf, Trennzeile, Zeilen
      if (line.indexOf("|") >= 0 && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        flush();
        var head = cells(line), rows = [];
        i += 2;
        while (i < lines.length && lines[i].indexOf("|") >= 0 && !/^\s*$/.test(lines[i])) {
          rows.push(cells(lines[i])); i++;
        }
        var t = "<table><thead><tr>" + head.map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") +
                "</tr></thead><tbody>";
        rows.forEach(function (r) {
          t += "<tr>" + head.map(function (_, k) { return "<td>" + inline(r[k] || "") + "</td>"; }).join("") + "</tr>";
        });
        html.push(t + "</tbody></table>");
        continue;
      }
      if (/^\s*>/.test(line)) {
        flush();
        var q = [];
        while (i < lines.length && /^\s*>/.test(lines[i])) { q.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
        html.push("<blockquote>" + render(q.join("\n")) + "</blockquote>");
        continue;
      }
      // Liste (eine Ebene Einrückung)
      if ((m = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/.exec(line))) {
        flush();
        var ordered = /\d/.test(m[2]), tag = ordered ? "ol" : "ul", items = [], cur = null;
        while (i < lines.length) {
          var lm = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/.exec(lines[i]);
          if (lm && lm[1].length < 2) {
            cur = {text: [lm[3]], sub: [], subOrdered: false}; items.push(cur); i++;
          } else if (lm && cur) {
            cur.sub.push(lm[3]); cur.subOrdered = /\d/.test(lm[2]); i++;
          } else if (cur && /^\s{2,}\S/.test(lines[i])) {
            cur.text.push(lines[i].trim()); i++;
          } else {
            break;
          }
        }
        var l = "<" + tag + ">";
        items.forEach(function (it) {
          l += "<li>" + it.text.map(inline).join("<br>");
          if (it.sub.length) {
            var st = it.subOrdered ? "ol" : "ul";
            l += "<" + st + ">" + it.sub.map(function (s) { return "<li>" + inline(s) + "</li>"; }).join("") + "</" + st + ">";
          }
          l += "</li>";
        });
        html.push(l + "</" + tag + ">");
        continue;
      }
      para.push(line.trim());
      i++;
    }
    flush();
    return html.join("\n");
  }

  var api = {render: render, escape: esc};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ChatMarkdown = api;
})(this);
