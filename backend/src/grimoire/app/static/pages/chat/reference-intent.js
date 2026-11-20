// 070: references remain tab-memory annotations. Only reviewed public text crosses into the existing
// composer; its input event remains the sole owner of canonical draft persistence. With an imported sketch,
// Use as draft also hands the composer's attachment tray (./attach.js) one picture: the sketch with the circled
// region drawn on it. Posting carries it under the post as the player's uncommitted intent, in the post's own
// picture slot, the way any picture attached to a post is kept. Nothing here writes a map, actor, scene record or
// evidence, interprets the image or sends it to a model; provenance, rights and private notes never leave the panel.
const staged = new Map();
const MIME = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_BYTES = 2 * 1024 * 1024;
// the most one picture on a post may be (art/picker.js MAX_UPLOAD); an annotated sketch is refused above it
export const MAX_CARRIED = 32 * 1024 * 1024;
// a drag smaller than this, either way, is a stray tap: the region it would replace is kept
const MIN_DRAG = 0.01;
// a carried picture's note is at most 2000 characters (mediaapi MAX_NOTE_CHARS); the label is cut to fit
const MAX_NOTE_LABEL = 1500;

export function validateRegion(region) {
  const {x, y, w, h} = region;
  if (![x,y,w,h].every(Number.isFinite) || x < 0 || y < 0 || w <= 0 || h <= 0
      || x + w > 1 || y + h > 1) throw Error('Region must be finite, positive and contained within 0–1.');
  return {x,y,w,h};
}

// Two normalized points of a drag as a contained region, to three decimals (the numeric fields' precision).
export function dragRegion(from, to) {
  const clamp = v => Math.min(1, Math.max(0, v));
  const round = v => Math.round(v * 1000) / 1000;
  const [x0, x1] = [clamp(from.x), clamp(to.x)].sort((a, b) => a - b);
  const [y0, y1] = [clamp(from.y), clamp(to.y)].sort((a, b) => a - b);
  const x = round(x0), y = round(y0);
  return {x, y, w: Math.max(0, Math.min(round(x1 - x0), round(1 - x))), h: Math.max(0, Math.min(round(y1 - y0), round(1 - y)))};
}

// The draft action the panel offers: the typed action, and the spot it is about in the player's own words.
// Nothing interprets the sketch and no model is sent it, so a carried sketch always has a typed description:
// the post's text is all the table reads (Use as draft refuses a sketch with none).
export function draftAction(action, label) {
  return action + (label ? ' (Reference: ' + label + ')' : '');
}

// What the carried picture says of itself (its note in the campaign's art): public words only.
export function carriedNote(label, shape, r) {
  const at = n => Number(n.toFixed(3));
  const points = Array.from(label);  // code points, as the server counts the note, never half a character
  const named = points.length > MAX_NOTE_LABEL ? points.slice(0, MAX_NOTE_LABEL).join('') + '…' : label;
  return 'Player reference intent, not a world fact: ' + named + '; ' + shape + ' at x ' + at(r.x) + ', y '
    + at(r.y) + ', width ' + at(r.w) + ', height ' + at(r.h) + ' of the sketch.';
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

// The sketch with its region drawn on it, at its own size and in its own type: a light halo under a dark line, so
// the mark shows on a pale sketch or a dark map alike. Throws a reason to show; draws nothing anywhere else.
export async function annotate({file, width, height}, shape, r) {
  let source, release = () => {};
  if (globalThis.createImageBitmap) {
    source = await createImageBitmap(file);
    release = () => source.close && source.close();
  } else {
    const url = URL.createObjectURL(file);
    release = () => URL.revokeObjectURL(url);
    source = new Image();
    source.src = url;
    await source.decode();
  }
  try {
    const canvas = globalThis.OffscreenCanvas ? new OffscreenCanvas(width, height)
      : Object.assign(document.createElement('canvas'), {width, height});
    const g = canvas.getContext && canvas.getContext('2d');
    if (!g) throw Error('this browser cannot draw on the sketch');
    g.drawImage(source, 0, 0, width, height);
    const line = Math.max(3, Math.round(Math.min(width, height) * 0.008));
    const outline = () => {
      g.beginPath();
      if (shape === 'ellipse') g.ellipse((r.x + r.w / 2) * width, (r.y + r.h / 2) * height, r.w * width / 2, r.h * height / 2, 0, 0, 2 * Math.PI);
      else g.rect(r.x * width, r.y * height, r.w * width, r.h * height);
    };
    g.lineJoin = 'round';
    outline(); g.lineWidth = line * 2; g.strokeStyle = '#ffffff'; g.stroke();
    outline(); g.lineWidth = line; g.strokeStyle = '#c4004f'; g.stroke();
    const blob = canvas.convertToBlob ? await canvas.convertToBlob({type:file.type, quality:0.92})
      : await new Promise(resolve => canvas.toBlob(resolve, file.type, 0.92));
    if (!blob || !MIME.has(blob.type)) throw Error('this browser cannot save the marked sketch as PNG, JPEG or WebP');
    if (blob.size > MAX_CARRIED) throw Error('the marked sketch would be over 32 MiB');
    const ext = blob.type === 'image/jpeg' ? 'jpg' : blob.type.slice(6);
    return new File([blob], 'Marked sketch.' + ext, {type:blob.type});
  } finally { release(); }
}

// makeReferenceIntent(ctx, chat, { composer, attach }) -> the panel. `attach` is ./attach.js's: its stage/unstage
// put the marked sketch in the composer's waiting place. Without one (or where the chat keeps no pictures) the
// draft action is text only, and the panel says so.
export function makeReferenceIntent(ctx, chat, {composer, attach = null}) {
  const {ui} = ctx, {el} = ui;
  ctx.styles('reference-intent.css');
  const empty = () => ({action:'', 'region-label':'', provenance:'', rights:'', 'private-notes':'',
    shape:'rectangle', x:'0', y:'0', w:'1', h:'1', image:null, used:null, carried:null});
  let state = staged.get(chat) || empty(), generation = 0, previewURL = null, pending = false, decoding = null, drag = null, token = null, imageNode = null;
  const alive = () => !ctx.signal?.aborted;
  const status = el('p', {role:'status', 'aria-live':'polite', class:'reference-status'});
  const offered = el('p', {class:'reference-draft'});
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
      if (['x','y','w','h'].includes(name)) { stale(); draw(); } else offer();
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
  const setRegion = r => { for (const key of ['x','y','w','h']) { state[key]=String(r[key]); inputs[key].value=state[key]; } draw(); };
  const release = () => { if (previewURL) URL.revokeObjectURL(previewURL); previewURL=null; imageNode=null; };
  const svgNode = (tag,attrs) => {
    const node = document.createElementNS ? document.createElementNS('http://www.w3.org/2000/svg',tag) : document.createElement(tag);
    for(const [key,value] of Object.entries(attrs)) node.setAttribute(key,String(value));
    return node;
  };
  // the draft action as it would be offered, before anything reaches the composer
  const carries = () => Boolean(state.image && attach && attach.writes);
  function offer() {
    const action = (inputs.action?.value || '').trim();
    const label = (inputs['region-label']?.value || '').trim();
    offered.textContent = !action ? 'Draft action: type the intended action.'
      : carries() && !label ? 'Draft action: ' + action + ' · name the circled spot (for example, doorway) to attach your marked sketch'
      : 'Draft action: ' + draftAction(action, label) + (carries() ? ' · with your marked sketch' : '');
  }
  function draw() {
    offer();
    preview.replaceChildren();
    if (!state.image || !alive()) return;
    if (!previewURL) {
      previewURL=URL.createObjectURL(state.image.file);
      // one image node per preview, so circling redraws only the overlay
      imageNode=el('img',{src:previewURL,alt:'Local scene reference',width:state.image.width,height:state.image.height,draggable:'false'});
    }
    preview.append(imageNode);
    try {
      const r=region();
      const svg=svgNode('svg',{viewBox:'0 0 1000 1000',preserveAspectRatio:'none','aria-label':'Selected '+state.shape+' region',role:'img'});
      svg.append(svgNode(state.shape === 'ellipse' ? 'ellipse' : 'rect',state.shape === 'ellipse'
        ? {cx:(r.x+r.w/2)*1000,cy:(r.y+r.h/2)*1000,rx:r.w*500,ry:r.h*500}
        : {x:r.x*1000,y:r.y*1000,width:r.w*1000,height:r.h*1000}));
      preview.append(svg);
    } catch(error) { status.textContent=error.message; }
  }
  // Circling with a pointer (mouse, touch or pen): a drag across the sketch sets the region the numeric fields
  // show, which stay the keyboard way to set it. A tap or a cancelled drag keeps the region there was.
  const point = event => {
    const image = preview.querySelector('img');
    const box = image && image.getBoundingClientRect ? image.getBoundingClientRect() : null;
    if (!box || !(box.width > 0) || !(box.height > 0)) return null;
    return {x:(event.clientX-box.left)/box.width, y:(event.clientY-box.top)/box.height};
  };
  preview.addEventListener('pointerdown', event => {
    if (!state.image || pending || !alive() || (event.button !== undefined && event.button !== 0)) return;
    const at = point(event);
    if (!at || !remember()) return;
    drag = {from:at, id:event.pointerId, before:{x:state.x, y:state.y, w:state.w, h:state.h}};
    if (preview.setPointerCapture && event.pointerId !== undefined) preview.setPointerCapture(event.pointerId);
    if (event.preventDefault) event.preventDefault();
  });
  preview.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.id) return;
    const at = point(event);
    if (!at) return;
    const r = dragRegion(drag.from, at);
    if (r.w >= MIN_DRAG && r.h >= MIN_DRAG) setRegion(r);
  });
  const finish = (event, cancelled) => {
    if (!drag || event.pointerId !== drag.id) return;
    const {from, before} = drag, at = point(event);
    drag = null;
    const r = at && !cancelled ? dragRegion(from, at) : null;
    if (!r || r.w < MIN_DRAG || r.h < MIN_DRAG) {
      setRegion(before);
      status.textContent = cancelled ? 'Circling cancelled; the region is unchanged.' : 'Drag across the sketch to circle a spot; the region is unchanged.';
      return;
    }
    setRegion(r);
    status.textContent = 'Spot circled on your sketch; review the draft action. Nothing is sent.';
    stale();
  };
  preview.addEventListener('pointerup', event => finish(event, false));
  preview.addEventListener('pointercancel', event => finish(event, true));
  const shape=ui.choice('Region shape',[{value:'rectangle',label:'Rectangle'},{value:'ellipse',label:'Ellipse'}],{
    value:state.shape,onchange:value=>{if(remember()){state.shape=value;stale();draw();}}});
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
      release();state.image={file:chosen,...dimensions};stale();
      status.textContent='Image retained locally; drag across it to circle a spot, then review the action.';draw();
    } catch(error) {
      if(token === generation && alive()) status.textContent='Image not changed: '+(error.message || 'could not decode image.');
    } finally { if(token === generation) pending=false; }
  });
  // The marked sketch waiting for the next post is this panel's own while `token` names it in the tray. A
  // callback about an earlier staging (an upload that ends after a newer sketch was staged) changes nothing.
  const restage = carried => {
    const mine = {token:null};
    const done = attach.stage(carried.file, {note:carried.note, origin:'player reference intent', label:'your marked sketch',
      sent: () => {
        if (token !== mine.token) return;
        state.carried = null; state.used = null; token = null;
        if (alive()) status.textContent = 'Your marked sketch went with your post, as your intent only.';
      },
      removed: () => {
        if (token !== mine.token) return;
        state.carried = null; token = null;
        if (alive()) status.textContent = 'Marked sketch removed from your next post; your draft text is unchanged.';
      }});
    if (done && done.refused) return done.refused;
    token = mine.token = done; return null;
  };
  // Takes the panel's marked sketch out of the tray; false when it was not there to take, or already belongs to a
  // made post (waiting for Try again), which stays: its `sent`/`removed` still report what becomes of it.
  const unstage = () => {
    if (token === null) { state.carried = null; return false; }
    if (!(attach && attach.unstage && attach.unstage(token))) return false;
    token = null; state.carried = null; return true;
  };
  const inDraft = text => Boolean(text) && composer.input.value.includes(text);
  // A sketch, region or shape changed after its marked copy was attached: the copy no longer shows what the panel
  // does, so it comes off the next post rather than going with it unseen.
  const stale = () => {
    if (token === null && !state.carried) return;
    if (!unstage()) return;
    if (alive()) status.textContent = 'The sketch or region changed, so the marked sketch was taken off your next post. Use as draft to attach the new one.';
  };
  // The player took the draft text out of the composer: its sketch does not go with whatever they post instead.
  // (Posting clears the composer without an input event, so this never fires for the post itself.)
  composer.input.addEventListener('input', () => {
    if (token === null || !state.used || inDraft(state.used)) return;
    if (!unstage()) return;
    if (alive()) status.textContent = 'Your draft text for this reference was removed, so its marked sketch was taken off your next post.';
  }, {signal: ctx.signal});
  const use=ui.button('Use as draft',async()=>{
    if(!alive()) return;
    if(pending) {status.textContent='Wait for image validation before using this reference.';return;}
    try {
      const r = region();
      const action=inputs.action.value.trim(), label=inputs['region-label'].value.trim();
      if(!action) throw Error('Type the intended action first; an image is optional.');
      const sketch = state.image, carrying = carries();
      if(carrying && !label) throw Error('Name the circled spot (for example, doorway): the table reads only your words, never the sketch.');
      const text=draftAction(action, label);
      const key = sketch ? [text, state.shape, r.x, r.y, r.w, r.h].join('|') : null;
      // whether the composer still needs the text, read again after any wait: the player may have typed or
      // posted meanwhile, and only the composer as it is now may be appended to. Text-only, an intent used once is
      // not pressed on a player who edited it away; a marked sketch never goes without its text, so it is re-added.
      const needs = () => !inDraft(text) && (carrying || state.used !== text);
      const current = () => {
        try { const now = region(); return [draftAction(inputs.action.value.trim(), inputs['region-label'].value.trim()), state.shape, now.x, now.y, now.w, now.h].join('|'); }
        catch { return null; }
      };
      const fits = () => (composer.input.value+(composer.input.value ? '\n\n' : '')+text).length
        <= (Number(composer.input.getAttribute('maxlength')) || 20000);
      const sketchFresh = carrying && !(state.carried && state.carried.key === key && state.carried.image === sketch && token !== null);
      if(!needs() && !sketchFresh) {status.textContent='Already used; your current composer draft is unchanged.';return;}
      if(!remember()) return;
      if(needs() && !fits()) throw Error('Composer draft is full; shorten it before adding this intent.');
      const held = attach && attach.waits ? attach.waits() : null;
      if (sketchFresh && held === 'own') throw Error('Your next post already has an image attached. Remove it first: a post carries one picture.');
      if (sketchFresh && held === 'retry') throw Error('Your last post\'s picture is not attached yet. Try again or Remove it in the tray first.');
      if (sketchFresh) {
        pending = true;
        const epoch = generation;
        let marked;
        try { marked = await annotate(sketch, state.shape, r); }
        catch (error) { throw Error('Your marked sketch could not be prepared (' + (error.message || 'drawing failed') + '); nothing was added.'); }
        finally { pending = false; }
        if (epoch !== generation || !alive() || state.image !== sketch) return;
        // the region, shape or words changed while it was drawn: this copy shows the old ones
        if (current() !== key) { status.textContent = 'The reference changed while its sketch was drawn; nothing was added. Use as draft again.'; return; }
        if (needs() && !fits()) throw Error('Composer draft is full; shorten it before adding this intent.');
        const previous = token;
        const carried = {file:marked, key, image:sketch, note:carriedNote(label, state.shape, r)};
        const refused = restage(carried);
        if (refused === 'busy') throw Error(attach.waits && attach.waits() === 'retry'
          ? 'Your last post\'s picture is not attached yet. Try again or Remove it in the tray first.'
          : 'Your next post already has an image attached. Remove it first: a post carries one picture.');
        if (refused) { token = previous; state.carried = null; }
        else state.carried = carried;
      }
      const fresh = needs();
      if (fresh) {
        composer.input.value=composer.input.value+(composer.input.value ? '\n\n' : '')+text;
        state.used=text;
        composer.input.dispatchEvent(new Event('input',{bubbles:true}));
      }
      status.textContent = !carrying && sketch ? 'Added to your draft only; this chat cannot keep pictures, so the sketch stays here. Review before posting.'
        : state.carried ? (fresh ? 'Added to your draft, with your marked sketch waiting for the same post. Review before posting.'
          : 'Updated the marked sketch waiting for your next post; your composer draft is unchanged.')
        : 'Added to your draft only. Review before posting.';
      composer.input.focus();
    } catch(error) {status.textContent=error.message;}
  });
  const cancel=ui.button('Cancel',()=>{generation++;decoding?.abort();pending=false;root.open=false;status.textContent='Cancelled; composer unchanged. Local reference retained.';composer.input.focus();});
  const clear=ui.button('Clear reference',()=>{
    generation++;decoding?.abort();pending=false;drag=null;unstage();release();staged.delete(chat);state=empty();
    for(const [name,input] of Object.entries(inputs)) input.value=state[name];
    shape.select.value=state.shape;file.value='';preview.replaceChildren();offer();
    status.textContent='Reference cleared; composer unchanged.';
  });
  const root=el('details',{class:'reference-intent'},el('summary',{},'Reference intent (local draft)'),
    el('p',{},'Local, unsynced reference; cleared on reload. Ordinary posting sends composer text. With a sketch, Use as draft also attaches '
      + 'the sketch marked with your region to your next post, shown under it as your intent; it never changes a map or the scene, and no model is sent it. '
      + 'Source, rights and private notes stay here. No interpretation is inferred.'),
    el('label',{for:file.id},'Import sketch or image (PNG/JPEG/WebP, 2 MiB, 4096 × 4096 maximum)'),file,preview,controls,
    field('region-label','Public region description (for example, doorway)'),field('action','Intended action (typed input works without an image)',{multiline:true}),
    offered,
    field('provenance','Source / provenance (local only)'),field('rights','Rights / permission (local only)'),field('private-notes','Privacy notes / corrections (local only)',{multiline:true}),
    el('div',{class:'reference-actions'},use,cancel,clear),status);
  ctx.signal?.addEventListener('abort',()=>{generation++;decoding?.abort();pending=false;drag=null;token=null;release();preview.replaceChildren();},{once:true});
  // Coming back to the chat: a marked sketch whose draft text is still in the composer waits for the post again
  // (the tray belongs to the page, so it was emptied when the page left).
  if (state.carried && attach && attach.writes && inDraft(state.used)) {
    if (restage(state.carried)) { state.carried = null; status.textContent = 'Your marked sketch was not attached again; Use as draft to attach it.'; }
  } else state.carried = null;
  draw();
  return root;
}
