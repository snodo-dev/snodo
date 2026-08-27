# ADR 036 — Operator human review tracking via audit log events

## Status

Accepted

## Context

Snodo records validator decisions (`validator_result`), task executions, and verification evidence (`verification_executed` per ADR 031) in `.snodo/audit.log`. While this attests to process compliance, it does not capture post-execution human review outcomes — whether a completed task was accepted unchanged, required manual operator edits, or was discarded entirely.

The fraction of completed tasks accepted unchanged over a time window is essential empirical evidence for validating claims about unattended operation.

## Decision

1. **Verdicts belong in `.snodo/audit.log`.** Human review arrives after task execution and branch merging have concluded. Appending a `human_review_recorded` event to `.snodo/audit.log` fits Snodo's append-only audit model, preserving hash-chain integrity (`previous_hash` linkages) without mutating past session checkpoints or introducing a separate database store.

2. **A 3-category review taxonomy:**
   - `accepted`: The task output was accepted unchanged.
   - `amended`: The task output landed but required manual operator modifications/fixes.
   - `discarded`: The task output was rejected, reverted, or rewritten from scratch.

3. **CLI Recording & Reporting Interface:**
   - `snodo task review <task_id> <verdict> [--notes NOTES]` appends a `human_review_recorded` event.
   - `snodo task review --report` (or `snodo task report [--days N] [--json]`) calculates the fraction of completed tasks accepted unchanged over a rolling window.

## Consequences

- Operators can record task review outcomes in seconds without leaving the CLI.
- Unattended capability claims can be evaluated empirically using `snodo task report`.
- Machine-readable JSON output (`snodo.task_review_report.v1`) supports automated dashboard tracking.
