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
    emitted.update({"now-task-button", "now-task-panel", "now-task-panel-header", "now-task-panel-id", "now-task-panel-close", "now-task-panel-location", "now-task-panel-job", "now-task-panel-elapsed", "now-task-panel-loading", "now-task-selected", "now-flight-layout", "now-flight-layout-open"})
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


def test_now_task_rows_stay_single_line_and_respect_reduced_motion() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert "white-space:nowrap" in css
    assert "[aria-selected=\"true\"]" in css
    assert "@media (prefers-reduced-motion:reduce) { .now-task-icon-running" in css
    assert "@media (max-width:640px)" in css
    assert ".now-task-button .now-elapsed { display:none; }" in css


def test_now_panel_uses_wide_sticky_column_and_stacks_below_1080px() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert "minmax(19rem, 26.5rem)" in css
    assert ".now-task-panel { position: sticky" in css
    assert "@media (max-width: 1080px)" in css
    assert ".now-task-panel { position: static; }" in css
    assert ".now-task-panel-facts" in css
    assert ".now-task-panel-actions" in css


def test_now_refetch_spinner_respects_reduced_motion() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert ".now-spinner" in css
    assert "@media (prefers-reduced-motion: reduce) { .now-spinner { animation: none; } }" in css
    assert ".now-refreshing .now-counts, .now-refreshing .now-flight-layout" in css


def test_now_flight_layout_transitions_from_full_width_and_respects_reduced_motion() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert ".now-flight-layout { display: grid; grid-template-columns: minmax(0, 1fr);" in css
    assert ".now-flight-layout-open { grid-template-columns: minmax(0, 1fr) minmax(19rem, 26.5rem); }" in css
    assert "@media (prefers-reduced-motion: reduce) { .now-flight-layout { transition: none; } }" in css
    assert "transition: grid-template-columns 180ms ease" in css


def test_now_layout_transition_respects_reduced_motion() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")

    assert ".now-flight-layout { display: grid; grid-template-columns: minmax(0, 1fr);" in css
    assert ".now-flight-layout-open { grid-template-columns: minmax(0, 1fr) minmax(19rem, 26.5rem); }" in css
    assert "transition: grid-template-columns 180ms ease" in css
    assert "@media (prefers-reduced-motion: reduce) { .now-flight-layout { transition: none; } }" in css


def test_now_spec_progress_styles_use_theme_tokens_and_reduced_motion() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert ".now-task-panel-progress-track" in css
    assert ".now-task-panel-progress-fill" in css
    assert "var(--accent)" in css
    assert "@media (prefers-reduced-motion: reduce) { .now-task-panel-progress-fill { transition: none; } }" in css
