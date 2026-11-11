// The Import page's card flow and every state of its review, in fakedom against a real app server: the kinds
// across the top, a card chosen or dropped on the drop zone, what was found, the preview's file list and the
// selected file, then Reject, a proposal the server no longer holds, an approval gone stale, and a file that is
// no card. Run by test_app_imports.py, which serves a synthetic library and passes GRIMOIRE_APP, GRIMOIRE_TOKEN
// and GRIMOIRE_CARD (a file holding a synthetic Character Card V3).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
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

function fileOf(name, bytes) {
  return { name, size: bytes.length, arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.length) };
}

// An op called beside the page, as another tab or device would.
async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": process.env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}

test("a card is found, previewed and decided, and every review state says what happened", { skip: !base }, async () => {
  const card = new Uint8Array(readFileSync(process.env.GRIMOIRE_CARD));
  const window = install(base);
  // the proposals the page is handed, so the test can end one beside the page
  const proposals = [];
  const pageFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const response = await pageFetch(url, options);
    if (String(url).endsWith("/api/imports.card_preview") && response.ok) proposals.push((await response.clone().json()).proposal);
    return response;
  };
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  window.location.hash = "#/import";

  // the kinds the app can import today, one of them chosen; no card kind the backend cannot read is offered
  const kinds = await until(() => main.querySelector("nav.import-kinds"), 15000, "the Import page");
  assert.deepEqual(kinds.querySelectorAll("a").map((node) => node.querySelector("span").textContent),
    ["Character card", "Chat history", "V1 library", "Change set", "Source update"]);
  assert.deepEqual(kinds.querySelectorAll("a").filter((node) => node.getAttribute("aria-current") === "page")
    .map((node) => node.querySelector("span").textContent), ["Character card"]);
  const zone = () => main.querySelector("div.import-drop");
  const chooser = () => zone().querySelector("input");
  assert.equal(chooser().getAttribute("accept"), ".json,application/json", "only the formats the app reads");
  assert.ok(!main.textContent.includes("PNG card") && !main.textContent.includes("folder of them"), "nothing the app cannot import is offered");
  assert.ok(byText(zone(), "button", "Choose a file").length);
  assert.equal(main.querySelector("section.import-review"), null);

  const choose = (file) => { chooser().files = [file]; chooser().dispatchEvent({ type: "change" }); };
  const preview = async () => {
    const go = await until(() => byText(main, "button", "Preview import").find((node) => !node.disabled), 15000, "what was found");
    go.click();
    return until(() => main.querySelector("section.import-review"), 30000, "the review");
  };
  const state = () => main.querySelector("section.import-review").querySelector("div.g-eyebrow").textContent;
  const outcome = (text) => until(() => byText(main, "strong.import-outcome", text).length, 30000, "the outcome: " + text);
  const approve = async () => {
    byText(main, "button", "Approve and write").find((node) => !node.disabled).click();
    const dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
    byText(dialog, "button", "Write ")[0].click();
  };
  const again = async () => {
    byText(main, "button", "Import another")[0].click();
    await until(() => zone() && !main.querySelector("section.import-review"), 15000, "the drop zone again");
  };

  // after a file is read: what was found, before anything is sent
  choose(fileOf("mara.json", card));
  const name = await until(() => main.querySelector("h2.import-found-name"), 15000, "what was found");
  assert.equal(name.textContent, "Mara Vell");
  const found = main.querySelectorAll("section").find((node) => node.getAttribute("aria-label") === "What was found");
  assert.ok(found.textContent.includes("mara.json") && found.textContent.includes("Character Card V3"));
  assert.ok(byText(found, "button", "Add a portrait (optional)").length && byText(found, "button", "Add a lorebook file (optional)").length);
  assert.ok(byText(main, "label", "Import as a scenario").length);
  assert.equal(zone(), null, "the drop zone gives way to what was found");

  // the review: a summary, each file with what it does and its size, the selected file's text, and the digest
  let review = await preview();
  assert.equal(state(), "Review before anything is written");
  assert.equal(review.querySelector("h2").textContent, "Mara Vell becomes a new character");
  assert.ok(review.querySelectorAll("dt").find((node) => node.textContent === "Destination"));
  assert.ok(!review.textContent.includes("[object"));
  let files = review.querySelectorAll("button.import-file");
  assert.ok(files.length > 1);
  assert.ok(review.querySelector("div.import-count").textContent === files.length + " files");
  for (const node of files) assert.match(node.textContent, /New file\d+ bytes$/);
  assert.equal(files[0].getAttribute("aria-pressed"), "true");
  assert.ok(review.querySelector("p.import-hash").textContent.startsWith(files[0].querySelector("span").textContent + " · sha256 "));
  const first = review.querySelector("pre").textContent;
  files[1].click();
  assert.deepEqual(files.map((node) => node.getAttribute("aria-pressed")), ["false", "true", ...files.slice(2).map(() => "false")]);
  assert.ok(review.querySelector("p.import-hash").textContent.startsWith(files[1].querySelector("span").textContent + " · sha256 "));
  assert.notEqual(review.querySelector("pre").textContent, first, "the pane shows the selected file");
  assert.ok(byText(review, "span.import-digest", "Digest sha256:").length);
  assert.ok(byText(review, "button", "Approve and write " + files.length + " files").length);

  // Reject: nothing is written, the footer says so and offers the way on
  byText(review, "button", "Reject")[0].click();
  await outcome("Rejected: nothing was written");
  assert.equal(state(), "Rejected");
  assert.ok(!byText(main, "button", "Approve").length && !byText(main, "button", "Reject").length);
  assert.equal(byText(main, "a", "Open the library")[0].getAttribute("href"), "#/sources");
  assert.equal(main.querySelectorAll("button.import-file").length, files.length, "what was reviewed stays on screen");
  await again();

  // a card dropped on the zone is read the same way; a proposal the server no longer holds may have been
  // written, so the page says to check instead of saying it was not
  zone().dispatchEvent({ type: "drop", preventDefault() {}, dataTransfer: { files: [fileOf("mara.json", card)] } });
  await preview();
  await call("imports.proposal_reject", { proposal: proposals.at(-1) });
  await approve();
  await outcome("This may already have been written");
  assert.equal(state(), "Check whether this was written");
  assert.ok(!byText(main, "button", "Approve").length);
  await again();

  // an approval of a preview the library has moved past writes nothing and says to preview again
  choose(fileOf("mara.json", card));
  await preview();
  const beside = await call("imports.card_preview", { card: Buffer.from(card).toString("base64"), origin: "mara.json" });
  await call("imports.proposal_apply", { proposal: beside.proposal, digest: beside.digest });
  await approve();
  await outcome("Not written: preview it again");
  assert.equal(state(), "Not written");
  assert.ok(byText(body, "div", "Nothing was written; preview it again.").length, "the refusal is said with its reason");
  await again();

  // a file that is no card: said plainly when it is read, and refused by the preview with nothing proposed
  choose(fileOf("notes.json", new TextEncoder().encode("not a card")));
  await until(() => main.querySelector("h2.import-found-name"), 15000, "the file read");
  assert.ok(main.textContent.includes("This is not a JSON document"));
  const alerts = () => body.querySelectorAll("div").filter((node) => node.getAttribute("role") === "alert" && node.textContent.length).length;
  const before = alerts();
  byText(main, "button", "Preview import").find((node) => !node.disabled).click();
  await until(() => alerts() > before, 15000, "the refusal");
  assert.equal(main.querySelector("section.import-review"), null, "a refused preview proposes nothing");
  assert.ok(byText(main, "button", "Use a different file").length);
});
