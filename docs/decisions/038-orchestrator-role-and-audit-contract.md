# ADR 038 — The Orchestrator Role & Audit Trail Contract

## Status

Accepted

## Context

Snodo bounds a coder and escalates to an operator. The halt taxonomy, escalation resolution (`escalate → resolve → resume`), decision record signatures (ADR 016), and `snodo authorize` are all addressed to an entity outside the inner execution loop (`engine/loop.py`).

Historically, that role was assumed to be a human operator sitting at a terminal. Increasingly, this role is filled by autonomous orchestrator agents (or external workflow engines) that break down goals, draft task specifications, and resolve validator escalations automatically.

While Snodo logs every coder tool turn, records every validator verdict with cited criteria, and hashes every execution token in an append-only audit log (`.snodo/audit.log`), the **Orchestrator** — the component responsible for framing task specifications, defining constraints, and deciding whether work resumes or halts — has had no formal role definition and leaves no record in the audit trail beyond eventual git commit messages.

The task specification is the single highest-leverage artifact in the system: it dictates whether the coder can possibly succeed. Relying on git commit messages leaves a blind spot when attempting to answer "why was this task shaped that way?" or auditing agent-driven orchestration decisions.

## Decision

1. **Formal Definition of the Orchestrator:**
   The **Orchestrator** is the entity (human operator, autonomous agent, or external workflow controller) operating outside the inner execution loop. It is responsible for problem decomposition, task specification framing, capability binding, and out-of-band escalation resolution.

2. **Responsibilities & Boundary Constraints:**
   - **What the Orchestrator MAY do:**
     - Author and decompose task specifications (`TaskSpec`, `Task.spec`, `root_spec`).
     - Define acceptance criteria and protocol constraints for task execution.
     - Issue signed `DecisionRecord` JWTs / adjudications to resolve validator escalations (`snodo authorize`).
     - Record human/operator review verdicts (`snodo task review`).
     - Select execution modes and bound recovery depth per task.
   - **What the Orchestrator MAY NOT do:**
     - Directly mutate in-flight loop state or bypass validator gates inside `engine/loop.py`.
     - Bypass single-use validation tokens (`TokenStore`) or mutate `.snodo/` runtime state outside official interfaces.
     - Grant execution permissions without producing traceable signed attestations.

3. **Audit Trail Contract for Orchestrator Work:**
   **Task specifications and orchestrator decisions MUST be recorded in `.snodo/audit.log`.**
   Relying on git commit messages is insufficient because commit messages only record code that landed, omitting failed task framings, spec iterations during recovery (ADR 021, ADR 023), and the reasoning behind initial task shapes.

   The audit trail contract requires recording the following first-class audit events:
   - `task_spec_authored`: Records `task_id`, `root_task_ref`, full `spec` content, `constraints`, and orchestrator identity when a task is submitted or reshaped.
   - `task_decomposed`: Records parent task references, wave IDs, and subtask dependencies when an orchestrator splits a goal into subtasks.
   - `orchestrator_decision_issued`: Records signed adjudication payloads (`task_ref`, `decision`, `validator_id`, `overrides`) issued during escalation resolution.

## Consequences

- The Orchestrator role is explicitly bounded, making it clear what actions an agent acting as operator may and may not take.
- Every task specification and framing decision is immutably recorded in `.snodo/audit.log`, eliminating blind spots in post-mortem analysis and audit reports.
- Future audit inspection tools can trace a task from initial orchestrator specification through inner-loop execution, validator verdicts, and final operator review.
