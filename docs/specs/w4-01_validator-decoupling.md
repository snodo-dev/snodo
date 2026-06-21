# W4-01: Decouple validators from coder _completion_fn

## Intent
Validators currently get their LLM client (completion_fn + model) from
the coder via ValidatorContext. This breaks when the coder is a CLI
adapter (no _completion_fn). Validators need their own LLM client,
independent of whatever coder is configured.

Also add per-validator model override with default_model fallback cascade:
  validator.model → coder.model/default_model → DEFAULT_MODEL

## What to change

### compiler/models.py
Add to Validator pydantic model:
  model: Optional[str] = None
Protocol YAML can now specify model per validator. Field is optional —
no existing protocol templates need changes.

### infrastructure/config.py
Add ValidatorLLMConfig(BaseModel):
  model: Optional[str] = None  # None = use default_model
Extend LlmConfig with:
  validator_llm: ValidatorLLMConfig = ValidatorLLMConfig()

### engine/validators.py — ValidatorRunner
Constructor: replace coder parameter with completion_fn + default_model.
  __init__(self, protocol, completion_fn, default_model, validator_config,
           audit_log, workspace_mcp, git_mcp, session_manager)

In run(): remove getattr(self.coder, ...) — use self.default_model directly.

In _dispatch_one(): compute effective model per validator:
  effective_model = v.model or self.default_model or DEFAULT_MODEL
  Set context.model = effective_model before calling instance.evaluate(context)

### engine/loop.py — GraphBuilder
In __init__: extract completion_fn and model from coder at construction time.
  self._completion_fn = getattr(coder, "_completion_fn", None) or \
                        getattr(coder, "completion_fn", None)
  self._default_model = getattr(coder, "model", DEFAULT_MODEL)

Pass completion_fn + default_model to ValidatorRunner instead of coder.

In build_protocol_graph(): no change to coder construction — the engine
still builds a coder. The extraction happens in GraphBuilder.__init__.

### cli/config.py — backward-compatible default_model
In get_model(): check both keys:
  return config.get("default_model") or config.get("model", DEFAULT_MODEL)
No rename of the config key. Existing user config.yml files continue to work.

## Acceptance criteria
- ValidatorRunner has no reference to coder after this change
- Per-validator model override works: validator with model: X uses X,
  validator without model uses default_model
- CLI coder adapter (no _completion_fn) does not break validators
- Existing protocol templates (solo, team, 2+n) work unchanged —
  no model field required on validators
- backward-compatible: config.yml with "model" key still works

## Testing
- Unit test: ValidatorRunner with explicit completion_fn + default_model,
  no coder — validators run correctly
- Unit test: per-validator model override — validator with model field
  uses that model, not default_model
- Unit test: validator without model field uses default_model
- Unit test: MockAdapter as coder (no _completion_fn) — validators
  still run
- Full test suite passes clean

## Constraints
- Read engine/validators.py, engine/loop.py, compiler/models.py,
  validators/context.py in full before touching anything
- One commit: all files together
- Do not rename "model" to "default_model" in config.yml schema
- Do not touch llm_validator.py or protocol_adherence.py —
  they already respect context.model
