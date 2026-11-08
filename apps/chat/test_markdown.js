// Tests für static/markdown.js -- node apps/chat/test_markdown.js
"use strict";
const md = require("./static/markdown.js");
let pass = 0, fail = 0;
function check(name, ok, info) {
  if (ok) { pass++; console.log("  PASS  " + name); }
  else { fail++; console.log("  FAIL  " + name + (info !== undefined ? "  " + JSON.stringify(info).slice(0, 400) : "")); }
}
const r = s => md.render(s);

// Darstellung
check("fett, kursiv, durchgestrichen", r("Das ist **fett**, *kursiv* und ~~weg~~.") ===
      "<p>Das ist <strong>fett</strong>, <em>kursiv</em> und <del>weg</del>.</p>", r("Das ist **fett**, *kursiv* und ~~weg~~."));
check("Projekt **Bussard** (die Antwort aus der Messung)", r("Du hast ein Projekt: **Projekt Bussard**.").includes("<strong>Projekt Bussard</strong>"));
check("Inline-Code bleibt wörtlich, auch mit **", r("Nimm `a**b**c`").includes("<code>a**b**c</code>"));
check("Überschrift # wird h3", r("# Titel") === "<h3>Titel</h3>", r("# Titel"));
check("Liste", r("- eins\n- zwei") === "<ul><li>eins</li><li>zwei</li></ul>", r("- eins\n- zwei"));
check("nummerierte Liste mit Unterpunkten", r("1. a\n   - a1\n2. b") ===
      "<ol><li>a<ul><li>a1</li></ul></li><li>b</li></ol>", r("1. a\n   - a1\n2. b"));
check("Tabelle", r("| Nr | Titel |\n|---|---|\n| V-1 | Drucker |") ===
      "<table><thead><tr><th>Nr</th><th>Titel</th></tr></thead><tbody><tr><td>V-1</td><td>Drucker</td></tr></tbody></table>",
      r("| Nr | Titel |\n|---|---|\n| V-1 | Drucker |"));
check("Zitat", r("> gesagt") === "<blockquote><p>gesagt</p></blockquote>", r("> gesagt"));
check("Absatz mit Zeilenumbruch", r("a\nb\n\nc") === "<p>a<br>b</p>\n<p>c</p>", r("a\nb\n\nc"));
check("Codeblock mit Sprache, Inhalt escaped", r("```python\nif a < b:\n  pass\n```") ===
      '<pre><code class="lang-python">if a &lt; b:\n  pass</code></pre>', r("```python\nif a < b:\n  pass\n```"));
check("Link", r("[OAAP](https://oaap.example/x?a=1&b=2)") ===
      '<p><a href="https://oaap.example/x?a=1&amp;b=2" target="_blank" rel="noopener noreferrer">OAAP</a></p>',
      r("[OAAP](https://oaap.example/x?a=1&b=2)"));
check("nackte Adresse wird Link, ohne den Punkt am Satzende", r("Siehe https://oaap.example/a.") ===
      '<p>Siehe <a href="https://oaap.example/a" target="_blank" rel="noopener noreferrer">https://oaap.example/a</a>.</p>',
      r("Siehe https://oaap.example/a."));
check("snake_case bleibt", r("die Variable max_effect_class") === "<p>die Variable max_effect_class</p>", r("die Variable max_effect_class"));
check("Trennlinie", r("a\n\n---\n\nb").includes("<hr>"));
const mm = r("```mermaid\ngraph TD\n  A-->B\n```");
check("Mermaid-Block wird Platzhalter mit escaptem Quelltext und sichtbarem Code",
      mm === '<div class="mermaid-src" data-src="graph TD\n  A--&gt;B"><pre><code>graph TD\n  A--&gt;B</code></pre></div>', mm);

// Sicherheit
const bad = [
  ["<script>alert(1)</script>", "Skript"],
  ["<img src=x onerror=alert(1)>", "Bild mit onerror"],
  ["**<img src=x onerror=alert(1)>**", "HTML in fett"],
  ["[klick](javascript:alert(1))", "javascript-Link"],
  ["[klick](data:text/html,<script>alert(1)</script>)", "data-Link"],
  ['[x](https://a.example/"onmouseover="alert(1))', "Attribut-Ausbruch im Link"],
  ["| a |\n|---|\n| <svg onload=alert(1)> |", "HTML in Tabelle"],
  ["```mermaid\n\"><img src=x onerror=alert(1)>\n```", "Ausbruch aus data-src"],
  ["> <iframe src=https://evil.example>", "iframe im Zitat"],
  ["`</code><script>x</script>`", "Ausbruch aus Inline-Code"],
];
for (const [src, name] of bad) {
  const out = r(src);
  // Attributwerte in "…" entfernen; was dann in einem Tag nach on…= aussieht, wäre ein Attribut.
  const tagsOnly = out.replace(/"[^"]*"/g, '""');
  const evil = /<(script|img|svg|iframe)\b/i.test(out) || /<[^>]*\son\w+\s*=/i.test(tagsOnly)
    || /href="(?!https?:|mailto:)/i.test(out);
  check("sicher: " + name, !evil, out);
}
check("javascript-Link bleibt Text", !r("[klick](javascript:alert(1))").includes("<a "));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
