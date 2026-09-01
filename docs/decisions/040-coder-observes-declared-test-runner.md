# ADR 040 — The coder observes the declared test runner; the validators still judge

## Status

Accepted. Implementation tracked separately — this ADR records the boundary
before the tools that cross it exist.

## Context

`LiteLLMAdapter`'s toolset is read-only by construction: `read_files`,
`read_file`, `read_file_lines`, `list_files`, `submit_files`. The header at
`packages/snodo-engine/src/snodo/coders/litellm.py:12` says "NO write tool, NO
shell" and it means it. The consequence is that the coder writes blind. It
reads, proposes an edit, submits, and only then — a full loop later, through
the validators — learns whether the thing compiled. Every iteration buys with
a complete turn the feedback that one command would have given for free, and a
`NameError` costs the same as a design flaw.

A test runner already exists. `ShellMCP.run_tests`
(`packages/snodo-tools/src/snodo/tools/shell.py:92`) executes a whitelisted
test command inside the project root and converts the result to a
`ValidatorResult`. It is exposed to MCP consumers
(`packages/snodo-mcp/src/snodo/mcp/tools.py:60`, mapped to the `test` and
`validate` modes at line 565). The coder loop cannot reach it. Two read-only
search tools are in the same state, further along: `ReadMemoryTracker`
(`litellm.py:952`, `litellm.py:1056`) already deduplicates `search_symbol` and
`search_string` calls, for tools no coder can make and no module implements.

The reason none of this reached the coder is ADR 039, which exists to keep the
protocol's gates out of the coder's hands: the engine builds its own LLM client
so that a coder cannot supply, influence or discharge the mechanism that judges
it. ADR 026 protects the protocol from the agent's tool surface; ADR 014 bounds
the trust the repository extends to a running agent. A coder that can execute
things sits close to all three, and "the coder should get faster feedback" is
not on its own an argument that survives them.

It survives them under one distinction, which this ADR draws.

## Decision

1. **Observation is not adjudication.** A coder may run the project's declared
   test runner during generation and read what it printed. That is what a
   developer does with `pytest` in a second terminal while drafting, and it is
   not governance: the engine's validators — the acceptance validator of ADR
   028, protocol adherence, the LLM quality gates — still run independently
   after `submit_files`, on the submitted artifact, and still decide. The coder
   cannot alter validator logic, cannot self-report a pass, and cannot bypass
   post-execution validation. Nothing about the halt taxonomy changes.

2. **The coder receives evidence, not a verdict.** `run_tests` returns a
   `ValidatorResult` carrying a `severity` field — the exact type and the exact
   field the validators emit. Handing that object to the coder would conflate
   the two roles in the one place this ADR exists to keep them apart. The
   coder-facing surface returns the exit code, stdout and stderr. Existing MCP
   consumers keep the `ValidatorResult` shape; this is a second, narrower
   surface over the same execution, not a change to the first.

3. **The declared runner only; no arbitrary shell.** The coder gets
   `ShellMCP`'s whitelist — `pytest`, `npm test`, `cargo test`
   (`shell.py:49`) — and nothing else. Arbitrary shell access is an
   unacceptable expansion of the threat surface and would rewrite ADR 014's
   trusted-repository model rather than operate inside it. A project whose
   tests run some other way does not get this capability until its runner is
   declared.

4. **The whitelist bounds the binary, not the invocation — so the coder-facing
   surface bounds the invocation.** `run_tests` extends the argv with
   caller-supplied `extra_args` unvalidated (`shell.py:115`) and appends
   `test_path` raw, despite the class docstring claiming the call is "sandboxed
   within project root". Under pytest that is sufficient to defeat every other
   protection here: `-k` and `--deselect` run only the tests that pass, `-p`
   loads a plugin, `-c` and `--override-ini` substitute a different
   configuration — none of which touches a test file, so none of which raises
   the flag in point 5. The `# noqa: S603` at `shell.py:122` reasons about
   shell injection, which is genuinely absent, and is silent on argument
   injection, which is not. The coder-facing surface therefore accepts no
   `extra_args`, and resolves `test_path` under the project root or refuses.

5. **Detection covers test-governing files, not test files.** Path globs over
   `tests/`, `*_test.py`, `test_*.py` and `spec/` catch the naive case and miss
   the real one. `conftest.py`, `pytest.ini`, `tox.ini`, `[tool.pytest]` in
   `pyproject.toml`, `.coveragerc` and fixture modules outside the test tree all
   govern what a suite proves; a fixture that stubs the unit under test weakens
   the suite without matching any of those globs. The detected category is
   every file that governs what the tests assert.

6. **The flag is per-file and carries the change kind.** A single
   `test_mutation_detected: true` on the artifact tells the acceptance
   validator that something happened and nothing about what, which is not
   enough to judge whether the task's specification authorized it. The artifact
   carries, per governing file, whether it was added, modified or deleted.
   Deletion is the case worth naming: a task that removes a test it was not
   asked to remove is the failure this whole mechanism is watching for.

7. **Read-only search joins the toolset.** `search_string` and `search_symbol`
   are read-only exploration and raise none of the questions above; they let
   the coder locate code in one turn instead of listing exhaustively. They do
   not currently exist — the deduplication in `ReadMemoryTracker` was written
   ahead of them — and are implemented as part of this decision, not wired.

8. **The capability is bounded inside the turn.** `run_tests` carries a
   300-second timeout and the MCP server already classes it slow
   (`packages/snodo-mcp/src/snodo/mcp/server.py:88`). A coder that can call it
   freely can spend a turn budget on a suite that has nothing to do with its
   task. The coder-facing surface carries its own invocation cap and its own
   timeout, and exhausting either is a bound reached, not a test failure:
   it ends the observation, never the run, and never reaches the validators as
   a verdict.

9. **It is a declared capability under ADR 035, not a feature of one adapter.**
   The in-place coders already execute — `opencode` and `agy` shell out by
   nature (ADR 030, ADR 034) — so this decision changes what `litellm` can do
   while leaving them where they were. That asymmetry is precisely what ADR 035
   requires the coder to declare to the engine rather than the engine to infer.
   The engine asks the coder whether it observes tests; it does not check which
   class it is.

10. **Both sides are audited.** A `test_modified` event records a change to a
    governing file, per point 5, so `snodo status` and the audit trail carry it.
    A second event records the coder's own test runs. Without the second, point
    1's claim — that this is developer feedback and not governance — is an
    assertion about the design rather than a fact about a run: an operator
    should be able to see afterwards that the coder ran the suite six times
    before submitting, and a reviewer should be able to see that those runs
    changed nothing about who judged the result.

## Consequences

- A generation turn can be wrong about something a test would have caught, and
  the coder finds out inside the turn rather than through the validators. The
  loop pays fewer iterations for mechanical errors; the validators keep seeing
  exactly what they saw before.
- The trust surface grows by one bounded, whitelisted, argument-free execution
  path. It does not grow by a shell. ADR 014's model is unchanged, and a
  project that does not declare a runner is unaffected.
- The acceptance validator (ADR 028) receives a new input and a new
  responsibility: judging whether a change to a test-governing file was
  authorized by the specification. This is LLM judgment over evidence, and the
  evidence is now specific enough to judge — which file, and what happened to
  it.
- `ShellMCP.run_tests` is hardened for its existing MCP callers as a
  side-effect of point 4. That hardening is not optional and not deferrable:
  the argument-injection path is reachable today by any MCP client, and this
  ADR is the reason it was looked at.
- Coder capability is no longer uniform across the registry, and the conformance
  suite must exercise both answers to the declaration in point 9.

## Alternatives considered

- **Give the coder an arbitrary shell.** Rejected. It is the largest single
  expansion of the threat surface available and it discards ADR 014 rather than
  working inside it. Every argument for it is an argument for a different
  product.
- **Leave the coder blind and let the validators be the only feedback.** This
  is the status quo and it is defensible — it is the cleanest possible reading
  of ADR 039. Rejected because it makes the protocol pay a full governed
  iteration for a `NameError`, which is not what the governance is for, and
  because the cost falls hardest on exactly the small bounded tasks the
  protocol is best at.
- **Let the coder call the acceptance validator directly.** Rejected outright.
  That is not observation; it is the coder marking its own homework, and it is
  the thing ADR 039 was written to prevent.
- **Detect test weakening by comparing pass counts before and after.** Rejected
  as the primary mechanism — it is defeatable by the argument-injection path in
  point 4, it cannot distinguish a legitimately removed test from a suppressed
  one, and it makes the engine depend on parsing runner output it does not
  control. It may be worth having as corroboration; it is not the boundary.
