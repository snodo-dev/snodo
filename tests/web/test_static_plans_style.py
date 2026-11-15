from pathlib import Path
import re


STATIC = Path(__file__).parents[2] / "snodo" / "web" / "static"
SCRIPT = STATIC / "plans.js"
STYLESHEET = STATIC / "plans.css"


PLANS_CLASSES = {
    "plans-filter",
    "plans-filter-select",
    "plans-empty",
    "plans-table",
    "plans-table-wrap",
    "plans-name",
    "plans-project",
    "plans-tasks",
    "plans-updated",
    "plans-tag",
    "plans-tag-running",
    "plans-tag-main",
    "plans-tag-not-main",
    "plans-tag-no-branch",
    "view-message",
}


def test_plans_classes_have_styles() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert all(class_name in script for class_name in PLANS_CLASSES)
    assert all(
        re.search(rf"\.{re.escape(class_name)}(?:\W|$)", stylesheet)
        for class_name in PLANS_CLASSES
    )


def test_progress_and_delivery_states_are_styled() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert ".plans-table progress {" in stylesheet
    assert "progress::-webkit-progress-value" in stylesheet
    assert "progress::-moz-progress-bar" in stylesheet
    for state in ("running", "main", "not-main", "no-branch"):
        assert f".plans-tag-{state}" in stylesheet


def test_styles_use_only_local_theme_tokens() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet)
    assert "@import" not in stylesheet
    assert not re.search(r"https?://|url\(", stylesheet)


def test_plans_script_keeps_safe_dom_construction() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "createElement" in script
    assert "textContent" in script
