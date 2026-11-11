// Sprint 113's campaign page end to end, in fakedom against a real app server: from the menu to the list, a campaign and
// its chat, the table's and the director's views, and one effect accepted with two keys given in two calls
// (the director proposes and approves; the player endorses exactly what was shown, in the inbox the table's
// view links to).
// Run by test_campaign_page.py, which serves a synthetic library whose card chat has a kept turn by the seated
// player, and passes GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT (that chat) and GRIMOIRE_CAMPAIGN (its campaign).
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
const chat = process.env.GRIMOIRE_CHAT;
const campaign = process.env.GRIMOIRE_CAMPAIGN;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");

async function answerDialog(label) {
  const dialog = await until(() => document.body.querySelector("dialog"), 15000, "the confirmation");
  byText(dialog, "button", label).at(-1).click();
}

// a card of the hub, by its name
function section(main, title) {
  return main.querySelector('section[aria-label="' + title + '"]') || undefined;
}

const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));

test("a campaign's chat, its two views and an effect accepted with two keys", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the menu lists the page; it lists every campaign with a chat, each a row that opens its hub, and counts them
  byText(body.querySelector("nav"), "a", "Campaigns")[0].click();
  const campaignHash = "#/campaign/" + encodeURIComponent(campaign);
  const listed = await until(() => main.querySelectorAll("a.campaign-rowlink").find((node) => node.getAttribute("href") === campaignHash), 15000, "the campaigns");
  assert.equal(main.querySelectorAll("a.campaign-rowlink").length, 2, "the card chat's campaign and the imported one");
  await until(() => byText(body.querySelector("nav"), "a", "Campaigns")[0].textContent.includes("2"), 15000, "the count in the rail");
  const sortable = main.querySelectorAll("th").filter((node) => node.hasAttribute("aria-sort"));
  assert.ok(sortable.some((node) => node.getAttribute("aria-sort") === "descending"), "the table says how it is sorted");
  listed.click();

  // the campaign opens on the hub of the chat played last, named among its chats
  const chatHash = campaignHash + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  const chip = main.querySelectorAll("a.campaign-chip").find((node) => node.getAttribute("href") === chatHash);
  assert.equal(chip.getAttribute("aria-current"), "page");
  assert.ok(main.querySelector("div.g-cover").textContent.includes("1 chat"));

  // the table's view: its cast, and neither world truth nor a director's action
  assert.equal(view(main, "Table view").getAttribute("aria-pressed"), "true");
  assert.ok(byText(section(main, "Cast and stage"), "span", "your character").length, "the seated player's character is theirs");
  assert.equal(section(main, "World truth"), undefined, "world truth is the director's");
  assert.equal(section(main, "NPC goals"), undefined, "and so are NPC goals");
  assert.equal(section(main, "Propose an effect"), undefined, "the table proposes nothing");
  assert.ok(byText(section(main, "Not available yet"), "strong", "Revoke effect is not available yet").length);

  // the director proposes an effect for the kept turn, in structured form
  view(main, "Director view").click();
  const propose = await until(() => section(main, "Propose an effect"), 15000, "the director's view");
  assert.equal(view(main, "Director view").getAttribute("aria-pressed"), "true");
  assert.ok(section(main, "World truth") && section(main, "NPC goals"));
  const cast = section(main, "Cast and stage").querySelectorAll("span.campaign-name").map((node) => node.textContent);
  assert.deepEqual(cast.sort(), ["Anya Rook", "Mara Vell"]);
  byText(propose, "button", "Add an operation").at(-1).click();
  const fields = propose.querySelectorAll("input,select,textarea");
  const pick = (labelText) => {
    const label = propose.querySelectorAll("label").find((node) => node.textContent === labelText);
    return fields.find((node) => node.getAttribute("id") === label.getAttribute("for"));
  };
  const actor = (name) => pick("Subject").querySelectorAll("option").find((node) => node.textContent === name).getAttribute("value");
  pick("What happens").value = "Anya pays Mara a coin.";
  pick("What happens").dispatchEvent({ type: "input" });
  pick("Relation").value = "knows";
  pick("Subject").value = actor("Mara Vell");
  pick("Object").value = actor("Anya Rook");
  pick("Done by").value = actor("Anya Rook");
  byText(propose, "button", "Propose").at(-1).click();
  await idle();
  const waiting = () => section(main, "Changes waiting on approval");
  const effect = await until(() => waiting() && waiting().querySelector("div.campaign-effect"), 15000, "the proposed effect");
  assert.ok(effect.textContent.includes("Anya pays Mara a coin."), effect.textContent);
  assert.ok(effect.textContent.includes("Your key: not given"));
  assert.ok(!byText(effect, "button", "Endorse").length, "the director's view gives no player's key");

  // the player's key, on exactly what was shown: the table's view shows the effect and links to the inbox,
  // where it is decided beside everything else that waits on the player
  view(main, "Table view").click();
  const shown = await until(() => waiting() && waiting().querySelectorAll("div.campaign-effect").find((node) => byText(node, "a", "Decide in Needs you").length), 15000, "the effect at the table");
  assert.ok(shown.textContent.includes("Your key: not given"));
  assert.ok(!byText(main, "button", "Endorse").length && !byText(main, "button", "Approve").length, "the table's view decides nothing in place");
  byText(shown, "a", "Decide in Needs you")[0].click();
  const item = await until(() => main.querySelector("section.inbox-open"), 15000, "the item in the inbox");
  assert.ok(window.location.hash.startsWith("#/inbox?item=effect"));
  assert.equal(item.querySelector("h2").textContent, "Anya pays Mara a coin.");
  assert.ok(item.textContent.includes("begin knows: Mara Vell → Anya Rook (by Anya Rook)"), "shown in full before the key is given");
  byText(item, "button", "Endorse as shown")[0].click();
  await answerDialog("Endorse");
  await until(() => byText(main, "span", "Your key is given").length, 15000, "the endorsement");
  await idle();
  await until(() => main.querySelector("section.inbox-empty"), 15000, "nothing else waiting");
  window.location.hash = chatHash;
  await until(() => waiting() && byText(main, "span", "Your key: given").length, 15000, "the hub after the endorsement");
  assert.ok(!byText(main, "button", "Approve").length, "the table gives no director's key");
  assert.ok(!byText(main, "a", "Decide in Needs you").length, "and nothing waits on the table now");

  // the director's key is the last: it accepts the effect, once
  view(main, "Director view").click();
  const ready = await until(() => byText(main, "button", "Approve and accept")[0], 15000, "the director's key");
  ready.click();
  await answerDialog("Approve");
  await idle();
  await until(() => byText(section(main, "World truth"), "span", "Anya pays Mara a coin.").length, 15000, "the accepted event");
  assert.ok(byText(section(main, "Turns and effects"), "strong", "(accepted)").length);
  assert.ok(!byText(main, "button", "Approve").length, "an accepted effect takes no further key");
  assert.equal(waiting().querySelector("div.campaign-effect"), null, "and waits on nothing");

  // a perspective is the actor's own, never world truth
  const knowledge = section(main, "Knowledge");
  assert.ok(knowledge.textContent.includes("None of it is world truth"));
  view(knowledge, "Mara Vell").click();
  await until(() => section(main, "Knowledge").querySelectorAll("h3").some((node) => node.textContent === "Mara Vell's perspective"), 15000, "Mara's perspective");
});
