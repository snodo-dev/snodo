# W3-05: Execution integrity — empty artifact and error artifact checks

## Intent
Two silent failure modes in _execute_node / _default_executor:

1. CodeArtifact(files=[]) passes through as success — zero files written,
   token consumed, task completes normally with no signal to the user.

2. LLMCallError / ParseError caught by the bare except in _default_executor
   appends "error: ..." strings to the artifacts list. _execute_node sees
   a non-empty list and proceeds as if files were written.

Both need to halt loudly before the token is consumed.

## What to change

### core/interfaces.py
Add ExecutionError(Exception) alongside AuditError.
"Raised when task execution produced no usable artifacts."

### engine/loop.py — _default_executor
After the file-writing loop (after artifact_paths is populated):
- If artifact_paths is empty AND workspace_mcp is not None:
  raise ExecutionError("Coder produced no file operations")
- If any string in artifacts starts with "error:":
  raise ExecutionError(f"Coder execution failed: {artifacts}")

### engine/loop.py — _execute_node
Wrap executor_fn call in try/except ExecutionError:
- On ExecutionError: do NOT consume the token. Set loop_state.is_blocked=True,
  loop_state.halt_reason to the error message, audit the failure,
  return state routed to _blocked_node.
- Token must NOT be consumed on ExecutionError — the task should be
  retryable.

## Acceptance criteria
- Empty CodeArtifact halts with BLOCKED before token consumption
- "error: ..." artifact strings halt with BLOCKED before token consumption
- Token is NOT consumed on ExecutionError — task is retryable
- User sees a clear halt message: "Execution produced no artifacts"
- Happy path (files written) unchanged

## Testing
- Unit test: _default_executor with CodeArtifact(files=[]) →
  ExecutionError raised
- Unit test: _default_executor with LLMCallError → ExecutionError raised
- Unit test: _execute_node with mocked executor raising ExecutionError →
  state is BLOCKED, token not consumed
- Unit test: happy path — files written → no exception, token consumed
- Existing test suite passes clean

## Constraints
- Read engine/loop.py _execute_node and _default_executor in full
  before touching anything
- Touch only core/interfaces.py and engine/loop.py
- Do not change _default_executor's except Exception — wrap it,
  don't replace it
- One commit
