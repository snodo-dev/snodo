# W3-02: Centralize DEFAULT_MODEL, fix gpt-4 hardcodes

## Intent
Five occurrences of "gpt-4" are hardcoded across coders/, validators/,
and engine/. The engine cannot import from cli/ by design. The right
home is infrastructure/config.py alongside the existing
_CODER_MAX_TOKENS_DEFAULT / _VALIDATOR_MAX_TOKENS_DEFAULT pattern.
Sandbox hardcodes (snodo-worker:latest, network:none) are left alone —
no config.yml sandbox section exists and adding one is out of scope.

## What to change

### infrastructure/config.py
Add alongside existing defaults:
  DEFAULT_MODEL = "claude-sonnet-4-20250514"

### coders/litellm.py:47
Replace: model: str = "gpt-4"
With:    model: str = DEFAULT_MODEL
Import DEFAULT_MODEL from infrastructure.config

### coders/__init__.py:47
Same as above if it re-exports the constructor default.

### validators/llm_validator.py:64
Replace: model: str = "gpt-4"
With:    model: str = DEFAULT_MODEL
Import DEFAULT_MODEL from infrastructure.config

### validators/protocol_adherence.py:38
Same as llm_validator.

### engine/validators.py:84
Replace the "gpt-4" string in the getattr fallback:
  model=getattr(self.coder, "model", DEFAULT_MODEL)
Import DEFAULT_MODEL from infrastructure.config

### cli/config.py
Replace the inline "claude-sonnet-4-20250514" string in DEFAULT_MODEL
with an import from infrastructure.config — single source of truth.

## Acceptance criteria
- "gpt-4" string does not appear in any of the above files
- DEFAULT_MODEL defined once in infrastructure/config.py
- cli/config.py imports it rather than redefining it
- All fallback paths now resolve to a model that supports
  response_format (claude-sonnet-4-20250514)

## Testing
- No new tests required
- Full test suite passes clean — any test instantiating LiteLLMAdapter()
  or LLMValidator() directly without a model arg will now get
  claude-sonnet-4-20250514 instead of gpt-4. If that breaks a test,
  the test was implicitly depending on gpt-4 behavior — fix the test.

## Constraints
- Do not add a sandbox: section to config.yml
- Do not touch sandbox/base.py
- One commit, all files together
