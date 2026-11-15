from pathlib import Path
import re


STATIC = Path(__file__).parents[2] / "snodo" / "web" / "static"
SCRIPT = STATIC / "now.js"
STYLESHEET = STATIC / "now.css"


def test_now_view_classes_have_styles() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLESHEET.read_text(encoding="utf-8")
    emitted = set(re.findall(r'"(now-[a-z-]+)"', script))
    emitted.update(re.findall(r'"(now-[a-z-]+) ', script))
    emitted.update({"now-status-running", "now-status-completed", "now-status-pending", "now-status-blocked", "now-status-errored", "now-status-in-progress", "now-status-unmerged"})
    rules = set(re.findall(r"\.([a-z][a-z0-9-]*)", css))
    assert emitted <= rules


def test_now_styles_cover_all_task_statuses() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    for status in ("running", "completed", "pending", "blocked", "errored", "in-progress", "unmerged"):
        assert f".now-status-{status}" in css


def test_now_styles_use_only_local_theme_tokens() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert "@import" not in css
    assert not re.search(r"https?://|url\(", css, re.IGNORECASE)


def test_now_script_uses_safe_dom_apis() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script


def test_every_emitted_now_class_has_a_css_rule() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    css = STYLESHEET.read_text(encoding="utf-8")
    emitted = set(re.findall(r'"(now-[a-z-]+)"', script))
    emitted.update(re.findall(r'"(now-[a-z-]+) ', script))
    emitted.update(re.findall(r'"(now-[a-z-]+)"', script))
    emitted.update({"now-task-button", "now-task-panel", "now-task-panel-header", "now-task-panel-id", "now-task-panel-close", "now-task-panel-location", "now-task-panel-job", "now-task-panel-elapsed", "now-task-panel-loading", "now-task-selected", "now-flight-layout"})
    rules = set(re.findall(r"\.([a-z][a-z0-9-]*)", css))
    assert emitted <= rules


def test_now_task_spec_styles_use_dashboard_tokens_and_scroll() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert ".now-task-panel-spec" in css
    assert "overflow: auto" in css
    assert "var(--mono)" in css
    assert "var(--muted)" in css


def test_now_plan_rows_hide_bar_and_summary_on_narrow_screens() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")

    assert "@media (max-width: 640px)" in css
    assert ".now-wave-segments, .now-plan-summary { display:none; }" in css
    assert ".now-plan-title .now-expand { grid-template-columns:1rem minmax(0,1fr) auto; }" in css
