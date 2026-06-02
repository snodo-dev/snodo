---
adr: 005
status: Accepted
---

## 005: Protocol-adherence validator from mode profiles

- **Status**: Accepted
- **Context**: In a multi-mode protocol (producer → reviewer), tasks may be submitted to the wrong mode (e.g., review work sent to the producer mode). The engine needs a validator that checks whether the task spec is appropriate for the current mode's operational profile, without requiring per-protocol custom validator logic.
- **Decision**: The protocol-adherence validator (`validators/protocol_adherence.py:43`) derives a mode profile from the current mode's `tools`, `validators`, `transitions`, and `name`. The profile is compiled into a structured description of the mode's operational scope and fed to the LLM with the task spec. The LLM compares the task against the profile, determines whether it belongs in this mode or a sibling, and emits pass/warn/blocker with justification. The validator is configured with `severity_cap: "warn"` in shipped templates — misrouted work triggers a warning, not a blocker, because it's an advisory check, not a hard boundary violation.
- **Consequences**: Every protocol with mode transitions gets a free mode-appropriateness check. The validator reads mode profiles structurally (tools, validators, transitions) from `_derive_mode_profile()` and enriches with resolved validator information via `_enrich_profile()`. The `severity_cap` prevents misrouting from blocking execution — it's designed to inform, not to enforce.
- **Alternatives considered**: Per-protocol custom validator — rejected; the mode profile is derivable from the protocol YAML. Block on mismatch — rejected; advisory warnings are more appropriate for mode routing.
- **Evidence**: Audit log entry 55 (2025-05-27, Task 7.11), commit `ac124d8d`; `validators/protocol_adherence.py:43-84`.

---
