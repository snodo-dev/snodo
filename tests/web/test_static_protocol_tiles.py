from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-tiles.js"


def test_protocol_tiles_render_working_copy_counts_and_policy_delivery_copy() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol rendering"
    harness = r"""
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.className=""; this.style={}; this.textContent=""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  querySelector(selector) { return this.children.find(n => n.className === selector.slice(1)) || null; }
}
global.document={createElement:tag=>new Element(tag)};
global.window={snodoProtocol:{registerRenderer(fn){(this.renderers ||= []).push(fn);},subscribe(fn){this.subscriber=fn;}}};
eval(process.argv[1]);
const api=window.snodoProtocol;
const root=new Element("main"), head=new Element("header"); head.className="head"; root.append(head);
const validators=[
 {id:"a",phase:"pre_execute"},{id:"b",phase:"post_execute"},{id:"c",phase:"pre_execute"}
];
const state={container:root,data:{protocol:{initial_mode:"producer",disagreement_policy:"unanimous"},modes:[{id:"producer",validators:["a","b","c","missing"]}],execution:{delivery:"local_merge"}},validators};
api.renderers[1](state);
function text(n){return n.children.map(c=>c.textContent || text(c)).join("|");}
let first=root.querySelector(".tiles");
const before=text(first);
state.validators=validators.slice(1); api.renderers[1](state);
const after=text(root.querySelector(".tiles"));
state.data.protocol.disagreement_policy="majority"; state.data.execution.delivery="change_request"; api.renderers[1](state);
const words=text(root.querySelector(".tiles"));
console.log(JSON.stringify({before,after,words,classes:root.querySelector(".tiles").children.map(x=>[x.className,...x.children.map(y=>y.className)]),small:root.querySelector(".tiles").children.slice(-2).map(x=>[x.children[1].style.fontSize,x.children[1].style.paddingTop])}));
"""
    result = subprocess.run([node, "-e", harness, SOURCE.read_text()], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    assert rendered["before"].startswith("Modes|1|initial: producer|Validators|3|of 4 declared|Before coder|2|spec checks|After coder|1|the gate")
    assert "Disagreement|unanimous|one blocker halts" in rendered["before"]
    assert "Delivery|auto-merge|on a clean pass" in rendered["before"]
    assert "Validators|2|of 4 declared|Before coder|1|spec checks|After coder|1|the gate" in rendered["after"]
    assert "Disagreement|majority|most validators must agree" in rendered["words"]
    assert "Delivery|change request|needs a code host plugin" in rendered["words"]
    assert rendered["small"] == [["26px", "8px"], ["26px", "8px"]]
    assert all(classes == ["tile", "eyebrow", "n", "s"] for classes in rendered["classes"])


def test_protocol_tiles_cover_all_copy_and_use_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    for word in ("unanimous", "majority", "local_merge", "push_branch", "change_request", "leave unmerged", "work stays on a branch", "needs a code host plugin", "merge by hand"):
        assert word in source
