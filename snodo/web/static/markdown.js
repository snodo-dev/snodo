(() => {
  function text(value) {
    return document.createTextNode(value);
  }

  function appendInline(parent, source) {
    const pattern = /(`[^`\n]*`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|(?<![\w])_[^_\n]+_(?![\w])|\[[^\]\n]+\]\([^\s)]+\))/g;
    let cursor = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > cursor) parent.append(text(source.slice(cursor, match.index)));
      const token = match[0];
      let node = null;
      if (token[0] === "`") {
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
          if (!scheme || /^(https?):$/i.test(scheme[0])) {
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

  function appendParagraph(node, lines) {
    const lead = /^([A-Za-z][A-Za-z0-9 -]{0,35}:)(\s*)(.*)$/.exec(lines[0]);
    if (lead) {
      const label = document.createElement("span");
      label.setAttribute("class", "markdown-label");
      label.textContent = lead[1];
      node.append(label);
      if (lead[2]) node.append(text(lead[2]));
      appendInline(node, lead[3]);
    } else {
      appendInline(node, lines[0]);
    }
    for (const line of lines.slice(1)) {
      node.append(document.createElement("br"));
      appendInline(node, line);
    }
  }

  function render(source) {
    const fragment = document.createDocumentFragment();
    const lines = String(source == null ? "" : source).replace(/\r\n?/g, "\n").split("\n");
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      if (!line.trim()) { index += 1; continue; }
      if (/^\s*```/.test(line)) {
        const code = document.createElement("pre");
        const contents = [];
        index += 1;
        while (index < lines.length && !/^\s*```/.test(lines[index])) contents.push(lines[index++]);
        if (index < lines.length) index += 1;
        const inlineCode = document.createElement("code");
        inlineCode.textContent = contents.join("\n");
        code.append(inlineCode);
        fragment.append(code);
        continue;
      }
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const bareHeading = /^[A-Z ]{3,30}$/.test(line.trim()) && line.trim().length === line.length;
      if (heading || bareHeading) {
        const node = document.createElement(heading ? `h${heading[1].length}` : "h2");
        appendInline(node, heading ? heading[2] : line);
        fragment.append(node);
        index += 1;
        continue;
      }
      if (/^\s*(?:(?:-{3,})|(?:\*\s*){3,}|(?:_\s*){3,})\s*$/.test(line)) {
        fragment.append(document.createElement("hr"));
        index += 1;
        continue;
      }
      if (/^\s*>/.test(line)) {
        const quote = document.createElement("blockquote");
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          const paragraph = document.createElement("p");
          appendInline(paragraph, lines[index++].replace(/^\s*>\s?/, ""));
          quote.append(paragraph);
        }
        fragment.append(quote);
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
        !/^[A-Z ]{3,30}$/.test(lines[index]) &&
        !/^\s*(?:[-*+]\s+|\d+[.)]\s+|>)/.test(lines[index]) &&
        !/^\s*(?:(?:-{3,})|(?:\*\s*){3,}|(?:_\s*){3,})\s*$/.test(lines[index])) {
        paragraph.push(lines[index++]);
      }
      const node = document.createElement("p");
      appendParagraph(node, paragraph);
      fragment.append(node);
    }
    return fragment;
  }

  window.snodoMarkdown = { render };
})();
