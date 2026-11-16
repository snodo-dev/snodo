from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-execution.js"


def test_execution_and_rules_render_values_defaults_and_failures() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol rendering"
    harness = r"""
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.className=""; this.textContent=""; this.parent=null; }
  append(...nodes) { for (const node of nodes) { this.children.push(node); node.parent=this; } }
  replaceWith(node) { const index=this.parent.children.indexOf(this); this.parent.children[index]=node; node.parent=this.parent; }
  querySelector(selector) { return flatten(this).find(node => node.className.split(" ").includes(selector.slice(1))) || null; }
}
function flatten(node) { return [node, ...node.children.flatMap(flatten)]; }
global.document={createElement:tag=>new Element(tag)};
global.window={snodoProtocol:{state:null,registerRenderer(fn){(this.renderers ||= []).push(fn);}}};
eval(process.argv[1]);
const api=window.snodoProtocol, root=new Element("main");
const state={container:root,data:{execution:{max_retries:4,branch_ttl_days:8,max_recovery_depth:2,max_total_fix_attempts:12,branch_prefix:"work"},queue:{parallel_runs:5},rules:[
 {id:"valid",description:"A passing rule",ok:true},
 {id:"invalid",description:"A failing rule",ok:false,detail:"Needs attention"},
 {id:"no-detail",description:"No detail",ok:false}
]}};
api.state=state; api.renderers[0](state);
const section=cls=>root.querySelector("."+cls);
const kv=section("execution-section").querySelector(".kv");
const values=kv.children.map(c=>[c.children[0].textContent,c.children[1].textContent]);
const rules=section("rules-section").querySelector(".rules").children.map(row=>({pill:row.children[0].className,word:row.children[0].textContent,id:row.children[1].textContent,description:row.children[2].textContent,detail:row.children[3]?.textContent || null}));
const classes=[...new Set(flatten(root).flatMap(n=>n.className.split(" ")).filter(Boolean))];
const failing=api.hasFailingRule(); state.data.rules=[{ok:true}]; const passing=api.hasFailingRule();
state.data={}; api.renderers[0](state);
console.log(JSON.stringify({values,rules,classes,failing,passing,defaults:section("execution-section").querySelector(".kv").children.map(c=>String(c.children[1].textContent))}));
"""
    result = subprocess.run([node, "-e", harness, SOURCE.read_text()], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    assert rendered["values"] == [
        ["max_retries", "4"], ["branch_ttl_days", "8"], ["max_recovery_depth", "2"],
        ["max_total_fix_attempts", "12"], ["branch_prefix", "work"], ["queue.parallel_runs", "5"],
    ]
    assert rendered["rules"] == [
        {"pill": "pill pass", "word": "ok", "id": "valid", "description": "A passing rule", "detail": None},
        {"pill": "pill abs", "word": "fail", "id": "invalid", "description": "A failing rule", "detail": "Needs attention"},
        {"pill": "pill abs", "word": "fail", "id": "no-detail", "description": "No detail", "detail": None},
    ]
    assert {"sec", "sec-h", "eyebrow", "sub", "card", "kv", "rules", "pill", "pass", "abs", "dim"} <= set(rendered["classes"])
    assert rendered["failing"] is True and rendered["passing"] is False
    assert rendered["defaults"] == ["3", "7", "3", "10", "task", "1"]


def test_execution_renderer_uses_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
