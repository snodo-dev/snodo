from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "snodo" / "web" / "static" / "gates.js"
STYLESHEET = ROOT / "snodo" / "web" / "static" / "gates.css"


def test_gates_classes_have_styles_and_bars_use_tracks() -> None:
    script = SCRIPT.read_text(encoding="utf-8")
    stylesheet = STYLESHEET.read_text(encoding="utf-8")
    emitted = set(re.findall(r'"(gates-[a-z-]+)"', script))
    selectors = set(re.findall(r"\.(gates-[a-z-]+)", stylesheet))

    assert emitted <= selectors
    assert 'element("span", undefined, "gates-track")' in script
    assert "track.append(bar)" in script
    assert "gates-outcome-landed" in script
    assert "gates-outcome-pending" in script
    assert ".gates-outcome-landed" in stylesheet
    assert ".gates-outcome-pending" in stylesheet


def test_gates_styles_use_only_theme_colours() -> None:
    stylesheet = STYLESHEET.read_text(encoding="utf-8")

    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet)
    assert "@import" not in stylesheet
    assert not re.search(r"https?://|url\(", stylesheet, re.IGNORECASE)
