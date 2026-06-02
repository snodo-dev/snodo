---
adr: 006
status: Accepted
---

## 006: Severity cap for validators under evaluation

- **Status**: Accepted
- **Context**: When a new validator is deployed experimentally, a false-positive `blocker` can halt the entire pipeline. Operators need a way to deploy validators in "shadow mode" — they evaluate and report, but their maximum severity is capped at `warn` so they cannot block execution until proven reliable.
- **Decision**: Add `severity_cap: Optional[Severity]` to the `Validator` model (`models.py:89`). The engine applies the cap after dispatch: if `Severity(result.severity) > v.severity_cap`, the severity is lowered to the cap value (`loop.py:757-762`). A cap of `warn` means a validator can warn but never block. No cap (or `blocker`) means full power.
- **Consequences**: Validators can be graduated from shadow (capped at `warn`) to live (no cap) without protocol changes. The cap is applied post-validation, so the validator's original reasoning is preserved in the justification — only the severity is adjusted. The protocol-adherence validator in shipped templates uses `severity_cap: "warn"` as its default deployment posture.
- **Alternatives considered**: Separate shadow-mode flag — rejected; `severity_cap` generalises to any intermediate severity. Feature-flag toggles — rejected; the cap lives in the protocol YAML where it's visible and auditable.
- **Evidence**: Audit log entry 58 (2025-05-31, Task 7.17), commit `1f04b9fb`; `models.py:89-94`, `loop.py:757-762`.

---
