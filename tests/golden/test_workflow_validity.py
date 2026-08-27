"""The local suite validates every GitHub Actions workflow file.

FILE: tests/golden/test_workflow_validity.py (Fixes #74)

The gate that all other gates depend on is the CI workflow file itself, and it
had no canary: ci.yml was invalid YAML for several merges (the patch-coverage
step embedded unindented Python in a ``run: |`` block scalar), every CI run
failed at startup with no log and no test output, and nothing noticed — the
merge path ran its gates locally and no gate validated the file that defines
the gates.

This check parses every ``.github/workflows/*.yml`` as YAML and asserts it is
structurally a workflow. A broken workflow now fails at the branch instead of
silently disabling CI — the canary rule (#58) applied to the one gate that had
none.

Depth decision: parse + structural validation, no workflow-schema package.
PyYAML is already a dependency, and the structural checks below catch the
class of failure that actually occurred (a file that is not parseable YAML, or
a workflow whose shape cannot be interpreted). A full GitHub-Actions schema
validator is a heavier dependency and is not warranted for files we author.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

# GitHub Action step kinds that do not run a shell (they reference an action).
# (Unused here — a step is valid if it has 'uses' or 'run'.)


def _load_workflows() -> list:
    """Return (filename, parsed document) for every .yml/.yaml workflow file."""
    if not WORKFLOWS_DIR.is_dir():
        pytest.fail(f"No workflows directory at {WORKFLOWS_DIR} — CI is unguarded.")
    loaded = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")) + sorted(WORKFLOWS_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            pytest.fail(f"{path.name} is not valid YAML: {exc}")
        loaded.append((path.name, data))
    if not loaded:
        pytest.fail("No workflow files found under .github/workflows/ — CI is unguarded.")
    return loaded


def _workflow_trigger(data: dict):
    """Return the 'on' trigger mapping.

    GitHub Actions reads YAML 1.2, where ``on:`` is the string key ``"on"``.
    PyYAML is YAML 1.1 and parses the same token as the boolean ``True`` (YAML
    1.1 treats ``on``/``off`` as booleans). Both spellings are the same
    semantic trigger; a normalising helper keeps the structural check from
    tripping on a real workflow.
    """
    if "on" in data:
        return data["on"]
    if True in data:
        return data[True]
    return None


def _assert_workflow_shape(name: str, data) -> None:
    """Assert *data* is structurally a GitHub Actions workflow.

    Structural, not schema-complete: a workflow is a mapping with an ``on``
    trigger and a non-empty ``jobs`` map whose jobs each declare ``runs-on``
    and at least one step that runs a command (``run``) or references an action
    (``uses``). This catches the failure class that disabled CI: a file that
    parses as YAML but is not interpretable as a workflow.
    """
    assert isinstance(data, dict), (
        f"{name}: workflow root must be a mapping, got {type(data).__name__}"
    )
    assert _workflow_trigger(data) is not None, (
        f"{name}: workflow is missing the 'on' trigger"
    )
    assert "jobs" in data, f"{name}: workflow is missing 'jobs'"
    jobs = data["jobs"]
    assert isinstance(jobs, dict) and jobs, (
        f"{name}: 'jobs' must be a non-empty mapping of job name -> job spec"
    )
    for job_name, job in jobs.items():
        assert isinstance(job, dict), (
            f"{name}: job '{job_name}' must be a mapping, got {type(job).__name__}"
        )
        assert "runs-on" in job, (
            f"{name}: job '{job_name}' is missing 'runs-on'"
        )
        steps = job.get("steps")
        assert isinstance(steps, list) and steps, (
            f"{name}: job '{job_name}' must have a non-empty 'steps' list"
        )
        for i, step in enumerate(steps):
            assert isinstance(step, dict), (
                f"{name}: job '{job_name}' step {i} must be a mapping"
            )
            assert "uses" in step or "run" in step, (
                f"{name}: job '{job_name}' step {i} does nothing — it has "
                "neither 'uses' nor 'run'"
            )


def test_every_workflow_is_valid_yaml_and_structural():
    """Every file under .github/workflows/ parses as YAML and is a workflow."""
    for name, data in _load_workflows():
        _assert_workflow_shape(name, data)


def test_workflow_validator_detects_broken_workflow():
    """Canary (#58): a malformed workflow must fail the validation, proving the
    gate can fail. Without this, a future broken workflow ships silently."""
    broken = {
        "name": "CI",
        # 'on' as a bare scalar instead of a mapping — structurally broken.
        "on": "push",
        "jobs": "not-a-mapping",
    }
    with pytest.raises(AssertionError):
        _assert_workflow_shape("canary.yml", broken)

    # A job with no steps is also structurally broken.
    jobless = {
        "on": {"push": {"branches": ["**"]}},
        "jobs": {"test": {"runs-on": "ubuntu-latest", "steps": []}},
    }
    with pytest.raises(AssertionError):
        _assert_workflow_shape("canary.yml", jobless)


def test_yaml_parse_detects_unindented_block():
    """Canary: a workflow file whose content is not parseable YAML must fail."""
    from yaml.scanner import ScannerError

    # The exact failure from #74: an unindented line inside a block scalar.
    bad_yaml = "on:\n  push:\n    branches: ['**']\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: |\n        uv run python\n"
    with pytest.raises(ScannerError):
        yaml.safe_load(bad_yaml)
