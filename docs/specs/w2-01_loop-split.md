# W2-01: Split engine/loop.py into three files

## Intent
loop.py is 1228 lines mixing graph construction, constraint evaluation,
and validator dispatch. Extract two natural seams into standalone classes.
No behavior change — pure structural refactor.

## What to change

### engine/constraints.py (new file)
Extract into a ConstraintEngine class:
- _default_governance
- _evaluate_constraints  
- _apply_constraint_failure

Constructor receives: protocol, predicate_registry, workspace_mcp, git_mcp.
GraphBuilder instantiates ConstraintEngine in __init__ and delegates
_governance_node to it.

### engine/validators.py (new file)
Extract into a ValidatorRunner class:
- _default_validator
- _resolve_validators
- _dispatch_one
- _get_completion_fn

Constructor receives: protocol, coder, audit_log, workspace_mcp, git_mcp,
session_manager.
GraphBuilder instantiates ValidatorRunner in __init__ and delegates
_validate_node and _post_validate_node to it.

### engine/loop.py (keep)
Everything else stays:
- LoopState, LoopStage
- GraphBuilder with graph topology, nodes, routing, serialization
- _collect_project_context, _build_dir_tree (leave in place — too small
  to justify a new file)
- _maybe_summarize, _init_summary_model (leave in place)
- build_protocol_graph factory
- _build_audit_results

## Acceptance criteria
- loop.py under 700 lines after extraction
- constraints.py and validators.py each under 150 lines
- All existing behavior identical — no logic changes, no new features
- All existing tests pass with no modifications to test files
- Imports in loop.py updated to use ConstraintEngine and ValidatorRunner

## Testing
- No new tests required — this is a pure structural refactor
- Full test suite (1562 tests) passes clean
- If any test breaks, it means behavior changed — fix the refactor,
  not the test

## Constraints
- Read loop.py in full before touching anything
- One commit: all three files + any import updates together
- Do not change method signatures
- Do not change LoopState fields
- Do not move _dict_to_state / _state_to_dict — they stay in loop.py
