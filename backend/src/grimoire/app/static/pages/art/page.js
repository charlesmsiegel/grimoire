// Art (Sprint 114): a scope's pictures, and one subject's picture slots, over the media ops
// (docs/MEDIA_SYNC_UI.md). The address holds the place: #/art?scope=<scope> lists its pictures and who uses
// them, and #/art?scope=<scope>&subject=<subject> shows one subject's slots and every picture it may use. A
// subject is a character or a chat actor (an avatar, a portrait), any other entry (a cover; a lorebook's entry
// pictures, a scenario's backdrops), or the source or campaign itself (its cover; a campaign's chat backdrops
// and the pictures on its posts, with the ones whose post is gone).
//
// Everything shown comes from the library (titles, descriptions, provenance) and is only ever set as text. A
// slot is shown, hidden or unavailable exactly as the library resolves it: a picture that cannot be shown says
// why, and nothing stands in for it. A slot's picture is chosen and framed in the picture picker (picker.js);
// every edit names the slot's revision as read, so a slot that changed meanwhile is refused and nothing is
// written.

import { ACCEPT, MAX_LIST, MAX_UPLOAD, base64, describe, frame, openPicker, refusal, reusablePictures, slotAspect,
  slotKind, slotName, slotUse, zoomOf } from "./picker.js";
import { forgetSubject } from "./covers.js";

const PAGE = 200;  // pictures listed at first
// The slots a subject is shown with even before anything is placed there: a character's or a chat actor's
// avatar and portrait, and the cover of anything else (an entry, a source, a campaign).
function firstSlots(subject) {
  const parts = subject.split("/");
  return parts.length === 3 && parts[1] === "character" ? ["avatar", "portrait"] : ["cover"];
}
// What a slot card is called: its slot's name, and for a named slot (a backdrop, an entry or post picture) which one.
function slotTitle(slot) {
  const kind = slotKind(slot);
  return kind && kind !== "cover" ? slotName(slot) + " (" + slot.slice(kind.length + 1) + ")" : slotName(slot);
}
const GALLERY_SLOT = /^gallery[0-9]{1,3}$/;
const STATES = { shown: "Shown", hidden: "Hidden", unavailable: "Unavailable", empty: "Nothing placed" };
const ORIGINS = { local: "Placed here", replaced: "Replaced here", cleared: "Hidden here", adjusted: "Re-cropped here",
  missing: "Its picture is missing", broken: "Its picture cannot be read" };
const USED = { set: "", adjust: " (re-cropped)" };

export async function mount(root, ctx) {
  ctx.styles("picker.css");
  ctx.styles("page.css");
  ctx.layout("page");
  ctx.setTitle("Art");
  const scopes = await ctx.api("media.scopes", {});
  const choices = [
    ...scopes.sources.map((item) => ({ value: item.scope, excluded: item.state === "excluded",
      label: (item.title || item.scope) + (item.state === "excluded" ? " (left out of this library: shows no art)" : "") })),
    ...scopes.campaigns.map((item) => ({ value: item.scope, excluded: false,
      label: (item.title || item.campaign) + " (chat pictures)" })),
  ];
  const asked = ctx.query.get("scope");
  const scope = choices.some((item) => item.value === asked) ? asked : (choices.find((item) => !item.excluded) || {}).value;
  const subject = ctx.query.get("subject");
  if (!scope) {
    root.append(ctx.ui.empty("Nothing in this library holds pictures yet. Import a card or start a chat first."));
    return;
  }
  if (subject) await subjectView(root, ctx, scope, subject, choices);
  else await galleryView(root, ctx, scope, choices);
}

function address(ctx, query) {
  const clean = Object.fromEntries(Object.entries(query).filter(([, value]) => value !== undefined && value !== null));
  return ctx.href("art", [], clean);
}

// -- one scope's pictures ------------------------------------------------------------------------------------
async function galleryView(root, ctx, scope, choices) {
  const { ui } = ctx;
  const { el } = ui;
  const asked = Number.parseInt(ctx.query.get("limit") || "", 10);
  const limit = Number.isInteger(asked) && asked >= 1 ? Math.min(asked, MAX_LIST) : PAGE;
  const picker = ui.choice("Pictures of", choices.filter((item) => !item.excluded || item.value === scope),
    { value: scope, onchange: (value) => ctx.navigate(address(ctx, { scope: value })) });
  const excluded = choices.filter((item) => item.excluded);
  const gallery = await ctx.api("media.gallery", { scope, limit });
  const names = new Map(gallery.subjects.map((item) => [item.subject, item.name || item.subject]));
  const adding = ctx.has("media.upload") ? adder(ctx, [scope], () => ctx.navigate(address(ctx, { scope, limit: ctx.query.get("limit") }))) : null;

  const page = el("div", { class: "g-stack g-loose" },
    el("div", { class: "art-top" }, picker.node, excluded.length
      ? el("p", { class: "g-muted" }, excluded.length + " source" + (excluded.length === 1 ? " is" : "s are")
        + " left out of this library, so " + (excluded.length === 1 ? "it shows" : "they show") + " no art.") : null),
    el("section", { class: "g-stack", "aria-label": "Who uses these pictures" },
      ui.eyebrow("Who uses these pictures"),
      gallery.subjects.length ? el("div", { class: "art-people" }, gallery.subjects.map((item) =>
        el("a", { class: "g-rowcard", href: address(ctx, { scope, subject: item.subject }) },
          ui.avatar(item.name || item.subject, { size: 36, key: item.subject }),
          el("span", { class: "g-grow" }, el("span", { class: "g-title g-clip art-block" }, item.name || item.subject),
            el("span", { class: "g-muted g-ref art-block" }, item.subject)))))
        : ui.empty("Nothing here uses pictures yet.")),
    el("section", { class: "g-stack", "aria-label": "Pictures" },
      ui.eyebrow("Pictures"),
      adding ? adding.tray : ui.unavailable("Adding pictures", "This app does not serve media.upload."),
      gallery.images.length || adding ? el("div", { class: "art-grid" }, adding ? adding.tile : null,
        gallery.images.map((image) => pictureCard(ctx, image, usedBy(image, names)))) : null,
      gallery.images.length ? null : ui.empty("No pictures here yet."),
      gallery.more ? (limit < MAX_LIST
        ? el("div", {}, el("a", { class: "g-button", href: address(ctx, { scope, limit: Math.min(limit * 4, MAX_LIST) }) }, "Show more"))
        : el("p", { class: "g-muted" }, "Only the first " + MAX_LIST + " pictures are listed.")) : null),
    problems(ui, gallery.problems));
  root.append(page);
}

// Where a picture is placed, by the people of its own scope: "Portrait of Mara Vell".
function usedBy(image, names, only) {
  const uses = image.used_by.filter((use) => !only || use.subject === only);
  if (!uses.length) return "Not placed anywhere";
  return "Used in " + uses.map((use) => (only ? use.slot : use.slot + " of " + (names.get(use.subject) || use.subject))
    + (USED[use.mode] === undefined ? " (" + use.mode + ")" : USED[use.mode])).join(", ");
}

// -- one subject's slots -------------------------------------------------------------------------------------
async function subjectView(root, ctx, scope, subject) {
  const { ui } = ctx;
  const { el } = ui;
  let art = await ctx.api("media.art", { subject });
  let galleries = await reusablePictures(ctx, art);  // the readable pictures of each scope this subject may use
  // what a placed picture is called, from its gallery; a picture past the listed ones goes unnamed
  const named = (ref) => {
    for (const { images } of galleries) {
      const found = images.find((image) => image.image === ref);
      if (found) return describe(found.provenance);
    }
    return null;
  };
  const added = new Set();  // gallery slots added on this page, not yet placed
  const region = el("div", { class: "g-stack g-loose" });
  root.append(region);
  const editable = () => art.editable && (ctx.has("media.place") || ctx.has("media.upload"));
  const reload = async (next) => {
    forgetSubject(subject);  // what other pages kept of this subject's cover is out of date after a change here
    art = next || await ctx.api("media.art", { subject });
    galleries = await reusablePictures(ctx, art);
    render();
  };

  // Replace and Crop open the picker on the slot; both end in the same write, the picker's "Use this picture"
  const pick = (slot, key) => ctx.act(async () => {
    const done = await openPicker(ctx, { subject, slot: slot.slot, art,
      target: (art.name || art.subject) + ": " + slotName(slot.slot).toLowerCase() });
    if (!done) return;
    await reload(done.art);
    const back = region.querySelector('button[data-focus-key="' + CSS.escape(key) + '"]');
    if (back) back.focus();
  });
  const place = (slot, mode, mine, said) => ctx.act(async () => {
    const next = await ctx.api("media.place", { subject, slot: slot.slot, mode, expected: mine ? mine.etag : null });
    ctx.notify(said);
    await reload(next);
  });

  const slotCard = (slot, mine) => {
    const conflicted = art.conflicts.includes(slot.slot);
    const title = slotTitle(slot.slot);
    const label = (art.name || art.subject) + ": " + title.toLowerCase();
    const use = slotUse(slot.slot);
    const name = slot.state === "shown" ? named(slot.image) : null;
    const zoom = slot.state === "shown" ? zoomOf(slot.crop) : null;
    const card = el("div", { class: "art-slot", "data-slot": slot.slot },
      frame(ctx, { aspect: slotAspect(slot.slot), image: slot.state === "shown" ? slot.image : null, label,
        zoom: zoom && zoom > 100 ? zoom : null, empty: STATES[slot.state] || slot.state }),
      el("div", { class: "art-slot-head" }, el("strong", {}, title),
        el("span", { class: "art-slot-state" + (slot.state === "unavailable" ? " art-bad" : slot.state === "shown" ? " art-good" : "") },
          STATES[slot.state] || slot.state)),
      slot.state === "unavailable" ? el("div", { class: "art-meta art-bad" }, slot.reason + (slot.note ? " (" + slot.note + ")" : "")) : null,
      use ? el("div", { class: "g-muted art-meta" }, use) : null,
      name ? el("div", { class: "art-meta" }, name) : null,
      slot.origin ? el("div", { class: "g-muted art-meta" }, slot.origin === "inherited"
        ? "Inherited from " + slot.placed_by : (ORIGINS[slot.origin] || slot.origin)) : null,
      slot.state === "shown" && slot.crop && zoom === null ? el("div", { class: "g-muted art-meta" }, "Shown with an exact crop") : null,
      slot.state === "shown" ? provenance(el, slot.provenance, !name) : null);
    if (conflicted) {
      card.append(...present(ui.notice("Two devices changed this slot. Choose which to keep under Devices and sync before changing it again.", { kind: "bad" }),
        ctx.registered("sync") ? ui.link("Open Devices and sync", "sync") : null));
    } else if (editable()) {
      const keys = { replace: "art-replace:" + slot.slot, crop: "art-crop:" + slot.slot };
      const buttons = [ui.button(slot.state === "shown" ? "Replace" : "Choose", () => pick(slot, keys.replace), { size: "small" })];
      buttons[0].setAttribute("data-focus-key", keys.replace);
      buttons[0].setAttribute("aria-label", (slot.state === "shown" ? "Replace " : "Choose a picture for ") + label);
      if (slot.state === "shown" && ctx.has("media.place")) {
        const crop = ui.button("Crop", () => pick(slot, keys.crop), { size: "small" });
        crop.setAttribute("data-focus-key", keys.crop);
        crop.setAttribute("aria-label", "Crop " + label);
        buttons.push(crop);
      }
      if (ctx.has("media.place") && art.modes.includes("hide") && slot.state !== "hidden" && slot.state !== "empty") {
        // a character's slot is hidden; a cover, a backdrop and the like are taken off once something is there
        const named = slotKind(slot.slot);
        if (!named || mine) {
          const hide = ui.button(named ? "Remove" : "Hide", () => place(slot, "hide", mine, title + (named ? " removed." : " hidden.")), { size: "small" });
          hide.setAttribute("aria-label", (named ? "Remove " : "Hide ") + label);
          buttons.push(hide);
        }
      }
      if (ctx.has("media.place") && art.modes.includes("inherit") && mine && mine.mode !== "inherit") {
        const reset = ui.button("Use inherited", () => place(slot, "inherit", mine, title + " follows its source again."), { size: "small" });
        reset.setAttribute("aria-label", "Go back to the inherited picture for " + label);
        buttons.push(reset);
      }
      card.append(el("div", { class: "art-slot-actions" }, buttons));
    }
    return card;
  };

  const newSlot = () => {
    const name = ui.field("Gallery slot", { placeholder: "gallery1", hint: "gallery and a number" });
    return el("details", { class: "art-more" }, el("summary", {}, "Add a gallery slot"), name.node, ui.actions(ui.button("Add slot", () => {
      const value = name.value.trim();
      if (!GALLERY_SLOT.test(value)) { name.setError("A gallery slot is gallery and a number, such as gallery1."); return; }
      name.setError("");
      added.add(value);
      render();
    })));
  };

  function render() {
    const who = art.name || art.subject;
    ctx.setTitle(who);
    ctx.setCrumb(ui.link("Art", "art", [], { scope }), " / " + scope);
    const own = Object.fromEntries(art.own.map((entry) => [entry.slot, entry]));
    // the avatar and the portrait first, placed or not; then every other slot the subject has, then new ones
    const read = new Map(art.slots.map((slot) => [slot.slot, slot]));
    const slots = new Map(firstSlots(art.subject).map((name) => [name, read.get(name) || { slot: name, state: "empty" }]));
    for (const [name, slot] of read) slots.set(name, slot);
    for (const name of added) if (!slots.has(name)) slots.set(name, { slot: name, state: "empty" });
    const pictures = galleries.flatMap(({ scope: from, images }) => images.map((image) => ({ image, from })));
    const adding = art.editable && ctx.has("media.upload") ? adder(ctx, art.reusable, () => reload()) : null;
    region.replaceChildren(...present(
      el("div", { class: "g-stack" },
        el("div", { class: "g-row g-between" }, el("span", { class: "g-muted g-ref" }, art.subject),
          ui.link("All pictures of this scope", "art", [], { scope })),
        art.inherits ? el("p", { class: "g-muted art-flat" }, art.inherits.follows === "pinned"
          ? "This chat keeps the pictures its character had when the chat started. A picture added to the character later shows here only after the chat moves to that revision."
          : "This follows its character's pictures as they change.") : null,
        art.editable ? null : ui.notice("This device's sync records cannot be read, so no slot can be changed here.", { kind: "bad" })),
      el("section", { class: "g-stack", "aria-label": "Where these pictures show" },
        ui.eyebrow("Where these pictures show"),
        el("div", { class: "art-slots" }, [...slots.values()].map((slot) => slotCard(slot, own[slot.slot]))),
        art.editable && ctx.has("media.place") ? newSlot() : null),
      el("section", { class: "g-stack", "aria-label": "All pictures" },
        el("div", { class: "g-row g-between" }, ui.eyebrow("All pictures of " + who),
          pictures.length && editable() ? el("span", { class: "g-muted" }, "Use Replace on a slot to place one") : null),
        adding ? adding.tray : null,
        pictures.length || adding ? el("div", { class: "art-grid" }, adding ? adding.tile : null,
          pictures.map(({ image, from }) => pictureCard(ctx, image,
            usedBy(image, new Map(), subject) + (art.reusable.length > 1 ? " · " + from : "")))) : null,
        pictures.length ? null : ui.empty("No pictures here yet.")),
      problems(ui, art.problems)));
  }

  render();
}

// -- shared pieces -------------------------------------------------------------------------------------------

// Adding pictures to a gallery: a drop tile (drag files onto it, or choose them) and a tray that lists what is
// waiting, each with its own description, before anything is sent. Returns { tile, tray }.
function adder(ctx, scopes, done) {
  const { ui } = ctx;
  const { el } = ui;
  let waiting = [];  // [{ file, note }]
  const input = el("input", { type: "file", accept: ACCEPT, multiple: true, hidden: true, "aria-label": "Picture files" });
  const tile = el("button", { type: "button", class: "art-add", onclick: () => input.click() }, "Drop pictures here, or choose files");
  const into = scopes.length > 1 ? ui.choice("Keep them with", scopes.map((scope) => ({ value: scope, label: scope })), { value: scopes[0] }) : null;
  const tray = el("div", { class: "art-tray", hidden: true });
  const send = async () => {
    let sent = 0;
    try {
      while (waiting.length) {
        const { file, note } = waiting[0];
        if (file.size > MAX_UPLOAD) throw refusal((file.name || "A picture") + " is larger than 32 MiB.");
        await ctx.api("media.upload", { scope: into ? into.value : scopes[0], media: await base64(file),
          note: note.value.trim() || null, origin: null });
        waiting.shift();
        sent += 1;
      }
    } finally {
      if (sent) ctx.notify(sent === 1 ? "Added." : "Added " + sent + " pictures.");
      if (sent && waiting.length) show();
    }
    await done();
  };
  function show() {
    tray.hidden = !waiting.length;
    tray.replaceChildren(...present(
      el("strong", {}, waiting.length === 1 ? "1 picture ready to add" : waiting.length + " pictures ready to add"),
      ...waiting.map((item) => el("div", { class: "art-tray-row" }, item.note.node,
        ui.button("Remove", () => { waiting = waiting.filter((other) => other !== item); show(); }, { kind: "linklike", size: "small" }))),
      into ? into.node : null,
      ui.actions(ui.button(waiting.length === 1 ? "Add picture" : "Add " + waiting.length + " pictures", () => ctx.act(send), { kind: "primary" }),
        ui.button("Clear", () => { waiting = []; show(); }))));
  }
  const take = (files) => {
    for (const file of files || []) waiting.push({ file, note: ui.field("Description of " + (file.name || "this picture") + " (optional)", {}) });
    show();
  };
  input.addEventListener("change", () => { take(input.files); input.value = ""; });
  for (const type of ["dragenter", "dragover"]) tile.addEventListener(type, (event) => { event.preventDefault(); tile.classList.add("art-over"); });
  tile.addEventListener("dragleave", () => tile.classList.remove("art-over"));
  tile.addEventListener("drop", (event) => {
    event.preventDefault();
    tile.classList.remove("art-over");
    if (event.dataTransfer) take([...event.dataTransfer.files]);
  });
  return { tile: el("div", { class: "art-add-wrap" }, tile, input), tray };
}

// One picture of a gallery: the picture (or why it cannot be shown), what it is called, where it came from and
// where it is used.
function pictureCard(ctx, image, used) {
  const { el } = ctx.ui;
  const label = image.state === "readable" ? describe(image.provenance) : "Unavailable picture";
  return el("div", { class: "art-card" },
    image.state === "readable" ? frame(ctx, { aspect: "3/4", image: image.image, label })
      : el("div", { class: "art-frame art-aspect-3-4" }, el("span", { class: "art-frame-text art-bad" }, "Unavailable: " + (image.reason || "unknown"))),
    image.state === "readable" ? el("span", { class: "art-card-name" }, label) : null,
    provenance(el, image.provenance, false),
    el("span", { class: "g-muted art-meta" }, used));
}

// The nodes that are there: append() and replaceChildren() would write a missing one out as the text "null".
function present(...nodes) {
  return nodes.filter((node) => node !== null && node !== undefined);
}

// Where a picture came from, as its record says. `withNote` also shows its description (a slot card names its
// picture from the gallery, and falls back to the record's own note).
function provenance(el, record, withNote) {
  if (!record) return null;
  const lines = [];
  if (withNote && record.note) lines.push(el("div", { class: "g-muted art-meta" }, record.note));
  if (record.origin) lines.push(el("div", { class: "g-muted art-meta" }, "Origin: " + record.origin));
  if (record.legacy_id) lines.push(el("div", { class: "g-muted art-meta" }, "V1 record: " + record.legacy_id));
  for (const [key, value] of Object.entries(record.other || {})) lines.push(el("div", { class: "g-muted art-meta" }, key + ": " + value));
  return lines.length ? el("div", {}, lines) : null;
}

function problems(ui, list) {
  if (!list || !list.length) return null;
  return ui.el("details", { class: "g-notice art-problems" },
    ui.el("summary", {}, list.length + " record" + (list.length === 1 ? "" : "s") + " could not be read"),
    list.map((item) => ui.el("div", { class: "g-muted art-meta" }, (item.path ? item.path + ": " : "") + item.code + (item.detail ? " (" + item.detail + ")" : ""))));
}
