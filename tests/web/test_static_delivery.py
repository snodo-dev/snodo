from pathlib import Path
import json
import re
import shutil
import subprocess


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


def test_delivery_chart_thins_date_labels_to_fit() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise chart label sampling"

    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"function updateDateLabels\(chart\) \{.*?\n  \}", source, re.DOTALL
    )
    assert match is not None
    harness = r"""
const updateDateLabels = new Function(`${process.argv[1]}; return updateDateLabels`)();
function visibleDates(count, width) {
  const labels = Array.from({ length: count }, (_, index) => ({ hidden: false, index }));
  updateDateLabels({ clientWidth: width, querySelectorAll: () => labels });
  return labels.filter((label) => !label.hidden).map((label) => label.index);
}
const seven = visibleDates(7, 240);
const thirty = visibleDates(30, 2400);
console.log(JSON.stringify({ seven, thirty }));
"""
    result = subprocess.run(
        [node, "-e", harness, match.group(0)],
        check=True,
        capture_output=True,
        text=True,
    )
    visible = json.loads(result.stdout)
    assert visible["seven"] == list(range(7))
    assert len(visible["thirty"]) < 30
    assert visible["thirty"][0] == 0
    assert visible["thirty"][-1] == 29
