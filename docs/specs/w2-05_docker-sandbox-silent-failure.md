# W2-05: Docker sandbox silent container failure

## Intent
DockerSandbox.run_task() has an except Exception at line 143 that returns
SandboxResult(exit_code=1) instead of raising. The method's own docstring
promises SandboxError on failure. A caller can't distinguish "container ran
and failed" from "Docker never started." Fix the contradiction.

## What to change

### sandbox/docker_sandbox.py
Replace the bare except Exception / return SandboxResult(exit_code=1)
at line 143 with:
  raise SandboxError(f"Container execution failed: {e}") from e

### cli/commands/sandbox_run.py
Wrap the run_task call at line 100 in try/except SandboxError:
- On SandboxError: print the error message, return exit code 1
- Pattern already exists in sandbox_cmd.py:58 — follow that pattern

## Acceptance criteria
- run_task raises SandboxError on container execution failure
- sandbox_run.py catches SandboxError and exits cleanly with code 1
- Caller can distinguish real container failure (non-zero exit_code on
  SandboxResult) from infrastructure failure (SandboxError raised)
- Docstring contract matches implementation

## Testing
- Add test: mock container execution failure → assert SandboxError raised
- Add test: sandbox_run catches SandboxError → returns exit code 1
- Existing test suite passes clean

## Constraints
- Read docker_sandbox.py and sandbox_run.py before touching anything
- Follow the existing SandboxError pattern in sandbox_cmd.py:58
- Touch only docker_sandbox.py and sandbox_run.py
