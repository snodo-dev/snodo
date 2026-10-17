# Restore opencode-cli dispatch compatibility (#976)

OpenCode 2.x launches with `run --format json --standalone` and the optional
model argument, using the task workspace as subprocess `cwd`. No directory
flag is invented. The 1.x path retains `--dir` and
`--dangerously-skip-permissions`. Session moves and worktree creation remain
disabled; external-directory access is denied. Executor escape detection is
unchanged.

The previous `permission.edit: deny` blocked edits everywhere, including the
workspace. Ordered edit rules now deny by default and allow the resolved task
workspace. Tests cover in-tree edits, sibling paths, normalized parent traversal,
inherited configuration overrides, provider configuration, and both generations'
actual subprocess launch working directories.

`test_launch_flags_match_recorded_run_help[2]` calls the assertion
`flags <= _accepted_options(RUN_HELP[major])`, with the failure message
`launch flags absent from recorded run help`. Injecting `--auto` into the 2.x
argv in memory was verified to fail this assertion (1 failed, 1 passed).
Recorded help tests make no CLI or model calls. The existing recorded help
excerpts are retained; GitHub access was unavailable to refresh issue #976.

Restored the full CHANGELOG.md from the parent of the earlier implementation
commit and verified byte-for-byte preservation except for one #976 bullet
under Unreleased.

Validation: 52 focused tests passed; lint-imports (six contracts), repository
ruff, file-length, docs-coverage, vocabulary, changelog, release/package docs,
cloud schema, and audit-event checks passed. `make gate` was attempted but the
sandbox refused SSH to its gate host (`Operation not permitted`). The broader coder suite completed with 461 passed, 2 skipped and 2 failures
in subprocess descendant cleanup; its logs show sandbox-denied process
enumeration (`psutil` / `sysctl`). The full local suite completed with 5,659 passed, 3 skipped and 50 failed
(`LITELLM_LOCAL_MODEL_COST_MAP=True CI=true .venv/bin/pytest tests/ -q -n 4
--timeout=60 --timeout-method=signal`). Failures include denied socket/process
operations, audit-path expectations, unavailable JWKS, gate process probes, and
subprocesses unable to find `python`/`pytest` on PATH. These are not claimed as
passing or all proven pre-existing. Full output: `/tmp/opencode-gate-tests-local.log`.
A green `make gate` still requires the normal gate environment.
