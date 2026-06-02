---
adr: 010
status: Accepted
---

## 010: GitPython over subprocess for git operations

- **Status**: Accepted
- **Context**: Decisions.md records: "What we built that we shouldn't have: Custom git subprocess (Task 4.7 fixes)." The original `GitMCP` shelled out to `git` CLI via subprocess, parsing text output. GitPython provides a programmatic API with structured results.
- **Decision**: Replace subprocess-based git operations with GitPython (`git.py:16`, `from git import Repo, GitCommandError, InvalidGitRepositoryError`). The `GitMCP` wraps a `Repo` object and exposes git operations as methods rather than CLI wrappers.
- **Consequences**: Error handling is structured (exceptions, not output parsing). The initialisation path checks for valid git repositories (`InvalidGitRepositoryError`). All git mutating operations require a validation token (WF1 enforcement).
- **Alternatives considered**: Subprocess git CLI — rejected; recorded in decisions.md as a mistake. Dulwich (pure-Python git) — considered but GitPython is more widely used and maintained.
- **Evidence**: Audit log entry 28 (2025-05-25, Task 4.7), commit `9c5df9da`; `decisions.md:50`; `git.py:16`.

---
