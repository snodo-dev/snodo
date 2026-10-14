# What Snodo's gate caught while building Snodo

Since v0.19.0, Snodo has been built with Snodo. The audit history includes
ordinary moments when an agent said the work was ready and the test gate had
more to say.

As of **2026-10-14**, the maintainer's orchestrator counted `verification_executed`
and `validator_contradiction_detected` audit events for tasks run in their own
worktrees, excluding mock runs:

- **353 tasks** ran `make gate` in their own worktree, across **515 gate runs**.
  **102 runs did not pass.**
- In **40 tasks**, the acceptance judge concluded every criterion was met
  **61 times** while the test gate failed that same work. Snodo kept the
  failing verdict.
- **15 tasks** were caught and then fixed: the gate named the failing tests,
  Snodo sent that evidence back to the coder, and the next attempt passed.

One example is task `task_9e66dcc7cd4e` (2026-09-29). Its first gate run named
two failures in `tests/mcp/test_pr.py`:
`TestFetchPrContext::test_fetch_pr_context_full` and
`test_fetch_pr_context_no_comments`. The summary was “4 failed, 4834 passed.”
The second attempt passed the full gate.

These are numbers from one project, with one protocol and the coders used here.
They show what happened in this repository; they do not establish how often
the same pattern occurs elsewhere. For controlled experiments, see the
[paper linked in the README's Research section](https://arxiv.org/abs/2606.20615).
