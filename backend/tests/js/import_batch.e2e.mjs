// The Import page's batch of cards, in fakedom against a real app server: a dropped folder is read through the
// drop's own directory entries, every card found is listed with what importing it would do (decided by the app),
// a file that is no card stays in the table with its reason, the ticked cards are reviewed as one proposal grouped
// by card and approved at once, an approval the library has moved past writes nothing, and too many files are
// refused. Run by test_app_imports.py, which serves a synthetic library that already holds one of the cards and
// passes GRIMOIRE_APP, GRIMOIRE_TOKEN and GRIMOIRE_BATCH (a folder of synthetic card files).
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
const stored = (name) => new Uint8Array(readFileSync(path.join(process.env.GRIMOIRE_BATCH, name)));

// A drop's directory entries, as a browser hands them over: a folder answers its children a part at a time.
function fileEntry(folder, name, bytes) {
  return { isFile: true, isDirectory: false, name, fullPath: folder + "/" + name, file: (done) => done(fileOf(name, bytes)) };
}
function folderEntry(parent, name, children) {
  const fullPath = parent + "/" + name;
  const made = children(fullPath);
  return { isFile: false, isDirectory: true, name, fullPath, createReader() {
    const parts = [made.slice(0, 2), made.slice(2), []];
    return { readEntries: (done) => done(parts.shift() || []) };
  } };
}

async function call(op, args) {
  const response = await fetch(base + "/api/" + op, { method: "POST", body: JSON.stringify(args),
    headers: { "Content-Type": "application/json", "X-Grimoire-Token": process.env.GRIMOIRE_TOKEN } });
  const answer = await response.json();
  assert.ok(response.ok, op + ": " + JSON.stringify(answer));
  return answer;
}

test("a folder of cards is listed, reviewed as one proposal and approved at once", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");
  window.location.hash = "#/import";
  const zone = await until(() => main.querySelector("div.import-drop"), 15000, "the Import page");
  assert.equal(zone.querySelector("span.import-drop-title").textContent, "Drop a card, or a whole folder of them");
  assert.ok(zone.querySelector("input").hasAttribute("multiple"), "several files can be chosen");
  assert.ok(byText(zone, "button", "Choose files").length);

  // a folder is dropped: its card files are found through the drop's directory entries, also in a folder inside it
  const folder = folderEntry("", "cards", (at) => [
    fileEntry(at, "tobin.json", stored("tobin.json")),
    fileEntry(at, "readme.txt", new TextEncoder().encode("not a card file")),
    fileEntry(at, "mara.png", stored("mara.png")),
    folderEntry(at, "more", (inner) => [fileEntry(inner, "ilse.png", stored("ilse.png")), fileEntry(inner, "picture.png", stored("picture.png"))]),
    fileEntry(at, "notes.json", new TextEncoder().encode("not a card")),
  ]);
  zone.dispatchEvent({ type: "drop", preventDefault() {}, dataTransfer: { items: [{ webkitGetAsEntry: () => folder }], files: [] } });
  const table = await until(() => main.querySelector("table.import-batch"), 30000, "the batch table");
  assert.equal(main.querySelector("h2.import-batch-title").textContent, "3 cards found");
  assert.equal(main.querySelector("div.import-batch-said").textContent,
    "2 new · 1 updates a card you have · 2 cannot be imported · 1 other file in the folder is not a card file");
  assert.deepEqual(table.querySelectorAll("th").map((node) => node.textContent), ["Include", "Picture", "Card", "File", "Lorebooks", "Result"]);
  const rows = () => main.querySelector("table.import-batch").querySelector("tbody").querySelectorAll("tr");
  const cells = (row) => row.querySelectorAll("td").map((node) => node.textContent);
  // in the folder's own order, by name; each with its file, its lorebooks and the app's result
  assert.deepEqual(rows().map((row) => cells(row).slice(2)), [
    ["Mara Vell", "cards/mara.png", "Harbor lore (2 entries)", "Updates the card you have"],
    ["Ilse Dorn", "cards/more/ilse.png", "None", "New"],
    ["Left out", "cards/more/picture.png", "", cells(rows()[2])[5]],
    ["Left out", "cards/notes.json", "", "Cannot be imported: This file is not a JSON document."],
    ["Tobin Marsh", "cards/tobin.json", "Harbor lore (2 entries)", "Same name as another card"],
  ]);
  assert.ok(cells(rows()[2])[5].startsWith("Cannot be imported: This picture holds no character card"));
  const tick = (row) => row.querySelector("input");
  assert.deepEqual(rows().map((row) => [tick(row).checked, tick(row).disabled]),
    [[true, false], [true, false], [false, true], [false, true], [true, false]], "a file that is no card cannot be included");
  assert.equal(tick(rows()[1]).getAttribute("aria-label"), "Include Ilse Dorn");
  // a PNG card shows its own picture, from the dropped file; a JSON card its initial
  const pictures = rows().map((row) => row.querySelector("img.import-batch-pic"));
  assert.ok(pictures[0].getAttribute("src").startsWith("blob:") && pictures[1].getAttribute("src").startsWith("blob:"));
  assert.equal(pictures[4], null);
  assert.equal(rows()[4].querySelector("span.g-avatar").textContent, "T");
  assert.equal(main.querySelector("section.import-review"), null, "nothing is proposed by listing");

  // the one button counts the ticked cards, and none ticked is nothing to review
  const go = () => byText(main, "button", "Review ")[0];
  assert.equal(go().textContent, "Review 3 cards");
  const set = (row, value) => { tick(row).checked = value; tick(row).dispatchEvent({ type: "change" }); };
  set(rows()[4], false);
  assert.equal(go().textContent, "Review 2 cards");
  set(rows()[0], false); set(rows()[1], false);
  assert.ok(go().textContent === "Review 0 cards" && go().disabled);
  set(rows()[0], true); set(rows()[1], true);

  // one review: every file of both cards under one digest, grouped by card, and what was left out, with why
  go().click();
  const review = await until(() => main.querySelector("section.import-review"), 30000, "the combined review");
  assert.equal(review.querySelector("h2").textContent, "2 cards in one review");
  assert.equal(review.querySelector("div.g-eyebrow").textContent, "Review before anything is written");
  const groups = review.querySelectorAll("div.import-file-group");
  assert.deepEqual(groups.map((node) => node.querySelector("span.import-file-group-name").textContent), ["Mara Vell", "Ilse Dorn"]);
  assert.ok(groups[0].textContent.includes("cards/mara.png · Updates the card you have · card:mara-vell"));
  assert.ok(groups[1].textContent.includes("cards/more/ilse.png · New · card:ilse-dorn"));
  const files = review.querySelectorAll("button.import-file");
  assert.equal(review.querySelector("div.import-count").textContent, files.length + " files");
  const counted = groups.map((node) => Number(/(\d+) files?$/.exec(node.textContent)[1]));
  assert.equal(counted[0] + counted[1], files.length, "every file is listed under the card it belongs to");
  assert.ok(files.some((node) => node.textContent.includes("sources/card/ilse-dorn/")) && files.some((node) => node.textContent.includes("sources/card/mara-vell/")));
  assert.equal(files.filter((node) => node.getAttribute("aria-pressed") === "true").length, 1);
  const left = review.querySelectorAll("details").find((node) => node.querySelector("summary").textContent === "Left out of this review (3)");
  assert.deepEqual(left.querySelectorAll("li").map((node) => node.textContent.split(":")[0]), ["cards/more/picture.png", "cards/notes.json", "cards/tobin.json"]);
  assert.ok(left.textContent.includes("cards/tobin.json: not ticked") && left.textContent.includes("cannot be imported. This file is not a JSON document."));
  assert.ok(byText(review, "span.import-digest", "Digest sha256:").length);
  assert.ok(!byText(review, "button", "Approve and write").length);

  // approved once, for all of them
  byText(review, "button", "Approve all 2 cards")[0].click();
  let dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
  assert.ok(dialog.textContent.includes("Write " + files.length + " files for 2 cards into this library, exactly as shown?"));
  byText(dialog, "button", "Write ")[0].click();
  await until(() => byText(main, "strong.import-outcome", "Written to your library").length, 30000, "the write");
  assert.deepEqual(review.querySelector("ul.import-written").querySelectorAll("li").map((node) => node.textContent),
    ["Mara Vell: updated as card:mara-vell/character/mara-vell", "Ilse Dorn: added as card:ilse-dorn/character/ilse-dorn"]);

  // several files chosen at once are a batch too; what the library now holds is said and cannot be ticked
  byText(main, "button", "Import another")[0].click();
  const chooser = await until(() => main.querySelector("div.import-drop") && main.querySelector("div.import-drop").querySelector("input"), 15000, "the drop zone again");
  chooser.files = [fileOf("ilse.png", stored("ilse.png")), fileOf("tobin.json", stored("tobin.json"))];
  chooser.dispatchEvent({ type: "change" });
  await until(() => main.querySelector("table.import-batch"), 30000, "the second batch");
  assert.equal(main.querySelector("div.import-batch-said").textContent, "1 new · 1 already in your library");
  assert.deepEqual(rows().map((row) => [cells(row)[5], tick(row).checked, tick(row).disabled]),
    [["Already in your library", false, true], ["Same name as another card", true, false]]);
  assert.equal(go().textContent, "Review 1 card");

  // an approval the library has moved past writes no card and says to preview again
  go().click();
  await until(() => main.querySelector("section.import-review"), 30000, "the second review");
  assert.ok(byText(main, "button", "Approve all 1 card").length);
  const beside = await call("imports.card_preview", { card: Buffer.from(stored("tobin.json")).toString("base64"), origin: "tobin.json" });
  await call("imports.proposal_apply", { proposal: beside.proposal, digest: beside.digest });
  byText(main, "button", "Approve all 1 card")[0].click();
  dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
  byText(dialog, "button", "Write ")[0].click();
  await until(() => byText(main, "strong.import-outcome", "Not written: preview it again").length, 30000, "the stale approval");
  assert.ok(byText(body, "div", "Nothing was written; preview it again.").length);

  // more files than one batch takes are refused before anything is sent
  byText(main, "button", "Import another")[0].click();
  const again = await until(() => main.querySelector("div.import-drop") && main.querySelector("div.import-drop").querySelector("input"), 15000, "the drop zone a third time");
  again.files = Array.from({ length: 51 }, (_, index) => fileOf("card-" + index + ".json", stored("tobin.json")));
  again.dispatchEvent({ type: "change" });
  await until(() => byText(body, "div", "one batch takes at most 50").length, 15000, "the refusal of too many files");
  assert.equal(main.querySelector("table.import-batch"), null);
  assert.ok(main.querySelector("div.import-drop"), "the drop zone stays");
});
