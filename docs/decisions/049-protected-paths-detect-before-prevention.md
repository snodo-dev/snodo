# ADR 049 — Declared protected paths are detected from the task branch diff

## Status

Accepted.

## Context

Some repository paths are decisions rather than implementation. A protocol
needs to say that a task must not change them, and the engine must use evidence
independent of the coder's account of its work. The task branch's diff is that
evidence: it is available after execution and can stop a change before it is
merged.

## Decision

`Protocol.protected_paths` is an optional list of repository-relative paths.
After execution, the engine compares the task branch with the captured base
ref. A changed declared path, including a descendant of a declared directory,
adds an existing `blocker` validator result naming the path. An absent declared
path is valid and has no effect. An undeclared list is exactly the current
behavior. The `.snodo/` mutation guard remains separate and unchanged.

This ticket deliberately does not prevent writes. Future enforcement levels
are: hidden, where the task worktree does not contain the path; and read-only,
where a container adapter mounts it read-only. A same-user host subprocess
cannot receive a real read-only boundary: Linux bind mounts require privileges
or a namespace the child can undo, and macOS has no bind mounts. Snodo will fail
open on a platform or adapter that cannot enforce a requested boundary, and
must say so at run start rather than imply protection that is absent.

## Consequences

Detection is uniform across adapters, operating systems, privileges, and
mounts, but it reports after the coder has run. The branch remains unmerged
when the blocker is raised.

## Alternatives

Pre-execution rejection was rejected because it judges what might happen rather
than what did happen. The coder's file report was rejected because the diff is
the authoritative evidence.
