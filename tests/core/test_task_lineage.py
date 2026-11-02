from snodo.core.task_lineage import belongs_to_recovery_lineage, same_task_lineage


def test_belongs_to_recovery_lineage_accepts_root_and_positive_recovery():
    assert belongs_to_recovery_lineage("task", "task")
    assert belongs_to_recovery_lineage("task_fix_2", "task")
    assert not belongs_to_recovery_lineage("task_fix_0", "task")
    assert not belongs_to_recovery_lineage("other_fix_1", "task")


def test_same_task_lineage_matches_both_recovery_directions():
    assert same_task_lineage("task", "task_fix_1")
    assert same_task_lineage("task_fix_1", "task")
    assert same_task_lineage("task_fix_1", "task_fix_2")
    assert not same_task_lineage("task", "other")
