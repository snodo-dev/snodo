from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "gates.js"


def test_gates_view_registers_and_fetches_api() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.snodoViews.gates" in source
    assert '"/api/gates"' in source


def test_gates_view_offers_windows_and_safe_dom() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "[7, 21, 30]" in source
    assert 'let selectedDays = 21' in source
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert '"The record"' in source
    assert '"unknown"' in source
    assert "textContent" in source


def test_gates_view_handles_failed_fetch_and_empty_records() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "catch (_)" in source
    assert "The gates dashboard could not be reached." in source
    assert "No gate send-backs in this window." in source
