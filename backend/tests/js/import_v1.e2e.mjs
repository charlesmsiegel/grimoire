// The Import page's V1 library form (110) end to end in fakedom, against a real app server: scan a synthetic V1
// library, preview, approve, and see the run's report of what was not carried over after the write; then, from
// a fresh form, read the same report back from the receipt by the archive's name.
// Run by test_app_imports.py, which serves a synthetic library and passes GRIMOIRE_APP, GRIMOIRE_TOKEN,
// GRIMOIRE_V1 (the V1 library folder) and GRIMOIRE_ARCHIVE; "/static/..." imports map onto the package.
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
const archiveName = process.env.GRIMOIRE_ARCHIVE;

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

const labelled = (root, text) => {
  const label = byText(root, "label", text)[0];
  assert.ok(label, "a field labelled " + text);
  return root.querySelector('[id="' + label.getAttribute("for") + '"]');
};

test("a V1 import's report of what it left behind is shown after the write and read back later", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => byText(main, "h2", "Your characters").length, 15000, "Home");
  window.location.hash = "#/import?tab=v1";
  await until(() => byText(main, "button", "Scan library").length, 15000, "the V1 form");

  // asking for a report without an archive name says so at the field, and asks nothing of the server
  byText(main, "button", "Show the last import report")[0].click();
  const archive = labelled(main, "Archive name");
  await until(() => archive.getAttribute("aria-invalid") === "true", 15000, "the missing name");
  assert.equal(document.activeElement, archive, "focus goes to the field that needs the name");

  // before any import, the archive's report says nothing was imported
  type(archive, archiveName);
  byText(main, "button", "Show the last import report")[0].click();
  await until(() => byText(main, "h2", "No import yet").length, 15000, "no import yet");
  assert.equal(archive.getAttribute("aria-invalid"), "false");

  type(labelled(main, "Grimoire 0.2.1 library folder"), process.env.GRIMOIRE_V1);
  byText(main, "button", "Scan library")[0].click();
  await until(() => byText(main, "button", "Preview import").length, 30000, "the scan");
  byText(main, "button", "Preview import")[0].click();
  const review = await until(() => main.querySelector("section.import-review"), 30000, "the preview");
  const warned = byText(review, "summary", "Unresolved").map((node) => node.textContent);
  assert.ok(warned.length, "the preview warns of what it cannot resolve");

  byText(main, "button", "Approve and write")[0].click();
  const dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
  byText(dialog, "button", "Write ")[0].click();
  await until(() => byText(main, "strong.import-outcome", "Written to your library").length, 30000, "the write");

  // after the write, the run's report stays on the page, with every warning the preview gave
  const report = await until(() => byText(main, "h2", "Import report: " + archiveName + ", run 1")[0], 15000, "the report");
  const card = report.parentNode;
  for (const text of warned) assert.ok(byText(card, "summary", text).length, "the report keeps " + text);
  assert.ok(byText(card, "summary", "Not imported").length, "the report keeps what was not imported");
  assert.ok(!main.textContent.includes("[object"), "no value is shown as an object's name");

  // later, from a fresh form, the same report is read back from the receipt
  byText(main, "button", "Import another")[0].click();
  await until(() => byText(main, "button", "Scan library").length && !byText(main, "h2", "Import report:").length,
    15000, "a fresh form");
  type(labelled(main, "Archive name"), archiveName);
  byText(main, "button", "Show the last import report")[0].click();
  const again = await until(() => byText(main, "h2", "Import report: " + archiveName + ", run 1")[0], 15000, "the kept report");
  const kept = again.parentNode;
  for (const text of warned) assert.ok(byText(kept, "summary", text).length, "the kept report still says " + text);
});
