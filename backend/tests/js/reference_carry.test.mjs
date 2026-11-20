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
    // like attach.js: only the waiting sketch can be taken back, and not once a made post waits on Try again
    unstage(token) {
      t.unstaged.push(token);
      if (t.holding !== token || t.retry) return false;
      t.holding = null;
      return true;
    },
    waits: () => (t.holding === null ? null : t.retry ? 'retry' : 'staged'),
    retry: false,
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

test('a drag is a contained region in either direction; the offered draft names the spot in the player\'s words', () => {
  assert.deepEqual(dragRegion({x: .7, y: .9}, {x: .2, y: .1}), {x: .2, y: .1, w: .5, h: .8});
  assert.deepEqual(dragRegion({x: -.5, y: .5}, {x: 1.5, y: 2}), {x: 0, y: .5, w: 1, h: .5});
  assert.equal(draftAction('I hide here', 'doorway'), 'I hide here (Reference: doorway)');
  assert.equal(draftAction('I hide here', ''), 'I hide here');
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
  assert.match(p.root.querySelector('p.reference-draft').textContent, /name the circled spot/);
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
  assert.deepEqual(p.attach.unstaged, [1], 'a new circle takes the old marked sketch off the next post');
  assert.match(p.status(), /taken off your next post/);
  await p.use();
  assert.equal(p.attach.staged.length, 2);
  assert.equal(p.saved.length, 1);
  assert.match(p.attach.staged[1].carry.note, /x 0, y 0, width 0.5, height 0.5/);
  assert.match(p.status(), /composer draft is unchanged/);
  // the post took it: the panel says so, and Use as draft offers the same intent for the next post
  p.attach.staged[1].carry.sent('turn:1');
  assert.match(p.status(), /went with your post/);
  byText(p.root, 'button', 'Clear')[0].click();
  assert.deepEqual(p.attach.unstaged, [1], 'nothing was waiting once it was sent');
});

test('a sketch needs the spot named; Clear, the tray Remove and deleting the draft text take the sketch back', async () => {
  canvases();
  const p = page({draft: ''});
  await sketch(p);
  circle(p, [10, 20], [110, 70]);
  p.type('action', 'I hide here');
  await p.use();
  assert.equal(p.composer.input.value, '', 'nobody reads the sketch, so an unnamed spot is not offered');
  assert.equal(p.attach.staged.length, 0);
  assert.match(p.status(), /Name the circled spot/);
  p.type('region-label', 'doorway');
  await p.use();
  assert.equal(p.composer.input.value, 'I hide here (Reference: doorway)');
  assert.match(p.attach.staged[0].carry.note, /doorway; rectangle/);
  p.attach.staged[0].carry.removed();
  assert.match(p.status(), /removed from your next post/);
  assert.equal(p.composer.input.value, 'I hide here (Reference: doorway)', 'the draft text is the player\'s');
  await p.use();  // the text is there; the sketch goes back to the tray
  assert.equal(p.attach.staged.length, 2);
  p.attach.staged[0].carry.sent('turn:old');  // a stale callback about the first staging changes nothing
  byText(p.root, 'button', 'Clear')[0].click();
  assert.deepEqual(p.attach.unstaged, [2]);
  assert.equal(p.composer.input.value, 'I hide here (Reference: doorway)');
  // deleting the reference's text from the composer takes its sketch off the post the player makes instead
  const q = page({draft: ''});
  await sketch(q);
  q.type('action', 'I hide here'); q.type('region-label', 'doorway');
  await q.use();
  q.composer.input.value = 'I run instead';
  q.composer.input.dispatchEvent({type: 'input'});
  assert.deepEqual(q.attach.unstaged, [1]);
  assert.match(q.status(), /draft text for this reference was removed/);
  byText(q.root, 'button', 'Clear')[0].click();
});

test('typing or posting while the sketch is drawn: the composer is appended as it is then, never restored', async () => {
  let finish;
  const made = canvases();
  const p = page({draft: 'I creep forward'});
  await sketch(p);
  globalThis.OffscreenCanvas.prototype.convertToBlob = function ({type}) {
    return new Promise(resolve => { finish = () => resolve(new Blob([new Uint8Array(PNG)], {type})); });
  };
  p.type('action', 'I hide here'); p.type('region-label', 'doorway');
  byText(p.root, 'button', 'Use as draft')[0].click();
  await tick();
  p.composer.input.value = 'Wait';  // posted meanwhile, then typed again
  finish();
  await tick(); await tick();
  assert.equal(made.length, 1);
  assert.equal(p.composer.input.value, 'Wait\n\nI hide here (Reference: doorway)');
  byText(p.root, 'button', 'Clear')[0].click();
});

test('a long description is cut in the note, and a post\'s picture waiting for Try again is never replaced', async () => {
  canvases();
  const p = page();
  await sketch(p);
  p.type('action', 'I hide here'); p.type('region-label', 'd'.repeat(2000));
  await p.use();
  const {note} = p.attach.staged[0].carry;
  assert.ok(note.length <= 2000, note.length);
  assert.match(note, /d…; rectangle at x 0/);
  byText(p.root, 'button', 'Clear')[0].click();
  const retry = tray();
  retry.waits = () => 'retry';
  const q = page({attach: retry});
  await sketch(q);
  q.type('action', 'I hide here'); q.type('region-label', 'doorway');
  await q.use();
  assert.equal(q.attach.staged.length, 0);
  assert.equal(q.composer.input.value, 'Existing draft');
  assert.match(q.status(), /Try again or Remove/);
  byText(q.root, 'button', 'Clear')[0].click();
});

test('an image the player attached is never replaced, and a failed drawing adds nothing', async () => {
  canvases();
  const busy = page({attach: tray({busy: true})});
  await sketch(busy);
  busy.type('action', 'I hide here'); busy.type('region-label', 'doorway');
  await busy.use();
  assert.equal(busy.composer.input.value, 'Existing draft');
  assert.match(busy.status(), /already has an image attached\. Remove it first/);
  byText(busy.root, 'button', 'Clear')[0].click();
  for (const broken of [{context: false}, {saved: null}, {saved: new Blob([new Uint8Array(4)], {type: 'image/gif'})},
                        {saved: {size: 32 * 1024 * 1024 + 1, type: 'image/png'}}]) {
    canvases(broken);
    const p = page();
    await sketch(p);
    p.type('action', 'I hide here'); p.type('region-label', 'doorway');
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

test('the sketch goes only with its own words, and only the outline the player sees when it was drawn', async () => {
  // the region typed anew while the sketch is drawn (circling waits for it): the old outline is not attached
  let finish;
  canvases();
  const p = page({draft: ''});
  await sketch(p);
  globalThis.OffscreenCanvas.prototype.convertToBlob = function ({type}) {
    return new Promise(resolve => { finish = () => resolve(new Blob([new Uint8Array(PNG)], {type})); });
  };
  p.type('action', 'I hide here'); p.type('region-label', 'doorway');
  byText(p.root, 'button', 'Use as draft')[0].click();
  await tick();
  circle(p, [210, 120], [410, 220]);
  assert.deepEqual(regionOf(p), ['0', '0', '1', '1'], 'no circling while the sketch is drawn');
  p.type('x', '0.5');
  finish();
  await tick(); await tick();
  assert.equal(p.attach.staged.length, 0);
  assert.equal(p.composer.input.value, '');
  assert.match(p.status(), /changed while its sketch was drawn; nothing was added/);
  byText(p.root, 'button', 'Clear')[0].click();

  // editing around the drafted words keeps the sketch; replacing them takes it off, and taking the intent again
  // puts the words back with it rather than sending the sketch under someone else's words
  canvases();
  const q = page({draft: ''});
  await sketch(q);
  q.type('action', 'I hide here'); q.type('region-label', 'doorway');
  await q.use();
  q.composer.input.value = 'I hide here (Reference: doorway) \nand hold my breath';
  q.composer.input.dispatchEvent({type: 'input'});
  assert.deepEqual(q.attach.unstaged, [], 'continuing the line is not deleting it');
  q.composer.input.value = 'I draw my sword';
  q.composer.input.dispatchEvent({type: 'input'});
  assert.deepEqual(q.attach.unstaged, [1]);
  await q.use();
  assert.equal(q.attach.staged.length, 2);
  assert.equal(q.composer.input.value, 'I draw my sword\n\nI hide here (Reference: doorway)');
  byText(q.root, 'button', 'Clear')[0].click();

  // after the post was made but its picture waits on Try again, the panel neither claims it was taken back nor
  // loses track of it: Try again reporting it sent still reaches the panel
  canvases();
  const r = page({draft: ''});
  await sketch(r);
  r.type('action', 'I hide here'); r.type('region-label', 'doorway');
  await r.use();
  r.attach.retry = true;
  r.composer.input.value = 'x';
  r.composer.input.dispatchEvent({type: 'input'});
  byText(r.root, 'button', 'Clear')[0].click();
  assert.doesNotMatch(r.status(), /taken off|removed from/);
  r.attach.staged[0].carry.sent('turn:2');
  assert.match(r.status(), /went with your post/);
});

test('a short text-only action inside other words is still offered', async () => {
  const p = page({draft: 'Attack the guard? No, wait.'});
  p.type('action', 'Attack');
  await p.use();
  assert.equal(p.composer.input.value, 'Attack the guard? No, wait.\n\nAttack');
  byText(p.root, 'button', 'Clear')[0].click();
});
