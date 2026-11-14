from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "delivery.js"


def test_delivery_view_registers_and_fetches_api() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "window.snodoViews.delivery" in source
    assert '"/api/delivery"' in source


def test_delivery_view_offers_supported_windows_and_safe_dom() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "[7, 30]" in source
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert '"n/a"' in source
    assert "textContent" in source


def test_delivery_view_handles_failed_fetch() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "catch (_)" in source
    assert "The delivery dashboard could not be reached." in source
