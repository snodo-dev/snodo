from pathlib import Path
import json
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo/web/static/protocol-savebar.js"


def test_savebar_builds_mockup_structure_and_copy_without_html() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    for value in ("bar", "wrap", "pill aut", "dim", "sp", "btn pri", "diff", "show", "box", "Review changes", "Back", "Write file", "A diff of .snodo/protocol.yml. Saving writes the file; committing and pushing stay with you."):
        assert value in source
    assert '"No unsaved edits."' in source
    assert '"A criterion is empty. Fill it or remove it before saving."' in source
    assert '"Written to .snodo/protocol.yml. Commit and push when ready."' in source
    assert '"/api/protocol/preview"' in source and '"/api/protocol/save"' in source
    assert 'page_token: state.data.page_token' in source and 'etag: state.data.etag' in source
    assert 'edits: edits(state)' in source
    assert 'el("span", line, kind)' in source
    assert all(f'"{kind}"' in source for kind in ("a", "d", "c"))


def test_bar_interactions_and_gates() -> None:
    node = shutil.which("node")
    assert node is not None
    source = SOURCE.read_text(encoding="utf-8")
    protocol = (ROOT / "snodo/web/static/protocol.js").read_text(encoding="utf-8")
    harness = r"""
class E { constructor(tag){this.tagName=tag;this.children=[];this.className='';this.disabled=false;this.listeners={};this.classList={add:()=>{},remove:()=>{}};this.isConnected=true;} append(...n){this.children.push(...n)} appendChild(n){this.children.push(n)} replaceChildren(...n){this.children=n} addEventListener(k,f){this.listeners[k]=f} setAttribute(){} querySelector(){return null} }
global.document={createElement:t=>new E(t),createTextNode:t=>({textContent:t})}; global.window={};
eval(process.argv[1]); const api=window.snodoProtocol; const root=new E('root');
api.state.container=root; api.state.project='demo'; api.state.data={etag:'etag',page_token:'token',validators:[],rules:[]}; api.state.original={validators:[{id:'v',type:'quality',phase:'pre_execute',severity_cap:'none',tools:[],criteria:['ok']}]}; api.state.validators=JSON.parse(JSON.stringify(api.state.original.validators));
let calls=[]; global.fetch=async (url,opts)=>{calls.push([url,JSON.parse(opts.body)]);return {ok:true,json:async()=>({diff:'--- old\n+++ new\n@@ -1 +1 @@\n-old\n+new\n',changes:[1],valid:true,rules:[]})}};
api.registerRenderer(()=>{}); const file=process.argv[2]; eval(file);
api.state.validators[0].criteria=['']; api.notify(); const bar=root.children.at(-1); const all=[]; const walk=n=>{all.push(n);(n.children||[]).forEach(walk)};walk(root); console.log(JSON.stringify({text:all.map(x=>x.textContent).filter(Boolean),classes:all.map(x=>x.className),disabled:all.filter(x=>x.disabled).length}));
"""
    result = subprocess.run([node, "-e", harness, protocol, source], check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    assert "A criterion is empty. Fill it or remove it before saving." in data["text"]
    assert "empty criterion" in source
    assert "bar" in data["classes"] and "wrap" in data["classes"]
