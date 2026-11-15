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
    const known = ["running", "in-progress", "completed", "pending", "blocked", "unmerged", "errored", "failed"];
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
  let selectedTaskKey = null;
  let selectedTaskDetails = null;
  let currentData = null;
  let currentContainer = null;
  const specCache = new Map();

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

  function taskRow(task, context) {
    const row = element("li", undefined, `now-task ${statusClass(task.status)}`);
    const key = `${context.project}:${task.task_id}`;
    if (selectedTaskKey === key) selectedTaskDetails = { task, context, key };
    const button = element("button", undefined, `now-task-button${selectedTaskKey === key ? " now-task-selected" : ""}`);
    button.type = "button";
    button.setAttribute("aria-expanded", String(selectedTaskKey === key));
    button.setAttribute("aria-pressed", String(selectedTaskKey === key));
    button.dataset.taskKey = key;
    button.append(element("span", task.status === "blocked" || task.status === "errored" ? "!" : task.status === "completed" ? "✓" : "•", "now-task-mark"));
    button.append(element("span", task.task_id, "now-task-id"));
    const jobId = task.job && task.job.job_id; // Reads task.job.job_id from the API contract.
    if (jobId) button.dataset.jobId = jobId;
    button.append(element("span", task.status, `now-status ${statusClass(task.status)}`));
    button.append(element("span", task.elapsed_seconds == null ? "—" : elapsed(task.elapsed_seconds), "now-elapsed"));
    button.addEventListener("click", () => {
      selectedTaskKey = key;
      currentContainer.replaceChildren(render(currentData, currentContainer));
      const close = currentContainer.querySelector(".now-task-panel-close");
      if (close) close.focus();
    });
    row.append(button);
    return row;
  }

  function showSpecMessage(body, message, className = "now-task-panel-message") {
    body.replaceChildren(element("p", message, className));
  }

  function renderSpec(body, markdown) {
    body.replaceChildren();
    if (window.snodoMarkdown && typeof window.snodoMarkdown.render === "function") {
      body.append(window.snodoMarkdown.render(markdown));
    } else {
      const pre = element("pre", undefined, "now-task-panel-plain");
      pre.textContent = markdown;
      body.append(pre);
    }
  }

  function loadSpec(body, task, context, key, retry = false) {
    if (context.plan === "Outside a plan") {
      showSpecMessage(body, "This task ran outside a plan and has no spec.");
      return;
    }
    let entry = specCache.get(key);
    if (entry && !retry) {
      if (entry.state === "loaded") renderSpec(body, entry.markdown);
      else if (entry.state === "missing") showSpecMessage(body, "No spec on this machine for this task.");
      else if (entry.state === "failed") showRetry(body, task, context, key);
      else showSpecMessage(body, "Loading spec…", "now-task-panel-loading");
      return;
    }
    entry = { state: "loading", scrollTop: body.scrollTop };
    specCache.set(key, entry);
    showSpecMessage(body, "Loading spec…", "now-task-panel-loading");
    const url = `/api/now/spec?project=${encodeURIComponent(context.project)}&plan=${encodeURIComponent(context.plan)}&task=${encodeURIComponent(task.task_id)}`;
    fetch(url).then((response) => {
      if (response.status === 404) return { missing: true };
      if (!response.ok) throw new Error("Spec request failed");
      return response.json();
    }).then((result) => {
      if (specCache.get(key) !== entry) return;
      if (result.missing) entry.state = "missing";
      else { entry.state = "loaded"; entry.markdown = result.markdown; }
      if (selectedTaskKey !== key || !body.isConnected) return;
      if (entry.state === "missing") showSpecMessage(body, "No spec on this machine for this task.");
      else renderSpec(body, entry.markdown);
      body.scrollTop = entry.scrollTop;
    }).catch(() => {
      if (specCache.get(key) !== entry) return;
      entry.state = "failed";
      if (selectedTaskKey === key && body.isConnected) showRetry(body, task, context, key);
    });
  }

  function showRetry(body, task, context, key) {
    body.replaceChildren();
    body.append(element("p", "The spec could not be loaded.", "now-task-panel-error"));
    const retry = element("button", "Retry", "now-task-panel-retry");
    retry.type = "button";
    retry.addEventListener("click", () => loadSpec(body, task, context, key, true));
    body.append(retry);
  }

  function render(data, container) {
    selectedTaskDetails = null;
    const fragment = document.createDocumentFragment();
    const counters = data.counters || {};
    const projects = Array.isArray(data.projects) ? data.projects : [];
    const waiting = Array.isArray(data.waiting) ? data.waiting : [];
    const working = projects.filter((project) => project.liveness === "working");
    const idle = projects.filter((project) => project.liveness !== "working");

    const header = element("div", undefined, "now-header");
    header.dataset.windowHours = String(data.window_hours || 24);
    const heading = element("div", undefined, "now-heading");
    heading.append(element("p", "Last 24 hours", "now-eyebrow"));
    heading.append(element("h1", "Now", "now-title"));
    header.append(heading);
    const freshness = element("div", undefined, "now-freshness");
    const live = element("span", undefined, "now-live");
    live.append(element("span", undefined, "now-live-dot"), element("span", "Refreshes every ten seconds", "now-refresh-text"));
    freshness.append(live);
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
    const attentionStatuses = new Set(["failed", "blocked", "errored"]);
    const taskCounts = (counters.tasks && counters.tasks.by_status) || {};
    const hasAttentionCount = Object.entries(taskCounts).some(([status, count]) => attentionStatuses.has(status.toLowerCase()) && Number(count) > 0);
    const tasksTile = element("div", undefined, `now-count${hasAttentionCount ? " now-count-attention" : ""}`);
    tasksTile.append(element("span", "Tasks", "now-count-label"));
    tasksTile.append(element("strong", String((counters.tasks && counters.tasks.count) ?? 0), "now-count-number"));
    if (taskDetail) tasksTile.append(taskDetail);
    counts.append(tasksTile);
    const recons = counters.recons;
    const reconDetail = recons ? element("span", `Running ${recons.running} · Completed ${recons.completed}`, "now-count-detail") : undefined;
    tile("Recons", recons ? recons.count : null, reconDetail, recons == null);
    tile("Commits", counters.commits, undefined, counters.commits == null);
    fragment.append(counts);
    const attentionTasks = [];
    for (const project of projects) {
      for (const plan of project.plans || []) {
        for (const [waveIndex, group] of (plan.wave_groups || []).entries()) {
          for (const task of group.tasks || []) if (attentionStatuses.has(String(task.status).toLowerCase())) attentionTasks.push({ task, project: project.name, plan: plan.name, wave: waveIndex + 1, projectKey: `project:${project.name}`, planKey: `project:${project.name}:plan:${plan.name}`, waveKey: `project:${project.name}:plan:${plan.name}:wave:${group.id || waveIndex}` });
        }
      }
      for (const task of project.outside_plan || []) if (attentionStatuses.has(String(task.status).toLowerCase())) attentionTasks.push({ task, project: project.name, plan: "Outside a plan", wave: null, projectKey: `project:${project.name}`, outsideKey: `project:${project.name}:outside` });
    }
    if (attentionTasks.length) {
      const first = attentionTasks[0];
      const strip = element("div", undefined, "now-attention-strip");
      strip.append(element("span", first.task.status, "now-attention-status"));
      strip.append(element("span", `${first.task.task_id} · ${first.plan}`, "now-attention-task"));
      if (attentionTasks.length > 1) strip.append(element("span", `and ${attentionTasks.length - 1} more`, "now-attention-more"));
      const show = element("button", "Show task", "now-attention-show");
      show.type = "button";
      show.addEventListener("click", () => {
        expandedRows.set(first.projectKey, true);
        if (first.planKey) expandedRows.set(first.planKey, true);
        if (first.waveKey) expandedRows.set(first.waveKey, true);
        if (first.outsideKey) expandedRows.set(first.outsideKey, true);
        selectedTaskKey = `${first.project}:${first.task.task_id}`;
        currentContainer.replaceChildren(render(currentData, currentContainer));
        const row = currentContainer.querySelector(`[data-task-key="${CSS.escape(selectedTaskKey)}"]`);
        if (row) { row.scrollIntoView({ behavior: "smooth", block: "center" }); row.focus(); }
        const close = currentContainer.querySelector(".now-task-panel-close");
        if (close) close.focus();
      });
      strip.append(show);
      fragment.append(strip);
    }
    if (Object.values(counters).every((value) => value === 0 || (value && value.count === 0))) {
      fragment.append(element("p", "Nothing moved in the last 24 hours.", "now-quiet-note"));
    }

    fragment.append(element("h2", "In flight"));
    const flightNote = element("div", undefined, "now-flight-note");
    flightNote.append(element("span", "ordered by last activity — deepest first"));
    flightNote.append(element("span", "state", "now-column-label"));
    flightNote.append(element("span", "elapsed", "now-column-label"));
    fragment.append(flightNote);
    const flight = element("ul", undefined, "now-projects now-fleet");
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
      projectRow.button.append(element("span", `${plans.length} plans · ${activeTasks} tasks`, "now-project-summary"));
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
        const tasksInPlan = groups.flatMap((group) => group.tasks || []);
        const taskStatuses = tasksInPlan.map((task) => String(task.status).toLowerCase());
        const failedLike = taskStatuses.some((status) => ["failed", "blocked", "errored"].includes(status));
        const runningLike = taskStatuses.some((status) => ["in_progress", "in progress", "in-progress", "running"].includes(status));
        const completed = tasksInPlan.length > 0 && taskStatuses.every((status) => status === "completed");
        const stateTask = tasksInPlan.find((task) => failedLike ? ["failed", "blocked", "errored"].includes(String(task.status).toLowerCase()) : runningLike ? ["in_progress", "in progress", "in-progress", "running"].includes(String(task.status).toLowerCase()) : completed ? String(task.status).toLowerCase() === "completed" : true);
        const planState = stateTask ? stateTask.status : "pending";
        const planRow = expandableRow(element("span", undefined, "now-plan-label"), planKey, failedLike || runningLike, "now-plan-title");
        const nameBlock = element("span", undefined, "now-plan-name-block");
        nameBlock.append(element("strong", plan.name, "now-plan-name"), element("span", `${total} tasks · wave ${plan.wave} of ${plan.waves}`, "now-plan-summary"));
        planRow.button.querySelector(".now-plan-label").append(nameBlock);
        const segments = element("span", undefined, "now-wave-segments");
        segments.setAttribute("role", "img");
        const statusCounts = {};
        for (const group of groups) for (const task of group.tasks || []) { segments.append(element("span", undefined, `now-wave-segment ${statusClass(task.status)}`)); statusCounts[task.status] = (statusCounts[task.status] || 0) + 1; }
        segments.setAttribute("aria-label", Object.entries(statusCounts).map(([status, count]) => `${count} ${status}`).join(", ") || "No tasks");
        planRow.button.append(segments, element("span", planState, `now-status now-plan-state ${statusClass(planState)}`), element("span", plan.last_activity_at ? ageLabel(plan.last_activity_at).replace("as of ", "") : "", "now-plan-age"));
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
          for (const task of tasks) taskList.append(taskRow(task, { project: project.name, plan: plan.name, wave: waveIndex + 1 }));
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
        for (const task of outside) outsideTasks.append(taskRow(task, { project: project.name, plan: "Outside a plan", wave: null }));
        outsideItem.append(outsideTasks);
        children.append(outsideItem);
      }
      projectItem.append(children);
      flight.append(projectItem);
    }
    const flightLayout = element("div", undefined, "now-flight-layout");
    flightLayout.append(flight);
    if (selectedTaskKey && !selectedTaskDetails) selectedTaskKey = null;
    if (selectedTaskDetails) {
      const { task, context, key } = selectedTaskDetails;
      const panel = element("aside", undefined, "now-task-panel");
      panel.setAttribute("role", "complementary");
      panel.setAttribute("aria-label", `Task ${task.task_id} details`);
      panel.addEventListener("keydown", (event) => {
        if (event.key === "Escape") closeButton.click();
      });
      const panelHeader = element("div", undefined, "now-task-panel-header");
      panelHeader.append(element("h3", task.task_id, "now-task-panel-id"));
      const closeButton = element("button", "Close", "now-task-panel-close");
      closeButton.type = "button";
      closeButton.addEventListener("click", () => {
        selectedTaskKey = null;
        currentContainer.replaceChildren(render(currentData, currentContainer));
        const row = currentContainer.querySelector(`[data-task-key="${CSS.escape(key)}"]`);
        if (row) row.focus();
      });
      panelHeader.append(closeButton);
      panel.append(panelHeader);
      panel.append(element("p", task.status, `now-status ${statusClass(task.status)}`));
      panel.append(element("p", context.plan === "Outside a plan" ? "Outside a plan" : `${context.plan} · Wave ${context.wave}`, "now-task-panel-location"));
      const jobId = task.job && task.job.job_id; // Reads task.job.job_id from the API contract.
      if (jobId) panel.append(element("p", jobId, "now-task-panel-job"));
      if (task.elapsed_seconds != null) panel.append(element("p", elapsed(task.elapsed_seconds), "now-task-panel-elapsed"));
      const specBody = element("div", undefined, "now-task-panel-spec");
      const cachedSpec = specCache.get(key);
      if (cachedSpec) specBody.scrollTop = cachedSpec.scrollTop || 0;
      specBody.addEventListener("scroll", () => {
        const entry = specCache.get(key);
        if (entry) entry.scrollTop = specBody.scrollTop;
      });
      panel.append(specBody);
      loadSpec(specBody, task, context, key);
      flightLayout.append(panel);
    }
    fragment.append(flightLayout);

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
      currentData = data;
      currentContainer = container;
      const content = render(data, container);
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
