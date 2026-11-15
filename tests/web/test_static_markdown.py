from pathlib import Path
import shutil
import subprocess


SCRIPT = Path(__file__).parents[2] / "snodo" / "web" / "static" / "markdown.js"


def test_markdown_renderer_is_safe_and_supports_required_syntax() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "window.snodoMarkdown" in source
    assert "render" in source
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in source
    for marker in ('h${heading[1].length}', '"ul"', '"ol"', '"pre"', '"code"', '"strong"', '"em"', '"a"'):
        assert marker in source
    assert 'noopener noreferrer' in source
    assert "https?" in source


def test_markdown_renderer_in_node_when_available() -> None:
    node = shutil.which("node")
    if node is None:
        return
    source = SCRIPT.read_text(encoding="utf-8")
    harness = r"""
const elements = [];
function make(tag) {
  const node = { tag, children: [], attributes: {}, textContent: "", append(child) { this.children.push(child); }, setAttribute(k, v) { this.attributes[k] = v; } };
  elements.push(node);
  return node;
}
global.document = { createElement: make, createTextNode: (value) => ({ text: value }), createDocumentFragment: () => make("fragment") };
global.window = {};
""" + source + r"""
const result = window.snodoMarkdown.render("# Title\n\n- item\n\n```\nconst x = 1;\n```\n\n[site](https://example.com) [bad](javascript:alert(1))");
const tags = elements.map((item) => item.tag);
if (!tags.includes("h1") || !tags.includes("ul") || !tags.includes("pre") || !tags.includes("a")) process.exit(1);
if (elements.filter((item) => item.tag === "a").length !== 1) process.exit(2);
if (elements.find((item) => item.tag === "a").attributes.rel !== "noopener noreferrer") process.exit(3);
"""
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)


def test_markdown_renderer_handles_ticket_spec_structure() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert '"markdown-label"' in source
    assert 'document.createElement("br")' in source
    assert 'document.createElement("blockquote")' in source
    assert 'document.createElement("hr")' in source


def test_markdown_renderer_preserves_identifiers_and_inline_code() -> None:
    node = shutil.which("node")
    if node is None:
        return
    source = SCRIPT.read_text(encoding="utf-8")
    harness = r"""
const elements = [];
function make(tag) {
  const node = { tag, children: [], attributes: {}, textContent: "", append(child) { this.children.push(child); }, setAttribute(k, v) { this.attributes[k] = v; } };
  elements.push(node);
  return node;
}
global.document = { createElement: make, createTextNode: (value) => ({ text: value }), createDocumentFragment: () => make("fragment") };
global.window = {};
""" + source + r"""
const result = window.snodoMarkdown.render("INTENT\nA view\n\ntests/web/test_static_shell.py\n*italic* `__code__`\n\nEvidence: details\n\n1. ordered\n\n> quote\n\n---\n\n[a](javascript:alert(1))");
const tags = elements.map((item) => item.tag);
if (!tags.includes("h2") || !tags.includes("br") || !tags.includes("ol") || !tags.includes("blockquote") || !tags.includes("hr")) process.exit(1);
if (!tags.includes("em") || !tags.includes("code") || !elements.some((item) => item.attributes.class === "markdown-label")) process.exit(2);
if (elements.some((item) => item.tag === "a")) process.exit(3);
const texts = elements.flatMap((item) => item.children || []).filter((item) => item && item.text).map((item) => item.text).join("");
if (!texts.includes("tests/web/test_static_shell.py")) process.exit(4);
"""
    subprocess.run([node, "-e", harness], check=True, capture_output=True, text=True)
