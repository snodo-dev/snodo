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

    assert "[7, 30, 90]" in source
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



def test_delivery_chart_label_sampling_is_data_based_for_7_30_and_90_days() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise chart label sampling"

    source = SCRIPT.read_text(encoding="utf-8")
    match = re.search(
        r"function updateDateLabels\(chart\) \{.*?\n  \}", source, re.DOTALL
    )
    assert match is not None
    harness = r"""
const updateDateLabels = new Function(`${process.argv[1]}; return updateDateLabels`)();
function visibleDates(count) {
  const labels = Array.from({ length: count }, () => ({ hidden: false }));
  updateDateLabels({ querySelectorAll: () => labels });
  return labels.map((label, index) => label.hidden ? null : index).filter((index) => index !== null);
}
console.log(JSON.stringify([visibleDates(7), visibleDates(30), visibleDates(90)]));
"""
    result = subprocess.run(
        [node, "-e", harness, match.group(0)],
        check=True,
        capture_output=True,
        text=True,
    )
    seven, thirty, ninety = json.loads(result.stdout)
    assert seven == list(range(7))
    for count, visible in ((30, thirty), (90, ninety)):
        assert 7 <= len(visible) <= 9
        assert visible[0] == 0
        assert visible[-1] == count - 1
        assert all(right - left > 1 for left, right in zip(visible, visible[1:]))


def test_delivery_chart_labels_use_short_date_format_and_no_responsive_observers() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'day.date.slice(5)' in source
    assert "ResizeObserver" not in source
    assert 'addEventListener("resize"' not in source
    assert "innerHTML" not in source


def test_delivery_flow_cards_render_from_sample_payload() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise Delivery rendering"
    source = SCRIPT.read_text(encoding="utf-8")
    harness = r"""
class Node {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.attributes = {};
    this.style = { setProperty: (name, value) => { this.style[name] = value; } };
    this.dataset = {};
    this.textContent = "";
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this[name] = callback; }
  querySelector(selector) {
    if (selector === "strong") return this.children.find((child) => child.tagName === "strong");
    return null;
  }
  querySelectorAll(selector) {
    const className = selector.slice(1);
    return this.children.flatMap((child) => [
      ...(child.className === className ? [child] : []),
      ...child.querySelectorAll(selector),
    ]);
  }
}
global.document = {
  createElement: (tag) => new Node(tag),
  createDocumentFragment: () => new Node("fragment"),
};
const requested = [];
global.testEfficiency = 0.4;
global.fetch = async (url) => {
  requested.push(url);
  return { ok: true, json: async () => ({
    window_days: 7,
    totals: {
      landed: 10, median_flow_seconds: 3600, p85_flow_seconds: 7200,
      efficiency: global.testEfficiency, flow_load: 3, first_try_rate: 0.8,
      plans_not_in_main: 2,
      by_type: {
        feature: { landed: 6, share: 0.6, median_flow_seconds: 600, p85_flow_seconds: 1200 },
        defect: { landed: 2, share: 0.2, median_flow_seconds: 300, p85_flow_seconds: 900 },
        debt: { landed: 1, share: 0.1, median_flow_seconds: 180, p85_flow_seconds: 600 },
        risk: { landed: 1, share: 0.1, median_flow_seconds: null, p85_flow_seconds: null },
      },
      by_week: [
        { week_start: "2026-01-05", feature: 2, defect: 1, debt: 0, risk: 0 },
        { week_start: "2026-01-12", feature: 4, defect: 1, debt: 1, risk: 1 },
      ],
    }, by_day: [], projects: [],
  }) };
};
global.window = { snodoViews: {} };
const source = process.argv[1];
eval(source);
const container = new Node("main");
(async () => {
  await window.snodoViews.delivery(container);
  const all = (node) => [node, ...node.children.flatMap(all)];
  let nodes = all(container);
  const cards = nodes.filter((node) => node.className === "delivery-figure");
  const cardData = cards.map((card) => ({
    label: card.children[0].textContent,
    text: all(card).map((node) => node.textContent).join(" "),
    segments: all(card).filter((node) => node.className === "delivery-segment").map((node) => ({
      text: node.textContent, title: node.title,
    })),
    bars: all(card).filter((node) => node.className === "delivery-stack").length,
  }));
  const weeklyColumns = nodes.filter((node) => node.className === "delivery-week-column");
  const flowRows = nodes.filter((node) => node.className === "delivery-flow-row");
  const typeTable = nodes.find((node) => node.className === "delivery-types");
  const detailBlocks = nodes.filter((node) => node.className === "delivery-detail-block");
  const figureIndexes = nodes.flatMap((node, index) => node.className === "delivery-figure" ? [index] : []);
  const detailIndexes = nodes.flatMap((node, index) => node.className === "delivery-detail-block" ? [index] : []);
  const typeBlock = detailBlocks.find((block) => block.children[0].textContent === "By type");
  const detailData = {
    weeks: weeklyColumns.map((column) => ({
      total: column.children[0].textContent,
      date: column.children[2].textContent,
      segments: all(column).filter((node) => node.className === "delivery-week-segment").map((node) => ({ text: node.textContent, title: node.title })),
    })),
    flows: flowRows.map((row) => ({
      text: all(row).map((node) => node.textContent).join(" "),
      median: all(row).filter((node) => node.className === "delivery-flow-median").length,
      range: all(row).filter((node) => node.className === "delivery-flow-range").length,
      na: all(row).filter((node) => node.className === "delivery-flow-na").length,
    })),
    tableHeaders: all(typeTable).filter((node) => node.tagName === "th").map((node) => node.textContent),
    tableRows: all(typeTable).filter((node) => node.tagName === "tr").length - 1,
    order: detailBlocks.map((block) => block.children[0].textContent),
    detailAfterFigures: Math.min(...detailIndexes) > Math.max(...figureIndexes),
    reworkNoteInByType: all(typeBlock).some((node) => node.className === "delivery-rework-note"),
  };
  const buttons = nodes.filter((node) => node.tagName === "button");
  await buttons.find((button) => button.textContent === "90d").click();
  global.testEfficiency = null;
  eval(source);
  const nullContainer = new Node("main");
  await window.snodoViews.delivery(nullContainer);
  const nullEfficiency = all(nullContainer).find((node) => node.className === "delivery-figure" && node.children[0]?.textContent === "Flow efficiency");
  global.testEfficiency = undefined;
  eval(source);
  const missingContainer = new Node("main");
  await window.snodoViews.delivery(missingContainer);
  const missingEfficiency = all(missingContainer).find((node) => node.className === "delivery-figure" && node.children[0]?.textContent === "Flow efficiency");
  console.log(JSON.stringify({ cardData, requested, detailData,
    nullEfficiency: all(nullEfficiency).map((node) => node.textContent).join(" "),
    missingEfficiency: all(missingEfficiency).map((node) => node.textContent).join(" ") }));
})();
"""
    result = subprocess.run(
        [node, "-e", harness, source], check=True, capture_output=True, text=True
    )
    rendered = json.loads(result.stdout)
    cards = rendered["cardData"]
    assert [card["label"] for card in cards] == [
        "Flow velocity", "Flow time", "Flow efficiency", "Flow load", "Flow distribution"
    ]
    assert "10" in cards[0]["text"] and "How much work is getting done" in cards[0]["text"]
    assert cards[0]["bars"] == 2
    assert "60% features" in cards[4]["text"]
    assert len(cards[4]["segments"]) == 4
    assert all(segment["text"] and segment["title"] for segment in cards[4]["segments"])
    assert "1.0 hr" in cards[1]["text"] and "85% under 2.0 hr" in cards[1]["text"]
    assert "40%" in cards[2]["text"]
    assert "n/a" in rendered["nullEfficiency"]
    assert "n/a" in rendered["missingEfficiency"]
    assert "3" in cards[3]["text"] and "Items started and not yet landed" in cards[3]["text"]
    assert "/api/delivery?days=90" in rendered["requested"]
    details = rendered["detailData"]
    assert len(details["weeks"]) == 2
    assert [week["total"] for week in details["weeks"]] == ["3", "7"]
    assert [week["date"] for week in details["weeks"]] == ["Jan 5", "Jan 12"]
    assert [segment["text"] for segment in details["weeks"][0]["segments"]] == ["feature 2", "defect 1"]
    assert all(segment["title"] for week in details["weeks"] for segment in week["segments"])
    assert len(details["flows"]) == 4
    assert all(flow["median"] == 1 and flow["range"] == 1 for flow in details["flows"][:3])
    assert details["flows"][0]["text"].endswith("10.0 min · 20.0 min")
    assert details["flows"][3]["na"] == 1 and "n/a" in details["flows"][3]["text"]
    assert details["tableHeaders"] == ["Type", "Landed", "Share", "Median time", "85%"]
    assert details["tableRows"] == 4
    assert details["order"] == ["Landed per day", "Velocity by week", "Flow time by type", "By type", "By project"]
    assert details["detailAfterFigures"]
    assert details["reworkNoteInByType"]


def test_delivery_efficiency_is_null_safe_and_type_tokens_exist_in_themes() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'rate(totals.efficiency)' in source
    assert 'totals.efficiency !== null && totals.efficiency !== undefined' in source
    html = (SCRIPT.parents[0] / "index.html").read_text(encoding="utf-8")
    for token in ("feature", "defect", "debt", "risk"):
        assert re.search(rf"--flow-{token}:#[0-9a-fA-F]{{3,8}}", html)
    assert html.count("--flow-feature:") >= 3
    assert html.count("--flow-defect:") >= 3
    assert html.count("--flow-debt:") >= 3
    assert html.count("--flow-risk:") >= 3


def test_delivery_chart_fits_all_days_and_hides_zero_counts_near_bars() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'chart.style.setProperty("--delivery-day-count", String(Math.max(1, byDay.length)))' in source
    assert 'count > 0 ? String(day.landed) : ""' in source
    assert 'countLabel.style.bottom = `calc(${barHeight}% + 0.15rem)`' in source
    assert 'chart.setAttribute("aria-label", "Tasks landed per day")' in source
    assert 'bar.title = `${day.date}: ${day.landed}`' in source
