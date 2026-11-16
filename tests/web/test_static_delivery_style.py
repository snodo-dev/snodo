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
