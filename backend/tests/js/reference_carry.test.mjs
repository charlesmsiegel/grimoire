// 070: circling a spot on an imported sketch and carrying the marked sketch with the next post as the player's
// uncommitted intent, through the composer's attachment tray. The tray, canvas and bitmap decoder are stand-ins
// that record what the panel asked of them; the page-level journey is reference.e2e.mjs.
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {install, byText} from './fakedom.mjs';
import * as ui from '../../src/grimoire/app/static/shell/ui.js';
import {makeReferenceIntent, dragRegion, draftAction} from '../../src/grimoire/app/static/pages/chat/reference-intent.js';

const PNG = [137,80,78,71,13,10,26,10];
// previews of stand-in files: Node's own URL.createObjectURL takes only a real Blob
URL.createObjectURL = () => 'blob:synthetic';
URL.revokeObjectURL = () => {};
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
let serial = 0;

// A canvas that records its drawing and saves `saved` (default: a small PNG of the same type).
function canvases({context = true, saved} = {}) {
  const made = [];
  globalThis.OffscreenCanvas = class {
    constructor(width, height) { this.width = width; this.height = height; this.ops = []; made.push(this); }
    getContext() {
      if (!context) return null;
      const ops = this.ops;
      return new Proxy({}, {
        get: (_t, name) => (...args) => ops.push([name, ...args]),
        set: (_t, name, value) => { ops.push(['set ' + String(name), value]); return true; },
      });
    }
    async convertToBlob({type}) { return saved !== undefined ? saved : new Blob([new Uint8Array(PNG)], {type}); }
  };
  return made;
}

// The tray of ./attach.js as the panel uses it.
function tray({writes = true, busy = false} = {}) {
  const t = {writes, staged: [], unstaged: [], holding: null,
    stage(file, carry) {
      if (!writes) return {refused: 'unsupported'};
      if (busy) return {refused: 'busy'};
      const token = t.staged.push({file, carry});
      t.holding = token;
      return token;
    },
    unstage(token) { t.unstaged.push(token); if (t.holding === token) t.holding = null; },
  };
  return t;
}

function page({chat = `chat:carry/${serial++}`, attach = tray(), draft = 'Existing draft'} = {}) {
  install('http://localhost');
  globalThis.createImageBitmap = async () => ({width: 400, height: 200, close() {}});
  const controller = new AbortController(), saved = [];
  const composer = ui.field('Your turn', {value: draft, multiline: true});
  const dispatch = composer.input.dispatchEvent.bind(composer.input);
  composer.input.dispatchEvent = event => dispatch({type: event.type});
  composer.input.addEventListener('input', () => saved.push(composer.input.value));
  const root = makeReferenceIntent({ui, styles() {}, signal: controller.signal}, chat, {composer, attach});
  document.body.append(root, composer.node);
  const field = name => root.querySelector(`[name="${name}"]`);
  const type = (name, value) => { field(name).value = value; field(name).dispatchEvent({type: 'input'}); };
  const use = async () => { byText(root, 'button', 'Use as draft')[0].click(); await tick(); await tick(); };
  const status = () => root.querySelector('p.reference-status').textContent;
  return {root, composer, controller, saved, field, type, use, status, attach, chat};
}

async function sketch(p) {
  const file = {size: 8, type: 'image/png', arrayBuffer: async () => new Uint8Array(PNG).buffer};
  p.field('reference-file').files = [file];
  p.field('reference-file').dispatchEvent({type: 'change'});
  await tick();
  const image = p.root.querySelector('img');
  image.getBoundingClientRect = () => ({left: 10, top: 20, width: 400, height: 200});
  return image;
}

// a drag on the sketch, in page pixels
function circle(p, from, to, {cancel = false} = {}) {
  const preview = p.root.querySelector('div.reference-preview');
  preview.dispatchEvent({type: 'pointerdown', pointerId: 1, button: 0, clientX: from[0], clientY: from[1]});
  preview.dispatchEvent({type: 'pointermove', pointerId: 1, clientX: (from[0] + to[0]) / 2, clientY: (from[1] + to[1]) / 2});
  preview.dispatchEvent({type: 'pointermove', pointerId: 2, clientX: 400, clientY: 210});  // another finger moves nothing
  preview.dispatchEvent({type: cancel ? 'pointercancel' : 'pointerup', pointerId: 1, clientX: to[0], clientY: to[1]});
}
const regionOf = p => ['x', 'y', 'w', 'h'].map(key => p.field(key).value);

test('a drag is a contained region in either direction; the offered draft names a sketched spot', () => {
  assert.deepEqual(dragRegion({x: .7, y: .9}, {x: .2, y: .1}), {x: .2, y: .1, w: .5, h: .8});
  assert.deepEqual(dragRegion({x: -.5, y: .5}, {x: 1.5, y: 2}), {x: 0, y: .5, w: 1, h: .5});
  assert.equal(draftAction('I hide here', 'doorway', true), 'I hide here (Reference: doorway)');
  assert.equal(draftAction('I hide here', '', true), 'I hide here (Reference: the circled spot on my sketch)');
  assert.equal(draftAction('I hide here', '', false), 'I hide here');
});

test('circling with a pointer sets the region the fields show; a tap or a cancel keeps it', async () => {
  const p = page();
  await sketch(p);
  p.click = name => byText(p.root, 'button', name)[0].click();
  p.root.querySelector('select').value = 'ellipse';
  p.root.querySelector('select').dispatchEvent({type: 'change'});
  circle(p, [110, 70], [230, 190]);
  assert.deepEqual(regionOf(p), ['0.25', '0.25', '0.3', '0.6']);
  const ellipse = p.root.querySelector('ellipse');
  assert.deepEqual(['cx', 'cy', 'rx', 'ry'].map(name => Number(ellipse.getAttribute(name))), [400, 550, 150, 300]);
  assert.match(p.status(), /circled/);
  circle(p, [200, 100], [201, 100]);  // a tap
  assert.deepEqual(regionOf(p), ['0.25', '0.25', '0.3', '0.6']);
  circle(p, [10, 20], [410, 220], {cancel: true});
  assert.deepEqual(regionOf(p), ['0.25', '0.25', '0.3', '0.6']);
  assert.match(p.status(), /cancelled/);
  assert.equal(p.composer.input.value, 'Existing draft', 'circling sends nothing');
  p.type('action', 'I hide here');
  assert.match(p.root.querySelector('p.reference-draft').textContent, /circled spot on my sketch/);
  p.click('Clear');
});

test('Use as draft offers the circled doorway and carries the marked sketch, with public words only', async () => {
  const made = canvases();
  const p = page();
  await sketch(p);
  p.root.querySelector('select').value = 'ellipse';
  p.root.querySelector('select').dispatchEvent({type: 'change'});
  circle(p, [110, 70], [230, 190]);
  p.type('action', 'I hide here'); p.type('region-label', 'doorway');
  p.type('private-notes', 'SECRET NOTE'); p.type('provenance', 'PRIVATE SOURCE'); p.type('rights', 'PRIVATE RIGHTS');
  assert.equal(p.root.querySelector('p.reference-draft').textContent, 'Draft action: I hide here (Reference: doorway) · with your marked sketch');
  await p.use();
  assert.equal(p.composer.input.value, 'Existing draft\n\nI hide here (Reference: doorway)');
  assert.equal(p.saved.length, 1);
  const [{file, carry}] = p.attach.staged;
  assert.equal(file.type, 'image/png');
  assert.equal(carry.origin, 'player reference intent');
  assert.equal(carry.note, 'Player reference intent, not a world fact: doorway; ellipse at x 0.25, y 0.25, width 0.3, height 0.6 of the sketch.');
  assert.ok(!/SECRET|PRIVATE/.test(JSON.stringify(carry)) && !/SECRET|PRIVATE/.test(p.composer.input.value));
  // the sketch at its own size, then the ellipse drawn over it twice (halo, line)
  const [{width, height, ops}] = made;
  assert.deepEqual([width, height], [400, 200]);
  assert.equal(ops[0][0], 'drawImage');
  const ellipses = ops.filter(op => op[0] === 'ellipse').map(op => op.slice(1, 5).map(v => Math.round(v * 1000) / 1000));
  assert.deepEqual(ellipses, [[160, 110, 60, 60], [160, 110, 60, 60]]);
  assert.equal(ops.filter(op => op[0] === 'stroke').length, 2);
  assert.match(p.status(), /marked sketch waiting/);
  // the same intent again changes nothing; a new circle replaces the waiting sketch and leaves the composer
  await p.use();
  assert.equal(p.attach.staged.length, 1);
  assert.match(p.status(), /Already used/);
  circle(p, [10, 20], [210, 120]);
  await p.use();
  assert.equal(p.attach.staged.length, 2);
  assert.equal(p.saved.length, 1);
  assert.match(p.attach.staged[1].carry.note, /x 0, y 0, width 0.5, height 0.5/);
  assert.match(p.status(), /composer draft is unchanged/);
  // the post took it: the panel says so, and Use as draft offers the same intent for the next post
  p.attach.staged[1].carry.sent('turn:1');
  assert.match(p.status(), /went with your post/);
  byText(p.root, 'button', 'Clear')[0].click();
  assert.deepEqual(p.attach.unstaged, [], 'nothing was waiting once it was sent');
});

test('with no label the spot is named as circled; Clear and the tray Remove take the sketch back', async () => {
  canvases();
  const p = page({draft: ''});
  await sketch(p);
  circle(p, [10, 20], [110, 70]);
  p.type('action', 'I hide here');
  await p.use();
  assert.equal(p.composer.input.value, 'I hide here (Reference: the circled spot on my sketch)');
  assert.match(p.attach.staged[0].carry.note, /the circled spot on my sketch; rectangle/);
  p.attach.staged[0].carry.removed();
  assert.match(p.status(), /removed from your next post/);
  assert.equal(p.composer.input.value, 'I hide here (Reference: the circled spot on my sketch)', 'the draft text is the player\'s');
  await p.use();  // the text is there; the sketch goes back to the tray
  assert.equal(p.attach.staged.length, 2);
  byText(p.root, 'button', 'Clear')[0].click();
  assert.deepEqual(p.attach.unstaged, [2]);
  assert.equal(p.composer.input.value, 'I hide here (Reference: the circled spot on my sketch)');
});

test('an image the player attached is never replaced, and a failed drawing adds nothing', async () => {
  canvases();
  const busy = page({attach: tray({busy: true})});
  await sketch(busy);
  busy.type('action', 'I hide here');
  await busy.use();
  assert.equal(busy.composer.input.value, 'Existing draft');
  assert.match(busy.status(), /already has an image attached\. Remove it first/);
  byText(busy.root, 'button', 'Clear')[0].click();
  for (const broken of [{context: false}, {saved: null}, {saved: new Blob([new Uint8Array(4)], {type: 'image/gif'})},
                        {saved: {size: 32 * 1024 * 1024 + 1, type: 'image/png'}}]) {
    canvases(broken);
    const p = page();
    await sketch(p);
    p.type('action', 'I hide here');
    await p.use();
    assert.equal(p.composer.input.value, 'Existing draft', JSON.stringify(broken));
    assert.equal(p.attach.staged.length, 0);
    assert.match(p.status(), /could not be prepared .*nothing was added/);
    byText(p.root, 'button', 'Clear')[0].click();
  }
});

test('a chat that keeps no pictures gets the text only, and says so', async () => {
  const made = canvases();
  const p = page({attach: tray({writes: false})});
  await sketch(p);
  p.type('action', 'I hide here'); p.type('region-label', 'doorway');
  assert.equal(p.root.querySelector('p.reference-draft').textContent, 'Draft action: I hide here (Reference: doorway)');
  await p.use();
  assert.equal(p.composer.input.value, 'Existing draft\n\nI hide here (Reference: doorway)');
  assert.equal(made.length, 0, 'nothing is drawn when nothing can be carried');
  assert.match(p.status(), /cannot keep pictures/);
  byText(p.root, 'button', 'Clear')[0].click();
});

test('coming back to the chat attaches the marked sketch again while its text is still in the draft', async () => {
  canvases();
  const chat = 'chat:carry/revisit';
  const first = page({chat});
  await sketch(first);
  first.type('action', 'I hide here'); first.type('region-label', 'doorway');
  await first.use();
  const draft = first.composer.input.value;
  first.controller.abort();
  const back = page({chat, draft});
  assert.equal(back.attach.staged.length, 1, 'staged again in the new page\'s tray');
  assert.match(back.attach.staged[0].carry.note, /doorway/);
  back.controller.abort();
  const edited = page({chat, draft: 'I changed my mind'});
  assert.equal(edited.attach.staged.length, 0, 'the player took the text out: the sketch is not attached behind their back');
  byText(edited.root, 'button', 'Clear')[0].click();
});
