from pathlib import Path


SHELL = Path(__file__).parents[2] / "snodo" / "web" / "static" / "index.html"


def test_shell_loads_optional_stylesheet_for_each_view() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert 'stylesheet.href = `/static/${name}.css`' in html
    assert 'script.src = `/static/${name}.js`' in html
    assert 'stylesheet.onerror' not in html
    assert 'failed.add(name)' not in html.split('stylesheet.href', maxsplit=1)[1].split(
        'const script', maxsplit=1
    )[0]


def test_shell_has_shared_view_styles() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert ".view h1, .view h2, .view h3, .view h4" in html
    assert ".view table" in html
    assert '.view button[aria-pressed="true"]' in html
    assert ".view :focus-visible" in html
    assert ".view .view-message" in html
    assert "font-variant-numeric:tabular-nums" in html or "font-variant-numeric: tabular-nums" in html
