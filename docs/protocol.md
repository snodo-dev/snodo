
# Snodo protocol.yml — DSL Reference

The protocol file (`protocol.yml`) declares your team's intent: what work can be done, by whom, under which rules, and with what enforcement. The engine reads this declaration and enforces it structurally — no after-the-fact review.

> **Security note: protocol files are executable input.**
>
> A `protocol.yml` is not passive configuration. Protocol fields such as
> `tooling.test_command` and `prepare_command` (for example
> `execution.prepare_command`) are executed through `shell=True`, and snodo does
> **not** sandbox protocol-authored commands. Trust a protocol file like you
> would trust a `Makefile`, `package.json` script, or CI config. Running a
> protocol you did not write is running code you did not read.

## Minimal protocol

```yaml
protocol_id: "my_protocol"
name: "My Protocol"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["security"]
validators:
  - validator_id: "security"
    validator_type: "security"
    criteria:
      - "Check for injection risks"
disagreement_policy: "unanimous"
initial_mode: "producer"
```

This declares one mode (producer) with one tool (edit) and one validator (security checks). The unanimous disagreement policy requires the validator to pass before execution proceeds.

Job notification targets are configured per user in
[`~/.snodo/config.yml`](configuration.md) under `notifications:`. They contain
personal endpoints and credentials, so they do not belong in a project
`protocol.yml`. Supported target types are `ntfy`, `webhook`, `slack`,
`discord`, and `teams`; the default events are `job_finished`, `task_halted`,
`authorization_needed`, and `job_silent`. See the
[notification configuration reference](configuration.md#notifications) for
payloads, environment references, and the test command.

---

## `Protocol` — top-level

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `protocol_id` | string | yes | Unique identifier |
| `name` | string | yes | Human-readable name |
| `version` | string | no | Semantic version (default `"1.0.0"`) |
| `modes` | list[Mode] | yes | One or more operational modes |
| `roles` | list[Role] | no | Participant roles |
| `validators` | list[Validator] | yes | One or more validator configurations |
| `disagreement_policy` | DisagreementPolicy | no | How to resolve validator conflicts: `"unanimous"`, `"majority"`, `"quorum"`, `"any"` (default `"unanimous"`) |
| `initial_mode` | string | yes | Mode ID to start in |
| `global_constraints` | list[Constraint] | no | Protocol-wide constraints (see Constraints) |
| `execution` | ExecutionConfig | no | Execution and recovery configuration (see Execution configuration) |
| `queue` | QueueConfig | no | Default behaviour for queue runs (see Queue configuration) |
| `protected_paths` | list[string] | no | Repository-relative paths a task may not change; checked against the task branch diff |
| `write_allowed_prefixes` | list[string] | no | Project-relative prefixes writable through MCP `write`; default `[".snodo/"]` |
| `exclusive_tools` | set[string] | no | Tools that must be exclusive to one mode; always includes `approve` and `merge` |
| `modules` | list[Module] | no | Optional named repository scopes (see Modules) |
| `metadata` | dict | no | Arbitrary key/value metadata |

---

## Execution configuration

```yaml
execution:
  max_retries: 3
  branch_ttl_days: 7
  branch_prefix: task
  max_recovery_depth: 3
  max_total_fix_attempts: 10
  delivery: push_branch
  prepare_command: "uv sync"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `max_retries` | int | no | Maximum execution retries (default `3`, range 0–10) |
| `branch_ttl_days` | int | no | Branch lifetime in days (default `7`, range 1–30) |
| `branch_prefix` | string | no | Prefix used for task branches (default `"task"`) |
| `max_recovery_depth` | int | no | Maximum recursive subtask recovery depth along a single branch (default `3`, range 0–20) |
| `max_total_fix_attempts` | int | no | Maximum total fix subtasks spawned across the task tree (default `10`, range 1–100) |
| `delivery` | `local_merge`, `push_branch`, `change_request` | no | How completed work is delivered. `local_merge` merges locally; `push_branch` pushes the task branch to `delivery_remote` (default `origin`) without changing the local base branch. `change_request` is not yet supported. Omitted settings preserve the legacy default of leaving work unmerged. |
| `delivery_remote` | string | no | Remote used by `push_branch` (default `origin`). |
| `auto_merge` | bool | no | Deprecated compatibility setting: `true` maps to `local_merge`; `false` preserves the existing leave-unmerged behavior. Do not set alongside `delivery`. |
| `prepare_command` | string | no | Command executed after worktree setup to prepare environment (e.g. `npm ci`, `uv sync`) |

Security note: `execution.prepare_command` is protocol-authored shell input.
snodo does not sandbox it. Treat it like a `Makefile` target, `package.json`
script, or CI config.

---

## Queue configuration

Queue defaults are optional; protocols without a `queue` section use strict,
one-plan-at-a-time runs.

```yaml
queue:
  non_blocking: false
  parallel_runs: 1
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `non_blocking` | bool | no | Pass over a failed plan and continue with later plans (default `false`) |
| `parallel_runs` | int | no | Maximum plans run at once for a non-blocking queue run; positive integer, default `1`. Read only when `non_blocking` is `true`. |

The protocol's top-level `queue` field defaults to `non_blocking: false` and
`parallel_runs: 1` when omitted.

## Modules

Modules are optional named scopes, not operational modes. A task may name a
module to bound writable paths and select module-specific tooling and validators.
The CLI accepts `--module` for `snodo run`, `snodo plan run`, and
`snodo plan add-task`; MCP `dispatch_task` and `generate_spec` accept a
`module`. The named module is returned in plan/job status. An omitted module
preserves project-wide task behavior.

```yaml
modules:
  - module_id: "api"
    paths: ["src/api/**"]
    decisions_path: "docs/api-decisions"
    tooling: {test_command: "pytest tests/api"}
    validators: ["security"]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `module_id` | string | yes | Unique slug-safe module identifier |
| `paths` | list[string] | yes | One or more non-empty repository roots owned by the module |
| `decisions_path` | string or null | no | Decision-record directory; falls back to protocol-level setting |
| `tooling` | dict | no | Per-module tooling; empty falls back to protocol-level tooling |
| `validators` | list[string] | no | Validator IDs applying within this module |

`protected_paths` forbids task changes to listed repository-relative paths.
`write_allowed_prefixes` separately controls the MCP `write` capability; it
defaults to `[".snodo/"]` and does not grant `write` to any mode by itself.

Mode `tools` entries are logical capability grants, translated by the MCP
server using the mapping below. Unknown grant names do not expose operations;
Snodo warns about them when creating an MCP server, including the mode,
unknown name, and known capability names; `snodo ready` reports the same
warning in its diagnostics. Remove misspelled or obsolete grants.
Grant `write` only to modes that need its `write_file` operation; the default
write path allowlist is `.snodo/`, and it does not stage or commit files.

`snodo serve` without `--mode` serves only the current project mode from
`.snodo/state.json`, falling back to `initial_mode`. Passing `--mode` pins the
server to that mode. In either case, planning tools require the mode's `plan`
capability; read-only diagnostics and the guide remain available in every
mode. MCP install creates one explicitly mode-pinned entry per protocol mode.

### `max_recovery_depth` tradeoff

The recovery depth cap controls how deep the engine recurses when spawning subtasks to fix validator rejections. It is configured at the protocol level (`execution.max_recovery_depth`, default `3`) and can be overridden per mode (`mode.max_recovery_depth`). When a mode is silent (`null`), it inherits the protocol's setting.

- **Established Repositories / Feature Build Modes (`max_recovery_depth: 3`, default)**: When a codebase has a stable, verified build and test harness, validator rejections stem from implementation bugs or criterion mismatches. Legitimate fixes often require 2–3 incremental subtask turns (e.g., fixing the primary implementation, then addressing edge-case test failures). Recovery stall detection (which halts after 2 identical validator verdicts) and `max_total_fix_attempts` prevent runaway loops if non-convergence occurs.
- **Greenfield Setup Modes (`max_recovery_depth: 1`)**: In bootstrap phases like `decide` or `scaffold`, early validator failures are setup or environment faults (unrecorded ADRs, missing lockfiles, an unset placeholder test command) that subtasks cannot fix without human intervention. Setting `max_recovery_depth: 1` on setup modes bounds expenditure to a single recovery attempt, while allowing the downstream `build` mode to use `max_recovery_depth: 3` once the harness is verified.

---

## `Mode` — operational stages

Each mode defines what tools are available, which validators run, and what happens when work is complete. The engine runs single-mode per invocation; cross-mode handoffs are explicit user actions.

```yaml
modes:
  - mode_id: "producer"
    name: "Producer Mode"
    tools:
      - "edit"
      - "dispatch"
    validators:
      - "security"
      - "architecture"
    transitions:
      complete: "reviewer"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode_id` | string | yes | Unique identifier within the protocol |
| `name` | string | yes | Human-readable name |
| `description` | string or null | no | Human-readable mode description |
| `tools` | list[string] | no | Available logical tools — see Tool table below |
| `validators` | list[string] | no | Validator IDs active in this mode |
| `transitions` | dict[string, string] | no | Declarative event→target-mode mappings (documented, not engine-executed) |
| `constraints` | list[Constraint] | no | Mode-specific constraints |
| `coder` | string | no | Coder backend (`"litellm"`, `"mock"`; `"opencode"` and `"opencode-cli"` are **experimental** — see below) |
| `coder_config` | dict | no | Coder backend configuration |
| `delivery` | `local_merge`, `push_branch`, `change_request` | no | Override protocol-level `execution.delivery` for this mode (default `null`) |
| `auto_merge` | bool | no | Deprecated compatibility override: `true` maps to `local_merge`, `false` to leave-unmerged. Do not set alongside `delivery`. |
| `max_recovery_depth` | int | no | Override protocol-level `execution.max_recovery_depth` for this mode (default `null`) |
| `concurrency` | int | no | Per-mode concurrency ceiling (positive integer); absent uses `coder_config.concurrency` if set, otherwise `1` |

`concurrency` is resolved per mode: `mode.concurrency` takes precedence,
then `mode.coder_config.concurrency` is used when present, otherwise the
ceiling is `1`. There is no protocol-level `execution.concurrency` setting.

### Coder backends

snodo supports interchangeable code generation backends declared via `--coder` or a mode's `coder` field:

- **`litellm`** *(default, supported)*: Routes completions via LiteLLM (~100+ providers). The engine writes artifacts via `WorkspaceMCP` and manages commits and per-turn telemetry.
- **`agy`** *(host CLI)*: Shells out to Antigravity CLI (`agy -p`) on the host. Edits files in place and commits to git upon completion.
- **`codex-cli`** *(host CLI)*: Runs `codex exec --json` in `workspace-write` mode using the operator's Codex subscription. Edits files in place and commits to git upon completion.
- **`opencode-cli`** *(host CLI, experimental)*: Shells out to host `opencode run`. Edits files in place and commits to git upon completion.
- **`opencode`** *(container, experimental)*: OpenCode server running in Docker over HTTP (`POST /session`, `POST /session/{id}/message`). Edits files in place in the volume-mounted workspace and commits to git upon completion.
- **`mock`** *(supported)*: Deterministic stub for dry runs and testing.

#### Selection Precedence

1. `--mock` / `use_mock_coder=True` option (always returns `'mock'`).
2. `--coder <name>` CLI option on `snodo run` / `snodo plan run`.
3. `coder: <name>` field in the active protocol mode definition.
4. Model prefix mapping (`codex-cli/`, `opencode-cli/`, `opencode/`, `agy/`, `gpt`/`o1`/`o3`, `claude`, `gemini`).
5. Default (`litellm`).

#### Model Role Separation (Judging vs Execution)

- **`-m` / `--model` sets the JUDGING model**: Passing `-m` specifies the model used by LiteLLM for **validators** (pre/post-execute gates) and the **classifier**.
- **External CLI Coders use their own model catalogs**: External CLI tools (`agy`, `codex-cli`, `opencode-cli`) use their own CLI configuration and internal catalogs. Non-prefixed model names passed to `-m` are stripped by `SubprocessCoderAdapter._bare_model()` so the CLI uses its own default model.
- **Explicit Coder Model**: To set an external coder's model explicitly while keeping `-m` for validators, use the adapter namespace prefix (e.g. `--model codex-cli/gpt-5-codex`, `--model agy/gemini-2.5-pro`, or `--model opencode-cli/claude-3-7-sonnet`).

#### In-Place Coders & Governance

External in-place coders (`opencode`, `opencode-cli`, `agy`) inherit `InPlaceCoderAdapter` (`skip_engine_commit = True`, `skip_workspace_write = True`). They write directly to the working tree, snapshot and enforce `.snodo/` mutation boundaries (ADR 027), and commit the result so post-execute validators reviewing `git diff HEAD~1..HEAD` see the exact change (ADR 030).

Per **ADR 034**, the absence of per-turn usage or token metrics for external coders is a stated decision (non-goal), not an attestation gap: token and cost metrics reside in per-job `state.json` telemetry (`snodo meta`), while snodo's audit log attests to governance decisions and verification evidence.

### Tool set restrictions (WF1)

Approval-conferring tools (`approve`, `merge` by default) must appear in at most one mode. If any two modes share an exclusive tool, the protocol fails to load with `WF1Violation`. Non-exclusive tools (e.g. `edit`, `test`) may be shared across modes. This preserves the no-self-approval guarantee — the producer (which holds `edit`) and the reviewer (which holds `approve`/`merge`) can never both hold an approval tool — without requiring total disjointness. The exclusive set is declared via `exclusive_tools` at the protocol root and defaults to `{approve, merge}`; it may be extended but not shrunk.

### Concrete tool mapping

Each logical tool maps to one or more MCP operations:

| Protocol tool | Concrete MCP tools |
|---------------|-------------------|
| `edit` | `read_file`, `list_files`, `list_models`, `resolve_model`, `recon`, `get_recon_status`, `get_recon_results` |
| `write` | `write_file` |
| `decide` | `propose_adjudicate`, `propose_set_model` |
| `dispatch` | `dispatch_task`, `get_job_status`, `list_jobs`, `get_job_logs`, `watch_job`, `retry_job` |
| `test` | `run_tests` |
| `validate` | `run_tests` |
| `review` | `read_file`, `list_files`, `read_diff`, `get_status`, `recon`, `get_recon_status`, `get_recon_results` |
| `approve` | `stage_files`, `commit` |
| `commit` | `stage_files`, `commit` |
| `merge` | `create_branch`, `stage_files`, `commit`, `merge_branch`, `delete_branch` |
| `pr` | `create_pr`, `read_pr_diff`, `post_review_comment`, `approve_pr`, `reject_pr`, `merge_pr` (MCP tool names; provider operations use the neutral change-request contract described in [Code-host providers](extending.md#4-code-host-providers)) |
| `plan` | `decompose`, `generate_spec`, `validate_plan`, `propose_plan`, `get_plan`, `run_plan`, `record_task_status`, `queue_list`, `queue_create`, `queue_move`, `queue_remove`, `queue_validate`, `queue_run` |
| `queue` | `queue_list`, `queue_create`, `queue_move`, `queue_remove`, `queue_validate`, `queue_run` |
| `read` | `read_file`, `list_files` |

For dispatched jobs, use the browser watch link returned by `watch_job` when
the server has a reachable HTTP base URL (including a managed tunnel). The
link is a short-lived, job-scoped read-only capability. The MCP Apps panel is
optional and only some hosts render it; other clients receive a plain-text
snapshot. From the CLI, `snodo logs <job_id> --watch` is the live output path.

### Reference modes

Shipped templates define modes suited to their workflows; common roles include:

**Producer mode** — generates code. Typical tools: `edit`, `dispatch`, `test`, `validate`. Validators check security, architecture, conventions before execution.

**Reviewer mode** — reviews and integrates. Typical tools: `review`, `approve`, `merge`, `pr`. Validators re-check security at review time.

**Planner mode** — decomposes and manages work plans. Typical tool: `plan`, which grants decomposition, spec generation, plan lifecycle, task status, and queue operations. Validators check intent clarity, scope, and completeness.

Shipped templates grant these capabilities to match their workflows. `solo`
gives its producer mode the complete single-operator loop, including plan and
queue control plus `.snodo/`-confined `write_file`. `team` gives plan/queue
control and `.snodo/`-confined writing to its separate planner mode, while
reviewer retains approval and merge. `2+n` gives its producer plan/queue
control and `.snodo/`-confined writing, with reviewer-only approval and merge.
The greenfield template has a planning mode with the same confined authoring
surface. See the shipped YAML files for each complete grant set.

---

## `Role` — participant identity

```yaml
roles:
  - role_id: "lead"
    name: "Tech Lead"
    permissions: ["review", "approve"]
    responsibilities: ["architecture decisions", "code review"]
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `role_id` | string | yes | Unique role identifier |
| `name` | string | yes | Human-readable name |
| `permissions` | list[string] | no | Allowed actions |
| `responsibilities` | list[string] | no | Expected duties |

Roles declare intent; the engine does not enforce role-based access at runtime. They are reference documentation for protocols with human-in-the-loop participants.

---

## `Validator` — evaluation gate

```yaml
validators:
  - validator_id: "security"
    validator_type: "security"
    evaluation_phase: "pre_execute"
    criteria:
      - "Check for SQL injection"
      - "Validate input sanitization"
    severity_cap: "blocker"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `validator_id` | string | yes | Unique identifier |
| `validator_type` | string | yes | Backend type: `"security"`, `"architecture"`, `"quality"`, `"conventions"`, `"planning"`, `"protocol"`, or custom |
| `evaluation_phase` | string | no | When to run: `"pre_execute"`, `"post_execute"`, `"mode_transition"` (default `"pre_execute"`) |
| `criteria` | list[string] | no | Prompts for LLM-backed validators; ignored by non-LLM backends |
| `constraints` | list[Constraint] | no | Additional predicate constraints |
| `tooling` | dict | no | Backend tooling configuration (e.g. `test_command` for the quality validator) |
| `severity_cap` | string | no | Maximum severity this validator can emit. `"warn"` caps blocker to warn — useful for experimental validators. `"blocker"` or absent = full power. |
| `scope` | `"task"` or `"wave"` | no | Unit judged (default `"task"`); wave scope is valid only for `pre_execute` |
| `tools` | list[string] | no | Read-only tool allowlist; empty means no tool access |
| `check_tool_access` | bool | no | When true, refuse rather than pass criteria requiring a capability outside the validator's declared tools (default `false`) |
| `model` | string or null | no | Validator-specific LLM model override; otherwise falls back to coder model / `default_model` |
| `max_tool_turns` | int or null | no | Read-tool-loop turn budget override (1–200); otherwise uses configured `llm.validator.max_tool_turns` |
| `judges_spec` | bool | no | Whether critique is about spec wording and may feed the spec-authoring rewriter (default `false`) |

Security note: `tooling.test_command` (used by the `quality` validator) is
protocol-authored shell input. snodo does not sandbox it. Treat it like a
`Makefile` target, `package.json` script, or CI config.

Every shipped template sets a `test_command` default that runs on any POSIX
shell and exits zero, so a project with no test framework yet can always run.
Auto-detection (marker files) and `snodo init --test-command` take precedence
over the default; when the default itself runs, the quality validator records
audit outcome `no_tests` — it states that no tests were executed rather than
claiming a pass (Fixes #215).

### Validator types

| Type | Backend | What it does |
|------|---------|-------------|
| `security` | LLM | Reviews task spec against security criteria |
| `architecture` | LLM | Reviews task spec against design criteria |
| `conventions` | LLM | Reviews against naming/file/doc conventions |
| `planning` | LLM | Reviews plan intents against planning criteria |
| `performance` | LLM | Reviews performance criteria |
| `testing` | LLM | Reviews testing criteria |
| `protocol` | LLM | Checks whether work belongs in the current mode |
| `quality` | subprocess | Runs the repo's test suite (auto-detects test command) |
| `acceptance` | built-in | Judges produced artifacts against task acceptance criteria |
| custom | your code | Register any string via the ValidatorRegistry |

### Severity

Every validator result carries one of three severities, ordered `pass < warn < blocker`:

| Severity | Meaning | Effect on execution |
|----------|---------|-------------------|
| `pass` | No issues found | Counts toward policy threshold |
| `warn` | Advisory concern | Withholds approval — does NOT count toward policy threshold (post-policy-fix: warn ≠ approval) |
| `blocker` | Critical issue | Halts execution unconditionally (INV3) — bypasses all policy thresholds |

### A judge is made to decide

A validator returns a verdict: `pass`, `warn`, or `blocker`. There is no
fourth thing it can return.

At the boundary of its tool budget a judge is asked for a verdict and given no
way to keep reading — the final turn offers `submit_verdict` alone, and the
read tools are withdrawn. A verdict reached on incomplete reading is a real
verdict: `warn` exists for exactly that, and a `warn` escalating to a human is
the flow working, not failing. The judge is told to say in its justification
that its view was partial.

A judge that still returns nothing — prose, an empty reply, or a read tool call
on the final turn — is an error. It takes the path errors already take:
fail-closed. An engine that cannot get a verdict out of its quorum must not
proceed, and nothing new is added to express this. The record of what a failing
judge examined travels on the error result (`examined`), so a human can still
see how far the inspection got.

### The halt payload records the attempts, not only the result

The halt payload's `validator_results` are the final verdicts. Alongside them,
`attempts` states how the task got there, so a clean pass and a hard-won one are
distinguishable without reading the logs:

| Field | Meaning |
|-------|---------|
| `attempts.total` | How many judged attempts the task took (root + recovery subtasks) |
| `attempts.coder_dispatches` | How many coder runs the chain cost |
| `attempts.history` | `{attempt, outcome}` per attempt, most recent `_MAX_ATTEMPT_HISTORY` entries |
| `attempts.omitted` | Count dropped from the front of a truncated `history` |

Each `outcome` is one of `passed`, `warned`, `blocked`, or `error` — canonical
tokens, not prose, so payloads aggregate across projects. A task that passed
first time reports one attempt. The pre-execute `pre_validation` verdicts feed
the outcome only when a task halted before execution. Halt types and
`validator_results` are unchanged: `attempts` is the history that precedes them.

---

## `DisagreementPolicy` — validator consensus

Four policies determine how validator results combine into a proceed/block decision. All threshold on `pass_count` only; `warn` withholds approval.

```yaml
disagreement_policy: "majority"
```

| Policy | Rule | When used |
|--------|------|-----------|
| `"unanimous"` | `pass_count == total_count` | Every validator must approve — critical systems |
| `"majority"` | `pass_count > total_count / 2.0` | >50% approval — team workflow |
| `"quorum"` | `pass_count >= total_count * 0.67` | Configurable 2/3 threshold |
| `"any"` | `pass_count >= 1` | At least one approval — permissive front-end |

The threshold is evaluated **per phase**, and `total_count` is the number of
validators that ran in that phase. Under `"unanimous"` a phase with exactly one
validator is therefore an **unopposed veto**: that validator alone decides
whether the phase passes. This is worth noticing for `post_execute` — the phase
that reviews completed work — because `quality` and `acceptance` both run there
in the shipped templates, and a single post-execute validator under unanimous
can block every task on operational noise (a flaky test, a tool outage) with no
second opinion. The verifier warns at load time when a protocol has exactly one
`post_execute` validator under `"unanimous"`; either add a second post-execute
judge or choose a different policy.

**INV3**: `blocker_count > 0` halts execution **before** any policy logic runs. A single blocker overrides every policy — by design. This is the structural guarantee that no critical defect can be voted down.

### Actions

| Action | When |
|--------|------|
| `PROCEED` | Policy threshold met, zero warns |
| `PROCEED_WITH_LOG` | Policy threshold met, one or more warns present |
| `ESCALATE` | Policy threshold not met, no blockers — requires human resolution |
| `HALT` | One or more blockers present (INV3 override) |

When ESCALATE fires, the task is blocked and a structured payload is emitted. Use `snodo authorize <task_id>` to review and sign the pending decision. The engine tracks the decision in the session checkpoint and takes the declared action on resume.

---

## `Constraint` — predicate-enforced rules

```yaml
global_constraints:
  - constraint_id: "files_in_scope"
    description: "Modified files must be within project scope"
    predicate: "files_in_scope"
    params:
      scope_paths: ["src/**", "tests/**"]
    severity: "blocker"
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `constraint_id` | string | yes | Unique identifier |
| `description` | string | yes | Human-readable description |
| `expression` | string | no | Boolean expression (legacy; documentation-only when predicate is set) |
| `predicate` | string | no | Registered predicate name to evaluate |
| `params` | dict | no | Parameters passed to the predicate |
| `severity` | string | no | `"pass"`, `"warn"`, or `"blocker"` (default `"blocker"`) |

Constraints can be placed at three levels:
- `global_constraints` — enforced on every task
- `mode.constraints` — enforced only in that mode
- `validator.constraints` — enforced by that validator

Shipped predicates: `files_in_scope`, `tests_exist_for_modified`, `no_secrets_in_diff`. Custom predicates can be registered via the PredicateRegistry API.

A task scoped to a module (ADR 041) is bounded by that module's declared `paths`
instead of `scope_paths` — writes outside the module fail the constraint even
when the protocol scope covers the repository. Unscoped tasks are judged against
`scope_paths` unchanged, and module scope never restricts reads.

---

## Token

The engine issues a JWT validation token when the policy threshold is met with no blockers, verifies it at the execute boundary, and consumes it there — single-use per task, expiring at the configured TTL (default 600 seconds). This discipline lives in the engine loop, not at the MCP tool surface: `validate_task` on the MCP path runs the same pre-execute quorum and records the token that the next `dispatch_task` consumes as the audit link, but no MCP tool call is refused for want of a token the caller holds (ADR 047).

Configure in `~/.snodo/config.yml`:
```yaml
engine:
  token_ttl_seconds: 600
```

Or in code via `TokenIssuer(ttl_seconds=...)`.

---

## Well-formedness — WF1 through WF5

Every protocol is verified at load time. A violation raises a `ProtocolWellFormednessError` with a list of specific failures. The protocol will not load if any check fails.

| Check | Enforces |
|-------|----------|
| **WF1** — Mode Separation | Approval-conferring tools (`approve`, `merge` by default) must be exclusive to one mode. Non-exclusive tools may be shared. |
| **WF2** — Role Uniqueness | Role IDs must be unique across the protocol. |
| **WF3** — Validator Coverage | Every validator referenced by a mode must exist in the `validators` list. The `initial_mode` must exist. Any mode with `dispatch` must have at least one `pre_execute` validator. |
| **WF4** — Policy Completeness | Disagreement policy must match the validator count: unanimous needs ≥1, majority needs ≥2, quorum warns at <3. |
| **WF5** — Constraint Consistency | Constraint IDs must be unique. Predicate names, if set, must be registered. |

---

## Templates

The template registry is derived from the YAML files in `snodo/protocols/templates/` — adding a template file is sufficient to make it selectable. Shipped templates:

| Template | Modes | Signature |
|----------|-------|-----------|
| `solo` | producer only | Single-coder, no reviewer handoff |
| `team` | producer → reviewer → planner | Two-stage with separate approval authority |
| `2+n` | producer → reviewer | Paper's reference config with predicate constraints (`files_in_scope`, `tests_exist_for_modified`, `no_secrets_in_diff`) |
| `intent` | producer | Intent-driven; warn-only spec validators |
| `bugfix-surgeon` | producer | Bug-fix flow with post-execute review gate |
| `feature-warden` | producer | Feature flow with scope guard |
| `greenfield` | decide → scaffold → build | Phased greenfield build with per-phase exit gates |

Use `snodo init --template <name>` to start from a template, or run `snodo init` to choose from the interactive menu.
