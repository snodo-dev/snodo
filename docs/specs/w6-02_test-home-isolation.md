# W6-02: Isolate SNODO_HOME in tests

## Intent
Tests that call main() or construct AgentMemoryManager() without a
home_dir read and (in one case) WRITE to the real ~/.snodo/agents.json.
test_agent_list_shows_agents writes on every run; the other TestAgentCLI
tests read the real file and crash when it's corrupted. No test isolates
SNODO_HOME — temp_project isolates the project dir but not the home dir.
Fix: ensure NO test touches the real ~/.snodo.

## What to change

### tests/conftest.py (or the relevant fixture)
- Add SNODO_HOME isolation to the fixture(s) used by tests that invoke
  main() or AgentMemoryManager without an explicit home_dir.
- Use monkeypatch.setenv("SNODO_HOME", <temp dir>) so resolve_home()
  (paths.py:24) returns a temp dir, not ~/.snodo.
- temp_project should isolate BOTH project dir AND SNODO_HOME.

### Verify no test reaches real home
- After the fix, TestAgentCLI tests use the isolated home — they no
  longer read or write ~/.snodo/agents.json.
- The 5 previously-failing tests pass because they read an isolated
  (empty/clean) registry, not the real corrupted one.

### Optional hardening (low priority — include only if trivial)
- _load_registry (memory.py:59) crashes on a corrupt agents.json with a
  raw JSONDecodeError. Consider catching it and returning an empty
  registry with a clear warning, so a corrupt file degrades gracefully
  instead of crashing every read. ONLY do this if it's a clean small
  change; the primary fix is isolation.

## Acceptance criteria
- No test reads or writes the real ~/.snodo (verified: SNODO_HOME points
  to a temp dir in the relevant fixtures)
- TestAgentCLI tests pass without depending on real-home state
- The fixture cleans up its temp SNODO_HOME
- If corruption-handling is added: a corrupt agents.json → empty registry
  + warning, not a crash

## Testing
- The 5 previously-failing TestAgentCLI tests pass (full suite, -m "")
- Confirm via the fixture that SNODO_HOME is set to a temp path during
  these tests
- Full suite passes clean

## Constraints
- Read tests/conftest.py, tests/infrastructure/test_memory.py (temp_project,
  temp_home fixtures, TestAgentCLI), infrastructure/paths.py (resolve_home),
  tests/e2e/conftest.py (the existing SNODO_HOME pattern for subprocesses)
  before touching anything
- Do NOT change the atomic write in memory.py — it's correct
- Primary fix is test isolation; corruption-handling is optional and only
  if trivial
- Touch only test files (and memory.py only if adding the optional
  graceful-read, kept minimal)
