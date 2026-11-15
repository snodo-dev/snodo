(() => {
  function text(value) {
    return document.createTextNode(value);
  }

  function appendInline(parent, source) {
    const pattern = /(`[^`\n]*`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[[^\]\n]+\]\([^\s)]+\))/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) parent.append(text(source.slice(cursor, match.index)));
      const token = match[0];
      let node = null;
      if (token[0] === "`" && token.length >= 2) {
        node = document.createElement("code");
        node.textContent = token.slice(1, -1);
      } else if (token.startsWith("**") || token.startsWith("__")) {
        node = document.createElement("strong");
        appendInline(node, token.slice(2, -2));
      } else if (token[0] === "*" || token[0] === "_") {
        node = document.createElement("em");
        appendInline(node, token.slice(1, -1));
      } else {
        const link = /^\[([^\]]+)\]\(([^\s)]+)\)$/.exec(token);
        if (link) {
          const address = link[2];
          const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(address);
          const permitted = !scheme || /^(https?):$/i.test(scheme[0]);
          if (permitted) {
            node = document.createElement("a");
            node.setAttribute("href", address);
            node.setAttribute("rel", "noopener noreferrer");
            appendInline(node, link[1]);
          }
        }
      }
      parent.append(node || text(token));
      cursor = pattern.lastIndex;
    }
    if (cursor < source.length) parent.append(text(source.slice(cursor)));
  }

  function render(source) {
    const fragment = document.createDocumentFragment();
    const lines = String(source == null ? "" : source).replace(/\r\n?/g, "\n").split("\n");
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      const fence = /^\s*```/.test(line);
      if (fence) {
        const code = document.createElement("pre");
        const contents = [];
        index += 1;
        while (index < lines.length && !/^\s*```/.test(lines[index])) {
          contents.push(lines[index++]);
        }
        if (index < lines.length) index += 1;
        const inlineCode = document.createElement("code");
        inlineCode.textContent = contents.join("\n");
        code.append(inlineCode);
        fragment.append(code);
        continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      if (heading) {
        const node = document.createElement(`h${heading[1].length}`);
        appendInline(node, heading[2]);
        fragment.append(node);
        index += 1;
        continue;
      }
      const listMatch = /^\s*(?:[-*+]\s+|\d+[.)]\s+)/.exec(line);
      if (listMatch) {
        const ordered = /^\s*\d/.test(line);
        const list = document.createElement(ordered ? "ol" : "ul");
        while (index < lines.length) {
          const item = /^\s*(?:[-*+]\s+|\d+[.)]\s+)(.*)$/.exec(lines[index]);
          if (!item || /^\s*\d/.test(lines[index]) !== ordered) break;
          const li = document.createElement("li");
          appendInline(li, item[1]);
          list.append(li);
          index += 1;
        }
        fragment.append(list);
        continue;
      }
      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim() &&
        !/^\s*```/.test(lines[index]) && !/^(#{1,6})\s+/.test(lines[index]) &&
        !/^\s*(?:[-*+]\s+|\d+[.)]\s+)/.test(lines[index])) {
        paragraph.push(lines[index++]);
      }
      const node = document.createElement("p");
      appendInline(node, paragraph.join(" "));
      fragment.append(node);
    }
    return fragment;
  }

  window.snodoMarkdown = { render };
})();
