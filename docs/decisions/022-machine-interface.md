# ADR 022 — A versioned machine interface (`--json`) for integrations

## Status
Accepted

## Context
An opencode plugin enforces snodo's mode boundary by reading `.snodo/state.json`
and parsing `protocol.yml` with a line scanner. That works for a proof of
concept and is not a foundation: it depends on the on-disk file format, which is
an implementation detail, not a contract. Integrating further — validating a
prompt before work, validating a result after, resolving escalations from
inside the agent — needs a stable machine interface.

The engine already produces the right shape for a validation result: the halt
payload carries `status`, per-validator `results`, and a `policy_decision`. What
is missing is a way to reach that shape directly, without running a coder, and
a way for a caller to branch on the outcome without parsing prose.

## Decision

1. **`--json` on the commands an integration needs.** `status`, `mode show`,
   `session show`, `task show`, and `worktree list` gain a `--json` flag that
   emits a single JSON object to stdout. Human output is unchanged — `--json`
   is additive.

2. **A versioned schema field.** Every payload carries `schema` of the form
   `snodo.<command>.v<N>`. A consumer checks this field first; a mismatch means
   the shape changed and the consumer should refuse to parse rather than
   misread. Field names are asserted by the test suite, so a rename fails the
   suite rather than a downstream consumer.

3. **`snodo validate`** runs a phase's validators through the shared engine
   runner (`snodo.validators.runner`) and returns the four-outcome result as
   JSON — the halt-payload shape, reachable directly, with no coder. It reuses
   the same runner the engine and the MCP server use, so the three paths cannot
   drift apart.

4. **Exit codes distinguish the four outcomes.** `pass`=0, `blocker`=1,
   `escalate`=2, `validator_error`=3, `internal_error`=4. A caller branches on
   the exit code without parsing prose.

5. **The contract is documented in `docs/machine-interface.md`**, not only in
   `--help`. An integration surface that is only discoverable by reading the
   source is not a contract.

## Why this is a promise

This is snodo's first committed machine interface. Its stability is a promise:
a consumer (the opencode plugin, and any future agent integration) will build
against these field names and exit codes. Breaking them silently would break
that consumer. The schema field is the mechanism that makes a breaking change
detectable rather than silent — the same principle as the four-outcome
vocabulary: each outcome means one thing, and nothing is silently remapped.

## Consequences

- `snodo validate` is a new top-level command; the command-registry e2e test
  pins it.
- The shared JSON helper (`snodo/cli/json_output.py`) centralises the schema
  version and the exit-code map, so a future command cannot invent a divergent
  convention.
- No new dependency: JSON is emitted with the standard library.

## Alternatives considered

- **Expose the on-disk files as the contract** (document `state.json` /
  `protocol.yml`): rejected — those are implementation details that have
  already changed shape (e.g. `active_session` migrated from a string to a
  per-mode dict). A file format is not a stable interface.
- **A separate `snodo api` subcommand surface**: rejected — the existing
  commands already read the right data; adding `--json` to them is the smallest
  surface that stays discoverable.
- **Exit code 0 for "ran successfully" regardless of outcome**: rejected — a
  caller must be able to branch on the outcome without parsing prose, which is
  the whole point of the exit-code contract.
