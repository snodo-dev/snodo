# ADR 014 — Trusted-repository threat model and the `snodo init` consent gate

## Status
Accepted

## Context
A hardening review of the tool surfaced a family of "security" findings around
shell/test execution: `ShellMCP` runs repo-defined test commands (`npm test`,
`pytest`, `cargo test`), test subprocesses inherit the ambient environment, and
the shell tool appends user-controlled arguments into the command list. Whether
any of these is a vulnerability depends on an assumption that was never stated
explicitly: *whose code is in the workspace?*

snodo is a local protocol engine. The user initialises it inside a repository
and asks it to run AI agents that read, write, and execute code in that
repository. The boundary of trust therefore hinges on the act of running
`snodo init` in a given directory — and nothing in the product made that
boundary explicit to the user.

## Decision
snodo operates under a **trusted-repository model**:

- snodo runs agents and executes code (tests, build commands) inside the
  repository it is initialised in.
- The user must explicitly run `snodo init` in that repository. That act is the
  consent boundary.
- Repository contents — `package.json` scripts, `conftest.py`, Makefiles, test
  code, and anything else on disk — are treated as TRUSTED, equivalent to the
  user running them directly.
- Running snodo against untrusted or third-party code is OUT OF SCOPE and
  unsupported. Isolation (containerisation, environment scrubbing, egress
  control) is explicitly not claimed.

To make the assumption explicit, `snodo init` now presents a consent gate:
before writing anything it warns that snodo runs AI agents that execute code in
the repository, and requires explicit confirmation (default **No**). A `--yes`
/ `--no-input` flag bypasses the prompt for CI and scripted use. When standard
input is not a terminal and no flag is supplied, init fails with guidance to
re-run with `--yes` rather than hanging or silently defaulting.

A second threat source remains in scope: the *agent* is semi-untrusted, because
prompt injection (issue text, README, dependency documentation) can steer its
tool calls. Defences against a manipulated agent — e.g. argument and path
validation on tool inputs — remain in scope and are tracked separately.

## Consequences
- The `npm test` tool running repo-defined `pre`/`posttest` scripts, and test
  subprocesses inheriting the environment, are ACCEPTED RISKS under this model.
  They are documented here rather than mitigated.
- Tool-input validation (argument injection, path traversal) is still fixed,
  because the agent is semi-untrusted and must not be able to escalate from
  "trusted repo, manipulated agent" to arbitrary effects.
- `snodo init` gains a consent prompt and a `--yes`/`--no-input` flag; existing
  callers that invoke `init` non-interactively must pass the flag.
- `snodo init` now appends `.snodo/` to the project `.gitignore` (creating the
  file if absent, never duplicating the entry) so protocol state is not
  committed by default.

## Non-consequences
- Committing `.snodo/` deliberately is an edge case and explicitly out of scope.
- Fixing `npm` pre/posttest execution, environment scrubbing, or any
  sandboxing/containerisation is out of scope under this model.

## Hosted / multi-tenant caveat
A hosted or multi-tenant deployment — e.g. running snodo against repositories
the operator does not own or trust — would **invalidate** this model and require
real isolation: containerisation, network egress control, and environment
scrubbing. This ADR applies only to the single-user, local, trusted-repository
case.

## Alternatives considered
- Treat repository contents as untrusted and mitigate each shell/test finding
  in isolation (env scrubbing, `--ignore-scripts`, strict arg allowlists):
  rejected — it implies a sandboxing posture that snodo does not implement
  end-to-end and cannot honestly claim.
- Silently proceed without a consent gate: rejected — the trust boundary must
  be explicit, not implicit.
