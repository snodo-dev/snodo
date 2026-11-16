from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-pipeline.js"


def test_pipeline_renderer_matches_mockup_and_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    for class_name in ("sec", "sec-h", "eyebrow", "sub", "card", "flow", "stage", "stage coder", "pill run", "pill abs", "pill pass", "h4", "dim", "chips", "mod", "off"):
        assert class_name in source
    assert 'registerRenderer(() => {})' in source


def test_pipeline_reflects_working_copy_and_click_opens_editor_row() -> None:
    node = shutil.which("node")
    assert node is not None, "Node.js is required to exercise protocol rendering"
    source = SOURCE.read_text()
    harness = r"""
class Element {
  constructor(tag) { this.tagName=tag; this.children=[]; this.className=""; this.attrs={}; this.handlers={}; this.textContent=""; this.tabIndex=-1; }
  append(...nodes) { for (const n of nodes) { this.children.push(n); n.parent=this; } }
  setAttribute(k,v) { this.attrs[k]=v; }
  addEventListener(k,fn) { this.handlers[k]=fn; }
  replaceWith(n) { const i=this.parent.children.indexOf(this); this.parent.children[i]=n; n.parent=this.parent; }
  querySelector(selector) { return flatten(this).find(n => selector[0]==="." ? n.className.split(" ").includes(selector.slice(1)) : false) || null; }
  scrollIntoView(options) { this.scrolled=options; }
}
function flatten(n) { return [n,...n.children.flatMap(flatten)]; }
const root=new Element("main");
const rows={};
root.querySelector=(selector)=>selector.startsWith(".") ? flatten(root).find(n=>n.className.split(" ").includes(selector.slice(1)))||null : rows[selector.match(/data-id="([^"]+)/)?.[1]]||null;
global.document={createElement:t=>new Element(t)};
global.requestAnimationFrame=fn=>fn();
global.window={snodoProtocol:{registerRenderer(fn){(this.renderers ||= []).push(fn);},isChanged:id=>id==="edited",notify(){this.notified=true;}}};
eval(process.argv[1]);
const api=window.snodoProtocol;
const validators=[{id:"a",phase:"pre_execute"},{id:"edited",phase:"pre_execute"},{id:"moved",phase:"pre_execute"},{id:"gate",phase:"post_execute"}];
const state={container:root,validators,data:{protocol:{initial_mode:"producer"},modes:[{id:"producer",validators:["a","edited","moved","gate","missing"],tools:["edit","dispatch","plan"]}],execution:{delivery:"local_merge",max_retries:3,max_recovery_depth:2,max_total_fix_attempts:8}}};
rows.edited=new Element("div"); rows.edited.scrollIntoView=Element.prototype.scrollIntoView;
api.renderers[1](state);
let section=root.querySelector(".pipeline-section");
function stages(){section=root.querySelector(".pipeline-section"); return section.children[1].children[0].children;}
function words(n){return n.textContent || n.children.map(words).join("|");}
const before=stages().map(s=>({cls:s.className,text:words(s),pill:s.children[0].textContent,heading:s.children[1].textContent,detail:s.children[2].textContent,chips:s.children[3].children.map(c=>({text:c.textContent,cls:c.className,click:!!c.handlers.click}))}));
const moved=state.validators.find(v=>v.id==="moved"); moved.phase="post_execute";
state.validators=state.validators.filter(v=>v.id!=="a"); state.data.modes[0].validators=state.data.modes[0].validators.filter(id=>id!=="a");
api.renderers[1](state);
const after=stages().map(s=>s.children[3].children.map(c=>c.textContent));
const editChip=stages()[0].children[3].children.find(c=>c.textContent==="edited"); editChip.handlers.click();
console.log(JSON.stringify({before,after,open:state.openValidator,notified:api.notified,scroll:rows.edited.scrolled}));
"""
    result = subprocess.run([node, "-e", harness, source], check=True, capture_output=True, text=True)
    rendered = json.loads(result.stdout)
    stages = rendered["before"]
    assert [next((text for text in ("pre_execute", "coder", "post_execute", "merge") if text in stage["text"]), "") for stage in stages] == ["pre_execute", "coder", "post_execute", "merge"]
    assert [stage["cls"] for stage in stages] == ["stage", "stage coder", "stage", "stage"]
    assert [stage["pill"] for stage in stages] == ["pre_execute", "coder", "post_execute", "merge"]
    assert [stage["heading"] for stage in stages] == ["Spec checks", "Implement", "Gate", "Deliver"]
    assert [stage["detail"] for stage in stages] == ["before any code is written", "model comes from config", "checks the work", "local_merge"]
    assert [stage["chips"] for stage in stages][0][1]["cls"] == "mod"
    assert [stage["chips"] for stage in stages][0][-1] == {"text": "missing", "cls": "off", "click": False}
    assert rendered["after"][0] == ["edited", "missing"] and "moved" in rendered["after"][2]
    assert rendered["open"] == "edited" and rendered["notified"] is True
    assert rendered["scroll"] == {"behavior": "smooth", "block": "center"}
