from types import SimpleNamespace

from snodo.cli.commands.plan_retry import spec_was_replaced


def test_changed_spec_starts_new_retry_lineage(tmp_path):
    task = tmp_path / ".snodo" / "plans" / "plan" / "wave_1" / "1.1_task_task.md"
    task.parent.mkdir(parents=True)
    task.write_text("new specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)

    assert spec_was_replaced(
        planner, "plan", "1.1_task", {"original_spec": "old specification"}
    )


def test_unchanged_spec_keeps_retry_lineage(tmp_path):
    task = tmp_path / ".snodo" / "plans" / "plan" / "wave_1" / "1.1_task_task.md"
    task.parent.mkdir(parents=True)
    task.write_text("same specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)

    assert not spec_was_replaced(
        planner, "plan", "1.1_task", {"original_spec": "same specification"}
    )
