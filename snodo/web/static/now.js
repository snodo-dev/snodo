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

  function render(data) {
    const fragment = document.createDocumentFragment();
    const projects = Array.isArray(data.projects) ? data.projects : [];
    const waiting = Array.isArray(data.waiting) ? data.waiting : [];
    const working = projects.filter((project) => project.state === "working");
    const idle = projects.filter((project) => project.state !== "working");
    const plans = working.flatMap((project) => project.plans || []);
    const runningTasks = plans.flatMap((plan) => plan.tasks || [])
      .filter((task) => task.job && task.job.status === "running");

    const counts = element("div", undefined, "now-counts");
    [
      [working.length, "projects with work in flight"],
      [plans.length, "plans in flight"],
      [runningTasks.length, "tasks running"],
      [waiting.length, "items waiting"],
    ].forEach(([number, label]) => {
      const tile = element("div", undefined, "now-count");
      tile.append(element("strong", String(number), "now-count-number"));
      tile.append(element("span", label, "now-count-label"));
      counts.append(tile);
    });
    fragment.append(counts);

    fragment.append(element("h2", "In flight"));
    const flight = element("ul", undefined, "now-projects");
    if (working.length === 0) flight.append(element("li", "Nothing is running.", "now-empty"));
    for (const project of [...working, ...idle]) {
      const projectItem = element("li", undefined, `now-project${project.state === "working" ? "" : " now-project-idle"}`);
      const projectTitle = element("div", undefined, "now-project-title");
      projectTitle.append(element("strong", project.name, "now-project-name"));
      if (project.state !== "working") projectTitle.append(element("span", "— idle", "now-idle-label"));
      projectItem.append(projectTitle);
      const planList = element("ul", undefined, "now-plans");
      for (const plan of project.plans || []) {
        const planItem = element("li", undefined, "now-plan");
        planItem.append(element("div", `${plan.name} — wave ${plan.wave} of ${plan.waves}`, "now-plan-title"));
        const taskList = element("ul", undefined, "now-tasks");
        for (const task of plan.tasks || []) {
          const taskItem = element("li", undefined, "now-task");
          taskItem.append(element("span", task.task_id, "now-task-id"));
          taskItem.append(element("span", task.status, `now-status ${statusClass(task.status)}`));
          if (task.job) {
            taskItem.append(element("span", `job ${task.job.job_id}, ${elapsed(task.job.elapsed_seconds)}`, "now-job"));
          }
          taskList.append(taskItem);
        }
        planItem.append(taskList);
        planList.append(planItem);
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
      if (item.kind === "not_in_main") {
        entry.textContent = `${item.plan}: all tasks completed, not merged into main`;
      } else if (item.kind === "uncommitted") {
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
})();
