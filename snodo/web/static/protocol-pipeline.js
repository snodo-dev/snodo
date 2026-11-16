(() => {
  const api = window.snodoProtocol;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };

  const pillClasses = { pre_execute: "pill run", coder: "pill", post_execute: "pill abs", merge: "pill pass" };

  function render(state) {
    if (!state.container || !state.data) return;
    const protocol = state.data.protocol || {};
    const mode = (state.data.modes || []).find((item) => item.id === protocol.initial_mode) || {};
    const byId = new Map((state.validators || []).map((validator) => [validator.id, validator]));
    const section = el("section", undefined, "sec pipeline-section");
    const head = el("div", undefined, "sec-h");
    const title = el("div");
    title.append(el("div", "Pipeline", "eyebrow"), el("div", "What a task passes through, in order. Click a validator chip to jump to its editor.", "sub"));
    head.append(title);
    const card = el("div", undefined, "card");
    const flow = el("div", undefined, "flow");
    const execution = state.data.execution || {};
    const stages = [
      ["pre_execute", "Spec checks", "before any code is written", "run", "pre_execute"],
      ["coder", "Implement", "model comes from config", "", "coder"],
      ["post_execute", "Gate", "checks the work", "abs", "post_execute"],
      ["merge", "Deliver", execution.delivery || "", "pass", "deliver"],
    ];
    for (const [pill, heading, detail, variant, kind] of stages) {
      const stage = el("div", undefined, kind === "coder" ? "stage coder" : "stage");
      stage.append(el("span", pill, pillClasses[pill]), el("h4", heading), el("span", detail, "dim"));
      const chips = el("div", undefined, "chips");
      if (kind === "coder") {
        for (const [label, value] of [["retries", execution.max_retries], ["recovery depth", execution.max_recovery_depth], ["fix attempts", execution.max_total_fix_attempts]]) chips.append(el("span", `${label} ${value}`));
      } else if (kind === "deliver") {
        for (const tool of mode.tools || []) chips.append(el("span", tool));
      } else {
        for (const id of mode.validators || []) {
          const validator = byId.get(id);
          if (validator && validator.phase !== kind) continue;
          const chip = el("span", id, validator ? (api.isChanged(id) ? "mod" : "") : "off");
          if (validator) {
            chip.setAttribute("role", "button");
            chip.tabIndex = 0;
            const open = () => {
              state.openValidator = id;
              api.notify();
              requestAnimationFrame(() => {
                const row = state.container.querySelector(`[data-id="${id}"]`);
                if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
              });
            };
            chip.addEventListener("click", open);
            chip.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") open(); });
          }
          chips.append(chip);
        }
      }
      stage.append(chips);
      flow.append(stage);
    }
    card.append(flow);
    section.append(head, card);
    const existing = state.container.querySelector(".pipeline-section");
    if (existing) existing.replaceWith(section); else state.container.append(section);
  }

  api.registerRenderer(() => {});
  api.registerRenderer(render);
})();
