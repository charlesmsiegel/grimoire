// 070's proving scenario through the real chat page in fakedom against a real app server over a synthetic
// library: import a sketch, circle the doorway, say "I hide here", take the offered draft action, and post it. The
// post carries the marked sketch as the player's uncommitted intent; the turn and the scene gain nothing else.
// Run by test_reference_intent_page.py, which seats this device at GRIMOIRE_CHAT, passes it, GRIMOIRE_APP and
// GRIMOIRE_TOKEN, and checks
// afterwards what the library on disk gained.
import assert from "node:assert/strict";
import { register } from "node:module";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { createHash } from "node:crypto";

import { byText, install, until } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC = pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static") + path.sep).href;
register("data:text/javascript," + encodeURIComponent(`
  export async function resolve(specifier, context, next) {
    if (specifier.startsWith("/static/")) return next(${JSON.stringify(STATIC)} + specifier.slice(8), context);
    return next(specifier, context);
  }`));

const env = process.env;
const base = env.GRIMOIRE_APP, CHAT = env.GRIMOIRE_CHAT;
const PNG = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 0x0d];
const png = (tail) => new Uint8Array([...PNG, ...new TextEncoder().encode("IHDR synthetic " + tail)]);

async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}
function type(input, text) { input.value = text; input.dispatchEvent({ type: "input" }); }

test("a circled doorway on a sketch is offered as a draft action and carried with the post as intent only", { skip: !base }, async () => {
  const window = install(base);
  // the browser's image decoding and drawing, as stand-ins: the sketch decodes as 400 × 200, and the marked
  // sketch the canvas saves is a picture of its own, whose bytes say which outline was drawn on it
  const drawn = [];
  globalThis.createImageBitmap = async () => ({ width: 400, height: 200, close() {} });
  globalThis.OffscreenCanvas = class {
    constructor(width, height) { this.size = [width, height]; this.ops = []; }
    getContext() { const ops = this.ops; return new Proxy({}, { get: (_t, name) => (...args) => ops.push([name, ...args]), set: () => true }); }
    async convertToBlob({ type }) {
      const outline = this.ops.find((op) => op[0] === "ellipse" || op[0] === "rect");
      drawn.push({ size: this.size, outline });
      return new Blob([png("marked " + outline.slice(1, 5).map((n) => Math.round(n)).join(","))], { type });
    }
  };
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  window.location.hash = "#/chat/" + encodeURIComponent(CHAT);
  const composer = () => main.querySelector("div.chat-composer");
  const panel = await until(() => main.querySelector("details.reference-intent") && composer() && main.querySelector("details.reference-intent"),
    15000, "the reference panel beside the composer");
  const tray = () => composer().querySelector("div.chat-attach-tray");
  await until(() => byText(composer(), "button", "Attach an image").length, 15000, "the attach button");
  const before = await call("open", { chat: CHAT });
  const status = () => panel.querySelector("p.reference-status").textContent;

  // import the sketch, choose an ellipse and circle the doorway with a drag
  const sketch = png("sketch of a hall");
  const input = panel.querySelectorAll("input").find((node) => node.getAttribute("name") === "reference-file");
  input.files = [new File([sketch], "hall.png", { type: "image/png" })];
  input.dispatchEvent({ type: "change" });
  const image = await until(() => panel.querySelector("img"), 15000, "the sketch in the panel");
  image.getBoundingClientRect = () => ({ left: 0, top: 0, width: 400, height: 200 });
  const select = panel.querySelector("select");
  select.value = "ellipse";
  select.dispatchEvent({ type: "change" });
  const preview = panel.querySelector("div.reference-preview");
  preview.dispatchEvent({ type: "pointerdown", pointerId: 7, button: 0, clientX: 280, clientY: 40 });
  preview.dispatchEvent({ type: "pointermove", pointerId: 7, clientX: 300, clientY: 120 });
  preview.dispatchEvent({ type: "pointerup", pointerId: 7, clientX: 320, clientY: 180 });
  assert.ok(panel.querySelector("ellipse"), "the circle is drawn over the sketch");
  assert.match(status(), /circled/);
  const field = (name) => panel.querySelectorAll("input").concat(panel.querySelectorAll("textarea")).find((node) => node.getAttribute("name") === name);
  type(field("region-label"), "doorway");
  type(field("action"), "I hide here");
  type(field("private-notes"), "PRIVATE NOTE");
  assert.equal(panel.querySelector("p.reference-draft").textContent, "Draft action: I hide here (Reference: doorway) · with your marked sketch");
  assert.equal(composer().querySelector("textarea").value, "", "nothing reaches the composer before the player takes it");

  // the player's own image waits: the marked sketch never replaces it
  const own = png("players own");
  const attachInput = composer().querySelectorAll("input").find((node) => node.getAttribute("type") === "file");
  attachInput.files = [{ name: "lamp.png", size: own.length, arrayBuffer: async () => own.buffer.slice(0) }];
  attachInput.dispatchEvent({ type: "change" });
  byText(panel, "button", "Use as draft")[0].click();
  await until(() => /Remove it first/.test(status()), 15000, "the refusal");
  assert.equal(composer().querySelector("textarea").value, "");
  assert.ok(tray().textContent.includes("Attached to your next post: lamp.png"));
  byText(tray(), "button", "Remove")[0].click();

  byText(panel, "button", "Use as draft")[0].click();
  await until(() => !tray().hidden, 15000, "the marked sketch in the tray");
  assert.equal(composer().querySelector("textarea").value, "I hide here (Reference: doorway)");
  assert.ok(tray().textContent.includes("Attached to your next post: your marked sketch"), tray().textContent);
  assert.ok(tray().textContent.includes("no map or scene record changes"));
  assert.deepEqual(drawn.map((item) => [item.size, item.outline.slice(1, 5).map(Math.round)]), [[[400, 200], [300, 110, 20, 70]]]);
  assert.equal((await call("open", { chat: CHAT })).turns.length, before.turns.length, "taking the draft posts nothing");
  assert.deepEqual((await call("media.chat", { chat: CHAT })).posts, [], "and attaches nothing yet");

  const post = byText(composer(), "button", "Post")[0];
  await until(() => !post.disabled, 15000, "Post");
  post.click();
  const picture = await until(() => main.querySelector("img.chat-attached-img"), 30000, "the marked sketch under the post");
  const row = main.querySelectorAll("div.chat-post").find((node) => node.contains(picture));
  assert.ok(row.textContent.includes("I hide here (Reference: doorway)"));
  await until(() => /went with your post/.test(status()), 15000, "the panel told");
  assert.ok(tray().hidden, "the tray is empty for the next post");

  const after = await call("open", { chat: CHAT });
  assert.equal(after.turns.length, before.turns.length + 1);
  const posted = after.turns.at(-1);
  assert.equal(posted.post.text, "I hide here (Reference: doorway)");
  assert.deepEqual(Object.keys(posted.post).sort(), ["actor", "channel", "kind", "private", "speaker", "text", "to", "withheld"],
    "the turn's post carries nothing of the sketch");
  const art = await call("media.chat", { chat: CHAT });
  assert.deepEqual(art.posts.map((item) => [item.turn, item.state]), [[posted.turn, "shown"]]);
  const scope = CHAT.split("#")[0];
  const images = (await call("media.gallery", { scope })).images;
  const carried = images.find((item) => item.image === art.posts[0].image);
  assert.ok(carried, "the marked sketch is the campaign's own picture");
  assert.equal(carried.provenance.origin, "player reference intent");
  assert.equal(carried.provenance.note, "Player reference intent, not a world fact: doorway; ellipse at x 0.7, y 0.2, width 0.1, height 0.7 of the sketch.");
  assert.ok(!JSON.stringify(images).includes("PRIVATE NOTE"));
  // the bytes kept are the marked sketch, not the imported one, which is kept nowhere
  assert.equal(carried.sha256, createHash("sha256").update(png("marked 300,110,20,70")).digest("hex"));
  assert.ok(!images.some((item) => item.sha256 === createHash("sha256").update(sketch).digest("hex")));
});
