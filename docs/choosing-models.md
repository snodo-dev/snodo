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

## Benchmark one model

For models accessed through a ChatGPT subscription, configure `auth: chatgpt-oauth` and sign in with `snodo models --provider chatgpt --authenticate`; see the [ChatGPT provider guide](providers/chatgpt.md). A known model can be benchmarked by name even if that provider cannot list models.

Benchmark a specific model by name with `snodo models --benchmark <provider>/<model>`, for example `snodo models --benchmark openai/gpt-4o`. The equivalent longer form is `snodo models --provider <provider> --id <model> --benchmark`. The benchmark makes a billed call and reports timing and throughput measurements; use it when you want a like-for-like measurement rather than aggregate job statistics.

## Compare a task benchmark

Use `snodo models --set-baseline --plan <plan> --task <task>` to capture a
completed task's solution diff and recorded model/coder as its local baseline.
The baseline also keeps the available usage summary and coder-turn count in
`.snodo/baselines/<plan>/<task>/baseline.json`.

Run a candidate against the baseline's recorded base commit with
`snodo models --benchmark-run --plan <plan> --task <task> --model <provider/model>`.
Before dispatch, Snodo reports the baseline and candidate coder and model, and
flags when their coder harnesses differ. The candidate is run without merging.
The result includes its status, halt type, and reason when available; a
candidate that does not complete is still reported for comparison.

`snodo models --compare --plan <plan> --task <task>` compares a stored baseline
with a candidate job (the latest matching job by default); add `--job` to select
a job explicitly. The score covers patch content, validator outcome, and changed
files. Change-size, coder time, total elapsed time, coder turns, coder and
validator tokens, output-token rate, cost, and post-validation findings are
shown for context but are not scored. Missing measurements or findings appear
as `n/a`. Token counts may not be comparable when the coder harnesses differ;
the comparison reports this caveat. `--json` emits the comparison data as JSON.

For example, if a benchmark candidate did not complete, its comparison can
still show `candidate status: failed (halt: blocked)` alongside the reason and
baseline-to-candidate score and run metrics; a failed candidate does not imply
that those measurements were scored.

### End-to-end walkthrough

1. Choose a task with a completed recorded run. For example, use plan
   `coder-tool-surface-guardrails-956` and task `1.1_bounded_tool_results`.
   The plan may be archived; see the [command reference](command-reference.md)
   for the prerequisites and details.
2. Capture that run as the baseline:

   ```sh
   snodo models --set-baseline --plan coder-tool-surface-guardrails-956 --task 1.1_bounded_tool_results
   ```

3. Run a candidate coder model against the baseline's recorded base commit. Use
   the `opencode-cli/<provider>/<model>` form, for example
   `opencode-cli/anthropic/claude-sonnet-4-5`; a bare provider string makes
   direct paid API calls. The `--model` option selects the coder model.

   ```sh
   snodo models --benchmark-run --plan coder-tool-surface-guardrails-956 --task 1.1_bounded_tool_results --model opencode-cli/anthropic/claude-sonnet-4-5
   ```

   The command prints the baseline and candidate coder/model, and prints
   `Harness differs: baseline ran in <coder>; candidate runs in <coder>.` when
   the coder harness changes. It then prints the comparison.
4. Read the comparison score and its direction (`better`, `worse`, or `same`).
   Its three scored signals are content, validator outcome, and changed surface.
   Change size, coder time, elapsed time, turns, token counts, output-token rate,
   cost, and post-validation findings are display-only; missing values appear
   as `n/a`. For field definitions, see the [comparison JSON reference](machine-interface.md).
5. To compare another model, repeat the benchmark command with its model string.
   The run compares automatically; to select a particular candidate job, add
   `--job` followed by that job’s recorded ID to this command:

   ```sh
   snodo models --compare --plan coder-tool-surface-guardrails-956 --task 1.1_bounded_tool_results
   ```

A different coder harness makes token counts not comparable. A candidate that
does not complete is still shown in the comparison.

## Benchmark one validator

1. Choose a task with a recorded baseline. For this walkthrough, use plan
   `coder-tool-surface-guardrails-956` and task `1.1_bounded_tool_results`.
   Open `.snodo/baselines/coder-tool-surface-guardrails-956/1.1_bounded_tool_results/baseline.json`
   and find the `validator_id` values in its `validator_results` array. Copy the
   ID you want to replay; the baseline is the source of the recorded IDs.
2. Replay that validator with an explicit candidate model. For example, replace
   `<recorded-validator-id>` with one of the IDs from the baseline:

   ```sh
   snodo models --benchmark-run \
     --plan coder-tool-surface-guardrails-956 \
     --task 1.1_bounded_tool_results \
     --validator <recorded-validator-id> \
     --model opencode-cli/<provider>/<model>
   ```

   A bare `<provider>/<model>` string makes direct paid API calls; use the
   `opencode-cli/` prefix for the OpenCode CLI model form. The replay notice is
   printed to stderr and identifies the validator, phase, judged commit, and
   candidate model; it also says, `This validator call is billed or uses your subscription.`
3. Read the comparison: `Validator verdict: <recorded severity> -> <new
   severity> (<direction>)`, followed by whether the justification differs.
   Elapsed time, input and output tokens, and cost are shown for recorded and
   replayed results; each metric says `not scored`. Direction is based on
   severity, and justification, time, tokens, and cost do not affect it. See the
   [models command reference](command-reference.md) for the command details.

The replay judges the recorded commit in a temporary worktree, leaving your
checkout untouched. It uses no verdict cache and never merges.

## Discover configured models

Call `list_models` to inspect the models discovered from Snodo's configured
providers. For setup instructions, see the guides for [Cloudflare](providers/cloudflare.md),
[Ollama](providers/ollama.md), [OpenCode Go](providers/opencode-go.md),
[Anthropic](providers/anthropic.md), [OpenAI](providers/openai.md), [ChatGPT subscription](providers/chatgpt.md),
[Google](providers/google.md), [OpenRouter](providers/openrouter.md), and
[DeepSeek](providers/deepseek.md). It returns an object with a `models` array.
Each item contains:

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
