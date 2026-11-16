from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo/web/static/protocol-savebar.js"


def test_savebar_behavior_and_mockup_contract() -> None:
    node = shutil.which("node")
    assert node is not None
    source = SOURCE.read_text(encoding="utf-8")
    mockup = (ROOT / "docs/design/protocol-editor.html").read_text(encoding="utf-8")
    assert "innerHTML" not in source and "insertAdjacentHTML" not in source
    for class_name in ("bar", "wrap", "pill aut", "dim", "sp", "btn", "btn pri", "diff", "box", "a", "d", "c", "show"):
        assert f'"{class_name}"' in source
        assert f'class="{class_name}"' in mockup or class_name in ("a", "d", "c", "show")

    harness = r"""
const assert = require('node:assert/strict');
(async () => {
class E {
  constructor(tag) { this.tagName=tag; this.children=[]; this.className=''; this.textContent=''; this.disabled=false; this.listeners={}; this.isConnected=true; this.classList={add:c=>{this.className += ` ${c}`},remove:c=>{this.className=this.className.split(' ').filter(x=>x!==c).join(' ')}}; }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); }
  replaceChildren(...nodes) { this.children=nodes; }
  addEventListener(name,fn) { this.listeners[name]=fn; }
  setAttribute() {}
  querySelector(selector) { return selector === '.live .pill' ? pill : null; }
  click() { return this.listeners.click?.(); }
}
const pill = new E('span');
pill.className='pill pass'; pill.textContent='schema valid';
global.document={createElement:tag=>new E(tag),createTextNode:text=>({textContent:text})};
global.window={};
const protocol = process.argv[1], savebar = process.argv[2];
eval(protocol); const api=window.snodoProtocol; const root=new E('root');
api.state.container=root; api.state.project='demo';
const original=[{id:'v',type:'quality',phase:'pre_execute',severity_cap:'none',tools:[],criteria:['original']}];
api.state.data={etag:'etag-1',page_token:'token-1',validators:original,rules:[]};
api.state.original={validators:JSON.parse(JSON.stringify(original))}; api.state.validators=JSON.parse(JSON.stringify(original));
root.querySelector=()=>pill;
let responses=[], calls=[];
global.fetch=async (url,opts)=>{calls.push({url,body:JSON.parse(opts.body)}); const item=responses.shift(); return {ok:item.ok,json:async()=>item.body};};
api.registerRenderer(()=>{}); eval(savebar);
const find=(node,pred)=>{if(pred(node))return node;for(const child of node.children||[]){const found=find(child,pred);if(found)return found;}return null;};
const parts=()=>({bar:find(root,n=>n.className==='bar'),buttons:(find(root,n=>n.className==='sp')||{}).children||[]});
let p=parts();
assert.equal(p.buttons[0].textContent,'Discard'); assert.equal(p.buttons[1].textContent,'Review diff'); assert.equal(p.buttons[2].textContent,'Save to protocol.yml');
assert.deepEqual(p.buttons.map(b=>b.disabled),[true,true,true]);
assert.equal(find(p.bar,n=>n.className==='pill aut').textContent,'0 changes');
assert.equal(find(p.bar,n=>n.className==='dim').textContent,'No unsaved edits.');
api.state.validators[0].criteria=['']; api.notify(); p=parts();
assert.deepEqual(p.buttons.map(b=>b.disabled),[false,true,true]);
assert.equal(find(p.bar,n=>n.className==='dim').textContent,'A criterion is empty. Fill it or remove it before saving.');
assert.equal(pill.className,'pill abs'); assert.equal(pill.textContent,'empty criterion');
api.state.validators[0].criteria=['edited']; api.notify(); p=parts();
assert.equal(find(p.bar,n=>n.className==='dim').textContent,'Unsaved edits in v.');
assert.deepEqual(p.buttons.map(b=>b.disabled),[false,false,false]);
responses.push({ok:true,body:{diff:'failure',changes:[{}],valid:false,rules:[{ok:false}]}});
await p.buttons[1].click();
let overlay=find(root,n=>n.className==='diff show'); assert.ok(overlay);
assert.deepEqual(parts().buttons.map(b=>b.disabled),[false,true,true]);
assert.equal(find(overlay,n=>n.textContent==='Write file').disabled,true);
find(overlay,n=>n.textContent==='Back').click();
responses.push({ok:true,body:{diff:'--- old\n+++ new\n@@ -1 +1 @@\n-old\n+new\n',changes:[{}],valid:true,rules:[]}});
await p.buttons[1].click();
overlay=find(root,n=>n.className==='diff show'); assert.ok(overlay);
assert.equal(overlay.children[0].children[0].children[0].textContent,'Review changes');
assert.equal(overlay.children[0].children[0].children[1].textContent,'1 change');
assert.equal(overlay.children[0].children[1].textContent,'A diff of .snodo/protocol.yml. Saving writes the file; committing and pushing stay with you.');
const pre=find(overlay,n=>n.tagName==='pre');
assert.deepEqual(pre.children.filter(n=>n.className).map(n=>[n.className,n.textContent]),[['c','--- old'],['c','+++ new'],['c','@@ -1 +1 @@'],['d','-old'],['a','+new']]);
assert.deepEqual(calls[0],{url:'/api/protocol/preview',body:{project:'demo',etag:'etag-1',edits:[{validator_id:'v',criteria:['edited']}]}});
const write=find(overlay,n=>n.textContent==='Write file');
responses.push({ok:false,body:{error:'disk changed'}}); await write.click();
assert.equal(find(root,n=>n.className==='diff show'),null);
assert.equal(api.state.validators[0].criteria[0],'edited');
assert.equal(find(parts().bar,n=>n.className==='dim').textContent,'disk changed');
responses.push({ok:true,body:{diff:'diff',changes:[{}],valid:true,rules:[]}}); await parts().buttons[1].click();
overlay=find(root,n=>n.className==='diff show');
responses.push({ok:false,body:{error:'invalid protocol'}}); await find(overlay,n=>n.textContent==='Write file').click();
assert.equal(find(root,n=>n.className==='diff show'),null);
assert.equal(api.state.validators[0].criteria[0],'edited');
assert.equal(find(parts().bar,n=>n.className==='dim').textContent,'invalid protocol');
assert.deepEqual(calls[2],{url:'/api/protocol/save',body:{project:'demo',etag:'etag-1',page_token:'token-1',edits:[{validator_id:'v',criteria:['edited']}]}});
responses.push({ok:true,body:{diff:'diff',changes:[{}],valid:true,rules:[]}});
responses.push({ok:true,body:{etag:'etag-2',page_token:'token-2',validators:[{...original[0],criteria:['saved']}],rules:[]}});
await parts().buttons[2].click(); overlay=find(root,n=>n.className==='diff show');
await find(overlay,n=>n.textContent==='Write file').click();
assert.equal(api.state.validators[0].criteria[0],'saved');
assert.equal(find(parts().bar,n=>n.className==='dim').textContent,'Written to .snodo/protocol.yml. Commit and push when ready.');
assert.equal(api.state.data.etag,'etag-2');
api.state.validators[0].criteria=['temporary']; api.notify(); await parts().buttons[0].click();
assert.equal(api.state.validators[0].criteria[0],'saved');
assert.equal(find(parts().bar,n=>n.className==='dim').textContent,'No unsaved edits.');
})().catch(error => { console.error(error); process.exitCode=1; });
"""
    protocol = (ROOT / "snodo/web/static/protocol.js").read_text(encoding="utf-8")
    result = subprocess.run([node, "-e", harness, protocol, source], check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
