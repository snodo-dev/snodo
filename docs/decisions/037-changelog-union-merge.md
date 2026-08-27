# ADR 037 — CHANGELOG.md merges with `merge=union`, not fragment files

## Status

Accepted

## Context

Every agent appends its user-visible change at the top of `### Added` under
`[Unreleased]` in `CHANGELOG.md`, so any two branches collide on the same three
lines by construction. Six agent merges today produced six conflicts, all in
CHANGELOG.md, all resolved identically by keeping both entries (issue #81). The
code merges cleanly every time; the changelog never does. With four agents this
is the dominant friction in the merge path.

The usual answer is fragment files — each change writes `changelog.d/<issue>.md`
and a release step assembles them into CHANGELOG.md. That is not the only
answer, and it has real costs.

## Decision

**`CHANGELOG.md` is marked `merge=union` in `.gitattributes`, and git's built-in
union driver does the keep-both resolution automatically.** A parallel agent
appending its entry at the top of `### Added` merges cleanly; both entries
survive; two agents writing the *identical* entry (same issue) dedupe to one.
Verified against the exact failure class — two branches each inserting an entry
above an existing bullet, merged through the repository's GitPython merge path
— and pinned by a canary test (`tests/golden/test_changelog_union_merge.py`)
that fails at the branch if the `.gitattributes` declaration is dropped or if
the no-driver conflict ever stops being a conflict.

Why not fragments:

- Fragments cost an **assembly step** — a release process that does not exist
  yet must be built and maintained to turn `changelog.d/*` into the one
  readable CHANGELOG.
- Fragments cost **the single readable CHANGELOG in the working tree** between
  releases — the file an operator or auditor reads becomes a generated
  artifact.
- The conflict being fixed is trivially auto-resolvable in the correct
  direction, which is exactly what a merge driver does. A process redesign is
  warranted when the resolution is ambiguous or needs judgement; "keep both
  entries" is neither.

Consequences:

- Agents keep writing to `CHANGELOG.md`; no instruction change beyond noting
  the driver exists. CONTRIBUTING.md now states it.
- Parallel merges of CHANGELOG.md no longer conflict. The resolution is
  deterministic and correct for the observed failure class.
- Trade-offs accepted: within a conflicted hunk, entry order is
  deterministic-but-not-newest-first, and two agents editing the *same* entry
  would concatenate both versions. Neither is the observed failure class
  (agents append their own new entries), and both remain visible in the merged
  file for a human at release time.

## Alternatives considered

- **Fragment files (`changelog.d/` + an assembly step):** rejected — costs an
  assembly step and the single readable working-tree file to fix a conflict
  that a merge driver resolves correctly for free. Revisit if agents begin
  editing one another's entries or if ordering within a release matters.
- **Custom merge driver via `git config`:** rejected — a custom driver must be
  registered on every machine that merges; `merge=union` is built into git and
  needs no configuration.
- **Leave as-is (resolve by hand every time):** rejected — the friction is
  proportional to the number of agents and grows; it was the problem being
  solved.
