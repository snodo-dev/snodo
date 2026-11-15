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
    return `now-status-${String(status || "pending").toLowerCase().replace(/[^a-z]+/g, "-")}`;
  }

  function ageLabel(generatedAt) {
    const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(generatedAt)) / 1000));
    if (!Number.isFinite(seconds)) return "as of an unknown time";
    if (seconds < 60) return `as of ${seconds}s ago`;
    if (seconds < 3600) return `as of ${Math.floor(seconds / 60)}m ago`;
    return `as of ${Math.floor(seconds / 3600)}h ago`;
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
    const flight = element("ul", undefined, "now-projects");
    if (working.length === 0) flight.append(element("li", "Nothing is running.", "now-empty"));
    for (const project of [...working, ...idle]) {
      const isWorking = project.liveness === "working";
      const projectItem = element("li", undefined, `now-project${isWorking ? "" : " now-project-idle"}`);
      const projectTitle = element("div", undefined, "now-project-title");
      projectTitle.append(element("strong", project.name, "now-project-name"));
      if (!isWorking) projectTitle.append(element("span", "— idle", "now-idle-label"));
      projectItem.append(projectTitle);
      const planList = element("ul", undefined, "now-plans");
      for (const plan of project.plans || []) {
        const planItem = element("li", undefined, "now-plan");
        planItem.append(element("div", `${plan.name} — wave ${plan.wave} of ${plan.waves}`, "now-plan-title"));
        const taskList = element("ul", undefined, "now-tasks");
        for (const group of plan.wave_groups || []) {
          for (const task of group.tasks || []) {
            const taskItem = element("li", undefined, "now-task");
            taskItem.append(element("span", task.task_id, "now-task-id"));
            taskItem.append(element("span", task.status, `now-status ${statusClass(task.status)}`));
            if (task.job) taskItem.append(element("span", `job ${task.job.job_id}, ${elapsed(task.elapsed_seconds)}`, "now-job"));
            taskList.append(taskItem);
          }
        }
        planItem.append(taskList);
        planList.append(planItem);
      }
      for (const task of project.outside_plan || []) {
        const taskItem = element("li", undefined, "now-task");
        taskItem.append(element("span", task.task_id, "now-task-id"));
        taskItem.append(element("span", task.status, `now-status ${statusClass(task.status)}`));
        if (task.job) taskItem.append(element("span", `job ${task.job.job_id}, ${elapsed(task.elapsed_seconds)}`, "now-job"));
        planList.append(taskItem);
      }
      projectItem.append(planList);
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
