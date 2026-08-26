# ADR 031 — First-class verification events in the audit trail

## Status
Accepted

## Context
Previously, the audit log recorded which validators were invoked and what policy decisions were rendered, but it did not record empirical evidence that project verification commands (e.g. `pytest`, `npm test`, `cargo test`) actually ran, against which commit, or with what exit code. An auditor inspecting `.snodo/audit.log` could not verify whether verification commands ran or were skipped.

Furthermore, self-reported summaries could theoretically be accepted without proof of execution, creating an unverified merge vulnerability.

## Decision
1. **First-Class `verification_executed` Audit Event**:
   - Verification command execution is recorded as a first-class audited event (`verification_executed`) in the audit log.
   - Payload includes:
     - `command`: exact shell command executed (e.g., `pytest`, `npm test`)
     - `commit`: HEAD git commit hash of the working tree at the time of execution
     - `returncode`: process exit code
     - `outcome`: `"pass"`, `"fail"`, or `"error"`
     - `validator_id`: identifier of the validator (e.g. `quality`)
     - `task_ref`, `session_id`, `working_directory`, and output tail.

2. **Governance Principle — Unverified Merges are Impossible**:
   - An unverified merge cannot be represented in the trail and is impossible.
   - `_merge_on_success` checks the audit log prior to merging a task branch. If no passing `verification_executed` event is present for the task in the audit log, the merge is refused (`unverified_merge_blocked`), and the branch is left unmerged for human review.
