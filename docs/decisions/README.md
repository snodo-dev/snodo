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
