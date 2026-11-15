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

    assert "prefers-color-scheme: light" in html
    assert 'data-theme="dark"' in html
    assert 'data-theme="light"' in html
    assert "color-scheme: dark" in html
    assert "window.snodoViews" in html
    assert "setInterval(() => refresh(active), 10000)" in html
    assert "innerHTML" not in html


def test_static_shell_does_not_reference_external_hosts() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert "http://" not in html
    assert "https://" not in html


def test_shell_uses_cloud_tokens_and_legacy_aliases() -> None:
    html = SHELL.read_text(encoding="utf-8")
    tokens = html.split("    * { box-sizing:border-box; }", maxsplit=1)[0]

    for token, value in (
        ("--bg", "#060a0b"), ("--bg-2", "#0b1215"),
        ("--teal", "#2dd4bf"), ("--pass", "#4ade80"),
        ("--blocked", "#ff6b5e"), ("--abstained", "#e0a83a"),
        ("--running", "#38bdf8"), ("--line", "rgba(255,255,255,.06)"),
    ):
        assert f"{token}:{value}" in tokens
    for token in ("--background", "--surface", "--border", "--text", "--muted", "--accent", "--amber", "--red", "--green", "--mono"):
        assert token in tokens
    assert "--background:var(--bg)" in tokens
    assert "--surface:var(--bg-2)" in tokens


def test_shell_topbar_matches_cloud_and_uses_location_host() -> None:
    html = SHELL.read_text(encoding="utf-8")
    assert "height:56px" in html
    assert "max-width:1480px" in html
    assert "backdrop-filter:blur(16px)" in html
    assert 'class="brand-name">snod<span>o</span>' in html
    assert 'class="local">local</span>' in html
    assert 'getElementById("host").textContent = location.host' in html
    assert "127.0.0.1:7878" not in html
    assert "fonts.googleapis.com" not in html
    assert "Archivo" in html and "Hanken Grotesk" in html and "JetBrains Mono" in html


def test_shell_has_no_hex_colours_outside_token_block() -> None:
    import re

    html = SHELL.read_text(encoding="utf-8")
    css = html.split("<style>", maxsplit=1)[1].split("</style>", maxsplit=1)[0]
    token_end = css.index("    * { box-sizing:border-box; }")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css[token_end:])


def test_static_shell_has_first_render_loading_skeleton() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert "const rendered = new Set()" in html
    assert "const firstRender = !rendered.has(name)" in html
    assert "if (firstRender) showSkeleton(container)" in html
    assert "rendered.add(name)" in html
    assert 'container.setAttribute("aria-busy", "true")' in html
    assert 'status.textContent = "Loading"' in html
    assert 'status.setAttribute("role", "status")' in html
    assert 'container.removeAttribute("aria-busy")' in html
    assert 'container.replaceChildren(skeleton)' in html
    assert "innerHTML" not in html


def test_static_shell_skeleton_has_reduced_motion_and_token_styles() -> None:
    html = SHELL.read_text(encoding="utf-8")
    css = html.split("<style>", maxsplit=1)[1].split("</style>", maxsplit=1)[0]
    token_end = css.index("    * { box-sizing:border-box; }")
    component_css = css[token_end:]

    assert ".view-skeleton" in component_css
    assert ".skeleton-heading" in component_css
    assert ".skeleton-card" in component_css
    assert ".skeleton-list" in component_css
    assert "@media (prefers-reduced-motion: reduce)" in component_css
    assert "animation:none" in component_css
    assert "color-mix(" in component_css
    assert "#" not in component_css
    assert "rgba(" not in component_css


def test_static_shell_failure_replaces_skeleton_with_existing_message() -> None:
    html = SHELL.read_text(encoding="utf-8")

    assert 'showMessage(container, "This view could not be loaded.")' in html
    assert 'showMessage(container, "This view is unavailable.")' in html
    assert "container.replaceChildren(line)" in html
    assert 'container.removeAttribute("aria-busy")' in html
