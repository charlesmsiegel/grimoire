// Sprint 116's copy of a chat into another campaign (084) end to end, in fakedom against a real app server: the
// chat's Details link the director to it; the table's view shows nothing of it; the director picks the target, sees
// the alternate Mara only as a namesake (never offered), decides Mara new and Anya reused, plans the copy (the
// whisper is omitted as private, nothing is written), and copies it as reviewed; the new chat opens with the copied
// history. An answer to a plan asked before a decision changed is never drawn. Run by test_app_transfer.py, which
// serves the played card chat and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT and GRIMOIRE_TARGET, and
// checks afterwards that the origin's files are unchanged.
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
const chat = env.GRIMOIRE_CHAT;
const target = env.GRIMOIRE_TARGET;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (root, label) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === label);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const exact = (root, tag, text) => root.querySelectorAll(tag).find((node) => node.textContent === text);

// the control of the field whose label starts with `text` under `root`
function control(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent.startsWith(text));
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function pick(root, label, value) {
  const select = control(root, label);
  select.value = value;
  select.dispatchEvent({ type: "change" });
}

const options = (select) => select.querySelectorAll("option").map((node) => ({ value: node.getAttribute("value"), text: node.textContent }));

function holdBack(prefix) {
  const fetchNow = globalThis.fetch;
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  globalThis.fetch = async (url, init) => { if (String(url).startsWith(prefix)) await held; return fetchNow(url, init); };
  return () => { globalThis.fetch = fetchNow; release(); };
}

test("the director copies this chat into another campaign as reviewed", { skip: !env.GRIMOIRE_APP }, async () => {
  const window = install(env.GRIMOIRE_APP);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the chat's Details link the director to the copy, naming no audience
  window.location.hash = "#/chat/" + encodeURIComponent(chat);
  const details = await until(() => exact(main, "button", "Details"), 15000, "the chat");
  details.click();
  const link = await until(() => byText(main, "a", "Copy into another campaign")[0], 15000, "the director's link");
  assert.equal(link.getAttribute("href"), "#/transfer/" + encodeURIComponent(chat));

  // the table's view shows nothing of it
  window.location.hash = link.getAttribute("href");
  const tableView = await until(() => section(main, "Copy into another campaign"), 15000, "the table's view");
  assert.ok(tableView.textContent.includes("it is the director's") || tableView.textContent.includes("so it is the director's"));
  assert.equal(tableView.querySelectorAll("button,select").length, 0);
  assert.ok(!section(main, "Copy into"));

  view(main, "Director view").click();
  const into = await until(() => section(main, "Copy into"), 15000, "the director's view");
  const choices = options(control(into, "Copy into")).map((item) => item.value);
  assert.deepEqual(choices, ["", target], "only another campaign is offered");
  assert.ok(byText(main, "strong", "Copying state and attached assets is not available yet").length);
  assert.ok(byText(main, "strong", "Mapping the copy onto the target's chronology is not available yet").length);
  assert.ok(byText(main, "strong", "Presenting missing art is not available yet").length);
  pick(into, "Copy into", target);

  const scan = await until(() => { const node = section(main, "What would be copied"); return node && group(node, "Mara Vell") ? node : null; }, 15000, "the scan");
  const mara = group(scan, "Mara Vell"), anya = group(scan, "Anya Rook");
  const maraChoices = options(control(mara, "Mara Vell"));
  assert.deepEqual(maraChoices.map((item) => item.value), ["", "new", "leave"], "the namesake is never offered");
  assert.ok(mara.textContent.includes("Shares the name, another individual (never offered): Mara Vell."));
  const anyaChoices = options(control(anya, "Anya Rook"));
  const reuse = anyaChoices.find((item) => item.text.startsWith("Reuse Anya Rook"));
  assert.ok(reuse && reuse.value.startsWith("campaign:" + target + "/"), "the target's own Anya may be reused");
  assert.ok(scan.textContent.includes("3 played turns"));

  // nothing is planned until every actor is decided
  byText(scan, "button", "Plan the copy")[0].click();
  await until(() => scan.querySelectorAll("div").some((node) => node.getAttribute("role") === "alert" && node.textContent === "Decide what happens to Mara Vell."), 15000, "the missing decision");
  pick(mara, "Mara Vell", "new");
  pick(anya, "Anya Rook", reuse.value);

  // an answer to a plan asked before a decision changed is never drawn
  const release = holdBack("/api/transfer.plan");
  byText(scan, "button", "Plan the copy")[0].click();
  pick(anya, "Anya Rook", "leave");
  release();
  await idle();
  await new Promise((resolve) => setTimeout(resolve, 100));
  assert.ok(!section(main, "Review the copy"), "the plan of the decisions as they were is not shown");
  pick(anya, "Anya Rook", reuse.value);

  byText(scan, "button", "Plan the copy")[0].click();
  const reviewed = await until(() => section(main, "Review the copy"), 15000, "the review");
  const text = reviewed.textContent;
  assert.ok(text.includes("Not copied yet") && text.includes("Nothing is written until you copy it as reviewed."));
  assert.ok(text.includes("Mara Vell · created new in the target as Mara Vell"));
  assert.ok(text.includes("Anya Rook · reused as Anya Rook, already in the target"));
  assert.ok(text.includes("You came back. I didn't think you would.") && text.includes("Ledgers drown"));
  assert.ok(text.includes("private (a whisper or a withheld action), never copied"));
  assert.ok(text.includes("Copied: 3 posts on 1 page"));
  assert.ok(byText(reviewed, "summary", "The 3 files it would write").length);
  assert.equal(document.activeElement, reviewed.querySelector(".transfer-focus"), "focus moves to the review");

  byText(reviewed, "button", "Copy as reviewed")[0].click();
  const copied = await until(() => section(main, "Copied"), 15000, "the copy");
  assert.ok(!section(main, "Review the copy"));
  const open = byText(copied, "a", "Open the copy")[0];
  assert.ok(open && open.getAttribute("href").startsWith("#/chat/" + encodeURIComponent("campaign:" + target + "#chat/t-copy-")));
  assert.equal(document.activeElement, open, "focus moves to the new chat's link");

  // the new chat opens with the copied history
  window.location.hash = open.getAttribute("href");
  await until(() => main.textContent.includes("Imported history") && main.textContent.includes("Ledgers drown"), 15000, "the copied chat");
  assert.ok(!main.textContent.includes("third crate"), "the whisper was never copied");
});
