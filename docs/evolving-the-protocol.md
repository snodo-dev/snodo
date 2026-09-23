<!-- snodo-guide topic="evolving-the-protocol" aliases="intake,protocol-fit,project-intake" summary="Start a project, read protocol-fit signals, and evolve governance deliberately" section="# Starting a project and growing its protocol" -->
# Starting a project and growing its protocol

Snodo governs work through `.snodo/protocol.yml`: modes, validators, criteria,
and verification tooling define what the engine will enforce. A project may
need a few iterations to get those declarations aligned with its repository.
Treat that early calibration as intake. Once the basics are stable, repeated
friction is evidence to inspect the protocol itself rather than simply pushing
the next task harder.

## Intake: establish a trustworthy first loop

### 1. Initialize the project

Run `snodo init` in a Git repository (or select a template explicitly, for
example `snodo init --template team`). Init creates `.snodo/protocol.yml` from
the selected template. It can detect a test command from common root markers,
accept one via `--test-command`, or leave the template's safe no-op in place
when no runner is known. Review the generated protocol as executable
configuration: commands declared there are run by Snodo. See
[`protocol.md`](protocol.md) and the
[trusted-repository decision, ADR 014](decisions/014-trusted-repository-threat-model.md).

For a repository with committed code, run `snodo ready` before spending on
tasks. Readiness deterministically checks the artefacts demanded by the
protocol, including whether the protocol and required decision records are
committed and whether configured paths and tooling resolve. It distinguishes
repository findings from workstation findings; a file merely present in the
working tree may not be visible in a task worktree. Fix the inexpensive missing
prerequisites and commit them, then run readiness again. Readiness is a
diagnostic, not a claim that every task will pass.

For decision records that state enforceable rules, `snodo intake` can propose
criteria, each attributed to its source record. The CLI presents proposals for
individual acceptance or rejection; only accepted criteria are written, and
the protocol is checked before it is saved. In MCP, `intake` is deliberately
read-only and returns proposals with `written: false`; the human must make and
apply the protocol change. Intake reads rules from ADR **Decision** sections,
not background or consequences. See [ADR 044](decisions/044-intake-proposes-criteria.md).

### 2. Run small first plans and learn from calibration

Start with a small, representative task or a short authored plan. Plans are
authored and validated before execution; they do not automatically tune the
protocol. Keep the task specification concrete, run the declared verification
gate, and inspect the result. During intake it is normal to discover that a
validator's instructions need sharper criteria, or that the post-execute test
command needs to be made real and stable. For a new project, the shipped no-op
test command is recorded as `no_tests`, not as a test pass. Replace it with the
project's actual command when available. See [ADR 031](decisions/031-first-class-verification-audit-events.md).

Early failures do not by themselves mean the implementation task was defective.
Separate a valid finding about the work from a gate that is not yet configured
to judge the work. Confirm the evidence, refine the validator criteria or test
command with the operator, and repeat a small task to check that the revised
protocol now gives a useful judgement. This is calibration, not a reason to
add a new halt, status, or severity.

## Read signals that the protocol needs to change

When a task halts, first use the existing outcome vocabulary and evidence. A
`blocker` is a validator finding about the task or its work; inspect the
validator result and source evidence. An `escalate` means no blocker was
emitted, but the policy threshold was not met and a human decision is required.
`validator_error` means a validator could not produce a verdict; it is not a
negative judgement on the task. `environment_error` points to an execution
environment or coder-invocation problem, and `internal_error` is an engine
fault. These canonical outcomes are distinct from raw engine halt types and
from plan/job statuses. See [outcomes](outcomes.md) and
[ADR 015](decisions/015-mcp-validation-four-outcome-contract.md).

Then look for repetition across tasks in `.snodo/audit.log` and the task
records. The `halt` audit event carries the task reference, canonical
`halt_type`, `raw_halt_type`, blocker validator IDs, and reason. The audit trail
records validator activity and policy decisions. The
`verification_executed` event records the exact command, commit, return code,
outcome, validator, task reference, working directory, and output tail. These
records let an operator distinguish a bad change from a broken or mis-scoped
gate; they are evidence, not an automatic protocol-edit mechanism. See
[ADR 031](decisions/031-first-class-verification-audit-events.md) and
[ADR 038](decisions/038-orchestrator-role-and-audit-contract.md).

Signals that the project may have outgrown its protocol include:

- The same `blocker` recurs on unrelated task specs because a validator is
  asked to judge a property that is not present in the spec, diff, or evidence
  it can inspect. Improve the criterion or its evidence path; do not keep
  rewriting unrelated task descriptions to appease an invisible requirement.
- `verification_executed` repeatedly shows the root test command running an
  unnecessarily broad suite for tiny changes, or repeatedly failing because
  the declared command is stale. Confirm the desired gate and narrow or correct
  the declared tooling.
- The same validator or operational halt recurs across otherwise different
  tasks. Check whether the verdict is a genuine repeated defect, an unreliable
  validator/provider, or a project-wide configuration issue before retrying.
  A recurring `validator_error` calls for diagnosing the validator or its
  provider, not changing a task's acceptance criteria.
- A monorepo task is repeatedly judged against root-level criteria, ADRs, or
  tests that belong to another package. The repository's single protocol
  scope may be too coarse; inspect module boundaries and module-specific
  tooling.

A single failure is not enough to diagnose protocol drift. Compare the halt,
verdicts, task specification, and verification record. Keep a valid task
finding as a task to fix; change the protocol when the recurring mismatch is
the rule, evidence, scope, or gate itself.

## Survey code boundaries and protocol drift

Use the read-only `survey` MCP tool (or `snodo survey --json`) to compare what
the repository contains with what the protocol declares. The default survey
is deterministic and reports discovered boundaries, confirmed test commands,
decision-record locations, and repository tooling. If useful and configured,
`agent: true` permits an agent to judge ambiguous boundaries; survey remains
read-only. In a governed repository, it also reports protocol/code divergences
and what it could not compare. `survey` is a project diagnostic exposed on the
MCP surface regardless of the active mode's write capability.

Declare modules when the repository has independently verifiable scopes—for
example, `services/api` and `apps/web` with separate test commands and local
decision records—or when one root test command and one root ADR set no longer
describe task-level work honestly. A monorepo does not need every package to be
ready at once: module scope lets intake and readiness focus on the unit adopting
Snodo. Survey's findings are proposals grounded in the code, not automatic
protocol edits. Review the paths, tooling, and decision locations, then have
the operator declare the agreed modules in `protocol.yml`.

Modules are optional scopes, not modes. They can declare owned paths,
`decisions_path`, and tooling such as `test_command`; they do not get their own
protocol, audit log, session, or merge authority. See
[ADR 041 — A module is a scope, not a mode](decisions/041-modules-scope-tooling-not-modes.md).

## Who changes the protocol

The orchestrator frames tasks, observes signals, and proposes a protocol
change with evidence. A human decides whether the governance rule should
change and edits `.snodo/protocol.yml`. The orchestrator does not silently
write or loosen the rules used to judge its own work: Snodo protects
`.snodo/` from agent tool-surface mutation (ADR 026,
[Protocol and Governance State Protected from Agent Mutation](decisions/026-protocol-protected-from-agent-mutation.md)).
After a human-approved edit, validate the protocol, run `snodo ready`, and
check the next representative task against the new gate. Keep the relevant
protocol edit and its rationale reviewable in the project's normal change
history.

The boundary is intentional: the orchestrator proposes; a human decides and
edits. It is the operator's protocol, not a task-level escape hatch.
