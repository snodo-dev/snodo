# Spec: cache optimization — prompt reorder + CF session-affinity header

## Why
~77% of input tokens are re-sent static content paying full price.
Two root causes, two fixes:
A) Coder prompt interleaves variable session_history BETWEEN stable
   blocks, so the cacheable prefix changes every task — DeepSeek
   auto-cache (cached $0.0028 vs $0.14, 50x) rarely hits.
B) CF Workers AI prefix caching needs x-session-affinity header to
   route multi-turn calls to the same instance; we never send it.

## Fix A — reorder coder prompt for a stable cacheable prefix
coders/litellm.py _build_prompt (~121-188). Move "## Session History"
to the END, after task/constraints/tools/output-format. Target order:
  1. "You are an expert software engineer..."   [stable]
  2. "## Project Context" + dir tree + configs  [stable]
  3. "## Task\nDescription: {spec}"             [per-task]
  4. "## Constraints"                            [stable]
  5. "## Available Tools"                        [stable]
  6. "## Output Format"                          [stable]
  7. "## Session History\n{memory_summary}"      [VARIABLE — last]
Goal: contiguous stable prefix so prefix-caching providers hit.

## Fix B — x-session-affinity header on CF calls only
Add extra_headers to completion kwargs, gated to cloudflare provider,
keyed on _task_id (stable across all turns of one run):

  provider = ConfigManager._provider_for_model(self.model)
  if provider == "cloudflare":
      kwargs["extra_headers"] = {"x-session-affinity": self._task_id or "unknown"}

Apply at:
  coders/litellm.py — _call_llm, _call_llm_with_tools
  validators/llm_validator.py — _call_llm, _call_llm_structured,
    _evaluate_with_tools (the completion call ~line 242)

Only set when provider==cloudflare (header is meaningless/harmful for
deepseek/anthropic).

## Tests
- _build_prompt: session history appears AFTER output-format section
- stable blocks form a contiguous prefix before any variable content
- CF model: completion kwargs include extra_headers with
  x-session-affinity = task_id
- deepseek/anthropic model: NO extra_headers (or no x-session-affinity)
- affinity value stable across multiple turns of same task

## Touch only
coders/litellm.py, validators/llm_validator.py

Commit: feat(cache): reorder prompt for stable prefix + CF x-session-affinity header
