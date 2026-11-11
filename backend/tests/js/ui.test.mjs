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

test("a segmented control has one choice pressed and tells the page only when it changes", () => {
  const heard = [];
  const control = ui.segments("Sort", [{ value: "recent", label: "Recent" }, { value: "name", label: "A–Z" }], { onchange: (value) => heard.push(value) });
  const [recent, name] = control.node.querySelectorAll("button");
  assert.equal(control.node.getAttribute("aria-label"), "Sort");
  assert.equal(recent.getAttribute("aria-pressed"), "true");
  name.click();
  name.click();  // already chosen: nothing more to say
  assert.deepEqual(heard, ["name"]);
  assert.equal(control.value, "name");
  assert.equal(recent.getAttribute("aria-pressed"), "false");
  control.set("recent");  // set by the page: no echo back to it
  assert.deepEqual(heard, ["name"]);
  assert.equal(recent.getAttribute("aria-pressed"), "true");
});

test("a placeholder picture keeps one colour per key and shows the initial as text", async () => {
  assert.equal(ui.hue("card:mara"), ui.hue("card:mara"));
  assert.match(ui.hue("card:mara"), /^g-hue-(\d|1[01])$/);
  assert.ok(new Set(["a", "b", "c", "d", "e", "f", "g", "h"].map((key) => ui.hue(key))).size > 3, "keys spread over the colours");
  const plain = ui.avatar("<b>mara</b>", { size: 22 });
  assert.equal(plain.textContent, "<");
  assert.ok(plain.classList.contains("g-size-22"));
  assert.equal(ui.avatar("").textContent, "?");
  const pictured = ui.avatar("Mara", { picture: Promise.resolve("blob:x") });
  const failed = ui.portrait("Oren", { picture: Promise.reject(new Error("gone")) });
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(pictured.querySelector("img").getAttribute("src"), "blob:x");
  assert.equal(failed.textContent, "O", "a picture that cannot load leaves the placeholder");
});

test("a modal left with its page closes once and runs its onclose", () => {
  let closed = 0;
  const modal = ui.modal("Choose a picture", { onclose: () => { closed += 1; } });
  assert.ok(modal.dialog.open);
  ui.closeConfirmations();
  modal.close();
  assert.equal(closed, 1);
  assert.equal(document.body.querySelector("dialog"), null);
});
