from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "plans.js"


def test_plans_view_registers_fetches_and_uses_safe_dom() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.snodoViews.plans" in source
    assert 'fetch("/api/plans")' in source
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "textContent" in source


def test_plans_view_shows_delivery_labels_and_handles_fetch_failure() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"not in main"' in source
    assert '"no branch on record"' in source
    assert "catch (_)" in source
    assert "The plans dashboard could not be reached." in source
