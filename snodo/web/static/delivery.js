(() => {
  window.snodoViews = window.snodoViews || {};

  let selectedDays = 7;

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function duration(seconds) {
    if (seconds === null || seconds === undefined) return "n/a";
    const value = Math.max(0, Number(seconds));
    if (!Number.isFinite(value)) return "n/a";
    if (value < 60) return `${Math.round(value)} sec`;
    if (value < 3600) return `${Math.round(value / 60)} min`;
    return `${(value / 3600).toFixed(1)} hr`;
  }

  function rate(value) {
    if (value === null || value === undefined) return "n/a";
    const number = Number(value);
    return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "n/a";
  }

  function render(data) {
    const fragment = document.createDocumentFragment();
    const totals = data.totals || {};
    const days = data.window_days || selectedDays;
    let summary = `${totals.landed ?? "n/a"} tasks landed in the last ${days} days across ${(data.projects || []).length} projects.`;
    if (totals.median_flow_seconds !== null && totals.median_flow_seconds !== undefined) {
      summary += ` A typical task takes about ${duration(totals.median_flow_seconds)}`;
      if (totals.first_try_rate !== null && totals.first_try_rate !== undefined) {
        summary += ` and ${rate(totals.first_try_rate)} landed on the first try`;
      }
      summary += ".";
    } else if (totals.first_try_rate !== null && totals.first_try_rate !== undefined) {
      summary += ` ${rate(totals.first_try_rate)} landed on the first try.`;
    }
    fragment.append(element("p", summary, "delivery-summary"));

    const controls = element("div", undefined, "delivery-windows");
    for (const daysOption of [7, 30]) {
      const button = element("button", `${daysOption}d`);
      button.type = "button";
      button.setAttribute("aria-pressed", String(days === daysOption));
      button.addEventListener("click", () => {
        selectedDays = daysOption;
        window.snodoViews.delivery(containerForView);
      });
      controls.append(button);
    }
    fragment.append(controls);

    const figures = element("div", undefined, "delivery-figures");
    const values = [
      ["Landed", totals.landed],
      ["Flow time", totals.median_flow_seconds === null || totals.median_flow_seconds === undefined ? null : duration(totals.median_flow_seconds)],
      ["First try", totals.first_try_rate === null || totals.first_try_rate === undefined ? null : rate(totals.first_try_rate)],
      ["Flow load", totals.flow_load],
      ["Not in main", totals.plans_not_in_main],
    ];
    for (const [label, value] of values) {
      const figure = element("div", undefined, "delivery-figure");
      figure.append(element("strong", value === null || value === undefined ? "n/a" : String(value)));
      figure.append(element("span", label));
      figures.append(figure);
    }
    fragment.append(figures);

    fragment.append(element("h2", "Landed per day"));
    const chart = element("div", undefined, "delivery-chart");
    chart.setAttribute("role", "img");
    chart.setAttribute("aria-label", "Tasks landed per day");
    const byDay = Array.isArray(data.by_day) ? data.by_day : [];
    const maximum = Math.max(1, ...byDay.map((day) => Number(day.landed) || 0));
    for (const day of byDay) {
      const column = element("div", undefined, "delivery-day");
      column.append(element("span", day.landed === null || day.landed === undefined ? "n/a" : String(day.landed), "delivery-day-count"));
      const bar = element("div", undefined, "delivery-bar");
      bar.style.height = `${Math.max(2, (Number(day.landed) || 0) / maximum * 100)}%`;
      bar.setAttribute("aria-label", `${day.date}: ${day.landed}`);
      column.append(bar, element("span", day.date || "", "delivery-day-date"));
      chart.append(column);
    }
    fragment.append(chart);

    fragment.append(element("h2", "By project"));
    const table = element("table", undefined, "delivery-projects");
    const head = element("tr");
    for (const heading of ["Project", "Landed", "First try", "Median"]) head.append(element("th", heading));
    const thead = element("thead");
    thead.append(head);
    table.append(thead);
    const tbody = element("tbody");
    for (const project of data.projects || []) {
      const row = element("tr");
      row.append(element("td", project.name || ""));
      row.append(element("td", project.landed === null || project.landed === undefined ? "n/a" : String(project.landed)));
      row.append(element("td", rate(project.first_try_rate)));
      row.append(element("td", duration(project.median_flow_seconds)));
      tbody.append(row);
    }
    table.append(tbody);
    fragment.append(table);
    return fragment;
  }

  let containerForView;
  window.snodoViews.delivery = async function delivery(container) {
    containerForView = container;
    try {
      const response = await fetch("/api/delivery" + `?days=${selectedDays}`);
      if (!response.ok) throw new Error("Request failed");
      const data = await response.json();
      container.replaceChildren(render(data));
    } catch (_) {
      let notice = container.querySelector("[data-delivery-error]");
      if (!notice) {
        notice = element("p", "The delivery dashboard could not be reached.", "view-message");
        notice.dataset.deliveryError = "true";
        container.append(notice);
      }
    }
  };
})();
