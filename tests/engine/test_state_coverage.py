"""Direct-import tests for snodo.engine.state helpers.

Covers lines 54-70 (_build_audit_results body) and 91-92 (_branch_exists exception
path) — these are only covered when imported from snodo.engine.state itself, not via
the re-exported copies in snodo.engine.loop.
"""

from unittest.mock import MagicMock

from snodo.core.interfaces import ValidatorResult
from snodo.engine.state import (
    _branch_exists,
    _build_audit_results,
    _slugify,
    _task_branch_name,
)

# ---------------------------------------------------------------------------
# _build_audit_results
# ---------------------------------------------------------------------------

class TestBuildAuditResults:
    def _make_result(self, vid="v1", severity="pass", justification="ok"):
        return ValidatorResult(
            validator_id=vid, severity=severity, justification=justification
        )

    def _make_validator(self, vid="v1", severity_cap=None):
        v = MagicMock()
        v.validator_id = vid
        v.severity_cap = severity_cap
        return v

    def test_empty_inputs(self):
        result = _build_audit_results([], [])
        assert result == []

    def test_basic_pass_result(self):
        r = self._make_result(vid="sec", severity="pass")
        v = self._make_validator(vid="sec")
        entries = _build_audit_results([v], [r])
        assert len(entries) == 1
        assert entries[0]["validator_id"] == "sec"
        assert entries[0]["severity"] == "pass"
        assert entries[0]["justification"] == "ok"
        assert "severity_at_cap" not in entries[0]

    def test_severity_at_cap_flagged(self):
        """When a cap actually fired (id in cap_originals) → flag set."""
        cap = MagicMock()
        cap.value = "warn"
        v = self._make_validator(vid="v1", severity_cap=cap)
        r = self._make_result(vid="v1", severity="warn")
        entries = _build_audit_results([v], [r], cap_originals={"v1": "blocker"})
        assert entries[0]["severity_at_cap"] is True
        assert entries[0]["severity_original"] == "blocker"

    def test_no_cap_no_flag(self):
        """No severity_cap → severity_at_cap not added."""
        v = self._make_validator(vid="v1", severity_cap=None)
        r = self._make_result(vid="v1", severity="blocker")
        entries = _build_audit_results([v], [r])
        assert "severity_at_cap" not in entries[0]

    def test_severity_cap_mismatch_no_flag(self):
        """severity_cap set but result severity doesn't match cap → no flag."""
        cap = MagicMock()
        cap.value = "warn"
        v = self._make_validator(vid="v1", severity_cap=cap)
        r = self._make_result(vid="v1", severity="pass")  # doesn't match "warn"
        entries = _build_audit_results([v], [r])
        assert "severity_at_cap" not in entries[0]

    def test_uncapped_at_cap_value_no_flag(self):
        """Genuine warn under a warn cap is not flagged as capped.

        severity_at_cap must mean a cap actually fired (the id appears in
        cap_originals), not merely that the result's severity equals the cap
        value.
        """
        cap = MagicMock()
        cap.value = "warn"
        v = self._make_validator(vid="v1", severity_cap=cap)
        r = self._make_result(vid="v1", severity="warn")
        entries = _build_audit_results([v], [r])
        assert "severity_at_cap" not in entries[0]
        assert "severity_original" not in entries[0]

    def test_more_results_than_validators_no_crash(self):
        """Extra results beyond validators list length skip cap check gracefully."""
        v = self._make_validator(vid="v1")
        r1 = self._make_result(vid="v1", severity="pass")
        r2 = self._make_result(vid="v2", severity="warn")
        entries = _build_audit_results([v], [r1, r2])
        assert len(entries) == 2
        assert "severity_at_cap" not in entries[1]

    def test_multiple_results_mixed_caps(self):
        """Multiple results: first capped, second not capped."""
        cap = MagicMock()
        cap.value = "warn"
        v1 = self._make_validator(vid="v1", severity_cap=cap)
        v2 = self._make_validator(vid="v2", severity_cap=None)
        r1 = self._make_result(vid="v1", severity="warn")
        r2 = self._make_result(vid="v2", severity="blocker")
        entries = _build_audit_results(
            [v1, v2], [r1, r2], cap_originals={"v1": "blocker"}
        )
        assert entries[0]["severity_at_cap"] is True
        assert "severity_at_cap" not in entries[1]

    def test_misordered_validators_pairs_by_id(self):
        """Results pair to validators by id, not by list position."""
        cap = MagicMock()
        cap.value = "warn"
        v1 = self._make_validator(vid="v1", severity_cap=cap)
        v2 = self._make_validator(vid="v2", severity_cap=None)
        r1 = self._make_result(vid="v1", severity="warn")
        r2 = self._make_result(vid="v2", severity="pass")
        # Misordered: v1 (capped) is second in the list, its result is first.
        entries = _build_audit_results(
            [v2, v1], [r1, r2], cap_originals={"v1": "blocker"}
        )
        assert entries[0]["validator_id"] == "v1"
        assert entries[0]["severity_at_cap"] is True
        assert entries[1]["validator_id"] == "v2"
        assert "severity_at_cap" not in entries[1]


# ---------------------------------------------------------------------------
# _branch_exists
# ---------------------------------------------------------------------------

class TestBranchExists:
    def test_branch_present(self):
        git_mcp = MagicMock()
        git_mcp.repo.heads = ["main", "task/t1/do-thing"]
        assert _branch_exists(git_mcp, "main") is True

    def test_branch_absent(self):
        git_mcp = MagicMock()
        git_mcp.repo.heads = ["main"]
        assert _branch_exists(git_mcp, "other-branch") is False

    def test_exception_returns_false(self):
        """Any exception accessing git_mcp.repo.heads → returns False."""
        git_mcp = MagicMock()
        type(git_mcp.repo).heads = property(lambda self: (_ for _ in ()).throw(RuntimeError("no git")))
        assert _branch_exists(git_mcp, "main") is False


# ---------------------------------------------------------------------------
# _slugify / _task_branch_name (sanity checks, ensure module lines load)
# ---------------------------------------------------------------------------

def test_slugify_basic():
    assert _slugify("Add feature X") == "add-feature-x"

def test_slugify_max_words():
    slug = _slugify("one two three four five six seven", max_words=3)
    assert slug == "one-two-three"

def test_task_branch_name():
    name = _task_branch_name("t42", "implement login flow now")
    assert name.startswith("task/t42/")
