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
