# W2-02: Split mcp/server.py into three files

## Intent
mcp/server.py is 832 lines. Two clean extractions reduce it to ~350 lines
with no behavior change. Pure structural refactor.

## What to change

### mcp/tools.py (new file)
Move out of server.py:
- TOOL_REGISTRY dict (lines 29-364)
- MODE_TOOL_MAP dict (lines 367-381)
- The validate_task tool definition currently inline in _resolve_tools
  (lines 492-505) — move it into TOOL_REGISTRY alongside the other tools

server.py imports TOOL_REGISTRY and MODE_TOOL_MAP from mcp/tools.py.

### mcp/job_handlers.py (new file)
Extract into a JobToolHandler class:
- _handle_get_job_status
- _handle_list_jobs  
- _handle_get_job_logs

Constructor receives project_root only — that's the only dependency
these three methods share. ProtocolMCPServer instantiates JobToolHandler
in __init__ and delegates the three job tool calls to it.

### mcp/server.py (keep)
Everything else stays:
- ProtocolMCPServer with __init__, call_tool, _dispatch_tool,
  _enforce_wf1, _resolve_tools, _handle_validate_task,
  _handle_dispatch_task, _audit, _args_hash, get_tools,
  is_slow_tool, call_tool_async, _resolve_provider

## Acceptance criteria
- server.py under 400 lines after extraction
- tools.py contains all tool schemas including validate_task
- job_handlers.py under 100 lines
- All existing behavior identical
- Adding a new meta-tool still touches 4 sites (that's fine — not
  fixing the registration pattern in this ticket)

## Testing
- No new tests required — pure structural refactor
- Full test suite (1562 tests) passes clean
- If any test breaks, fix the refactor not the test

## Constraints
- Read server.py in full before touching anything
- One commit: all three files + import updates together
- Do not change method signatures
- Do not change tool schemas
