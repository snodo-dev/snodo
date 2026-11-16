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


def test_delivery_chart_uses_short_dates_and_preserves_full_bar_details() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'day.date.slice(5)' in source
    assert 'bar.title = `${day.date}: ${day.landed}`' in source
    assert 'bar.setAttribute("aria-label", `${day.date}: ${day.landed}`)' in source


def test_delivery_chart_thins_date_labels_even_on_wide_screens() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "const maxLabels = labels.length > 7 ? 6 : labels.length" in source
    assert "const capacity = Math.min(widthCapacity, maxLabels)" in source
    assert "const visible = new Set(Array.from({ length: capacity }" in source
    assert "label.hidden = !visible.has(index)" in source
    assert 'container.replaceChildren(render(data));\n      observeDateLabels(container);' in source
    assert "updateDateLabels(chart);" in source
    assert "new ResizeObserver(() => updateDateLabels(chart))" in source
