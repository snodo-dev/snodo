from pathlib import Path
import re
import json
import shutil
import subprocess


ROOT = Path(__file__).parents[2]
STATIC = ROOT / "snodo" / "web" / "static"


def test_protocol_shell_links_view_assets_and_tab() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'data-view="protocol" data-hash="#/protocol">Protocol</button>' in html
    assert 'id="protocol-view"' in html
    assert 'href="/static/protocol.css"' in html
    for name in ("protocol", "protocol-tiles", "protocol-pipeline", "protocol-validators", "protocol-execution", "protocol-savebar"):
        assert f'src="/static/{name}.js"' in html


def test_protocol_shell_theme_tokens_are_defined_for_both_themes() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for token in ("--automated", "--automated-dim", "--automated-border", "--serif", "--body"):
        assert html.count(token) >= 3
    style = html.split("<style>", 1)[1].split("</style>", 1)[0]
    outside_tokens = re.sub(r"--[\w-]+\s*:[^;]*#[0-9a-fA-F]{3,8}[^;]*;", "", style)
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", outside_tokens) is None
    assert "fonts.googleapis.com" not in html
    assert "https://" not in html


def test_protocol_state_change_list_tracks_changed_fields() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol state"
    source = (STATIC / "protocol.js").read_text(encoding="utf-8")
    harness = r"""
 global.window = {};
 global.document = {};
 eval(process.argv[1]);
 const api = window.snodoProtocol;
 api.state.original = { validators: [{id:'gate',type:'quality',phase:'post_execute',severity_cap:'none',tools:[],criteria:['one']}] };
 api.state.validators = JSON.parse(JSON.stringify(api.state.original.validators));
 const untouched = api.changes();
 api.state.validators[0].type = 'testing';
 api.state.validators[0].criteria[0] = 'two';
 console.log(JSON.stringify({untouched, changes:api.changes()}));
 """
    result = subprocess.run([node, "-e", harness, source], check=True, capture_output=True, text=True)
    assert result.stdout == '{"untouched":[],"changes":[{"validator_id":"gate","type":"testing"},{"validator_id":"gate","criteria":["two"]}]}\n'


def test_protocol_scripts_use_safe_dom_and_register_ordered_sections() -> None:
    names = ("tiles", "pipeline", "validators", "execution", "savebar")
    for name in names:
        source = (STATIC / f"protocol-{name}.js").read_text(encoding="utf-8")
        assert "registerRenderer" in source
        assert "innerHTML" not in source
        assert "insertAdjacentHTML" not in source
    source = (STATIC / "protocol.js").read_text(encoding="utf-8")
    assert '`/api/protocol${query}`' in source
    assert '"Protocol"' in source
    assert '"Reads .snodo/protocol.yml"' in source
    assert '"Reload from disk"' in source
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source


def test_protocol_navigation_shows_protocol_container() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'view.hidden = view.id !== (name === "protocol" ? "protocol-view" : `view-${name}`);' in html


def test_protocol_head_renders_mockup_classes_and_copy() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol rendering"
    source = (STATIC / "protocol.js").read_text(encoding="utf-8")
    harness = r"""
class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.className = ""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; }
  setAttribute() {}
  addEventListener() {}
}
global.document = { createElement: (tag) => new Element(tag), createTextNode: (text) => ({textContent:text}) };
global.window = {};
eval(process.argv[1]);
const api = window.snodoProtocol;
api.state.data = {protocol:{name:"Example",version:"1.2"}};
const root = new Element("div");
api.renderHead(root);
function flatten(node) { return [node, ...(node.children || []).flatMap(flatten)]; }
const nodes = flatten(root);
console.log(JSON.stringify({classes:nodes.map(n=>n.className).filter(Boolean), text:nodes.map(n=>n.textContent).filter(Boolean)}));
"""
    result = subprocess.run([node, "-e", harness, source], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    assert {"head", "eyebrow", "live", "dot", "pill pass", "btn"} <= set(rendered["classes"])
    assert "Example · v1.2" in rendered["text"]
    assert "Protocol" in rendered["text"]
    assert "Reads .snodo/protocol.yml" in rendered["text"]
    assert "schema valid" in rendered["text"]
    assert "Reload from disk" in rendered["text"]


def test_protocol_section_scripts_register_in_shell_order() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    names = ("tiles", "pipeline", "validators", "execution", "savebar")
    positions = [html.index(f'/static/protocol-{name}.js') for name in names]
    assert positions == sorted(positions)
    for name in names:
        source = (STATIC / f"protocol-{name}.js").read_text(encoding="utf-8")
        assert "registerRenderer(() => {})" in source
