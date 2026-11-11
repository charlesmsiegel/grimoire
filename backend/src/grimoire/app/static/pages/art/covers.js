// Pictures beyond a character's own slots, for any page (docs/MEDIA_SYNC_UI.md): the cover of an entry, of a
// source and of a campaign, and any other one slot of one subject. A page imports what it needs:
//
//   import { coverBand, coverTile, shownOf } from "/static/pages/art/covers.js";
//
//   shownOf(ctx, subject, slot, { fresh })  what one slot shows, as media.shown answers it; null where it cannot be read
//   pictureOf(ctx, subject, slot)      a promise of the picture's URL (for ui.avatar and friends), or of null
//   coverBand(ctx, options, ...text)   a cover band with "Change cover" and "Remove", or a dashed block to add one
//   coverTile(ctx, subject, face)      a library tile's face with its cover; returns the place for "Add cover"
//   slotButton(ctx, options)           one small picture slot (a lorebook entry's): the picture, or a way to add it
//   pictureRow(ctx, options)           a row of named slots of one subject (a scenario's backdrops), with a way to add
//   lorePictures(ctx, options)         a lorebook's entry pictures: a slot per entry, and a note of orphaned ones
//   entryPictures(ctx, options)        an entry sheet's pictures section ("Scene backdrops" for a scenario)
//
// Reads are batched and kept: every slot asked for in one turn of the page goes out as one media.shown request,
// and an answer is kept for a few moments, so a grid of tiles drawn again asks for nothing. A page that shows one
// subject (a sheet, a hub, a shelf's band) asks afresh, so what it offers to change is what is there now; a write
// made here keeps what it wrote, and a write refused as changed elsewhere reads the slot again. Every write goes
// through the picture picker (./picker.js) or media.place, naming the slot's revision as read.
//
// The classes are in covers.css (all "art-"), which this module links itself.

import { linkStyles as pickerStyles, openPicker, slotAspect, slotKind, slotName, zoomClass, zoomOf } from "./picker.js";

const STYLES = "/static/pages/art/covers.css";
const KEPT = 20000;      // how long an answer is kept
const MOST = 200;        // the most subjects one media.shown request reads
const kept = new Map();  // slot + "\n" + subject -> { at, view: a promise of the slot's view or null }
let waiting = null;      // { ctx, slots: Map(slot -> Map(subject -> [resolve])) } until the next microtask

function linkStyles() {
  pickerStyles();  // the zoom steps a framed picture is shown with are the picker's
  if (!document.head.querySelector('link[data-page-styles="' + CSS.escape(STYLES) + '"]')) {
    const link = document.createElement("link");
    link.setAttribute("rel", "stylesheet");
    link.setAttribute("href", STYLES);
    link.setAttribute("data-page-styles", STYLES);
    document.head.append(link);
  }
}

// Whether the app serves what showing, and what changing, a picture here needs.
export function reads(ctx) { return ctx.has("media.shown"); }
export function writes(ctx) {
  return reads(ctx) && ctx.has("media.art") && ctx.has("media.gallery") && (ctx.has("media.place") || ctx.has("media.upload"));
}

async function flush(batch) {
  if (waiting === batch) waiting = null;  // what is asked from now on starts another batch
  for (const [slot, asked] of batch.slots) {
    const subjects = [...asked.keys()];
    for (let start = 0; start < subjects.length; start += MOST) {
      const part = subjects.slice(start, start + MOST);
      let answer = null;
      try { answer = await batch.ctx.api("media.shown", { subjects: part, slot }); } catch (error) {
        // not an answer (the page was left, or the read failed): nothing is kept, so it is asked again next time
        for (const subject of part) { kept.delete(slot + "\n" + subject); for (const resolve of asked.get(subject)) resolve(null); }
        continue;
      }
      const found = new Map(answer.shown.map((item) => [item.subject, item]));
      for (const subject of part) for (const resolve of asked.get(subject)) resolve(found.get(subject) || null);
    }
  }
}

export function shownOf(ctx, subject, slot = "cover", { fresh = false } = {}) {
  if (!reads(ctx)) return Promise.resolve(null);
  const key = slot + "\n" + subject;
  const had = kept.get(key);
  if (!fresh && had && Date.now() - had.at < KEPT) return had.view;
  const view = new Promise((resolve) => {
    if (!waiting || waiting.ctx !== ctx) {
      const batch = { ctx, slots: new Map() };
      waiting = batch;
      Promise.resolve().then(() => flush(batch));
    }
    if (!waiting.slots.has(slot)) waiting.slots.set(slot, new Map());
    const asked = waiting.slots.get(slot);
    asked.set(subject, [...(asked.get(subject) || []), resolve]);
  });
  kept.set(key, { at: Date.now(), view });
  return view;
}

// What a slot shows is known without asking: after a write, or when a page already holds the answer.
export function remember(subject, slot, view) { kept.set(slot + "\n" + subject, { at: Date.now(), view: Promise.resolve(view) }); }
export function forget(subject, slot = "cover") { kept.delete(slot + "\n" + subject); }
// Every slot of one subject, after a write made elsewhere (the Art page's own editor).
export function forgetSubject(subject) { for (const key of [...kept.keys()]) if (key.endsWith("\n" + subject)) kept.delete(key); }

export function pictureOf(ctx, subject, slot = "cover") {
  return shownOf(ctx, subject, slot).then((view) => (view && view.state === "shown" && view.image ? ctx.picture(view.image) : null))
    .catch(() => null);
}

// One slot of a subject's art (the media.art answer a write returns), as media.shown would give it.
export function viewIn(art, slot) {
  const shown = art.slots.find((item) => item.slot === slot) || { slot, state: "empty" };
  const own = art.own.find((item) => item.slot === slot);
  return { subject: art.subject, ...shown, etag: own ? own.etag : null };
}

// The picture of a shown slot as an <img> with its zoom, behind whatever else `host` holds, under a dark scrim
// so white text over it stays readable on any picture. Nothing is added when the picture cannot be loaded.
function paint(ctx, host, view, alt = "") {
  const { el } = ctx.ui;
  if (!view || view.state !== "shown" || !view.image) return;
  const zoom = zoomOf(view.crop);
  ctx.picture(view.image).then((url) => {
    if (!url) return;
    host.prepend(el("img", { class: "art-cover-img" + (zoom && zoom > 100 ? " " + zoomClass(zoom) : ""), src: url, alt }),
      el("span", { class: "art-scrim", "aria-hidden": "true" }));
    host.classList.add("art-pictured");
  }, () => {});
}

async function change(ctx, { subject, slot, target, use, aspect, file, offered }) {
  const done = await openPicker(ctx, { subject, slot, target, use, aspect: aspect || slotAspect(slot), file, offered });
  if (!done) return null;
  const view = viewIn(done.art, slot);
  remember(subject, slot, view);
  return view;
}

async function remove(ctx, subject, slot, view, said) {
  const art = await ctx.api("media.place", { subject, slot, mode: "hide", expected: view.etag });
  ctx.notify(said);
  const next = viewIn(art, slot);
  remember(subject, slot, next);
  return next;
}

function focusKeyed(host, key) {
  const again = host.querySelector('button[data-focus-key="' + CSS.escape(key) + '"]');
  if (again) again.focus();
}

// Files dragged onto `node` open the picker with the first of them.
function droppable(node, take) {
  for (const type of ["dragenter", "dragover"]) node.addEventListener(type, (event) => { event.preventDefault(); node.classList.add("art-over"); });
  node.addEventListener("dragleave", () => node.classList.remove("art-over"));
  node.addEventListener("drop", (event) => {
    event.preventDefault();
    node.classList.remove("art-over");
    const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) take(file);
  });
}

// A cover band: the subject's cover behind `text` (its name and a line), on its own colour until it has one.
//
//   subject   whose cover: an entry's ref, a source id or "campaign:<id>" (required)
//   key       what its placeholder colour is drawn from (default: the subject)
//   name      what it is called, for the picker's header and the buttons' labels (required)
//   tall      a 200px band instead of 132px
//   dashed    with no cover, show a dashed block with "Drop a cover image, or choose one" instead of a band
//   use       where the cover shows, said in the picker
//   view      what the cover slot shows, when the page already asked (else it is read here)
//   onchange  called with the slot's new view after a cover was set or removed
//
// Returns the node. It draws itself again after a change, keeping the keyboard on the button that was used.
export function coverBand(ctx, options, ...text) {
  const { ui } = ctx;
  const { el } = ui;
  const { subject, name, slot = "cover" } = options;
  linkStyles();
  const host = el("div", { class: "art-cover-host" });
  const key = "art-cover:" + subject;
  const target = "the " + slotName(slot).toLowerCase() + " of " + name;
  let view = options.view || null;
  const act = (work, focus) => ctx.act(async () => {
    let next;
    try { next = await work(); } catch (error) {
      if (error && error.reason === "changed") {
        // changed elsewhere since it was read: show what is there now, and say so (ctx.act shows the refusal)
        const found = await shownOf(ctx, subject, slot, { fresh: true });
        if (found) { view = found; draw(); }
      }
      throw error;
    }
    if (!next) return;
    view = next;
    draw();
    if (options.onchange) options.onchange(view);
    focusKeyed(host, focus);
  });
  const pick = (file) => act(() => change(ctx, { subject, slot, target, use: options.use, file }), key);
  const button = (label, onclick, focus, aria) => {
    const node = el("button", { type: "button", class: "g-cover-button art-cover-button", onclick, "data-focus-key": focus, "aria-label": aria }, label);
    return node;
  };

  function draw() {
    const set = view && view.state === "shown";
    const can = writes(ctx) && view !== null;
    if (options.dashed && can && !set) {
      // nothing set (or it was removed): a dashed block that takes a dropped file or opens the picker
      const add = el("button", { type: "button", class: "art-cover-add", "data-focus-key": key, onclick: () => pick() },
        "Drop a cover image, or choose one");
      const block = el("div", { class: "art-cover-empty" }, el("div", { class: "art-cover-text" }, ...text), add);
      droppable(block, (file) => pick(file));
      host.replaceChildren(block);
      return;
    }
    const band = ui.cover({ key: options.key || subject, tall: Boolean(options.tall) }, el("div", { class: "art-cover-text" }, ...text));
    band.classList.add("art-cover");
    paint(ctx, band, view);
    if (can) {
      // each button's name starts with the words on it, then says whose cover
      const buttons = [button(set ? "Change cover" : "Add cover", () => pick(), key, (set ? "Change cover of " : "Add cover to ") + name)];
      if (set && ctx.has("media.place")) {
        buttons.push(button("Remove", () => act(() => remove(ctx, subject, slot, view, "Cover removed."), key), key + ":remove", "Remove cover of " + name));
      }
      band.append(el("div", { class: "art-cover-actions" }, buttons));
      droppable(band, (file) => pick(file));
    }
    host.replaceChildren(band);
  }

  draw();
  if (!view && reads(ctx)) {
    shownOf(ctx, subject, slot, { fresh: true }).then((found) => { if (found && !ctx.signal.aborted) { view = found; draw(); } }, () => {});
  }
  return host;
}

// A library tile's face (its kind and title on a card) with the entry's cover behind them once it has one.
// Returns a node that stays empty until the cover is known, then holds "Add cover" when there is none and the
// app can set one: the page puts it where the tile's actions are. `redraw` is called after a cover was added.
export function coverTile(ctx, { subject, name }, face, redraw) {
  const { el } = ctx.ui;
  linkStyles();
  const holder = el("span", { class: "art-add-holder", hidden: true });
  if (!reads(ctx)) return holder;
  shownOf(ctx, subject).then((view) => {
    if (!view || ctx.signal.aborted) return;
    if (view.state === "shown") { face.classList.add("art-tile-cover"); paint(ctx, face, view); return; }
    if (!writes(ctx)) return;
    const add = el("button", { type: "button", class: "art-add-cover", "aria-label": "Add cover to " + name,
      "data-focus-key": "art-add-cover:" + subject, onclick: () => ctx.act(async () => {
        const next = await change(ctx, { subject, slot: "cover", target: "the cover of " + name,
          use: "Shown across the top of its sheet and on its library tile" });
        if (next && redraw) redraw();
      }) }, "Add cover");
    holder.replaceChildren(add);
    holder.hidden = false;
  }, () => {});
  return holder;
}

// One small picture slot: the picture when one is shown, else a dashed "Add picture". Pressing it opens the
// picker for that slot. `view` is what the slot shows now; `onchange(view)` follows a change.
export function slotButton(ctx, { subject, slot, name, view, onchange, focus }) {
  const { el } = ctx.ui;
  linkStyles();
  const set = view && view.state === "shown";
  const label = (set ? "Change the picture of " : "Add picture to ") + name;
  const can = writes(ctx);
  const node = el(can ? "button" : "span", { class: "art-slot-button" + (set ? "" : " art-slot-empty"), "aria-label": can ? label : undefined,
    title: can ? label : undefined }, set ? "" : can ? "Add picture" : "");
  if (can) {
    node.setAttribute("type", "button");
    node.setAttribute("data-focus-key", focus || "art-slot:" + subject + ":" + slot);
    node.addEventListener("click", () => ctx.act(async () => {
      const next = await change(ctx, { subject, slot, target: "the picture of " + name, use: "Shown beside the entry on this sheet" });
      if (next && onchange) onchange(next);
    }));
  }
  if (set) {
    const zoom = zoomOf(view.crop);
    ctx.picture(view.image).then((url) => {
      if (url) node.replaceChildren(el("img", { class: zoom && zoom > 100 ? zoomClass(zoom) : undefined, src: url, alt: can ? "" : "Picture of " + name }));
    }, () => {});
  } else if (view && view.state === "unavailable") {
    node.textContent = "Cannot be shown";
    node.title = view.reason || "";
  }
  return node;
}

// A row of pictures in named slots of one subject, each with Change and Remove, and a dashed tile that adds one
// in a new slot. For a scenario's backdrops: slots "backdrop.<n>".
//
//   subject, name   whose pictures, and what it is called
//   prefix          the slots listed: every slot that starts with it ("backdrop.")
//   noun            what one picture is ("backdrop")
//   art             the subject's media.art answer
//   onchange(art)   called with the subject's art after a change, to draw the row again
export function pictureRow(ctx, { subject, name, prefix, noun, art, onchange }) {
  const { ui } = ctx;
  const { el } = ui;
  linkStyles();
  const can = writes(ctx) && art.editable;
  const own = new Map(art.own.map((item) => [item.slot, item]));
  const slots = art.slots.filter((slot) => slot.slot.startsWith(prefix) && slot.state !== "hidden");
  const taken = new Set(art.own.map((item) => item.slot));
  const fresh = () => { for (let n = 1; ; n += 1) if (!taken.has(prefix + n)) return prefix + n; };
  const open = (slot, focus) => ctx.act(async () => {
    const done = await openPicker(ctx, { subject, slot, art, target: "a " + noun + " of " + name, aspect: slotAspect(slot) });
    if (done) onchange(done.art, focus);
  });
  const tiles = slots.map((slot, index) => {
    const label = slotName(slot.slot) + " " + (index + 1);
    const frame = el("div", { class: "art-row-frame" }, slot.state === "shown" ? "" : "Cannot be shown: " + (slot.reason || slot.state));
    if (slot.state === "shown") {
      const zoom = zoomOf(slot.crop);
      ctx.picture(slot.image).then((url) => {
        if (url) frame.replaceChildren(el("img", { class: zoom && zoom > 100 ? zoomClass(zoom) : undefined, src: url, alt: label + " of " + name }));
      }, () => {});
    }
    const mine = own.get(slot.slot);
    const key = "art-row:" + slot.slot;
    const change = ui.button("Change", () => open(slot.slot, key), { size: "small" });
    change.setAttribute("aria-label", "Change " + label.toLowerCase() + " of " + name);
    change.setAttribute("data-focus-key", key);
    const drop = mine && ctx.has("media.place") && !art.conflicts.includes(slot.slot) ? ui.button("Remove", () => ctx.act(async () => {
      const next = await ctx.api("media.place", { subject, slot: slot.slot, mode: "hide", expected: mine.etag });
      ctx.notify(slotName(slot.slot) + " removed.");
      onchange(next, "art-row-add");
    }), { size: "small" }) : null;
    if (drop) drop.setAttribute("aria-label", "Remove " + label.toLowerCase() + " of " + name);
    return el("div", { class: "art-row-tile", "data-slot": slot.slot }, frame,
      el("span", { class: "art-row-name" }, label),
      can ? el("div", { class: "g-row art-row-actions" }, change, drop) : null);
  });
  const add = can ? el("button", { type: "button", class: "art-row-add", "data-focus-key": "art-row-add",
    onclick: () => open(fresh(), "art-row-add") }, "Add a " + noun) : null;
  if (!add && !tiles.length) return null;
  return el("div", { class: "art-row" }, add, tiles);
}

// A lorebook's entry pictures (media.entries): `slot(index, label)` is the 72px place beside entry `index` of
// the current version, filled once the lorebook's pictures are read; `note` says how many pictures no entry
// reads as any more (the entry was edited or removed), with the way to them. Null where the app does not
// serve media.entries.
export function lorePictures(ctx, { subject, source, name }) {
  if (!ctx.has("media.entries")) return null;
  const { el } = ctx.ui;
  linkStyles();
  const holders = new Map();  // index -> { node, label }
  const note = el("p", { class: "g-muted art-orphans", hidden: true });
  let answer = null;
  const draw = (focus) => {
    for (const [index, holder] of holders) {
      const entry = answer.entries[index];
      if (!entry || !entry.slot) { holder.node.replaceChildren(); continue; }
      holder.node.replaceChildren(slotButton(ctx, { subject, slot: entry.slot, name: "the entry " + holder.label, view: entry.picture,
        onchange: () => load("art-slot:" + subject + ":" + entry.slot) }));
    }
    const lost = answer.orphans.length;
    note.hidden = !lost;
    note.replaceChildren(...(lost ? [(lost === 1 ? "1 picture here belonged" : lost + " pictures here belonged")
      + " to an entry that was edited or removed since. ",
    ctx.registered("art") ? el("a", { class: "g-link", href: ctx.href("art", [], { scope: source, subject }) }, "See the pictures of " + name) : null]
      .filter(Boolean) : []));
    if (focus) for (const holder of holders.values()) focusKeyed(holder.node, focus);
  };
  async function load(focus) {
    try { answer = await ctx.api("media.entries", { subject }); } catch (error) { return; }  // unread: the slots stay empty
    if (!ctx.signal.aborted) draw(focus);
  }
  load();
  return { note, slot(index, label) {
    const node = el("span", { class: "art-slot-holder" });
    holders.set(index, { node, label });
    return node;
  } };
}

// The pictures section of an entry sheet: for a scenario its backdrops ("Scene backdrops": a row to add, change
// and remove them; a chat played from the scenario may take one), and for every entry the way to all of its
// pictures on the Art page. Null where there would be nothing in it.
export function entryPictures(ctx, { subject, source, name, kind }) {
  const { el } = ctx.ui;
  const scenario = kind === "scenario" && ctx.has("media.art");
  const all = ctx.registered("art") && ctx.has("media.art")
    ? el("a", { class: "g-link", href: ctx.href("art", [], { scope: source, subject }) }, "All pictures of " + name) : null;
  if (!scenario && !all) return null;
  linkStyles();
  const title = scenario ? "Scene backdrops" : "Pictures";
  const body = el("div", { class: "g-stack" });
  const section = el("section", { class: "g-stack art-pictures", "aria-label": title },
    el("div", { class: "g-row g-between" }, el("h3", { class: "g-eyebrow art-pictures-title" }, title), all), body);
  if (!scenario) return section;
  const draw = (art, focus) => {
    const row = pictureRow(ctx, { subject, name, prefix: "backdrop.", noun: "backdrop", art, onchange: draw });
    body.replaceChildren(el("p", { class: "g-muted art-orphans" }, "A chat played from this scenario can take one of these as its backdrop."),
      ...(row ? [row] : []));
    if (focus) focusKeyed(body, focus);
  };
  ctx.api("media.art", { subject }).then((art) => { if (!ctx.signal.aborted) draw(art); }, () => {});
  return section;
}

export { slotKind };
