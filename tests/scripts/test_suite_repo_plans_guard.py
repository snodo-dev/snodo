"""The suite-repository plans tripwire detects writes and edits."""

from tests.conftest import _plans_dir_state


def test_plans_guard_detects_new_plan_file(tmp_path):
    before = _plans_dir_state(tmp_path)
    plan = tmp_path / ".snodo" / "plans" / "build_feature_x" / "plan.yml"
    plan.parent.mkdir(parents=True)
    plan.write_text("intent: build feature X\n")

    assert before != _plans_dir_state(tmp_path)


def test_plans_guard_detects_modified_plan_file(tmp_path):
    plan = tmp_path / ".snodo" / "plans" / "build_user_profile_page" / "plan.yml"
    plan.parent.mkdir(parents=True)
    plan.write_text("intent: original\n")
    before = _plans_dir_state(tmp_path)
    plan.write_text("intent: changed\n")

    assert before != _plans_dir_state(tmp_path)
