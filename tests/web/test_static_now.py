from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "now.js"


def test_now_view_registers_and_fetches_api() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.snodoViews.now" in source
    assert 'fetch("/api/now")' in source


def test_now_view_uses_safe_dom_apis() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "textContent" in source


def test_now_view_has_sections_and_fetch_error_handling() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "In flight" in source
    assert "Waiting on you" in source
    assert "The dashboard could not be reached." in source
    assert "catch (_)" in source
