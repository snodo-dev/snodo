# ADR 012 — Project identity is the git remote, or nothing

## Status

Accepted

## Context

A project needs an identity that is stable across time and, where possible,
across machines and people. Two things depend on it: the audit chain, which
hashes `project_id` into every event, and any consumer of that chain, which has
no other way to tell whether two streams of events describe the same work.

The obvious candidate — the filesystem path — is wrong. It differs per machine,
per clone and per worktree, and it embeds the operator's home directory. A
project moved or renamed would become a different project. A project on two
laptops would be two projects. Path is not identity.

The next candidate — a generated identifier written into the repository — is
also wrong, but less obviously. It works for a single checkout and fails the
moment two people work on the same code, because each clone would either invent
its own or require the identifier to be committed, which puts a machine
concern into the source tree.

What is left is the thing the repository already has and already agrees on: the
remote it pushes to. Every engineer working on a project resolves the same
remote URL, on any machine, from any directory, forever. It is the only fact
available locally that means the same thing everywhere.

Not every repository has one. A scratch directory, a repository that has not
been pushed yet, a private experiment — these are real and must work. For them
there is no shared fact to build on, and inventing one would be a lie.

## Decision

**A project's identity is its normalized git remote URL. A project with no
remote gets a locally generated identifier, and that identifier is deliberately
unreconcilable.**

Concretely:

1. **Remote scope.** A repository with a git remote takes that remote's
   normalized URL as its `project_id`, with scope `remote`. Normalization
   removes the transport, credentials and the `.git` suffix, so `ssh` and
   `https` forms of the same remote resolve identically.

2. **Local scope.** A repository with no remote takes `local:<uuid>`, with
   scope `local`. This identifier means *this checkout*. Nothing can establish
   that two local checkouts are the same project — not across machines, not
   across paths on one machine — and no mechanism to try may be added. Path
   hashing, content fingerprinting and name matching are all excluded. A local
   project is a leaf.

3. **Promotion is one-way and follows the repository.** A repository that gains
   a remote promotes to the remote identity on the next resolution. A cached
   `local:` identity is therefore re-resolved on every call; a cached `remote`
   or operator-supplied `override` identity is stable and returned as-is. There
   is no demotion — removing a remote does not return a project to `local`,
   because the identity is already known elsewhere.

4. **The local uuid is stable.** A repository that still has no remote keeps
   the identifier it was first given. Re-resolution promotes; it does not
   re-mint.

5. **Scope is derived from the identity, never stored independently.** A
   `local:` prefix means local scope, anything else means remote. Deriving it
   at the point of use removes the possibility of a cached scope contradicting
   the id it describes — which is what allowed a stale `local` scope to survive
   a repository gaining a remote.

6. **The cache is a cache.** `.snodo/project.json` holds the resolved identity
   so that a local uuid survives between runs. It is gitignored, and it is
   never authoritative over the repository's actual state for a local identity.

7. **Identity is hashed.** `project_id` is an input to `_compute_hash`, so it
   is part of what each audit event attests. A promotion therefore changes the
   identity of future events only; events already written keep the identity
   they were written under, and nothing rewrites them.

## Consequences

Engineers sharing a remote share a project identity without configuring
anything, which is the case that matters. Two clones of a remote-less
repository are two projects and will always be reported as two — correct, and
occasionally surprising.

A project that gains a remote after its first run has two identities in its
history, with a clean cut at the moment of promotion. Consumers aggregating
across the boundary have to decide whether to re-key the earlier events, keep
an alias, or show the discontinuity. Snodo does not make that decision for
them; it does not rewrite the past.

Resolution for a local-scope project runs `git remote get-url` on every call,
because the answer can change. That is a subprocess where a cache read would
otherwise do, and it is the price of identity following the repository rather
than following whatever was true the first time.

Consumers receiving events must be told which kind of identity they hold, since
the two demand opposite treatment: a `remote` id may be merged across machines,
a `local` id may never be. Scope is transmitted alongside the id for exactly
this reason (`docs/specs/cloud-sync.md`).

## Implementation

`snodo/project.py` — `resolve_project_id`, `get_project_id`,
`scope_for_project_id`, `cache_project_id`, `normalize_remote_url`.

Written after the fact: this record was cited by `project.py` and `audit.py`
for months before it existed, and in its absence the implementation drifted
from the rule — a cached `local:` identity short-circuited resolution, so a
repository that gained a remote never promoted (#205). The decision described
here is the one that was always intended; the drift is what an unwritten
decision costs.
