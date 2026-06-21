# Spec: legacy validator path must respect configured max_tokens

## Why
validators/llm_validator.py:550 hardcodes max_tokens=500 in _call_llm.
Config values (single_max_tokens / max_tokens) are ignored on this
path. Long justifications hit the 500 ceiling → truncated JSON →
"Could not parse LLM response". Confirmed: the model is cut by the
cap, not snodo slicing.

## Change
validators/llm_validator.py:548-551 — replace hardcoded 500 with the
configured value. Use self.completion_tokens (already resolved from
config / context, default 1500) rather than a literal.

  kwargs = {
      "messages": [{"role": "user", "content": prompt}],
      "max_tokens": self.completion_tokens,
  }

Confirm self.completion_tokens is the right field (it backs the
structured path at line 566). If a separate single-completion cap is
wanted, wire single_max_tokens through to it — but reusing
completion_tokens is simplest and matches the structured path.

## Tests
- _call_llm passes self.completion_tokens (not 500) as max_tokens
- a validator configured with higher max_tokens uses it on the legacy path

## Touch only
validators/llm_validator.py

Commit: fix(validators): legacy parse path respects configured max_tokens, not hardcoded 500
