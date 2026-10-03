# ADR 055 — Orchestrator pub/sub coordination by plan

## Status

Proposed (2026-10-03), not scheduled.

## Context

Orchestrator agents working on related plans across projects currently
coordinate by appending dated notes to a shared plain-text exchange file (for
example, between snodo-public and snodo-cloud during a cloud interface change).
This works, but requires a shared folder and provides neither structure nor a
delivery guarantee. The cloud repository has proposed cloud-hosted,
per-organisation streams; this record captures the client-side counterpart.

## Decision

Direction only: snodo would offer publish/subscribe for orchestrator messages
scoped to a plan id, using the cloud as shared transport and a local file as a
fallback. Messages would identify who sent them and the plan, task, job or
commit they refer to.

The following remain open for a later decision: message types; whether messages
ever enter the audit trail (the state machine and audit vocabulary are closed,
so this requires an explicit decision); retention; and authentication.

## Consequences

Related orchestrators could coordinate without a shared folder, while a local
fallback remains available. No implementation or protocol change is scheduled
by this proposal.

## Alternatives

Keep the shared plain-text exchange file as the only coordination mechanism;
it works today but retains its shared-folder, structure and delivery limits.
