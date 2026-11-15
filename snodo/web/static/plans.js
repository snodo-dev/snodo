(() => {
  window.snodoViews = window.snodoViews || {};

  let selectedProject = "all";

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function updatedDate(value) {
    if (!value) return "n/a";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "n/a";
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
  }

  function render(plans) {
    const fragment = document.createDocumentFragment();
    const projects = [...new Set(plans.map((plan) => plan.project || ""))].sort();
    const visiblePlans = selectedProject === "all"
      ? plans
      : plans.filter((plan) => plan.project === selectedProject);

    if (projects.length > 1) {
      const label = element("label", "Project ", "plans-filter");
      const filter = element("select", undefined, "plans-filter-select");
      filter.setAttribute("aria-label", "Filter plans by project");
      const allOption = element("option", "All projects");
      allOption.value = "all";
      filter.append(allOption);
      for (const project of projects) {
        const option = element("option", project);
        option.value = project;
        filter.append(option);
      }
      filter.value = selectedProject;
      filter.addEventListener("change", () => {
        selectedProject = filter.value;
        window.snodoViews.plans(containerForView);
      });
      label.append(filter);
      fragment.append(label);
    }

    if (plans.length === 0) {
      fragment.append(element("p", "No plans to show.", "plans-empty"));
      return fragment;
    }

    const table = element("table", undefined, "plans-table");
    const thead = element("thead");
    const heading = element("tr");
    for (const title of ["Plan", "Project", "Tasks", "Progress", "Delivery", "Updated"]) {
      heading.append(element("th", title));
    }
    thead.append(heading);
    table.append(thead);

    const tbody = element("tbody");
    for (const plan of visiblePlans) {
      const row = element("tr");
      const name = element("span", plan.name || "", "plans-name");
      if (plan.intent) name.title = plan.intent;
      const nameCell = element("td");
      nameCell.append(name);
      row.append(nameCell);
      row.append(element("td", plan.project || "", "plans-project"));
      const total = Math.max(0, Number(plan.tasks_total) || 0);
      const completed = Math.max(0, Number(plan.tasks_completed) || 0);
      row.append(element("td", `${completed} / ${total}`, "plans-tasks"));
      const progressCell = element("td");
      const progress = element("progress");
      progress.max = total || 1;
      progress.value = Math.min(completed, total);
      progress.setAttribute("aria-label", `${completed} of ${total} tasks completed`);
      progressCell.append(progress);
      row.append(progressCell);

      const deliveryCell = element("td");
      let delivery;
      let deliveryClass;
      if (plan.running) {
        delivery = "running";
        deliveryClass = "plans-tag-running";
      } else if (plan.delivery === "in_main") {
        delivery = "in main";
        deliveryClass = "plans-tag-main";
      } else if (plan.delivery === "not_in_main") {
        delivery = "not in main";
        deliveryClass = "plans-tag-not-main";
      } else {
        delivery = "no branch on record";
        deliveryClass = "plans-tag-no-branch";
      }
      deliveryCell.append(element("span", delivery, `plans-tag ${deliveryClass}`));
      row.append(deliveryCell);
      row.append(element("td", updatedDate(plan.updated_at), "plans-updated"));
      tbody.append(row);
    }
    table.append(tbody);
    const tableWrap = element("div", undefined, "plans-table-wrap");
    tableWrap.append(table);
    fragment.append(tableWrap);
    return fragment;
  }

  let containerForView;
  window.snodoViews.plans = async function plans(container) {
    containerForView = container;
    try {
      const response = await fetch("/api/plans");
      if (!response.ok) throw new Error("Request failed");
      const data = await response.json();
      const content = render(Array.isArray(data) ? data : []);
      const notice = container.querySelector("[data-plans-error]");
      container.replaceChildren(content);
      if (notice) notice.remove();
    } catch (_) {
      let notice = container.querySelector("[data-plans-error]");
      if (!notice) {
        notice = element("p", "The plans dashboard could not be reached.", "view-message");
        notice.dataset.plansError = "true";
        container.append(notice);
      }
    }
  };
})();
