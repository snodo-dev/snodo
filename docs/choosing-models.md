# Choosing models from the orchestrator's side

<!-- snodo-guide topic="models" aliases="model,choosing-models" summary="Discover, resolve, and propose model choices" section="# Choosing models from the orchestrator's side" -->

An orchestrator should discover and resolve a model before proposing a change.
The normal plan path uses the model configured for the project; choosing a
different model for one run is an explicit exception.

Run `snodo models --check` before a run to make a small, billed provider canary
using each role's request shape. Validator, recon, and LiteLLM coder checks
continue for a second turn when the model returns a tool call; the canary feeds
back a short tool result. A failure reports the turn that failed, while a first
turn without a tool call is reported as successful with the second turn skipped.
Classifier checks are single-turn. This is a smoke test, not a guarantee that a
model will complete every task.

## Discover configured models

Call `list_models` to inspect the models discovered from Snodo's configured
providers. It returns an object with a `models` array. Each item contains:

- `provider`: the provider that supplied the model;
- `id`: the provider-normalized model id;
- `full_string`: the string to pass to a coder or adapter;
- `display_name`: a display label, when the provider supplied one; and
- `context_window`: the reported input context size, or `0` when unavailable.

Pass the optional `provider` argument to filter the result to one configured
provider. Discovery is cached for 24 hours, and a provider that cannot be
queried is skipped or served from cache; an empty result is therefore not proof
that no provider exists.

## Use snodo cloud models

Sign in with `snodo cloud login`, or connect with an API key using
`snodo cloud connect <api_key>`. Name a model `snodo-cloud/<id>`, such as
`snodo-cloud/gpt-6-luna` or `snodo-cloud/@cf/qwen/qwen3.8-27b`. To see the
models enabled for your organisation, run `snodo models --provider snodo-cloud`.
For the coder, use the `opencode-cli/snodo-cloud/<id>` prefix. Billing follows
your organisation's setting in Snodo Cloud under **Models → Billing**, including
one monthly limit. The gateway defaults to `https://models.snodo.dev/v1`; set
`cloud.models_url` to use another URL.

For example, this user configuration selects a cloud model for the coder:

```yaml
model: snodo-cloud/gpt-6-luna
llm:
  coder:
    model: opencode-cli/snodo-cloud/gpt-6-luna
cloud:
  models_url: https://models.snodo.dev/v1
```

## Resolve a model query

Call `resolve_model` with a required `query`. Matching is case-insensitive and
ignores hyphens, underscores, dots, and slashes; the query is matched as a
substring against both the bare model name and its `full_string`.

- One match returns `{"status": "exact", "model": <model-info>}`.
- Multiple matches return `{"status": "ambiguous", "candidates": [...], "hint": ...}`.
  Re-call with the candidate's zero-based `index`, or make the query more
  specific. A valid index returns the selected model with `status: "exact"`.
- No match returns `{"status": "not_found", "query": <query>}`.

An omitted query is an MCP error. An index that is not an integer or is outside
the candidate range is also an MCP error. Resolve first, then use the returned
`full_string`, rather than guessing a provider prefix.

## The normal plan model

A plan normally runs with the configured coder model. `run_plan` accepts an
optional `model`, but omitting it lets the run path select the configured coder
model. This is the default because the project configuration is the operator's
stable policy for the plan, while a per-run override makes the run harder to
compare, reproduce, and audit.

Pin a model for a particular run only for a deliberate reason, such as testing
a migration or isolating a provider-specific failure. A pin is a coder-model
override for that run; it is not a change to project configuration and does not
silently change the validator or classifier configuration.

## Propose a model change

Use `propose_set_model` when the scope should change beyond an individual run.
It requires `task_id`, `proposed_model`, `scope`, and `justification`. The tool
records a proposal under the session's `pending_decisions` with:

- `type: "set_model"`;
- the proposed model and scope;
- the justification;
- `proposed_by: "agent"`; and
- a UTC timestamp.

It returns `status: "pending"`, the task id, the proposal, and an instruction
to run `snodo authorize <task_id>`. The orchestrator does not decide or apply
the change: a human reviews the pending proposal and authorization signs it.
Only a verified coder-scope authorization is consumed to respawn the coder;
the existing decision and audit paths remain in control of the change.

## When resolution fails

If a provider or model name was renamed, such as `opencode` becoming
`opencode-go`, resolving the old query returns `status: "not_found"` with the
original query. `resolve_model` does not migrate names or invent a replacement.
Call `list_models`, identify the current `full_string`, and resolve that exact
or more specific name. Then use the configured/current identifier, or propose a
scoped change with `propose_set_model` for human authorization. Do not keep
retrying the obsolete name or pin an unverified string.
