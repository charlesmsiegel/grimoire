// The Import page (110) end to end in fakedom, against a real app server: open Import from Home, review a pasted
// change set, see every file it would write before anything is written, approve it through the confirmation,
// and see it written; a second approval of the same proposal is refused and says so.
// Run by test_app_imports.py, which serves a synthetic library and passes GRIMOIRE_APP, GRIMOIRE_TOKEN and
// GRIMOIRE_CHANGESET (a file holding the change set document to paste); "/static/..." imports map onto the package.
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

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

test("a pasted change set is reviewed, approved and written", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => byText(main, "h2", "What next").length, 15000, "Home");

  // Home links Import now that this page is registered
  const importLink = byText(main, "a", "Import")[0];
  assert.ok(importLink, "Import is a link on Home");
  importLink.click();
  await until(() => byText(main, "a", "Change set").length, 15000, "the Import page");
  assert.equal(window.location.hash, "#/import");
  byText(main, "a", "Change set")[0].click();
  const field = await until(() => window.location.hash === "#/import?tab=changeset" && main.querySelector("textarea"), 15000, "the change set form");

  // review: every file is listed with its hash, and nothing is written yet
  type(field, readFileSync(process.env.GRIMOIRE_CHANGESET, "utf8"));
  byText(main, "button", "Review change set")[0].click();
  await until(() => byText(main, "h2", "Review before anything is written").length, 30000, "the review");
  assert.ok(byText(main, "p", "Digest sha256:").length, "the digest is shown");
  const applicable = main.querySelectorAll("dt").find((node) => node.textContent === "Can be applied");
  assert.ok(applicable, "the report's facts are shown as labelled values");
  assert.ok(!main.textContent.includes("[object"), "no value is shown as an object's name");
  const files = main.querySelectorAll("details").filter((node) => node.className.includes("import-file"));
  assert.ok(files.length > 0, "each file it would write is listed");

  // approve: the confirmation names the count; cancelling writes nothing, confirming writes
  byText(main, "button", "Approve and write")[0].click();
  let dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation");
  assert.ok(dialog.textContent.includes("exactly as shown"));
  byText(dialog, "button", "Cancel")[0].click();
  const approveAgain = await until(() => !body.querySelector("dialog")
    && byText(main, "button", "Approve and write").find((node) => !node.disabled), 15000, "the confirmation closed");
  assert.ok(byText(main, "h2", "Review before anything is written").length, "still only a review");
  approveAgain.click();
  dialog = await until(() => body.querySelector("dialog"), 15000, "the confirmation again");
  byText(dialog, "button", "Write ")[0].click();
  await until(() => byText(main, "h2", "Written").length, 30000, "the write");
  assert.ok(!byText(main, "button", "Approve and write").length, "a written proposal offers no second approval");
});
