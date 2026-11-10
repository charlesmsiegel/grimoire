// Sprint 113's campaign page end to end, in fakedom against a real app server: from the menu to a campaign and
// its chat, the table's and the director's views, and one effect accepted with two keys given in two calls
// (the director proposes and approves, the player endorses exactly what was shown).
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

function section(main, title) {
  return main.querySelectorAll("section").find((node) => node.querySelector("h2") && node.querySelector("h2").textContent === title);
}

test("a campaign's chat, its two views and an effect accepted with two keys", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(process.env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const body = document.body;
  const main = await until(() => body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the menu lists the page; it lists every campaign with a chat, each by its id
  byText(body.querySelector("nav"), "a", "Campaigns")[0].click();
  const campaignHash = "#/campaign/" + encodeURIComponent(campaign);
  const listed = await until(() => main.querySelectorAll("a").find((node) => node.getAttribute("href") === campaignHash), 15000, "the campaigns");
  listed.click();
  const chatHash = campaignHash + "?chat=" + encodeURIComponent(chat);
  const row = await until(() => main.querySelectorAll("a").find((node) => node.getAttribute("href") === chatHash), 15000, "its chats");
  row.click();

  // the table's view: its cast, and neither world truth nor a director's action
  await until(() => section(main, "Cast"), 15000, "the hub");
  assert.ok(byText(section(main, "Cast"), "span", "your character").length, "the seated player's character is theirs");
  assert.equal(section(main, "World truth"), undefined, "world truth is the director's");
  assert.equal(section(main, "Propose an effect"), undefined, "the table proposes nothing");
  assert.ok(byText(section(main, "Not available yet"), "strong", "Revoke effect is not available yet").length);

  // the director proposes an effect for the kept turn, in structured form
  byText(main, "a", "Director")[0].click();
  const propose = await until(() => section(main, "Propose an effect"), 15000, "the director's view");
  assert.ok(section(main, "World truth"));
  const cast = section(main, "Cast").querySelectorAll("span").filter((node) => node.className === "g-row-title").map((node) => node.textContent);
  assert.deepEqual(cast.sort(), ["Anya Rook", "Mara Vell"]);
  byText(propose, "button", "Add an operation").at(-1).click();
  const fields = propose.querySelectorAll("input,select,textarea");
  const pick = (labelText) => {
    const label = propose.querySelectorAll("label").find((node) => node.textContent === labelText);
    return fields.find((node) => node.getAttribute("id") === label.getAttribute("for"));
  };
  const actor = (name) => pick("Subject").querySelectorAll("option").find((node) => node.textContent === name).getAttribute("value");
  pick("What happens").value = "Anya pays Mara a coin.";
  pick("Relation").value = "knows";
  pick("Subject").value = actor("Mara Vell");
  pick("Object").value = actor("Anya Rook");
  pick("Done by").value = actor("Anya Rook");
  byText(propose, "button", "Propose").at(-1).click();
  await idle();
  const effect = await until(() => main.querySelectorAll("div").find((node) => node.className === "campaign-effect"), 15000, "the proposed effect");
  assert.ok(effect.textContent.includes("Anya pays Mara a coin."), effect.textContent);
  assert.ok(effect.textContent.includes("your key not given"));
  assert.ok(!byText(effect, "button", "Endorse").length, "the director's view gives no player's key");

  // the player's key, on exactly what was shown
  byText(main, "a", "Table")[0].click();
  const shown = await until(() => main.querySelectorAll("div").find((node) => node.className === "campaign-effect" && byText(node, "button", "Endorse").length), 15000, "the effect at the table");
  byText(shown, "button", "Endorse as shown")[0].click();
  await answerDialog("Endorse");
  await idle();
  await until(() => byText(main, "p", "your key given").length, 15000, "the endorsement");
  assert.ok(!byText(main, "button", "Approve").length, "the table gives no director's key");

  // the director's key is the last: it accepts the effect, once
  byText(main, "a", "Director")[0].click();
  const ready = await until(() => byText(main, "button", "Approve and accept")[0], 15000, "the director's key");
  ready.click();
  await answerDialog("Approve");
  await idle();
  await until(() => byText(section(main, "World truth"), "span", "Anya pays Mara a coin.").length, 15000, "the accepted event");
  assert.ok(byText(main, "strong", "(accepted)").length);
  assert.ok(!byText(main, "button", "Approve").length, "an accepted effect takes no further key");

  // a perspective is the actor's own, never world truth
  byText(main, "button", "What Mara Vell knows")[0].click();
  await idle();
  await until(() => main.querySelectorAll("h2").some((node) => node.textContent === "Mara Vell's perspective"), 15000, "Mara's perspective");
});
