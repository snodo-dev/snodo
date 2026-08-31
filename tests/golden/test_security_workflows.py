"""Security-scanning workflow gates.

FILE: tests/golden/test_security_workflows.py

Guards the four security gates added to CI plus the publish-step change:

- CodeQL (codeql.yml) runs on push to main and on pull requests.
- OpenSSF Scorecard (scorecard.yml) publishes results so the badge endpoint
  works (publish_results: true).
- ci.yml runs pip-audit (fails the build on a known CVE in current deps) and
  zizmor (lints the workflow files themselves).
- release.yml publishes via pypa/gh-action-pypi-publish with attestations: true
  (PEP 740 provenance) instead of `uv publish`.

Every third-party action is pinned to a commit SHA with the tag as a trailing
comment — Scorecard checks this and it is the correct posture regardless.
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"

#: Third-party actions that must be pinned to a commit SHA (not a tag).
#: `actions/checkout` is first-party GitHub but is pinned too, for consistency.
_THIRD_PARTY_ACTIONS = (
    "actions/checkout",
    "actions/upload-artifact",
    "astral-sh/setup-uv",
    "codecov/codecov-action",
    "github/codeql-action",
    "ossf/scorecard-action",
    "pypa/gh-action-pip-audit",
    "pypa/gh-action-pypi-publish",
)


def _workflow(name: str) -> str:
    return (WORKFLOWS_DIR / name).read_text(encoding="utf-8")


def test_codeql_runs_on_main_and_prs():
    text = _workflow("codeql.yml")
    assert "github/codeql-action/init" in text
    assert "github/codeql-action/analyze" in text
    assert "languages: ${{ matrix.language }}" in text
    assert "python" in text
    assert "branches: [main]" in text
    assert "pull_request" in text


def test_scorecard_publishes_results():
    text = _workflow("scorecard.yml")
    assert "ossf/scorecard-action" in text
    assert "publish_results: true" in text
    assert "results_format: sarif" in text
    assert "github/codeql-action/upload-sarif" in text
    assert "id-token: write" in text


def test_ci_runs_pip_audit_and_zizmor():
    text = _workflow("ci.yml")
    assert "pip-audit" in text
    assert "requirements-audit.txt" in text
    assert "zizmor" in text


def test_release_publishes_with_attestations():
    text = _workflow("release.yml")
    assert "pypa/gh-action-pypi-publish" in text
    assert "attestations: true" in text
    assert "uv publish" not in text


def test_every_third_party_action_is_pinned_to_a_sha():
    for name in sorted(WORKFLOWS_DIR.glob("*.yml")):
        text = name.read_text(encoding="utf-8")
        for action in _THIRD_PARTY_ACTIONS:
            if action not in text:
                continue
            for line in text.splitlines():
                stripped = line.strip()
                if not stripped.startswith("uses:"):
                    continue
                uses = stripped.split("uses:", 1)[1].strip()
                if not uses.startswith(action + "@"):
                    continue
                ref = uses.split("@", 1)[1].split()[0]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), (
                    f"{name.name}: {action} is not pinned to a commit SHA "
                    f"(got {ref!r})"
                )
                assert "# v" in uses, (
                    f"{name.name}: {action} pin is missing the tag comment"
                )
