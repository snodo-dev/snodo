(() => {
  const api = window.snodoProtocol;

  function tile(label, value, sub, options = {}) {
    const node = document.createElement("div");
    node.className = "tile";
    const eyebrow = document.createElement("div");
    eyebrow.className = "eyebrow";
    eyebrow.textContent = label;
    const number = document.createElement("div");
    number.className = "n";
    number.textContent = value;
    if (options.small) {
      number.style.fontSize = "26px";
      number.style.paddingTop = "8px";
    }
    const detail = document.createElement("div");
    detail.className = options.ok ? "s ok" : "s";
    detail.textContent = sub;
    node.append(eyebrow, number, detail);
    return node;
  }

  function render(state) {
    if (!state.container || !state.data) return;
    const container = state.container;
    let tiles = container.querySelector(".tiles");
    if (!tiles) {
      tiles = document.createElement("div");
      tiles.className = "tiles";
      const head = container.querySelector(".head");
      if (head && head.nextSibling) container.insertBefore(tiles, head.nextSibling);
      else container.append(tiles);
    }

    const data = state.data;
    const protocol = data.protocol || {};
    const modes = data.modes || [];
    const initialMode = modes.find((mode) => mode.id === protocol.initial_mode);
    const declaredIds = initialMode ? initialMode.validators || [] : [];
    const validatorsById = new Map((state.validators || []).map((validator) => [validator.id, validator]));
    const active = declaredIds.map((id) => validatorsById.get(id)).filter(Boolean);
    const policy = protocol.disagreement_policy;
    const delivery = protocol.effective_delivery;
    const deliveryCopy = {
      local_merge: ["auto-merge", "on a clean pass", true],
      push_branch: ["push branch", "work stays on a branch", false],
      change_request: ["change request", "needs a code host plugin", false],
    };
    const [deliveryWord, deliverySub, isOk] = deliveryCopy[delivery] || ["leave unmerged", "merge by hand", false];
    const disagreements = {
      unanimous: "one blocker halts",
      majority: "most validators must agree",
    };

    tiles.replaceChildren(
      tile("Modes", modes.length, `initial: ${protocol.initial_mode || ""}`),
      tile("Validators", active.length, `of ${declaredIds.length} declared`),
      tile("Before coder", active.filter((validator) => validator.phase === "pre_execute").length, "spec checks"),
      tile("After coder", active.filter((validator) => validator.phase === "post_execute").length, "the gate"),
      tile("Disagreement", policy || "", disagreements[policy] || "", { small: true }),
      tile("Delivery", deliveryWord, deliverySub, { small: true, ok: isOk }),
    );
  }

  api.registerRenderer(() => {});
  api.registerRenderer(render);
})();
