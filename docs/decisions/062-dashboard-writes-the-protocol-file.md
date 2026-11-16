# ADR 062 — The dashboard writes the protocol file

## Status

Accepted. Fixes #1276.

## Context

The local dashboard has been read-only. Protocol editing is the first project
write path, and a browser can make requests to local loopback servers on a
user's behalf. This path therefore needs explicit browser-request protections
and must remain tightly scoped.

## Decision

Only the Protocol view's preview and save routes accept POST. Requests remain
restricted to loopback Host values; a supplied Origin must match the Host and
writes require JSON. The read payload provides a per-process random page token,
which saves compare in constant time. Preview applies text-level edits and
validates without writing. Save repeats that work, verifies the on-disk etag,
and atomically replaces only the known project's `.snodo/protocol.yml` when
valid. Stale and invalid edits do not write. The dashboard never commits,
pushes, or runs git.

## Consequences

The dashboard can persist intentional protocol edits while unrelated routes
remain read-only. External file changes are detected instead of overwritten,
and users retain responsibility for version-control operations.
