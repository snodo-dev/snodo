from pathlib import Path


SHELL = Path(__file__).parents[2] / "snodo" / "web" / "static" / "index.html"


def test_static_shell_has_views_and_hash_navigation() -> None:
    html = SHELL.read_text(encoding="utf-8")

    for name in ("now", "delivery", "gates", "plans"):
        assert f'id="view-{name}"' in html
        assert f'data-view="{name}"' in html
        assert f'data-hash="#/{name}"' in html
    assert 'location.hash = "/now"' in html
    assert 'script.src = `/static/${name}.js`' in html


def test_static_shell_preloads_markdown_before_rendering_views() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert 'script.src = "/static/markdown.js"' in html
    assert "const markdownReady = new Promise" in html
    assert html.index('script.src = "/static/markdown.js"') < html.index("await markdownReady")
    assert html.index("await markdownReady") < html.index("await render(container)")
    assert "script.onload = resolve" in html
    assert "script.onerror = resolve" in html


def test_static_shell_has_themes_and_view_contract() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert "prefers-color-scheme: dark" in html
    assert 'data-theme="dark"' in html
    assert 'data-theme="light"' in html
    assert "window.snodoViews" in html
    assert "setInterval(() => refresh(active), 10000)" in html
    assert "innerHTML" not in html


def test_static_shell_does_not_reference_external_hosts() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert "http://" not in html
    assert "https://" not in html
