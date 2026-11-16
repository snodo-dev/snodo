(() => {
  const api = window.snodoProtocol;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  let bar;
  let overlay;
  let preview = null;
  let statusMessage = "";

  const edits = (state) => api.changes(state);
  const emptyCriterion = (state) => (state.validators || []).some((validator) =>
    (validator.criteria || []).some((criterion) => !String(criterion).trim()));
  const failsRule = (state) => (preview?.rules || state.data?.rules || []).some((rule) => rule.ok === false) || preview?.valid === false;

  function updateSchemaPill(state, empty) {
    const pill = state.container?.querySelector(".live .pill");
    if (!pill) return;
    pill.className = `pill ${empty ? "abs" : "pass"}`;
    pill.textContent = empty ? "empty criterion" : "schema valid";
  }

  function paintDiff(pre) {
    pre.replaceChildren();
    const lines = preview?.diff ?? "";
    lines.split("\n").forEach((line, index, all) => {
      if (index === all.length - 1 && line === "") return;
      let kind = "c";
      if (line.startsWith("+++") || line.startsWith("---")) kind = "c";
      else if (line.startsWith("+")) kind = "a";
      else if (line.startsWith("-")) kind = "d";
      pre.append(el("span", line, kind), document.createTextNode("\n"));
    });
  }

  function makeOverlay() {
    overlay = el("div", undefined, "diff");
    const box = el("div", undefined, "box");
    const titleRow = el("div");
    titleRow.append(el("h2", "Review changes"), el("span", "0 changes", "pill aut"));
    const copy = el("p", "A diff of .snodo/protocol.yml. Saving writes the file; committing and pushing stay with you.", "dim");
    const pre = el("pre");
    const actions = el("div");
    const back = el("button", "Back", "btn"); back.type = "button";
    const write = el("button", "Write file", "btn pri"); write.type = "button";
    actions.append(back, write);
    box.append(titleRow, copy, pre, actions);
    overlay.append(box);
    back.addEventListener("click", () => overlay.classList.remove("show"));
    write.addEventListener("click", save);
    return { overlay, pre, count: titleRow.children[1], write };
  }

  async function review() {
    const state = api.state;
    const body = { project: state.project, etag: state.data.etag, edits: edits(state) };
    try {
      const response = await fetch("/api/protocol/preview", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json();
      if (!response.ok) { statusMessage = result.error || "Protocol preview failed."; render(state); return; }
      preview = result;
      const view = makeOverlay();
      view.count.textContent = `${result.changes.length} ${result.changes.length === 1 ? "change" : "changes"}`;
      paintDiff(view.pre);
      view.write.disabled = emptyCriterion(state) || failsRule(state);
      state.container.append(view.overlay);
      overlay = view.overlay;
      overlay.classList.add("show");
      render(state);
    } catch (error) { statusMessage = error.message; render(state); }
  }

  async function save() {
    const state = api.state;
    const body = { project: state.project, etag: state.data.etag, page_token: state.data.page_token, edits: edits(state) };
    try {
      const response = await fetch("/api/protocol/save", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const result = await response.json();
      if (!response.ok) {
        statusMessage = result.error || "Protocol file could not be saved.";
        overlay?.classList.remove("show"); render(state); return;
      }
      overlay?.classList.remove("show");
      statusMessage = "Written to .snodo/protocol.yml. Commit and push when ready.";
      state.data = result; state.original = JSON.parse(JSON.stringify(result));
      state.validators = JSON.parse(JSON.stringify(result.validators || []));
      preview = null;
      api.notify();
    } catch (error) { statusMessage = error.message; overlay?.classList.remove("show"); render(state); }
  }

  function render(state) {
    if (!state.container || !state.data) return;
    if (!bar || !bar.isConnected) {
      bar = el("div", undefined, "bar");
      const wrap = el("div", undefined, "wrap");
      const count = el("span", undefined, "pill aut");
      const message = el("span", undefined, "dim");
      const actions = el("div", undefined, "sp");
      const discard = el("button", "Discard", "btn"); discard.type = "button";
      const reviewButton = el("button", "Review diff", "btn"); reviewButton.type = "button";
      const saveButton = el("button", "Save to protocol.yml", "btn pri"); saveButton.type = "button";
      actions.append(discard, reviewButton, saveButton); wrap.append(count, message, actions); bar.append(wrap);
      state.container.append(bar);
      discard.addEventListener("click", () => {
        state.validators = JSON.parse(JSON.stringify(state.original.validators || []));
        preview = null; statusMessage = ""; api.notify();
      });
      reviewButton.addEventListener("click", review); saveButton.addEventListener("click", review);
      bar._parts = { count, message, discard, reviewButton, saveButton };
    }
    const current = edits(state); const bad = emptyCriterion(state); const blocked = bad || failsRule(state);
    const parts = bar._parts;
    parts.count.textContent = `${current.length} ${current.length === 1 ? "change" : "changes"}`;
    parts.message.textContent = statusMessage || (bad ? "A criterion is empty. Fill it or remove it before saving." : current.length ? `Unsaved edits in ${[...new Set(current.map((edit) => edit.validator_id))].join(", ")}.` : "No unsaved edits.");
    parts.discard.disabled = !current.length;
    parts.reviewButton.disabled = parts.saveButton.disabled = !current.length || blocked;
    updateSchemaPill(state, bad);
  }
  api.registerRenderer(render);
})();
