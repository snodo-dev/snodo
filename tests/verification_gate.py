"""Single source of truth for the local-vs-CI verification-gate divergence (Refs #87).

The documented local check — ``uv run pytest tests/`` — runs with pyproject's
``[tool.pytest.ini_options].addopts``, which deselect the ``e2e`` marker (the
fast loop: roughly 47s vs 149s). CI runs the SAME suite with the marker filter
cleared (``-m ""``) plus a coverage floor and a patch-coverage script. The two
commands look alike and are not the same gate: a contributor could pass the
documented local check and then be surprised by a CI failure that the reduced
command cannot even see — an e2e test that had been failing on the base all
along.

The fast local loop is deliberate and kept. The defect was that it was silent:
nothing in the reduced run's output said a class of tests had been skipped, or
that what just passed is not the thing that will judge it.

This module states, in one place, what the reduced gate skips and what the full
CI gate adds, so that:

  * the local run can announce the reduction on the same screen
    (tests/conftest.py calls :func:`build_notice` from ``pytest_terminal_summary``), and
  * tests/test_verification_gate.py fails if pyproject.toml, ci.yml and
    CONTRIBUTING.md drift apart again without the difference still being stated.

Keep these constants in lockstep with the files they describe; the guard test
enforces it. If CI changes what it runs, change it here and here, and the guard
keeps the notice honest.
"""

from __future__ import annotations

E2E_MARKER = "e2e"

# The reduced local gate: pyproject addopts deselect e2e for a fast loop.
LOCAL_ADDOPTS_MARKER = "not e2e"

# The gate that decides (CI): marker filter cleared, plus coverage + patch coverage.
CI_PYTEST_MARKER = ""  # -m "" -> run everything, e2e included
CI_COV_FAIL_UNDER = "75"  # CI's --cov-fail-under value (do not change per #87)
CI_PATCH_COVERAGE_SCRIPT = "scripts/enforce_patch_coverage.py"

# Commands the notice prints so a contributor can reproduce the CI test gate.
CI_FULL_TEST_COMMAND = (
    'uv run pytest tests/ -m "" -n auto --tb=short --timeout=60 '
    "--cov --cov-report=term-missing "
    f"--cov-fail-under={CI_COV_FAIL_UNDER}"
)
CI_PATCH_COVERAGE_COMMAND = f"uv run python {CI_PATCH_COVERAGE_SCRIPT}"

# A short sentinel the notice carries; the guard test keys on it.
NOTICE_SENTINEL = "REDUCED LOCAL GATE"


def build_notice(deselected: int | None) -> str:
    """Return the terminal notice announcing this run was the reduced local gate.

    Names the ``e2e`` suite that was skipped, states plainly that this is NOT
    the gate CI applies, and lists exactly what CI runs that did not run here —
    the e2e tests, the coverage floor, and the patch-coverage check — together
    with the commands that reproduce the full gate. ``deselected`` is the number
    of e2e tests skipped, or ``None`` when it cannot be determined.
    """
    if deselected is None:
        skipped = "the end-to-end (e2e) suite"
        skipped_item = "1. the e2e suite deselected above (not part of this result),"
    else:
        skipped = f"{deselected} e2e test(s)"
        skipped_item = f"1. the {deselected} e2e tests deselected above (not part of this result),"

    bar = "=" * 78
    return "\n".join(
        [
            "",
            bar,
            f"  {NOTICE_SENTINEL} — this is NOT the gate CI runs",
            bar,
            f"  {skipped} were deselected by the default marker filter",
            f"  `-m {LOCAL_ADDOPTS_MARKER!r}` in pyproject.toml [tool.pytest.ini_options].addopts.",
            "",
            "  You just ran the FAST LOCAL loop. It is not the check that decides. CI",
            "  runs this same suite with the marker filter cleared, plus gates that",
            "  never ran here:",
            f"    {skipped_item}",
            f"    2. coverage: --cov-fail-under={CI_COV_FAIL_UNDER} (and the coverage report),",
            f"    3. patch coverage: {CI_PATCH_COVERAGE_SCRIPT}.",
            "",
            "  A clean local run does NOT mean CI is green. Run the gate that decides:",
            f"    {CI_FULL_TEST_COMMAND}",
            f"    {CI_PATCH_COVERAGE_COMMAND}",
            "",
            "  See CONTRIBUTING.md -> \"Testing and checks\".  (Refs #87)",
            bar,
        ]
    )


def divergence_statement_gaps(
    notice: str,
    *,
    cov_floor: str,
    patch_script: str,
    e2e_marker: str = E2E_MARKER,
) -> list[str]:
    """Return the CI-only facts a notice fails to state (empty == all stated).

    The guard test feeds this a notice built from this module's constants and
    the facts it parsed independently out of ``ci.yml``. Any CI-only element the
    notice does not name is returned, so a divergence that has stopped being
    announced cannot ship silently.
    """
    gaps: list[str] = []
    if e2e_marker not in notice:
        gaps.append(f"notice does not name the deselected {e2e_marker!r} suite")
    if f"--cov-fail-under={cov_floor}" not in notice:
        gaps.append(
            f"notice does not state the CI coverage floor --cov-fail-under={cov_floor}"
        )
    if patch_script not in notice:
        gaps.append(f"notice does not mention the patch-coverage script {patch_script}")
    if '-m ""' not in notice and "-m ''" not in notice:
        gaps.append('notice does not show the full-suite command that clears the marker filter (-m "")')
    return gaps
