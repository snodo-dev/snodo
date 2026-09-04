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
     - `outcome`: `"pass"`, `"fail"`, `"error"`, or `"no_tests"`
     - `validator_id`: identifier of the validator (e.g. `quality`)
     - `task_ref`, `session_id`, `working_directory`, and output tail.

2. **`no_tests` outcome — an honest ungated record**:
   - Every template ships a default test command that runs on a POSIX shell and
     exits zero, so a project with no `tooling.test_command` and no detectable
     marker file can always run its first task. When the quality validator runs
     that configured no-op it records `outcome: "no_tests"` and states that no
     tests were executed. It never writes `outcome: "pass"` for work that did
     not run: a false "Tests passed" in a tamper-evident log — read by the merge
     gate and synced to the cloud — would be worse than the halt it replaces.
   - Auto-detection and `snodo init --test-command` take precedence over the
     default; `no_tests` is recorded only when the validator ran the shipped
     no-op and nothing else was configured or detected.
   - Genuine outcomes are unchanged: a real suite that exits zero records
     `"pass"`, a non-zero exit records `"fail"` (blocker), and 126/127/timeout
     record `"error"` (operational fault).

3. **Governance Principle — Unverified Merges are Impossible**:
   - An unverified merge cannot be represented in the trail and is impossible.
   - `_merge_on_success` checks the audit log prior to merging a task branch. If no passing `verification_executed` event is present for the task in the audit log, the merge is refused (`unverified_merge_blocked`), and the branch is left unmerged for human review.
   - A matching `verification_executed` event with `outcome: "no_tests"` is not a
     pass, but it is an explicit, audited statement that the task ran ungated by
     the operator's configuration; such a task may merge (a fresh project must
     not strand its first task) while the merge line and the record both say that
     no tests were executed. A `"fail"` or `"error"` record never satisfies the
     gate, and neither does the absence of any record.
