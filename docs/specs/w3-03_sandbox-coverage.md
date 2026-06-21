# W3-03: Sandbox test coverage gaps

## Intent
Fill the highest-risk coverage gaps in tests/sandbox/test_sandbox.py.
Not a full rewrite — targeted additions only.

## What to add

### 1. conftest.py for tests/sandbox/
Create tests/sandbox/conftest.py with a shared docker_client_mock
fixture returning a pre-configured (mock_client, mock_container) tuple.
mock_client.ping.return_value = True
mock_container.wait.return_value = {"StatusCode": 0}
mock_container.logs.return_value = [b"stdout", b""]
mock_client.containers.run.return_value = mock_container
This eliminates ~40 lines of repeated boilerplate across TestDockerSandbox.
Do NOT refactor existing tests to use it — new tests only.

### 2. OOM kill test
Container exits with StatusCode=137 (OOM kill).
Assert SandboxResult.exit_code == 137.
Assert SandboxResult.stderr contains "OOM" or similar — check what
docker_sandbox.py actually puts in stderr for non-zero exit codes first.

### 3. Docker timeout test
container.wait() raises docker.errors.APIError (the real SDK exception
for timeout, not a generic Exception).
Assert SandboxError raised with message referencing timeout.

### 4. Volume mounts test
SandboxConfig with mounts=["/host/path:/container/path"].
Assert containers.run called with correct volumes dict.
Read _build_volumes() in docker_sandbox.py before writing this test.

### 5. Network error test
containers.run() raises docker.errors.DockerException.
Assert SandboxError raised — not SandboxResult returned.

## Acceptance criteria
- 5 new tests added (or more if natural sub-cases emerge)
- conftest.py created with docker_client_mock fixture
- All existing 48 tests still pass
- New tests use the conftest fixture where appropriate

## Testing
This ticket IS the tests — no implementation changes.

## Constraints
- Touch only tests/sandbox/
- Do not refactor existing tests
- Read docker_sandbox.py before writing any test — don't assume
  behavior, verify it
