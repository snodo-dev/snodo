# W2-04: Migrate core/interfaces.py dataclasses to pydantic BaseModel

## Intent
core/interfaces.py has 6 active dataclasses and 1 pydantic BaseModel.
Three types are dead code. Convert the active types to pydantic BaseModel
for consistency. Remove the dead types.

## What to change

### Remove (dead code — zero consumers)
- ExecutionResult (@dataclass, 0 consumers)
- Event (@dataclass, 0 consumers)
- Mode (@dataclass, 0 consumers — shadowed by compiler/models.py:Mode)

### Convert to pydantic BaseModel
- Task
- TaskSpec — field(default_factory=dict) becomes Field(default_factory=dict)
- FileArtifact
- CodeArtifact — fix untyped files:list to files:list[FileArtifact],
  add Field(default_factory=list)

### Cleanup
- Remove `from dataclasses import dataclass, field` import once all
  @dataclass decorators are gone
- Add `from pydantic import BaseModel, Field` if not already present

## Acceptance criteria
- interfaces.py imports only pydantic, no dataclasses
- Dead types removed
- CodeArtifact.files typed as list[FileArtifact]
- All active types are pydantic BaseModel
- No consumer files need changes (verify: no asdict/fields/replace
  calls on these types anywhere)

## Testing
- No new tests required
- Full test suite passes clean
- If any test breaks, a consumer was using dataclass-specific behavior
  that wasn't caught in recon — fix the consumer, not the spec

## Constraints
- Read core/interfaces.py and grep all consumers before touching anything
- One commit: interfaces.py only (consumers need no changes)
- Do not change AuditError, Coder ABC, MCPServer ABC
- Do not touch compiler/models.py — its Mode is unrelated
