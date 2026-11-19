(() => {
  window.snodoViews = window.snodoViews || {};

  let selectedDays = 21;
  let containerForView;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function relativeTime(value) {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(timestamp)) return "unknown";
    const hours = Math.max(0, Math.floor((Date.now() - timestamp) / 3600000));
    if (hours < 24) return `${hours}h`;
    const days = Math.floor(hours / 24);
    if (days < 30) return `${days}d`;
    return `${Math.floor(days / 30)}mo`;
  }

  function render(data) {
    const fragment = document.createDocumentFragment();
    const days = Number(data.window_days) || selectedDays;
    const sentBack = Number(data.sent_back) || 0;
    const landed = Number(data.landed_after_retry) || 0;
    fragment.append(element("p", sentBack === 0
      ? `The gates sent back no tasks in the last ${days} days.`
      : `The gates sent back ${sentBack} ${sentBack === 1 ? "task" : "tasks"} in the last ${days} days. ${landed} landed after being rewritten.`, "gates-summary"));

    const controls = element("div", undefined, "gates-windows");
    for (const option of [7, 21, 30]) {
      const button = element("button", `${option}d`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(days === option));
      button.addEventListener("click", () => {
        selectedDays = option;
        window.snodoViews.gates(containerForView);
      });
      controls.append(button);
    }
    fragment.append(controls);

    const panels = element("div", undefined, "gates-panels");
    const validators = Array.isArray(data.by_validator) ? data.by_validator : [];
    const validatorPanel = element("section", undefined, "gates-panel");
    validatorPanel.append(element("h2", "What the validators caught"));
    const maxValidator = Math.max(1, ...validators.map((row) => Number(row.count) || 0));
    if (validators.length === 0) {
      validatorPanel.append(element("p", "No validator send-backs in this window.", "gates-empty"));
    }
    for (const row of validators) {
      const line = element("div", undefined, "gates-bar-row");
      line.append(element("span", row.validator || "unknown"));
      const track = element("span", undefined, "gates-track");
      const bar = element("span", undefined, "gates-bar");
      bar.style.width = `${Math.max(0, Number(row.count) || 0) / maxValidator * 100}%`;
      bar.setAttribute("aria-label", `${row.count} sent back`);
      track.append(bar);
      line.append(track, element("strong", String(row.count ?? 0)));
      validatorPanel.append(line);
    }
    panels.append(validatorPanel);

    const projects = Array.isArray(data.by_project) ? data.by_project : [];
    const projectPanel = element("section", undefined, "gates-panel");
    projectPanel.append(element("h2", "Per project"));
    if (projects.length === 0) {
      projectPanel.append(element("p", "No project send-backs in this window.", "gates-empty"));
    }
    for (const project of projects) {
      const count = Number(project.sent_back) || 0;
      const retried = Number(project.landed_after_retry) || 0;
      const line = element("div", undefined, "gates-bar-row");
      line.append(element("span", project.name || "unknown"));
      const track = element("span", undefined, "gates-track");
      const bar = element("span", undefined, "gates-bar");
      bar.style.width = `${count ? Math.min(100, retried / count * 100) : 0}%`;
      bar.setAttribute("aria-label", `${retried} landed after retry out of ${count} sent back`);
      track.append(bar);
      line.append(track, element("strong", `${retried} / ${count}`));
      projectPanel.append(line);
    }
    panels.append(projectPanel);
    const countPanel = element("section", undefined, "gates-panel gates-counts");
    countPanel.append(element("strong", String(sentBack)), element("span", "sent back"), element("strong", String(landed)), element("span", "landed after retry"));
    panels.append(countPanel);
    fragment.append(panels);

    fragment.append(element("h2", "The record"));
    const records = Array.isArray(data.records) ? data.records : [];
    if (records.length === 0) {
      fragment.append(element("p", "No gate send-backs in this window.", "gates-empty"));
      return fragment;
    }
    const table = element("table", undefined, "gates-records");
    const head = element("tr");
    for (const heading of ["Task", "Project", "What stopped it", "Outcome", "When"]) head.append(element("th", heading));
    const thead = element("thead");
    thead.append(head);
    table.append(thead);
    const tbody = element("tbody");
    for (const record of records) {
      const row = element("tr");
      row.append(element("td", record.task || ""));
      row.append(element("td", record.project || ""));
      row.append(element("td", record.validator || "unknown"));
      const landedAfterRetry = record.outcome === "landed_after_retry";
      row.append(element("td", landedAfterRetry ? "Landed after a retry" : "Not landed yet", landedAfterRetry ? "gates-outcome-landed" : "gates-outcome-pending"));
      row.append(element("td", relativeTime(record.when)));
      tbody.append(row);
    }
    table.append(tbody);
    fragment.append(table);
    return fragment;
  }

  window.snodoViews.gates = async function gates(container) {
    containerForView = container;
    try {
      const response = await fetch("/api/gates" + `?days=${selectedDays}`);
      if (!response.ok) throw new Error("Request failed");
      const data = await response.json();
      container.replaceChildren(render(data));
    } catch (_) {
      let notice = container.querySelector("[data-gates-error]");
      if (!notice) {
        notice = element("p", "The gates dashboard could not be reached.", "view-message");
        notice.dataset.gatesError = "true";
        container.append(notice);
      }
    }
  };
})();
