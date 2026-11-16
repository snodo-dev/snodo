(() => {
  const api = window.snodoProtocol;
  const el = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  const TYPES = ["acceptance", "architecture", "conventions", "llm", "performance", "planning", "protocol", "quality", "security", "testing"];
  const SEVERITIES = ["pass", "warn", "blocker"];
  const originalFor = (id) => (api.state?.original?.validators || []).find((row) => row.id === id) || {};
  const options = (select, values, current) => {
    for (const value of values) {
      const option = el("option", value);
      option.value = value;
      option.selected = value === current;
      select.append(option);
    }
  };
  function field(labelText, control) {
    const wrap = el("div");
    wrap.append(el("label", labelText), control);
    return wrap;
  }
  function validatorEdit(state, validator, active) {
    const edit = el("div", undefined, `edit${state.openValidator === validator.id ? " show" : ""}`);
    edit.dataset.edit = validator.id;
    const fields = el("div", undefined, "fields");
    const type = el("select"); type.dataset.f = "type";
    options(type, [...new Set([...TYPES, validator.type].filter(Boolean))], validator.type);
    const phase = el("div", undefined, "seg");
    for (const value of ["pre_execute", "post_execute"]) {
      const button = el("button", value, validator.phase === value ? "on" : "");
      button.type = "button"; button.dataset.phase = value;
      button.addEventListener("click", (event) => { event.stopPropagation(); validator.phase = value; api.notify(); });
      phase.append(button);
    }
    const severity = el("select"); severity.dataset.f = "severity_cap";
    options(severity, ["none", ...SEVERITIES, ...(validator.severity_cap && !SEVERITIES.includes(validator.severity_cap) ? [validator.severity_cap] : [])], validator.severity_cap || "none");
    const tools = el("input"); tools.dataset.f = "tools"; tools.placeholder = "none"; tools.value = (validator.tools || []).join(", ");
    fields.append(field("Type", type), field("Phase", phase), field("Severity cap", severity), field("Tools (read-only)", tools));
    edit.append(fields, el("label", "Criteria"));
    const original = originalFor(validator.id);
    (validator.criteria || []).forEach((criterion, index) => {
      const item = el("div", undefined, `crit${criterion !== (original.criteria || [])[index] ? " mod" : ""}`);
      const area = el("textarea", criterion); area.dataset.criterion = String(index);
      area.addEventListener("input", () => { validator.criteria[index] = area.value; item.classList.toggle("mod", area.value !== (original.criteria || [])[index]); api.notify(); });
      const remove = el("button", "×", "x"); remove.type = "button"; remove.title = "Remove";
      remove.addEventListener("click", (event) => { event.stopPropagation(); validator.criteria.splice(index, 1); api.notify(); });
      item.append(area, remove); edit.append(item);
    });
    const add = el("button", "+ Add criterion", "btn"); add.type = "button";
    add.addEventListener("click", (event) => { event.stopPropagation(); validator.criteria.push(""); api.notify(); const inputs = edit.parentNode?.querySelectorAll?.("textarea") || []; inputs[inputs.length - 1]?.focus(); });
    const toggle = el("button", `${active ? "Remove from" : "Add to"} ${state.data.protocol.initial_mode} mode`, "btn"); toggle.type = "button";
    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const mode = (state.data.modes || []).find((item) => item.id === state.data.protocol.initial_mode);
      if (!mode) return;
      mode.validators = mode.validators || [];
      const index = mode.validators.indexOf(validator.id);
      if (index >= 0) mode.validators.splice(index, 1); else mode.validators.push(validator.id);
      api.notify();
    });
    for (const control of [type, severity]) control.addEventListener("change", () => { validator[control.dataset.f] = control.value === "none" ? null : control.value; api.notify(); });
    tools.addEventListener("input", () => { validator.tools = tools.value.split(",").map((value) => value.trim()).filter(Boolean); api.notify(); });
    edit.append(add, toggle);
    return edit;
  }
  function validatorRow(state, validator, active) {
    const row = el("div", undefined, `row${state.openValidator === validator.id ? " open" : ""}${api.isChanged(validator.id) ? " mod" : ""}`);
    row.dataset.id = validator.id;
    const caret = el("span", "▶", "car"), summary = el("div"), criteria = validator.criteria || [];
    const phaseName = validator.phase || "pre_execute";
    summary.append(el("div", validator.id, "nm"), el("div", `${validator.type || ""} · ${criteria.length} ${criteria.length === 1 ? "criterion" : "criteria"}${(validator.tools || []).length ? " · reads files" : ""}`, "sb"));
    const phase = el("div", undefined, "ph"); phase.append(el("span", phaseName, `pill ${phaseName === "pre_execute" ? "run" : "abs"}`));
    const bars = el("div", undefined, "bars"); for (const _ of criteria.slice(0, 8)) bars.append(el("i", undefined, active ? "" : "x"));
    row.append(caret, summary, phase, bars, el("span", active ? "active" : "unused", `pill ${active ? "pass" : "off"}`));
    row.addEventListener("click", () => { state.openValidator = state.openValidator === validator.id ? null : validator.id; api.notify(); });
    return [row, validatorEdit(state, validator, active)];
  }
  function appendGroup(state, card, title, initial, activeIds, rows, unusedCount) {
    const group = el("div", undefined, "grp"); group.append(el("h3", title));
    if (initial) group.append(el("span", "initial", "pill pass"));
    group.append(el("span", `${activeIds.size} active · ${unusedCount} declared but unused`, "dim")); card.append(group);
    for (const validator of rows) { const [row, edit] = validatorRow(state, validator, activeIds.has(validator.id)); card.append(row, edit); }
  }
  function render(state) {
    if (!state.container || !state.data) return;
    const section = el("section", undefined, "sec validators-section"), head = el("div", undefined, "sec-h"), heading = el("div");
    heading.append(el("div", "Validators", "eyebrow"), el("div", "Ordered as the producer mode runs them. Open a row to edit its criteria. Nothing is written until you save.", "sub"));
    const add = el("button", "+ Add validator", "btn"); add.type = "button"; add.disabled = true; add.title = "Adding validators is not available yet";
    head.append(heading, add); const card = el("div", undefined, "card"), validators = state.validators || [];
    const byId = new Map(validators.map((validator) => [validator.id, validator])), modes = state.data.modes || [];
    const usedIds = new Set(modes.flatMap((mode) => mode.validators || [])), globallyUnused = validators.filter((validator) => !usedIds.has(validator.id));
    for (const mode of modes) {
      const activeIds = (mode.validators || []).filter((id) => byId.has(id)), activeSet = new Set(activeIds);
      const modeRows = [...activeIds.map((id) => byId.get(id)), ...validators.filter((validator) => !activeSet.has(validator.id) && usedIds.has(validator.id))];
      appendGroup(state, card, mode.name || mode.id, mode.id === (state.data.protocol || {}).initial_mode, activeSet, modeRows, modeRows.length - activeIds.length);
    }
    if (globallyUnused.length) appendGroup(state, card, "Unused", false, new Set(), globallyUnused, globallyUnused.length);
    section.append(head, card);
    const existing = state.container.querySelector(".validators-section");
    if (existing) {
      const focused = document.activeElement;
      if (focused && existing.contains(focused) && focused.dataset) {
        const selector = focused.dataset.criterion !== undefined
          ? `[data-criterion="${focused.dataset.criterion}"]`
          : focused.dataset.f !== undefined ? `[data-f="${focused.dataset.f}"]` : null;
        const replacement = selector && section.querySelector(selector);
        if (replacement) replacement.replaceWith(focused);
      }
      existing.replaceWith(section);
    } else state.container.append(section);
  }
  api.registerRenderer(() => {});
  api.registerRenderer(render);
})();
