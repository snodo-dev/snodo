from pathlib import Path
import re


ROOT = Path(__file__).parents[2]
MOCKUP = ROOT / "docs" / "design" / "protocol-editor.html"
STYLESHEET = ROOT / "snodo" / "web" / "static" / "protocol.css"
SCOPE = "#protocol-view"
# These rules belong to dashboard tokens, reset, or shell rather than the view.
EXCLUDED_SELECTORS = [
    ":root",
    ':root:not([data-theme="dark"])',
    "*",
    "body",
    ".top",
    ".top .wrap",
    ".logo",
    ".env",
    ".tabs",
    ".tabs a",
    ".tabs a.on",
    ".meta",
    ".meta b",
]


def _rules(css: str, media: str = "") -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    """Parse this stylesheet subset into media/selector/declaration entries."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    found: dict[tuple[str, str], tuple[tuple[str, str], ...]] = {}
    position = 0
    while position < len(css):
        while position < len(css) and css[position].isspace():
            position += 1
        if position == len(css):
            break
        opening = css.find("{", position)
        assert opening >= 0, f"Unparsed CSS: {css[position:]}"
        selector = css[position:opening].strip()
        depth = 1
        closing = opening + 1
        while depth:
            if css[closing] == "{":
                depth += 1
            elif css[closing] == "}":
                depth -= 1
            closing += 1
        body = css[opening + 1 : closing - 1]
        if selector.startswith("@media"):
            found.update(_rules(body, selector))
        else:
            declarations = tuple(
                sorted(
                    (name.strip(), value.strip())
                    for declaration in body.split(";")
                    if declaration.strip()
                    for name, value in [declaration.split(":", 1)]
                )
            )
            for item in selector.split(","):
                found[(media, item.strip())] = declarations
        position = closing
    return found


def _mockup_css() -> str:
    html = MOCKUP.read_text(encoding="utf-8")
    return re.search(r"<style>(.*?)</style>", html, re.S).group(1)


def _expected() -> dict[tuple[str, str], tuple[tuple[str, str], ...]]:
    excluded = set(EXCLUDED_SELECTORS)
    expected = {}
    for (media, selector), declarations in _rules(_mockup_css()).items():
        if selector not in excluded:
            expected[(media, f"{SCOPE} {selector}")] = declarations
    return expected


def _assert_parity(css: str) -> None:
    assert _rules(css) == _expected()


def test_protocol_styles_match_mockup_exactly() -> None:
    _assert_parity(STYLESHEET.read_text(encoding="utf-8"))


def test_protocol_style_parity_detects_a_declaration_mismatch() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    modified = css.replace("max-width:1440px", "max-width:1441px", 1)
    assert modified != css
    try:
        _assert_parity(modified)
    except AssertionError:
        pass
    else:
        raise AssertionError("Parity check accepted a modified declaration")


def test_protocol_styles_have_no_literal_colors_or_external_urls() -> None:
    css = STYLESHEET.read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert not re.search(r"https?://|url\(", css, re.I)


def test_protocol_selectors_are_scoped() -> None:
    rules = _rules(STYLESHEET.read_text(encoding="utf-8"))
    assert all(selector.startswith(f"{SCOPE} ") for _, selector in rules)
