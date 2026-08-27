# Architecture Decision Records

Snodo design decisions extracted from the development audit log and `.snodo/bootstrap/decisions.md`. Each ADR follows the Nygard format: **Title**, **Status**, **Context**, **Decision**, **Consequences**, **Alternatives**. One page or less each.

| ADR | Title | Date | Audit-log anchor |
|-----|-------|------|-----------------|
| [001](001-pyjwt-over-hmac.md) | PyJWT over custom HMAC signing | 2025-05-27 | Task 7.7 (`tokens.py`) |
| [002](002-warn-withholds-approval.md) | Warn withholds approval in policy thresholds | 2025-06-01 | Policy semantic fix (`policy.py`) |
| [003](003-escalate-halt-resolve-resume.md) | ESCALATE as halt → resolve → resume | 2025-05-27 | Task 7.10 (`loop.py`) |
| [004](004-constraint-predicate-framework.md) | Constraint predicate framework | 2025-05-27 | Task 7.8 (`predicates/`) |
| [005](005-protocol-adherence-validator.md) | Protocol-adherence validator from mode profiles | 2025-05-27 | Task 7.11 |
| [006](006-severity-cap.md) | Severity cap for validators under evaluation | 2025-05-31 | Task 7.17 (`models.py`) |
| [007](007-coder-adapter-provider-pattern.md) | Coder adapter + code-host provider pattern | 2025-05-25 | Tasks 4.10, 4.6 |
| [008](008-file-backed-session-checkpoint.md) | File-backed session checkpoint over in-memory state | 2025-05-27 | Task 7.3 (`session.py`) |
| [009](009-fastmcp-over-custom-transport.md) | FastMCP over custom MCP transport | 2025-05-25 | Task 4.5 (`transport.py`) |
| [010](010-gitpython-over-subprocess.md) | GitPython over subprocess for git operations | 2025-05-25 | Task 4.7 (`git.py`) |
| [011](011-typer-over-argparse.md) | Typer over argparse for CLI | 2025-05-25 | Task 4.8 (`main.py`) |
| [013](013-kleene-closure-auto-fix-recovery.md) | Kleene-closure auto-fix recovery loop | 2026-06-28 | Recovery driver (`closure.py`) |
| [014](014-trusted-repository-threat-model.md) | Trusted-repository threat model and `init` consent gate | 2026-08-21 | Threat-model decision (`init_cmd.py`) |
| [015](015-mcp-validation-four-outcome-contract.md) | Real validation on the MCP path + four-outcome `validate_task` | 2026-08-21 | `handle_validate_task` (`server.py`) |
| [016](016-token-single-use-sqlite-store.md) | Shared SQLite store for validation-token single-use | 2026-08-22 | `TokenStore` (`tokens.py`) |
| [017](017-wf1-exclusive-tools.md) | WF1 relaxed to exclusivity on approval-conferring tools | 2026-08-23 | `check_wf1` (`verifier.py`) |
| [018](018-auto-merge-task-branches.md) | Auto-merge task branches on successful completion | 2026-08-24 | `_merge_on_success` (`run_cmd.py`) |
| [019](019-phase-aware-validator-prompts.md) | Phase-aware validator prompts + read tools for repository-content validators | 2026-08-24 | `_phase_frame` (`llm_validator.py`) |
| [020](020-wave-classifier-config.md) | Wave classification reads `ClassifierConfig`; classifier model resolved once | 2026-08-24 | `_migrate_wave_classifier_keys` (`config.py`) |
| [021](021-recovery-builds-from-original-task.md) | Recovery builds from the original task, not the previous attempt | 2026-08-24 | `_spawn_recovery_subtask` (`loop.py`) |
| [022](022-machine-interface.md) | A versioned machine interface (`--json`) for integrations | 2026-08-24 | `json_output.py` + `validate_cmd.py` |
| [023](023-spec-authoring-spec-quality-critique.md) | Spec-authoring receives only spec-quality critique | 2026-08-24 | `judges_spec` (`models.py`) + `_spec_authoring_reentry` |
| [024](024-environment-preparation-and-task-isolation.md) | Environment preparation before task execution | 2026-08-24 | `environment.py` + `governance.py` |
| [025](025-unborn-head-worktree-fails-loud.md) | Unborn-HEAD worktree creation fails loud, never degrades to no isolation | 2026-08-24 | `create_worktree` + `setup_for_task` (`worktree.py`) |
| [026](026-protocol-protected-from-agent-mutation.md) | Protocol and governance state protected from agent tool-surface mutation | 2026-08-24 | `workspace.py` + `git.py` + `coders/` |
| [027](027-in-place-coder-snodo-mutation-halt.md) | In-place coder .snodo/ mutations are detected and halt as a blocker | 2026-08-25 | `coders/base.py` + `engine/loop.py` |
| [028](028-acceptance-validator.md) | Post-execute acceptance validator judges artifacts against the task's acceptance criteria | 2026-08-25 | `validators/acceptance.py` + `validators/runner.py` |
| [029](029-per-mode-recovery-depth.md) | Per-mode max_recovery_depth override | 2026-08-25 | `resolve_mode_setting` (`models.py`) |
| [030](030-in-place-coder-owns-commit.md) | In-place coder adapters own the commit, so the review channel is the artifact channel | 2026-08-26 | `coders/base.py` + `coders/opencode_adapter.py` |
| [031](031-first-class-verification-audit-events.md) | First-class verification events in audit trail and blocking unverified merges | 2026-08-26 | `quality.py` + `run_cmd.py` |
| [032](032-patch-coverage-enforcement.md) | Patch coverage measurement over modified lines | 2026-08-26 | `patch_coverage.py` + `ci.yml` |
| [033](033-tool-loop-repeat-read-deduplication.md) | Tool loop repeat read memory and result preservation | 2026-08-26 | `litellm.py` + `llm_validator.py` |
| [034](034-opencode-path-experimental.md) | The opencode coder path is experimental, not supported | 2026-08-26 | `init_cmd.py` + `docs/protocol.md` |
| [035](035-declared-coder-capability-interface.md) | Declared coder-adapter capability interface; "coder produced nothing" is always a fault | 2026-08-26 | `core/interfaces.py` + `engine/nodes/executor.py` |
| [036](036-operator-human-review-tracking.md) | Operator human review tracking via audit log events | 2026-08-26 | `task_cmd.py` + `audit.py` |

