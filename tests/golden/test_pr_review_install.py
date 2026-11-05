"""The review workflow installs Snodo without overriding its dependencies."""

import json
import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/pr-review.yml"
OLD_INSTALL_SCRIPT = """\
uv venv "$RUNNER_TEMP/pr-review-venv"
if [[ -n "$SNODO_VERSION" ]]; then
  uv pip install --python "$RUNNER_TEMP/pr-review-venv/bin/python" "snodo==$SNODO_VERSION" "litellm<1.106"
else
  uv pip install --python "$RUNNER_TEMP/pr-review-venv/bin/python" snodo "litellm<1.106"
fi
"""


def _install_script():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("name") == "Install released snodo"
    ]
    assert len(steps) == 1, "Expected exactly one Install released snodo step"
    return steps[0]["run"]


def _run_install(script, tmp_path, version):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "uv-calls.jsonl"
    uv = bin_dir / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        + dedent("""\
            import json
            import os
            import sys
            from pathlib import Path

            with Path(os.environ["UV_CALL_RECORD"]).open("a") as output:
                output.write(json.dumps(sys.argv[1:]) + "\\n")
            if sys.argv[1] == "venv":
                Path(sys.argv[2]).mkdir(parents=True)
            """),
        encoding="utf-8",
    )
    uv.chmod(0o755)
    runner_temp = tmp_path / "runner temp"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "RUNNER_TEMP": str(runner_temp),
        "UV_CALL_RECORD": str(record),
    }
    env.pop("SNODO_VERSION", None)
    if version is not None:
        env["SNODO_VERSION"] = version
    subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert (runner_temp / "pr-review-venv").is_dir()
    calls = [json.loads(line) for line in record.read_text().splitlines()]
    return calls, runner_temp


def _assert_install_contract(calls, runner_temp, version):
    venv = runner_temp / "pr-review-venv"
    requirement = f"snodo=={version}" if version else "snodo"
    expected = [
        ["venv", str(venv)],
        ["pip", "install", "--python", str(venv / "bin/python"), requirement],
    ]
    assert calls == expected, (
        f"Expected only the selected Snodo requirement {requirement!r}; got {calls!r}"
    )


@pytest.mark.parametrize("version", ["0.28.2", "0.26.0", "", None])
def test_review_installs_only_selected_snodo(tmp_path, version):
    calls, runner_temp = _run_install(_install_script(), tmp_path, version)
    _assert_install_contract(calls, runner_temp, version)


@pytest.mark.parametrize("version", ["0.28.2", "0.26.0", "", None])
def test_install_contract_rejects_old_dependency_cap(tmp_path, version):
    """Canary: the former workflow must fail the same installation contract."""
    calls, runner_temp = _run_install(OLD_INSTALL_SCRIPT, tmp_path, version)
    requirement = f"snodo=={version}" if version else "snodo"
    assert calls[1][4:] == [requirement, "litellm<1.106"]
    with pytest.raises(AssertionError, match=r"litellm<1\.106"):
        _assert_install_contract(calls, runner_temp, version)
