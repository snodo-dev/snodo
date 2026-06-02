---
adr: 008
status: Accepted
---

## 008: File-backed session checkpoint over in-memory state

- **Status**: Accepted
- **Context**: The original session state was in-memory, lost on restart. Resumability requires that session state survives process boundaries — the orchestrator must be able to stop, read the structured halt payload, decide, and resume. An in-memory-only session also cannot support multi-process orchestration.
- **Decision**: Persist sessions as JSON files under `~/.snodo/sessions/` (`session.py:39-348`). Each `SessionState` carries a `Checkpoint` (current task, decisions dict, memory summary, timestamp). No status field — all session files represent valid sessions; deletion removes the session. Active session tracking is project-level via `.snodo/state.json` (`active_session` field). Security: `.snodo/config.yml` permissions `0600` (`config.py:84`).
- **Consequences**: Sessions survive restarts. Resolution decisions are written to `checkpoint.decisions` and consumed on the next governance pass. The `snodo session` CLI (list, show, delete, prune) manages the session directory. Pruning removes sessions older than `max_session_age_days` (default 30). The prune function does NOT read `state.json` to protect the active session — this is a known gap documented in `session.py:291`.
- **Alternatives considered**: SQLite-backed sessions — rejected; JSON files are simpler, human-readable, and sufficient for the per-session access pattern. In-memory with periodic flush — rejected; restart loss is unacceptable for the resolution workflow. Status-field design (active/closed) — rejected; 7.19 simplified to existence = valid session.
- **Evidence**: Audit log entry 48 (2025-05-27, Task 7.3), commit `03dea531`; `session.py:39-61,92-130,159-178`, `state.py:19-25`.

---
