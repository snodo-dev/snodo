"""Tests for local Now counters."""

from datetime import datetime, timezone

from snodo.web.now_counters import project_now_counters, UNAVAILABLE


def test_missing_project_is_unavailable(tmp_path):
    result = project_now_counters(tmp_path / "missing", datetime.now(timezone.utc))
    assert result["tasks"] == UNAVAILABLE
    assert result["recons"] == UNAVAILABLE
