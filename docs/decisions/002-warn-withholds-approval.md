---
adr: 002
status: Accepted
---

## 002: Warn withholds approval in policy thresholds

- **Status**: Accepted
- **Context**: The original `PolicyEvaluator` treated `warn` as approval — all four policies thresholded on `pass_or_warn = pass + warn`. This meant policies collapsed to identical behaviour: on good tasks, `pass_or_warn` always equalled `total_count` (everything counted), so all policies PROCEED'd identically. The policy taxonomy was non-functional. The paper's design intent was that policies parametrise strictness: a front-end protocol should tolerate disagreement (majority/quorum), a core-system protocol should demand unanimity. Warn = approval eliminated that distinction.
- **Decision**: Threshold on `pass_count` only. Warn withholds approval (`policy.py:149,191,233,274`). `PROCEED_WITH_LOG` fires when the threshold is met AND `warn_count >= 1`. The four policies now produce distinct behaviour: unanimous strictest (needs all pass), any most permissive (needs ≥1 pass), majority/quorum in between. Blockers still override unconditionally (INV3).
- **Consequences**: The policy taxonomy is now functional. For N=3 validators with 2 pass + 1 warn: unanimous → ESCALATE, majority → PROCEED_WITH_LOG, quorum → ESCALATE (2 < 2.01), any → PROCEED_WITH_LOG. At N=3, quorum (≥ 3×0.67 = 2.01 → needs 3 passes) is functionally identical to unanimous — divergence would appear at N≥5. Existing tests that assumed warn-as-approval were updated (test_policy.py, test_loop.py, CLI tests). The 8.2 study was re-run and now shows separated curves.
- **Alternatives considered**: Keep warn-as-approval — rejected because the policy taxonomy was dead. Invent a fourth severity — rejected because the engine only accepts pass/warn/blocker.
- **Evidence**: Audit log entry 73 (2025-06-01, policy-warn-semantics-fix), commit `75136842`; `policy.py:149-277`.

---
