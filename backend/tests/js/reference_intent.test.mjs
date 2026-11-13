import {test} from 'node:test';
import assert from 'node:assert/strict';
import {install, byText} from './fakedom.mjs';
import * as ui from '../../src/grimoire/app/static/shell/ui.js';
import {makeReferenceIntent, validateRegion} from '../../src/grimoire/app/static/pages/chat/reference-intent.js';
let serial=0;
function page(chat=`chat:ref/${serial++}`) {
  install('http://localhost');
  const controller=new AbortController(), saved=[];
  const composer=ui.field('Your turn',{value:'Existing draft',multiline:true});
  // Native Events have readonly target; this small DOM takes a mutable event record.
  const dispatch=composer.input.dispatchEvent.bind(composer.input);
  composer.input.dispatchEvent=event=>dispatch({type:event.type});
  composer.input.addEventListener('input',()=>saved.push(composer.input.value));
  const root=makeReferenceIntent({ui,styles(){},signal:controller.signal},chat,{composer});
  document.body.append(root,composer.node);
  const field=name=>root.querySelector(`[name="${name}"]`);
  const type=(name,value)=>{field(name).value=value;field(name).dispatchEvent({type:'input'});};
  const click=name=>byText(root,'button',name)[0].click();
  return {root,composer,controller,saved,field,type,click};
}
const tick=()=>new Promise(resolve=>setTimeout(resolve,0));
const image=(size=8,type='image/png')=>({size,type,arrayBuffer:async()=>new Uint8Array([137,80,78,71,13,10,26,10]).buffer});
async function upload(p,file){p.field('reference-file').files=[file];p.field('reference-file').dispatchEvent({type:'change'});await tick();}
test('normalized regions refuse nonfinite, nonpositive and uncontained bounds',()=>{
  for(const r of [{x:NaN,y:0,w:1,h:1},{x:0,y:0,w:0,h:1},{x:.9,y:0,w:.2,h:1},{x:0,y:-1,w:1,h:1}]) assert.throws(()=>validateRegion(r));
  assert.deepEqual(validateRegion({x:0,y:0,w:1,h:1}),{x:0,y:0,w:1,h:1});
});
test('typed intent appends through canonical input, is idempotent, keeps metadata private and cancel preserves edits',()=>{
 const p=page();p.type('action','I hide here');p.type('region-label','doorway');p.type('private-notes','SECRET');p.type('provenance','PRIVATE SOURCE');p.type('rights','PRIVATE RIGHTS');
 p.click('Use as draft');assert.equal(p.composer.input.value,'Existing draft\n\nI hide here (Reference: doorway)');assert.equal(p.saved.length,1);
 p.type('action','I wait');p.click('Use as draft');p.type('action','I hide here');p.click('Use as draft');assert.equal(p.saved.length,2);
 p.type('action','I wait');p.composer.input.value='Player revised';p.click('Use as draft');assert.equal(p.composer.input.value,'Player revised');
 p.click('Cancel');assert.equal(p.composer.input.value,'Player revised');p.click('Clear');assert.equal(p.composer.input.value,'Player revised');
});
test('exact chat staging survives revisit and stays isolated',()=>{
 const a=page('chat:ref/revisit');a.type('action','I hide here');a.controller.abort();
 const b=page();assert.equal(b.field('action').value,'');b.controller.abort();b.click('Clear');
 const again=page('chat:ref/revisit');assert.equal(again.field('action').value,'I hide here');again.click('Clear');
});
test('image validation, decoding failure and stale abort preserve composer and release resources',async()=>{
 const p=page();globalThis.createImageBitmap=async()=>({width:400,height:200,close(){}});
 await upload(p,image(2097153));assert.match(p.root.textContent,/2 MiB/);
 await upload(p,image(8,'image/gif'));assert.match(p.root.textContent,/PNG/);
 globalThis.createImageBitmap=async()=>({width:4097,height:200,close(){}});await upload(p,image());assert.match(p.root.textContent,/4096/);
 globalThis.createImageBitmap=async()=>{throw Error('broken');};await upload(p,image());assert.match(p.root.textContent,/decode/i);
 let resolve,closed=0;globalThis.createImageBitmap=()=>new Promise(r=>resolve=r);const pending=upload(p,image());await tick();p.controller.abort();resolve({width:20,height:20,close(){closed++;}});await pending;assert.equal(closed,1);assert.equal(p.root.querySelectorAll('img').length,0);assert.equal(p.composer.input.value,'Existing draft');
 p.click('Clear');
});
test('valid image overlay responds to controls; clear revokes preview and rejects invalid region use',async()=>{
 const p=page();let revoked=0;const oldCreate=URL.createObjectURL,oldRevoke=URL.revokeObjectURL;
 URL.createObjectURL=()=> 'blob:synthetic';URL.revokeObjectURL=()=>revoked++;
 globalThis.createImageBitmap=async()=>({width:400,height:200,close(){}});
 try {await upload(p,image());assert.equal(p.root.querySelectorAll('img').length,1);p.type('x','.9');p.type('w','.2');p.type('action','Hide');p.click('Use as draft');assert.equal(p.saved.length,0);p.type('w','.1');p.click('Use as draft');assert.equal(p.saved.length,1);p.click('Clear');assert.equal(revoked,1);} finally {URL.createObjectURL=oldCreate;URL.revokeObjectURL=oldRevoke;}
});
test('at most ten staged chats are retained without silently replacing earlier drafts',()=>{
 const pages=Array.from({length:11},()=>page());for(let i=0;i<pages.length;i++)pages[i].type('action',`Intent ${i}`);
 assert.match(pages.at(-1).root.textContent,/ten|10/);assert.equal(pages[0].field('action').value,'Intent 0');
 for(const p of pages)p.click('Clear');
});
test('replacement and cancel fence late decodes; invalid bytes retain the validated image',async()=>{
 const p=page(), pending=[];let closed=0,revoked=0;
 const oldCreate=URL.createObjectURL,oldRevoke=URL.revokeObjectURL;
 URL.createObjectURL=()=> 'blob:synthetic';URL.revokeObjectURL=()=>revoked++;
 globalThis.createImageBitmap=()=>new Promise(resolve=>pending.push(resolve));
 try {
  await upload(p,image());await upload(p,image());
  pending[1]({width:200,height:100,close(){closed++;}});await tick();
  pending[0]({width:10,height:10,close(){closed++;}});await tick();
  assert.equal(p.root.querySelector('img').getAttribute('width'),'200');assert.equal(closed,2);
  await upload(p,{size:8,type:'image/png',arrayBuffer:async()=>new Uint8Array(8).buffer});
  assert.match(p.root.textContent,/bytes/);assert.equal(p.root.querySelector('img').getAttribute('width'),'200');
  await upload(p,image());p.click('Cancel');pending[2]({width:300,height:100,close(){closed++;}});await tick();
  assert.equal(p.root.querySelector('img').getAttribute('width'),'200');
  p.controller.abort();assert.equal(revoked,1);
  p.click('Clear');
 } finally {URL.createObjectURL=oldCreate;URL.revokeObjectURL=oldRevoke;}
});
