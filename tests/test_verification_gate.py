"""The reduced local gate and the CI gate must not drift apart silently (Refs #87).

``uv run pytest tests/`` runs with pyproject's ``addopts`` (deselects e2e); CI
runs with ``-m ""`` plus a coverage floor and a patch-coverage script. Those two
commands look alike and are not the same gate. The fast local loop is kept, but
the difference must be *stated* — on the same screen the reduced run prints, and
in CONTRIBUTING.

These tests hold the three artifacts (pyproject.toml, .github/workflows/ci.yml,
CONTRIBUTING.md) and the runtime notice (tests/verification_gate.py, wired in
tests/conftest.py) together. If CI changes what it runs, or the local default
changes, the divergence must still be described or this fails.

Canary (#58): the divergence checker is exercised with a notice that omits a
CI-only fact and asserted to report the gap, so the guard can actually fail.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import yaml

from tests.conftest import _e2e_excluded_by_markexpr
from tests.verification_gate import (
    CI_COV_FAIL_UNDER,
    CI_PATCH_COVERAGE_SCRIPT,
    CI_PYTEST_MARKER,
    LOCAL_ADDOPTS_MARKER,
    NOTICE_SENTINEL,
    build_notice,
    divergence_statement_gaps,
)

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
PROBE_PATH = "tests/gate_notice_probe"


# --- parsing the artifacts --------------------------------------------------


def _local_addopts() -> str:
    with open(PYPROJECT, "rb") as f:
        data = tomllib.load(f)
    return data["tool"]["pytest"]["ini_options"]["addopts"]


def _ci_test_run_text() -> str:
    data = yaml.safe_load(CI_WORKFLOW.read_text())
    steps = data["jobs"]["test"]["steps"]
    return "\n".join(
        step.get("run", "") for step in steps if isinstance(step, dict)
    )


def _ci_clears_marker_filter(run_text: str) -> bool:
    return '-m ""' in run_text or "-m ''" in run_text


def _ci_cov_floor(run_text: str) -> str | None:
    import re

    match = re.search(r"--cov-fail-under=(\d+)", run_text)
    return match.group(1) if match else None


# --- the divergence exists, and the two sides' facts agree with the module ---


def test_local_default_deselects_e2e():
    """The fast local loop is intact: pyproject addopts deselect e2e."""
    assert "not e2e" in _local_addopts()
    assert LOCAL_ADDOPTS_MARKER == "not e2e"


def test_ci_runs_the_full_suite_and_the_extra_gates():
    """CI clears the marker filter (runs e2e) and adds coverage + patch coverage."""
    run_text = _ci_test_run_text()
    assert _ci_clears_marker_filter(run_text), (
        "CI must clear the marker filter (-m \"\") so it runs e2e; if it no "
        "longer does, the local/CI divergence has changed and must be re-stated."
    )
    assert _ci_cov_floor(run_text) is not None, "CI must enforce a coverage floor"
    assert "enforce_patch_coverage" in run_text, (
        "CI must run the patch-coverage script; if it no longer does, the "
        "divergence has changed and must be re-stated."
    )


def test_module_constants_match_the_files_they_describe():
    """The notice's facts equal ci.yml's facts (the drift lock, #87).

    If CI's coverage floor or patch-coverage path changes and this module is not
    updated with it, the notice would state a stale fact — this fails first.
    """
    run_text = _ci_test_run_text()
    assert _ci_cov_floor(run_text) == CI_COV_FAIL_UNDER, (
        f"ci.yml enforces --cov-fail-under={_ci_cov_floor(run_text)} but "
        f"verification_gate.CI_COV_FAIL_UNDER={CI_COV_FAIL_UNDER!r}. Update the "
        "notice so the stated difference matches the gate that decides."
    )
    assert CI_PATCH_COVERAGE_SCRIPT in run_text
    assert CI_PYTEST_MARKER == "" and _ci_clears_marker_filter(run_text)
    assert "not e2e" in _local_addopts() and LOCAL_ADDOPTS_MARKER == "not e2e"


# --- the notice states every CI-only fact -----------------------------------


def test_notice_states_every_ci_only_gate():
    """The reduced-run notice names everything CI runs that the local run did not."""
    run_text = _ci_test_run_text()
    notice = build_notice(124)
    gaps = divergence_statement_gaps(
        notice,
        cov_floor=_ci_cov_floor(run_text) or "",
        patch_script=CI_PATCH_COVERAGE_SCRIPT,
    )
    assert not gaps, "reduced-run notice fails to state: " + "; ".join(gaps)


def test_decision_table_for_marker_filter():
    """e2e is reported 'excluded' only when the marker filter drops e2e.

    ``-m ""``/CI (empty) and ``-m e2e`` (selects e2e) must read as NOT excluded;
    the default ``not e2e`` and combinations that drop e2e must read as excluded.
    """
    def cfg(markexpr: str) -> SimpleNamespace:
        return SimpleNamespace(
            getoption=lambda name, default=None: markexpr if name == "markexpr" else default
        )

    assert _e2e_excluded_by_markexpr(cfg("")) is False
    assert _e2e_excluded_by_markexpr(cfg("not e2e")) is True
    assert _e2e_excluded_by_markexpr(cfg("e2e")) is False
    assert _e2e_excluded_by_markexpr(cfg("e2e and not smoke")) is False
    assert _e2e_excluded_by_markexpr(cfg("not e2e and not slow")) is True
    assert _e2e_excluded_by_markexpr(cfg("slow")) is True  # drops e2e too
    assert _e2e_excluded_by_markexpr(cfg("not e2ex")) is False  # marker is e2e, not e2ex


# --- canary: the guard can fail (#58) ---------------------------------------


def test_divergence_guard_can_fail():
    """Canary (#58): a notice that omits a CI-only fact must be reported as a gap."""
    run_text = _ci_test_run_text()
    cov_floor = _ci_cov_floor(run_text) or "75"

    silence = "This run finished. All tests passed."
    gaps = divergence_statement_gaps(
        silence, cov_floor=cov_floor, patch_script=CI_PATCH_COVERAGE_SCRIPT
    )
    assert gaps, "a notice stating nothing about the reduction must be flagged"

    # A notice that forgot the coverage floor is still a gap.
    no_cov = build_notice(124).replace(f"--cov-fail-under={cov_floor}", "--cov")
    assert divergence_statement_gaps(
        no_cov, cov_floor=cov_floor, patch_script=CI_PATCH_COVERAGE_SCRIPT
    ), "a notice missing the coverage floor must be flagged"


# --- wiring: the real local run prints the notice ---------------------------


def _run_pytest(extra_args: list[str]) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", PROBE_PATH, *extra_args,
         "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc


def test_reduced_local_run_announces_the_reduction():
    """The documented local invocation (default addopts) prints the notice."""
    proc = _run_pytest([])
    assert NOTICE_SENTINEL in proc.stdout, (
        "the reduced local run did not announce that e2e was deselected:\n"
        + proc.stdout
        + proc.stderr
    )


def test_full_suite_run_does_not_announce():
    """Clearing the filter (-m "") runs e2e and prints no reduction notice.

    The same wiring that prints the notice must stay silent here; the e2e-only
    and combination cases are covered by the decision table above, so a single
    real subprocess covers the "hook stays quiet" branch of the wiring.
    """
    proc = _run_pytest(["-m", ""])
    assert NOTICE_SENTINEL not in proc.stdout, (
        "-m \"\" runs the full suite; it must not announce a reduction:\n"
        + proc.stdout
    )
    assert "1 passed" in proc.stdout or "2 passed" in proc.stdout
