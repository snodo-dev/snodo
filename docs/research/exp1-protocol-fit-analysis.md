# EXP1 follow-up — Protocol fit: can a protocol help bug fixes, and where does it belong?

Companion to `exp1-findings.md`. EXP1 showed the `intent` protocol (spec-authoring
scaffolding) underperformed prose on single-shot SWE-bench bug fixes via
over-elaboration. Two questions before declaring anything conclusive: (a) could a
*different* protocol do better on bug fixes, and (b) which task types is protocol
governance actually built for?

## (a) Is `intent` the best we can do on bug fixes?

Reviewed all four templates (`intent`, `solo`, `2+n`, `team`). They all bias in the
**same** direction that cost arm-c — toward abstraction, structure, tests, and
planning — so the heavier ones would do *worse* on bug fixes, not better:

- **`meta-spec`** (solo / 2+n / team): explicitly *rejects* code-prescriptive specs
  and forces "intent + constraints," i.e. more paraphrase and abstraction — the exact
  over-elaboration that lowered resolve rate.
- **`tests_exist`** (2+n, blocker): forces adding test files → larger diffs, and it's
  wasted on SWE-bench, where the harness overwrites tests with the gold `test_patch`.
- **`architecture` / SOLID / `conventions`**: create refactor and structure pressure
  → broader changes than a minimal fix needs.
- **`planner` mode** (team): decomposition into waves with `value_increment` /
  `completeness` gates — heavy overhead for a one-line fix.

`intent` is the **lightest** protocol (only `spec-manners` + the now-removed
`review`), and it's the one we ran — and it still lost to prose. So within the
current library, `intent` is already the best bug-fix fit, and there is **no
minimality-oriented protocol** to fall back on.

**Not yet conclusive.** The failure mode (small patches win; median resolved patch
~750–1000 chars, failed ~2050–2430) points to a protocol that could plausibly help
and doesn't exist yet — a **"hotfix / surgeon" protocol** that enforces *constraint*
instead of elaboration:

- no spec-authoring — pass the raw problem + failing test straight to the coder;
- hard scope constraint — touch only files implicated by the failing test;
- no new tests, no refactor;
- post-execute validator that **blocks a sprawling or out-of-scope diff** and sends
  K-recovery back for a *tighter* fix.

That inverts snodo's current bias (expand/abstract) toward (constrain/localize).
**EXP1b should add it as a 4th arm.** If it beats prose → a protocol *can* help bug
fixes, we just lacked the right one. If it also fails → then it's conclusive: on
single-shot bug fixes, protocol governance is net overhead and prose ≥ enforcement.

## (b) Where protocol governance should fit

Enforcement's value is that instructions can be ignored and enforcement cannot. That
is invisible on a pure capability task (bug fix) and becomes load-bearing when a task
has structure requirements, length/recovery needs, or boundaries to protect. Mapping
protocol features to task types:

1. **Feature implementation** (multi-file; tests + design are part of the
   deliverable). `meta-spec`, `tests_exist`, `architecture` become assets rather than
   taxes. Metric: resolve **plus** test coverage / maintainability.
2. **Long-horizon agentic tasks** (repo-scale, multi-step; e.g. SWE-Lancer-style).
   The K-recovery loop and planner waves are the point. Metric: completion + recovery
   success rate, not one-shot resolve.
3. **Refactoring with invariant preservation.** Consistency / architecture validators
   are exactly the job. Metric: behavior preserved (tests green) + structural gain.
4. **Safety / compliance-gated tasks — the strongest test of the thesis.** Seed tasks
   with a temptation to cross a boundary (leak a secret, edit out-of-scope, drop an
   auth check, exfiltrate). Enforcement's non-overridable blockers (INV2/INV3) should
   beat prose **by construction**, because prose guidance can be ignored and a blocker
   cannot. Metric: **violation rate**, not resolve rate. A capability benchmark
   structurally cannot surface this — which is why bug-fix resolve rate was the wrong
   yardstick for snodo's actual value.
5. **Multi-agent / separation-of-duties** (2+N producer↔reviewer). Metric: issues
   caught at review, separation guarantees honored.

## Bottom line

- The bug-fix conclusion is **90% closed**: every existing protocol hurts, and the
  lightest one lost to prose. To fully close it, run EXP1b with a minimality
  ("hotfix") protocol as a 4th arm. If minimality-enforcement also fails to beat
  prose, "protocol governance is net overhead on single-shot bug fixes" is settled.
- The thesis "enforcement > instruction" is mis-tested by a bug-fix benchmark. It
  should be evaluated on **safety/compliance-gated** and **long-horizon** tasks with
  **violation / completion** metrics — where enforcement is load-bearing. That, not a
  bigger SWE-bench run, is EXP2's real target.
