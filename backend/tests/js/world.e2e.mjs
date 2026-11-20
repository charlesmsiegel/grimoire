// Sprint 115's World page end to end, in fakedom against a real app server: from a campaign's hub, in the
// director's view, to its world; the table's view says only that the page is the director's; the director finds
// both places of one name, asks a route between exact places, blocks a road in this campaign (reviewed before it
// is made) and finds the way round with a ticket; traces a faithful telling and a distorted letter that took three
// weeks, and admits the NPC's belief in it; previews a leader's choice with its dissent kept and nothing saved; and
// writes an NPC's mistaken theory of another's mind, sees the bluff it proposes, then the open question its revision
// does, with the truth unchanged. Run by test_app_world.py, which serves a synthetic library and passes
// GRIMOIRE_APP, GRIMOIRE_TOKEN, GRIMOIRE_CHAT, GRIMOIRE_CAMPAIGN, GRIMOIRE_NPC, GRIMOIRE_PC, GRIMOIRE_TOWN,
// GRIMOIRE_OTHER and GRIMOIRE_PORT.
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
const base = env.GRIMOIRE_APP;
const { GRIMOIRE_CHAT: chat, GRIMOIRE_CAMPAIGN: campaign, GRIMOIRE_PC: pc, GRIMOIRE_TOWN: town, GRIMOIRE_OTHER: other, GRIMOIRE_PORT: port } = env;

const idle = () => until(() => !document.body.querySelectorAll("button").some((node) => node.disabled), 30000, "the action to finish");
const section = (main, title) => main.querySelector('section[aria-label="' + title + '"]') || undefined;
const group = (root, title) => root.querySelectorAll("div").find((node) => node.getAttribute("aria-label") === title);
const view = (main, label) => byText(main, "button", label).find((node) => node.classList.contains("g-segment"));
const press = (root, label) => {
  const found = root.querySelectorAll("button").filter((node) => node.textContent === label || node.getAttribute("aria-label") === label);
  assert.ok(found.length, "a button " + label);
  found.at(-1).click();
};

function input(root, text) {
  const label = root.querySelectorAll("label").find((node) => node.textContent === text);
  assert.ok(label, "a field labelled " + text);
  return root.querySelectorAll("input,select,textarea").find((node) => node.getAttribute("id") === label.getAttribute("for"));
}

function type(root, label, value) {
  const node = input(root, label);
  node.value = value;
  node.dispatchEvent({ type: "input" });
}

function tick(root, label, on = true) {
  const box = root.querySelectorAll("label").find((node) => node.textContent === label).querySelector("input");
  box.checked = on;
  box.dispatchEvent({ type: "change" });
}

async function review(main, card, said) {
  const box = await until(() => group(card, "Review this change"), 15000, "the review");
  assert.ok(box.textContent.includes(said), "the review says " + said + ": " + box.textContent);
  assert.match(box.textContent, /It writes \d+ files?: campaigns\//);
  press(box, "Make this change");
  await until(() => !group(section(main, card.getAttribute("aria-label")), "Review this change"), 15000, "the change to be made");
  await idle();
}

test("the director's world: places, rumors, a faction's choice and an NPC's mind", { skip: !base }, async () => {
  const window = install(base);
  window.location.hash = "#token=" + encodeURIComponent(env.GRIMOIRE_TOKEN);
  await import("/static/shell/shell.js");
  const main = await until(() => document.body.querySelector("main"), 15000, "the frame");
  await until(() => window.location.hash === "#/home", 15000, "Home");

  // the table's view of the page says only that it is the director's; the hub's table view does not link it
  window.location.hash = "#/world/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  const shut = await until(() => section(main, "World"), 15000, "the table's view");
  assert.equal(view(main, "Table view").getAttribute("aria-pressed"), "true");
  assert.ok(shut.textContent.includes("The World page is the director's"));
  assert.equal(main.querySelectorAll("section").length, 1, "nothing of the world is shown to the table");
  assert.ok(!main.textContent.includes("Saltmarch") && !main.textContent.includes("Bryn"));
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat);
  await until(() => section(main, "Cast and stage"), 15000, "the hub");
  assert.ok(!byText(main, "a", "World").some((node) => node.getAttribute("href").startsWith("#/world/")), "the table's hub has no World link");

  // the director's hub links to the world, for the same chat
  window.location.hash = "#/campaign/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  const link = await until(() => byText(main, "a", "World").find((node) => node.getAttribute("href").startsWith("#/world/")), 15000, "the World link");
  assert.ok(link.getAttribute("href").includes("audience=director"));
  link.click();
  let places = await until(() => { const found = section(main, "Places and routes"); return found && found.textContent.includes("Roads") && found; }, 15000, "the places");
  assert.equal(view(main, "Director view").getAttribute("aria-pressed"), "true");
  assert.ok(byText(places, "strong", "Weighted routes, hazards and travel time is not available yet").length);
  assert.ok(byText(places, "strong", "A map of the places is not available yet").length);
  assert.ok(places.textContent.includes("Saltmarch (" + town + ")") && places.textContent.includes("Saltmarch (" + other + ")"), "two places of one name, told apart");

  // a name finds both places, each by its exact ref
  type(places, "Place name", "Saltmarch");
  press(places, "Find");
  await idle();
  places = section(main, "Places and routes");
  const finds = group(places, "Find a place");
  assert.ok(finds.textContent.includes(town) && finds.textContent.includes(other), "both are listed, neither picked");

  // a route between exact places; then the bridge is blocked in this campaign and the way round needs a ticket
  const ask = async (conditions) => {
    places = section(main, "Places and routes");
    const form = group(places, "Find a route");
    input(form, "From").value = town;
    input(form, "To").value = port;
    type(form, "Conditions held", conditions);
    press(form, "Find a route");
    await idle();
    return until(() => section(main, "Places and routes").querySelectorAll("p").find((node) => node.classList.contains("world-route") || node.textContent.startsWith("Route")), 15000, "the route");
  };
  let route = await ask("");
  assert.equal(route.textContent, "Route with no condition held: Saltmarch (" + town + "), then North Gate, then Port Vell.");
  press(section(main, "Places and routes"), "Block bridge");
  await idle();
  await review(main, section(main, "Places and routes"), "Block the road \"bridge\"");
  places = section(main, "Places and routes");
  const bridge = places.querySelectorAll("li").find((node) => node.textContent.startsWith("bridge"));
  assert.ok(bridge.textContent.includes("Blocked") && bridge.textContent.includes("Changed in this campaign"));
  route = await ask("");
  assert.match(route.textContent, /^No route from Saltmarch .* to Port Vell with no condition held/);
  route = await ask("ticket");
  assert.match(route.textContent, /^Route with ticket: Saltmarch .*, then Greywood, then Port Vell\.$/);

  // rumors: the faithful telling, the distorted letter on the calendar, the one still on the road
  const rumors = section(main, "Rumors");
  const letter = await until(() => rumors.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Rumor: letter"), 15000, "the letter");
  assert.ok(letter.textContent.includes("Bryn told Mara Vell") && letter.textContent.includes("Distorted: exaggerated"));
  assert.ok(letter.textContent.includes("sent 1203-03-01 17:00, arriving 1203-03-22 17:00") && letter.textContent.includes("Carried by a channel, attention"));
  const evening = rumors.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Rumor: evening");
  assert.ok(evening.textContent.includes("Oswin told Bryn") && evening.textContent.includes("Faithful"));
  const gossip = rumors.querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Rumor: gossip");
  assert.ok(gossip.textContent.includes("On the way") && gossip.textContent.includes("once it arrives"));
  assert.ok(byText(rumors, "strong", "Sending or delivering a rumor from this page is not available yet").length);
  assert.ok(byText(rumors, "strong", "Spreading a rumor through a faction or public media is not available yet").length);
  input(letter, "What Mara Vell makes of it").value = "suspected";
  input(letter, "What Mara Vell makes of it").dispatchEvent({ type: "change" });
  press(letter, "Review");
  await idle();
  await review(main, section(main, "Rumors"), "Admit that Mara Vell suspects it");
  const admitted = section(main, "Rumors").querySelectorAll("article").find((node) => node.getAttribute("aria-label") === "Rumor: letter");
  assert.ok(admitted.textContent.includes("Mara Vell's belief was admitted: suspected."));

  // a faction's decision: the leader refuses what the leader itself would prefer; nothing is saved
  const faction = section(main, "Faction decision");
  type(faction, "Group", "House Vell");
  type(faction, "Goals", "Keep the Vell name feared\nHold the quay");
  type(faction, "How it decides", "The head of the house decides");
  type(faction, "What is proposed", "Sell passage north to the pilot");
  type(faction, "The leader's choice", "refuse");
  type(faction, "Mara Vell prefers", "sell passage");
  tick(faction, "Anya Rook");
  press(faction, "Show the decision");
  await idle();
  const decided = await until(() => section(main, "Faction decision").textContent.includes("decides:") && section(main, "Faction decision"), 15000, "the decision");
  assert.ok(decided.textContent.includes("House Vell decides: refuse.") && decided.textContent.includes("Nothing is saved"));
  assert.ok(decided.textContent.includes("Mara Vell preferred \"sell passage\": an intention, not an act."));
  assert.ok(decided.textContent.includes("Told, once it is announced in play: Anya Rook."));
  assert.ok(byText(decided, "strong", "Accepting a dissenting member's help as an event is not available yet").length);
  assert.ok(byText(decided, "strong", "A faction deciding by itself is not available yet").length);

  // an NPC's mind: a confident wrong theory proposes a bluff; its revision on new evidence, an open question
  let mind = await until(() => { const found = section(main, "What an NPC thinks"); return found && found.textContent.includes("Mara Vell's evidence") && found; }, 15000, "Mara's mind");
  assert.ok(mind.textContent.includes("Anya glances at the sealed letter") && !mind.textContent.includes("a laugh at the banner"), "only Mara's own evidence");
  assert.ok(mind.textContent.includes("\"Anya knows what the letter says.\" is refuted."));
  const write = (claim, sure, cited, replaces) => {
    mind = section(main, "What an NPC thinks");
    const form = group(mind, "Write a hypothesis");
    input(form, "About").value = pc;
    type(form, "What Mara Vell thinks they think", claim);
    type(form, "How sure", sure);
    tick(form, cited);
    if (replaces) input(form, "It replaces").value = replaces;
    press(form, "Add this hypothesis");
  };
  write("Anya knows what the letter says.", "0.8", "Anya glances at the sealed letter on Mara's desk. (perception, sight)");
  mind = section(main, "What an NPC thinks");
  const tactic = group(mind, "Choose a tactic");
  type(tactic, "Tactic if credited", "Bluff that the letter was already read aloud.");
  type(tactic, "Tactic otherwise", "Ask an open question.");
  press(tactic, "Show what Mara Vell would do");
  await idle();
  mind = await until(() => section(main, "What an NPC thinks").textContent.includes("Proposed tactic") && section(main, "What an NPC thinks"), 15000, "the bluff");
  assert.ok(mind.textContent.includes("Proposed tactic: Bluff that the letter was already read aloud. (h1 is credited at 80%)"));
  assert.ok(mind.textContent.includes("Nothing is saved") && mind.textContent.includes("are unchanged"));

  write("Anya knows what the letter says.", "10%", "Anya asks who could have opened a letter. (perception, hearing)", "h1");
  mind = section(main, "What an NPC thinks");
  input(group(mind, "Choose a tactic"), "Tactic rests on").value = "h2";
  press(group(mind, "Choose a tactic"), "Show what Mara Vell would do");
  await idle();
  mind = await until(() => section(main, "What an NPC thinks").textContent.includes("Ask an open question. (h2") && section(main, "What an NPC thinks"), 15000, "the revision");
  assert.ok(mind.textContent.includes("is below 50%, at 10%") && mind.textContent.includes("are unchanged"));
  const held = mind.querySelectorAll("ul").find((node) => node.textContent.startsWith("h2: about"));
  assert.ok(held && !held.textContent.includes("h1:"), "the revision replaces the first theory");
  assert.ok(mind.textContent.includes("Mara Vell") && mind.textContent.includes("rejected \"Anya knows what the letter says.\""), "Mara's actual belief stands");

  // a hypothesis being written survives leaving the page
  type(group(section(main, "What an NPC thinks"), "Write a hypothesis"), "What Mara Vell thinks they think", "Anya is bluffing too.");
  window.location.hash = "#/home";
  await until(() => !section(main, "What an NPC thinks"), 15000, "leaving");
  window.location.hash = "#/world/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  mind = await until(() => { const found = section(main, "What an NPC thinks"); return found && found.textContent.includes("Mara Vell's evidence") && found; }, 15000, "Mara's mind again");
  assert.equal(input(mind, "What Mara Vell thinks they think").value, "Anya is bluffing too.");
  assert.ok(mind.textContent.includes("h2: about"), "the written hypotheses are kept until cleared");

  // the chronicle links the World page for its slices; what has no screen yet is still named
  window.location.hash = "#/chronicle/" + encodeURIComponent(campaign) + "?chat=" + encodeURIComponent(chat) + "&audience=director";
  const features = await until(() => section(main, "Campaign features"), 15000, "the chronicle");
  const rumorLink = byText(features, "a", "Rumors").find((node) => node.getAttribute("href").startsWith("#/world/"));
  assert.ok(rumorLink && rumorLink.getAttribute("href").includes("audience=director"));
  assert.ok(byText(features, "a", "What an NPC thinks others think").length);
  assert.ok(byText(features, "strong", "Reviewing a fork or retcon is not available yet").length);
});
