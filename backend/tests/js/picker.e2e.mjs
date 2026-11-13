// The picture picker (pages/art/picker.js) in fakedom, against a real app server over a synthetic library: its
// zoom and crop arithmetic, then on the Art page an avatar chosen from the pictures already there, a cancel that
// writes nothing, a slot that changed meanwhile refused inside the modal, the modal closing with its page, and a
// cover framed 16/5 and taken off again with Remove.
// Run by test_media_pages.py, which passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_SCOPE and GRIMOIRE_SUBJECT.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const base = process.env.GRIMOIRE_APP;
const scope = process.env.GRIMOIRE_SCOPE, subject = process.env.GRIMOIRE_SUBJECT;
const png = (tail) => Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d,
  ...new TextEncoder().encode("IHDR synthetic " + tail)]).toString("base64");

// An op called beside the page, as another tab or device would.
async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": process.env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}

test("the picker chooses, cancels, refuses a stale slot and closes with its page", { skip: !base }, async () => {
  const window = install(base);
  const { cropOf, zoomOf, aspectClass, slotAspect, slotKind, slotName, ZOOM_MIN, ZOOM_MAX, ZOOM_STEP } = await import("/static/pages/art/picker.js");

  // a zoom is stored as a centred crop, and read back as the same zoom; any other box is an exact crop
  assert.equal(cropOf(100), null);
  assert.deepEqual(cropOf(200), { x: 2500, y: 2500, w: 5000, h: 5000 });
  for (let zoom = ZOOM_MIN; zoom <= ZOOM_MAX; zoom += ZOOM_STEP) {
    const crop = cropOf(zoom);
    assert.equal(zoomOf(crop), zoom);
    if (crop) assert.ok(crop.w >= 1 && crop.x + crop.w <= 10000 && crop.y + crop.h <= 10000, "the crop lies inside the picture");
  }
  assert.equal(zoomOf(null), 100);
  assert.equal(zoomOf({ x: 0, y: 0, w: 5000, h: 5000 }), null);
  assert.equal(zoomOf({ x: 1000, y: 1000, w: 8000, h: 6000 }), null);
  assert.deepEqual([slotAspect("avatar"), slotAspect("portrait"), slotAspect("gallery1")], ["1/1", "3/4", "3/4"]);
  assert.deepEqual([aspectClass("1/1"), aspectClass("3/4")], ["art-aspect-1-1", "art-aspect-3-4"]);
  // slots beyond a character's own: a cover and a backdrop are 16/5 bands, an entry's picture a square, a post's 16/10
  assert.deepEqual(["cover", "backdrop.inn", "backdrop.t-1", "entry.0a1b", "post.t-1.000002"].map(slotAspect), ["16/5", "16/5", "16/5", "1/1", "16/10"]);
  assert.deepEqual([aspectClass("16/5"), aspectClass("16/10"), aspectClass("9/9")], ["art-aspect-16-5", "art-aspect-16-10", "art-aspect-1-1"]);
  assert.deepEqual(["cover", "backdrop.inn", "entry.0a1b", "post.t-1.2", "avatar", "backdrop", "expr.default.smile", "coverage"].map(slotKind),
    ["cover", "backdrop", "entry", "post", null, null, null, null]);
  assert.deepEqual(["cover", "backdrop.inn", "entry.0a1b", "post.t-1.2", "gallery1"].map(slotName), ["Cover", "Backdrop", "Entry picture", "Post picture", "gallery1"]);

  // two pictures are already in the character's gallery, placed nowhere
  const dusk = (await call("media.upload", { scope, media: png("dusk"), note: "Harbor at dusk", origin: null })).image.image;
  const lamp = (await call("media.upload", { scope, media: png("lamp"), note: "Lamp room", origin: null })).image.image;

  const before = await call("media.art", { subject });
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  const address = "#/art?scope=" + encodeURIComponent(scope) + "&subject=" + encodeURIComponent(subject);
  window.location.hash = address;
  const slot = (name) => main.querySelector('div[data-slot="' + name + '"]');
  const open = async (name, label) => {
    const button = await until(() => slot(name) && byText(slot(name), "button", label).find((node) => !node.disabled), 15000, label + " on " + name);
    button.click();
    return until(() => body.querySelector("dialog"), 15000, "the picker");
  };
  const tile = (dialog, text) => dialog.querySelectorAll("button.art-pick").find((node) => (node.getAttribute("aria-label") || "").startsWith(text));
  const alertIn = (dialog) => dialog.querySelectorAll("div").find((node) => node.getAttribute("role") === "alert" && !node.hidden);

  // the modal: its header names the target, its two steps, a preview at the slot's shape, a fixed foot
  let dialog = await open("avatar", "Choose");
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for Mara Vell: avatar");
  assert.deepEqual(dialog.querySelectorAll("div.g-eyebrow").map((node) => node.textContent), ["1 · Pick a picture", "2 · Frame it"]);
  assert.ok(dialog.querySelector("div.art-picker-frame").classList.contains("art-aspect-1-1"), "an avatar is framed square");
  assert.ok(dialog.textContent.includes("Beside their lines and in lists"));
  assert.ok(byText(dialog, "button", "Drop or choose a file").length);
  assert.ok(tile(dialog, "Harbor at dusk") && tile(dialog, "Lamp room"), "the subject's pictures are offered");
  assert.equal(dialog.querySelectorAll("button.art-pick").filter((node) => node.getAttribute("aria-pressed") === "true" && !node.hidden).length, 0);
  // the card says a picture belongs here that it does not hold: the slot can be hidden (a standalone card inherits
  // nothing, so there is no inherited picture to go back to), or given a picture
  assert.deepEqual(dialog.querySelector("div.g-modal-foot").querySelectorAll("button").map((node) => node.textContent),
    ["Hide this slot", "Cancel", "Use this picture"]);
  assert.ok(!byText(dialog, "button", "Use the inherited picture").length);
  const range = dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "range");
  assert.deepEqual([range.getAttribute("min"), range.getAttribute("max")], ["100", "200"]);

  // nothing picked: said in the modal, which stays open; Cancel then writes nothing
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => alertIn(dialog), 15000, "the refusal in the modal");
  assert.ok(alertIn(dialog).textContent.includes("Pick a picture first."));
  assert.ok(body.querySelector("dialog"));
  byText(dialog, "button", "Cancel")[0].click();
  await until(() => !body.querySelector("dialog"), 15000, "the picker closed");
  assert.equal(slot("avatar").querySelector("img"), null);
  assert.deepEqual(await call("media.art", { subject }), before, "a cancel writes nothing");

  // choose: the picked tile is the pressed one, and Use this picture places it
  dialog = await open("avatar", "Choose");
  tile(dialog, "Lamp room").click();
  assert.equal(tile(dialog, "Lamp room").getAttribute("aria-pressed"), "true");
  assert.equal(tile(dialog, "Harbor at dusk").getAttribute("aria-pressed"), "false");
  assert.ok(dialog.querySelector("p.art-picker-caption").textContent.startsWith("Lamp room"));
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => !body.querySelector("dialog") && slot("avatar").textContent.includes("Shown"), 15000, "the avatar placed");
  assert.ok(slot("avatar").textContent.includes("Lamp room"));
  let art = await call("media.art", { subject });
  assert.equal(art.slots.find((item) => item.slot === "avatar").image, lamp);

  // a slot another device changed while the picker was open: refused in the modal, nothing swapped
  dialog = await open("avatar", "Replace");
  assert.equal(tile(dialog, "Lamp room").getAttribute("aria-pressed"), "true", "the picture shown now starts picked");
  assert.deepEqual(dialog.querySelector("div.g-modal-foot").querySelectorAll("button").map((node) => node.textContent),
    ["Hide this slot", "Cancel", "Use this picture"], "a character's picture can be hidden; a standalone card has none to go back to");
  await call("media.place", { subject, slot: "avatar", mode: "set", image: dusk, expected: art.own.find((item) => item.slot === "avatar").etag });
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => alertIn(dialog), 15000, "the stale slot refused");
  assert.ok(alertIn(dialog).textContent.startsWith("changed:"));
  assert.ok(body.querySelector("dialog"), "the modal stays open on a refusal");

  // the modal closes with its page, and answers as a cancel
  window.location.hash = "#/home";
  await until(() => !body.querySelector("dialog") && window.location.hash === "#/home", 15000, "the picker left with its page");
  art = await call("media.art", { subject });
  assert.equal(art.slots.find((item) => item.slot === "avatar").image, dusk, "only the other device's change stands");

  // back on the page: Crop opens the same picker on the picture shown, and a zoom is saved as its crop
  window.location.hash = address;
  dialog = await open("avatar", "Crop");
  assert.equal(tile(dialog, "Harbor at dusk").getAttribute("aria-pressed"), "true");
  const zoom = dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "range");
  zoom.value = "200";
  zoom.dispatchEvent({ type: "input" });
  assert.deepEqual(dialog.querySelectorAll("input").filter((node) => node.getAttribute("type") === "number").slice(0, 4).map((node) => node.value),
    ["25", "25", "50", "50"], "the zoom fills in the exact crop");
  // Saving closes the modal before the page's gallery refresh finishes. Hold that
  // response to exercise the stale-image interval seen on slower CI runners.
  const fetchBeforeRefresh = globalThis.fetch;
  let releaseGallery, galleryHeld = false;
  const galleryGate = new Promise((resolve) => { releaseGallery = resolve; });
  globalThis.fetch = async (url, options) => {
    const response = await fetchBeforeRefresh(url, options);
    if (url === "/api/media.gallery") {
      galleryHeld = true;
      await galleryGate;
    }
    return response;
  };
  try {
    byText(dialog, "button", "Use this picture")[0].click();
    await until(() => !body.querySelector("dialog"), 15000, "the crop saved");
    await until(() => galleryHeld, 15000, "the page refresh is pending");
    art = await call("media.art", { subject });
    const avatar = art.slots.find((item) => item.slot === "avatar");
    assert.equal(avatar.image, dusk);
    assert.deepEqual(avatar.crop, { x: 2500, y: 2500, w: 5000, h: 5000 });
  } finally {
    globalThis.fetch = fetchBeforeRefresh;
    releaseGallery();
  }
  const shown = await until(() => {
    const image = slot("avatar") && slot("avatar").querySelector("img");
    return image && image.classList.contains("art-zoom-200") && image;
  }, 15000, "the avatar renders its saved zoom");
  assert.ok(shown.classList.contains("art-zoom-200"), "the slot shows the zoom it stores");

  // a cover, here the source's own: framed 16/5 at the modal's width, and taken off again with Remove
  window.location.hash = "#/art?scope=" + encodeURIComponent(scope) + "&subject=" + encodeURIComponent(scope);
  dialog = await open("cover", "Choose");
  assert.ok(dialog.getAttribute("aria-label").endsWith(": cover"), dialog.getAttribute("aria-label"));
  const wide = dialog.querySelector("div.art-picker-frame");
  assert.ok(wide.classList.contains("art-aspect-16-5") && wide.classList.contains("art-picker-wide"), "a cover is framed 16/5");
  assert.ok(dialog.textContent.includes("Shown as a band across the top, and on its tile"));
  assert.deepEqual(dialog.querySelector("div.g-modal-foot").querySelectorAll("button").map((node) => node.textContent),
    ["Cancel", "Use this picture"], "nothing to remove before a cover is set");
  tile(dialog, "Lamp room").click();
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => !body.querySelector("dialog") && slot("cover") && slot("cover").textContent.includes("Shown"), 15000, "the cover placed");
  assert.ok((await until(() => slot("cover").querySelector("div.art-frame"), 15000, "its frame")).classList.contains("art-aspect-16-5"));
  dialog = await open("cover", "Replace");
  assert.deepEqual(dialog.querySelector("div.g-modal-foot").querySelectorAll("button").map((node) => node.textContent),
    ["Remove", "Cancel", "Use this picture"]);
  byText(dialog, "button", "Remove")[0].click();
  await until(() => !body.querySelector("dialog") && slot("cover") && slot("cover").textContent.includes("Hidden"), 15000, "the cover removed");
  const removed = (await call("media.shown", { subjects: [scope] })).shown[0];
  assert.equal(removed.state, "hidden");
  assert.ok((await call("media.gallery", { scope })).images.some((image) => image.image === lamp), "the picture stays in its gallery");
});
