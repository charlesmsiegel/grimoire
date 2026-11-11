// The picture picker: one modal for choosing which picture a subject's slot shows and how it is framed, over the
// media ops (docs/MEDIA_SYNC_UI.md). Any page may use it:
//
//   import { openPicker } from "/static/pages/art/picker.js";
//   const done = await openPicker(ctx, { subject, slot: "portrait", target: "Mara Vell's portrait" });
//   if (done) { /* done.art is the subject's art as it now reads (the media.art answer) */ }
//
// openPicker(ctx, options) resolves to { art } once a picture was placed (or the slot hidden or reset), and to
// null when the person cancelled or left the page. It rejects only when the subject's art cannot be read.
//
//   subject   whose slot: a source or chat actor ref, as media.art takes it (required)
//   slot      "avatar", "portrait", or another slot name (required)
//   target    what the header names after "Choose a picture for" (default: the subject's name and the slot)
//   aspect    the preview's shape: "1/1" or "3/4" (default: 1/1 for an avatar, 3/4 otherwise)
//   use       one line saying where the picture shows (default: what is known of the slot)
//   art       the subject's media.art answer, when the caller already holds it (saves a read)
//
// It needs media.art and media.gallery; it places with media.place and adds a file with media.upload, and offers
// each only when the app serves it. Every write names the slot's revision as read, so a slot that changed
// meanwhile is refused, said in the modal, and nothing is written. A zoom is stored as the placement's crop: a
// centred box, so 200% keeps the middle half of the picture each way.
//
// Its classes are in picker.css (all "art-"), which it links itself, so a page outside this folder needs nothing
// else. Aspect ratios and zoom steps are classes from a fixed set: the page's policy allows no inline styles.

const BASIS = 10000;  // a crop and a focus are integer basis points; a person edits them as percent
const STYLES = "/static/pages/art/picker.css";

export const MAX_UPLOAD = 32 * 1024 * 1024;
export const ACCEPT = "image/png,image/jpeg,image/webp,image/gif";
export const MAX_LIST = 1000;  // the most one gallery request returns
export const ZOOM_MIN = 100, ZOOM_MAX = 200, ZOOM_STEP = 5;

const ASPECTS = { "1/1": "art-aspect-1-1", "3/4": "art-aspect-3-4" };
const SLOT_NAMES = { avatar: "Avatar", portrait: "Portrait" };
const USES = { avatar: "Beside their lines and in lists", portrait: "On their sheet and on stage" };

export function slotName(slot) { return SLOT_NAMES[slot] || slot; }
export function slotUse(slot) { return USES[slot] || null; }
export function slotAspect(slot) { return slot === "avatar" ? "1/1" : "3/4"; }
export function aspectClass(aspect) { return ASPECTS[aspect] || ASPECTS["1/1"]; }
export function zoomClass(zoom) { return "art-zoom-" + zoom; }

// The crop a zoom stores: nothing at 100%, else a centred box of the picture's 100/zoom each way.
export function cropOf(zoom) {
  if (zoom <= ZOOM_MIN) return null;
  const side = Math.round(BASIS * 100 / zoom);
  const edge = Math.floor((BASIS - side) / 2);
  return { x: edge, y: edge, w: side, h: side };
}

// The zoom a stored crop is, or null when it is some other box (one typed in as exact numbers).
export function zoomOf(crop) {
  if (!crop) return ZOOM_MIN;
  const zoom = Math.round(BASIS * 100 / crop.w / ZOOM_STEP) * ZOOM_STEP;
  if (zoom < ZOOM_MIN || zoom > ZOOM_MAX) return null;
  const same = cropOf(zoom);
  if (!same) return crop.x === 0 && crop.y === 0 && crop.w === BASIS && crop.h === BASIS ? ZOOM_MIN : null;
  return ["x", "y", "w", "h"].every((key) => same[key] === crop[key]) ? zoom : null;
}

export function describe(provenance) {
  return (provenance && provenance.note) || "Untitled picture";
}

export function refusal(message) {
  return Object.assign(new Error(message), { reason: "invalid_field" });
}

export async function base64(file) {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let text = "";
  for (let start = 0; start < bytes.length; start += 0x8000) text += String.fromCharCode(...bytes.subarray(start, start + 0x8000));
  return btoa(text);
}

export function linkStyles() {
  if (!document.head.querySelector('link[data-page-styles="' + CSS.escape(STYLES) + '"]')) {
    const link = document.createElement("link");
    link.setAttribute("rel", "stylesheet");
    link.setAttribute("href", STYLES);
    link.setAttribute("data-page-styles", STYLES);
    document.head.append(link);
  }
}

// The readable pictures of each scope a subject may place from: [{ scope, images }]. A scope that cannot be
// listed offers none; nothing stands in for it.
export async function reusablePictures(ctx, art) {
  const found = [];
  for (const scope of art.reusable) {
    let images = [];
    try {
      images = (await ctx.api("media.gallery", { scope, limit: MAX_LIST })).images.filter((image) => image.state === "readable");
    } catch (error) {
      if (error.reason === "left") throw error;
    }
    found.push({ scope, images });
  }
  return found;
}

// A frame of one shape showing one picture by its ref, or a plain statement of why there is none: never another
// picture in its place. `zoom` is a step of the fixed set, or null for no zoom.
export function frame(ctx, { aspect, image, label, zoom, empty }) {
  const { el } = ctx.ui;
  const node = el("div", { class: "art-frame " + aspectClass(aspect) });
  if (!image) {
    node.append(el("span", { class: "art-frame-text" }, empty || "Nothing placed"));
    return node;
  }
  node.setAttribute("role", "img");
  node.setAttribute("aria-label", label);
  node.append(el("span", { class: "art-frame-text" }, "Loading…"));
  ctx.picture(image).then((url) => {
    if (!url) { node.replaceChildren(el("span", { class: "art-frame-text" }, "This picture cannot be shown here.")); return; }
    node.removeAttribute("role");
    node.removeAttribute("aria-label");
    node.replaceChildren(el("img", { class: "art-frame-img" + (zoom ? " " + zoomClass(zoom) : ""), src: url, alt: label }));
  }, () => { /* the page was left */ });
  return node;
}

// A crop or focus field: percent with two decimals, read back as basis points (or null when left empty).
function number(ui, label, value) {
  const item = ui.field(label, { type: "number" });
  item.input.setAttribute("min", "0");
  item.input.setAttribute("max", "100");
  item.input.setAttribute("step", "0.01");
  item.input.setAttribute("inputmode", "decimal");
  const set = (next) => { item.input.value = next === null || next === undefined ? "" : String(next / (BASIS / 100)); };
  set(value);
  return { node: item.node, input: item.input, label, set };
}

function read(item) {
  const text = item.input.value.trim();
  if (text === "") return null;
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0 || value > 100) throw refusal(item.label + " must be 0 to 100.");
  return Math.round(value * (BASIS / 100));
}

export async function openPicker(ctx, options) {
  const { ui } = ctx;
  const { el } = ui;
  const { subject, slot } = options;
  linkStyles();
  const art = options.art || await ctx.api("media.art", { subject });
  const galleries = ctx.has("media.place") ? await reusablePictures(ctx, art) : [];

  const own = art.own.find((entry) => entry.slot === slot) || null;
  const shown = art.slots.find((entry) => entry.slot === slot) || { slot, state: "empty" };
  const expected = own ? own.etag : null;
  const current = shown.state === "shown" ? shown.image : null;
  // the picture this slot inherits, where the art says which: re-framing it keeps following its source (an
  // "adjust"), where any other picture is this subject's own choice (a "set")
  const inherited = shown.state === "shown" && shown.origin === "inherited" ? shown.image
    : own && own.mode === "adjust" ? own.image : null;
  const locked = !art.editable || art.conflicts.includes(slot);
  const aspect = options.aspect || slotAspect(slot);
  const target = options.target || (art.name || art.subject) + ": " + slotName(slot).toLowerCase();
  const use = options.use === undefined ? slotUse(slot) : options.use;

  const pictures = galleries.flatMap(({ scope, images }) => images.map((image) =>
    ({ ref: image.image, label: describe(image.provenance), from: scope })));
  // the picture shown now stays in the list even when its gallery does not list it, so framing it alone never
  // swaps it for another
  if (current && !pictures.some((picture) => picture.ref === current)) {
    pictures.unshift({ ref: current, label: describe(shown.provenance), from: "shown now" });
  }

  return new Promise((resolve) => {
    let result = null;
    let chosen = current ? { ref: current } : null;  // { ref } a listed picture, or { file, url } a new one
    let busy = false;
    const modal = ui.modal("Choose a picture for " + target, { onclose: () => {
      if (chosen && chosen.url) URL.revokeObjectURL(chosen.url);
      resolve(result);
    } });
    modal.dialog.classList.add("art-picker-dialog");
    modal.dialog.querySelector("div").append(ui.button("Close", () => modal.close(), { kind: "linklike" }));

    const problem = el("div", { class: "g-notice g-bad", role: "alert", hidden: true });
    const say = (text) => { problem.textContent = text || ""; problem.hidden = !text; };

    // -- 1 · pick ------------------------------------------------------------------------------------------
    const input = el("input", { type: "file", accept: ACCEPT, hidden: true, "aria-label": "Picture file" });
    const add = el("button", { type: "button", class: "art-pick art-pick-add", onclick: () => input.click() }, "Drop or choose a file");
    const fileTile = el("button", { type: "button", class: "art-pick", "aria-pressed": "true", hidden: true });
    const tiles = pictures.map((picture) => {
      const tile = el("button", { type: "button", class: "art-pick", "aria-label": picture.label + " · " + picture.from,
        title: picture.label, "aria-pressed": "false", onclick: () => choose({ ref: picture.ref }) });
      ctx.picture(picture.ref).then((url) => { if (url) tile.replaceChildren(el("img", { src: url, alt: "" })); }, () => {});
      return [picture, tile];
    });
    const caption = el("p", { class: "g-muted art-picker-caption" });
    const note = ui.field("Description (optional)", { hint: "Kept with the picture, so it can be told apart later." });
    const into = art.reusable.length > 1
      ? ui.choice("Keep it with", art.reusable.map((scope) => ({ value: scope, label: scope })), { value: art.reusable[0] }) : null;
    const fresh = el("div", { hidden: true }, note.node, into ? into.node : null);
    const grid = el("div", { class: "art-picker-grid" }, ctx.has("media.upload") ? [add, input] : null, fileTile, tiles.map(([, tile]) => tile));
    const pick = el("div", { class: "art-picker-col" }, ui.eyebrow("1 · Pick a picture"), grid, caption, fresh);

    // -- 2 · frame -----------------------------------------------------------------------------------------
    const view = el("div", { class: "art-frame art-picker-frame " + aspectClass(aspect) });
    const zoom = el("input", { type: "range", class: "art-zoom", min: ZOOM_MIN, max: ZOOM_MAX, step: ZOOM_STEP, id: ui.id("zoom") });
    const zoomSays = el("span", { class: "g-muted art-zoom-value" });
    const box = ["x", "y", "w", "h"].map((key) => number(ui, "Crop " + key + " %", null));
    const point = ["x", "y"].map((key) => number(ui, "Focus " + key + " %", null));
    const exact = el("details", { class: "art-exact" }, el("summary", {}, "Exact crop and focus"),
      el("p", { class: "g-muted" }, "In percent of the picture. A crop needs all four numbers; a focus needs both."),
      el("div", { class: "art-numbers" }, box.map((item) => item.node), point.map((item) => item.node)));
    const framing = el("div", { class: "art-picker-col" }, ui.eyebrow("2 · Frame it"), view,
      el("div", { class: "art-zoom-row" }, el("label", { for: zoom.id }, "Zoom"), zoom, zoomSays),
      use ? el("p", { class: "g-muted" }, use) : null, exact);

    let step = ZOOM_MIN;  // the zoom the preview shows, or null for a crop that is not a centred zoom
    let previewUrl = null;
    const preview = () => {
      zoomSays.textContent = step === null ? "Exact crop" : step + "%";
      if (!previewUrl) {
        view.replaceChildren(el("span", { class: "art-frame-text" }, chosen ? "Loading…" : "Pick a picture to frame it"));
        return;
      }
      view.replaceChildren(el("img", { class: "art-frame-img" + (step ? " " + zoomClass(step) : ""), src: previewUrl, alt: "Preview" }));
    };
    const cropNow = () => {
      const values = box.map((item) => { try { return read(item); } catch (error) { return undefined; } });
      if (values.every((value) => value === null)) return null;
      if (values.some((value) => value === null || value === undefined)) return undefined;
      return { x: values[0], y: values[1], w: values[2], h: values[3] };
    };
    const setFrame = (crop, focus) => {
      box.forEach((item, index) => item.set(crop ? crop[["x", "y", "w", "h"][index]] : null));
      point.forEach((item, index) => item.set(focus ? focus[["x", "y"][index]] : null));
      step = zoomOf(crop);
      zoom.value = String(step === null ? ZOOM_MIN : step);
    };
    zoom.addEventListener("input", () => {
      step = Number(zoom.value);
      const crop = cropOf(step);
      box.forEach((item, index) => item.set(crop ? crop[["x", "y", "w", "h"][index]] : null));
      preview();
    });
    for (const item of box) {
      item.input.addEventListener("input", () => {
        const crop = cropNow();
        step = crop === undefined ? null : zoomOf(crop);
        if (step !== null) zoom.value = String(step);
        preview();
      });
    }

    function choose(next) {
      if (chosen && chosen.url && chosen.url !== next.url) URL.revokeObjectURL(chosen.url);
      const same = chosen && next.ref && chosen.ref === next.ref;
      chosen = next;
      say("");
      for (const [picture, tile] of tiles) tile.setAttribute("aria-pressed", String(picture.ref === chosen.ref));
      fileTile.hidden = !chosen.file;
      fresh.hidden = !chosen.file;
      if (chosen.file) {
        fileTile.setAttribute("aria-label", "New picture: " + (chosen.file.name || "chosen file"));
        fileTile.replaceChildren(chosen.url ? el("img", { src: chosen.url, alt: "" }) : el("span", {}, chosen.file.name || "New"));
        caption.textContent = "New picture: " + (chosen.file.name || "chosen file");
      } else {
        const picture = pictures.find((item) => item.ref === chosen.ref);
        caption.textContent = picture.label + " · " + picture.from;
      }
      // a picture keeps the frame it is shown with; another one starts whole
      if (!same) setFrame(chosen.ref && chosen.ref === current ? shown.crop : null, chosen.ref && chosen.ref === current ? shown.focus : null);
      previewUrl = chosen.file ? chosen.url : null;
      preview();
      if (chosen.ref) {
        const ref = chosen.ref;
        ctx.picture(ref).then((url) => { if (chosen && chosen.ref === ref) { previewUrl = url || null; preview(); } }, () => {});
      }
    }

    function take(file) {
      if (!file) return;
      if (file.size > MAX_UPLOAD) { say("A picture is at most 32 MiB."); return; }
      let url = null;
      try { url = URL.createObjectURL(file); } catch (error) { url = null; }  // shown by its name where it cannot be previewed
      choose({ file, url });
    }
    input.addEventListener("change", () => { take(input.files && input.files[0]); input.value = ""; });
    for (const type of ["dragenter", "dragover"]) {
      pick.addEventListener(type, (event) => { event.preventDefault(); add.classList.add("art-over"); });
    }
    pick.addEventListener("dragleave", () => add.classList.remove("art-over"));
    pick.addEventListener("drop", (event) => {
      event.preventDefault();
      add.classList.remove("art-over");
      if (ctx.has("media.upload") && event.dataTransfer) take(event.dataTransfer.files[0]);
    });

    // -- the foot: what the slot may do ------------------------------------------------------------------
    const buttons = [];
    const run = (work, done) => async () => {
      if (busy) return;
      busy = true;
      say("");
      for (const node of buttons) node.disabled = true;
      try {
        result = { art: await work() };
        ctx.notify(done);
        modal.close();
      } catch (error) {
        if (error.reason !== "left") say(ui.errorText(error));
      } finally {
        busy = false;
        for (const node of buttons) node.disabled = false;
      }
    };
    const framed = () => {
      const crop = box.map(read), focus = point.map(read);
      const args = {};
      if (crop.some((value) => value !== null)) {
        if (crop.some((value) => value === null)) throw refusal("A crop needs all four of x, y, w and h.");
        args.crop = { x: crop[0], y: crop[1], w: crop[2], h: crop[3] };
      }
      if (focus.some((value) => value !== null)) {
        if (focus.some((value) => value === null)) throw refusal("A focus needs both x and y.");
        args.focus = { x: focus[0], y: focus[1] };
      }
      return args;
    };
    const place = async () => {
      if (!chosen) throw refusal("Pick a picture first.");
      if (chosen.file) {
        const answer = await ctx.api("media.upload", { scope: into ? into.value : art.reusable[0], media: await base64(chosen.file),
          note: note.value.trim() || null, origin: null, subject, slot, expected, ...framed() });
        return answer.art;
      }
      const mode = chosen.ref === inherited && art.modes.includes("adjust") ? "adjust" : "set";
      return ctx.api("media.place", { subject, slot, mode, image: chosen.ref, expected, ...framed() });
    };
    const name = slotName(slot);
    const cancel = ui.button("Cancel", () => modal.close());
    if (locked) {
      modal.body.append(ui.notice(art.editable
        ? "Two devices changed this slot. Choose which to keep under Devices and sync before changing it again."
        : "This device's sync records cannot be read, so no slot can be changed here.", { kind: "bad" }));
      modal.foot.append(cancel);
      return;
    }
    if (ctx.has("media.place") && art.modes.includes("hide") && shown.state !== "hidden") {
      buttons.push(ui.button("Hide this slot", run(() => ctx.api("media.place", { subject, slot, mode: "hide", expected }), name + " hidden.")));
    }
    if (ctx.has("media.place") && art.modes.includes("inherit") && own && own.mode !== "inherit") {
      buttons.push(ui.button("Use the inherited picture", run(() => ctx.api("media.place", { subject, slot, mode: "inherit", expected }),
        name + " follows its source again.")));
    }
    const extra = buttons.slice();
    const confirm = ui.button("Use this picture", run(place, name + " updated."), { kind: "primary" });
    buttons.push(cancel, confirm);
    modal.body.append(el("div", { class: "art-picker" }, pick, framing), problem);
    modal.foot.append(...extra, el("span", { class: "g-grow" }), cancel, confirm);
    if (chosen) { const start = chosen; chosen = null; choose(start); }
    else { setFrame(null, null); preview(); caption.textContent = pictures.length ? "" : "No picture here yet: add one."; }
    const first = tiles.find(([picture]) => chosen && picture.ref === chosen.ref) || tiles[0];
    (first ? first[1] : ctx.has("media.upload") ? add : cancel).focus();
  });
}
