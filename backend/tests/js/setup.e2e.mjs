// First run (108) end to end in fakedom: the app started with no library opens on Library setup, which the rail
// does not list; the person names this device, checks the suggested folder and makes the library there, and the
// app then serves it. The next steps lead to a first chat: a card is added through the import's reviewed write,
// the character to play is made, and the chat begins. Run by test_app_shell_js.py with GRIMOIRE_APP,
// GRIMOIRE_TOKEN, GRIMOIRE_PLACE (the folder the app suggests) and GRIMOIRE_CARD (a synthetic card's JSON).
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

function type(input, text) {
  input.value = text;
  input.dispatchEvent({ type: "input" });
}

const steps = (main) => main.querySelector("ol.setup-steps").querySelectorAll(".setup-step");
const currentStep = (main) => steps(main).filter((node) => node.getAttribute("aria-current") === "step").map((node) => node.textContent);
const idle = (main) => !main.querySelectorAll("button").some((node) => node.disabled);

test("from no library to an open one, and on to a first chat", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");

  // with no library, the app opens on setup, which the rail does not list; every item of the rail waits on a library
  await until(() => window.location.hash === "#/setup" && main.querySelector("input"), 15000, "Library setup");
  const nav = body.querySelector("nav");
  assert.ok(byText(nav, "a", "Chats")[0].classList.contains("g-blocked"), "Chats waits on a library");
  const items = nav.querySelectorAll("a.g-nav-item");
  assert.ok(items.length && items.every((node) => node.classList.contains("g-blocked")), "every page in the rail waits on a library");
  assert.equal(byText(nav, "a", "Library setup").length, 0, "setup is not in the rail");
  assert.equal(byText(nav, "a", "New chat").length, 0, "nothing starts a chat before a library is open");
  assert.equal(body.querySelector("h1").textContent, "Welcome to Grimoire");
  // the palette still lists setup, and nothing that waits on a library
  byText(body.querySelector("header"), "button", "Search")[0].click();
  const palette = await until(() => body.querySelector("dialog"), 15000, "the palette");
  const offered = palette.querySelectorAll("li").map((node) => node.textContent);
  assert.ok(offered.some((text) => text.startsWith("Library setup")), "the palette lists setup");
  assert.ok(!offered.some((text) => text.startsWith("Chats") || text.startsWith("Home")), "the palette lists no page that waits on a library");
  byText(palette, "li", "Library setup")[0].click();
  await until(() => !body.querySelector("dialog") && main.querySelector("input"), 15000, "setup from the palette");
  assert.equal(window.location.hash, "#/setup");

  // the four steps: only the first can be worked on before a library is open
  assert.deepEqual(steps(main).map((node) => node.textContent), ["1Library folder", "2First card", "3Who you play", "4Begin"]);
  assert.deepEqual(currentStep(main), ["1Library folder"]);
  assert.equal(main.querySelector("ol.setup-steps").querySelectorAll("a").length, 0, "no step is a link yet");
  assert.ok(byText(main, "h2", "Where should your library live?").length);
  const [folder, device] = main.querySelectorAll("input");
  assert.equal(folder.value, process.env.GRIMOIRE_PLACE, "the folder the app was started on is offered");
  assert.equal(byText(main, "button", "Make the library here").length, 0, "nothing is made before the check");
  assert.ok(byText(main, "span", "Check the folder first; nothing is written until you say so.").length);

  // a relative path is refused with its reason, tied to the field
  type(folder, "relative/folder");
  byText(main, "button", "Check").at(-1).click();
  await until(() => folder.getAttribute("aria-invalid") === "true", 15000, "the refusal");
  assert.ok(byText(main, "div", "bad_path").length, "the refusal says its reason");
  assert.equal(byText(main, "button", "Make the library here").length, 0);
  // a check whose folder was edited while it was out is dropped: no action is offered for a folder not typed
  type(folder, process.env.GRIMOIRE_PLACE);
  byText(main, "button", "Check").at(-1).click();
  type(folder, process.env.GRIMOIRE_PLACE + "-other");
  await new Promise((resolve) => setTimeout(resolve, 1000));
  assert.equal(byText(main, "button", "Make the library here").length, 0, "the stale answer is dropped");
  type(folder, process.env.GRIMOIRE_PLACE);
  byText(main, "button", "Check").at(-1).click();
  const make = await until(() => byText(main, "button", "Make the library here").at(-1), 15000, "the check");
  assert.ok(byText(main, "div", "new library").length, "the check says what it found");
  make.click();
  await until(() => byText(main, "div", "Name this device first").length, 15000, "the device is asked for");
  type(device, "desk");
  byText(main, "button", "Make the library here").at(-1).click();

  // the library is open, and setup goes on to a first card
  await until(() => window.location.hash === "#/setup?step=card" && byText(main, "h2", "Who do you want to meet first?").length, 15000, "the first card step");
  assert.deepEqual(currentStep(main), ["2First card"]);
  assert.equal(main.querySelector("ol.setup-steps").querySelectorAll("a").length, 4, "every step is a link now");
  assert.equal(window.sessionStorage.getItem("grimoire-draft:setup:device"), null, "the typed fields are cleared");
  assert.equal(main.querySelectorAll("button.setup-pick").length, 0, "a new library has no card yet");
  // a premise is the other way in
  const premise = byText(main, "button", "No card yet: start from a one-line premise instead")[0];
  assert.equal(premise.getAttribute("aria-expanded"), "false");
  premise.click();
  assert.equal(premise.getAttribute("aria-expanded"), "true");
  byText(main, "button", "Continue with this premise")[0].click();
  assert.ok(byText(main, "div", "Write a premise first.").length && window.location.hash === "#/setup?step=card");

  // a file that is no card is refused, and nothing is offered to write
  let file = main.querySelector('input[type="file"]');
  assert.equal(file.getAttribute("accept"), ".png,image/png,.json,application/json", "a PNG card or a card JSON");
  file.files = [new File(["not a card"], "notes.json")];
  file.dispatchEvent({ type: "change" });
  await until(() => body.querySelectorAll("div.g-bad").length && idle(main), 15000, "the refusal of a file that is no card");
  assert.equal(byText(main, "button", "Add this card").length, 0);
  // a PNG card goes through the same preview: its picture is one of the files it would write. Rejected, nothing is
  file.files = [new File([Buffer.from(process.env.GRIMOIRE_CARD_PNG, "base64")], "mara.png")];
  file.dispatchEvent({ type: "change" });
  await until(() => byText(main, "button", "Add this card: write").at(-1), 15000, "the PNG card's preview");
  assert.ok(byText(main, "h3", "mara.png").length);
  assert.ok(byText(main, "details.setup-file", "assets/blobs/").length, "the PNG itself is the portrait it would write");
  byText(main, "button", "Reject")[0].click();
  file = await until(() => !byText(main, "button", "Add this card").length && main.querySelector('input[type="file"]'), 15000, "the drop zone again");
  // the card is previewed: every file it would write, and nothing written until it is approved
  file.files = [new File([process.env.GRIMOIRE_CARD], "mara.json")];
  file.dispatchEvent({ type: "change" });
  const add = await until(() => byText(main, "button", "Add this card: write").at(-1), 15000, "the card's preview");
  assert.ok(byText(main, "h3", "mara.json").length && byText(main, "p", "Digest sha256:").length);
  assert.ok(main.querySelectorAll("details.setup-file").length > 0, "each file it would write is listed");
  assert.ok(byText(main, "button", "Reject").length);
  add.click();

  // who you play: a name is enough, and it is asked for
  await until(() => window.location.hash === "#/setup?step=persona" && byText(main, "h2", "And who are you in this story?").length, 15000, "the persona step");
  assert.deepEqual(currentStep(main), ["3Who you play"]);
  assert.equal(main.querySelectorAll("button.g-chip").length, 0, "the card to meet is not offered as yourself");
  byText(main, "button", "Continue").at(-1).click();
  await until(() => byText(main, "div", "Name who you play first.").length, 15000, "the name is asked for");
  type(main.querySelector("input"), "Wren Ashby");
  type(main.querySelector("textarea"), "A courier new to the harbor.");
  byText(main, "button", "Continue").at(-1).click();

  // begin: who meets whom, the card's openings with the default chosen, and that no model will answer yet
  await until(() => window.location.hash === "#/setup?step=begin" && byText(main, "h2", "Wren Ashby meets Mara Vell").length, 15000, "the last step");
  assert.deepEqual(currentStep(main), ["4Begin"]);
  const openings = await until(() => main.querySelectorAll("button.setup-opening").length && main.querySelectorAll("button.setup-opening"), 15000, "the openings");
  assert.equal(openings.filter((node) => node.getAttribute("aria-pressed") === "true").length, 1, "the default opening is chosen");
  assert.ok(byText(main, "div", "No model is set up yet").length);
  // the picks are still there when the person goes back a step
  byText(main.querySelector("ol.setup-steps"), "a", "Who you play")[0].click();
  const chip = await until(() => main.querySelectorAll("button.g-chip")[0], 15000, "the persona step again");
  assert.ok(chip.textContent.includes("Wren Ashby") && chip.getAttribute("aria-pressed") === "true");
  chip.click();
  await until(() => main.querySelectorAll("button.setup-opening").length, 15000, "the last step again");
  byText(main, "button", "Begin").at(-1).click();
  await until(() => window.location.hash.startsWith("#/chat/"), 15000, "the first chat");
  await until(() => main.querySelectorAll("div.chat-line").some((node) => node.textContent.includes("You came back. I didn't think you would.")), 15000, "the card's greeting");

  // setup still says which library is open, and the pages that waited on it are usable
  window.location.hash = "#/setup";
  await until(() => byText(main, "h2", "This library").length, 15000, "the open library");
  assert.equal(body.querySelector("h1").textContent, "Library setup");
  assert.ok(byText(main, "dd", "desk").length);
  assert.ok(byText(main, "dd", process.env.GRIMOIRE_PLACE).length && byText(main, "code", "python -m grimoire.app serve").length);
  assert.ok(byText(main, "h2", "Or go straight to").length && byText(main, "a", "Next: a first card").length);
  // the first chat is done, so the last step asks for its picks again rather than starting a second one
  window.location.hash = "#/setup?step=begin";
  await until(() => byText(main, "a", "First card").length > 1, 15000, "the last step with nothing picked");
  assert.equal(byText(main, "button", "Begin").length, 0);
  const chats = await until(() => byText(body.querySelector("nav"), "a", "Chats")[0], 15000, "the menu again");
  assert.ok(!chats.classList.contains("g-blocked"));
  assert.equal(byText(body.querySelector("nav"), "a", "Library setup").length, 0, "setup stays out of the rail");
  chats.click();
  await until(() => main.querySelectorAll("a").some((node) => node.getAttribute("href").startsWith("#/chat/")), 15000, "the first chat in the list");
});
