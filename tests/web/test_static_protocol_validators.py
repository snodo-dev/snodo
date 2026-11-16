from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-validators.js"


def test_validators_renderer_uses_mockup_classes_and_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    for class_name in ("sec", "sec-h", "eyebrow", "sub", "btn", "card", "grp", "pill pass", "dim", "row", "open", "mod", "car", "nm", "sb", "ph", "bars", "x", "edit", "show"):
        assert class_name in source


def test_validators_render_by_mode_and_toggle_shared_open_state() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol rendering"
    harness = r"""
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.className=""; this.dataset={}; this.handlers={}; this.attrs={}; this.textContent=""; this.disabled=false; }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent=this; } }
  addEventListener(name, fn) { this.handlers[name]=fn; }
  setAttribute(k,v) { this.attrs[k]=v; }
  replaceWith(node) { const i=this.parent.children.indexOf(this); this.parent.children[i]=node; node.parent=this.parent; }
  querySelector(selector) { return flatten(this).find(n=>selector[0]==="." && n.className.split(" ").includes(selector.slice(1))) || null; }
}
function flatten(node) { return [node,...node.children.flatMap(flatten)]; }
const root=new Element("main");
const api={registerRenderer(fn){(this.renderers ||= []).push(fn);},isChanged:id=>id==="a",notify(){this.notified=true;}};
global.document={createElement:t=>new Element(t)}; global.window={snodoProtocol:api};
eval(process.argv[1]);
const validators=[
 {id:"a",type:"security",phase:"pre_execute",criteria:["one"],tools:["read_file"]},
 {id:"b",type:"quality",phase:"post_execute",criteria:["one","two"]},
 {id:"c",type:"testing",phase:"pre_execute",criteria:Array(10).fill("x"),tools:[]},
 {id:"z",type:"planning",phase:"pre_execute",criteria:["unused"],tools:[]},
];
const state={container:root,validators,openValidator:null,data:{protocol:{initial_mode:"producer"},modes:[{id:"producer",name:"Producer",validators:["a","b"]},{id:"reviewer",name:"Reviewer",validators:["c"]}]}};
api.renderers[1](state);
const section=root.querySelector(".validators-section"), nodes=flatten(section);
const rows=nodes.filter(n=>n.className.startsWith("row"));
const groups=nodes.filter(n=>n.className==="grp");
const add=nodes.find(n=>n.tagName==="button");
const bars=rows.map(row=>row.children[3].children.map(n=>n.className));
rows[0].handlers.click();
const opened=state.openValidator; rows[0].handlers.click();
console.log(JSON.stringify({groups:groups.map(g=>g.children.map(n=>n.textContent)),ids:rows.map(r=>r.dataset.id),classes:rows.map(r=>r.className),summaries:rows.map(r=>r.children[1].children[1].textContent),pills:rows.map(r=>r.children[4].textContent),bars,edits:nodes.filter(n=>n.className.startsWith("edit")).map(n=>n.className),button:{disabled:add.disabled,title:add.title},opened,closed:state.openValidator,notified:api.notified}));
"""
    result = subprocess.run([node, "-e", harness, SOURCE.read_text()], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    assert rendered["groups"] == [["Producer", "initial", "2 active · 1 declared but unused"], ["Reviewer", "1 active · 2 declared but unused"], ["Unused", "0 active · 1 declared but unused"]]
    assert rendered["ids"] == ["a", "b", "c", "c", "a", "b", "z"]
    assert rendered["summaries"][:3] == ["security · 1 criterion · reads files", "quality · 2 criteria", "testing · 10 criteria"]
    assert rendered["pills"] == ["active", "active", "unused", "active", "unused", "unused", "unused"]
    assert rendered["bars"][2] == ["x"] * 8
    assert rendered["bars"][3] == [""] * 8
    assert rendered["bars"][4] == ["x"]
    assert "mod" in rendered["classes"][0]
    assert rendered["button"] == {"disabled": True, "title": "Adding validators is not available yet"}
    assert "show" not in rendered["edits"][0]
    assert rendered["opened"] == "a" and rendered["closed"] is None and rendered["notified"] is True
