// 070: references remain tab-memory annotations. Only reviewed public text crosses into the existing
// composer; its input event remains the sole owner of canonical draft persistence. No API/storage calls.
const staged = new Map();
const MIME = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_BYTES = 2 * 1024 * 1024;

export function validateRegion(region) {
  const {x, y, w, h} = region;
  if (![x,y,w,h].every(Number.isFinite) || x < 0 || y < 0 || w <= 0 || h <= 0
      || x + w > 1 || y + h > 1) throw Error('Region must be finite, positive and contained within 0–1.');
  return {x,y,w,h};
}

function signature(bytes, mime) {
  const starts = values => values.every((v,i) => bytes[i] === v);
  if (mime === 'image/png') return starts([137,80,78,71,13,10,26,10]);
  if (mime === 'image/jpeg') return starts([255,216,255]);
  return starts([82,73,70,70]) && [87,69,66,80].every((v,i) => bytes[i+8] === v);
}

async function decode(file, signal) {
  if (globalThis.createImageBitmap) {
    const bitmap = await createImageBitmap(file);
    try { return {width:bitmap.width, height:bitmap.height}; } finally { bitmap.close(); }
  }
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    let abort;
    const cancelled = new Promise((_, reject) => {
      abort = () => reject(Error('Image decoding cancelled.'));
      signal.addEventListener('abort', abort, {once:true});
      if (signal.aborted) abort();
    });
    try { await Promise.race([image.decode(), cancelled]); }
    finally { signal.removeEventListener('abort', abort); }
    return {width:image.naturalWidth, height:image.naturalHeight};
  } finally { URL.revokeObjectURL(url); }
}

export function makeReferenceIntent(ctx, chat, {composer}) {
  const {ui} = ctx, {el} = ui;
  ctx.styles('reference-intent.css');
  const empty = () => ({action:'', 'region-label':'', provenance:'', rights:'', 'private-notes':'',
    shape:'rectangle', x:'0', y:'0', w:'1', h:'1', image:null, used:null});
  let state = staged.get(chat) || empty(), generation = 0, previewURL = null, pending = false, decoding = null;
  const alive = () => !ctx.signal?.aborted;
  const status = el('p', {role:'status', 'aria-live':'polite', class:'reference-status'});
  const preview = el('div', {class:'reference-preview'});
  const controls = el('div', {class:'reference-region'});
  const inputs = {};
  const remember = () => {
    if (!alive()) return false;
    if (!staged.has(chat) && staged.size >= 10) {
      status.textContent = 'Only ten (10) chats can retain references. Clear one before staging another.';
      return false;
    }
    staged.set(chat,state); return true;
  };
  const field = (name,label,{type='text', multiline=false}={}) => {
    const f = ui.field(label,{type,multiline,value:state[name],name});
    inputs[name]=f.input;
    f.input.setAttribute('maxlength','2000');
    if(type === 'number') {
      f.input.setAttribute('min','0'); f.input.setAttribute('max','1'); f.input.setAttribute('step','0.01');
    }
    f.input.addEventListener('input',()=>{
      if (!remember()) return;
      state[name]=f.input.value.slice(0,2000);
      status.textContent='Local reference updated; not sent.';
      if (['x','y','w','h'].includes(name)) draw();
    });
    return f.node;
  };
  const region = () => {
    const r = {};
    for(const key of ['x','y','w','h']) {
      if (!inputs[key].value.trim()) throw Error('Fill in every region coordinate.');
      r[key]=Number(inputs[key].value);
    }
    return validateRegion(r);
  };
  const release = () => { if (previewURL) URL.revokeObjectURL(previewURL); previewURL=null; };
  const svgNode = (tag,attrs) => {
    const node = document.createElementNS ? document.createElementNS('http://www.w3.org/2000/svg',tag) : document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    return node;
  };
  function draw() {
    preview.replaceChildren();
    if (!state.image || !alive()) return;
    if (!previewURL) previewURL=URL.createObjectURL(state.image.file);
    const image=el('img',{src:previewURL,alt:'Local scene reference',width:state.image.width,height:state.image.height});
    preview.append(image);
    try {
      const r=region();
      const svg=svgNode('svg',{viewBox:'0 0 1000 1000',preserveAspectRatio:'none','aria-label':'Selected '+state.shape+' region',role:'img'});
      svg.append(svgNode(state.shape === 'ellipse' ? 'ellipse' : 'rect',state.shape === 'ellipse'
        ? {cx:(r.x+r.w/2)*1000,cy:(r.y+r.h/2)*1000,rx:r.w*500,ry:r.h*500}
        : {x:r.x*1000,y:r.y*1000,width:r.w*1000,height:r.h*1000}));
      preview.append(svg);
    } catch(error) { status.textContent=error.message; }
  }
  const shape=ui.choice('Region shape',[{value:'rectangle',label:'Rectangle'},{value:'ellipse',label:'Ellipse'}],{
    value:state.shape,onchange:value=>{if(remember()){state.shape=value;draw();}}});
  controls.append(shape.node,...[['x','Region x (0–1)'],['y','Region y (0–1)'],['w','Region width (0–1)'],['h','Region height (0–1)']].map(([key,label])=>field(key,label,{type:'number'})));
  const file=el('input',{type:'file',accept:'image/png,image/jpeg,image/webp',name:'reference-file',id:ui.id('reference-file')});
  file.addEventListener('change',async()=>{
    const chosen=file.files[0]; file.value='';
    if (!chosen || !alive()) return;
    decoding?.abort(); decoding=new AbortController();
    const token=++generation;
    pending=true;
    try {
      if(!MIME.has(chosen.type)) throw Error('Choose PNG, JPEG or WebP only.');
      if(chosen.size <= 0 || chosen.size > MAX_BYTES) throw Error('Image must be at most 2 MiB and nonempty.');
      const bytes=new Uint8Array(await chosen.arrayBuffer());
      if(bytes.length > MAX_BYTES || !signature(bytes,chosen.type)) throw Error('Image bytes do not match PNG, JPEG or WebP type.');
      if(token !== generation || !alive()) return;
      let dimensions;
      try { dimensions=await decode(chosen,decoding.signal); } catch { throw Error('Could not decode image.'); }
      if(token !== generation || !alive()) return;
      if(!Number.isInteger(dimensions.width) || !Number.isInteger(dimensions.height) || dimensions.width < 1 || dimensions.height < 1
          || dimensions.width > 4096 || dimensions.height > 4096) throw Error('Decoded image must be at most 4096 × 4096 pixels.');
      if(!remember()) return;
      release();state.image={file:chosen,...dimensions};status.textContent='Image retained locally; review the region and action.';draw();
    } catch(error) {
      if(token === generation && alive()) status.textContent='Image not changed: '+(error.message || 'could not decode image.');
    } finally { if(token === generation) pending=false; }
  });
  const use=ui.button('Use as draft',()=>{
    if(!alive()) return;
    if(pending) {status.textContent='Wait for image validation before using this reference.';return;}
    try {
      region();
      const action=inputs.action.value.trim(), label=inputs['region-label'].value.trim();
      if(!action) throw Error('Type the intended action first; an image is optional.');
      const text=action+(label ? ' (Reference: '+label+')' : '');
      if(state.used === text || ('\n\n'+composer.input.value+'\n\n').includes('\n\n'+text+'\n\n')) {status.textContent='Already used; your current composer draft is unchanged.';return;}
      if(!remember()) return;
      const next=composer.input.value+(composer.input.value ? '\n\n' : '')+text;
      const max=Number(composer.input.getAttribute('maxlength')) || 20000;
      if(next.length > max) throw Error('Composer draft is full; shorten it before adding this intent.');
      composer.input.value=next;
      composer.input.dispatchEvent(new Event('input',{bubbles:true}));
      state.used=text;
      status.textContent='Added to your draft only. Review before posting.';
      composer.input.focus();
    } catch(error) {status.textContent=error.message;}
  });
  const cancel=ui.button('Cancel',()=>{generation++;decoding?.abort();pending=false;root.open=false;status.textContent='Cancelled; composer unchanged. Local reference retained.';composer.input.focus();});
  const clear=ui.button('Clear reference',()=>{
    generation++;decoding?.abort();pending=false;release();staged.delete(chat);state=empty();
    for(const [name,input] of Object.entries(inputs)) input.value=state[name];
    shape.select.value=state.shape;file.value='';preview.replaceChildren();status.textContent='Reference cleared; composer unchanged.';
  });
  const root=el('details',{class:'reference-intent'},el('summary',{},'Reference intent (local draft)'),
    el('p',{},'Local, unsynced reference; cleared on reload. No image or metadata is uploaded. Ordinary posting sends composer text only. No interpretation or scene changes are inferred.'),
    el('label',{for:file.id},'Import sketch or image (PNG/JPEG/WebP, 2 MiB, 4096 × 4096 maximum)'),file,preview,controls,
    field('region-label','Public region description (for example, doorway)'),field('action','Intended action (typed input works without an image)',{multiline:true}),
    field('provenance','Source / provenance (local only)'),field('rights','Rights / permission (local only)'),field('private-notes','Privacy notes / corrections (local only)',{multiline:true}),
    el('div',{class:'reference-actions'},use,cancel,clear),status);
  ctx.signal?.addEventListener('abort',()=>{generation++;decoding?.abort();pending=false;release();preview.replaceChildren();},{once:true});
  draw();
  return root;
}
