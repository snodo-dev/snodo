(() => {
  const renderers = [];
  const subscribers = new Set();
  const state = {
    original: null,
    validators: [],
    openValidator: null,
    data: null,
    project: "",
    projects: [],
    loaded: false,
    container: null,
  };

  const clone = (value) => JSON.parse(JSON.stringify(value));
  const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right);

  function changes() {
    if (!state.original) return [];
    const originalById = new Map((state.original.validators || []).map((row) => [row.id, row]));
    const edits = [];
    for (const row of state.validators) {
      const before = originalById.get(row.id);
      if (!before) continue;
      for (const [key, field] of [["type", "type"], ["phase", "phase"], ["severity_cap", "severity_cap"], ["tools", "tools"], ["criteria", "criteria"]]) {
        if (!equal(row[key], before[key])) edits.push({ validator_id: row.id, [field]: clone(row[key]) });
      }
    }
    return edits;
  }

  function isChanged(id) {
    return changes().some((edit) => edit.validator_id === id);
  }

  function notify() {
    subscribers.forEach((callback) => callback(state));
    renderers.forEach((render) => render(state));
  }

  function subscribe(callback) {
    subscribers.add(callback);
    return () => subscribers.delete(callback);
  }

  function registerRenderer(render) {
    renderers.push(render);
    if (state.container) render(state);
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function errorCard(container, message) {
    const card = el("p", message, "view-message");
    container.replaceChildren(card);
  }

  async function fetchProtocol() {
    const query = state.project ? `?project=${encodeURIComponent(state.project)}` : "";
    const response = await fetch(`/api/protocol${query}`);
    const data = await response.json();
    if (!response.ok || data.error) throw new Error(data.error || "Protocol file could not be read.");
    state.data = data;
    state.original = clone(data);
    state.validators = clone(data.validators || []);
    state.openValidator = null;
    state.loaded = true;
    notify();
  }

  function renderHead(container) {
    const data = state.data;
    const head = el("div", undefined, "head");
    const title = el("div");
    title.append(el("div", `${data.protocol.name} · v${data.protocol.version}`, "eyebrow"), el("div", "Protocol", "eyebrow"));
    const live = el("div", undefined, "live");
    const read = el("span");
    read.append(el("span", undefined, "dot"), document.createTextNode("Reads .snodo/protocol.yml"));
    live.append(read);
    live.append(el("span", "schema valid", "pill pass"));
    if (state.projects.length > 1) {
      const select = el("select");
      select.setAttribute("aria-label", "Project");
      for (const project of state.projects) {
        const option = el("option", project.name);
        option.value = project.name;
        option.selected = project.name === state.project;
        select.append(option);
      }
      select.addEventListener("change", () => { state.project = select.value; state.loaded = false; renderView(container); });
      live.append(select);
    }
    const reload = el("button", "Reload from disk", "btn");
    reload.type = "button";
    reload.addEventListener("click", () => { state.loaded = false; renderView(container); });
    live.append(reload);
    head.append(title, live);
    container.replaceChildren(head);
  }

  function render() {
    if (!state.container || !state.data) return;
    renderHead(state.container);
    renderers.forEach((renderer) => renderer(state));
  }

  async function renderView(container) {
    state.container = container;
    try {
      if (!state.projects.length) {
        const projectsResponse = await fetch("/api/projects");
        if (projectsResponse.ok) {
          const projectData = await projectsResponse.json();
          state.projects = Array.isArray(projectData) ? projectData : (projectData.projects || []);
          if (state.projects.length) state.project = state.project || state.projects[0].name;
        }
      }
      if (!state.loaded) await fetchProtocol();
      render();
    } catch (error) {
      errorCard(container, error.message);
    }
  }

  window.snodoProtocol = { state, changes, isChanged, subscribe, notify, registerRenderer, renderHead };
  window.snodoViews = window.snodoViews || {};
  window.snodoViews.protocol = renderView;
})();
