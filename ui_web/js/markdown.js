/* ═══════════════════════════════════════════════════════════════════════
   markdown.js — tiny, dependency-free markdown → DOM renderer.
   Safety: everything goes through textContent first; the only HTML we
   generate is our own tags. No innerHTML of model text ever happens.
   ═══════════════════════════════════════════════════════════════════════ */
(function () {
  "use strict";

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inline(s) {
    // order matters: code first so later rules don't touch code spans
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|\W)\*([^*\n]+)\*(?=\W|$)/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // bare URLs
    s = s.replace(/(^|\s)(https?:\/\/[^\s<]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
    return s;
  }

  // Render markdown text into a DOM element (replaces children).
  function render(text, root) {
    root.textContent = "";
    const lines = String(text || "").split(/\r?\n/);
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // fenced code block
      const fence = line.match(/^\s*```(\w*)\s*$/);
      if (fence) {
        const lang = fence[1] || "";
        const codeLines = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) {
          codeLines.push(lines[i]); i++;
        }
        i++; // closing fence (or EOF)

        const pre = document.createElement("pre");
        const btn = document.createElement("button");
        btn.className = "code-copy";
        btn.textContent = "Copy";
        btn.addEventListener("click", () => {
          navigator.clipboard.writeText(codeLines.join("\n")).then(() => {
            btn.textContent = "Copied";
            setTimeout(() => { btn.textContent = "Copy"; }, 1200);
          }).catch(() => {});
        });
        pre.appendChild(btn);
        const code = document.createElement("code");
        if (lang) code.setAttribute("data-lang", lang);
        code.textContent = codeLines.join("\n");
        pre.appendChild(code);
        root.appendChild(pre);
        continue;
      }

      // heading
      const h = line.match(/^(#{1,3})\s+(.*)$/);
      if (h) {
        const el = document.createElement(["h1", "h2", "h3"][h[1].length - 1]);
        el.innerHTML = inline(h[2]);
        root.appendChild(el);
        i++; continue;
      }

      // blockquote
      if (/^>\s?/.test(line)) {
        const q = document.createElement("blockquote");
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^>\s?/, "")); i++;
        }
        q.innerHTML = inline(buf.join(" "));
        root.appendChild(q);
        continue;
      }

      // lists
      const isUl = line.match(/^\s*[-*•]\s+(.*)$/);
      const isOl = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (isUl || isOl) {
        const list = document.createElement(isUl ? "ul" : "ol");
        while (i < lines.length) {
          const m = lines[i].match(isUl ? /^\s*[-*•]\s+(.*)$/ : /^\s*\d+[.)]\s+(.*)$/);
          if (!m) break;
          const li = document.createElement("li");
          li.innerHTML = inline(m[1]);
          list.appendChild(li);
          i++;
        }
        root.appendChild(list);
        continue;
      }

      // table (simple: | a | b | with --- separator)
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1])) {
        const table = document.createElement("table");
        const headCells = line.trim().replace(/^\||\|$/g, "").split("|");
        const thead = document.createElement("thead");
        const trh = document.createElement("tr");
        headCells.forEach(c => {
          const th = document.createElement("th");
          th.innerHTML = inline(c.trim()); trh.appendChild(th);
        });
        thead.appendChild(trh); table.appendChild(thead);
        i += 2;
        const tbody = document.createElement("tbody");
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          const cells = lines[i].trim().replace(/^\||\|$/g, "").split("|");
          const tr = document.createElement("tr");
          cells.forEach(c => {
            const td = document.createElement("td");
            td.innerHTML = inline(c.trim()); tr.appendChild(td);
          });
          tbody.appendChild(tr); i++;
        }
        table.appendChild(tbody);
        root.appendChild(table);
        continue;
      }

      // blank line
      if (!line.trim()) { i++; continue; }

      // paragraph (merge consecutive non-special lines)
      const buf = [line];
      i++;
      while (i < lines.length && lines[i].trim() &&
             !/^\s*(```|#{1,3}\s|>|[-*•]\s|\d+[.)]\s|\|)/.test(lines[i])) {
        buf.push(lines[i]); i++;
      }
      const p = document.createElement("p");
      p.innerHTML = inline(buf.join(" "));
      root.appendChild(p);
    }
  }

  window.mdRender = render;
})();
