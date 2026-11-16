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

  function updateDateLabels(chart) {
    const labels = Array.from(chart.querySelectorAll(".delivery-day-date"));
    if (labels.length <= 7) return;
    const step = Math.ceil((labels.length - 1) / 7);
    const visible = new Set();
    for (let index = 0; index < labels.length - 1; index += step) {
      visible.add(index);
    }
    const last = labels.length - 1;
    const sampledBeforeLast = Math.floor(last / step) * step;
    if (sampledBeforeLast > 0 && last - sampledBeforeLast < step / 2) {
      visible.delete(sampledBeforeLast);
    }
    visible.add(last);
    labels.forEach((label, index) => {
      label.hidden = !visible.has(index);
    });
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
    if (Number(totals.plans_not_in_main) > 0) {
      const count = Number(totals.plans_not_in_main);
      fragment.append(element("p", `${count} ${count === 1 ? "plan" : "plans"} not in main yet`, "delivery-not-in-main"));
    }

    const controls = element("div", undefined, "delivery-windows");
    for (const daysOption of [7, 30, 90]) {
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
    const card = (label, value, explanation) => {
      const figure = element("section", undefined, "delivery-figure");
      figure.append(element("span", label, "delivery-card-label"));
      figure.append(element("strong", value === null || value === undefined ? "n/a" : String(value)));
      figure.append(element("span", explanation, "delivery-card-explanation"));
      return figure;
    };
    const kinds = ["feature", "defect", "debt", "risk"];
    const typeColors = { feature: "var(--flow-feature)", defect: "var(--flow-defect)", debt: "var(--flow-debt)", risk: "var(--flow-risk)" };
    const totalsByType = totals.by_type || {};
    const weekly = Array.isArray(totals.by_week) ? totals.by_week : (Array.isArray(data.by_week) ? data.by_week : []);

    const velocity = card("Flow velocity", totals.landed, "How much work is getting done");
    const velocityBars = element("div", undefined, "delivery-velocity-bars");
    for (const week of weekly) {
      const bar = element("div", undefined, "delivery-stack");
      const total = kinds.reduce((sum, kind) => sum + (Number(week[kind]) || 0), 0);
      bar.setAttribute("role", "img");
      bar.setAttribute("aria-label", `${week.week_start || "Week"}: ${kinds.map((kind) => `${kind} ${Number(week[kind]) || 0}`).join(", ")}`);
      for (const kind of kinds) {
        const count = Number(week[kind]) || 0;
        const segment = element("span", `${kind} ${count}`, "delivery-segment");
        segment.style.width = `${total ? count / total * 100 : 0}%`;
        segment.style.backgroundColor = typeColors[kind];
        segment.title = `${kind}: ${count}`;
        bar.append(segment);
      }
      velocityBars.append(bar);
    }
    velocity.append(velocityBars);
    figures.append(velocity);

    const p85 = totals.p85_flow_seconds ?? totals.pooled_p85_flow_seconds;
    const flowTime = card("Flow time", totals.median_flow_seconds === null || totals.median_flow_seconds === undefined ? null : duration(totals.median_flow_seconds), "Start to landed");
    flowTime.append(element("span", p85 === null || p85 === undefined ? "85% under n/a" : `85% under ${duration(p85)}`, "delivery-card-detail"));
    const timeBar = element("div", undefined, "delivery-meter");
    timeBar.setAttribute("role", "img");
    timeBar.setAttribute("aria-label", p85 === null || p85 === undefined ? "85th percentile unavailable" : `85% under ${duration(p85)}`);
    flowTime.append(timeBar);
    figures.append(flowTime);

    const efficiency = card("Flow efficiency", rate(totals.efficiency), "Share of flow time not spent on rework");
    const efficiencyBar = element("div", undefined, "delivery-meter");
    const efficiencyValue = Number(totals.efficiency);
    if (totals.efficiency !== null && totals.efficiency !== undefined && Number.isFinite(efficiencyValue)) efficiencyBar.style.setProperty("--delivery-meter", `${Math.max(0, Math.min(100, efficiencyValue * 100))}%`);
    efficiencyBar.setAttribute("aria-label", `Flow efficiency ${rate(totals.efficiency)}`);
    efficiency.append(efficiencyBar);
    figures.append(efficiency);

    figures.append(card("Flow load", totals.flow_load, "Items started and not yet landed"));

    const distribution = card("Flow distribution", "n/a", "The mix of work that landed");
    const shares = kinds.map((kind) => ({ kind, count: Number(totalsByType[kind]?.landed) || 0, share: Number(totalsByType[kind]?.share) || 0 }));
    const classified = shares.reduce((sum, item) => sum + item.count, 0);
    const largest = shares.reduce((best, item) => item.count > best.count ? item : best, shares[0]);
    if (classified) distribution.querySelector("strong").textContent = `${Math.round(largest.count / classified * 100)}% ${largest.kind}s`;
    const distributionBar = element("div", undefined, "delivery-distribution");
    distributionBar.setAttribute("role", "img");
    distributionBar.setAttribute("aria-label", `Flow distribution: ${shares.map((item) => `${item.kind} ${Math.round((classified ? item.count / classified : item.share) * 100)}%`).join(", ")}`);
    for (const item of shares) {
      const share = classified ? item.count / classified : item.share;
      const segment = element("span", `${item.kind} ${Math.round(share * 100)}%`, "delivery-segment");
      segment.style.width = `${Math.max(0, share) * 100}%`;
      segment.style.backgroundColor = typeColors[item.kind];
      segment.title = `${item.kind}: ${Math.round(share * 100)}%`;
      distributionBar.append(segment);
    }
    distribution.append(distributionBar);
    figures.append(distribution);
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
      const track = element("div", undefined, "delivery-track");
      const bar = element("div", undefined, "delivery-bar");
      bar.style.height = `${Math.max(2, (Number(day.landed) || 0) / maximum * 100)}%`;
      bar.setAttribute("aria-label", `${day.date}: ${day.landed}`);
      bar.title = `${day.date}: ${day.landed}`;
      track.append(bar);
      const shortDate = typeof day.date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(day.date) ? day.date.slice(5) : day.date || "";
      column.append(track, element("span", shortDate, "delivery-day-date"));
      chart.append(column);
    }
    updateDateLabels(chart);
    fragment.append(chart);

    const byWeekBlock = element("section", undefined, "delivery-detail-block");
    byWeekBlock.append(element("h2", "Velocity by week"));
    const weeklyChart = element("div", undefined, "delivery-week-chart");
    weeklyChart.setAttribute("aria-label", "Landed work by type each week");
    const maxWeekly = Math.max(1, ...weekly.map((week) => kinds.reduce((sum, kind) => sum + (Number(week[kind]) || 0), 0)));
    for (const week of weekly) {
      const total = kinds.reduce((sum, kind) => sum + (Number(week[kind]) || 0), 0);
      const column = element("div", undefined, "delivery-week-column");
      column.append(element("span", String(total), "delivery-week-total"));
      const stack = element("div", undefined, "delivery-week-stack");
      stack.style.height = `${total / maxWeekly * 100}%`;
      stack.setAttribute("role", "img");
      stack.setAttribute("aria-label", `${week.week_start || "Week"}: ${total} landed`);
      for (const kind of kinds) {
        const count = Number(week[kind]) || 0;
        if (!count) continue;
        const segment = element("span", `${kind} ${count}`, "delivery-week-segment");
        segment.style.flexGrow = String(count);
        segment.style.backgroundColor = typeColors[kind];
        segment.title = `${kind}: ${count}`;
        stack.append(segment);
      }
      column.append(stack);
      const date = typeof week.week_start === "string" ? new Date(`${week.week_start}T00:00:00Z`) : null;
      const label = date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat("en", { month: "short", day: "numeric", timeZone: "UTC" }).format(date) : (week.week_start || "");
      column.append(element("span", label, "delivery-week-date"));
      weeklyChart.append(column);
    }
    byWeekBlock.append(weeklyChart);
    fragment.append(byWeekBlock);

    const flowBlock = element("section", undefined, "delivery-detail-block");
    flowBlock.append(element("h2", "Flow time by type"));
    const flowRows = element("div", undefined, "delivery-flow-rows");
    const typeData = data.by_type || totalsByType;
    const maxP85 = Math.max(1, ...kinds.map((kind) => Number(typeData[kind]?.p85_flow_seconds) || 0));
    const minutes = (seconds) => `${(Number(seconds) / 60).toFixed(1)} min`;
    for (const kind of kinds) {
      const values = typeData[kind] || {};
      const median = values.median_flow_seconds;
      const upper = values.p85_flow_seconds;
      const row = element("div", undefined, "delivery-flow-row");
      row.append(element("span", kind, "delivery-flow-kind"));
      if (median === null || median === undefined || upper === null || upper === undefined) {
        row.append(element("span", "n/a", "delivery-flow-na"));
      } else {
        const track = element("div", undefined, "delivery-flow-track");
        const range = element("span", undefined, "delivery-flow-range");
        range.style.width = `${Math.max(0, Number(upper)) / maxP85 * 100}%`;
        range.style.backgroundColor = typeColors[kind];
        range.title = `85th percentile: ${minutes(upper)}`;
        const marker = element("span", undefined, "delivery-flow-median");
        marker.style.left = `${Math.max(0, Number(median)) / maxP85 * 100}%`;
        marker.title = `Median: ${minutes(median)}`;
        track.append(range, marker);
        row.append(track, element("span", `${minutes(median)} · ${minutes(upper)}`, "delivery-flow-values"));
      }
      flowRows.append(row);
    }
    flowBlock.append(flowRows);
    fragment.append(flowBlock);

    const typeBlock = element("section", undefined, "delivery-detail-block");
    typeBlock.append(element("h2", "By type"));
    const typeScroll = element("div", undefined, "delivery-type-scroll");
    const typeTable = element("table", undefined, "delivery-types");
    const typeHead = element("tr");
    for (const heading of ["Type", "Landed", "Share", "Median time", "85%"] ) typeHead.append(element("th", heading));
    const typeThead = element("thead");
    typeThead.append(typeHead);
    typeTable.append(typeThead);
    const typeBody = element("tbody");
    for (const kind of kinds) {
      const values = typeData[kind] || {};
      const row = element("tr");
      row.append(element("td", kind), element("td", values.landed === null || values.landed === undefined ? "n/a" : String(values.landed)), element("td", rate(values.share)), element("td", values.median_flow_seconds == null ? "n/a" : minutes(values.median_flow_seconds)), element("td", values.p85_flow_seconds == null ? "n/a" : minutes(values.p85_flow_seconds)));
      typeBody.append(row);
    }
    typeTable.append(typeBody);
    typeScroll.append(typeTable);
    typeBlock.append(typeScroll, element("p", "Rework is time after a task was first sent back.", "delivery-rework-note"));
    fragment.append(typeBlock);

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
