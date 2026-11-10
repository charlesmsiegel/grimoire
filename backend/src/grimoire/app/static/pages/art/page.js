// Art (Sprint 114): a scope's pictures, and one character's or chat actor's picture slots, over the media ops
// (docs/MEDIA_SYNC_UI.md). The address holds the place: #/art?scope=<scope> lists its pictures and who uses
// them, and #/art?scope=<scope>&subject=<subject> edits one subject's slots.
//
// Everything shown comes from the library (titles, descriptions, provenance) and is only ever set as text. A
// slot is shown, hidden or unavailable exactly as the library resolves it: a picture that cannot be shown says
// why, and nothing stands in for it. Every edit names the slot's revision as read, so a slot that changed
// meanwhile is refused and nothing is written.

const BASIS = 10000;           // 018's crop and focus are integer basis points; the page edits them as percent
const MAX_UPLOAD = 32 * 1024 * 1024;
const PAGE = 200, MAX_LIST = 1000;  // pictures listed at first, and the most one gallery request returns
const SLOTS = ["avatar", "portrait"];
const GALLERY_SLOT = /^gallery[0-9]{1,3}$/;
const STATES = { shown: "Shown", hidden: "Hidden", unavailable: "Unavailable", empty: "Nothing placed" };
const ORIGINS = { local: "Placed here", replaced: "Replaced here", cleared: "Hidden here", adjusted: "Re-cropped here",
  missing: "Its picture is missing", broken: "Its picture cannot be read" };
const MODES = { set: "Use a picture", adjust: "Re-crop the inherited picture", hide: "Hide this slot",
  inherit: "Go back to the inherited picture" };

export async function mount(root, ctx) {
  ctx.styles("page.css");
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
  if (subject) await subjectView(root, ctx, scope, subject);
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
  root.append(...present(picker.node, excluded.length
    ? el("p", { class: "g-muted" }, excluded.length + " source" + (excluded.length === 1 ? " is" : "s are")
      + " left out of this library, so " + (excluded.length === 1 ? "it shows" : "they show") + " no art.") : null));
  const gallery = await ctx.api("media.gallery", { scope, limit });

  if (ctx.has("media.upload")) root.append(uploadForm(ctx, { scope }, () => ctx.navigate(address(ctx, { scope }))));
  else root.append(ui.unavailable("Adding pictures", "This app does not serve media.upload."));

  root.append(ui.section("Who uses these pictures", ui.list(gallery.subjects.map((item) =>
    el("a", { href: address(ctx, { scope, subject: item.subject }) }, ui.rowText(item.name || item.subject, item.subject))),
  { empty: "No characters or chat actors here." })));

  const cards = gallery.images.map((image) => el("li", { class: "art-card" },
    image.state === "readable" ? picture(ctx, image.image, describe(image.provenance))
      : el("div", { class: "art-none art-bad" }, "Unavailable: " + (image.reason || "unknown")),
    provenance(el, image.provenance),
    el("div", { class: "g-muted art-meta" }, image.used_by.length
      ? "Used in " + image.used_by.map((use) => use.slot + " (" + use.mode + ")").join(", ")
      : "Not placed anywhere")));
  root.append(ui.section("Pictures", cards.length ? el("ul", { class: "art-grid" }, cards) : ui.empty("No pictures here yet.")));
  if (gallery.more) {
    root.append(limit < MAX_LIST
      ? el("a", { class: "g-button", href: address(ctx, { scope, limit: Math.min(limit * 4, MAX_LIST) }) }, "Show more")
      : el("p", { class: "g-muted" }, "Only the first " + MAX_LIST + " pictures are listed."));
  }
  root.append(...present(problems(ui, gallery.problems)));
}

// -- one subject's slots -------------------------------------------------------------------------------------
async function subjectView(root, ctx, scope, subject) {
  const { ui } = ctx;
  const { el } = ui;
  let art = await ctx.api("media.art", { subject });
  let reusable = {};  // the readable pictures of each scope this subject may use, by scope
  const loadReusable = async () => {
    const next = {};
    for (const from of art.reusable) {
      try {
        next[from] = (await ctx.api("media.gallery", { scope: from, limit: MAX_LIST })).images
          .filter((image) => image.state === "readable");
      } catch (error) {
        if (error.reason === "left") throw error;
        next[from] = [];
      }
    }
    reusable = next;
  };
  await loadReusable();
  // what a placed picture is called, from its gallery; a picture past the listed ones goes unnamed
  const named = (ref) => {
    for (const images of Object.values(reusable)) {
      const found = images.find((image) => image.image === ref);
      if (found) return describe(found.provenance);
    }
    return null;
  };
  const added = new Set();  // gallery slots added on this page, not yet placed
  const region = el("div", {});
  root.append(el("p", {}, ui.link("All pictures of this scope", "art", [], { scope })), region);

  const render = () => {
    ctx.setTitle("Art: " + (art.name || art.subject));
    const own = Object.fromEntries(art.own.map((entry) => [entry.slot, entry]));
    const slots = new Map(art.slots.map((slot) => [slot.slot, slot]));
    for (const name of [...SLOTS, ...added]) if (!slots.has(name)) slots.set(name, { slot: name, state: "empty" });
    region.replaceChildren(...present(
      el("h2", {}, art.name || art.subject),
      el("p", { class: "g-muted art-meta" }, art.subject),
      art.inherits ? el("p", { class: "g-muted" }, art.inherits.follows === "pinned"
        ? "This chat keeps the pictures its character had when the chat started. A picture added to the character later shows here only after the chat moves to that revision."
        : "This follows its character's pictures as they change.") : null,
      art.editable ? null : ui.notice("This device's sync records cannot be read, so no slot can be changed here.", { kind: "bad" }),
      el("div", { class: "art-slots" }, [...slots.values()].map((slot) => slotCard(slot, own[slot.slot]))),
      art.editable && ctx.has("media.place") ? newSlot() : null,
      problems(ui, art.problems)));
  };

  const slotCard = (slot, mine) => {
    const conflicted = art.conflicts.includes(slot.slot);
    const state = STATES[slot.state] || slot.state;
    const card = ui.section(slot.slot,
      slot.state === "shown" ? picture(ctx, slot.image, (art.name || "") + " " + slot.slot) : null,
      slot.state === "shown" && named(slot.image) ? el("div", { class: "art-meta" }, named(slot.image)) : null,
      el("div", { class: slot.state === "unavailable" ? "art-bad" : "" }, state
        + (slot.state === "unavailable" ? ": " + slot.reason + (slot.note ? " (" + slot.note + ")" : "") : "")),
      slot.origin ? el("div", { class: "g-muted art-meta" }, slot.origin === "inherited"
        ? "Inherited from " + slot.placed_by : (ORIGINS[slot.origin] || slot.origin)) : null,
      slot.state === "shown" ? provenance(el, slot.provenance) : null);
    card.classList.add("art-card");
    if (conflicted) {
      card.append(...present(ui.notice("Two devices changed this slot. Choose which to keep under Sync before changing it again.", { kind: "bad" }),
        ctx.registered("sync") ? ui.link("Open Sync", "sync") : null));
    } else if (art.editable) {
      if (ctx.has("media.place")) card.append(editor(slot, mine));
      if (ctx.has("media.upload")) {
        card.append(el("details", {}, el("summary", {}, "Add a new picture to " + slot.slot),
          uploadForm(ctx, { scopes: art.reusable, subject, slot: slot.slot, expected: mine ? mine.etag : null }, async (answer) => {
            art = answer.art || art;
            await loadReusable();  // the new picture is in its gallery now, so it can be chosen and named here
            render();
          })));
      }
    }
    return card;
  };

  const editor = (slot, mine) => {
    // the picture this slot inherits, where the art says which: shown as inherited, or named by its own re-crop.
    // A re-crop names exactly that picture (038), so it is offered only when it is known.
    const inherited = slot.state === "shown" && slot.origin === "inherited" ? slot.image
      : mine && mine.mode === "adjust" ? mine.image : null;
    const modes = art.modes.filter((mode) => mode !== "adjust" || inherited)
      .map((mode) => ({ value: mode, label: MODES[mode] || mode }));
    const options = Object.entries(reusable).flatMap(([from, images]) => images.map((image) =>
      ({ value: image.image, label: describe(image.provenance) + " · " + from })));
    // the picture shown now stays chosen even when it is not among the listed ones, so saving a crop alone never
    // swaps it for another; with none shown, a picture must be picked
    const current = slot.state === "shown" ? slot.image : null;
    if (current && !options.some((option) => option.value === current)) {
      options.unshift({ value: current, label: (named(current) || "The picture shown now") + " · shown now" });
    }
    if (!current && options.length) options.unshift({ value: "", label: "Choose a picture" });
    const mode = ui.choice("Change " + slot.slot, modes, { value: modes.length ? modes[0].value : "" });
    const image = options.length ? ui.choice("Picture", options, { value: current || "" }) : null;
    const box = ["x", "y", "w", "h"].map((key) => number(ui, "Crop " + key + " %", (slot.crop || {})[key]));
    const point = ["x", "y"].map((key) => number(ui, "Focus " + key + " %", (slot.focus || {})[key]));
    const save = async () => {
      const args = { subject, slot: slot.slot, mode: mode.value, expected: mine ? mine.etag : null };
      if (mode.value === "set" || mode.value === "adjust") {
        if (mode.value === "set") {
          if (!image) throw refusal("A picture is needed: add one first.");
          if (!image.value) throw refusal("Choose the picture to use first.");
          args.image = image.value;
        } else {
          args.image = inherited;
        }
        const crop = box.map(read), focus = point.map(read);
        if (crop.some((value) => value !== null)) {
          if (crop.some((value) => value === null)) throw refusal("A crop needs all four of x, y, w and h.");
          args.crop = { x: crop[0], y: crop[1], w: crop[2], h: crop[3] };
        }
        if (focus.some((value) => value !== null)) {
          if (focus.some((value) => value === null)) throw refusal("A focus needs both x and y.");
          args.focus = { x: focus[0], y: focus[1] };
        }
      }
      art = await ctx.api("media.place", args);
      ctx.notify("Saved " + slot.slot + ".");
      render();
    };
    return el("div", {}, mode.node, image ? image.node : el("p", { class: "g-muted" }, "No picture can be placed here yet: add one first."),
      el("fieldset", {}, el("legend", {}, "Crop and focus, in percent (optional)"),
        el("div", { class: "art-numbers" }, box.map((item) => item.node), point.map((item) => item.node))),
      ui.actions(ui.button("Save", () => ctx.act(save), { kind: "primary" })));
  };

  const newSlot = () => {
    const name = ui.field("Gallery slot", { placeholder: "gallery1", hint: "gallery and a number" });
    return el("details", {}, el("summary", {}, "Add a gallery slot"), name.node, ui.actions(ui.button("Add slot", () => {
      const value = name.value.trim();
      if (!GALLERY_SLOT.test(value)) { name.setError("A gallery slot is gallery and a number, such as gallery1."); return; }
      name.setError("");
      added.add(value);
      render();
    })));
  };

  render();
}

// -- shared pieces -------------------------------------------------------------------------------------------
function uploadForm(ctx, target, done) {
  const { ui } = ctx;
  const { el } = ui;
  const inputId = ui.id("art-file");
  const file = el("input", { id: inputId, type: "file", class: "g-input", accept: "image/png,image/jpeg,image/webp,image/gif" });
  const note = ui.field("Description (optional)", { placeholder: "Mara at the harbor" });
  const into = target.scopes && target.scopes.length > 1
    ? ui.choice("Keep it with", target.scopes.map((scope) => ({ value: scope, label: scope })), { value: target.scopes[0] }) : null;
  const add = async () => {
    const chosen = file.files && file.files[0];
    if (!chosen) throw refusal("Choose a picture first.");
    if (chosen.size > MAX_UPLOAD) throw refusal("A picture is at most 32 MiB.");
    const args = { scope: target.scope || (into ? into.value : target.scopes[0]), media: await base64(chosen),
      note: note.value.trim() || null, origin: null };
    if (target.subject) Object.assign(args, { subject: target.subject, slot: target.slot, expected: target.expected });
    const answer = await ctx.api("media.upload", args);
    note.clear();
    ctx.notify(target.subject ? "Added and placed in " + target.slot + "." : "Added.");
    await done(answer);
  };
  return el("div", { class: "g-section" }, el("div", { class: "g-field" }, el("label", { for: inputId }, "Picture to add"), file),
    note.node, into ? into.node : null, ui.actions(ui.button("Add picture", () => ctx.act(add), { kind: "primary" })));
}

// A picture by its ref, or a plain statement that it cannot be shown here: never another picture in its place.
function picture(ctx, ref, label) {
  const { el } = ctx.ui;
  const frame = el("div", { class: "art-none", role: "img", "aria-label": label }, "Loading…");
  ctx.picture(ref).then((url) => {
    if (url) frame.replaceWith(el("img", { class: "art-thumb", src: url, alt: label }));
    else frame.textContent = "This picture cannot be shown here.";
  }, () => { /* the page was left */ });
  return frame;
}

// The nodes that are there: append() and replaceChildren() would write a missing one out as the text "null".
function present(...nodes) {
  return nodes.filter((node) => node !== null && node !== undefined);
}

function describe(provenance) {
  return (provenance && provenance.note) || "Untitled picture";
}

function provenance(el, record) {
  if (!record) return null;
  const lines = [];
  if (record.note) lines.push(el("div", { class: "art-meta" }, record.note));
  if (record.origin) lines.push(el("div", { class: "g-muted art-meta" }, "Origin: " + record.origin));
  if (record.legacy_id) lines.push(el("div", { class: "g-muted art-meta" }, "V1 record: " + record.legacy_id));
  for (const [key, value] of Object.entries(record.other || {})) lines.push(el("div", { class: "g-muted art-meta" }, key + ": " + value));
  return lines.length ? el("div", {}, lines) : null;
}

function problems(ui, list) {
  if (!list || !list.length) return null;
  return ui.el("details", { class: "g-notice" },
    ui.el("summary", {}, list.length + " record" + (list.length === 1 ? "" : "s") + " could not be read"),
    list.map((item) => ui.el("div", { class: "g-muted art-meta" }, (item.path ? item.path + ": " : "") + item.code + (item.detail ? " (" + item.detail + ")" : ""))));
}

// A crop or focus field: percent with two decimals, read back as basis points (or null when left empty).
function number(ui, label, value) {
  const item = ui.field(label, { type: "number", value: value === null || value === undefined ? "" : String(value / (BASIS / 100)) });
  item.input.setAttribute("min", "0");
  item.input.setAttribute("max", "100");
  item.input.setAttribute("step", "0.01");
  item.input.setAttribute("inputmode", "decimal");
  return { node: item.node, input: item.input, label };
}

function read(item) {
  const text = item.input.value.trim();
  if (text === "") return null;
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0 || value > 100) throw refusal(item.label + " must be 0 to 100.");
  return Math.round(value * (BASIS / 100));
}

function refusal(message) {
  return Object.assign(new Error(message), { reason: "invalid_field" });
}

async function base64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let start = 0; start < bytes.length; start += 0x8000) text += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(text);
}
