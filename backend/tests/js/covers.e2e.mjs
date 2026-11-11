// Pictures beyond character slots, through the real pages in fakedom against a real app server over a synthetic
// library: a cover set and removed from an entry sheet, an entry's picture, the library's tiles and world shelf
// (and how many requests a grid of tiles makes), a campaign's cover from its hub, a scenario's backdrops, a
// chat's backdrop taken from its scenario, and a picture attached to a post.
// Run by test_media_pages.py, which passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_LORE (a lorebook),
// GRIMOIRE_WORLD (a source), GRIMOIRE_CAMPAIGN (a campaign id), GRIMOIRE_SCENARIO and GRIMOIRE_PERSONA.
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

const env = process.env;
const base = env.GRIMOIRE_APP;
const LORE = env.GRIMOIRE_LORE, WORLD = env.GRIMOIRE_WORLD, CAMPAIGN = env.GRIMOIRE_CAMPAIGN, SCENARIO = env.GRIMOIRE_SCENARIO;
const bytes = (tail) => new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d,
  ...new TextEncoder().encode("IHDR synthetic " + tail)]);
const fileOf = (name, tail) => { const data = bytes(tail); return { name, size: data.length, arrayBuffer: async () => data.buffer.slice(0) }; };

// An op called beside the page, as another tab or device would; `refused` gives the refusal instead of failing.
async function call(op, args, { refused = false } = {}) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  if (!refused) assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}
const cover = async (subject, slot = "cover") => (await call("media.shown", { subjects: [subject], slot })).shown[0];

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

test("covers, entry pictures, backdrops and post pictures through the pages", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  // every request the pages make for covers, to count them
  const realFetch = globalThis.fetch;
  const asked = [];
  globalThis.fetch = (url, options) => { if (String(url).includes("/api/media.shown")) asked.push(JSON.parse(options.body)); return realFetch(url, options); };

  const idle = () => until(() => !body.querySelectorAll("button").some((node) => node.disabled && node.textContent !== "Post"), 15000, "the action to finish");
  const picker = (what = "the picker") => until(() => body.querySelector("dialog"), 15000, what);
  const closed = () => until(() => !body.querySelector("dialog"), 15000, "the picker closed");
  const foot = (dialog) => dialog.querySelector("div.g-modal-foot").querySelectorAll("button").map((node) => node.textContent);
  const tile = (dialog, text) => dialog.querySelectorAll("button.art-pick").find((node) => (node.getAttribute("aria-label") || "").startsWith(text));
  const alertIn = (dialog) => dialog.querySelectorAll("div").find((node) => node.getAttribute("role") === "alert" && !node.hidden);
  const give = (dialog, file) => {
    const input = dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "file");
    input.files = [file];
    input.dispatchEvent({ type: "change" });
  };
  const use = async (dialog) => { byText(dialog, "button", "Use this picture")[0].click(); await closed(); };
  // a new address: wait until the shell has mounted a new page and `ready()` holds on it, so a page still showing
  // is never pressed
  const go = async (hash, ready, what) => {
    const before = main.children[0];
    window.location.hash = hash;
    return until(() => main.children[0] && main.children[0] !== before && ready(), 15000, what);
  };
  const sheet = (ref, query = "") => "#/source/" + encodeURIComponent(ref) + query;

  // -- an entry sheet: the lorebook has no cover, so its title sits in a dashed block that offers one -----------
  await go(sheet(LORE), () => main.querySelector("div.art-cover-empty") && main.querySelector("button.art-slot-button"), "the lorebook's sheet");
  let block = main.querySelector("div.art-cover-empty");
  assert.equal(block.querySelector("h2").textContent, "Harbor lore");
  assert.equal(main.querySelector("div.g-cover"), null, "no band until there is a cover");
  byText(block, "button", "Drop a cover image, or choose one")[0].click();
  let dialog = await picker();
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the cover of Harbor lore");
  const frame = dialog.querySelector("div.art-picker-frame");
  assert.ok(frame.classList.contains("art-aspect-16-5") && frame.classList.contains("art-picker-wide"), "a cover is framed 16/5, at the modal's width");
  assert.ok(dialog.textContent.includes("Shown across the top of its sheet and on its library tile"));
  assert.deepEqual(foot(dialog), ["Cancel", "Use this picture"], "nothing to remove before something is set");
  give(dialog, fileOf("quay.png", "quay"));
  type(dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "text"), "The quay at dusk");
  await use(dialog);
  let band = await until(() => main.querySelector("div.g-cover"), 15000, "the cover band");
  assert.equal(main.querySelector("div.art-cover-empty"), null);
  assert.equal(band.querySelector("h2").textContent, "Harbor lore");
  assert.deepEqual(band.querySelectorAll("button").map((node) => node.textContent), ["Change cover", "Remove"]);
  assert.deepEqual(band.querySelectorAll("button").map((node) => node.getAttribute("aria-label")), ["Change cover of Harbor lore", "Remove cover of Harbor lore"]);
  const shown = await until(() => band.querySelector("img.art-cover-img"), 15000, "the cover's picture");
  assert.ok(shown.getAttribute("src").startsWith("blob:") && band.querySelector("span.art-scrim"), "the picture sits under a scrim, so the title stays readable");
  let now = await cover(LORE);
  assert.equal(now.state, "shown");
  const quay = now.image;
  assert.equal(document.activeElement.textContent, "Change cover", "the keyboard stays on the control that was used");

  // Change cover: the picture shown starts picked, a zoom is stored as its crop, and Remove is offered in the picker too
  byText(band, "button", "Change cover")[0].click();
  dialog = await picker();
  assert.equal(tile(dialog, "The quay at dusk").getAttribute("aria-pressed"), "true");
  assert.deepEqual(foot(dialog), ["Remove", "Cancel", "Use this picture"]);
  const zoom = dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "range");
  zoom.value = "150";
  zoom.dispatchEvent({ type: "input" });
  await use(dialog);
  band = await until(() => main.querySelector("div.g-cover"), 15000, "the band again");
  await until(() => band.querySelector("img.art-zoom-150"), 15000, "the cover at its zoom");
  now = await cover(LORE);
  assert.equal(now.image, quay);
  assert.equal(now.crop.w, 6667);

  // a cover another device changed while the picker was open: refused in the modal, nothing swapped
  byText(band, "button", "Change cover")[0].click();
  dialog = await picker();
  await call("media.place", { subject: LORE, slot: "cover", mode: "set", image: quay, expected: now.etag });
  byText(dialog, "button", "Use this picture")[0].click();
  await until(() => alertIn(dialog), 15000, "the stale cover refused");
  assert.ok(alertIn(dialog).textContent.startsWith("changed:"));
  byText(dialog, "button", "Cancel")[0].click();
  await closed();

  // -- an entry's picture: a 72px slot beside each entry of the current version ------------------------------------
  await go(sheet(LORE) + "?again=1", () => main.querySelectorAll("button.art-slot-button").length === 2, "the entries' picture slots");
  let slots = main.querySelectorAll("button.art-slot-button");
  assert.deepEqual(slots.map((node) => node.textContent), ["Add picture", "Add picture"]);
  assert.equal(slots[0].getAttribute("aria-label"), "Add picture to the entry lighthouse");
  slots[0].click();
  dialog = await picker();
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the picture of the entry lighthouse");
  assert.ok(dialog.querySelector("div.art-picker-frame").classList.contains("art-aspect-1-1"));
  tile(dialog, "The quay at dusk").click();  // a picture already in the lorebook's source: stored once, used twice
  await use(dialog);
  await until(() => main.querySelectorAll("button.art-slot-button")[0].querySelector("img"), 15000, "the entry's picture");
  slots = main.querySelectorAll("button.art-slot-button");
  assert.equal(slots[0].getAttribute("aria-label"), "Change the picture of the entry lighthouse");
  assert.equal(slots[1].textContent, "Add picture");
  let entries = await call("media.entries", { subject: LORE });
  assert.deepEqual(entries.entries.map((entry) => entry.picture.state), ["shown", "empty"]);
  assert.equal(entries.entries[0].picture.image, quay);
  // the entry edited elsewhere: its picture is on no entry any more, and the sheet says where it went
  const lore = await call("sources.read", { ref: LORE });
  await call("sources.save", { ref: LORE, head: lore.head, etag: lore.etag, expected: lore.manifest_etag,
    fields: { entries: [{ index: 0, content: "The lighthouse was relit last night." }, { index: 1 }] } });
  await go(sheet(LORE) + "?again=2", () => main.querySelector("p.art-orphans") && !main.querySelector("p.art-orphans").hidden, "the orphan note");
  assert.ok(main.querySelector("p.art-orphans").textContent.startsWith("1 picture here belonged to an entry that was edited or removed since."));
  assert.deepEqual(main.querySelectorAll("button.art-slot-button").map((node) => node.textContent), ["Add picture", "Add picture"]);
  const toArt = byText(main.querySelector("p.art-orphans"), "a", "See the pictures of")[0];
  assert.ok(toArt.getAttribute("href").startsWith("#/art?") && toArt.getAttribute("href").includes(encodeURIComponent(LORE)));
  // an older revision shows its entries without slots: pictures belong to the current version's entries
  await go(sheet(LORE + "@" + lore.head), () => main.querySelectorAll("div.source-lore").length === 2, "the older revision");
  assert.equal(main.querySelectorAll("button.art-slot-button").length, 0);

  // Remove: the band gives way to the dashed block again, and the picture stays in its gallery
  await go(sheet(LORE) + "?again=3", () => main.querySelector("div.g-cover") && byText(main.querySelector("div.g-cover"), "button", "Remove").length, "the cover again");
  byText(main.querySelector("div.g-cover"), "button", "Remove")[0].click();
  block = await until(() => main.querySelector("div.art-cover-empty"), 15000, "the dashed block after Remove");
  assert.equal(main.querySelector("div.g-cover"), null);
  assert.equal((await cover(LORE)).state, "hidden");
  assert.ok((await call("media.gallery", { scope: LORE.split("/")[0] })).images.some((image) => image.image === quay));
  // a file dropped on the block opens the picker with it
  block.dispatchEvent({ type: "drop", preventDefault() {}, dataTransfer: { files: [fileOf("fog.png", "fog")] } });
  dialog = await picker("the picker for a dropped file");
  assert.ok(dialog.querySelector("p.art-picker-caption").textContent.includes("fog.png"));
  byText(dialog, "button", "Cancel")[0].click();
  await closed();
  await idle();

  // -- the Art page lists the same slots like any other -------------------------------------------------------
  await go("#/art?scope=" + encodeURIComponent(LORE.split("/")[0]) + "&subject=" + encodeURIComponent(LORE),
    () => main.querySelector('div[data-slot="cover"]'), "the lorebook on the Art page");
  assert.ok(main.querySelector('div[data-slot="cover"]').textContent.includes("Hidden"));
  const orphan = main.querySelectorAll("div.art-slot").find((node) => (node.getAttribute("data-slot") || "").startsWith("entry."));
  assert.ok(orphan && orphan.textContent.includes("Entry picture") && orphan.textContent.includes("Shown"), "the orphaned entry picture is listed here");
  assert.ok(byText(orphan, "button", "Remove").length, "and can be taken off");
  assert.equal(main.querySelector('div[data-slot="avatar"]'), null, "a lorebook has no avatar or portrait");

  // -- the library: text tiles show their cover or offer one; a grid asks for its covers once -------------------
  // (what the sheet kept of the lorebook's cover is dropped first, so the grid's one request is all of its tiles)
  (await import("/static/pages/art/covers.js")).forgetSubject(LORE);
  asked.length = 0;
  const tiles = () => main.querySelectorAll("div.sources-tile");
  const tileFor = (ref) => tiles().find((node) => node.querySelector("a.g-tile").getAttribute("title") === ref);
  await go("#/sources", () => tiles().length && byText(main, "button", "Add cover").length, "the library");
  const texts = tiles().filter((node) => node.querySelector("span.g-tile-text"));
  assert.ok(texts.length >= 2, "the lorebook and the scenario at least");
  assert.equal(asked.length, 1, "one request reads every tile's cover");
  assert.deepEqual([...asked[0].subjects].sort(), texts.map((node) => node.querySelector("a.g-tile").getAttribute("title")).sort());
  // drawn again (another order, another role, the list): nothing is asked again
  await go("#/sources?sort=name", () => tiles().length && byText(main, "button", "Add cover").length, "the library by name");
  await go("#/sources?role=lore", () => tileFor(LORE) && byText(tileFor(LORE), "button", "Add cover").length, "the lore");
  assert.equal(asked.length, 1, "what was read is kept while the grid is drawn again");
  assert.equal(tiles().filter((node) => node.querySelector("span.g-portrait")).length, 0);
  // Add cover on the tile: the picture chosen shows behind the tile's kind and title
  byText(tileFor(LORE), "button", "Add cover")[0].click();
  dialog = await picker();
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the cover of Harbor lore");
  tile(dialog, "The quay at dusk").click();
  await use(dialog);
  const face = await until(() => tileFor(LORE) && tileFor(LORE).querySelector("span.art-tile-cover"), 15000, "the tile's cover");
  await until(() => face.querySelector("img.art-cover-img"), 15000, "the tile's picture");
  assert.equal(face.querySelector("span.g-eyebrow").textContent, "Lorebook", "the tile still names its kind");
  assert.equal(byText(tileFor(LORE), "button", "Add cover").length, 0);
  assert.equal((await cover(LORE)).image, quay);
  // the list shows it as the row's picture
  await go("#/sources?role=lore&view=list", () => main.querySelector("table.g-table"), "the list");
  await until(() => main.querySelector("table.g-table").querySelector("span.g-avatar").querySelector("img"), 15000, "the row's picture");

  // a world's shelf: its cover band above the results, with what the shelf could already do under it
  await go("#/sources?source=" + encodeURIComponent(WORLD), () => main.querySelector("section.sources-source-cover")
    && byText(main.querySelector("section.sources-source-cover"), "button", "Add cover").length, "the world's shelf");
  let shelf = main.querySelector("section.sources-source-cover");
  assert.equal(shelf.querySelector("h2").textContent, "Saltmarch");
  assert.ok(byText(shelf, "button", "Rename").length && byText(shelf, "a", "Show every source").length);
  byText(shelf, "button", "Add cover")[0].click();
  dialog = await picker();
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the cover of Saltmarch");
  assert.equal(tile(dialog, "The quay at dusk"), undefined, "a world places its own pictures, never another source's");
  give(dialog, fileOf("flats.png", "flats"));
  await use(dialog);
  shelf = await until(() => { const node = main.querySelector("section.sources-source-cover"); return node && byText(node, "button", "Remove").length && node; }, 15000, "the world's cover");
  assert.deepEqual(shelf.querySelector("div.g-cover").querySelectorAll("button").map((node) => node.textContent), ["Change cover", "Remove"]);
  assert.equal((await cover(WORLD)).state, "shown");

  // -- the campaign hub: its cover, set and removed ---------------------------------------------------------------
  const hubCover = () => main.querySelector("div.g-cover");
  await go("#/campaign/" + encodeURIComponent(CAMPAIGN), () => hubCover() && byText(hubCover(), "button", "Add cover").length, "the hub");
  assert.ok(hubCover().textContent.includes("1 chat"));
  byText(hubCover(), "button", "Add cover")[0].click();
  dialog = await picker();
  assert.ok(dialog.getAttribute("aria-label").startsWith("Choose a picture for the cover of "));
  assert.ok(dialog.textContent.includes("Shown at the top of the campaign hub"));
  give(dialog, fileOf("table.png", "table"));
  await use(dialog);
  await until(() => byText(hubCover(), "button", "Remove").length && hubCover().querySelector("img.art-cover-img"), 15000, "the campaign's cover");
  assert.equal((await cover("campaign:" + CAMPAIGN)).state, "shown");
  byText(hubCover(), "button", "Remove")[0].click();
  await until(() => byText(hubCover(), "button", "Add cover").length, 15000, "the cover removed");
  assert.equal(hubCover().querySelector("img"), null);
  assert.equal((await cover("campaign:" + CAMPAIGN)).state, "hidden");
  assert.ok(byText(body, "div", "Cover removed.").length, "and it says so");

  // -- a scenario's backdrops, and a chat played from it ------------------------------------------------------------
  const backdrops = () => main.querySelector('section[aria-label="Scene backdrops"]');
  await go(sheet(SCENARIO), () => backdrops() && byText(backdrops(), "button", "Add a backdrop").length, "the scenario's sheet");
  assert.equal(backdrops().querySelectorAll("div.art-row-tile").length, 0);
  byText(backdrops(), "button", "Add a backdrop")[0].click();
  dialog = await picker();
  assert.ok(dialog.getAttribute("aria-label").startsWith("Choose a picture for a backdrop of "));
  assert.ok(dialog.querySelector("div.art-picker-frame").classList.contains("art-aspect-16-5"));
  give(dialog, fileOf("inn.png", "inn"));
  type(dialog.querySelectorAll("input").find((node) => node.getAttribute("type") === "text"), "The harbor inn");
  await use(dialog);
  await until(() => backdrops().querySelectorAll("div.art-row-tile").length === 1 && backdrops().querySelector("div.art-row-frame").querySelector("img"), 15000, "the backdrop in its row");
  assert.deepEqual(backdrops().querySelector("div.art-row-tile").querySelectorAll("button").map((node) => node.textContent), ["Change", "Remove"]);
  const scenarioArt = await call("media.art", { subject: SCENARIO });
  const inn = scenarioArt.slots.find((slot) => slot.slot === "backdrop.1").image;

  const started = await call("play.start", { kind: "scenario", ref: SCENARIO, persona: env.GRIMOIRE_PERSONA });
  const chat = started.chat, scope = chat.split("#")[0];
  const bar = () => main.querySelector("div.chat-scene");
  await go("#/chat/" + encodeURIComponent(chat), () => bar() && byText(bar(), "button", "Backdrop").length, "the chat's scene bar");
  assert.ok(!bar().classList.contains("chat-backdrop-on"));
  assert.equal(bar().querySelectorAll("button").at(-1).getAttribute("aria-expanded"), "false", "the panel's button stays last");
  byText(bar(), "button", "Backdrop")[0].click();
  dialog = await picker();
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the backdrop of this chat");
  assert.ok(dialog.textContent.includes("Shown as a band behind the scene bar"));
  // the scenario's backdrop is offered, saying whose it is; taking it makes it the campaign's own picture
  const offered = tile(dialog, "The harbor inn");
  assert.equal(offered.getAttribute("aria-label"), "The harbor inn · " + SCENARIO);
  offered.click();
  await use(dialog);
  await until(() => bar().classList.contains("chat-backdrop-on") && bar().querySelector("span.chat-backdrop").querySelector("img"), 15000, "the backdrop behind the scene bar");
  assert.ok(bar().querySelector("span.chat-backdrop-scrim"), "under a scrim, so the bar's text stays readable");
  let art = await call("media.chat", { chat });
  assert.equal(art.backdrop.state, "shown");
  assert.ok(art.backdrop.image.startsWith(scope + "#image/") && art.backdrop.image !== inn);
  // Remove, in the picker
  await idle();
  byText(bar(), "button", "Backdrop")[0].click();
  dialog = await picker();
  assert.deepEqual(foot(dialog), ["Remove", "Cancel", "Use this picture"]);
  byText(dialog, "button", "Remove")[0].click();
  await closed();
  await until(() => !bar().classList.contains("chat-backdrop-on"), 15000, "the backdrop removed");
  assert.equal((await call("media.chat", { chat })).backdrop.state, "hidden");

  // -- a picture attached to a post ----------------------------------------------------------------------------------
  await idle();
  const composer = () => main.querySelector("div.chat-composer");
  const attachButton = () => byText(composer(), "button", "Attach an image")[0];
  const tray = () => composer().querySelector("div.chat-attach-tray");
  const attach = (file) => {
    const input = composer().querySelectorAll("input").find((node) => node.getAttribute("type") === "file");
    input.files = [file];
    input.dispatchEvent({ type: "change" });
  };
  assert.ok(attachButton() && tray().hidden);
  attach(fileOf("lamp.png", "lamp"));
  assert.ok(!tray().hidden && attachButton().hidden);
  assert.ok(tray().textContent.includes("Attached to your next post: lamp.png"));
  assert.ok(tray().textContent.includes("No character sees it and no model is sent it."));
  // Remove before posting: nothing is attached, and nothing was sent
  byText(tray(), "button", "Remove")[0].click();
  assert.ok(tray().hidden && !attachButton().hidden);
  assert.equal((await call("media.gallery", { scope })).images.length, 1, "only the backdrop's copy is in the campaign");
  attach(fileOf("lamp.png", "lamp"));
  const before = await call("open", { chat });
  type(composer().querySelector("textarea"), "I light the lamp.");
  byText(composer(), "button", "Post")[0].click();
  const picture = await until(() => main.querySelector("img.chat-attached-img"), 30000, "the picture under the post");
  const row = main.querySelectorAll("div.chat-post").find((node) => node.contains(picture));
  assert.ok(row.textContent.includes("I light the lamp."), "it sits under the post it was attached to");
  assert.ok(tray().hidden && !attachButton().hidden, "the composer is ready for the next post");
  art = await call("media.chat", { chat });
  const after = await call("open", { chat });
  const posted = after.turns.find((turn) => turn.post && turn.post.text === "I light the lamp.");
  assert.deepEqual(art.posts.map((item) => [item.turn, item.state]), [[posted.turn, "shown"]]);
  assert.equal(after.turns.length, before.turns.length + 1);
  assert.deepEqual(Object.keys(posted).sort(), ["device", "kept", "number", "offered", "open", "origin", "passed", "paused", "post", "set_aside", "turn"],
    "the turn itself carries nothing of the picture");
  assert.deepEqual(Object.keys(posted.post).sort(), ["actor", "channel", "kind", "private", "speaker", "text", "to", "withheld"]);
  // the turn passed elsewhere and the chat opened again: the picture is still under its post
  await idle();
  await call("pass", { chat, turn: (await call("open", { chat })).state.open_turn });
  await go("#/chat/" + encodeURIComponent(chat) + "?again=1", () => main.querySelector("img.chat-attached-img") && attachButton(), "the chat again");
  await until(() => !byText(composer(), "button", "Post")[0].disabled || composer().querySelector("textarea"), 15000, "the composer");
  // Change image opens the same picker on the post's slot, framed 16/10
  await idle();
  byText(main, "button", "Change image")[0].click();
  dialog = await picker("the picker for a post's image");
  assert.equal(dialog.getAttribute("aria-label"), "Choose a picture for the image on this post");
  assert.ok(dialog.querySelector("div.art-picker-frame").classList.contains("art-aspect-16-10"));
  assert.deepEqual(foot(dialog), ["Remove", "Cancel", "Use this picture"]);
  byText(dialog, "button", "Cancel")[0].click();
  await closed();
  // a picture that cannot be attached: the post stands, and the composer says so with the ways on
  await idle();
  const notes = new TextEncoder().encode("only words, no picture");
  attach({ name: "notes.txt", size: notes.length, arrayBuffer: async () => notes.buffer.slice(0) });
  type(composer().querySelector("textarea"), "I wait for the tide.");
  await until(() => !byText(composer(), "button", "Post")[0].disabled, 15000, "Post");
  byText(composer(), "button", "Post")[0].click();
  await until(() => !tray().hidden && tray().getAttribute("role") === "alert", 30000, "the refused attachment");
  assert.ok(tray().textContent.startsWith("Your post was made, but its image was not attached: unsupported_media"), tray().textContent);
  assert.deepEqual(tray().querySelectorAll("button").map((node) => node.textContent), ["Try again", "Remove"]);
  assert.ok((await call("open", { chat })).turns.some((turn) => turn.post && turn.post.text === "I wait for the tide."), "the post was made");
  byText(tray(), "button", "Remove")[0].click();
  assert.ok(tray().hidden);
  // Remove image, on the post
  await idle();
  byText(main, "button", "Remove image")[0].click();
  await until(() => !main.querySelector("img.chat-attached-img"), 15000, "the picture taken off the post");
  assert.deepEqual((await call("media.chat", { chat })).posts, []);
});
