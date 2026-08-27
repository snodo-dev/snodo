# Snodo Architecture

How enforcement works from top to bottom. For individual design decisions, see [ADRs](decisions/).

> **Maintenance note.** This document references files and functions, deliberately
> **not line numbers** — line references go stale within weeks and were the main
> source of drift in earlier revisions. If a change alters package structure, the
> execution path, or where an invariant is enforced, update this file in the same PR
> (see [CONTRIBUTING](../CONTRIBUTING.md)). Current findings and priorities live in
> GitHub Issues, never here.

## Overview

Snodo is a **policy-vs-mechanism** engine: you declare what a valid software development process looks like (`protocol.yml`), and the engine enforces it structurally — no after-the-fact review, no trust in agent compliance. AI agents participate as first-class team members, gated by the same rules as human contributors.

The 2+N model underlies everything: **2** human-in-control roles (producer and reviewer) plus **N** specialized AI agents that operate within those roles. Mode separation is structural — the engine refuses to load a protocol where two modes share an approval-conferring tool (WF1), and every mutating operation requires a cryptographically valid token that can only be issued by a satisfied validator quorum (INV1/INV3).

## Package map

A uv workspace of five packages, all under the `snodo.*` namespace, plus the root
CLI/TUI package.

| Package | Responsibility | Key modules |
|---------|---------------|-------------|
| **root** (`snodo/`) | CLI (`snodo`), Textual dashboard, prompts | `cli/main.py`, `cli/commands/*.py` |
| **snodo-core** | Kernel: path resolution, project identity, constraint predicates, sandbox | `paths.py` (`resolve_home()` → `SNODO_HOME`, default `~/.snodo`) |
| **snodo-tools** | Tool primitives the agent acts through | `workspace.py` (INV2 path validation), `git.py`, `shell.py`, code-host providers |
| **snodo-foundation** | Infrastructure + protocol compiler + shipped templates | `infrastructure/`: `tokens.py`, `audit.py`, `session.py`, `decisions.py`, `memory.py`, `cloud_sync.py` · `compiler/`: `models.py`, `verifier.py` (WF1–WF5) · `protocols/templates/*.yml` |
| **snodo-engine** | The executable part | `engine/`: `loop.py` (graph builder), `closure.py` (recursive driver), `policy.py`, `constraints.py`, `nodes/*` · `validators/`: `runner.py`, `registry.py`, `llm_validator.py`, `quality.py`, `protocol_adherence.py`, `context.py` · `coders/` |
| **snodo-mcp** | MCP server surface for external agents | `server.py`, `decision_handlers.py`, planner, PR, recon, jobs |

Two persistent stores, both user-global under `SNODO_HOME` (not per-project — task
execution may run inside a git worktree, so project-relative state would fragment):
`checkpoints.db` (LangGraph SqliteSaver) and `sessions/`.

## Key concepts

| Concept | Mechanism | Invariant |
|---------|-----------|-----------|
| Mode separation | Exclusive approval-conferring tools, verified at load time | WF1 |
| Validator quorum | N validators vote; policy decides proceed/block | Decision flow below |
| Non-overridable block | Any `blocker` halts before policy logic | INV3 |
| Token-gated mutations | Mutating MCP tools require JWT validation token | WF1, INV1 |
| Audit immutability | Hash-chained event log, append-only | INV4 |
| Session resumability | File-backed checkpoint per (mode, project) | INV5 |
| Recovery loop | Failed tasks re-enter governance on resume | Kleene closure |
| Coder independence | Adapter pattern over LLM backends | Coder adapter |

## Decision flow — how a task is evaluated

```
Governance → Validate → [Execute] → Post-validate → [Move-next] → Complete
     ↑                         |                          |
   Resolution              Blocked (ESCALATE)        Blocked (HALT/ESCALATE)
```

1. **Governance**: Checks iteration bounds (50 max), runs environment preparation on first iteration (detecting lockfile markers or `execution.prepare_command`; operational failures halt immediately with `validator_error`), consumes any pending resolution. If the session has a `proceed` decision for this task, `resolution_override` is set and validation is skipped. If `halt`, the task is blocked immediately.

2. **Validate** (`pre_execute`): Runs validators configured for the current mode and phase. Results feed into the `PolicyEvaluator`:
   - `blocker_count > 0` → **HALT** (INV3 — unconditional, all policies)
   - Threshold on `pass_count` per policy: unanimous needs all, majority needs >half, quorum needs ≥0.67×total, any needs ≥1
   - `warn` withholds approval — does NOT count toward the pass threshold
   - Threshold met → token issued → proceed to execute
   - Threshold not met → **ESCALATE** → `pending_disagreement` populated → task blocked, human resolves

3. **Execute**: The coder generates code artifacts. Files are written via WorkspaceMCP, staged and committed via GitMCP. Every mutation requires a valid JWT token (WF1 enforcement at the MCP server layer).

4. **Post-validate** (`post_execute`): Runs post-execute validators (e.g., quality/test-runner). Same policy evaluation. Can ESCALATE or HALT after execution.

5. **Move-next**: Marks task complete. Transitions are declarative — documented in the protocol, not engine-executed.

## Execution path (concrete)

What `snodo run "<task>"` actually does, in call order:

```
cli/commands/run_cmd.py
  → load protocol (YAML → Protocol model; compiler/verifier runs WF1–WF5)
  → ConfigManager (config.yml → provider/model resolution)
  → SessionManager (create or resume; INV5)
  → git worktree setup for the task (`infrastructure/worktree.py:create_worktree` /
    `setup_for_task`; MCPs root at the worktree, not project_root). If the worktree
    cannot be created (e.g. unborn HEAD on a repo with no commits), the run aborts
    unless `--no-isolation` was passed explicitly — isolation is never degraded
    silently (ADR 025).
  → engine/loop.py:build_protocol_graph(...)        → LangGraph StateGraph
  → engine/closure.py:run_to_closure(graph, task)   → recursive over spawned subtasks
        per invocation: context → governance (environment preparation) → pre_validate → execute
                        → post_validate → (loop | complete | escalate | blocked)
  → _report_closure(...)  → closure tree + structured halt payload (single emission site)
  → teardown: remove worktree, close checkpointer, cloud sync (fire-and-forget)
```

Validation itself is **one implementation with two callers**: `validators/runner.py`
is used both by the engine's validation nodes (`engine/nodes/validation.py`) and by
the MCP server, so the two paths cannot drift apart.

Halt outcomes are canonical across both paths — `escalate`, `blocker`,
`validator_error`, `internal_error` — with `halt_type == final_decision`. Only
`escalate` is resolvable by a human decision; a `blocker` is resolved by changing
the code or the spec (INV3), and `validator_error` / `internal_error` are
operational faults, not authorisation problems.

## Mode model + infrastructure boundary

Each mode declares a set of **logical tools** (edit, approve, pr, etc.) that map to **concrete MCP operations**. Approval-conferring tools (`approve`, `merge` by default, extendable via `Protocol.exclusive_tools`) must appear in at most one mode — WF1 verifies this at load time (`compiler/verifier.py:check_wf1()`), which is what makes self-approval impossible. Non-exclusive tools may be shared across modes; the active mode of every operation is recorded in the audit log (`mcp/server.py:_active_mode()`) so attribution no longer depends on tool uniqueness.

Two MCP servers can be served from one protocol:
```bash
snodo serve --mode producer  # edit, dispatch, test, validate
snodo serve --mode reviewer  # review, approve, merge, pr
```

The orchestrator connects to both servers, routing operations through the appropriate mode. Each server's tool set is the logical tools' concrete MCP operations, with read-only operations requiring no token and mutations requiring a valid JWT.

**Deployment caveat (see ADR 015):** on the MCP path snodo is one tool provider among
several. INV2 holds only if the host agent is restricted to snodo's tools; a host with
its own file-write or shell tools can bypass the boundary.

## Validator quorum → token issuance → gated mutations

This is the core enforcement chain:

1. Validators evaluate the task spec and emit `pass` / `warn` / `blocker`
2. `PolicyEvaluator` combines results per the disagreement policy (`engine/policy.py`)
3. If the policy permits and no blockers exist, `TokenIssuer.issue_token()` mints a JWT (`infrastructure/tokens.py`)
4. The MCP server's `_enforce_wf1()` checks the token before every mutation (`mcp/server.py`)
5. Without a token — no writes, no commits, no merges

The chain is structural: you cannot bypass validation by skipping a step. You need:
- A satisfied validator quorum → a token → the ability to mutate
- None of these can be forged (JWT signed, verifiable) or skipped (WF1 enforced at the boundary)

> **TODO (after the token single-use work lands):** document the token lifecycle —
> issue → verify (per mutating call) → consume (once, at the dispatch boundary) —
> and the shared consumed-token store. Deliberately left until the semantics settle.

## Audit log (INV4)

Every event — governance checks, validations, dispatches, completions, halts — is recorded in a hash-chained append-only log (`infrastructure/audit.py`). Each event has:
- `sequence`: monotonically increasing
- `previous_hash`: SHA-256 of the prior event
- `event_hash`: SHA-256 of this event's full payload

The chain is verifiable: `verify_chain()` recomputes every hash against the stored chain and returns false if tampered. The log is thread-safe (single lock wraps append + disk write).

Loading is fail-loud, consistent with the append path: `_load_existing_log`
raises `AuditError` (naming the offending line and log path) on a malformed
line, hash mismatch, or sequence discontinuity rather than silently returning a
partial list. A failed load leaves the object unusable for appends
(`append_event` refuses to write onto an unverified chain), and `verify_chain()`
also re-reads the file to confirm the on-disk log agrees with the in-memory
chain — so a forked or truncated chain is never certified.

The audit log is the **record**, not the gate: it proves what happened and that the
record was not altered, but enforcement decisions are never derived from scanning it.

## Session checkpoint (INV5)

Session state is persisted per (mode, project) as JSON files under `~/.snodo/sessions/`. Each session carries:
- `session_id`: timestamped unique identifier
- `mode`, `project_root`, `project_id`: scoping triple
- `checkpoint`: current task reference, pending decisions, memory summary, last-updated timestamp

On restart, `get_active_session()` finds the matching session by mode + project hash. Resolution decisions (`proceed` or `halt` for escalated tasks) are stored in `checkpoint.decisions` and consumed on the next governance pass. Validation tokens are deliberately **excluded** from session state — context may have shifted during the pause, so revalidation on resume is required for soundness.

Session writes are atomic (`_save_session` serialises to a same-directory `.tmp`
file then `os.replace`s onto the target), so a crash mid-write leaves the previous
session intact. Corrupt session files are surfaced rather than skipped: enumeration
warns and audits (`session_corrupt`), and a corrupt *active* session raises
`SessionError` instead of silently adopting a different session.

## Adapter pattern

Coders implement a single interface:
```python
class Coder(ABC):
    def implement(self, spec: TaskSpec) -> CodeArtifact:
        ...
```

Shipped adapters (`engine/coders/`):
- **LiteLLMAdapter** (`litellm.py`): routes to ~100+ LLM providers via litellm
- **OpenCodeAdapter** (`opencode_cli_adapter.py`): drives the host `opencode` CLI as the coder
- **MockAdapter** (`mock.py`): deterministic stub for testing

The opencode backends (`opencode` containerised, `opencode-cli` host) are
**experimental**: the conformance suite and the `.snodo/` guard + commit
(ADR 027/030) hold for them, but they do not yet report per-turn progress or
contribute usage/cost records, and no shipped template uses them. See
`docs/protocol.md` and `docs/architecture/coder-adapter-contract.md`.

Adapters that write to the working tree **in place** (opencode and similar;
`skip_workspace_write = True`) inherit `InPlaceCoderAdapter`
(`coders/base.py`). They bypass `WorkspaceMCP`, so the `.snodo/` boundary
cannot be enforced at the tool surface: the base class snapshots `.snodo/`
around the coder call and raises `SnodoMutationError` if the coder mutated it,
which the engine surfaces as a terminal `blocker` halt with a
`snodo_mutation_blocked` audit event (ADR 027). The base class also owns the
**commit**: after the coder runs it stages and commits the working tree with
an explicit identity, so `HEAD` moves and post-execute validators that review
`git diff HEAD~1..HEAD` ("## Code Change") see exactly the change the adapter
returned as a `CodeArtifact` — the two channels cannot diverge (ADR 030).
In-process adapters (litellm, mock) can only write through `WorkspaceMCP`,
which refuses `.snodo/` writes (ADR 026); the executor commits their changes
(`_commit_artifacts`), moving `HEAD` the same way.

Code-host providers follow the same pattern (`providers/registry.py:detect_provider()` → GitHub or local).

## Kleene closure

Subtasks spawn recursively: a completed task can dispatch sub-work. Each subtask runs the full governance loop independently. The engine bounds recursion depth (`max_subtask_depth`, default 3) and iteration count (50 max per task, configurable) to prevent runaway loops.

The closure driver (`engine/closure.py`) requires **positive evidence of completion**
to report `resolved`. A graph exception, an empty result, or a state carrying no
completion signal yields `internal_error` — absence of a failure signal is never
treated as success.

Two bounds terminate recursion: `max_total_fix_attempts` (global budget across the
whole tree) and `max_recovery_depth` (per-branch depth cap). A per-branch depth
violation records the exhausted child and moves on to the next sibling — it does not
cancel unrelated sibling work nor consume the global budget; only genuine global
exhaustion stops processing. A parent whose closure is incomplete (any sibling
depth-exhausted or otherwise non-resolved) is itself reported non-resolved.

## Invariant → mechanism table

| Invariant | Mechanism | Source |
|-----------|-----------|--------|
| WF1 — Mode separation | Exclusive approval-conferring tools in at most one mode, load-time verification | `foundation/compiler/verifier.py:check_wf1()` |
| WF2 — Role uniqueness | Duplicate detection, load-time verification | `verifier.py:check_wf2()` |
| WF3 — Validator coverage | Missing validator detection; initial mode existence; dispatch requires pre_execute | `verifier.py:check_wf3()` |
| WF4 — Policy completeness | Policy-to-validator-count matching | `verifier.py:check_wf4()` |
| WF5 — Constraint consistency | Unique IDs; registered predicate verification | `verifier.py:check_wf5()` |
| INV1 — Token integrity | JWT HS256, expiry, task binding | `foundation/infrastructure/tokens.py` |
| INV2 — Capability boundary | Mode-filtered tool exposure at the MCP boundary; path validation confines writes to the project root and excludes `.snodo/` from tool mutation (ADR 026) | `mcp/server.py`, `tools/workspace.py:validate_path()`, `tools/git.py` |
| INV3 — Non-overridable block | `blocker_count > 0 → HALT` before policy logic | `engine/policy.py` |
| INV4 — Audit immutability | Hash-chained append-only log | `foundation/infrastructure/audit.py` |
| INV5 — Session resumability | File-backed checkpoint per (mode, project) | `foundation/infrastructure/session.py` |

## Threat model

snodo assumes the repository it is initialised in is **trusted** — `snodo init` is the
consent boundary, and repository contents (test scripts, build files, conftest) are
executed as the user. Running snodo against untrusted third-party code is out of
scope. The *agent*, by contrast, is treated as semi-untrusted: prompt injection can
steer its tool calls, so tool-input validation remains in scope. See
[ADR 014](decisions/014-trusted-repository-threat-model.md).
