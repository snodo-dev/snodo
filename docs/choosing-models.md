# Choosing models from the orchestrator's side

<!-- snodo-guide topic="models" aliases="model,choosing-models" summary="Discover, resolve, and propose model choices" section="# Choosing models from the orchestrator's side" -->

An orchestrator should discover and resolve a model before proposing a change.
The normal plan path uses the model configured for the project; choosing a
different model for one run is an explicit exception.

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
