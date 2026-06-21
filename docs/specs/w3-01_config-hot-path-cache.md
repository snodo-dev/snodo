# W3-01: Cache LlmConfig at graph build, remove dead _default_validator

## Intent
ValidatorRunner.run() calls load_llm_config() on every validate pass —
minimum 2 file reads per task, more with iterations. LlmConfig is
read-only after construction and doesn't change mid-run. Cache it once
at build_protocol_graph() and pass it through. Also remove the dead
_default_validator method in loop.py — it's unreachable (validator_fn
falls back to ValidatorRunner.run, not _default_validator) and contains
its own redundant load_llm_config() call.

## What to change

### build_protocol_graph (loop.py)
Already calls load_llm_config() once for coder config. Extract
llm_cfg.validator and pass it into GraphBuilder.__init__ as
validator_config parameter.

### GraphBuilder.__init__ (loop.py)
Accept validator_config: ValidatorConfig parameter. Pass it into
ValidatorRunner.__init__.

### ValidatorRunner.__init__ (engine/validators.py)
Accept validator_config: ValidatorConfig parameter. Store as
self._validator_config. Remove load_llm_config() call from run().

### ValidatorRunner.run() (engine/validators.py)
Replace load_llm_config() call with self._validator_config.
Remove the load_llm_config import if no longer needed in validators.py.

### Remove _default_validator (loop.py lines 611-673)
Dead code — unreachable. validator_fn falls back to
ValidatorRunner.run, not this method. Remove it. Also remove
any test that patches or calls _default_validator directly —
those tests should patch ValidatorRunner.run instead.

## Acceptance criteria
- load_llm_config() called exactly once per snodo run invocation
- ValidatorRunner.run() reads from self._validator_config
- _default_validator removed from loop.py
- No behavior change on the validate path

## Testing
- Add test: ValidatorRunner constructed with explicit ValidatorConfig —
  verify run() uses it without calling load_llm_config()
- Update any tests that call or patch _default_validator to use
  ValidatorRunner.run instead
- Full test suite passes clean

## Constraints
- Read loop.py, engine/validators.py, and engine/loop.py before
  touching anything
- One commit: loop.py + validators.py + test updates
- Do not change ValidatorRunner.run() behavior, only how it gets config
