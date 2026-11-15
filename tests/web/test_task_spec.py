from pathlib import Path

from snodo.web.task_spec import MAX_SPEC_BYTES, TRUNCATION_MARKER, read_task_spec


def _spec(project: Path, area: str, plan: str, wave: str, task: str, text: str) -> Path:
    path = project / ".snodo" / area / plan / wave / f"{task}_task.md"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_reads_task_spec_from_live_plan_wave(tmp_path: Path) -> None:
    _spec(tmp_path, "plans", "plan-a", "wave_2", "task-a", "# Ticket\n")
    assert read_task_spec(tmp_path, "plan-a", "task-a") == "# Ticket\n"


def test_returns_none_for_missing_or_unreadable_spec(tmp_path: Path) -> None:
    assert read_task_spec(tmp_path, "missing", "task") is None
    _spec(tmp_path, "plans", "plan", "wave_1", "other", "text")
    assert read_task_spec(tmp_path, "plan", "task") is None
    path = _spec(tmp_path, "plans", "plan", "wave_2", "bad", "")
    path.write_bytes(b"\xff")
    assert read_task_spec(tmp_path, "plan", "bad") is None


def test_rejects_unsafe_names(tmp_path: Path) -> None:
    outside = tmp_path.parent / "task_task.md"
    outside.write_text("outside", encoding="utf-8")
    for bad in ("", "..", "../plan", "a/b", "a\\b", "x" * 129):
        assert read_task_spec(tmp_path, bad, "task") is None
        assert read_task_spec(tmp_path, "plan", bad) is None


def test_truncates_oversized_spec(tmp_path: Path) -> None:
    _spec(tmp_path, "plans", "plan", "wave_1", "task", "x" * (MAX_SPEC_BYTES + 1))
    result = read_task_spec(tmp_path, "plan", "task")
    assert result == "x" * MAX_SPEC_BYTES + TRUNCATION_MARKER


def test_reads_archived_plan_when_not_live(tmp_path: Path) -> None:
    _spec(tmp_path, "plans-archive", "old-plan", "wave_3", "task", "archived")
    assert read_task_spec(tmp_path, "old-plan", "task") == "archived"
