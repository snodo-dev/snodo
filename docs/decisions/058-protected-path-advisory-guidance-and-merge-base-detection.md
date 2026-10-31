# ADR 058 — Protected-path guidance is advisory; detection uses the task's merge base

## Status

Accepted. Succeeds [ADR 049](049-protected-paths-detect-before-prevention.md) on the comparison basis and coder guidance.

## Context

ADR 049 established post-execution diff detection for `Protocol.protected_paths` and deliberately rejected write prevention. CLI coders can now be told before execution which paths are declared protected. That advance notice is a request to the coder, not a new enforcement level or a boundary. Detection must also distinguish task changes from changes brought into the task branch from its base branch, including after a rebase.

## Decision

Keep `protected_paths` as repository-relative declarations. Surface them to CLI coders before they run as advisory instructions. The coder is not sandboxed and writes are not blocked; the post-execution guard remains the enforcement that can reject a task.

After execution, compare the task branch's changes from the common ancestor (merge base) of the task branch and the base branch. A declared path changed by the task, including a descendant of a declared directory, produces the existing blocking validator result. Changes already present on the base branch are not attributed to the task merely because a rebase moved the base ref. Thus the diff is evidence of changes introduced on the task branch since divergence, not a claim that writes were prevented or a record of every intermediate write.

## Consequences

Operators and coders get advance notice, while the post-execution diff remains the actual rejection mechanism. Using the common ancestor avoids blaming task work for changes delivered from the base branch. As in ADR 049, detection occurs after the coder has run; it does not establish a write boundary.

## Alternatives

Treating prompt guidance as enforcement was rejected: instructions do not prevent writes. Comparing against a base ref that may have advanced or been moved by a rebase was rejected because it can attribute base-branch changes to the task.

## Clarification — 2026-10-30

For host CLI coders, the executor also checks the operator's checkout against a
pre-dispatch baseline after each coder exit and blocks acceptance when content
changed, listing the paths; `.snodo/` runtime state is excluded while plan
definition files and `protocol.yml` are watched. This is after-the-fact
detection, not write prevention or repair: arbitrary host paths outside the
repository are invisible, and concurrent operator edits are indistinguishable
from coder edits. It does not apply to in-place work or in-process coders.
Prevention remains out of scope here; it requires the contained adapter in
[ADR 051](051-coder-containment-is-declared-not-deployed.md).
