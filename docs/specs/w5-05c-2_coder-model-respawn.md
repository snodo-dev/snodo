# W5-05c-2: Apply authorized set_model to the coder (kill + respawn)

## Intent
Coder-scoped set_model records are written to authorized_decisions but
ignored (W5-05c-1 applied only validator-scoped). Apply coder-scoped
overrides: an authorized set_model with scope "coder" replaces the coder
instance with a fresh one built on the new model. Context lives outside
the coder (loop_state.messages, passed via memory_summary each call), so
respawn is clean. The new instance also gets a fresh max_tool_turns
budget — which helps exploration-heavy tasks.

This closes the model-fluidity loop end to end and is the last piece of
Wave 5.

## Approach (from recon): swap mid-session, NOT defer
The coder is self.coder on GraphBuilder — an attribute lookup at call
time, NOT baked into the compiled graph. The governance node already
loads authorized_decisions every iteration. So: read the coder-scoped
override in the governance path, and if present, respawn the coder before
the next execute. No graph rebuild.

## What to build

### Read + verify the coder-scoped override
In the governance node (loop.py ~263-274, where authorized_decisions is
already loaded) or a helper it calls:
- Reuse find_set_model_overrides(self._authorized_decisions) via the
  existing verify-only issuer (self._decision_issuer) — RS256-verified,
  same as validators
- Find the override scoped to "coder":
  next((p for p in verified if p.get("scope") == "coder"), None)
- If none → no change (coder keeps its current model)

### Respawn the coder (the kill + respawn)
If a verified coder override exists AND the new model differs from the
current coder's model:
- adapter_cls = resolve_adapter_class(new_model)
- build a fresh coder: adapter_cls(model=new_model,
    max_tokens=llm_cfg.coder.max_tokens,
    max_tool_turns=llm_cfg.coder.max_tool_turns,
    workspace_mcp=workspace_mcp)
- Reassign self.coder = <new coder>
- CRITICAL: also update the two values captured at __init__
  (loop.py:149-151) so validator default and coder don't drift:
    self._completion_fn = <new coder's completion fn>
    self._default_model = new_model
  All THREE (self.coder, self._completion_fn, self._default_model) must
  update together. Updating only self.coder leaves the validator
  default_model stale.
- Only respawn when the model actually changes — do not rebuild the coder
  every iteration if the override equals the current model (idempotent).

### Precedence
authorized coder override > model param (build-time) > DEFAULT_MODEL.

### Idempotence / no churn
The governance node runs every iteration. Respawn must be a no-op when
the override model == current coder model — guard on model difference,
not on "override exists". Otherwise the coder rebuilds needlessly each
loop.

## Acceptance criteria
- A verified set_model record scoped to "coder" causes the coder to be
  rebuilt with the new model before the next execute
- The respawned coder uses the new model end to end (execute runs on it)
- self.coder, self._completion_fn, self._default_model all update
  together — validator default_model is not left stale
- The respawned coder gets a fresh max_tool_turns budget (new instance)
- Tampered/unverified coder override → NOT applied (verify gate, reuse
  find_set_model_overrides)
- Validator-scoped overrides (W5-05c-1) still work and are unaffected
- No override, or override == current model → no respawn (idempotent,
  no per-iteration churn)
- Context survives the respawn (loop_state.messages / memory_summary
  carries over — a fresh coder continues coherently)
- Precedence: coder override > model param > DEFAULT_MODEL

## Testing
- Unit: verified coder-scoped override → coder rebuilt with new model
- Unit: all three (coder, completion_fn, default_model) updated on respawn
- Unit: tampered coder override → not applied
- Unit: override == current model → no respawn (idempotent)
- Unit: validator-scoped override unaffected by coder path (c-1 regression)
- Unit: no authorized_decisions → coder unchanged
- Unit: respawned coder has fresh max_tool_turns
- Integration if feasible: override applied, next execute runs on new
  model, context (messages/summary) carried over
- Full suite passes clean

## Constraints
- Read engine/loop.py (coder construction ~1096-1117, _governance_node
  ~263-274, _execute_node ~409-412, the __init__ captures at 149-151),
  infrastructure/decisions.py (find_set_model_overrides, verify path),
  engine/validators.py (how c-1 applied validator overrides — mirror the
  verify+apply pattern), coders/litellm.py (max_tool_turns at 58/179,
  resolve_adapter_class) before touching anything
- Reuse find_set_model_overrides + the verify-only issuer — no new
  verification code, no private key anywhere in this path
- Swap mid-session (Approach A) — do NOT rebuild the graph; reassign the
  coder attribute + the two captured values
- Idempotent: guard respawn on model-difference, not override-presence
- Do not regress W5-05c-1 (validator overrides)
