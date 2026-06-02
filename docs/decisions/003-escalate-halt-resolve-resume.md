---
adr: 003
status: Accepted
---

## 003: ESCALATE as halt → resolve → resume

- **Status**: Accepted
- **Context**: When the validator quorum splits without blockers (e.g., 1 pass, 1 warn under unanimous), the engine needs a mechanism to request human judgment. A simple block would not distinguish this case from a hard-stop blocker, and the orchestrator needs structured information to decide.
- **Decision**: ESCALATE populates `pending_disagreement` on the `LoopState` (`loop.py:352,484`) with phase, policy, validator results, and policy decision. The CLI emits a structured JSON payload (halt_type="escalated") including per-validator justifications. The orchestrator (human or automated) reads the payload and calls `snodo resolve <session_id> <task_id> --decision proceed|halt`. The resolution is stored in the session checkpoint's `decisions` dict. On resume, the governance node checks for a resolution: if `proceed`, `resolution_override` is set and validation is skipped; if `halt`, the task is blocked.
- **Consequences**: ESCALATE is a structured workflow, not a generic block. The resolution is single-use (consumed after governance reads it). The structured halt payload mirrors the same envelope for both ESCALATE and blocker halts, with `halt_type` as a discriminator. The `snodo resolve` CLI command writes directly to the session decisions store.
- **Alternatives considered**: Auto-retry with different thresholds — rejected; human judgment is the orchestrator's decision, not the engine's. Block without structured data — rejected; the orchestrator needs validator-level justifications to decide. Always proceed on warn — rejected; contradicts INV3 semantics.
- **Evidence**: Audit log entry 54 (2025-05-27, Task 7.10), commit `f7f10a27`; `loop.py:342-374,470-502`, `run_cmd.py:542-585`.

---
