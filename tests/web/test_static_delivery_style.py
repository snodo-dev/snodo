from pathlib import Path
import re


STATIC = Path(__file__).parents[2] / "snodo" / "web" / "static"
SCRIPT = STATIC / "delivery.js"
STYLESHEET = STATIC / "delivery.css"


def test_every_delivery_class_has_a_style_rule() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    classes = set(re.findall(r'"(delivery-[\w-]+)"', source))
    for class_name in classes:
        assert re.search(rf"\.{re.escape(class_name)}(?:[\s,.#:[>~+]|$)", stylesheet), class_name


def test_delivery_bars_use_a_track() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'element("div", undefined, "delivery-track")' in source
    assert "track.append(bar)" in source


def test_delivery_styles_use_only_local_theme_tokens() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet)
    assert "@import" not in stylesheet.lower()
    assert not re.search(r"https?://", stylesheet, re.IGNORECASE)


def test_delivery_hidden_date_labels_do_not_take_up_chart_space() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    assert ".delivery-day-date[hidden]" in stylesheet
    assert "display: none" in stylesheet


def test_delivery_detail_panels_match_figure_border_and_radius_tokens() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    def declarations(selector: str) -> dict[str, str]:
        match = re.search(rf"\.{selector}\s*\{{([^}}]*)\}}", stylesheet)
        assert match is not None
        return dict(
            re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group(1))
        )

    figure = declarations("delivery-figure")
    detail = declarations("delivery-detail-block")
    assert detail["border"] == figure["border"] == "1px solid var(--border)"
    assert detail["border-radius"] == figure["border-radius"] == "0.75rem"


def test_delivery_week_columns_are_capped_and_scrollable() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    match = re.search(r"\.delivery-week-chart\s*\{([^}]*)\}", stylesheet)
    assert match is not None
    declarations = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group(1)))
    assert declarations["grid-auto-columns"] == "minmax(3.5rem, 4.5rem)"
    assert "1fr" not in declarations["grid-auto-columns"]
    assert declarations["overflow-x"] == "auto"


def test_delivery_day_and_week_charts_share_padding_and_baseline() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    def declarations(selector: str) -> dict[str, str]:
        match = re.search(rf"\.{selector}\s*\{{([^}}]*)\}}", stylesheet)
        assert match is not None
        return dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group(1)))

    day = declarations("delivery-chart")
    week = declarations("delivery-week-chart")
    assert day["padding"] == week["padding"]
    assert day["border-bottom"] == week["border-bottom"]


def test_delivery_flow_values_can_fit_narrow_panels() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    match = re.search(r"\.delivery-flow-row\s*\{([^}]*)\}", stylesheet)
    assert match is not None
    declarations = dict(re.findall(r"([\w-]+)\s*:\s*([^;]+);", match.group(1)))
    assert declarations["grid-template-columns"] == "5rem minmax(0, 1fr) minmax(0, auto)"
    values = re.search(r"\.delivery-flow-values, \.delivery-flow-na\s*\{([^}]*)\}", stylesheet)
    assert values is not None
    assert "overflow-wrap: anywhere" in values.group(1)
