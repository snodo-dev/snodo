(() => {
  window.snodoViews = window.snodoViews || {};

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function elapsed(seconds) {
    return `${Math.floor(Math.max(0, Number(seconds) || 0) / 60)}m`;
  }

  function statusClass(status) {
    const normalized = String(status || "").toLowerCase().replace(/[^a-z]+/g, "-");
    const known = ["running", "in-progress", "completed", "pending", "blocked", "unmerged", "errored"];
    return known.includes(normalized) ? `now-status-${normalized}` : "now-status-neutral";
  }

  function ageLabel(generatedAt) {
    const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(generatedAt)) / 1000));
    if (!Number.isFinite(seconds)) return "as of an unknown time";
    if (seconds < 60) return `as of ${seconds}s ago`;
    if (seconds < 3600) return `as of ${Math.floor(seconds / 60)}m ago`;
    return `as of ${Math.floor(seconds / 3600)}h ago`;
  }

  const expandedRows = new Map();

  function expandableRow(label, key, defaultExpanded, className = "now-tree-row") {
    const row = element("div", undefined, className);
    const button = element("button", undefined, "now-expand");
    button.type = "button";
    const expanded = expandedRows.has(key) ? expandedRows.get(key) : defaultExpanded;
    button.setAttribute("aria-expanded", String(expanded));
    button.append(element("span", expanded ? "▾" : "▸", "now-disclosure"), label);
    button.addEventListener("click", () => {
      const next = button.getAttribute("aria-expanded") !== "true";
      expandedRows.set(key, next);
      button.setAttribute("aria-expanded", String(next));
      button.querySelector(".now-disclosure").textContent = next ? "▾" : "▸";
      const children = row.parentElement.querySelector(":scope > .now-tree-children");
      if (children) children.hidden = !next;
    });
    row.append(button);
    return { row, button, expanded };
  }

  function taskRow(task) {
    const row = element("li", undefined, `now-task ${statusClass(task.status)}`);
    row.append(element("span", task.status === "blocked" || task.status === "errored" ? "!" : task.status === "completed" ? "✓" : "•", "now-task-mark"));
    row.append(element("span", task.task_id, "now-task-id"));
    const jobId = task.job && task.job.job_id;
    if (jobId) row.dataset.jobId = jobId;
    row.append(element("span", task.status, `now-status ${statusClass(task.status)}`));
    row.append(element("span", task.elapsed_seconds == null ? "—" : elapsed(task.elapsed_seconds), "now-elapsed"));
    return row;
  }

  function render(data) {
    const fragment = document.createDocumentFragment();
    const counters = data.counters || {};
    const projects = Array.isArray(data.projects) ? data.projects : [];
    const waiting = Array.isArray(data.waiting) ? data.waiting : [];
    const working = projects.filter((project) => project.liveness === "working");
    const idle = projects.filter((project) => project.liveness !== "working");

    const header = element("div", undefined, "now-header");
    const heading = element("div", undefined, "now-heading");
    heading.append(element("p", `${data.window_hours || 24} hours`, "now-eyebrow"));
    heading.append(element("h1", "Now", "now-title"));
    header.append(heading);
    const freshness = element("div", undefined, "now-freshness");
    const age = element("span", ageLabel(data.generated_at), "now-age");
    age.dataset.generatedAt = data.generated_at || "";
    freshness.append(age);
    const refetch = element("button", "Refetch", "now-refetch");
    refetch.type = "button";
    refetch.addEventListener("click", () => window.snodoViews.now(refetch.closest("[data-now-root]")));
    freshness.append(refetch);
    header.append(freshness);
    fragment.append(header);

    const counts = element("div", undefined, "now-counts");
    const tile = (label, value, detail, unavailable = false) => {
      const item = element("div", undefined, "now-count");
      item.append(element("span", label, "now-count-label"));
      item.append(element("strong", unavailable ? "—" : String(value), "now-count-number"));
      if (detail) item.append(detail);
      if (unavailable) item.append(element("span", "unavailable", "now-unavailable"));
      counts.append(item);
    };
    tile("Active projects", counters.projects_active ?? 0);
    const plansDetail = element("span", counters.queues == null ? "Queues —" : `Queues ${counters.queues}`, "now-count-detail");
    tile("Plans / Queues", counters.plans ?? 0, plansDetail);
    tile("Waves", counters.waves ?? 0);
    const taskDetail = element("span", undefined, "now-status-breakdown");
    for (const [status, count] of Object.entries((counters.tasks && counters.tasks.by_status) || {})) {
      taskDetail.append(element("span", `${status} ${count}`, `now-status-detail ${statusClass(status)}`));
    }
    tile("Tasks", (counters.tasks && counters.tasks.count) ?? 0, taskDetail);
    const recons = counters.recons;
    const reconDetail = recons ? element("span", `Running ${recons.running} · Completed ${recons.completed}`, "now-count-detail") : undefined;
    tile("Recons", recons ? recons.count : null, reconDetail, recons == null);
    tile("Commits", counters.commits, undefined, counters.commits == null);
    fragment.append(counts);
    if (Object.values(counters).every((value) => value === 0 || (value && value.count === 0))) {
      fragment.append(element("p", "Nothing moved in the last 24 hours.", "now-quiet-note"));
    }

    fragment.append(element("h2", "In flight"));
    const flightNote = element("div", undefined, "now-flight-note");
    flightNote.append(element("span", "ordered by last activity — deepest first"));
    flightNote.append(element("span", "state", "now-column-label"));
    flightNote.append(element("span", "elapsed", "now-column-label"));
    fragment.append(flightNote);
    const flight = element("ul", undefined, "now-projects");
    if (!projects.length) flight.append(element("li", "Nothing is running.", "now-empty"));
    for (const project of [...working, ...idle]) {
      const key = `project:${project.name}`;
      const plans = project.plans || [];
      const outside = project.outside_plan || [];
      const activeTasks = [...plans.flatMap((plan) => (plan.wave_groups || []).flatMap((group) => group.tasks || [])), ...outside].length;
      const labels = { working: "Working", reporting_not_progressing: "Reporting, not progressing", no_recent_signal: "No recent signal" };
      const projectItem = element("li", undefined, "now-project");
      const projectRow = expandableRow(element("strong", project.name, "now-project-name"), key, true, "now-project-title");
      projectRow.button.append(element("span", labels[project.liveness] || labels.no_recent_signal, `now-liveness now-liveness-${project.liveness || "no_recent_signal"}`));
      projectRow.button.append(element("span", `${plans.length} plans · ${activeTasks} tasks · ${outside.length} outside a plan`, "now-project-summary"));
      projectRow.button.append(element("span", project.last_activity_at ? ageLabel(project.last_activity_at).replace("as of ", "") : "", "now-project-age"));
      projectRow.button.append(element("span", "open", "now-open-label"));
      projectItem.append(projectRow.row);
      const children = element("div", undefined, "now-tree-children");
      children.hidden = !projectRow.expanded;
      const planList = element("ul", undefined, "now-plans");
      for (const plan of plans) {
        const planKey = `${key}:plan:${plan.name}`;
        const groups = plan.wave_groups || [];
        const total = groups.reduce((sum, group) => sum + (group.total || (group.tasks || []).length), 0);
        const planItem = element("li", undefined, "now-plan");
        const planRow = expandableRow(element("span", undefined, "now-plan-label"), planKey, true, "now-plan-title");
        planRow.button.querySelector(".now-plan-label").append(element("span", "PLAN", "now-plan-tag"), element("strong", plan.name, "now-plan-name"), element("span", `${total} tasks · wave ${plan.wave} of ${plan.waves}`, "now-plan-summary"));
        const segments = element("span", undefined, "now-wave-segments");
        for (const group of groups) for (const task of group.tasks || []) segments.append(element("span", undefined, `now-wave-segment ${statusClass(task.status)}`));
        planRow.button.append(segments);
        planItem.append(planRow.row);
        const waveList = element("ul", undefined, "now-waves");
        for (const [waveIndex, group] of groups.entries()) {
          const tasks = group.tasks || [];
          const counts = group.status_counts || {};
          const summary = Object.entries(counts).filter(([, count]) => count).map(([status, count]) => `${count} ${status}`).join(", ");
          const allCompleted = tasks.length > 0 && tasks.every((task) => task.status === "completed");
          const waveItem = element("li", undefined, "now-wave");
          const waveRow = expandableRow(element("span", `Wave ${waveIndex + 1}`, "now-wave-name"), `${planKey}:wave:${group.id || waveIndex}`, !allCompleted, "now-wave-title");
          waveRow.button.append(element("span", `${group.total ?? tasks.length} tasks${summary ? ` — ${summary}` : ""}`, "now-wave-summary"));
          waveItem.append(waveRow.row);
          const taskList = element("ul", undefined, "now-tasks now-tree-children");
          taskList.hidden = !waveRow.expanded;
          for (const task of tasks) taskList.append(taskRow(task));
          waveItem.append(taskList);
          waveList.append(waveItem);
        }
        const waveChildren = element("div", undefined, "now-tree-children");
        waveChildren.hidden = !planRow.expanded;
        waveChildren.append(waveList);
        planItem.append(waveChildren);
        planList.append(planItem);
      }
      children.append(planList);
      if (outside.length) {
        const outsideItem = element("li", undefined, "now-outside");
        const outsideRow = expandableRow(element("span", `Outside a plan · ${outside.length} tasks`, "now-outside-label"), `${key}:outside`, false, "now-outside-title");
        outsideItem.append(outsideRow.row);
        const outsideTasks = element("ul", undefined, "now-tasks now-tree-children");
        outsideTasks.hidden = !outsideRow.expanded;
        for (const task of outside) outsideTasks.append(taskRow(task));
        outsideItem.append(outsideTasks);
        children.append(outsideItem);
      }
      projectItem.append(children);
      flight.append(projectItem);
    }
    fragment.append(flight);

    fragment.append(element("h2", "Waiting on you"));
    const waitingList = element("ul", undefined, "now-waiting");
    if (waiting.length === 0) waitingList.append(element("li", "Nothing is waiting on you.", "now-empty"));
    for (const item of waiting) {
      const entryClass = item.kind === "not_in_main" ? "now-waiting-entry now-waiting-unmerged" : "now-waiting-entry now-waiting-files";
      const entry = element("li", undefined, entryClass);
      if (item.kind === "not_in_main") entry.textContent = `${item.plan}: all tasks completed, not merged into main`;
      else if (item.kind === "uncommitted") {
        entry.append(document.createTextNode(`${(item.files || []).length} files differ from the last commit`));
        const files = element("ul", undefined, "now-waiting-files-list");
        for (const file of item.files || []) files.append(element("li", file, "now-waiting-file"));
        entry.append(files);
      }
      waitingList.append(entry);
    }
    fragment.append(waitingList);
    return fragment;
  }

  window.snodoViews.now = async function now(container) {
    if (!container) return;
    container.dataset.nowRoot = "true";
    try {
      const response = await fetch("/api/now");
      if (!response.ok) throw new Error("Request failed");
      const data = await response.json();
      const content = render(data);
      const notice = container.querySelector("[data-now-error]");
      container.replaceChildren(content);
      if (notice) notice.remove();
    } catch (_) {
      let notice = container.querySelector("[data-now-error]");
      if (!notice) {
        notice = element("p", "The dashboard could not be reached.", "view-message");
        notice.dataset.nowError = "true";
        container.append(notice);
      }
    }
  };

  if (!window.__snodoNowAgeTimer) {
    window.__snodoNowAgeTimer = window.setInterval(() => {
      document.querySelectorAll(".now-age[data-generated-at]").forEach((node) => {
        node.textContent = ageLabel(node.dataset.generatedAt);
      });
    }, 1000);
  }
})();
