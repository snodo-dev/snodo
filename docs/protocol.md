
# Snodo protocol.yml — DSL Reference

The protocol file (`protocol.yml`) declares your team's intent: what work can be done, by whom, under which rules, and with what enforcement. The engine reads this declaration and enforces it structurally — no after-the-fact review.

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
| `disagreement_policy` | string | no | How to resolve validator conflicts: `"unanimous"`, `"majority"`, `"quorum"`, `"any"` (default `"unanimous"`) |
| `initial_mode` | string | yes | Mode ID to start in |
| `global_constraints` | list[Constraint] | no | Protocol-wide constraints (see Constraints) |
| `execution` | ExecutionConfig | no | Execution and recovery configuration (see Execution configuration) |
| `metadata` | dict | no | Arbitrary key/value metadata |

---

## Execution configuration

```yaml
execution:
  max_recovery_depth: 3
  max_total_fix_attempts: 10
  auto_merge: false
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `max_recovery_depth` | int | no | Maximum recursive subtask recovery depth along a single branch (default `3`, range 0–20) |
| `max_total_fix_attempts` | int | no | Maximum total fix subtasks spawned across the task tree (default `10`, range 1–100) |
| `auto_merge` | bool | no | Whether a completed task's branch merges into the base branch automatically (default `false`) |
| `prepare_command` | string | no | Command executed after worktree setup to prepare environment (e.g. `npm ci`, `uv sync`) |

### `max_recovery_depth` tradeoff

The recovery depth cap controls how deep the engine recurses when spawning subtasks to fix validator rejections:

- **Established Repositories (`max_recovery_depth: 3`, default)**: When a codebase has a stable, verified build and test harness, validator rejections stem from implementation bugs or criterion mismatches. Legitimate fixes often require 2–3 incremental subtask turns (e.g., fixing the primary implementation, then addressing edge-case test failures). Recovery stall detection (which halts after 2 identical validator verdicts) and `max_total_fix_attempts` prevent runaway loops if non-convergence occurs.
- **Greenfield & Bootstrap Projects (`max_recovery_depth: 1`)**: In a new repository with placeholder test commands or unverified tooling, early validator failures are usually setup/environment errors (missing lockfiles, broken test runners) that automated subtasks cannot fix. Capping recovery at depth 1 bounds expenditure to a single recovery attempt until the project environment is verified by the operator.

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
| `tools` | list[string] | no | Available logical tools — see Tool table below |
| `validators` | list[string] | no | Validator IDs active in this mode |
| `transitions` | dict[string, string] | no | Declarative event→target-mode mappings (documented, not engine-executed) |
| `constraints` | list[Constraint] | no | Mode-specific constraints |
| `coder` | string | no | Coder backend (`"litellm"`, `"mock"`) |
| `coder_config` | dict | no | Coder backend configuration |

### Tool set restrictions (WF1)

Approval-conferring tools (`approve`, `merge` by default) must appear in at most one mode. If any two modes share an exclusive tool, the protocol fails to load with `WF1Violation`. Non-exclusive tools (e.g. `edit`, `test`) may be shared across modes. This preserves the no-self-approval guarantee — the producer (which holds `edit`) and the reviewer (which holds `approve`/`merge`) can never both hold an approval tool — without requiring total disjointness. The exclusive set is declared via `exclusive_tools` at the protocol root and defaults to `{approve, merge}`; it may be extended but not shrunk.

### Concrete tool mapping

Each logical tool maps to one or more MCP operations:

| Protocol tool | Concrete MCP tools |
|---------------|-------------------|
| `edit` | `read_file`, `list_files` |
| `dispatch` | `dispatch_task` |
| `resolve` | `resolve_disagreement` |
| `test` | `run_tests` |
| `validate` | `run_tests` |
| `review` | `read_file`, `list_files`, `read_diff`, `get_status` |
| `approve` | `stage_files`, `commit` |
| `commit` | `stage_files`, `commit` |
| `merge` | `create_branch`, `stage_files`, `commit`, `merge_branch`, `delete_branch` |
| `pr` | `create_pr`, `read_pr_diff`, `post_review_comment`, `approve_pr`, `reject_pr`, `merge_pr` |
| `plan` | `decompose`, `generate_spec`, `validate_plan` |
| `assess` | `read_file`, `list_files` |

### Reference modes

The shipped templates implement three standard modes:

**Producer mode** — generates code. Typical tools: `edit`, `dispatch`, `test`, `validate`. Validators check security, architecture, conventions before execution.

**Reviewer mode** — reviews and integrates. Typical tools: `review`, `approve`, `merge`, `pr`. Validators re-check security at review time.

**Planner mode** — decomposes work. Typical tools: `assess`, `plan`. Validators check intent clarity, scope, completeness.

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

### Validator types

| Type | Backend | What it does |
|------|---------|-------------|
| `security` | LLM | Reviews task spec against security criteria |
| `architecture` | LLM | Reviews task spec against design criteria |
| `conventions` | LLM | Reviews against naming/file/doc conventions |
| `planning` | LLM | Reviews plan intents against planning criteria |
| `protocol` | LLM | Checks whether work belongs in the current mode |
| `quality` | subprocess | Runs the repo's test suite (auto-detects test command) |
| custom | your code | Register any string via the ValidatorRegistry |

### Severity

Every validator result carries one of three severities, ordered `pass < warn < blocker`:

| Severity | Meaning | Effect on execution |
|----------|---------|-------------------|
| `pass` | No issues found | Counts toward policy threshold |
| `warn` | Advisory concern | Withholds approval — does NOT count toward policy threshold (post-policy-fix: warn ≠ approval) |
| `blocker` | Critical issue | Halts execution unconditionally (INV3) — bypasses all policy thresholds |

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

---

## Token

The engine issues a JWT validation token when the policy threshold is met with no blockers. Mutating tools (write, commit, merge, etc.) require a valid token at invocation time — enforced by `_enforce_wf1` in the MCP server layer. Tokens are single-use per task and expire at the configured TTL (default 600 seconds).

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
