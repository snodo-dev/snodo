---
adr: 011
status: Accepted
---

## 011: Typer over argparse for CLI

- **Status**: Accepted
- **Context**: Decisions.md records: "What we built that we shouldn't have: Custom argparse CLI (Task 4.8 fixes)." The original CLI was built on argparse with manual help formatting. Typer provides type-annotated commands, automatic help, sub-app grouping, and shell completion.
- **Decision**: Replace argparse-based CLI with Typer (`main.py:13,33-37`). The root `app` registers 7 top-level commands and 7 sub-apps (plan, job, agent, config, session, mode, sandbox). The `main()` entry point (`main.py:555-571`) accepts an optional `argv` parameter for test invocation.
- **Consequences**: CLI is strongly typed — parameter types are inferred from annotations. Help output is auto-generated and consistent. Shell completion is built-in (`--install-completion`). The test suite invokes `main(argv=[...])` directly, bypassing sys.argv.
- **Alternatives considered**: argparse — rejected; recorded in decisions.md as a mistake. Click — Typer builds on Click with type annotations, which is more maintainable for a command set that has grown to 35 commands across 7 sub-apps.
- **Evidence**: Audit log entry 29 (2025-05-25, Task 4.8), commit `f12afbf3`; `decisions.md:51`; `main.py:13,33-37,555-571`.
