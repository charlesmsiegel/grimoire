// The structural accessibility audit (a11y.mjs) itself, in fakedom: each rule finds what it is for, and the
// shell's own primitives pass. Run by test_app_shell_js.py.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

import { audit } from "./a11y.mjs";
import { install } from "./fakedom.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
install("http://127.0.0.1:1");
const ui = await import(pathToFileURL(path.join(here, "..", "..", "src", "grimoire", "app", "static", "shell", "ui.js")).href);
const { el } = ui;

const rules = (root, options = { page: false }) => audit(root, options).map((found) => found.rule).sort();

test("each rule finds what it is for", () => {
  const cases = [
    [el("input", { type: "text" }), "label"],
    [el("input", { type: "text", placeholder: "Name" }), "label"],
    [el("button", { type: "button" }), "button-name"],
    [el("a", { href: "#/home" }), "link-name"],
    [el("img", { src: "x.png" }), "image-alt"],
    [el("div", { role: "buttn" }, "x"), "aria-roles"],
    [el("button", { type: "button", "aria-pressed": "yes" }, "Bold"), "aria-valid-attr-value"],
    [el("button", { type: "button", "aria-describedby": "nowhere" }, "Go"), "aria-valid-ref"],
    [el("button", { type: "button", tabindex: "2" }, "Go"), "tabindex"],
    [el("label", { for: "nowhere" }, "Name"), "label-for"],
    [el("h2", {}), "empty-heading"],
    [el("div", {}, el("li", {}, "loose")), "listitem"],
    [el("div", { role: "dialog" }, "Untitled"), "aria-dialog-name"],
    [el("div", { "aria-hidden": "true" }, el("button", { type: "button" }, "Hidden but focusable")), "aria-hidden-focus"],
    [el("button", { type: "button" }, el("a", { href: "#/x" }, "inner")), "nested-interactive"],
    [el("div", { onclick: () => {} }, "Tap me"), "click-events-have-key-events"],
    [el("div", { class: "row", onclick: () => {} }, "A row", el("button", { type: "button" }, "Delete")), "click-events-have-key-events"],
    [el("button", { type: "button" }, el("span", { "aria-hidden": "true" }, "×")), "button-name"],
    [el("details", {}, el("summary", {}, "More", el("button", { type: "button" }, "Inside"))), "nested-interactive"],
    [el("div", {}, el("label", { for: "masked", "aria-hidden": "true" }, "Name"), el("input", { id: "masked", type: "text" })), "label"],
    [el("div", { role: "button", onclick: () => {} }, "Tap me"), "focusable-widget"],
    [el("div", {}, el("p", { id: "same" }, "a"), el("p", { id: "same" }, "b")), "duplicate-id"],
  ];
  for (const [node, rule] of cases) {
    const root = el("div", {}, node);
    assert.ok(rules(root).includes(rule), rule + " was not found; found " + rules(root).join(", "));
  }
});

test("hidden subtrees are left out, and named things pass", () => {
  const root = el("div", {},
    el("div", { hidden: true }, el("input", { type: "text" })),
    el("label", {}, el("input", { type: "checkbox" }), "Include"),
    el("button", { type: "button", "aria-label": "Close" }, "×"),
    el("a", { href: "#/home" }, el("img", { src: "x.png", alt: "Home" })),
    el("div", { role: "button", tabindex: "0", onclick: () => {} }, "Tap me"),
    el("img", { src: "x.png", alt: "" }),
    el("button", { type: "button", "aria-describedby": "later-hint" }, "Save"), el("p", { id: "later-hint", hidden: true }, "Saved in place"),
    el("nav", { "aria-label": "Sections", onclick: () => {} }, el("a", { href: "#/a" }, "A")));
  assert.deepEqual(rules(root), []);
});

test("a page needs one main, a top heading, and names on several navs", () => {
  const page = el("div", {}, el("nav", {}, el("a", { href: "#/a" }, "A")), el("nav", {}, el("a", { href: "#/b" }, "B")),
    el("main", {}, el("h2", {}, "Not the top")));
  assert.deepEqual(rules(page, { page: true }), ["landmark-unique", "landmark-unique", "page-has-heading-one"]);
  const fine = el("div", {}, el("nav", { "aria-label": "Main" }), el("main", {}, el("h1", {}, "Home")));
  assert.deepEqual(rules(fine, { page: true }), []);
});

test("the shell's primitives pass the audit", () => {
  const field = ui.field("Name", { hint: "Your name" });
  field.setError("Say a name.");
  const root = el("div", {}, field.node, ui.choice("Kind", [{ value: "a", label: "A" }]).node,
    ui.button("Save", () => {}), ui.link("Home", "home"), ui.search("Search the library").node || ui.search("Search the library"));
  assert.deepEqual(rules(root), []);
});
