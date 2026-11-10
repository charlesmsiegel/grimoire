// The design primitives (108) that hold state of their own, in fakedom. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
install("http://127.0.0.1:1");
const ui = await import(path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell", "ui.js"));

test("a confirmation still open when its page is left is answered no and goes", async () => {
  const asked = ui.confirm("Delete Mara's ledger?", { confirmLabel: "Delete", danger: true });
  const dialog = document.body.querySelector("dialog");
  assert.ok(dialog && dialog.open);
  ui.closeConfirmations();
  assert.equal(await asked, false);
  assert.equal(document.body.querySelector("dialog"), null);
  // answered once: a late tap on the detached dialog changes nothing
  const again = ui.confirm("Keep it?");
  const yes = document.body.querySelector("dialog").querySelectorAll("button").at(-1);
  yes.click();
  assert.equal(await again, true);
  ui.closeConfirmations();
  yes.click();
});

test("an inline style is refused, not silently dropped", () => {
  assert.throws(() => ui.el("div", { style: "color: red" }), /inline styles/);
  assert.equal(ui.el("p", { class: "g-muted", text: "<b>kept as text</b>" }).textContent, "<b>kept as text</b>");
});
