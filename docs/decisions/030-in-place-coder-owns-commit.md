# ADR 030 — In-place coder adapters own the commit, so the review channel is the artifact channel

## Status

Accepted

## Context

Post-execute validators judge the produced change. There are two independent
readers of "what did the coder change":

- **Channel A** — the returned `CodeArtifact`, populated by every adapter and
  working uncommitted. Its file paths reach the validator context as
  `artifacts` (the "## Produced Artifacts" block).
- **Channel B** — `git diff HEAD~1..HEAD`, prepended to the judge prompt as
  "## Code Change" (`llm_validator.py`, `acceptance.py`) and read via the
  `read_diff_between_refs` tool.

Channel B only reflects reality if `HEAD` moved. For in-process adapters
(litellm, mock) the executor commits (`_commit_artifacts`), so `HEAD~1..HEAD`
is the change. `OpenCodeCLIAdapter` committed its own working tree.
`OpenCodeAdapter` — the Docker/HTTP path — never committed: it wrote to the
volume-mounted workspace in place and read back via git, but left `HEAD`
where it was, so `HEAD~1..HEAD` resolved to the **previous** commit and
validators confidently reviewed the wrong change and passed.

The seam is implicit — an ABC, two opt-out booleans (`skip_workspace_write`,
`skip_engine_commit`), and `hasattr` duck typing. An adapter that lacks the
"commit what I wrote" capability is indistinguishable from one where the
capability does not exist, which is exactly why this drifted: each adapter
"knew" it was supposed to commit and none of the ones that wrote in place was
structurally required to.

## Decision

The commit is a structural property of in-place coders, owned by
`InPlaceCoderAdapter` — the same base class that already owns the `.snodo/`
guard (ADR 027) and for the same reason: enforcement in the base class holds
automatically for every in-place adapter and cannot be forgotten by a future
one.

1. **`InPlaceCoderAdapter` owns the commit.** After `_implement_in_place`
   returns and the `.snodo/` guard passes, `implement()` stages the working
   tree (excluding `.snodo/`, virtualenvs, caches, and build junk) and
   commits with an explicit identity passed via environment (`GIT_AUTHOR_*` /
   `GIT_COMMITTER_*`), so a detached checkout with no configured git user
   still commits. The commit is skipped when nothing is staged. Non-fatal on
   failure — the working tree still holds the change, but the post-execute
   diff would be empty.

2. **The commit happens after the `.snodo/` guard.** A `.snodo/` mutation
   raises `SnodoMutationError` before anything is committed; the tree is left
   for operator inspection exactly as ADR 027 requires.

3. **`OpenCodeCLIAdapter`'s per-adapter commit is folded into the base**
   class, and the duplicated git readback (`_read_changes_from_disk`) is
   removed from both opencode adapters — one implementation, inherited.

4. **A conformance test parameterised over every registered adapter**
   (`tests/coders/test_adapter_conformance.py`) asserts that an adapter's
   change is observable (non-empty `CodeArtifact`), attributable (the reported
   paths exist on disk after the engine's write+commit step), and reviewable
   through the same channel validators read — `git diff HEAD~1..HEAD` is
   non-empty and covers every artifact path. An adapter that fails to leave
   its change reviewable fails at the branch.

## Why this over "commit in OpenCodeAdapter" (the narrow fix)

The narrow fix would add `_commit_changes` to `OpenCodeAdapter`, mirroring
the copy already in `OpenCodeCLIAdapter`. It fixes the reported case but
leaves the guarantee per-adapter: the next in-place adapter forgets the copy,
`HEAD` stops moving again, and the divergence returns. Owning the commit in
`InPlaceCoderAdapter` is the mechanism that made the `.snodo/` detection hold
automatically — the root-cause fix for an implicit seam is to make the
capability structural, then pin it with a conformance test.

## Consequences

- After `implement()`, every adapter's change is committed and
  `HEAD~1..HEAD` (the channel post-execute validators review) is exactly the
  change the adapter returned as a `CodeArtifact` — the two channels cannot
  diverge.
- The container `opencode` path is fixed without adding adapter-specific
  code: it inherits the commit from the base class.
- In-process adapters are unchanged: the executor still writes and commits
  their artifacts.
- A `.snodo/` mutation still halts as a blocker before any commit.
- The conformance test is a canary for the seam: a registered adapter with no
  conformance driver, or one that stops leaving its change committed, fails
  `tests/` immediately.

## Alternatives considered

- **Commit in `OpenCodeAdapter` only (narrow fix):** rejected — leaves the
  seam per-adapter; the next in-place adapter reintroduces the hole.
- **Collapse channel B entirely onto the artifact (feed validators the
  `CodeArtifact` diff, not a git diff):** rejected — the validator tool
  surface (`read_diff_between_refs`, `git_show`) is fundamentally git-based
  and reads committed history; making the git channel correct by committing
  achieves the same convergence without redefining the read-tool contract.
- **Review the uncommitted working tree instead of `HEAD~1..HEAD`:**
  rejected — mixed with the executor's commit for in-process adapters, the
  worktree diff is empty after a commit; a single committed representation is
  the only uniform choice.
