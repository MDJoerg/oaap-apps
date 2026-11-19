/* Gemeinsame Ansicht für Markdown (lib/mdview, Kopie in Chat und Vorgänge).
 *
 * markdown.js macht aus fremdem Text sicheres HTML; diese Datei macht daraus eine
 * Ansicht:
 *  - ```mermaid -> Diagramm (Mermaid im strengen Modus, aus derselben Ablage geladen)
 *  - ```svg     -> Bild. Als <img> mit data:-Adresse: ein SVG darin kann kein Skript
 *                  ausführen und nichts von außen nachladen. Bis dahin steht der Code da.
 *  - [data-md]  -> der rohe Text bleibt im Element, daneben die formatierte Fassung;
 *                  OAAPView.mode("raw"|"formatted") schaltet für die ganze Seite um
 *                  (<html data-md-view>, gemerkt im Browser). Ohne JavaScript bleibt der rohe Text sichtbar.
 */
(function (root) {
  "use strict";
  var me = document.currentScript;
  var base = me && me.src ? me.src.replace(/[^\/]*$/, "") : "";
  var ready = null;

  function dark() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t === "dark") return true;
    if (t === "light") return false;
    return !!(root.matchMedia && root.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  function loadMermaid() {
    if (!ready) ready = new Promise(function (ok, no) {
      var s = document.createElement("script");
      s.src = base + "mermaid.min.js";
      s.onload = function () {
        root.mermaid.initialize({startOnLoad: false, securityLevel: "strict", theme: dark() ? "dark" : "default"});
        ok();
      };
      s.onerror = no;
      document.head.appendChild(s);
    });
    return ready;
  }

  var n = 0;
  async function drawMermaid(el) {
    var boxes = [].slice.call(el.querySelectorAll(".mermaid-src:not([data-done])"));
    if (!boxes.length) return;
    try { await loadMermaid(); } catch (e) { return; }
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i];
      b.setAttribute("data-done", "1");
      try { var out = await root.mermaid.render("oaapmm" + Date.now() + (n++), b.getAttribute("data-src")); b.innerHTML = out.svg; }
      catch (e) { b.insertAdjacentHTML("beforeend", '<div class="muted">Diagramm nicht lesbar — oben der Quelltext.</div>'); }
    }
  }

  var SVG_START = /^\s*(<\?xml[^>]*>\s*)?(<!--[\s\S]*?-->\s*)*<svg[\s>]/i;
  function drawSvg(el) {
    [].forEach.call(el.querySelectorAll(".svg-src:not([data-done])"), function (b) {
      b.setAttribute("data-done", "1");
      var src = b.getAttribute("data-src") || "";
      if (!SVG_START.test(src) || src.length > 500000) return;      // dann bleibt der Code stehen
      var img = document.createElement("img");
      img.className = "svg-img";
      img.alt = "SVG-Grafik";
      img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(src);
      b.textContent = "";
      b.appendChild(img);
    });
  }

  function enhance(el) {
    drawSvg(el);
    return drawMermaid(el);
  }

  function mount(scope) {
    var md = root.ChatMarkdown;
    if (!md) return;
    [].forEach.call((scope || document).querySelectorAll("[data-md]:not(html):not([data-mounted])"), function (el) {
      el.setAttribute("data-mounted", "1");
      el.classList.add("md-raw");
      var f = document.createElement("div");
      f.className = "md-f md";
      f.innerHTML = md.render(el.textContent);
      el.parentNode.insertBefore(f, el.nextSibling);
      enhance(f);
    });
  }

  function mode(m) {
    if (m !== "raw" && m !== "formatted") {
      try { m = localStorage.getItem("oaap-md") || "formatted"; } catch (e) { m = "formatted"; }
    } else {
      try { localStorage.setItem("oaap-md", m); } catch (e) {}
    }
    document.documentElement.setAttribute("data-md-view", m);
    [].forEach.call(document.querySelectorAll("[data-md-mode]"), function (b) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-md-mode") === m));
    });
    return m;
  }

  root.OAAPView = {enhance: enhance, mount: mount, mode: mode, drawSvg: drawSvg, drawMermaid: drawMermaid};
})(typeof window !== "undefined" ? window : this);
