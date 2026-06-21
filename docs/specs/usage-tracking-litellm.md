# Spec: LLM usage + cost tracking via litellm CustomLogger

## Why
We need per-call token usage, cost, and timing for every coder and
validator call, to feed `snodo meta` and cost reporting. litellm
provides this natively — use it rather than hand-rolling capture or
a custom pricing module.

Recon confirmed:
- litellm.CustomLogger.log_success_event(self, kwargs, response_obj,
  start_time, end_time) fires per completion.
- response_obj.usage carries prompt_tokens / completion_tokens for
  both deepseek/ and openai/@cf/ (OpenAI-schema response).
- completion_cost fails on CF models ("not mapped") until
  register_model adds their pricing — then it works.
- metadata passed to completion() arrives in the callback's kwargs,
  giving us task_id/job_id correlation.

## Changes

### 1. UsageTracker (new) — litellm CustomLogger
New module (e.g. snodo/infrastructure/usage_tracker.py):
- class UsageTracker(litellm.integrations.custom_logger.CustomLogger)
- implement log_success_event(self, kwargs, response_obj, start_time, end_time):
    * tokens: response_obj.usage.prompt_tokens / .completion_tokens
    * cost: litellm.completion_cost(completion_response=response_obj)
            (guard: if it raises, record cost=None, do not crash the run)
    * timing: end_time - start_time
    * correlation: kwargs.get("litellm_params", {}).get("metadata", {})
                   -> job_id, task_id, and a role tag ("coder"/"validator:<id>")
    * model: kwargs.get("model")
- Persist each record to the job's state.json under a usage list,
  keyed by job_id. Use JobManager state read/update (jobs/__init__.py).
  Append, don't overwrite — multiple calls per job.

### 2. Module-init registration — coders/litellm.py
At the same module-init point as drop_params (line ~28):
- register the tracker:  litellm.callbacks = [UsageTracker()]
  (append if callbacks already set; don't clobber)
- register CF model pricing so completion_cost works:
    litellm.register_model({
      "openai/@cf/google/gemma-4-26b-a4b-it": {
        "input_cost_per_token": 0.10/1e6,
        "output_cost_per_token": 0.30/1e6,
      },
      "openai/@cf/nvidia/nemotron-3-120b-a12b": {
        "input_cost_per_token": 0.50/1e6,
        "output_cost_per_token": 1.50/1e6,
      },
      "openai/@cf/moonshotai/kimi-k2.6": {
        "input_cost_per_token": 0.95/1e6,
        "output_cost_per_token": 4.00/1e6,
      },
      "openai/@cf/moonshotai/kimi-k2.7-code": {
        "input_cost_per_token": 0.95/1e6,
        "output_cost_per_token": 4.00/1e6,
      },
      "openai/@cf/mistralai/mistral-small-3.1-24b-instruct": {
        "input_cost_per_token": 0.35/1e6,
        "output_cost_per_token": 0.55/1e6,
      },
    })
  (deepseek/ and others litellm already prices — leave them.)

### 3. Thread metadata into completion calls
Pass metadata so the callback can correlate. At each completion call site:
- coders/litellm.py _call_llm and _call_llm_with_tools: add
    metadata={"job_id": <job_id>, "task_id": <task_id>, "role": "coder"}
- validators/llm_validator.py _call_llm and _call_llm_structured: add
    metadata={"job_id": <job_id>, "task_id": <task_id>,
              "role": f"validator:{self.validator_id}"}

job_id/task_id must be threaded down to these call sites. Recon: confirm
how job_id is available in the adapter/validator (it may need passing in
at construction — surface it via the completion_fn partial or context).
If not currently available, that wiring is part of this task.

## Out of scope
- snodo meta command (separate task — reads what this persists)
- pricing for non-CF models (litellm already covers them)

## Tests
- UsageTracker.log_success_event extracts tokens, cost, timing from a
  mock response_obj + kwargs with metadata
- completion_cost returns a value for a registered CF model (not raises)
- a coder completion persists a usage record to job state.json with
  role="coder" and correct job_id
- a validator completion persists with role="validator:<id>"
- cost=None recorded gracefully if completion_cost raises (no crash)

## Touch
snodo/infrastructure/usage_tracker.py (new), coders/litellm.py,
validators/llm_validator.py, jobs/__init__.py (state append helper if needed)

Commit: feat(usage): litellm CustomLogger token/cost/timing tracking + CF model pricing
