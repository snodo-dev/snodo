(() => {
  const api = window.snodoProtocol;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const defaults = { max_retries: 3, branch_ttl_days: 7, max_recovery_depth: 3, max_total_fix_attempts: 10, branch_prefix: "task", parallel_runs: 1 };
  api.hasFailingRule = (state = api.state) => (state.data?.rules || []).some((rule) => rule.ok === false);

  function render(state) {
    if (!state.container || !state.data) return;
    const execution = state.data.execution || {};
    const values = [
      ["max_retries", execution.max_retries], ["branch_ttl_days", execution.branch_ttl_days],
      ["max_recovery_depth", execution.max_recovery_depth], ["max_total_fix_attempts", execution.max_total_fix_attempts],
      ["branch_prefix", execution.branch_prefix], ["queue.parallel_runs", execution.parallel_runs ?? state.data.queue?.parallel_runs],
    ];
    const section = (eyebrow, sub, extraClass) => {
      const sec = el("section", undefined, `sec ${extraClass}`);
      const head = el("div", undefined, "sec-h");
      const title = el("div");
      title.append(el("div", eyebrow, "eyebrow"), el("div", sub, "sub"));
      head.append(title);
      sec.append(head);
      return sec;
    };
    const executionSection = section("Execution", "Branch isolation, retries and delivery. Delivery and auto_merge cannot both be set.", "execution-section");
    const card = el("div", undefined, "card");
    const kv = el("div", undefined, "kv");
    for (const [key, value] of values) {
      const cell = el("div");
      cell.append(el("b", key), el("span", value == null ? (defaults[key] ?? "—") : String(value)));
      kv.append(cell);
    }
    card.append(kv);
    executionSection.append(card);

    const rulesSection = section("Rules checked on save", "Save stays disabled while any of these fail.", "rules-section");
    const rulesCard = el("div", undefined, "card");
    const list = el("ul", undefined, "rules");
    for (const rule of state.data.rules || []) {
      const row = el("li");
      row.append(el("span", rule.ok === false ? "fail" : "ok", rule.ok === false ? "pill abs" : "pill pass"));
      row.append(el("code", rule.id));
      const description = el("span", rule.description || "", "dim");
      row.append(description);
      if (rule.detail) row.append(el("span", rule.detail, "dim"));
      list.append(row);
    }
    rulesCard.append(list);
    rulesSection.append(rulesCard);
    for (const node of [executionSection, rulesSection]) {
      const className = node.className.split(" ").at(-1);
      const existing = state.container.querySelector(`.${className}`);
      if (existing) existing.replaceWith(node); else state.container.append(node);
    }
  }
  api.registerRenderer(render);
})();
